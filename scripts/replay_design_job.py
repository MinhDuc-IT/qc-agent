"""Replay saved proposals offline; never calls GitHub, an LLM, or the target app.

The saved execution report is reused, so this diagnoses design processing only.
Missing correction responses reuse the rejected response to exercise fail-closed handling.
Outputs are isolated from the managed baseline registry.
"""
import argparse
import json
from pathlib import Path

from tda.application.orchestrator import TestDesignOrchestrator
from tda.domain.contracts import SourceBundle, WorkflowConfig
from tda.infrastructure.artifacts import LocalArtifactStore
from tda.infrastructure.baselines import BaselineRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    def read(name):
        return json.loads((args.attempt / name).read_text(encoding="utf-8"))

    class Reader:
        def collect(self, config, directory):
            return SourceBundle.model_validate(read("sources.json"))

    class Agent:
        def propose(self, stage, instructions, context, output):
            stage = stage.removesuffix(".correction")
            files = list((args.attempt / "llm_outputs").glob(f"*-{stage}.json"))
            if len(files) != 1:
                raise ValueError(f"Expected one saved proposal for {stage}")
            return output.model_validate(json.loads(files[0].read_text(encoding="utf-8")))

    class Executor:
        def execute(self, cases, base_url, allow_mutations):
            report = read("rounds/0/execution.json")
            assert {case["id"] for case in cases} == {r["case_id"] for r in report["results"]}
            return report

    config = WorkflowConfig.model_validate(read("config.json"))
    result = TestDesignOrchestrator(Agent(), Reader(), Executor(),
        LocalArtifactStore(args.output / "run"), BaselineRepository(args.output / "baselines"))
    outcome = result.run(config, args.output, base_url="http://offline-replay.invalid")
    print(json.dumps({"state": outcome["state"], "case_count": outcome["case_count"],
                      "design_blockers": outcome["design_blockers"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
