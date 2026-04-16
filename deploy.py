"""Post-transfer deployment: register MBTiles sources in TileServer config and restart.

TileServer GL runs inside a Docker container where data is mounted at /srv/data/.
All source registration must use the container-internal path, NOT the host path
(/srv/geographica/data/).
"""

from __future__ import annotations

from typing import Callable


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def source_name_from_filename(filename: str) -> str:
    """Derive a TileServer source name from an MBTiles filename.

    Strips the .mbtiles extension only; other extensions are left intact.

    Examples:
        "imagery_noaa.mbtiles" -> "imagery_noaa"
        "my.custom.layer.mbtiles" -> "my.custom.layer"
        "foo" -> "foo"
    """
    if filename.endswith(".mbtiles"):
        return filename[: -len(".mbtiles")]
    return filename


def build_register_command(repo_path: str, source_name: str, filename: str) -> str:
    """Build the SSH command string that registers one MBTiles source.

    Uses the container-internal path /srv/data/<filename> — NOT the host path —
    because tileserver_config.py writes the path that TileServer sees inside its
    container.

    Args:
        repo_path: Absolute path to the geographica repo on the Pi.
        source_name: Logical source name (e.g. "imagery_noaa").
        filename: Bare MBTiles filename (e.g. "imagery_noaa.mbtiles").

    Returns:
        A shell command string suitable for SSH exec.
    """
    config_path = f"{repo_path}/tileserver/config.json"
    script_path = f"{repo_path}/scripts/tileserver_config.py"
    container_mbtiles = f"/srv/data/{filename}"
    return (
        f"python3 {script_path} add {config_path} {source_name} {container_mbtiles}"
    )


def generate_deploy_script(filenames: list[str], repo_path: str) -> str:
    """Generate a bash script that registers all sources and restarts TileServer.

    Args:
        filenames: List of bare MBTiles filenames to register.
        repo_path: Absolute path to the geographica repo on the Pi.

    Returns:
        A bash script string ready to be executed via SSH.
    """
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
    ]

    for filename in filenames:
        source_name = source_name_from_filename(filename)
        cmd = build_register_command(repo_path, source_name, filename)
        lines.append(cmd)

    lines += [
        "",
        f"cd {repo_path} && docker compose restart tileserver",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SSH deployment
# ---------------------------------------------------------------------------

def deploy_to_pi(
    host: str,
    username: str,
    password: str | None,
    key_path: str | None,
    repo_path: str,
    filenames: list[str],
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    """Register MBTiles sources on the Pi and restart TileServer over SSH.

    Registers each source individually so that an already-registered source
    can be detected and skipped without aborting the rest.  After all sources
    are handled TileServer is restarted once.

    Args:
        host: Hostname or IP of the Raspberry Pi.
        username: SSH username.
        password: SSH password (or None if using key auth).
        key_path: Path to private key file (or None if using password auth).
        repo_path: Absolute path to the geographica repo on the Pi.
        filenames: List of bare MBTiles filenames that were transferred.
        progress_callback: Optional callable that receives status strings.

    Returns:
        dict with keys:
            "registered": list[str] — source names that were newly registered
            "skipped": list[str]    — source names already present
            "error": str | None     — first fatal error, or None on success
    """
    import paramiko  # optional heavy import; only required at call time

    registered: list[str] = []
    skipped: list[str] = []

    def _log(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs: dict = {"username": username, "timeout": 30}
    if key_path:
        connect_kwargs["key_filename"] = key_path
    if password:
        connect_kwargs["password"] = password

    try:
        client.connect(host, **connect_kwargs)
    except Exception as exc:
        return {"registered": registered, "skipped": skipped, "error": str(exc)}

    try:
        # Register each source individually
        for filename in filenames:
            source_name = source_name_from_filename(filename)
            cmd = build_register_command(repo_path, source_name, filename)
            _log(f"Registering {source_name} …")
            _, stdout, stderr = client.exec_command(cmd)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()

            if exit_code == 0:
                registered.append(source_name)
                _log(f"  registered {source_name}")
            elif "already exists" in out.lower() or "already exists" in err.lower():
                skipped.append(source_name)
                _log(f"  skipped {source_name} (already registered)")
            else:
                detail = err or out or f"exit code {exit_code}"
                return {
                    "registered": registered,
                    "skipped": skipped,
                    "error": f"Failed to register {source_name}: {detail}",
                }

        # Restart TileServer once after all registrations
        restart_cmd = f"cd {repo_path} && docker compose restart tileserver"
        _log("Restarting TileServer …")
        _, stdout, stderr = client.exec_command(restart_cmd)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err = stderr.read().decode().strip()
            return {
                "registered": registered,
                "skipped": skipped,
                "error": f"TileServer restart failed: {err}",
            }
        _log("TileServer restarted.")

    except Exception as exc:
        return {"registered": registered, "skipped": skipped, "error": str(exc)}
    finally:
        client.close()

    return {"registered": registered, "skipped": skipped, "error": None}
