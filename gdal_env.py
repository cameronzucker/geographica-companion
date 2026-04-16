"""GDAL detection and environment setup for the companion utility.

With the rasterio refactor, GDAL CLI tools are no longer required.
Rasterio bundles GDAL as a shared library inside its pip wheel.
This module now primarily checks that rasterio is importable.
"""

import os
import platform
import shutil
from pathlib import Path

COMPANION_DIR = Path(__file__).parent


def detect_gdal() -> Path | None:
    """Detect GDAL availability.

    Resolution order:
    1. rasterio importable (GDAL bundled inside the pip wheel — preferred)
    2. GDAL_BIN_DIR env var (user override, for CLI tool fallback)
    3. Bundled bin/{platform}/ directory
    4. System PATH gdalwarp
    5. None (not found)
    """
    # rasterio bundles GDAL — this is the primary path now
    try:
        import rasterio  # noqa: F401
        return Path("rasterio-bundled")  # sentinel value indicating rasterio provides GDAL
    except ImportError:
        pass

    # Legacy fallback: system GDAL CLI tools
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
    """Build environment dict for subprocess execution.

    With rasterio handling GDAL operations in-process, this is mostly
    used for setting thread/cache hints that rasterio respects via env vars.
    """
    env = os.environ.copy()

    if gdal_bin_dir and str(gdal_bin_dir) != "rasterio-bundled":
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
