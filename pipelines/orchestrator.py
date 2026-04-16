"""Parallel pipeline subprocess coordinator.

Each pipeline runs as a separate Python subprocess, providing natural isolation
from module-level globals, signal handlers, and POSIX-specific code.
"""

import asyncio
import json
import logging
import os
import platform
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("orchestrator")


@dataclass
class PipelineJob:
    pipeline: str
    script: str
    args: list[str]
    status: str = "pending"
    process: subprocess.Popen | None = None
    error: str | None = None

    @property
    def state_filename(self) -> str:
        return f".{self.pipeline}-state.json"


_STATE_FILE_MAP = {
    "basemap": "tnmaccess",
    "noaa": "noaa",
    "m2m": "m2m",
}


class Orchestrator:
    def __init__(self, pipelines_dir: Path, output_dir: Path, env: dict):
        self._pipelines_dir = pipelines_dir
        self._output_dir = output_dir
        self._env = env
        self._jobs: dict[str, PipelineJob] = {}

    async def start(self, job: PipelineJob) -> None:
        script_path = job.script
        if not os.path.isabs(script_path):
            script_path = str(self._pipelines_dir / script_path)
        cmd = [sys.executable, "-u", script_path] + job.args
        kwargs = {
            "env": self._env if self._env else None,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,  # merge into stdout to prevent pipe deadlock
        }
        if platform.system() != "Windows":
            kwargs["preexec_fn"] = os.setsid
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        job.process = subprocess.Popen(cmd, **kwargs)
        job.status = "running"
        self._jobs[job.pipeline] = job

    async def cancel(self, job: PipelineJob) -> None:
        if job.process and job.process.poll() is None:
            if platform.system() != "Windows":
                try:
                    os.killpg(os.getpgid(job.process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                # Windows: kill the entire process tree via taskkill /T
                # terminate() only kills the parent; child threads/processes survive
                pid = job.process.pid
                try:
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=10,
                    )
                except (subprocess.SubprocessError, FileNotFoundError):
                    # Fallback if taskkill unavailable
                    job.process.kill()
                try:
                    job.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            job.status = "cancelled"
        # Clean up state file so next run starts fresh
        state_name = _STATE_FILE_MAP.get(job.pipeline, job.pipeline)
        state_file = self._output_dir / f".{state_name}-state.json"
        if state_file.exists():
            try:
                state_file.unlink()
                log.info("Removed stale state file: %s", state_file)
            except OSError:
                pass
        # Remove from jobs so pipeline can be restarted
        self._jobs.pop(job.pipeline, None)

    async def cancel_all(self) -> None:
        for job in list(self._jobs.values()):
            if job.status == "running":
                await self.cancel(job)

    def read_state(self, pipeline: str) -> dict:
        # Check if process has exited
        job = self._jobs.get(pipeline)
        if job and job.process and job.status == "running":
            rc = job.process.poll()
            if rc is not None:
                # Process exited — capture output (stderr merged into stdout)
                output = ""
                try:
                    output = job.process.stdout.read().decode(errors="replace") if job.process.stdout else ""
                except Exception:
                    pass
                job.status = "completed" if rc == 0 else "failed"
                job.error = output[-1000:] if output else (f"Exit code {rc}" if rc != 0 else None)
                if rc != 0:
                    log.error("Pipeline %s exited with code %d:\n%s",
                              job.pipeline, rc, output[-2000:] if output else "(no output)")

        # Job failure/cancellation takes priority over stale state files.
        # A state file from a previous run must not mask a fresh crash.
        if job and job.status == "failed":
            return {"status": "failed", "error": job.error or "Pipeline exited without writing state"}
        if job and job.status == "cancelled":
            return {"status": "cancelled", "error": job.error or "Cancelled by user"}

        # Read state file if it exists (only reached for running/completed jobs)
        state_name = _STATE_FILE_MAP.get(pipeline, pipeline)
        state_file = self._output_dir / f".{state_name}-state.json"
        if state_file.exists():
            try:
                return json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        # No state file
        if job and job.status == "completed":
            return {"status": "done"}
        if job and job.status == "running":
            return {"status": "starting"}
        return {}

    def read_all_states(self) -> dict[str, dict]:
        return {name: self.read_state(name) for name in self._jobs}

    def list_jobs(self) -> dict[str, PipelineJob]:
        return dict(self._jobs)

    def get_job(self, pipeline: str) -> PipelineJob | None:
        return self._jobs.get(pipeline)

    async def wait_for(self, job: PipelineJob) -> int:
        if not job.process:
            return -1
        loop = asyncio.get_event_loop()
        returncode = await loop.run_in_executor(None, job.process.wait)
        job.status = "completed" if returncode == 0 else "failed"
        if returncode != 0:
            output = job.process.stdout.read().decode(errors="replace") if job.process.stdout else ""
            job.error = output[-500:] if output else f"Exit code {returncode}"
        return returncode
