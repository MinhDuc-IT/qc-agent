import json
from pathlib import Path

from app.application.planning import CapabilityPlanner
from app.application.triage import TriageService
from app.domain.models import FindingTriage


ROOT = Path(__file__).parent / "eval"


def test_planner_required_capability_recall_is_100_percent():
    expected_total = recalled = 0
    for path in (ROOT / "planner").glob("*.json"):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        actual = CapabilityPlanner._required_capabilities(fixture["changed_files"])
        expected = set(fixture["expected_required_capabilities"])
        expected_total += len(expected)
        recalled += len(expected & actual)
    assert expected_total > 0
    assert recalled / expected_total == 1.0


def test_triage_eval_has_zero_policy_false_negatives():
    service = TriageService(store=None, min_confidence=0.9)
    for path in (ROOT / "triage").glob("*.json"):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        checked = service._post_validate(FindingTriage.model_validate(fixture["proposal"]))
        assert checked.proposed_action == fixture["expected_action"], path.name
