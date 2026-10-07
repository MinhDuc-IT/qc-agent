import json
from pathlib import Path

import pytest

from app.application.planning import PolicyValidator
from app.domain.models import (
    ExecutionPlan, ExternalAgentManifest, ProjectDescriptor, SourceAnalysis,
    TaskTarget, WorkerTask,
)
from app.infrastructure.execution.adapters.security import (
    GitleaksAdapter, SemgrepAdapter, TrivyDependencyAdapter,
)
from app.infrastructure.external_agents.registry import ExternalAgentRegistry
from workers.common import AgentRequest, SourceRef
from workers.integration_keploy.service import prepare as prepare_keploy
from workers.performance_k6.service import prepare as prepare_k6


def worker_task(capability="security.sast", target_type="repository", ref=None, **kwargs):
    return WorkerTask(
        run_id="run_test", capability=capability, objective="quality check",
        target=TaskTarget(type=target_type, project_id="project", ref=ref), **kwargs,
    )


def test_semgrep_json_becomes_source_finding():
    payload = {"results": [{"check_id": "python.sql-injection", "path": "app.py",
                            "start": {"line": 12},
                            "extra": {"message": "unsafe query", "severity": "ERROR"}}],
               "errors": []}
    result = SemgrepAdapter().parse_result(worker_task(), 1, json.dumps(payload))
    assert result.verdict == "fail"
    assert result.findings[0].path == "app.py"
    assert result.findings[0].start_line == 12


def test_semgrep_uses_bounded_named_ruleset_without_metrics():
    argv = SemgrepAdapter().build_argv(
        worker_task(), ProjectDescriptor(id="project", root=".", language="python")
    )
    ruleset = Path(argv[argv.index("--config") + 1])
    assert ruleset.name == "security.yaml"
    assert ruleset.is_file()
    assert argv[argv.index("--metrics") + 1] == "off"
    assert "--no-git-ignore" in argv


def test_security_parsers_accept_cli_diagnostics_before_json():
    semgrep = SemgrepAdapter().parse_result(
        worker_task(), 0, 'Scan completed\n{"results":[],"errors":[]}\n'
    )
    trivy = TrivyDependencyAdapter().parse_result(
        worker_task("security.sca"), 0, 'INFO scanning\n{"Results":[]}\n'
    )
    assert semgrep.verdict == "pass"
    assert trivy.verdict == "pass"


def test_trivy_json_becomes_dependency_finding():
    payload = {"Results": [{"Target": "requirements.txt", "Vulnerabilities": [{
        "VulnerabilityID": "CVE-1", "PkgName": "demo", "InstalledVersion": "1",
        "FixedVersion": "2", "Severity": "CRITICAL", "Title": "bad package",
    }]}]}
    result = TrivyDependencyAdapter().parse_result(
        worker_task("security.sca"), 1, json.dumps(payload)
    )
    assert result.findings[0].severity == "critical"
    assert result.summary["vulnerabilities"] == 1


def test_gitleaks_report_is_redacted_and_removed(tmp_path):
    report = tmp_path / ".agent-qc-gitleaks-task.json"
    report.write_text(json.dumps([{"RuleID": "api-key", "Description": "key found",
                                  "File": "config.py", "StartLine": 4}]), encoding="utf-8")
    result = GitleaksAdapter().parse_result(
        worker_task("security.secrets"), 1, "", tmp_path
    )
    assert result.verdict == "fail"
    assert result.findings[0].path == "config.py"
    assert not report.exists()


def test_dast_is_nightly_only():
    task = worker_task("security.dast", "http_service")
    analysis = SourceAnalysis(projects=[ProjectDescriptor(
        id="project", language="python", root=".")])
    pr = PolicyValidator().validate(ExecutionPlan(tasks=[task]), analysis, "pull_request")
    assert pr.tasks == []
    scheduled_task = worker_task("security.dast", "http_service")
    nightly = PolicyValidator().validate(
        ExecutionPlan(tasks=[scheduled_task]), analysis, "schedule"
    )
    assert nightly.tasks[0].parameters["trigger_type"] == "schedule"


def test_pentagi_manifest_rejected_until_vm_isolation():
    manifest = ExternalAgentManifest(
        agent_id="pentagi", name="PentAGI", version="1", capabilities=["security.dast"],
        supported_targets=["http_service"], endpoint="http://localhost:9999",
    )
    with pytest.raises(ValueError, match="isolated worker VM"):
        ExternalAgentRegistry([manifest])


def test_k6_reuses_repository_script_and_enforces_budget(tmp_path):
    script = tmp_path / "performance" / "k6.js"
    script.parent.mkdir()
    script.write_text("export default function() {}", encoding="utf-8")
    task = worker_task("performance.load", "http_service", "http://localhost:8000",
                       parameters={"vus": 10, "duration": "30s"})
    request = AgentRequest(
        task=task, project=ProjectDescriptor(id="project", root=".", language="python"),
        source=SourceRef(type="local_path", ref=str(tmp_path)),
    )
    prepared = prepare_k6(request, "run_worker")
    assert prepared.argv[-1] == str(script)
    assert prepared.metadata["duration_seconds"] == 30
    task.parameters["vus"] = 51
    with pytest.raises(ValueError, match="vus"):
        prepare_k6(request, "run_over_budget")


def test_keploy_uses_current_cli_without_removed_report_flag(tmp_path):
    (tmp_path / "keploy").mkdir()
    task = worker_task(
        "integration.service", "repository",
        parameters={"sut_command": "uvicorn app:app --port 8081"},
    )
    request = AgentRequest(
        task=task, project=ProjectDescriptor(id="project", root=".", language="python"),
        source=SourceRef(type="local_path", ref=str(tmp_path)),
    )
    prepared = prepare_keploy(request, "run_worker")
    assert "--generateTestReport" not in prepared.argv
    assert "--disable-ansi" in prepared.argv
    assert prepared.argv[prepared.argv.index("--path") + 1] == str(tmp_path)
