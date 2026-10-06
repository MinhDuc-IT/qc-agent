from datetime import datetime, timedelta, timezone
import logging
import re
from pathlib import Path

import httpx
import jwt

from ...domain.models import QCRun


logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self, app_id: str | None, private_key_path: Path | None, dry_run: bool,
                 publish_pr_review: bool = True):
        self.app_id = app_id
        self.private_key_path = private_key_path
        self.dry_run = dry_run
        self.publish_pr_review = publish_pr_review

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

    async def _publish_pr_review(self, run: QCRun, token: str,
                                 check_url: str | None) -> None:
        ctx = run.trigger_context
        if ctx.pull_request is None:
            return
        marker = f"<!-- agent-qc-review:{run.run_id} -->"
        headers = {"Authorization": f"Bearer {token}",
                   "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient(base_url="https://api.github.com") as client:
            existing = await client.get(
                f"/repos/{ctx.repository.full_name}/pulls/{ctx.pull_request.number}/reviews",
                headers=headers,
            )
            existing.raise_for_status()
            if any(marker in (review.get("body") or "") for review in existing.json()):
                return
            body = self._review_body(run, check_url, marker)
            response = await client.post(
                f"/repos/{ctx.repository.full_name}/pulls/{ctx.pull_request.number}/reviews",
                headers=headers, json={"event": "COMMENT", "body": body},
            )
            response.raise_for_status()

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
        conclusion = {
            "pass": "success", "fail": "failure", "warning": "neutral",
            "skipped": "skipped", "unknown": "failure",
        }.get(run.verdict, "failure")
        output = self._summary(run)
        annotations = []
        for result in run.results:
            if result.capability.startswith("security."):
                continue
            for finding in result.findings:
                if finding.path and finding.start_line:
                    annotations.append({
                        "path": finding.path, "start_line": finding.start_line,
                        "end_line": finding.start_line,
                        "annotation_level": "failure" if finding.severity in {"critical", "high"} else "warning",
                        "message": finding.message[:65535], "title": finding.title[:255],
                    })
        async with httpx.AsyncClient(base_url="https://api.github.com") as client:
            response = await client.patch(
                f"/repos/{ctx.repository.full_name}/check-runs/{run.check_run_id}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={"status": "completed", "conclusion": conclusion,
                      "output": {"title": self._review_title(run),
                                 "summary": output[:65535] or "No QC tasks were selected.",
                                 "annotations": annotations[:50]}},
            )
            response.raise_for_status()
            check_url = response.json().get("html_url")
        if self.publish_pr_review and run.trigger_context.pull_request is not None:
            try:
                await self._publish_pr_review(run, token, check_url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {403, 404}:
                    raise
                logger.warning(
                    "PR review was not published; grant GitHub App Pull requests write permission: %s",
                    exc,
                )

    @staticmethod
    def _compact_prose(value: str, limit: int = 255) -> str:
        text = " ".join(value.replace("`", "").replace("**", "").split())
        if len(text) <= limit:
            return text
        window = text[:limit]
        sentence_boundary = window.rfind(". ")
        boundary = sentence_boundary if sentence_boundary >= 80 else window.rfind("; ")
        if boundary >= 80:
            return window[:boundary + 1]
        return window[:limit - 1].rstrip() + "…"

    @classmethod
    def _review_body(cls, run: QCRun, check_url: str | None, marker: str) -> str:
        analysis = (run.agent_result_analysis.summary
                    if run.agent_result_analysis else cls._review_title(run))
        lines = [marker, "## Agent-QC review", "", analysis]
        public_findings = [(result, finding) for result in run.results
                           if result.capability != "security.secrets"
                           for finding in result.findings]
        secret_count = sum(len(result.findings) for result in run.results
                           if result.capability == "security.secrets")
        if public_findings or secret_count:
            lines += ["", "### Recommended attention", ""]
            for _, finding in public_findings[:5]:
                location = f" in `{finding.path}`" if finding.path else ""
                lines.append(
                    f"- **{finding.severity.upper()} — {finding.title}**{location}: "
                    f"{finding.message}"
                )
            if secret_count:
                lines.append(
                    f"- **{secret_count} potential secret(s)** require review. Sensitive details "
                    "are intentionally restricted to avoid exposing credentials."
                )
        passed = [cls._capability_label(result.capability) for result in run.results
                  if result.verdict == "pass"]
        if passed:
            lines += ["", "### Confidence from completed checks", "",
                      ", ".join(passed) + "."]
        if run.agent_result_analysis and run.agent_result_analysis.root_causes:
            lines += ["", "### Suggested next step", ""]
            for root_cause in run.agent_result_analysis.root_causes[:3]:
                lines.append(f"- {root_cause.remediation}")
        if check_url:
            lines += ["", f"[Open the full Agent-QC report with worker evidence and metrics]({check_url})"]
        return "\n".join(lines)[:65535]

    @classmethod
    def _review_title(cls, run: QCRun) -> str:
        """Human review summary shown on the collapsed PR check row."""
        if run.error:
            return "Review could not complete because the QC infrastructure failed"
        total = len(run.results)
        passed = sum(result.verdict == "pass" for result in run.results)
        errors = sum(result.execution_status != "completed" for result in run.results)
        secret_count = sum(len(result.findings) for result in run.results
                           if result.capability == "security.secrets")
        if secret_count:
            return ("The review found a potential secret that needs attention before merge. "
                    "Sensitive values and locations are hidden from this public summary.")
        findings = [finding for result in run.results
                    if result.capability != "security.secrets"
                    for finding in result.findings]
        if run.verdict == "fail":
            if findings:
                finding = findings[0]
                if finding.category == "security.sca":
                    title = re.search(r"(CVE-\d{4}-\d+) in ([^\s]+)", finding.title)
                    versions = re.search(
                        r"Installed ([^;]+); fixed in ([^;\s]+)", finding.message
                    )
                    if title and versions:
                        fixed = versions.group(2).rstrip(".,")
                        return (f"Dependency risk: {title.group(2)} {versions.group(1)} is affected "
                                f"by {title.group(1)}; upgrade to {fixed}.")
                return cls._compact_prose(
                    f"{cls._capability_label(finding.category)} needs attention: "
                    f"{finding.title}.", 120
                )
            return "The review found a blocking quality issue that should be resolved before merge."
        if run.verdict == "unknown" or errors:
            return ("The review is inconclusive because one or more quality checks could not "
                    "finish reliably; inspect the detailed report before merging.")
        if run.verdict == "warning":
            return "The change looks acceptable overall, but the review found warnings worth addressing."
        if run.verdict == "pass":
            return ("The tested behavior, security checks, and measured performance show no "
                    "blocking concerns for this change.")
        return "The review completed, but this change did not require any applicable quality checks."

    @staticmethod
    def _capability_label(capability: str) -> str:
        return {
            "unit.run": "Unit tests",
            "functional.e2e": "Functional journey",
            "functional.api": "API behavior",
            "integration.service": "Service integration",
            "performance.smoke": "Performance smoke test",
            "performance.load": "Performance load test",
            "security.sast": "Source security (SAST)",
            "security.sca": "Dependency security (SCA)",
            "security.secrets": "Secret scanning",
        }.get(capability, capability.replace(".", " / ").replace("_", " ").title())

    @staticmethod
    def _result_detail(result) -> str:
        parts = []
        for key, value in result.summary.items():
            if key in {"script", "test_path", "keploy_root"}:
                continue
            parts.append(f"{key.replace('_', ' ')}: {value}")
        for key, value in result.metrics.items():
            parts.append(f"{key.replace('_', ' ')}: {value:g}")
        if result.error:
            parts.append(f"{result.error.code}: {result.error.message}")
        return "; ".join(parts)[:800] or "Completed without additional measurements"

    @classmethod
    def _summary(cls, run: QCRun) -> str:
        if run.error:
            return ("## Review summary\n\nAgent-QC could not finish the review because its "
                    "infrastructure failed. This is not evidence of a code defect.\n\n"
                    f"**Infrastructure error:** `{run.error}`")
        if not run.results:
            return "## Review summary\n\nNo compatible QC tasks were selected."

        icons = {"pass": "✅", "fail": "❌", "warning": "⚠️",
                 "skipped": "⏭️", "unknown": "❓"}
        passed = [result for result in run.results if result.verdict == "pass"]
        failed = [result for result in run.results if result.verdict == "fail"]
        incomplete = [result for result in run.results
                      if result.execution_status != "completed"]
        public_findings = [(result, finding) for result in run.results
                           if result.capability != "security.secrets"
                           for finding in result.findings]
        secret_count = sum(len(result.findings) for result in run.results
                           if result.capability == "security.secrets")

        lines = ["## Review summary", ""]
        if run.verdict == "pass":
            lines.append(
                f"Agent-QC completed **{len(run.results)} checks** and found no blocking issues. "
                "The tested behavior, security scans, and measured performance are acceptable."
            )
        elif run.verdict == "fail":
            lines.append(
                f"Agent-QC completed **{len(run.results)} checks**: **{len(passed)} passed** and "
                f"**{len(failed)} failed**. The pull request needs attention before merge."
            )
        else:
            lines.append(
                f"Agent-QC completed {len(run.results) - len(incomplete)} of "
                f"{len(run.results)} checks. **{len(incomplete)} check(s) did not finish "
                "reliably**, so the result is inconclusive."
            )

        if public_findings or secret_count:
            lines += ["", "### What needs attention", ""]
            for _, finding in public_findings[:20]:
                location = f" in `{finding.path}`" if finding.path else ""
                if finding.start_line:
                    location += f" at line {finding.start_line}"
                lines.append(
                    f"- **{finding.severity.upper()} — {finding.title}**{location}: "
                    f"{finding.message}"
                )
            if secret_count:
                lines.append(
                    f"- **{secret_count} potential secret(s) detected.** Sensitive values and "
                    "file paths are intentionally hidden from the public check output."
                )

        if passed:
            labels = ", ".join(cls._capability_label(item.capability) for item in passed)
            lines += ["", "### What passed", "", f"{labels}."]

        lines += ["", "### Check results", "",
                  "| Check | Worker | Result | Evidence and measurements |",
                  "|---|---|---|---|"]
        for result in run.results:
            detail = cls._result_detail(result)
            if result.capability == "security.secrets":
                detail = f"{len(result.findings)} private security finding(s)"
            lines.append(
                f"| {cls._capability_label(result.capability)} | "
                f"`{result.worker_id or 'unavailable'}` | "
                f"{icons.get(result.verdict, '')} **{result.verdict}** | {detail} |"
            )

        lines += ["", "### Agent review", "", f"**Mode:** `{run.agent_mode}`"]
        if run.agent_plan and run.agent_plan.summary:
            lines += ["", f"**Change risk assessment:** {run.agent_plan.summary}"]
        if run.agent_plan and run.agent_plan.risks:
            lines.extend(
                f"- **{risk.severity.upper()} / {risk.category}:** {risk.description}"
                for risk in run.agent_plan.risks[:10]
            )
        if run.agent_result_analysis:
            lines += ["", "**Overall analysis:** " + run.agent_result_analysis.summary]
            for root_cause in run.agent_result_analysis.root_causes[:10]:
                lines.append(
                    f"- **Root cause ({root_cause.confidence:.0%} confidence):** "
                    f"{root_cause.root_cause} — **Recommended action:** "
                    f"{root_cause.remediation}"
                )
            if run.agent_result_analysis.residual_risks:
                lines += ["", "**Residual risks:**"]
                lines.extend(f"- {risk}" for risk in
                             run.agent_result_analysis.residual_risks[:10])
        return "\n".join(lines)
