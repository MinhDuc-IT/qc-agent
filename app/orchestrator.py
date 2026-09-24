import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .contracts import QCRun, WorkerResult
from .github import GitHubClient
from .store import Store


class Orchestrator:
    def __init__(self, settings: Settings, store: Store, github: GitHubClient):
        self.settings = settings
        self.store = store
        self.github = github

    async def execute(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not run:
            return
        workspace = (self.settings.agent_qc_workspace_dir / run.run_id).resolve()
        try:
            run.status = "preparing"
            run.check_run_id = await self.github.create_check(run)
            self.store.save_run(run)
            await self._prepare(run, workspace)
            run.status = "running"
            self.store.save_run(run)
            task_id = f"task_{uuid4().hex}"
            process = await asyncio.to_thread(
                subprocess.run,
                self.settings.agent_qc_worker_command,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "AGENT_QC_RUN_ID": run.run_id, "AGENT_QC_TASK_ID": task_id},
                shell=True,
                timeout=900,
                check=False,
            )
            text = process.stdout.decode(errors="replace")
            verdict = "pass" if process.returncode == 0 else "fail"
            run.result = WorkerResult(task_id=task_id, run_id=run.run_id,
                                      execution_status="completed", verdict=verdict,
                                      exit_code=process.returncode, output=text)
            run.status = "completed"
            run.verdict = verdict
        except Exception as exc:
            run.status = "failed"
            run.verdict = "unknown"
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.store.save_run(run)
            await self.github.complete_check(run)
            if workspace.exists():
                shutil.rmtree(workspace)

    async def _prepare(self, run: QCRun, workspace: Path) -> None:
        ctx = run.trigger_context
        workspace.parent.mkdir(parents=True, exist_ok=True)
        clone_url = ctx.repository.clone_url
        if not self.settings.agent_qc_dry_run:
            token = await self.github.installation_token(ctx.installation.id)
            clone_url = clone_url.replace("https://", f"https://x-access-token:{token}@", 1)
        clone_args = ["git"]
        local_source = Path(clone_url)
        if local_source.exists():
            # Needed on shared/portable Windows volumes used by this local demo.
            clone_args += ["-c", f"safe.directory={(local_source / '.git').as_posix()}"]
        clone_args += ["clone", "--no-checkout", clone_url, str(workspace)]
        proc = await asyncio.to_thread(
            subprocess.run,
            clone_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError(f"clone failed: {proc.stderr.decode(errors='replace')}")
        proc = await asyncio.to_thread(
            subprocess.run,
            ["git", "-c", f"safe.directory={workspace.as_posix()}",
             "checkout", "--detach", ctx.revision.head_sha],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError(f"checkout failed: {proc.stderr.decode(errors='replace')}")
