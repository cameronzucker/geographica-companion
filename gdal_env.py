"""GDAL binary detection and environment setup for the companion utility."""

import os
import platform
import shutil
from pathlib import Path

COMPANION_DIR = Path(__file__).parent


def detect_gdal() -> Path | None:
    """Detect GDAL binaries. Returns bin directory path, or None for system PATH.

    Resolution order:
    1. GDAL_BIN_DIR env var (user override)
    2. Bundled bin/{platform}/ directory
    3. System PATH (returns None)
    4. None with no system gdalwarp found
    """
    env_dir = os.environ.get("GDAL_BIN_DIR")
    if env_dir:
        return Path(env_dir)

    system = platform.system()
    if system == "Linux":
        bundled = COMPANION_DIR / "bin" / "linux-x64"
    elif system == "Windows":
        bundled = COMPANION_DIR / "bin" / "windows-x64"
    else:
        bundled = None

    if bundled and bundled.is_dir():
        gdalwarp = bundled / ("gdalwarp.exe" if system == "Windows" else "gdalwarp")
        if gdalwarp.exists():
            return bundled

    if shutil.which("gdalwarp"):
        return None

    return None


def get_gdal_env(gdal_bin_dir: Path | None, gdal_threads: int = 0) -> dict:
    """Build environment dict for GDAL subprocess execution."""
    env = os.environ.copy()

    if gdal_bin_dir:
        sep = ";" if platform.system() == "Windows" else ":"
        env["PATH"] = str(gdal_bin_dir) + sep + env.get("PATH", "")
        share_proj = gdal_bin_dir / "share" / "proj"
        share_gdal = gdal_bin_dir / "share" / "gdal"
        if share_proj.is_dir():
            env["PROJ_LIB"] = str(share_proj)
        if share_gdal.is_dir():
            env["GDAL_DATA"] = str(share_gdal)

    threads_str = str(gdal_threads) if gdal_threads > 0 else "ALL_CPUS"
    env["GDAL_NUM_THREADS"] = threads_str
    env["GDAL_CACHEMAX"] = "512"

    return env
