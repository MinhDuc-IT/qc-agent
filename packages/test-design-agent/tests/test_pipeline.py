import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from tda.models import ProjectInput
from tda.pipeline import approve, build, dump, execute, qc_plan, verify
from tda.runtime import check_case


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/requirements.yaml"


@pytest.fixture
def service():
    class Handler(BaseHTTPRequestHandler):
        broken = False

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query)
            result = int(query["a"][0]) + int(query["b"][0]) + int(self.broken)
            body = json.dumps({"result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_end_to_end_detects_product_regression(tmp_path, service):
    url, handler = service
    baseline = build(EXAMPLE, tmp_path / "baselines")
    original = (baseline / "cases/cases.json").read_bytes()
    report = execute(baseline, url)
    assert report["passed"]
    path = tmp_path / "passing-report.json"
    dump(path, report)
    approve(baseline, "reviewer", path)
    assert verify(baseline)["state"] == "approved"
    plan = qc_plan(baseline, "backend", "run-1", "baseline")
    assert plan["tasks"][0]["scope"]["paths"] == ["baseline/tests/test_baseline.py"]
    handler.broken = True
    failure = execute(baseline, url)
    assert not failure["passed"]
    assert all(r["status"] == "defect" for r in failure["results"])
    assert (baseline / "cases/cases.json").read_bytes() == original
    dump(path, failure)
    with pytest.raises(ValueError, match="passing report"):
        approve(baseline, "reviewer", path)


def test_generated_pytest_runs_without_generator_dependency(tmp_path, service):
    baseline = build(EXAMPLE, tmp_path / "baselines")
    environment = dict(os.environ, TDA_BASE_URL=service[0])
    result = subprocess.run([sys.executable, "-m", "pytest", str(baseline / "tests/test_baseline.py"), "-q"],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_tampered_baseline_and_draft_export_rejected(tmp_path):
    baseline = build(EXAMPLE, tmp_path / "baselines")
    with pytest.raises(ValueError, match="approved"):
        qc_plan(baseline, "backend", "run", "baseline")
    (baseline / "cases/cases.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        verify(baseline)


def test_uncovered_requirements_block_approval(tmp_path, service):
    data = yaml.safe_load(EXAMPLE.read_text())
    data["requirements"].append({"id": "REQ-UNKNOWN", "title": "Unknown behavior", "source_ref": "PRD#unknown"})
    source = tmp_path / "input.yaml"
    source.write_text(yaml.safe_dump(data))
    baseline = build(source, tmp_path / "baselines")
    coverage = json.loads((baseline / "validation_report.json").read_text())
    assert coverage["requirement_coverage"] == 0.5
    path = tmp_path / "report.json"
    dump(path, execute(baseline, service[0]))
    with pytest.raises(ValueError, match="uncovered"):
        approve(baseline, "reviewer", path)


def test_report_must_cover_all_cases(tmp_path, service):
    baseline = build(EXAMPLE, tmp_path / "baselines")
    report = execute(baseline, service[0])
    report["results"].pop()
    path = tmp_path / "report.json"
    dump(path, report)
    with pytest.raises(ValueError, match="every baseline case"):
        approve(baseline, "reviewer", path)


def test_mutations_require_explicit_opt_in(tmp_path):
    baseline = build(EXAMPLE, tmp_path / "baselines")
    case = json.loads((baseline / "cases/cases.json").read_text())[0]
    case["method"] = "DELETE"
    assert check_case(case, "http://127.0.0.1:1")["status"] == "blocked"


def test_duplicate_ids_and_missing_oracle_rejected():
    data = yaml.safe_load(EXAMPLE.read_text())
    data["requirements"][0]["scenarios"][1]["id"] = "ADD-POSITIVE"
    with pytest.raises(ValueError, match="unique"):
        ProjectInput.model_validate(data)
    del data["requirements"][0]["scenarios"][0]["oracle"]
    with pytest.raises(ValueError):
        ProjectInput.model_validate(data)


def test_qc_plan_matches_existing_contract(tmp_path, service):
    qc_root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("qc_models", qc_root / "app/domain/models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = build(EXAMPLE, tmp_path / "baselines")
    path = tmp_path / "report.json"
    dump(path, execute(baseline, service[0]))
    approve(baseline, "reviewer", path)
    payload = qc_plan(baseline, "backend", "run", "baseline")
    plan = module.ExecutionPlan.model_validate(payload)
    assert plan.tasks[0].implementation == "pytest"
