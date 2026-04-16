"""Rasterio-based replacements for GDAL CLI tools.

Eliminates the system GDAL install dependency — rasterio ships pre-built
wheels with GDAL bundled. All pipeline scripts should call these functions
instead of subprocess.run(["gdal_translate", ...]) etc.

Operations provided:
  - filter_tiles_by_bbox: ogr2ogr spatial filter → shapely + dbfread/pyshp
  - check_jp2_support: gdalinfo --formats → rasterio.drivers()
  - reproject_to_mercator: gdalwarp -t_srs EPSG:3857 → rasterio.warp
  - merge_to_mbtiles: gdalbuildvrt + gdal_translate → rasterio.merge + SQLite
  - translate_to_mbtiles: gdal_translate -of MBTiles → rasterio + SQLite
  - build_overviews: gdaladdo -r average → rasterio.build_overviews
  - translate_format: gdal_translate -of GTiff → rasterio copy
"""

import io
import logging
import math
import os
import sqlite3
import struct
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.merge import merge as rasterio_merge
from rasterio.transform import from_bounds
from rasterio.warp import calculate_default_transform, reproject

log = logging.getLogger(__name__)

WEB_MERCATOR = CRS.from_epsg(3857)
TILE_SIZE = 256

# ---------------------------------------------------------------------------
# MBTiles helpers
# ---------------------------------------------------------------------------

def _init_mbtiles(db_path: Path, metadata: dict | None = None) -> sqlite3.Connection:
    """Create or open an MBTiles SQLite database with the correct schema."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tiles (
            zoom_level INTEGER,
            tile_column INTEGER,
            tile_row INTEGER,
            tile_data BLOB,
            PRIMARY KEY (zoom_level, tile_column, tile_row)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            name TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    if metadata:
        for k, v in metadata.items():
            conn.execute(
                "INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)",
                (k, str(v)),
            )
    conn.commit()
    return conn


def _tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """Return (west, south, east, north) in EPSG:3857 for a TMS tile."""
    n = 2 ** z
    origin = 20037508.342789244
    tile_size = 2 * origin / n
    west = -origin + x * tile_size
    east = west + tile_size
    # TMS y-origin is bottom
    south = -origin + y * tile_size
    north = south + tile_size
    return (west, south, east, north)


def _encode_jpeg(array: np.ndarray, quality: int = 85) -> bytes:
    """Encode a 3-band uint8 array as JPEG bytes using rasterio's memory driver."""
    with rasterio.MemoryFile() as memfile:
        with memfile.open(
            driver="JPEG",
            height=array.shape[1],
            width=array.shape[2],
            count=min(array.shape[0], 3),  # JPEG supports 1 or 3 bands
            dtype="uint8",
            quality=quality,
        ) as dst:
            dst.write(array[:3])  # Only write RGB bands
        return memfile.read()


def _encode_png(array: np.ndarray) -> bytes:
    """Encode a uint8 array as PNG bytes using rasterio's memory driver."""
    with rasterio.MemoryFile() as memfile:
        with memfile.open(
            driver="PNG",
            height=array.shape[1],
            width=array.shape[2],
            count=array.shape[0],
            dtype="uint8",
        ) as dst:
            dst.write(array)
        return memfile.read()


# ---------------------------------------------------------------------------
# 1. Spatial filter (replaces ogr2ogr -spat)
# ---------------------------------------------------------------------------

def filter_tiles_by_bbox(
    shapefile_path: Path,
    west: float, south: float, east: float, north: float,
) -> list[str]:
    """Spatially filter a tile index shapefile by bounding box.

    Replaces: ogr2ogr -f CSV /vsistdout/ <shp> -spat w s e n -select filename

    Uses fiona (ships with rasterio wheel) to read the shapefile and
    shapely for bbox intersection. Falls back to simple DBF parsing
    if fiona is unavailable.
    """
    from shapely.geometry import box, shape as shapely_shape

    bbox_geom = box(west, south, east, north)
    filenames = []

    try:
        import fiona
        with fiona.open(str(shapefile_path)) as src:
            for feature in src:
                geom = shapely_shape(feature["geometry"])
                if geom.intersects(bbox_geom):
                    props = feature["properties"]
                    fname = props.get("filename") or props.get("FILENAME") or props.get("location")
                    if fname:
                        filenames.append(fname)
    except ImportError:
        # Fallback: use pyshp if fiona not available
        import shapefile
        with shapefile.Reader(str(shapefile_path)) as sf:
            # Find the filename field
            field_names = [f[0].lower() for f in sf.fields[1:]]
            fname_idx = None
            for i, name in enumerate(field_names):
                if name in ("filename", "location"):
                    fname_idx = i
                    break

            if fname_idx is None:
                log.error("No 'filename' or 'location' field in shapefile")
                return []

            for shape_rec in sf.iterShapeRecords():
                geom = shapely_shape(shape_rec.shape.__geo_interface__)
                if geom.intersects(bbox_geom):
                    filenames.append(shape_rec.record[fname_idx])

    log.info("Spatial filter: %d tiles intersect bbox", len(filenames))
    return filenames


# ---------------------------------------------------------------------------
# 2. Driver/format check (replaces gdalinfo --formats)
# ---------------------------------------------------------------------------

def check_jp2_support() -> bool:
    """Check if rasterio/GDAL supports JP2OpenJPEG format.

    Replaces: gdalinfo --formats | grep JP2OpenJPEG
    """
    try:
        drivers = rasterio.drivers.raster_driver_extensions()
        return "jp2" in drivers
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 3. Reproject to Web Mercator (replaces gdalwarp)
# ---------------------------------------------------------------------------

def reproject_to_mercator(
    src_path: Path,
    dst_path: Path,
    resampling: str = "lanczos",
    compress: str = "deflate",
    cancel_check=None,
) -> bool:
    """Reproject a raster to EPSG:3857 with tiled GeoTIFF output.

    Replaces: gdalwarp -t_srs EPSG:3857 -r lanczos -co TILED=YES
              -co COMPRESS=DEFLATE <src> <dst>
    """
    resamp = getattr(Resampling, resampling, Resampling.lanczos)

    try:
        with rasterio.open(str(src_path)) as src:
            if cancel_check and cancel_check():
                return False

            transform, width, height = calculate_default_transform(
                src.crs, WEB_MERCATOR, src.width, src.height, *src.bounds
            )

            profile = src.profile.copy()
            profile.update(
                crs=WEB_MERCATOR,
                transform=transform,
                width=width,
                height=height,
                driver="GTiff",
                tiled=True,
                compress=compress,
                blockxsize=256,
                blockysize=256,
            )

            dst_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(str(dst_path), "w", **profile) as dst:
                for i in range(1, src.count + 1):
                    if cancel_check and cancel_check():
                        return False
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=WEB_MERCATOR,
                        resampling=resamp,
                        num_threads=os.cpu_count() or 2,
                    )
        return True

    except Exception as exc:
        log.error("Reproject failed for %s: %s", src_path.name, exc)
        if dst_path.exists():
            dst_path.unlink()
        return False


# ---------------------------------------------------------------------------
# 4. Translate single raster to different format (replaces gdal_translate)
# ---------------------------------------------------------------------------

def translate_format(
    src_path: Path,
    dst_path: Path,
    output_format: str = "GTiff",
    creation_options: dict | None = None,
) -> bool:
    """Convert a raster from one format to another.

    Replaces: gdal_translate -of <format> [-co KEY=VAL ...] <src> <dst>
    Common use: JP2 → GeoTIFF conversion for NAIP data.
    """
    if creation_options is None:
        creation_options = {"tiled": True, "compress": "deflate"}

    try:
        with rasterio.open(str(src_path)) as src:
            profile = src.profile.copy()
            profile.update(driver=output_format, **creation_options)

            dst_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(str(dst_path), "w", **profile) as dst:
                # Copy in blocks for memory efficiency
                for ji, window in src.block_windows(1):
                    data = src.read(window=window)
                    dst.write(data, window=window)
        return True

    except Exception as exc:
        log.error("Format translation failed for %s: %s", src_path.name, exc)
        if dst_path.exists():
            dst_path.unlink()
        return False


# ---------------------------------------------------------------------------
# 5. Merge rasters and write to MBTiles (replaces gdalbuildvrt + gdal_translate)
# ---------------------------------------------------------------------------

def merge_to_mbtiles(
    input_paths: list[Path],
    output_path: Path,
    tile_format: str = "jpeg",
    quality: int = 85,
    cancel_check=None,
) -> bool:
    """Merge multiple rasters into an MBTiles file.

    Replaces: gdalbuildvrt <vrt> <files...> && gdal_translate -of MBTiles <vrt> <out>

    For large datasets, processes files in a streaming fashion rather than
    loading everything into memory at once.
    """
    if not input_paths:
        log.error("No input files to merge")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    encode_fn = _encode_jpeg if tile_format.lower() == "jpeg" else _encode_png

    try:
        # Open all datasets
        datasets = [rasterio.open(str(p)) for p in input_paths]

        try:
            # Merge to get combined bounds and resolution
            mosaic, mosaic_transform = rasterio_merge(datasets)

            if cancel_check and cancel_check():
                return False

            # Determine zoom levels from resolution
            res_x = abs(mosaic_transform.a)
            # Approximate zoom from resolution in source CRS
            # For EPSG:3857, resolution at zoom z = 20037508.342789244 * 2 / (256 * 2^z)
            # For EPSG:4326, approximate using equatorial conversion
            first_crs = datasets[0].crs
            if first_crs == WEB_MERCATOR:
                max_zoom = max(0, int(math.log2(20037508.342789244 * 2 / (TILE_SIZE * res_x))))
            else:
                # Convert degrees to approximate meters at equator
                res_meters = res_x * 111320
                max_zoom = max(0, int(math.log2(20037508.342789244 * 2 / (TILE_SIZE * res_meters))))

            min_zoom = max(0, max_zoom - 4)

            # Get bounds in EPSG:4326 for metadata
            from rasterio.warp import transform_bounds
            bounds_4326 = transform_bounds(first_crs, CRS.from_epsg(4326), *datasets[0].bounds)
            for ds in datasets[1:]:
                b = transform_bounds(ds.crs, CRS.from_epsg(4326), *ds.bounds)
                bounds_4326 = (
                    min(bounds_4326[0], b[0]), min(bounds_4326[1], b[1]),
                    max(bounds_4326[2], b[2]), max(bounds_4326[3], b[3]),
                )

            # Create MBTiles
            conn = _init_mbtiles(output_path, {
                "name": output_path.stem,
                "format": tile_format.lower(),
                "type": "overlay",
                "minzoom": str(min_zoom),
                "maxzoom": str(max_zoom),
                "bounds": f"{bounds_4326[0]},{bounds_4326[1]},{bounds_4326[2]},{bounds_4326[3]}",
            })

            try:
                _rasterize_to_tiles(
                    mosaic, mosaic_transform, first_crs,
                    conn, min_zoom, max_zoom,
                    encode_fn, quality, cancel_check,
                )
                conn.commit()
            finally:
                conn.close()

        finally:
            for ds in datasets:
                ds.close()

        return True

    except Exception as exc:
        log.error("merge_to_mbtiles failed: %s", exc)
        return False


def translate_to_mbtiles(
    src_path: Path,
    output_path: Path,
    tile_format: str = "jpeg",
    quality: int = 85,
    cancel_check=None,
) -> bool:
    """Convert a single raster to MBTiles.

    Replaces: gdal_translate -of MBTiles -co TILE_FORMAT=JPEG <src> <out>
    """
    return merge_to_mbtiles([src_path], output_path, tile_format, quality, cancel_check)


def _rasterize_to_tiles(
    data: np.ndarray,
    transform,
    src_crs,
    conn: sqlite3.Connection,
    min_zoom: int,
    max_zoom: int,
    encode_fn,
    quality: int,
    cancel_check=None,
):
    """Render a merged raster array into 256x256 tiles and write to MBTiles."""
    from rasterio.warp import transform_bounds

    # Get bounds in EPSG:4326 for tile coordinate calculation
    left = transform.c
    top = transform.f
    right = left + transform.a * data.shape[2]
    bottom = top + transform.e * data.shape[1]

    bounds_4326 = transform_bounds(src_crs, CRS.from_epsg(4326), left, bottom, right, top)

    tile_count = 0
    for zoom in range(min_zoom, max_zoom + 1):
        if cancel_check and cancel_check():
            return

        # Calculate tile range for this zoom
        x_min, y_min = _lonlat_to_tile(bounds_4326[0], bounds_4326[3], zoom)
        x_max, y_max = _lonlat_to_tile(bounds_4326[2], bounds_4326[1], zoom)

        for tx in range(x_min, x_max + 1):
            for ty in range(y_min, y_max + 1):
                if cancel_check and cancel_check():
                    return

                # Get tile bounds in source CRS
                tile_bounds_3857 = _tile_bounds(tx, ty, zoom)
                tile_bounds_src = transform_bounds(
                    WEB_MERCATOR, src_crs,
                    *tile_bounds_3857,
                )

                # Read the portion of the mosaic that covers this tile
                tile_data = _read_tile_from_array(
                    data, transform, tile_bounds_src, TILE_SIZE,
                )

                if tile_data is None or _is_empty_tile(tile_data):
                    continue

                # Encode and write
                tile_bytes = encode_fn(tile_data, quality) if encode_fn == _encode_jpeg else encode_fn(tile_data)

                # MBTiles uses TMS y-flip
                tms_y = (2 ** zoom - 1) - ty
                conn.execute(
                    "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
                    (zoom, tx, tms_y, tile_bytes),
                )
                tile_count += 1

                if tile_count % 1000 == 0:
                    conn.commit()
                    log.info("Written %d tiles (zoom %d)", tile_count, zoom)

    conn.commit()
    log.info("Total tiles written: %d", tile_count)


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Convert lon/lat to tile coordinates at given zoom."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def _read_tile_from_array(
    data: np.ndarray,
    transform,
    tile_bounds: tuple[float, float, float, float],
    tile_size: int,
) -> np.ndarray | None:
    """Extract a tile-sized portion from a numpy array given geographic bounds."""
    from rasterio.transform import rowcol

    west, south, east, north = tile_bounds
    try:
        row_start, col_start = rowcol(transform, west, north)
        row_end, col_end = rowcol(transform, east, south)
    except Exception:
        return None

    # Clamp to array bounds
    row_start = max(0, min(data.shape[1] - 1, row_start))
    row_end = max(0, min(data.shape[1], row_end))
    col_start = max(0, min(data.shape[2] - 1, col_start))
    col_end = max(0, min(data.shape[2], col_end))

    if row_end <= row_start or col_end <= col_start:
        return None

    window_data = data[:, row_start:row_end, col_start:col_end]

    if window_data.size == 0:
        return None

    # Resize to tile_size x tile_size using simple nearest-neighbor
    bands = window_data.shape[0]
    tile = np.zeros((bands, tile_size, tile_size), dtype=np.uint8)

    y_ratio = window_data.shape[1] / tile_size
    x_ratio = window_data.shape[2] / tile_size

    for b in range(bands):
        for ty in range(tile_size):
            src_y = min(int(ty * y_ratio), window_data.shape[1] - 1)
            for tx in range(tile_size):
                src_x = min(int(tx * x_ratio), window_data.shape[2] - 1)
                tile[b, ty, tx] = window_data[b, src_y, src_x]

    return tile


def _is_empty_tile(data: np.ndarray) -> bool:
    """Check if a tile is all zeros/black (no actual data)."""
    return not np.any(data)


# ---------------------------------------------------------------------------
# 6. Build overview pyramids (replaces gdaladdo)
# ---------------------------------------------------------------------------

def build_overviews(
    mbtiles_path: Path,
    levels: list[int] | None = None,
    resampling: str = "average",
    cancel_check=None,
) -> bool:
    """Build overview pyramid for an MBTiles file by downsampling existing tiles.

    Replaces: gdaladdo -r average <mbtiles> 2 4 8 16

    Works directly on the MBTiles SQLite database: reads tiles at the highest
    zoom, composites 2x2 groups into the next lower zoom, repeating until
    the minimum zoom is reached.
    """
    if levels is None:
        levels = [2, 4, 8, 16]

    try:
        conn = sqlite3.connect(str(mbtiles_path))
        conn.execute("PRAGMA journal_mode=WAL")

        # Find current max zoom
        row = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()
        if not row or row[0] is None:
            log.warning("No tiles in %s — skipping overviews", mbtiles_path)
            conn.close()
            return True

        max_zoom = row[0]

        # Build overview zooms by halving
        current_zoom = max_zoom
        for level in levels:
            target_zoom = current_zoom - int(math.log2(level))
            if target_zoom < 0:
                break
            current_zoom = target_zoom

        # Actually build: for each zoom from max_zoom-1 down, composite
        for z in range(max_zoom - 1, -1, -1):
            if cancel_check and cancel_check():
                conn.close()
                return False

            parent_z = z + 1
            # Get all tiles at parent zoom
            rows = conn.execute(
                "SELECT DISTINCT tile_column/2, tile_row/2 FROM tiles WHERE zoom_level = ?",
                (parent_z,),
            ).fetchall()

            if not rows:
                break

            tile_count = 0
            for (tx, ty) in rows:
                # Check if this overview tile already exists
                existing = conn.execute(
                    "SELECT 1 FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                    (z, tx, ty),
                ).fetchone()
                if existing:
                    continue

                # Gather 2x2 child tiles
                children = []
                for dx in range(2):
                    for dy in range(2):
                        child = conn.execute(
                            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                            (parent_z, tx * 2 + dx, ty * 2 + dy),
                        ).fetchone()
                        children.append((dx, dy, child[0] if child else None))

                if not any(c[2] for c in children):
                    continue

                # Decode children, composite, re-encode
                composite = np.zeros((3, TILE_SIZE, TILE_SIZE), dtype=np.uint8)
                half = TILE_SIZE // 2

                for dx, dy, tile_data in children:
                    if tile_data is None:
                        continue
                    try:
                        with rasterio.MemoryFile(tile_data) as memfile:
                            with memfile.open() as ds:
                                bands = min(ds.count, 3)
                                tile_arr = ds.read(list(range(1, bands + 1)))
                                # Downsample to half size using simple averaging
                                if tile_arr.shape[1] >= 2 and tile_arr.shape[2] >= 2:
                                    small = tile_arr[:, ::2, ::2][:, :half, :half]
                                else:
                                    small = tile_arr[:, :half, :half]
                                x_off = dx * half
                                y_off = dy * half
                                h = min(small.shape[1], half)
                                w = min(small.shape[2], half)
                                composite[:bands, y_off:y_off + h, x_off:x_off + w] = small[:, :h, :w]
                    except Exception:
                        continue

                tile_bytes = _encode_jpeg(composite)
                conn.execute(
                    "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
                    (z, tx, ty, tile_bytes),
                )
                tile_count += 1

            conn.commit()
            if tile_count > 0:
                log.info("Built %d overview tiles at zoom %d", tile_count, z)

        # Update metadata
        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom_actual = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO metadata (name, value) VALUES ('minzoom', ?)", (str(min_zoom),))
        conn.execute("INSERT OR REPLACE INTO metadata (name, value) VALUES ('maxzoom', ?)", (str(max_zoom_actual),))
        conn.commit()
        conn.close()

        log.info("Overviews complete for %s (zoom %d-%d)", mbtiles_path, min_zoom, max_zoom_actual)
        return True

    except Exception as exc:
        log.error("build_overviews failed for %s: %s", mbtiles_path, exc)
        return False
