from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    id: str
    target_types: tuple[str, ...]
    needs_running_target: bool = False
    default_timeout: int = 900


CATALOG = {
    item.id: item
    for item in [
        Capability("static.lint", ("source_tree",), default_timeout=300),
        Capability("static.type_check", ("source_tree",), default_timeout=600),
        Capability("functional.unit", ("source_tree",)),
        Capability("functional.integration", ("source_tree",), default_timeout=1800),
        Capability("functional.api", ("http_service",), True, 1800),
        Capability("functional.contract", ("http_service",), True, 1800),
        Capability("functional.ui", ("web_application",), True, 1800),
        Capability("functional.e2e", ("web_application",), True, 1800),
        Capability("security.sast", ("source_tree",), default_timeout=900),
        Capability("security.dependency", ("source_tree",), default_timeout=900),
        Capability("security.secret", ("source_tree",), default_timeout=300),
        Capability("security.container", ("container_image",), default_timeout=900),
        Capability("security.dast", ("http_service", "web_application"), True, 1800),
        Capability("performance.benchmark", ("http_service",), True, 900),
        Capability("performance.load", ("http_service",), True, 1800),
        Capability("experience.accessibility", ("web_application",), True, 900),
        Capability("experience.visual_regression", ("web_application",), True, 900),
        Capability("infrastructure.iac", ("source_tree",), default_timeout=600),
        Capability("data.schema", ("source_tree", "database"), default_timeout=900),
        Capability("ai.relevancy", ("ai_service",), True, 1800),
        Capability("ai.faithfulness", ("ai_service",), True, 1800),
        Capability("ai.safety", ("ai_service",), True, 1800),
        Capability("ai.regression", ("ai_service",), True, 1800),
    ]
}
