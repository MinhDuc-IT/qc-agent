from pathlib import Path

from app.domain.models import TaskTarget, WorkerTask
from workers.functional_hercules.service import (
    HerculesSettings, build_command, build_feature, parse_junit,
)


def task(**overrides):
    values = {
        "run_id": "run_test",
        "capability": "functional.e2e",
        "objective": "user can sign in",
        "target": TaskTarget(type="web_app", project_id="web", ref="http://localhost:3000"),
        "scope": {"scenarios": ["sign in with a valid account"]},
    }
    values.update(overrides)
    return WorkerTask(**values)


def test_feature_generation_uses_target_and_scenario():
    feature = build_feature(task())
    assert 'Given I open the page "http://localhost:3000"' in feature
    assert "sign in with a valid account" in feature


def test_explicit_feature_is_preserved():
    feature = "Feature: API\n  Scenario: health\n    When I call the health API"
    assert build_feature(task(parameters={"feature": feature})) == feature + "\n"


def test_command_is_argument_safe(tmp_path):
    settings = HerculesSettings()
    settings.executable = "testzeus-hercules"
    assert build_command(settings, tmp_path) == [
        "testzeus-hercules", "--project-base", str(tmp_path),
    ]


def test_parse_junit_maps_failures_to_findings(tmp_path: Path):
    (tmp_path / "result.xml").write_text(
        '<testsuite tests="2" failures="1" errors="0" skipped="0">'
        '<testcase name="login"/><testcase name="checkout">'
        '<failure message="total is wrong">trace</failure></testcase></testsuite>',
        encoding="utf-8",
    )
    totals, findings = parse_junit(tmp_path)
    assert totals == {"tests": 2, "failures": 1, "errors": 0, "skipped": 0}
    assert findings[0].title == "checkout"
    assert findings[0].category == "functional"
