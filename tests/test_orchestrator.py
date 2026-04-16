import asyncio
import json
import os
import sys
from pathlib import Path
import pytest

from pipelines.orchestrator import PipelineJob, Orchestrator


class TestPipelineJob:
    def test_job_creation(self):
        job = PipelineJob(
            pipeline="noaa",
            script="acquire_imagery.py",
            args=["--mode", "noaa", "--bbox", "-112,33,-111,34", "--output", "/tmp/test.mbtiles"],
        )
        assert job.pipeline == "noaa"
        assert job.status == "pending"
        assert job.process is None

    def test_state_file_name(self):
        job = PipelineJob(pipeline="noaa", script="acquire_imagery.py", args=[])
        assert job.state_filename == ".noaa-state.json"

    def test_different_pipelines_have_unique_state_files(self):
        job1 = PipelineJob(pipeline="noaa", script="acquire_imagery.py", args=[])
        job2 = PipelineJob(pipeline="elevation", script="download_elevation.py", args=[])
        assert job1.state_filename != job2.state_filename


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_start_pipeline_creates_subprocess(self, tmp_path):
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        script = tmp_path / "fake_pipeline.py"
        script.write_text('import time, sys\nprint("started", flush=True)\ntime.sleep(10)\n')
        job = PipelineJob(pipeline="test", script=str(script), args=[])
        await orch.start(job)
        assert job.status == "running"
        assert job.process is not None
        assert job.process.poll() is None
        await orch.cancel(job)

    @pytest.mark.asyncio
    async def test_cancel_pipeline_terminates_subprocess(self, tmp_path):
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        script = tmp_path / "slow_pipeline.py"
        script.write_text('import time\ntime.sleep(60)\n')
        job = PipelineJob(pipeline="test", script=str(script), args=[])
        await orch.start(job)
        await orch.cancel(job)
        await asyncio.sleep(0.5)
        assert job.process.poll() is not None
        assert job.status == "cancelled"

    @pytest.mark.asyncio
    async def test_read_state_returns_json(self, tmp_path):
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        state = {"source": "noaa", "status": "running", "items_done": 5, "items_total": 10}
        (tmp_path / ".noaa-state.json").write_text(json.dumps(state))
        result = orch.read_state("noaa")
        assert result["items_done"] == 5
        assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_read_state_missing_file_returns_empty(self, tmp_path):
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        result = orch.read_state("nonexistent")
        assert result == {}

    @pytest.mark.asyncio
    async def test_list_jobs(self, tmp_path):
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        script = tmp_path / "noop.py"
        script.write_text('pass\n')
        job1 = PipelineJob(pipeline="a", script=str(script), args=[])
        job2 = PipelineJob(pipeline="b", script=str(script), args=[])
        await orch.start(job1)
        await orch.start(job2)
        jobs = orch.list_jobs()
        assert len(jobs) == 2
        await orch.cancel_all()

    @pytest.mark.asyncio
    async def test_crashed_subprocess_surfaces_error(self, tmp_path):
        """A subprocess that crashes immediately must have its error surfaced."""
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        script = tmp_path / "crash.py"
        script.write_text('import sys\nprint("ImportError: No module named aiohttp", file=sys.stderr)\nsys.exit(1)\n')
        job = PipelineJob(pipeline="crash_test", script=str(script), args=[])
        await orch.start(job)
        await asyncio.sleep(0.5)  # let subprocess exit
        state = orch.read_state("crash_test")
        assert state["status"] == "failed"
        assert "aiohttp" in state.get("error", "")

    @pytest.mark.asyncio
    async def test_crashed_subprocess_not_masked_by_stale_state_file(self, tmp_path):
        """A stale state file from a previous run must not hide a fresh crash."""
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        # Write a stale state file from a "previous run"
        (tmp_path / ".crash_test-state.json").write_text(
            json.dumps({"status": "done", "detail": "old run"})
        )
        script = tmp_path / "crash.py"
        script.write_text('import sys\nsys.exit(1)\n')
        job = PipelineJob(pipeline="crash_test", script=str(script), args=[])
        await orch.start(job)
        await asyncio.sleep(0.5)
        state = orch.read_state("crash_test")
        assert state["status"] == "failed", f"Expected failed, got {state}"

    @pytest.mark.asyncio
    async def test_successful_exit_without_state_file_returns_done(self, tmp_path):
        """A subprocess that exits 0 without writing state should show 'done'."""
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        script = tmp_path / "quick_success.py"
        script.write_text('pass\n')
        job = PipelineJob(pipeline="quick", script=str(script), args=[])
        await orch.start(job)
        await asyncio.sleep(0.5)
        state = orch.read_state("quick")
        assert state.get("status") == "done"

    @pytest.mark.asyncio
    async def test_argparse_error_surfaces_usage_message(self, tmp_path):
        """Simulates a pipeline failing due to bad argparse args."""
        orch = Orchestrator(
            pipelines_dir=Path(__file__).parent.parent / "pipelines",
            output_dir=tmp_path,
            env={},
        )
        script = tmp_path / "argparse_fail.py"
        script.write_text(
            'import argparse, sys\n'
            'p = argparse.ArgumentParser()\n'
            'p.add_argument("--bbox", required=True)\n'
            'p.parse_args()\n'
        )
        job = PipelineJob(pipeline="argfail", script=str(script), args=[])
        await orch.start(job)
        await asyncio.sleep(0.5)
        state = orch.read_state("argfail")
        assert state["status"] == "failed"
        assert "required" in state.get("error", "").lower() or "bbox" in state.get("error", "").lower()
