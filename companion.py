"""Geographica Companion — FastAPI backend.

Serves the local web UI and provides API endpoints for managing data
pipelines (imagery acquisition, elevation, POI import). Binds exclusively
to 127.0.0.1 for security.
"""

import asyncio
import logging
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

log = logging.getLogger("companion")
from pipelines.orchestrator import Orchestrator, PipelineJob

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COMPANION_OUTPUT_DIR = Path(os.environ.get(
    "COMPANION_OUTPUT_DIR",
    str(Path(__file__).parent / "geographica-data")
))
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
        scripts_dir = Path(__file__).parent / "pipelines"
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
    """Build pipeline-specific CLI argument list from request body args dict.

    Uses --key=value syntax for values that may start with '-' (like bbox
    coordinates with negative longitudes) to avoid argparse misinterpretation
    on Windows where subprocess list-to-string conversion can be lossy.
    """
    cli: list[str] = []

    # Common args present in most pipelines — strip whitespace from bbox
    bbox = (args.get("bbox") or "").strip() or None
    output = args.get("output") or str(COMPANION_OUTPUT_DIR / f"{pipeline_name}.mbtiles")
    staging = args.get("staging") or str(COMPANION_OUTPUT_DIR / "staging")

    if pipeline_name == "basemap":
        cli += ["--mode", "tnmaccess"]
        cli += [f"--zoom={args.get('zoom', '0-14')}"]
        if bbox:
            cli += [f"--bbox={bbox}"]
        cli += [f"--output={output}", f"--staging={staging}"]

    elif pipeline_name == "noaa":
        cli += ["--mode", "noaa"]
        if bbox:
            cli += [f"--bbox={bbox}"]
        state = args.get("state")
        if state:
            cli += [f"--state={state}"]
        cli += [f"--output={output}", f"--staging={staging}"]

    elif pipeline_name == "m2m":
        cli += ["--mode", "m2m"]
        if bbox:
            cli += [f"--bbox={bbox}"]
        username = args.get("m2m_username") or args.get("m2m-username")
        token = args.get("m2m_token") or args.get("m2m-token")
        if username:
            cli += [f"--m2m-username={username}"]
        if token:
            cli += [f"--m2m-token={token}"]
        cli += [f"--output={output}", f"--staging={staging}"]

    elif pipeline_name == "sentinel":
        if bbox:
            cli += [f"--bbox={bbox}"]
        api_key = args.get("api_key") or args.get("api-key")
        if api_key:
            cli += [f"--api-key={api_key}"]
        cli += [f"--output={output}", f"--staging={staging}"]

    elif pipeline_name == "elevation":
        if bbox:
            cli += [f"--bbox={bbox}"]
        cli += [f"--zoom={args.get('zoom', '0-14')}"]
        cli += [f"--output={output}"]

    elif pipeline_name == "import":
        source = args.get("source")
        if source:
            cli += [f"--input={source}"]
        cli += [f"--output={output}"]

    else:
        # Generic fallback: pass all args as --key=value pairs
        for k, v in args.items():
            cli += [f"--{k}={v}"]

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
    gdal_available = gdal_bin_dir is not None
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


@app.get("/api/pipelines/{name}/debug")
async def debug_pipeline(name: str):
    """Debug endpoint — returns raw job state including error output."""
    orch = get_orchestrator()
    job = orch.get_job(name)
    if job is None:
        return {"job": None, "state": orch.read_state(name)}
    return {
        "job": {
            "pipeline": job.pipeline,
            "script": job.script,
            "args": job.args,
            "status": job.status,
            "error": job.error,
            "pid": job.process.pid if job.process else None,
            "returncode": job.process.poll() if job.process else None,
        },
        "state": orch.read_state(name),
    }


_PIPELINES_REQUIRING_GDAL = {"basemap", "noaa", "m2m", "sentinel", "elevation", "import"}


@app.post("/api/pipelines/start")
async def start_pipeline(request: StartRequest):
    pipeline_name = request.pipeline
    if pipeline_name not in _PIPELINE_DEF_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline: {pipeline_name!r}")

    # Check GDAL/rasterio availability before wasting time
    if pipeline_name in _PIPELINES_REQUIRING_GDAL:
        gdal_bin_dir = gdal_env.detect_gdal()
        if gdal_bin_dir is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "rasterio is required but not installed. "
                    "Run: pip install rasterio fiona numpy"
                ),
            )

    defn = _PIPELINE_DEF_MAP[pipeline_name]
    cli_args = _build_cli_args(pipeline_name, request.args)

    orch = get_orchestrator()
    job = PipelineJob(
        pipeline=pipeline_name,
        script=defn["script"],
        args=cli_args,
    )
    await orch.start(job)
    log.info("Started pipeline %s: %s %s", pipeline_name, defn["script"], cli_args)
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
# Transfer and deploy endpoints
# ---------------------------------------------------------------------------

OUTPUT_DIR = COMPANION_OUTPUT_DIR  # alias for clarity in transfer/deploy endpoints


@app.post("/api/transfer/test")
async def test_transfer_connection(request: Request):
    body = await request.json()
    from transfer import test_connection

    result = await asyncio.to_thread(
        test_connection,
        host=body["host"],
        username=body["username"],
        password=body.get("password"),
        key_path=body.get("key_path"),
    )
    return {
        "ssh_ok": result.ssh_ok,
        "rsync_available": result.rsync_available,
        "data_dir_writable": result.data_dir_writable,
        "docker_ok": result.docker_ok,
        "disk_free_bytes": result.disk_free_bytes,
        "repo_path": result.repo_path,
        "transfer_method": result.transfer_method,
        "error": result.error,
    }


@app.post("/api/transfer/start")
async def start_transfer(request: Request):
    body = await request.json()
    files = list(OUTPUT_DIR.glob("*.mbtiles"))
    if not files:
        raise HTTPException(status_code=404, detail="No MBTiles files to transfer")

    from transfer import transfer_all

    auth_type = body.get("auth_type", "password")
    results = await transfer_all(
        files=files,
        remote_host=body["host"],
        remote_user=body["username"],
        remote_dir="/srv/geographica/data/",
        auth_type=auth_type,
        password=body.get("password"),
        key_path=body.get("key_path"),
        progress_callback=None,
    )
    return {"results": results}


@app.post("/api/deploy")
async def deploy(request: Request):
    body = await request.json()
    from deploy import deploy_to_pi

    filenames = [f.name for f in OUTPUT_DIR.glob("*.mbtiles")]
    result = await asyncio.to_thread(
        deploy_to_pi,
        host=body["host"],
        username=body["username"],
        password=body.get("password"),
        key_path=body.get("key_path"),
        repo_path=body.get("repo_path", "/home/administrator/Code/geographica"),
        filenames=filenames,
    )
    return result


@app.get("/api/deploy/script")
async def get_deploy_script(repo_path: str = "/home/administrator/Code/geographica"):
    from deploy import generate_deploy_script

    filenames = [f.name for f in OUTPUT_DIR.glob("*.mbtiles")]
    script = generate_deploy_script(filenames, repo_path)
    return {"script": script}


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
