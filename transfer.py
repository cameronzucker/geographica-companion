"""Transfer engine for Geographica Companion.

Handles file transfer from workstation to Pi over LAN.

Key design:
  - SSH key auth  -> rsync  (fast, resumable, progress output)
  - Password auth -> paramiko SFTP  (no TTY needed; rsync prompts via /dev/tty
                                    which doesn't exist in a headless process)
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import paramiko


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ConnectionTestResult:
    """Result of a connection pre-flight check."""

    ssh_ok: bool
    rsync_available: bool
    data_dir_writable: bool
    docker_ok: bool
    disk_free_bytes: int
    repo_path: str
    error: str = ""

    @property
    def can_rsync(self) -> bool:
        """True when rsync is usable (SSH works and rsync is installed on remote)."""
        return self.ssh_ok and self.rsync_available

    @property
    def transfer_method(self) -> Optional[str]:
        """Best available transfer method, or None if SSH is unavailable."""
        if not self.ssh_ok:
            return None
        if self.can_rsync:
            return "rsync"
        return "sftp"


# ---------------------------------------------------------------------------
# Method selection
# ---------------------------------------------------------------------------

def detect_transfer_method(auth_type: str, rsync_available: bool) -> str:
    """Return the appropriate transfer method given auth type and rsync availability.

    Rules:
      - key auth  + rsync available -> "rsync"
      - password auth (any)         -> "sftp"  (rsync needs TTY for password)
      - key auth  + no rsync        -> "sftp"
    """
    if auth_type == "key" and rsync_available and shutil.which("rsync"):
        return "rsync"
    return "sftp"


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------

def test_connection(
    host: str,
    username: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    data_dir: str = "/srv/geographica/data",
) -> ConnectionTestResult:
    """Test SSH connectivity and gather remote environment info.

    Checks:
      - rsync availability  (which rsync)
      - data dir writable   (test -w <data_dir>)
      - docker compose      (docker compose version)
      - disk space          (df -B1 <data_dir>)
      - repo path           (docker inspect)
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        connect_kwargs: dict = {"username": username, "timeout": 10}
        if key_path:
            connect_kwargs["key_filename"] = key_path
        if password:
            connect_kwargs["password"] = password

        client.connect(host, **connect_kwargs)
    except Exception as exc:
        return ConnectionTestResult(
            ssh_ok=False,
            rsync_available=False,
            data_dir_writable=False,
            docker_ok=False,
            disk_free_bytes=0,
            repo_path="",
            error=str(exc),
        )

    def run(cmd: str) -> tuple:
        _, stdout, stderr = client.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, stdout.read().decode().strip(), stderr.read().decode().strip()

    # rsync availability
    rc, _, _ = run("which rsync")
    rsync_available = rc == 0

    # data dir writable
    rc, _, _ = run("test -w " + data_dir)
    data_dir_writable = rc == 0

    # docker compose
    rc, _, _ = run("docker compose version")
    docker_ok = rc == 0

    # disk free bytes
    disk_free_bytes = 0
    rc, out, _ = run("df -B1 --output=avail " + data_dir)
    if rc == 0:
        lines = out.strip().splitlines()
        for line in lines:
            line = line.strip()
            if line.isdigit():
                disk_free_bytes = int(line)
                break

    # repo path via docker inspect
    repo_path = ""
    rc, out, _ = run(
        "docker inspect geographica-frontend-1 --format "
        "'{{range .Mounts}}{{.Source}} {{end}}' 2>/dev/null || true"
    )
    if out:
        for part in out.split():
            if "geographica" in part and not part.startswith("/srv"):
                repo_path = part
                break

    client.close()
    return ConnectionTestResult(
        ssh_ok=True,
        rsync_available=rsync_available,
        data_dir_writable=data_dir_writable,
        docker_ok=docker_ok,
        disk_free_bytes=disk_free_bytes,
        repo_path=repo_path,
    )


# ---------------------------------------------------------------------------
# rsync transfer
# ---------------------------------------------------------------------------

# Matches rsync -P progress lines like:
#      102400  45%  512.00kB/s    0:00:01
_RSYNC_PROGRESS_RE = re.compile(
    r"^\s*(\d+)\s+(\d+)%",
)


async def transfer_file_rsync(
    local_path: Path,
    remote_host: str,
    remote_user: str,
    remote_dir: str,
    key_path: str,
    progress_callback: Optional[Callable[[float, int], None]],
) -> bool:
    """Transfer a single file via rsync over SSH.

    Uses asyncio.create_subprocess_exec with rsync -avP for progress output.
    SSH key authentication via -e argument (no shell injection; args are passed
    as a list).

    Args:
        local_path:        Path to local file.
        remote_host:       Hostname or IP of the Pi.
        remote_user:       SSH username.
        remote_dir:        Destination directory on the remote host.
        key_path:          Path to SSH private key.
        progress_callback: Called with (percent: float, transferred_bytes: int).

    Returns:
        True on success, False on failure.
    """
    remote_dest = remote_user + "@" + remote_host + ":" + remote_dir + "/"
    ssh_opt = "ssh -i " + key_path + " -o StrictHostKeyChecking=no"

    proc = await asyncio.create_subprocess_exec(
        "rsync",
        "-avP",
        "-e", ssh_opt,
        str(local_path),
        remote_dest,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async for raw_line in proc.stdout:
        line = raw_line.decode(errors="replace")
        if progress_callback:
            m = _RSYNC_PROGRESS_RE.match(line)
            if m:
                transferred = int(m.group(1))
                pct = float(m.group(2))
                progress_callback(pct, transferred)

    await proc.wait()
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# SFTP transfer (paramiko)
# ---------------------------------------------------------------------------

async def transfer_file_sftp(
    local_path: Path,
    remote_host: str,
    remote_user: str,
    remote_dir: str,
    password: Optional[str],
    progress_callback: Optional[Callable[[float, int], None]],
    key_path: Optional[str] = None,
) -> bool:
    """Transfer a single file via SFTP using paramiko.

    Safe for headless use -- no TTY required. Supports both password and
    key-based auth so key auth can fall back to SFTP when rsync isn't available.

    Args:
        local_path:        Path to local file.
        remote_host:       Hostname or IP of the Pi.
        remote_user:       SSH username.
        remote_dir:        Destination directory on the remote host.
        password:          SSH password.
        progress_callback: Called with (percent: float, transferred_bytes: int).
        key_path:          Path to SSH private key (optional fallback for key auth).

    Returns:
        True on success, False on failure.
    """
    try:
        remote_path = remote_dir + "/" + local_path.name

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {"username": remote_user, "timeout": 15}
        if key_path:
            connect_kwargs["key_filename"] = key_path
        if password:
            connect_kwargs["password"] = password
        ssh.connect(remote_host, **connect_kwargs)

        sftp = ssh.open_sftp()
        try:
            def _callback(transferred: int, total: int) -> None:
                if progress_callback and total > 0:
                    pct = transferred / total * 100.0
                    progress_callback(pct, transferred)

            sftp.put(
                str(local_path),
                remote_path,
                callback=_callback if progress_callback else None,
            )
        finally:
            sftp.close()
            ssh.close()

        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Batch transfer
# ---------------------------------------------------------------------------

async def transfer_all(
    files: list,
    remote_host: str,
    remote_user: str,
    remote_dir: str,
    auth_type: str,
    password: Optional[str],
    key_path: Optional[str],
    progress_callback: Optional[Callable[[float, int], None]],
) -> dict:
    """Transfer multiple files sequentially to the remote host.

    Dispatches to rsync (key auth) or SFTP (password auth) based on auth_type.

    Args:
        files:             List of local Path objects.
        remote_host:       Hostname or IP of the Pi.
        remote_user:       SSH username.
        remote_dir:        Destination directory on the remote host.
        auth_type:         "key" or "password".
        password:          SSH password (used when auth_type == "password").
        key_path:          Path to SSH private key (used when auth_type == "key").
        progress_callback: Called per-file with (percent: float, transferred_bytes: int).

    Returns:
        Dict mapping filename -> success (bool).
    """
    results: dict = {}

    for f in files:
        if auth_type == "key" and key_path and shutil.which("rsync"):
            ok = await transfer_file_rsync(
                local_path=f,
                remote_host=remote_host,
                remote_user=remote_user,
                remote_dir=remote_dir,
                key_path=key_path,
                progress_callback=progress_callback,
            )
        else:
            ok = await transfer_file_sftp(
                local_path=f,
                remote_host=remote_host,
                remote_user=remote_user,
                remote_dir=remote_dir,
                password=password,
                progress_callback=progress_callback,
                key_path=key_path,
            )
        results[f.name] = ok

    return results
