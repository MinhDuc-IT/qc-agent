"""Run from the project root after changing domain contracts."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tda.domain.contracts import (CriticReview, ExecutableCase, GeneratedBatch, KnowledgeModel,
                                 OracleBatch, RepairBatch, TestPlan, WorkflowConfig)


def main():
    root = Path(__file__).resolve().parents[1] / "schemas"
    root.mkdir(exist_ok=True)
    for model in (WorkflowConfig, KnowledgeModel, TestPlan, GeneratedBatch, OracleBatch,
                  ExecutableCase, RepairBatch, CriticReview):
        (root / f"{model.__name__}-2.0.json").write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
