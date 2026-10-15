from pathlib import Path

from app.application.planning import CapabilityPlanner, PolicyValidator
from app.domain.capabilities import CATALOG
from app.domain.models import ProjectDescriptor, SourceAnalysis
from app.infrastructure.external_agents.registry import ExternalAgentRegistry


def test_baseline_quality_intent_pins_version_and_routes_external_worker():
    version = "a" * 20
    analysis = SourceAnalysis(projects=[ProjectDescriptor(id="backend", language="javascript")],
                              config={"quality": {"baseline": {"http": {
                                  "enabled": True, "parameters": {"baseline_version": version}}}}})
    plan = PolicyValidator().validate(CapabilityPlanner().create_plan("run", analysis), analysis)
    task = next(task for task in plan.tasks if task.capability == "baseline.http")
    assert task.parameters["baseline_version"] == version
    assert task.target.type == "http_service"
    assert CATALOG[task.capability].needs_running_target
    registry = ExternalAgentRegistry.from_yaml(Path("packages/test-design-agent/examples/external-agents.baseline.yaml"))
    worker = registry.resolve(task)
    assert worker.agent_id == "approved-baseline-worker"
    assert not worker.fallback_to_tools
