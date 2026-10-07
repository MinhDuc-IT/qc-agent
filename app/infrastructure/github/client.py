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
                json={"name": "Agent-QC", "head_sha": ctx.revision.head_sha, "status": "queued",
                      "output": {"title": "Agent-QC đang chờ kiểm thử",
                                 "summary": "Đã tiếp nhận yêu cầu đánh giá; lượt kiểm thử đang chờ xử lý."}},
            )
            response.raise_for_status()
            return response.json()["id"]

    async def start_check(self, run: QCRun) -> None:
        if self.dry_run or run.check_run_id is None:
            return
        ctx = run.trigger_context
        token = await self.installation_token(ctx.installation.id)
        async with httpx.AsyncClient(base_url="https://api.github.com") as client:
            response = await client.patch(
                f"/repos/{ctx.repository.full_name}/check-runs/{run.check_run_id}",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                json={"status": "in_progress",
                      "output": {"title": "Agent-QC đang kiểm thử",
                                 "summary": "Đang thực hiện các bước kiểm tra chất lượng."}},
            )
            response.raise_for_status()

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
            if result.capability.startswith("security.") or self._is_confirmation(result):
                continue
            for finding in result.findings:
                if finding.path and finding.start_line:
                    title, message = self._finding_prose(finding)
                    annotations.append({
                        "path": finding.path, "start_line": finding.start_line,
                        "end_line": finding.start_line,
                        "annotation_level": "failure" if finding.severity in {"critical", "high"} else "warning",
                        "message": message[:65535], "title": title[:255],
                    })
        async with httpx.AsyncClient(base_url="https://api.github.com") as client:
            response = await client.patch(
                f"/repos/{ctx.repository.full_name}/check-runs/{run.check_run_id}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                json={"status": "completed", "conclusion": conclusion,
                      "output": {"title": self._review_title(run),
                                 "summary": output[:65535] or "Không có test task nào được chọn.",
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

    @staticmethod
    def _is_confirmation(result) -> bool:
        return result.summary.get("execution_purpose") == "confirmation"

    @staticmethod
    def _finding_prose(finding) -> tuple[str, str]:
        """Localize known adapter prose without changing stored evidence or fingerprints."""
        if finding.title == "Automated quality check failure":
            location = f"{finding.path}:{finding.start_line}" if finding.path else "test log"
            return "Automated test không đạt", f"Ghi nhận lỗi tại {location}. Cần đối chiếu kết quả thực tế với giá trị kỳ vọng."
        title = re.fullmatch(r"(CVE-\d{4}-\d+) in (.+)", finding.title)
        versions = re.fullmatch(r"Installed ([^;]+); fixed in (.+)\.", finding.message)
        if title and versions:
            return (f"{title.group(1)} ảnh hưởng đến {title.group(2)}",
                    f"Phiên bản đang sử dụng: {versions.group(1)}; phiên bản đã khắc phục: {versions.group(2)}.")
        return finding.title, finding.message

    @staticmethod
    def _verdict_label(verdict: str) -> str:
        return {"pass": "Pass", "fail": "Fail", "warning": "Warning",
                "skipped": "Skipped", "unknown": "Unknown"}.get(verdict, verdict)

    @classmethod
    def _public_findings(cls, run: QCRun):
        """Return unique primary findings suitable for public GitHub output."""
        findings = []
        seen = set()
        for result in run.results:
            if result.capability == "security.secrets" or cls._is_confirmation(result):
                continue
            for finding in result.findings:
                key = (
                    result.capability,
                    finding.category,
                    " ".join(finding.title.lower().split()),
                    finding.path or "",
                    finding.start_line,
                )
                if key in seen:
                    continue
                seen.add(key)
                findings.append((result, finding))
        return findings

    @classmethod
    def _review_body(cls, run: QCRun, check_url: str | None, marker: str) -> str:
        analysis = (run.agent_result_analysis.summary
                    if run.agent_result_analysis else cls._review_title(run))
        lines = [marker, "## Đánh giá của Agent-QC", "", analysis]
        public_findings = cls._public_findings(run)
        secret_count = sum(len(result.findings) for result in run.results
                           if result.capability == "security.secrets")
        if public_findings or secret_count:
            lines += ["", "### Các vấn đề cần xử lý", ""]
            for _, finding in public_findings[:5]:
                title, message = cls._finding_prose(finding)
                location = f" tại `{finding.path}`" if finding.path else ""
                lines.append(
                    f"- **{finding.severity.upper()} — {title}**{location}: "
                    f"{message}"
                )
            if secret_count:
                lines.append(
                    f"- **{secret_count} secret finding** cần được kiểm tra. Chi tiết nhạy cảm "
                    "được ẩn để tránh làm lộ credential."
                )
        passed = [cls._capability_label(result.capability) for result in run.results
                  if result.verdict == "pass" and not cls._is_confirmation(result)]
        if passed:
            lines += ["", "### Các hạng mục đã kiểm tra đạt", "",
                      ", ".join(passed) + "."]
        if run.agent_result_analysis and run.agent_result_analysis.root_causes:
            lines += ["", "### Remediation đề xuất", ""]
            for root_cause in run.agent_result_analysis.root_causes[:3]:
                lines.append(f"- {root_cause.remediation}")
        if check_url:
            lines += ["", f"[Xem báo cáo đầy đủ cùng bằng chứng và test metrics]({check_url})"]
        return "\n".join(lines)[:65535]

    @classmethod
    def _review_title(cls, run: QCRun) -> str:
        """Human review summary shown on the collapsed PR check row."""
        if run.error:
            return "Chưa thể hoàn tất đánh giá do lỗi test infrastructure"
        total = len(run.results)
        passed = sum(result.verdict == "pass" for result in run.results)
        errors = sum(result.execution_status != "completed" for result in run.results)
        secret_count = sum(len(result.findings) for result in run.results
                           if result.capability == "security.secrets")
        if secret_count:
            return ("Phát hiện secret finding cần xử lý trước khi merge. "
                    "Giá trị và vị trí nhạy cảm được ẩn khỏi summary công khai.")
        findings = [finding for _, finding in cls._public_findings(run)]
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
                        return (f"Rủi ro dependency: {title.group(2)} {versions.group(1)} bị ảnh hưởng "
                                f"bởi {title.group(1)}; cần nâng cấp lên {fixed}.")
                return cls._compact_prose(
                    f"{cls._capability_label(finding.category)} cần xử lý: "
                    f"{cls._finding_prose(finding)[0]}.", 120
                )
            return "Phát hiện vấn đề chất lượng cần xử lý trước khi merge."
        if run.verdict == "unknown" or errors:
            return ("Chưa đủ cơ sở kết luận vì một hoặc nhiều check không thể "
                    "hoàn tất ổn định; cần xem report chi tiết trước khi merge.")
        if run.verdict == "warning":
            return "Kết quả đánh giá có cảnh báo cần xem xét trước khi merge."
        if run.verdict == "pass":
            return ("Các hạng mục đã kiểm thử chưa ghi nhận "
                    "vấn đề ngăn cản việc merge thay đổi.")
        return "Đã hoàn tất đánh giá; không có check phù hợp với thay đổi này."

    @staticmethod
    def _capability_label(capability: str) -> str:
        return {
            "unit.run": "Unit tests",
            "functional.e2e": "Functional E2E",
            "functional.api": "API testing",
            "integration.service": "Service integration",
            "performance.smoke": "Performance smoke test",
            "performance.load": "Load testing",
            "security.sast": "Source security (SAST)",
            "security.sca": "Dependency security (SCA)",
            "security.secrets": "Secret scanning",
            "security.dast": "Dynamic security (DAST)",
            "contract.consumer_provider": "Contract testing",
            "performance.stress": "Stress testing",
            "performance.soak": "Soak testing",
            "test-failure": "Automated testing",
        }.get(capability, capability.replace(".", " / ").replace("_", " ").title())

    @staticmethod
    def _result_detail(result) -> str:
        parts = []
        for key, value in result.summary.items():
            if key in {"script", "test_path", "keploy_root"}:
                continue
            label = {"passed": "Passed", "failed": "Failed",
                     "skipped": "Skipped", "errors": "Errors",
                     "vulnerabilities": "Vulnerabilities"}.get(key, key.replace('_', ' '))
            parts.append(f"{label}: {value}")
        for key, value in result.metrics.items():
            parts.append(f"{key.replace('_', ' ')}: {value:g}")
        if result.error:
            parts.append(f"{result.error.code}: {result.error.message}")
        return "; ".join(parts)[:800] or "Đã hoàn tất; không có số liệu bổ sung"

    @classmethod
    def _summary(cls, run: QCRun) -> str:
        if run.error:
            return ("## Tổng quan đánh giá\n\nAgent-QC chưa thể hoàn tất đánh giá do "
                    "lỗi test infrastructure. Chưa có cơ sở xác định lỗi trong source code.\n\n"
                    f"**Lỗi hạ tầng:** `{run.error}`")
        if not run.results:
            return "## Tổng quan đánh giá\n\nKhông có test task phù hợp được chọn."

        icons = {"pass": "✅", "fail": "❌", "warning": "⚠️",
                 "skipped": "⏭️", "unknown": "❓"}
        passed = [result for result in run.results if result.verdict == "pass"]
        failed = [result for result in run.results if result.verdict == "fail"]
        incomplete = [result for result in run.results
                      if result.execution_status != "completed"]
        public_findings = cls._public_findings(run)
        secret_count = sum(len(result.findings) for result in run.results
                           if result.capability == "security.secrets")

        lines = ["## Tổng quan đánh giá", ""]
        if run.verdict == "pass":
            lines.append(
                f"Agent-QC đã thực hiện **{len(run.results)} check**, chưa ghi nhận vấn đề ngăn cản merge. "
                "Kết luận chỉ áp dụng trong phạm vi các hạng mục đã kiểm thử."
            )
        elif run.verdict == "fail":
            lines.append(
                f"Agent-QC ghi nhận **{len(run.results)} check**: **{len(passed)} đạt** và "
                f"**{len(failed)} không đạt**. Cần xử lý các vấn đề trước khi merge pull request."
            )
        elif run.verdict == "warning" and not incomplete:
            lines.append("Đã hoàn tất kiểm thử; có cảnh báo cần xem xét trước khi merge.")
        elif run.verdict == "skipped" and not incomplete:
            lines.append("Các tác vụ đã được bỏ qua; chưa có bằng chứng kiểm thử để đánh giá thay đổi.")
        else:
            lines.append(
                f"Agent-QC đã hoàn tất {len(run.results) - len(incomplete)} trên "
                f"{len(run.results)} tác vụ. **{len(incomplete)} tác vụ không hoàn tất "
                "ổn định**, nên chưa đủ cơ sở kết luận."
            )

        if public_findings or secret_count:
            lines += ["", "### Các vấn đề cần xử lý", ""]
            for _, finding in public_findings[:20]:
                title, message = cls._finding_prose(finding)
                location = f" tại `{finding.path}`" if finding.path else ""
                if finding.start_line:
                    location += f", dòng {finding.start_line}"
                lines.append(
                    f"- **{finding.severity.upper()} — {title}**{location}: "
                    f"{message}"
                )
            if secret_count:
                lines.append(
                    f"- **Phát hiện {secret_count} secret finding.** Giá trị và "
                    "đường dẫn nhạy cảm được ẩn khỏi report công khai."
                )

        if passed:
            labels = ", ".join(cls._capability_label(item.capability) for item in passed)
            lines += ["", "### Các hạng mục đạt yêu cầu", "", f"{labels}."]

        lines += ["", "### Test results", "",
                  "| Hạng mục | Worker | Kết quả | Bằng chứng và số liệu |",
                  "|---|---|---|---|"]
        for result in run.results:
            detail = cls._result_detail(result)
            if result.capability == "security.secrets":
                detail = f"{len(result.findings)} security finding; chi tiết được giữ riêng"
            lines.append(
                f"| {cls._capability_label(result.capability)} | "
                f"`{result.worker_id or 'unavailable'}` | "
                f"{icons.get(result.verdict, '')} **{cls._verdict_label(result.verdict)}** | {detail} |"
            )

        mode = {"active": "Có AI hỗ trợ", "fallback": "Fallback theo bộ quy tắc"}.get(run.agent_mode, run.agent_mode)
        lines += ["", "### Phân tích của agent", "", f"**Chế độ:** {mode}"]
        if run.agent_plan and run.agent_plan.summary:
            lines += ["", f"**Đánh giá rủi ro của thay đổi:** {run.agent_plan.summary}"]
        if run.agent_plan and run.agent_plan.risks:
            lines.extend(
                f"- **{risk.severity.upper()} / {risk.category}:** {risk.description}"
                for risk in run.agent_plan.risks[:10]
            )
        if run.agent_result_analysis:
            lines += ["", "**Nhận định tổng thể:** " + run.agent_result_analysis.summary]
            for root_cause in run.agent_result_analysis.root_causes[:10]:
                lines.append(
                    f"- **Root cause dự kiến (confidence {root_cause.confidence:.0%}):** "
                    f"{root_cause.root_cause} — **Remediation:** "
                    f"{root_cause.remediation}"
                )
            if run.agent_result_analysis.residual_risks:
                lines += ["", "**Residual risks:**"]
                lines.extend(f"- {risk}" for risk in
                             run.agent_result_analysis.residual_risks[:10])
        return "\n".join(lines)
