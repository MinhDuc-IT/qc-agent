import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infrastructure.artifacts import LocalArtifactStore
from app.infrastructure.settings import get_settings


def main() -> None:
    settings = get_settings()
    store = LocalArtifactStore(settings.agent_qc_artifact_dir,
                               settings.agent_qc_artifact_retention_days)
    removed = store.prune()
    print(f"removed={len(removed)}")


if __name__ == "__main__":
    main()
