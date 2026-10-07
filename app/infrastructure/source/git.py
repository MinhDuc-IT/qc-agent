import asyncio
import base64
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from ...domain.models import QCRun


class InstallationTokenProvider(Protocol):
    async def installation_token(self, installation_id: int) -> str: ...


class GitRepositoryManager:
    def __init__(self, workspace_root: Path, token_provider: InstallationTokenProvider, dry_run: bool):
        self.workspace_root = workspace_root
        self.token_provider = token_provider
        self.dry_run = dry_run

    async def prepare(self, run: QCRun) -> Path:
        workspace_root = self.workspace_root.resolve()
        workspace = (workspace_root / run.run_id).resolve()
        if workspace.parent != workspace_root:
            raise RuntimeError(f"invalid run workspace: {workspace}")
        workspace_root.mkdir(parents=True, exist_ok=True)
        # A process restart can recover an interrupted run from the durable
        # queue while its partial checkout still exists. The workspace belongs
        # exclusively to this run, so discard it before performing a fresh,
        # immutable checkout of head_sha.
        if workspace.exists():
            await asyncio.to_thread(shutil.rmtree, workspace)
        ctx = run.trigger_context
        clone_url = ctx.repository.clone_url
        clone_args = ["git"]
        local_source = Path(clone_url)
        if local_source.exists():
            clone_args += ["-c", f"safe.directory={(local_source / '.git').as_posix()}"]
        elif not self.dry_run:
            token = await self.token_provider.installation_token(ctx.installation.id)
            credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            clone_args += ["-c", f"http.extraHeader=Authorization: Basic {credential}"]
        clone_args += ["clone", "--no-checkout", clone_url, str(workspace)]
        process = await asyncio.to_thread(
            subprocess.run, clone_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        if process.returncode:
            raise RuntimeError(f"clone failed: {process.stderr.decode(errors='replace')}")
        process = await asyncio.to_thread(
            subprocess.run,
            ["git", "-c", f"safe.directory={workspace.as_posix()}",
             "checkout", "--detach", ctx.revision.head_sha],
            cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if process.returncode:
            raise RuntimeError(f"checkout failed: {process.stderr.decode(errors='replace')}")
        return workspace

    def cleanup(self, workspace: Path) -> None:
        if workspace.exists():
            shutil.rmtree(workspace)
