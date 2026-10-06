import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ....domain.models import Finding, ProjectDescriptor, WorkerResult, WorkerTask
from .base import CommandAdapter


LANGUAGES = ("python", "javascript", "java", "kotlin", "go", "rust", "dotnet", "generic")


def _json_payload(output: str) -> dict[str, Any]:
    """Extract the final JSON document when a CLI writes diagnostics first."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\s*\{", output):
        start = match.end() - 1
        try:
            payload, end = decoder.raw_decode(output[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and not output[start + end:].strip():
            return payload
    raise json.JSONDecodeError("No final JSON document found", output, 0)


def _severity(value: str | None) -> str:
    return {
        "CRITICAL": "critical", "HIGH": "high", "ERROR": "high",
        "MEDIUM": "medium", "WARNING": "medium", "LOW": "low", "INFO": "info",
        "UNKNOWN": "info",
    }.get((value or "").upper(), "medium")


def _finding(category: str, title: str, message: str, path: str | None,
             line: int | None, severity: str) -> Finding:
    fingerprint = hashlib.sha256(
        f"{category}:{title}:{path}:{line}".encode("utf-8")
    ).hexdigest()
    return Finding(
        id=f"finding_{fingerprint[:16]}", severity=_severity(severity), category=category,
        title=title[:300], message=message[:4000], path=path, start_line=line,
        fingerprint=fingerprint,
    )


class JsonSecurityAdapter(CommandAdapter):
    def parse_json(self, output: str, cwd: Path | None) -> tuple[list[Finding], dict[str, Any]]:
        raise NotImplementedError

    def parse_result(self, task: WorkerTask, exit_code: int, output: str,
                     cwd: Path | None = None) -> WorkerResult:
        try:
            findings, summary = self.parse_json(output, cwd)
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=self.worker_id, implementation=self.implementation, worker_kind="tool",
                execution_status="failed", verdict="unknown", exit_code=exit_code,
                output=output[-12000:], summary={"parse_error": str(exc)},
            )
        if exit_code > 1 and not findings:
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=self.worker_id, implementation=self.implementation, worker_kind="tool",
                execution_status="failed", verdict="unknown", exit_code=exit_code,
                output=output[-12000:], summary=summary,
            )
        return WorkerResult(
            task_id=task.task_id, run_id=task.run_id, capability=task.capability,
            worker_id=self.worker_id, implementation=self.implementation, worker_kind="tool",
            execution_status="completed", verdict="fail" if findings else "pass",
            exit_code=exit_code, output=output[-12000:], summary=summary,
            findings=findings[:200],
        )


class SemgrepAdapter(JsonSecurityAdapter):
    worker_id, implementation, capability = "semgrep-sast", "semgrep", "security.sast"
    languages, executable = LANGUAGES, "semgrep"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        # `auto` requires metrics and can remain blocked while resolving a
        # registry policy. Use a named, cacheable community ruleset and disable
        # telemetry; the runtime timeout remains the hard failure boundary.
        return ("semgrep", "scan", "--config", "p/default", "--metrics", "off",
                "--no-git-ignore", "--json", "--error", ".")

    def parse_json(self, output: str, cwd: Path | None) -> tuple[list[Finding], dict[str, Any]]:
        payload = _json_payload(output)
        findings = []
        for item in payload.get("results", []):
            extra = item.get("extra", {})
            findings.append(_finding(
                "security.sast", item.get("check_id", "Semgrep finding"),
                extra.get("message", "Static analysis finding"), item.get("path"),
                item.get("start", {}).get("line"), extra.get("severity", "WARNING"),
            ))
        return findings, {"findings": len(findings), "errors": len(payload.get("errors", []))}


class TrivyDependencyAdapter(JsonSecurityAdapter):
    worker_id, implementation, capability = "trivy-dependency", "trivy", "security.sca"
    languages, executable = LANGUAGES, "trivy"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("trivy", "fs", "--scanners", "vuln", "--format", "json",
                "--exit-code", "1", ".")

    def parse_json(self, output: str, cwd: Path | None) -> tuple[list[Finding], dict[str, Any]]:
        payload = _json_payload(output)
        findings = []
        for result in payload.get("Results", []):
            target = result.get("Target")
            for item in result.get("Vulnerabilities") or []:
                vulnerability = item.get("VulnerabilityID", "Dependency vulnerability")
                package = item.get("PkgName", "unknown package")
                installed = item.get("InstalledVersion", "unknown")
                fixed = item.get("FixedVersion") or "no fixed version"
                findings.append(_finding(
                    "security.sca", f"{vulnerability} in {package}",
                    f"Installed {installed}; fixed in {fixed}. {item.get('Title') or ''}".strip(),
                    target, None, item.get("Severity", "UNKNOWN"),
                ))
        return findings, {"vulnerabilities": len(findings)}


class GitleaksAdapter(JsonSecurityAdapter):
    worker_id, implementation, capability = "gitleaks-secrets", "gitleaks", "security.secrets"
    languages, executable = LANGUAGES, "gitleaks"

    def _report_name(self, task: WorkerTask) -> str:
        return f".agent-qc-gitleaks-{task.task_id}.json"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("gitleaks", "dir", ".", "--report-format", "json", "--report-path",
                self._report_name(task), "--redact=100", "--no-banner", "--exit-code", "1")

    def parse_json(self, output: str, cwd: Path | None) -> tuple[list[Finding], dict[str, Any]]:
        if cwd is None:
            raise ValueError("Gitleaks parser requires its working directory")
        reports = sorted(cwd.glob(".agent-qc-gitleaks-*.json"), key=lambda p: p.stat().st_mtime)
        if not reports:
            if "no leaks found" in output.lower():
                return [], {"secrets": 0}
            raise ValueError("Gitleaks report was not produced")
        report = reports[-1]
        try:
            payload = json.loads(report.read_text(encoding="utf-8") or "[]")
        finally:
            report.unlink(missing_ok=True)
        findings = [
            _finding(
                "security.secrets", item.get("RuleID", "Secret detected"),
                item.get("Description", "Potential secret detected (redacted)"),
                item.get("File"), item.get("StartLine"), "critical",
            )
            for item in payload
        ]
        return findings, {"secrets": len(findings)}
