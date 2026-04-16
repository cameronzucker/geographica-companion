import os
import platform
from pathlib import Path
from unittest.mock import patch
import pytest

from gdal_env import detect_gdal, get_gdal_env


class TestDetectGdal:
    def test_returns_bundled_path_when_exists(self, tmp_path):
        bin_dir = tmp_path / "bin" / "linux-x64"
        bin_dir.mkdir(parents=True)
        (bin_dir / "gdalwarp").write_text("#!/bin/sh\n")
        (bin_dir / "gdalwarp").chmod(0o755)
        with patch("gdal_env.COMPANION_DIR", tmp_path):
            with patch("platform.system", return_value="Linux"):
                result = detect_gdal()
        assert result is not None
        assert "linux-x64" in str(result)

    def test_returns_none_when_no_gdal(self, tmp_path):
        with patch("gdal_env.COMPANION_DIR", tmp_path):
            with patch("shutil.which", return_value=None):
                result = detect_gdal()
        assert result is None

    def test_env_var_override(self, tmp_path):
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        (custom_dir / "gdalwarp").write_text("#!/bin/sh\n")
        with patch.dict(os.environ, {"GDAL_BIN_DIR": str(custom_dir)}):
            result = detect_gdal()
        assert result == custom_dir

    def test_system_path_fallback(self, tmp_path):
        with patch("gdal_env.COMPANION_DIR", tmp_path):
            with patch("shutil.which", return_value="/usr/bin/gdalwarp"):
                result = detect_gdal()
        assert result is None  # None means "use system PATH as-is"


class TestGetGdalEnv:
    def test_env_includes_path_and_proj(self, tmp_path):
        bin_dir = tmp_path / "bin" / "linux-x64"
        share_proj = bin_dir / "share" / "proj"
        share_gdal = bin_dir / "share" / "gdal"
        share_proj.mkdir(parents=True)
        share_gdal.mkdir(parents=True)
        env = get_gdal_env(bin_dir, gdal_threads=4)
        assert str(bin_dir) in env["PATH"]
        assert env["PROJ_LIB"] == str(share_proj)
        assert env["GDAL_DATA"] == str(share_gdal)
        assert env["GDAL_NUM_THREADS"] == "4"

    def test_env_inherits_current(self, tmp_path):
        env = get_gdal_env(None, gdal_threads=2)
        assert "HOME" in env or "USERPROFILE" in env
