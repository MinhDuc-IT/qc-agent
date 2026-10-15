import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import yaml

from .pipeline import approve, build, dump, execute, qc_plan, verify


def main():
    parser = argparse.ArgumentParser(description="Evidence-based Test Design Agent")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("build")
    create.add_argument("source", type=Path)
    create.add_argument("--output", type=Path, default=Path("baselines"))
    design = commands.add_parser("design", help="Run layered LLM test-design workflow")
    design.add_argument("config", type=Path)
    design.add_argument("--output", type=Path, default=Path("baselines"))
    design.add_argument("--runs", type=Path, default=Path("runs"))
    design.add_argument("--base-url")
    design.add_argument("--allow-mutations", action="store_true")
    design.add_argument("--env-file", type=Path)
    design.add_argument("--model")
    validate = commands.add_parser("validate")
    validate.add_argument("baseline", type=Path)
    run = commands.add_parser("run")
    run.add_argument("baseline", type=Path)
    run.add_argument("--base-url", required=True)
    run.add_argument("--allow-mutations", action="store_true")
    run.add_argument("--report", type=Path, required=True)
    accept = commands.add_parser("approve")
    accept.add_argument("baseline", type=Path)
    accept.add_argument("--reviewer", required=True)
    accept.add_argument("--report", type=Path, help="Passing execution report required for legacy v1 baselines only")
    export = commands.add_parser("export-qc")
    export.add_argument("baseline", type=Path)
    export.add_argument("--project-id", required=True)
    export.add_argument("--run-id", required=True)
    export.add_argument("--relative-root", default="baseline")
    export.add_argument("--external-agent", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "build":
            print(build(args.source, args.output))
        elif args.command == "design":
            from .application.orchestrator import TestDesignOrchestrator
            from .domain.contracts import WorkflowConfig
            from .infrastructure.artifacts import LocalArtifactStore
            from .infrastructure.baselines import BaselineRepository
            from .infrastructure.openai_agent import OpenAIStructuredAgent
            from .infrastructure.sources import LocalSourceReader
            from .runner import HttpSuiteExecutor
            if args.env_file:
                from dotenv import load_dotenv
                if not args.env_file.is_file():
                    raise ValueError("env file does not exist")
                load_dotenv(args.env_file, override=False)
            data = yaml.safe_load(args.config.read_text(encoding="utf-8"))
            if args.model:
                data["model"] = args.model
            config = WorkflowConfig.model_validate(data)
            if not os.getenv("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY required for LLM design; no implicit fallback")
            store = LocalArtifactStore(args.runs / uuid4().hex, progress=True)
            print(f"Run artifacts: {store.root}", flush=True)
            agent = OpenAIStructuredAgent(os.environ["OPENAI_API_KEY"], config.model, store,
                                         config.max_calls, config.max_output_tokens,
                                         reasoning_effort=config.reasoning_effort)
            workflow = TestDesignOrchestrator(agent, LocalSourceReader(), HttpSuiteExecutor(),
                                              store, BaselineRepository(args.output))
            result = workflow.run(config, args.config.resolve().parent, args.base_url, args.allow_mutations)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["state"] in {"awaiting_execution", "ready_for_human_review"} else 1
        elif args.command == "validate":
            print(json.dumps(verify(args.baseline), indent=2))
        elif args.command == "run":
            if args.report.resolve().is_relative_to(args.baseline.resolve()):
                raise ValueError("execution reports must be outside immutable baseline")
            report = execute(args.baseline, args.base_url, args.allow_mutations)
            dump(args.report, report)
            print(json.dumps(report, indent=2))
            return 0 if report["passed"] else 1
        elif args.command == "approve":
            if not args.reviewer.strip():
                raise ValueError("reviewer must be non-empty")
            approve(args.baseline, args.reviewer, args.report)
            print("approved")
        else:
            print(json.dumps(qc_plan(args.baseline, args.project_id, args.run_id, args.relative_root,
                                     external=args.external_agent), indent=2))
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        parser.exit(2, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
