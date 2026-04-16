"""Tests for companion.py FastAPI backend."""

import json
import os
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import httpx
from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
def set_output_dir(tmp_path):
    """Point COMPANION_OUTPUT_DIR at a temp dir for all tests."""
    os.environ["COMPANION_OUTPUT_DIR"] = str(tmp_path)
    yield tmp_path
    del os.environ["COMPANION_OUTPUT_DIR"]


@pytest.fixture
def app():
    """Import app fresh after env var is set."""
    # Re-import to pick up the COMPANION_OUTPUT_DIR set above
    import importlib
    import companion
    importlib.reload(companion)
    return companion.app


@pytest.fixture
def csrf_token(app):
    """Get the CSRF token from the live module."""
    import companion
    return companion.CSRF_TOKEN


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client wired to the ASGI app."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:9000") as c:
        yield c


# ---------------------------------------------------------------------------
# CSRF middleware
# ---------------------------------------------------------------------------

class TestCSRF:
    @pytest.mark.asyncio
    async def test_post_without_csrf_token_returns_403(self, client):
        resp = await client.post("/api/pipelines/start", json={"pipeline": "noaa", "args": {}})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_post_with_invalid_csrf_token_returns_403(self, client):
        resp = await client.post(
            "/api/pipelines/start",
            json={"pipeline": "noaa", "args": {}},
            headers={"X-CSRF-Token": "bad-token"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_post_with_valid_csrf_token_passes_csrf_check(self, client, csrf_token):
        """Valid CSRF token should not get a 403 (may get other errors, but not CSRF block)."""
        resp = await client.post(
            "/api/pipelines/start",
            json={"pipeline": "noaa", "args": {}},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_get_requests_do_not_require_csrf(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_without_csrf_returns_403(self, client):
        resp = await client.delete("/api/pipelines/noaa/cancel")
        # DELETE is blocked by CSRF if no token
        # (Note: cancel uses POST, but test the method filtering)
        # Actually cancel is POST — just test that we get CSRF block on arbitrary DELETE
        # This endpoint doesn't exist so we should get 403 from CSRF, not 404
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------

class TestConfig:
    @pytest.mark.asyncio
    async def test_returns_csrf_token(self, client, csrf_token):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["csrf_token"] == csrf_token

    @pytest.mark.asyncio
    async def test_returns_output_dir(self, client, set_output_dir):
        resp = await client.get("/api/config")
        data = resp.json()
        assert "output_dir" in data
        assert data["output_dir"] == str(set_output_dir)

    @pytest.mark.asyncio
    async def test_returns_gdal_available(self, client):
        resp = await client.get("/api/config")
        data = resp.json()
        assert "gdal_available" in data
        assert isinstance(data["gdal_available"], bool)


# ---------------------------------------------------------------------------
# GET /api/pipelines
# ---------------------------------------------------------------------------

class TestPipelinesList:
    @pytest.mark.asyncio
    async def test_returns_list(self, client):
        resp = await client.get("/api/pipelines")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_contains_known_pipeline_names(self, client):
        resp = await client.get("/api/pipelines")
        names = {p["name"] for p in resp.json()}
        assert "basemap" in names
        assert "noaa" in names
        assert "m2m" in names
        assert "elevation" in names
        assert "import" in names

    @pytest.mark.asyncio
    async def test_each_pipeline_has_required_fields(self, client):
        resp = await client.get("/api/pipelines")
        for pipeline in resp.json():
            assert "name" in pipeline
            assert "label" in pipeline
            assert "script" in pipeline
            assert "auth" in pipeline


# ---------------------------------------------------------------------------
# GET /api/pipelines/{name}/state
# ---------------------------------------------------------------------------

class TestPipelineState:
    @pytest.mark.asyncio
    async def test_noaa_state_returns_200(self, client):
        resp = await client.get("/api/pipelines/noaa/state")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_state_returns_empty_dict_when_no_state_file(self, client):
        resp = await client.get("/api/pipelines/noaa/state")
        assert resp.json() == {}

    @pytest.mark.asyncio
    async def test_state_returns_json_from_state_file(self, client, set_output_dir):
        state = {"status": "running", "items_done": 3, "items_total": 10}
        (set_output_dir / ".noaa-state.json").write_text(json.dumps(state))
        resp = await client.get("/api/pipelines/noaa/state")
        data = resp.json()
        assert data["status"] == "running"
        assert data["items_done"] == 3

    @pytest.mark.asyncio
    async def test_unknown_pipeline_state_returns_empty(self, client):
        resp = await client.get("/api/pipelines/unknown_pipeline/state")
        assert resp.status_code == 200
        assert resp.json() == {}


# ---------------------------------------------------------------------------
# GET /api/pipelines/states
# ---------------------------------------------------------------------------

class TestAllStates:
    @pytest.mark.asyncio
    async def test_returns_dict(self, client):
        resp = await client.get("/api/pipelines/states")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)


# ---------------------------------------------------------------------------
# POST /api/pipelines/start
# ---------------------------------------------------------------------------

class TestPipelineStart:
    """Tests that the /start endpoint validates, builds args, and delegates to orchestrator.

    Uses a mock orchestrator to prevent spawning real pipeline subprocesses —
    real subprocess tests caused orphaned gdal_translate processes that OOM'd the Pi.
    Arg correctness is verified separately in test_build_cli_args.py and
    test_argparse_contract.py.
    """

    @pytest.mark.asyncio
    async def test_start_noaa_pipeline(self, client, csrf_token, set_output_dir):
        mock_orch = MagicMock()
        mock_orch.start = AsyncMock()
        with patch("companion.get_orchestrator", return_value=mock_orch), \
             patch("companion.gdal_env.detect_gdal", return_value=Path("/fake/gdal")):
            resp = await client.post(
                "/api/pipelines/start",
                json={
                    "pipeline": "noaa",
                    "args": {
                        "bbox": "-112.1,33.4,-111.9,33.6",
                        "state": "AZ",
                    },
                },
                headers={"X-CSRF-Token": csrf_token},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert data["pipeline"] == "noaa"
        assert any("--bbox=" in a for a in data["args"])
        mock_orch.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_unknown_pipeline_returns_400(self, client, csrf_token):
        resp = await client.post(
            "/api/pipelines/start",
            json={"pipeline": "nonexistent", "args": {}},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 400
        assert "nonexistent" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_start_basemap_pipeline_returns_args(self, client, csrf_token, set_output_dir):
        mock_orch = MagicMock()
        mock_orch.start = AsyncMock()
        with patch("companion.get_orchestrator", return_value=mock_orch), \
             patch("companion.gdal_env.detect_gdal", return_value=Path("/fake/gdal")):
            resp = await client.post(
                "/api/pipelines/start",
                json={
                    "pipeline": "basemap",
                    "args": {
                        "bbox": "-112.1,33.4,-111.9,33.6",
                    },
                },
                headers={"X-CSRF-Token": csrf_token},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "--mode" in data["args"]
        assert "--bbox=-112.1,33.4,-111.9,33.6" in data["args"]

    @pytest.mark.asyncio
    async def test_start_pipeline_blocked_without_gdal(self, client, csrf_token):
        """Pipeline start must be rejected when GDAL is not available."""
        with patch("companion.gdal_env.detect_gdal", return_value=None), \
             patch("companion.shutil.which", return_value=None):
            resp = await client.post(
                "/api/pipelines/start",
                json={"pipeline": "noaa", "args": {"bbox": "-112,33,-111,34"}},
                headers={"X-CSRF-Token": csrf_token},
            )
        assert resp.status_code == 400
        assert "rasterio" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /api/pipelines/{pipeline}/cancel
# ---------------------------------------------------------------------------

class TestPipelineCancel:
    @pytest.mark.asyncio
    async def test_cancel_nonrunning_pipeline_returns_200_or_404(self, client, csrf_token):
        resp = await client.post(
            "/api/pipelines/noaa/cancel",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_cancel_requires_csrf(self, client):
        resp = await client.post("/api/pipelines/noaa/cancel")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/disk
# ---------------------------------------------------------------------------

class TestDisk:
    @pytest.mark.asyncio
    async def test_returns_200(self, client):
        resp = await client.get("/api/disk")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_expected_fields(self, client):
        resp = await client.get("/api/disk")
        data = resp.json()
        assert "output_dir" in data
        assert "total_size" in data
        assert "disk_free" in data
        assert "disk_total" in data
        assert "files" in data

    @pytest.mark.asyncio
    async def test_files_is_list(self, client):
        resp = await client.get("/api/disk")
        data = resp.json()
        assert isinstance(data["files"], list)

    @pytest.mark.asyncio
    async def test_disk_counts_files_in_output_dir(self, client, set_output_dir):
        # Create a test file in the output dir
        (set_output_dir / "test.mbtiles").write_bytes(b"x" * 1024)
        resp = await client.get("/api/disk")
        data = resp.json()
        names = [f["name"] for f in data["files"]]
        assert "test.mbtiles" in names

    @pytest.mark.asyncio
    async def test_each_file_has_name_and_size(self, client, set_output_dir):
        (set_output_dir / "sample.mbtiles").write_bytes(b"y" * 512)
        resp = await client.get("/api/disk")
        for f in resp.json()["files"]:
            assert "name" in f
            assert "size" in f
            assert isinstance(f["size"], int)
