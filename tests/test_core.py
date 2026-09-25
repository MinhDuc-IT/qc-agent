import hashlib
import hmac
import subprocess

from app.application.analysis import RepositoryAnalyzer
from app.application.planning import CapabilityPlanner, PolicyValidator
from app.domain.models import normalize_pull_request
from app.domain.verdict import VerdictEngine
from app.infrastructure.execution.workers import WorkerExecutor, WorkerRegistry
from app.infrastructure.github.webhook import verify_webhook_signature


def payload():
    return {
        "action": "opened", "installation": {"id": 7},
        "sender": {"id": 1, "login": "demo"},
        "repository": {"name": "sample-app", "full_name": "demo/sample-app",
                       "clone_url": "https://github.com/demo/sample-app.git",
                       "default_branch": "main", "owner": {"login": "demo"}},
        "pull_request": {"number": 3, "html_url": "https://github.com/demo/sample-app/pull/3",
                         "head": {"sha": "abc", "ref": "feature"},
                         "base": {"sha": "def", "ref": "main"}},
    }


def test_signature():
    body, secret = b"hello", "secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature, secret)
    assert not verify_webhook_signature(body + b"!", signature, secret)


def test_normalize_pull_request():
    context = normalize_pull_request(payload(), "delivery-1")
    assert context.repository.full_name == "demo/sample-app"
    assert context.revision.head_sha == "abc"
    assert context.installation.id == 7


def test_generalized_pipeline_for_python_project(tmp_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (tmp_path / "test_calculator.py").write_text(
        "from calculator import add\ndef test_add(): assert add(2, 3) == 5\n", encoding="utf-8"
    )
    (tmp_path / ".agent-qc.yaml").write_text(
        'version: "1"\nquality:\n  functional:\n    unit:\n      enabled: true\n', encoding="utf-8"
    )
    analysis = RepositoryAnalyzer().analyze(tmp_path, "missing", "missing")
    assert analysis.projects[0].language == "python"
    plan = PolicyValidator().validate(CapabilityPlanner().create_plan("run_test", analysis), analysis)
    assert [task.capability for task in plan.tasks] == ["functional.unit"]
    task = plan.tasks[0]
    implementation = WorkerRegistry().resolve(task, analysis.projects[0])
    result = WorkerExecutor().execute(task, analysis.projects[0], tmp_path, implementation)
    aggregate = VerdictEngine().evaluate([result])
    assert result.worker_id == "python-pytest"
    assert result.verdict == "pass"
    assert aggregate.verdict == "pass"


def test_planner_models_unsupported_capability_as_skipped(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / ".agent-qc.yaml").write_text(
        'quality:\n  performance:\n    load:\n      enabled: true\n', encoding="utf-8"
    )
    analysis = RepositoryAnalyzer().analyze(tmp_path, "a", "b")
    plan = CapabilityPlanner().create_plan("run_test", analysis)
    assert plan.tasks[0].capability == "performance.load"
    task = plan.tasks[0]
    implementation = WorkerRegistry().resolve(task, analysis.projects[0])
    result = WorkerExecutor().execute(task, analysis.projects[0], tmp_path, implementation)
    assert result.verdict == "skipped"
