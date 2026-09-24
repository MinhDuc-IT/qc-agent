from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt

from .contracts import QCRun


class GitHubClient:
    def __init__(self, app_id: str | None, private_key_path: Path | None, dry_run: bool):
        self.app_id = app_id
        self.private_key_path = private_key_path
        self.dry_run = dry_run

    async def installation_token(self, installation_id: int) -> str:
        if not self.app_id or not self.private_key_path:
            raise RuntimeError("GitHub App credentials are not configured")
        now = datetime.now(timezone.utc)
        app_jwt = jwt.encode(
            {"iat": now - timedelta(seconds=60), "exp": now + timedelta(minutes=9), "iss": self.app_id},
            self.private_key_path.read_text(encoding="utf-8"),
            algorithm="RS256",
        )
        async with httpx.AsyncClient(base_url="https://api.github.com") as client:
            response = await client.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            return response.json()["token"]

    async def create_check(self, run: QCRun) -> int | None:
        if self.dry_run:
            return None
        ctx = run.trigger_context
        token = await self.installation_token(ctx.installation.id)
        async with httpx.AsyncClient(base_url="https://api.github.com") as client:
            response = await client.post(
                f"/repos/{ctx.repository.full_name}/check-runs",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={"name": "Agent-QC", "head_sha": ctx.revision.head_sha, "status": "in_progress",
                      "output": {"title": "Agent-QC is running", "summary": "Quality checks are being executed."}},
            )
            response.raise_for_status()
            return response.json()["id"]

    async def complete_check(self, run: QCRun) -> None:
        if self.dry_run or run.check_run_id is None:
            return
        ctx = run.trigger_context
        token = await self.installation_token(ctx.installation.id)
        conclusion = "success" if run.verdict == "pass" else "failure"
        output = (run.result.output if run.result else run.error or "No result")[-60000:]
        async with httpx.AsyncClient(base_url="https://api.github.com") as client:
            response = await client.patch(
                f"/repos/{ctx.repository.full_name}/check-runs/{run.check_run_id}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={"status": "completed", "conclusion": conclusion,
                      "output": {"title": f"Agent-QC: {run.verdict}", "summary": output or "Worker completed."}},
            )
            response.raise_for_status()

