import ast
import hashlib
import json
from pathlib import Path

import yaml

from .models import ProjectInput
from .runtime import check_case
from .runner import DEFAULT_AUTH


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source: Path, destination: Path):
    project = ProjectInput.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
    payload = project.model_dump()
    generator_sources = b"".join(Path(__file__).with_name(name).read_bytes()
                                 for name in ("pipeline.py", "runtime.py", "models.py"))
    version = hashlib.sha256(json.dumps(payload, sort_keys=True).encode() + generator_sources).hexdigest()[:16]
    baseline = destination / version
    baseline.mkdir(parents=True, exist_ok=False)
    cases = []
    trace = {}
    for requirement in project.requirements:
        trace[requirement.id] = []
        for scenario in requirement.scenarios:
            case = scenario.model_dump()
            case["requirement_id"] = requirement.id
            cases.append(case)
            trace[requirement.id].append(case["id"])
    dump(baseline / "cases/cases.json", cases)
    dump(baseline / "knowledge.json", payload)
    dump(baseline / "traceability.json", trace)
    dump(baseline / "test_plan.json", {
        "generator": "evidence-rules-v1",
        "scenarios": [{"id": c["id"], "requirement_id": c["requirement_id"],
                       "priority": c["priority"], "risks": c["risks"],
                       "type": "functional.api", "oracle_source": c["oracle"]["source_ref"]}
                      for c in cases],
        "open_questions": [{"requirement_id": key, "question": "Provide scenarios and acceptance criteria"}
                           for key, ids in trace.items() if not ids],
    })
    runtime = Path(__file__).with_name("runtime.py").read_text(encoding="utf-8")
    (baseline / "tests").mkdir()
    (baseline / "tests/tda_runtime.py").write_text(runtime, encoding="utf-8")
    runner = '''import importlib.util
import json
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("baseline_runtime", ROOT / "tests/tda_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
CASES = json.loads((ROOT / "cases/cases.json").read_text(encoding="utf-8"))

@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_baseline(case):
    base_url = os.environ.get("TDA_BASE_URL")
    assert base_url, "TDA_BASE_URL is required"
    result = runtime.check_case(case, base_url, os.getenv("TDA_ALLOW_MUTATIONS") == "1")
    assert result["status"] == "pass", result
'''
    ast.parse(runner)
    (baseline / "tests/test_baseline.py").write_text(runner, encoding="utf-8")
    covered = sum(bool(ids) for ids in trace.values())
    report = {
        "schema_valid": True, "syntax_valid": True,
        "requirements_total": len(trace), "requirements_covered": covered,
        "requirement_coverage": covered / len(trace),
        "uncovered_requirements": [key for key, ids in trace.items() if not ids],
        "test_count": len(cases), "execution_verified": False,
        "limitations": ["Coverage measures designed scenarios, not executed assertions or code coverage.",
                        "Only explicit HTTP scenarios are supported; no inferred business oracle."],
    }
    dump(baseline / "validation_report.json", report)
    files = {str(p.relative_to(baseline)).replace("\\", "/"): digest(p)
             for p in baseline.rglob("*") if p.is_file()}
    manifest = {"schema_version": "1.0", "baseline_version": version,
                "project": project.project, "source_revision": project.source_revision,
                "state": "draft", "generator": "evidence-rules-v1", "files": files,
                "capability": "unit.run", "semantic_type": "functional.api",
                "entrypoint": "tests/test_baseline.py", "requirements": ["python", "pytest"],
                "environment": ["TDA_BASE_URL"], "cases": [c["id"] for c in cases]}
    (baseline / "test_manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return baseline


def verify(baseline):
    manifest = yaml.safe_load((baseline / "test_manifest.yaml").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") not in {"1.0", "2.0"}:
        raise ValueError("unsupported baseline manifest")
    mandatory = {"cases/cases.json", "knowledge.json", "traceability.json", "validation_report.json",
                 "tests/test_baseline.py"}
    if manifest["schema_version"] == "2.0":
        mandatory.update({"sources.json", "test_plan.json", "coverage.json", "critic_review.json",
                          "review_gate.json", "tests/tda_runner.py", "ground_truth/oracles.json"})
    if not mandatory.issubset(manifest.get("files", {})):
        raise ValueError("baseline manifest omits required payload files")
    if manifest.get('approval_policy')=='design-quality-v1' and 'reference_execution.json' not in manifest['files']:
        raise ValueError('baseline omits immutable reference execution snapshot')
    for name, expected in manifest["files"].items():
        path = (baseline / name).resolve()
        if not path.is_relative_to(baseline.resolve()) or not path.is_file() or digest(path) != expected:
            raise ValueError(f"baseline integrity failed: {name}")
    cases = json.loads((baseline / "cases/cases.json").read_text(encoding="utf-8"))
    if any(case.get('runner','http')!='http' for case in cases) and not {'tests/tda_suite.py','tests/tda_native_runner.py'}.issubset(manifest['files']):
        raise ValueError('native baseline omits required runner files')
    if [case["id"] for case in cases] != manifest["cases"]:
        raise ValueError("manifest case IDs differ from payload")
    if manifest.get('approval_policy')=='workspace-reviewed-subset-v1':
        from .domain.workspace import TestWorkspace
        from .application.workspace import refresh_workspace,design_digest,coverage_for,binding_design_issues
        if not {'workspace_publication.json','test_workspace.json'}.issubset(manifest['files']):raise ValueError('Missing workspace publication snapshot')
        workspace=TestWorkspace.model_validate(json.loads((baseline/'test_workspace.json').read_text(encoding='utf-8')))
        publication=json.loads((baseline/'workspace_publication.json').read_text(encoding='utf-8'))
        refresh_workspace(workspace)
        selected={case.id:case for case in workspace.cases}
        if set(publication['selected_case_ids'])!=set(selected) or any(case.status!='approved' for case in selected.values()):raise ValueError('Publication contains unapproved design cases')
        if set(publication['executed_case_ids'])!=set(manifest['cases']):raise ValueError('Publication automation inventory differs from manifest')
        for data in cases:
            case=selected.get(data['id']);binding=workspace.bindings.get(data['id'])
            if not case or not binding or binding.state!='validated' or not binding.reviewer or binding.case_revision!=case.revision or binding.design_sha256!=design_digest(case) or binding.executable.model_dump()!=data:
                raise ValueError('Publication binding differs from approved case revision')
            if binding_design_issues(case,binding.executable):raise ValueError('Published oracle differs from test design')
        actual=coverage_for(workspace,legacy='regression_inventory' not in publication['coverage'])
        if actual!=publication['coverage']:raise ValueError('Publication coverage differs from snapshot')
        if (actual['missing_criteria'] or actual['missing_techniques']) and (not publication['acknowledged_gaps'] or not publication['reason'].strip()):raise ValueError('Publication gaps were not acknowledged')
    if manifest["schema_version"] == "2.0":
        from .domain.contracts import ExecutableCase, SourceBundle
        from .domain.rules import TestValidator
        parsed = [ExecutableCase.model_validate(case) for case in cases]
        sources = SourceBundle.model_validate(json.loads((baseline / "sources.json").read_text(encoding="utf-8")))
        actual = TestValidator().validate(parsed, sources)
        recorded = json.loads((baseline / "validation_report.json").read_text(encoding="utf-8"))
        if actual.model_dump() != recorded:
            raise ValueError("recorded validation report differs from deterministic validation")
    return manifest


def execute(baseline, base_url, allow_mutations=False, cancellation=None, auth_token=DEFAULT_AUTH, workspace=None,
            judge=None, k6_binary=None, browser_channel='chrome', unit_images=None):
    manifest = verify(baseline)
    cases = json.loads((baseline / "cases/cases.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] == "2.0":
        from .runner import HttpSuiteExecutor
        validation = json.loads((baseline / "validation_report.json").read_text(encoding="utf-8"))
        if validation["issues"]:
            raise ValueError("execution blocked by invalid cases; regenerate after resolving validation issues")
        if any(case.get('runner','http')!='http' for case in cases):
            from .suite import SuiteExecutor
            report=SuiteExecutor(workspace,cancellation,auth_token,judge,k6_binary,browser_channel,unit_images).execute(cases,base_url,allow_mutations)
        else:report = HttpSuiteExecutor(cancellation, auth_token).execute(cases, base_url, allow_mutations)
        report["baseline_version"] = manifest["baseline_version"]
        return report
    results = [check_case(case, base_url, allow_mutations) for case in cases]
    report = {"baseline_version": manifest["baseline_version"], "results": results,
              "passed": bool(results) and all(r["status"] == "pass" for r in results)}
    return report


def approve(baseline, reviewer, report_path=None):
    manifest = verify(baseline)
    expected = manifest["cases"]
    if not expected or not reviewer.strip():
        raise ValueError('approval requires non-empty test cases and reviewer')
    coverage = json.loads((baseline / "validation_report.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] == "2.0":
        gate = json.loads((baseline / "review_gate.json").read_text(encoding="utf-8"))
        metrics = json.loads((baseline / "coverage.json").read_text(encoding="utf-8"))
        critic = json.loads((baseline / "critic_review.json").read_text(encoding="utf-8"))
        if gate["design_blockers"] or gate.get('blocking_questions') or coverage["issues"] or (not metrics["design_satisfied"] and manifest.get('approval_policy')!='workspace-reviewed-subset-v1') or \
           sorted(coverage['valid_case_ids']) != sorted(expected) or \
           critic["recommendation"] != "ready_for_human_review" or \
           any(issue["severity"] == "blocker" for issue in critic["issues"]):
            raise ValueError("approval blocked by design, oracle, validation, coverage or critic issues")
        if manifest.get('approval_policy')!='workspace-reviewed-subset-v1':manifest.update(approval_policy='design-quality-v1',approval_basis='validation,coverage,critic,human_review')
        else:manifest['approval_basis']='approved_cases,validated_bindings,explicit_scope,human_review'
        # Older v2 baselines lack a pinned reference snapshot. They can be approved
        # for design, but QC must not trust an unhashed external report for comparison.
        if 'reference_execution.json' not in manifest['files']:
            manifest['approval_policy']='design-quality-legacy-v2'
    else:
        report=json.loads(report_path.read_text(encoding='utf-8')) if report_path else {}
        results=report.get('results',[])
        if report.get('baseline_version')!=manifest['baseline_version'] or sorted(r.get('case_id','') for r in results)!=sorted(expected) or any(r.get('status')!='pass' for r in results):
            raise ValueError('approval requires a passing report for every baseline case')
        if coverage['uncovered_requirements']:raise ValueError('approval blocked by uncovered requirements')
        manifest['approval_report_sha256']=digest(report_path)
    manifest.update(state="approved", reviewer=reviewer)
    (baseline / "test_manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def qc_plan(baseline, project_id, run_id, relative_root="baseline", external=False):
    manifest = verify(baseline)
    if manifest["state"] != "approved":
        raise ValueError("QC export requires an approved baseline")
    if external:
        if manifest["schema_version"] != "2.0":
            raise ValueError("HTTP baseline worker requires a v2 artifact")
        return {"schema_version": "1.1", "run_id": run_id, "profile": "regression",
                "planner_version": "test-design-v2", "tasks": [{
                    "schema_version": "1.1", "task_id": "baseline_" + manifest["baseline_version"],
                    "run_id": run_id, "capability": "baseline.http", "objective": "Execute approved HTTP baseline",
                    "target": {"type": "http_service", "project_id": project_id},
                    "execution_preference": "external_agent",
                    "parameters": {"baseline_version": manifest["baseline_version"]}, "timeout_seconds": 180}]}
    root = Path(relative_root)
    if root.is_absolute() or ".." in root.parts or ":" in relative_root:
        raise ValueError("baseline root must be relative to QC project checkout")
    return {"schema_version": "1.1", "run_id": run_id, "profile": "regression",
            "planner_version": "test-design-v1", "tasks": [{
                "schema_version": "1.1", "task_id": "baseline_" + manifest["baseline_version"],
                "run_id": run_id, "capability": "unit.run", "objective": "Execute approved API baseline",
                "target": {"type": "repository", "project_id": project_id},
                "implementation": "pytest", "execution_preference": "tool",
                "scope": {"paths": [(root / manifest["entrypoint"]).as_posix()]},
                "parameters": {"baseline_version": manifest["baseline_version"]},
                "timeout_seconds": 900}]}
