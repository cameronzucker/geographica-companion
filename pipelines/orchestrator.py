"""Parallel pipeline subprocess coordinator.

Each pipeline runs as a separate Python subprocess, providing natural isolation
from module-level globals, signal handlers, and POSIX-specific code.
"""

import asyncio
import json
import os
import platform
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


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
        cmd = [sys.executable, script_path] + job.args
        kwargs = {
            "env": self._env if self._env else None,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
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
                job.process.terminate()
            job.status = "cancelled"

    async def cancel_all(self) -> None:
        for job in self._jobs.values():
            if job.status == "running":
                await self.cancel(job)

    def read_state(self, pipeline: str) -> dict:
        state_file = self._output_dir / f".{pipeline}-state.json"
        if not state_file.exists():
            return {}
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
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
            stderr = job.process.stderr.read().decode() if job.process.stderr else ""
            job.error = stderr[-500:] if stderr else f"Exit code {returncode}"
        return returncode
