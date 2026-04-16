"""Tests for transfer.py — SSH/rsync/SFTP transfer engine.

All tests use mocks — no real SSH connections are made.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

from transfer import (
    ConnectionTestResult,
    detect_transfer_method,
    transfer_file_rsync,
    transfer_file_sftp,
    transfer_all,
)


# ---------------------------------------------------------------------------
# ConnectionTestResult dataclass
# ---------------------------------------------------------------------------

class TestConnectionTestResult:
    def test_can_rsync_true(self):
        r = ConnectionTestResult(
            ssh_ok=True,
            rsync_available=True,
            data_dir_writable=True,
            docker_ok=True,
            disk_free_bytes=10_000_000,
            repo_path="/opt/geographica",
            error="",
        )
        assert r.can_rsync is True

    def test_can_rsync_false_when_ssh_not_ok(self):
        r = ConnectionTestResult(
            ssh_ok=False,
            rsync_available=True,
            data_dir_writable=False,
            docker_ok=False,
            disk_free_bytes=0,
            repo_path="",
            error="connection refused",
        )
        assert r.can_rsync is False

    def test_can_rsync_false_when_rsync_unavailable(self):
        r = ConnectionTestResult(
            ssh_ok=True,
            rsync_available=False,
            data_dir_writable=True,
            docker_ok=True,
            disk_free_bytes=10_000_000,
            repo_path="/opt/geographica",
            error="",
        )
        assert r.can_rsync is False

    def test_transfer_method_rsync(self):
        r = ConnectionTestResult(
            ssh_ok=True,
            rsync_available=True,
            data_dir_writable=True,
            docker_ok=True,
            disk_free_bytes=10_000_000,
            repo_path="/opt/geographica",
            error="",
        )
        assert r.transfer_method == "rsync"

    def test_transfer_method_sftp_when_ssh_ok_no_rsync(self):
        r = ConnectionTestResult(
            ssh_ok=True,
            rsync_available=False,
            data_dir_writable=True,
            docker_ok=True,
            disk_free_bytes=10_000_000,
            repo_path="/opt/geographica",
            error="",
        )
        assert r.transfer_method == "sftp"

    def test_transfer_method_none_when_ssh_fails(self):
        r = ConnectionTestResult(
            ssh_ok=False,
            rsync_available=False,
            data_dir_writable=False,
            docker_ok=False,
            disk_free_bytes=0,
            repo_path="",
            error="timeout",
        )
        assert r.transfer_method is None

    def test_error_defaults_to_empty_string(self):
        r = ConnectionTestResult(
            ssh_ok=False,
            rsync_available=False,
            data_dir_writable=False,
            docker_ok=False,
            disk_free_bytes=0,
            repo_path="",
        )
        assert r.error == ""


# ---------------------------------------------------------------------------
# detect_transfer_method
# ---------------------------------------------------------------------------

class TestDetectTransferMethod:
    def test_key_auth_with_rsync_returns_rsync(self):
        assert detect_transfer_method("key", rsync_available=True) == "rsync"

    def test_password_auth_returns_sftp_regardless_of_rsync(self):
        assert detect_transfer_method("password", rsync_available=True) == "sftp"
        assert detect_transfer_method("password", rsync_available=False) == "sftp"

    def test_key_auth_no_rsync_returns_sftp(self):
        assert detect_transfer_method("key", rsync_available=False) == "sftp"

    def test_unknown_auth_type_returns_sftp(self):
        # Any auth type that isn't "key" falls back to sftp (safe default)
        assert detect_transfer_method("unknown", rsync_available=True) == "sftp"


# ---------------------------------------------------------------------------
# transfer_file_rsync
# ---------------------------------------------------------------------------

class TestTransferFileRsync:
    @pytest.mark.asyncio
    async def test_rsync_command_construction(self, tmp_path):
        """Verify rsync is called with -avP and the key path."""
        local_file = tmp_path / "imagery.mbtiles"
        local_file.write_bytes(b"fake data")
        key_path = "/home/user/.ssh/id_ed25519"

        async def empty_aiter():
            return
            yield  # make it an async generator

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.stdout.__aiter__ = lambda self: empty_aiter()
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await transfer_file_rsync(
                local_path=local_file,
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                key_path=key_path,
                progress_callback=None,
            )

        assert result is True
        assert mock_exec.called
        cmd_args = mock_exec.call_args[0]
        # First arg is the executable
        assert cmd_args[0] == "rsync"
        # -avP must be present
        assert "-avP" in cmd_args
        # key path must appear in the ssh -e argument
        full_cmd = " ".join(cmd_args)
        assert key_path in full_cmd
        # Destination must include host and remote dir
        assert "192.168.1.100" in full_cmd
        assert "/srv/geographica/data" in full_cmd

    @pytest.mark.asyncio
    async def test_rsync_returns_false_on_nonzero_exit(self, tmp_path):
        local_file = tmp_path / "bad.mbtiles"
        local_file.write_bytes(b"data")

        async def empty_aiter():
            return
            yield  # make it an async generator

        mock_proc = AsyncMock()
        mock_proc.returncode = 23  # rsync partial transfer error
        mock_proc.stdout.__aiter__ = lambda self: empty_aiter()
        mock_proc.wait = AsyncMock(return_value=23)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await transfer_file_rsync(
                local_path=local_file,
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                key_path="/home/user/.ssh/id_ed25519",
                progress_callback=None,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_rsync_calls_progress_callback(self, tmp_path):
        local_file = tmp_path / "test.mbtiles"
        local_file.write_bytes(b"data")

        # Simulate a progress line from rsync -P
        progress_line = b"     102400  45%  512.00kB/s    0:00:01\r"

        mock_proc = AsyncMock()
        mock_proc.returncode = 0

        async def mock_aiter():
            yield progress_line

        mock_proc.stdout.__aiter__ = lambda self: mock_aiter()
        mock_proc.wait = AsyncMock(return_value=0)

        callbacks = []

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await transfer_file_rsync(
                local_path=local_file,
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                key_path="/home/user/.ssh/id_ed25519",
                progress_callback=lambda pct, transferred: callbacks.append((pct, transferred)),
            )

        assert result is True
        # At least one progress callback should have fired
        assert len(callbacks) >= 1
        pct, transferred = callbacks[0]
        assert 0 <= pct <= 100
        assert transferred > 0


# ---------------------------------------------------------------------------
# transfer_file_sftp
# ---------------------------------------------------------------------------

class TestTransferFileSftp:
    @pytest.mark.asyncio
    async def test_sftp_happy_path(self, tmp_path):
        local_file = tmp_path / "imagery.mbtiles"
        local_file.write_bytes(b"fake data")

        mock_sftp = MagicMock()
        mock_sftp.put = MagicMock()
        mock_sftp.__enter__ = MagicMock(return_value=mock_sftp)
        mock_sftp.__exit__ = MagicMock(return_value=False)

        mock_ssh = MagicMock()
        mock_ssh.open_sftp = MagicMock(return_value=mock_sftp)
        mock_ssh.connect = MagicMock()
        mock_ssh.__enter__ = MagicMock(return_value=mock_ssh)
        mock_ssh.__exit__ = MagicMock(return_value=False)

        with patch("paramiko.SSHClient", return_value=mock_ssh):
            result = await transfer_file_sftp(
                local_path=local_file,
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                password="secret",
                progress_callback=None,
            )

        assert result is True
        mock_sftp.put.assert_called_once()
        # Verify destination path contains the remote dir and filename
        put_args = mock_sftp.put.call_args[0]
        assert str(local_file) == put_args[0]
        assert "/srv/geographica/data/imagery.mbtiles" == put_args[1]

    @pytest.mark.asyncio
    async def test_sftp_returns_false_on_exception(self, tmp_path):
        local_file = tmp_path / "imagery.mbtiles"
        local_file.write_bytes(b"data")

        mock_ssh = MagicMock()
        mock_ssh.connect = MagicMock(side_effect=Exception("connection refused"))
        mock_ssh.__enter__ = MagicMock(return_value=mock_ssh)
        mock_ssh.__exit__ = MagicMock(return_value=False)

        with patch("paramiko.SSHClient", return_value=mock_ssh):
            result = await transfer_file_sftp(
                local_path=local_file,
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                password="bad_password",
                progress_callback=None,
            )

        assert result is False


# ---------------------------------------------------------------------------
# transfer_all
# ---------------------------------------------------------------------------

class TestTransferAll:
    @pytest.mark.asyncio
    async def test_transfer_all_key_auth_dispatches_rsync(self, tmp_path):
        file1 = tmp_path / "a.mbtiles"
        file2 = tmp_path / "b.mbtiles"
        file1.write_bytes(b"a")
        file2.write_bytes(b"b")

        with patch("transfer.transfer_file_rsync", new_callable=AsyncMock, return_value=True) as mock_rsync, \
             patch("transfer.transfer_file_sftp", new_callable=AsyncMock, return_value=True) as mock_sftp:

            results = await transfer_all(
                files=[file1, file2],
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                auth_type="key",
                password=None,
                key_path="/home/user/.ssh/id_ed25519",
                progress_callback=None,
            )

        assert results == {"a.mbtiles": True, "b.mbtiles": True}
        assert mock_rsync.call_count == 2
        assert mock_sftp.call_count == 0

    @pytest.mark.asyncio
    async def test_transfer_all_password_auth_dispatches_sftp(self, tmp_path):
        file1 = tmp_path / "x.mbtiles"
        file1.write_bytes(b"x")

        with patch("transfer.transfer_file_rsync", new_callable=AsyncMock, return_value=True) as mock_rsync, \
             patch("transfer.transfer_file_sftp", new_callable=AsyncMock, return_value=True) as mock_sftp:

            results = await transfer_all(
                files=[file1],
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                auth_type="password",
                password="mypassword",
                key_path=None,
                progress_callback=None,
            )

        assert results == {"x.mbtiles": True}
        assert mock_sftp.call_count == 1
        assert mock_rsync.call_count == 0

    @pytest.mark.asyncio
    async def test_transfer_all_partial_failure(self, tmp_path):
        file1 = tmp_path / "good.mbtiles"
        file2 = tmp_path / "bad.mbtiles"
        file1.write_bytes(b"ok")
        file2.write_bytes(b"fail")

        async def fake_sftp(local_path, **kwargs):
            return local_path.name == "good.mbtiles"

        with patch("transfer.transfer_file_sftp", side_effect=fake_sftp):
            results = await transfer_all(
                files=[file1, file2],
                remote_host="192.168.1.100",
                remote_user="pi",
                remote_dir="/srv/geographica/data",
                auth_type="password",
                password="pw",
                key_path=None,
                progress_callback=None,
            )

        assert results["good.mbtiles"] is True
        assert results["bad.mbtiles"] is False

    @pytest.mark.asyncio
    async def test_transfer_all_empty_file_list(self):
        results = await transfer_all(
            files=[],
            remote_host="192.168.1.100",
            remote_user="pi",
            remote_dir="/srv/geographica/data",
            auth_type="key",
            password=None,
            key_path="/home/user/.ssh/id_ed25519",
            progress_callback=None,
        )
        assert results == {}
