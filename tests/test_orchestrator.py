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
