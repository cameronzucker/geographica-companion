# Geographica Companion — Data Ingestion Utility

Cross-platform desktop tool for downloading geospatial imagery data on a fast workstation and transferring results to a Geographica Raspberry Pi over LAN.

Run the download-heavy pipelines on your laptop or desktop (where bandwidth and disk I/O are fast), then push the resulting MBTiles directly to your Pi with one click.

---

## Prerequisites

- Python 3.10+
- GDAL (see [GDAL section](#gdal) below)
- SSH access to your Geographica Pi (for transfer)

---

## Quick Start

**Linux / macOS**
```bash
chmod +x companion.sh && ./companion.sh
```

**Windows**
Double-click `companion.bat`

Both launchers create a virtualenv, install dependencies, and open the UI at `http://127.0.0.1:9000` in your default browser.

---

## Features

- **6 imagery and elevation pipelines** — USGS basemap, NAIP county mosaics, USGS M2M high-res, Sentinel-2, elevation (terrain-RGB), and custom GeoTIFF/JP2/MBTiles import
- **Parallel pipeline execution** — run multiple downloads at once, each with live progress and cancellation
- **rsync / SFTP transfer** — push finished MBTiles to your Pi; auto-deploys to TileServer on arrival
- **MapLibre minimap** — draw your bounding box visually instead of typing coordinates
- **Catppuccin Mocha theme** — matches the Geographica frontend aesthetic
- **Bundled GDAL support** — ships `bin/` stubs; falls back to system PATH automatically

---

## Pipeline Sources

| Source | Zoom | Auth | Description |
|---|---|---|---|
| USGS Basemap | z0–14 | Free | 256 px tiles from The National Map |
| NOAA NAIP | z14–18 | Free | County mosaics with `gdaladdo` overview generation |
| USGS M2M | z15–19 | API Key | High-res NAIP via Machine-to-Machine API |
| Sentinel-2 | 10 m | API Key | Multispectral imagery via Copernicus Data Space |
| Elevation | z0–14 | Free | Terrain-RGB MBTiles for hillshade / routing |
| Import Custom | varies | Local | GeoTIFF, JP2, or existing MBTiles → re-tiled MBTiles |

API keys for M2M and Sentinel-2 are entered once in the **Connect** tab and stored in the system keychain (never written to disk in plaintext).

---

## Transfer

Two modes, both configured in the **Connect** tab:

| Mode | Auth | Speed | Notes |
|---|---|---|---|
| rsync | SSH key | Fast, resumable | Preferred; requires key-based auth set up on Pi |
| SFTP | Password | Moderate | No TTY needed; works everywhere paramiko runs |

After transfer completes, the companion sends a reload signal to TileServer GL on the Pi so the new layer appears immediately — no SSH session required.

---

## GDAL

Companion ships lightweight wrapper stubs in `bin/`. On first run it checks for a working `gdal_translate` on your PATH and uses that if found.

**Install system GDAL:**

```bash
# Debian / Ubuntu / Raspberry Pi OS
sudo apt install gdal-bin python3-gdal

# macOS (Homebrew)
brew install gdal

# Windows
# Install OSGeo4W from https://trac.osgeo.org/osgeo4w/
# Add C:\OSGeo4W\bin to your PATH
```

If GDAL is missing, pipelines that require raster processing (NAIP, Sentinel-2, elevation, custom import) will display a warning in the UI; USGS tile-download pipelines still work without it.

---

## Architecture

```
companion.sh / companion.bat
    └─ Python venv
        ├─ FastAPI backend   127.0.0.1:9000
        │   ├─ /api/pipeline/start|cancel|state
        │   ├─ /api/transfer/push
        │   └─ /api/connect  (SSH test, keychain read/write)
        ├─ Browser UI        static/index.html
        │   ├─ Connect tab   (SSH / API key config)
        │   ├─ Pipelines tab (bbox draw, source select, run)
        │   ├─ Transfer tab  (rsync / SFTP push)
        │   └─ Status tab    (live logs, active jobs)
        └─ Pipeline subprocesses
            ├─ pipelines/usgs_basemap.py
            ├─ pipelines/naip_county.py
            ├─ pipelines/usgs_m2m.py
            ├─ pipelines/sentinel2.py
            ├─ pipelines/elevation.py
            └─ pipelines/import_custom.py
```

The backend binds only to `127.0.0.1` — it is never exposed to the network.

---

## License

MIT
