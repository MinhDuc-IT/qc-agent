import json
from datetime import timedelta
from pathlib import Path

from ..domain.models import AgentResultAnalysis, Artifact, utc_now


class LocalArtifactStore:
    def __init__(self, root: Path, retention_days: int = 30):
        self.root = root
        self.retention_days = retention_days
        root.mkdir(parents=True, exist_ok=True)

    def save_self_heal_suggestion(self, run_id: str,
                                  analysis: AgentResultAnalysis) -> Artifact:
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "self-heal-suggestion.json"
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
        return Artifact(
            run_id=run_id, type="self_heal_suggestion", name=path.name,
            storage_ref=f"artifact://{run_id}/{path.name}",
            retention_until=utc_now() + timedelta(days=self.retention_days),
        )

    def prune(self) -> list[str]:
        removed: list[str] = []
        cutoff = utc_now().timestamp() - self.retention_days * 86400
        for path in self.root.glob("*/*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(str(path))
        for directory in self.root.iterdir():
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        return removed
