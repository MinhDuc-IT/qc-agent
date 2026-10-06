import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.models import (
    AggregateResult, ExecutionPlan, Finding, QCRun, TriggerContext,
    WorkerManifest, WorkerResult, WorkerTask,
)


MODELS = {
    "trigger-context-1.1": TriggerContext,
    "qc-run-1.1": QCRun,
    "worker-task-1.1": WorkerTask,
    "worker-result-1.1": WorkerResult,
    "finding-1.1": Finding,
    "aggregate-result-1.1": AggregateResult,
    "worker-manifest-1.1": WorkerManifest,
    "plan-1.1": ExecutionPlan,
}


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "schemas"
    destination.mkdir(exist_ok=True)
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://agent-qc.local/schemas/{name}.json"
        (destination / f"{name}.json").write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
