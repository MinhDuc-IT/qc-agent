from pathlib import Path

import yaml

from ...domain.capabilities import CATALOG
from ...domain.models import ExternalAgentManifest, WorkerTask


class ExternalAgentRegistry:
    def __init__(self, manifests: list[ExternalAgentManifest] | None = None):
        self.manifests = manifests or []
        self._validate()

    @classmethod
    def from_yaml(cls, path: Path | None) -> "ExternalAgentRegistry":
        if path is None or not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = raw.get("agents", []) if isinstance(raw, dict) else []
        return cls([ExternalAgentManifest.model_validate(item) for item in items])

    def resolve(self, task: WorkerTask) -> ExternalAgentManifest | None:
        candidates = [
            manifest for manifest in self.manifests
            if task.capability in manifest.capabilities
            and task.target.type in manifest.supported_targets
        ]
        return candidates[0] if candidates else None

    def _validate(self) -> None:
        ids: set[str] = set()
        for manifest in self.manifests:
            if "pentagi" in f"{manifest.agent_id} {manifest.name}".lower():
                raise ValueError(
                    "PentAGI is disabled until an isolated worker VM policy is implemented"
                )
            if manifest.agent_id in ids:
                raise ValueError(f"Duplicate external agent id: {manifest.agent_id}")
            ids.add(manifest.agent_id)
            unknown = set(manifest.capabilities) - set(CATALOG)
            if unknown:
                raise ValueError(f"Agent {manifest.agent_id} declares unknown capabilities: {sorted(unknown)}")
