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
        Capability("unit.run", ("repository",)),
        Capability("functional.api", ("openapi", "http_service"), True, 1800),
        Capability("functional.e2e", ("web_app", "mobile_app"), True, 1800),
        Capability("functional.exploratory", ("web_app",), True, 1800),
        # Keploy owns the SUT process so it can instrument dependency traffic.
        Capability("integration.service", ("repository", "http_service", "service_graph"), False, 1800),
        Capability("contract.consumer_provider", ("http_service",), default_timeout=1800),
        Capability("performance.smoke", ("http_service",), True, 900),
        Capability("performance.load", ("http_service",), True, 1800),
        Capability("performance.stress", ("http_service",), True, 1800),
        Capability("performance.soak", ("http_service",), True, 3600),
        Capability("security.sca", ("repository",), default_timeout=900),
        Capability("security.secrets", ("repository",), default_timeout=300),
        Capability("security.sast", ("repository",), default_timeout=120),
        Capability("security.dast", ("http_service", "web_app"), True, 1800),
        Capability("ai.evaluation", ("ai_service",), True, 1800),
        Capability("ai.redteam", ("ai_service",), True, 1800),
        Capability("accessibility.audit", ("web_app",), True, 900),
        Capability("visual.regression", ("web_app",), True, 900),
        Capability("resilience.chaos", ("service_graph",), True, 1800),
        Capability("data.quality", ("database", "data_pipeline"), default_timeout=900),
        Capability("data.migration", ("database", "repository"), default_timeout=1800),
    ]
}
