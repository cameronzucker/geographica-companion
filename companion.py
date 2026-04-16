"""Geographica Companion — FastAPI backend.

Serves the local web UI and provides API endpoints for managing data
pipelines (imagery acquisition, elevation, POI import). Binds exclusively
to 127.0.0.1 for security.
"""

import os
import secrets
import shutil
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

import gdal_env
from pipelines.orchestrator import Orchestrator, PipelineJob

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COMPANION_OUTPUT_DIR = Path(os.environ.get("COMPANION_OUTPUT_DIR", "./geographica-data"))
COMPANION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CSRF_TOKEN: str = secrets.token_urlsafe(32)

PIPELINE_DEFS = [
    {
        "name": "basemap",
        "label": "USGS Basemap",
        "script": "acquire_imagery.py",
        "zoom": "0-14",
        "auth": "free",
        "description": "256px tiles",
    },
    {
        "name": "noaa",
        "label": "NOAA NAIP",
        "script": "acquire_imagery.py",
        "zoom": "14-18",
        "auth": "free",
        "description": "County mosaics with gdaladdo",
    },
    {
        "name": "m2m",
        "label": "USGS M2M",
        "script": "acquire_imagery.py",
        "zoom": "15-19",
        "auth": "apikey",
        "description": "High-res NAIP",
    },
    {
        "name": "sentinel",
        "label": "Sentinel-2",
        "script": "acquire_sentinel.py",
        "zoom": "10m",
        "auth": "apikey",
        "description": "Multispectral",
    },
    {
        "name": "elevation",
        "label": "Elevation",
        "script": "download_elevation.py",
        "zoom": "0-14",
        "auth": "free",
        "description": "Terrain-RGB",
    },
    {
        "name": "import",
        "label": "Import Custom",
        "script": "import_imagery.py",
        "zoom": "varies",
        "auth": "local",
        "description": "GeoTIFF, JP2, MBTiles",
    },
]

_PIPELINE_DEF_MAP = {p["name"]: p for p in PIPELINE_DEFS}

# ---------------------------------------------------------------------------
# CSRF middleware
# ---------------------------------------------------------------------------

CSRF_PROTECTED_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in CSRF_PROTECTED_METHODS:
            token = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(token, CSRF_TOKEN):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        return await call_next(request)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Geographica Companion", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:9000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(CSRFMiddleware)

# ---------------------------------------------------------------------------
# Lazy orchestrator
# ---------------------------------------------------------------------------

_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        gdal_bin_dir = gdal_env.detect_gdal()
        env = gdal_env.get_gdal_env(gdal_bin_dir)
        scripts_dir = Path(__file__).parent / "scripts"
        _orchestrator = Orchestrator(
            pipelines_dir=scripts_dir,
            output_dir=COMPANION_OUTPUT_DIR,
            env=env,
        )
    return _orchestrator


# ---------------------------------------------------------------------------
# CLI arg builder
# ---------------------------------------------------------------------------

def _build_cli_args(pipeline_name: str, args: dict) -> list[str]:
    """Build pipeline-specific CLI argument list from request body args dict."""
    cli: list[str] = []

    # Common args present in most pipelines
    bbox = args.get("bbox")
    output = args.get("output") or str(COMPANION_OUTPUT_DIR / f"{pipeline_name}.mbtiles")
    staging = args.get("staging") or str(COMPANION_OUTPUT_DIR / "staging")

    if pipeline_name == "basemap":
        cli += ["--mode", "tnmaccess"]
        cli += ["--zoom", args.get("zoom", "0-14")]
        if bbox:
            cli += ["--bbox", bbox]
        cli += ["--output", output, "--staging", staging]

    elif pipeline_name == "noaa":
        cli += ["--mode", "noaa"]
        if bbox:
            cli += ["--bbox", bbox]
        state = args.get("state")
        if state:
            cli += ["--state", state]
        cli += ["--output", output, "--staging", staging]

    elif pipeline_name == "m2m":
        cli += ["--mode", "m2m"]
        if bbox:
            cli += ["--bbox", bbox]
        username = args.get("m2m_username") or args.get("m2m-username")
        token = args.get("m2m_token") or args.get("m2m-token")
        if username:
            cli += ["--m2m-username", username]
        if token:
            cli += ["--m2m-token", token]
        cli += ["--output", output, "--staging", staging]

    elif pipeline_name == "sentinel":
        if bbox:
            cli += ["--bbox", bbox]
        api_key = args.get("api_key") or args.get("api-key")
        if api_key:
            cli += ["--api-key", api_key]
        cli += ["--output", output, "--staging", staging]

    elif pipeline_name == "elevation":
        if bbox:
            cli += ["--bbox", bbox]
        cli += ["--zoom", args.get("zoom", "0-14")]
        cli += ["--output", output, "--staging", staging]

    elif pipeline_name == "import":
        source = args.get("source")
        if source:
            cli += ["--source", source]
        cli += ["--output", output]

    else:
        # Generic fallback: pass all args as --key value pairs
        for k, v in args.items():
            cli += [f"--{k}", str(v)]

    return cli


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class StartRequest(BaseModel):
    pipeline: str
    args: dict = {}


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def get_config():
    gdal_bin_dir = gdal_env.detect_gdal()
    gdal_available = gdal_bin_dir is not None or shutil.which("gdalwarp") is not None
    return {
        "output_dir": str(COMPANION_OUTPUT_DIR),
        "csrf_token": CSRF_TOKEN,
        "gdal_available": gdal_available,
    }


@app.get("/api/pipelines")
async def get_pipelines():
    return PIPELINE_DEFS


@app.get("/api/pipelines/states")
async def get_all_states():
    orch = get_orchestrator()
    return orch.read_all_states()


@app.get("/api/pipelines/{name}/state")
async def get_pipeline_state(name: str):
    orch = get_orchestrator()
    return orch.read_state(name)


@app.post("/api/pipelines/start")
async def start_pipeline(request: StartRequest):
    pipeline_name = request.pipeline
    if pipeline_name not in _PIPELINE_DEF_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline: {pipeline_name!r}")

    defn = _PIPELINE_DEF_MAP[pipeline_name]
    cli_args = _build_cli_args(pipeline_name, request.args)

    orch = get_orchestrator()
    job = PipelineJob(
        pipeline=pipeline_name,
        script=defn["script"],
        args=cli_args,
    )
    await orch.start(job)
    return {"status": "started", "pipeline": pipeline_name, "args": cli_args}


@app.post("/api/pipelines/{pipeline}/cancel")
async def cancel_pipeline(pipeline: str):
    orch = get_orchestrator()
    job = orch.get_job(pipeline)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No running job for pipeline: {pipeline!r}")
    await orch.cancel(job)
    return {"status": "cancelled", "pipeline": pipeline}


@app.get("/api/disk")
async def get_disk():
    output_dir = COMPANION_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    files = []
    total_size = 0
    for entry in sorted(output_dir.iterdir()):
        if entry.is_file():
            size = entry.stat().st_size
            total_size += size
            files.append({"name": entry.name, "size": size})

    disk_usage = shutil.disk_usage(output_dir)

    return {
        "output_dir": str(output_dir),
        "total_size": total_size,
        "disk_free": disk_usage.free,
        "disk_total": disk_usage.total,
        "files": files,
    }


# ---------------------------------------------------------------------------
# Static files and root
# ---------------------------------------------------------------------------

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    url = "http://127.0.0.1:9000"
    print(f"Geographica Companion starting at {url}")
    print(f"Output directory: {COMPANION_OUTPUT_DIR}")
    webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="info")


if __name__ == "__main__":
    main()
