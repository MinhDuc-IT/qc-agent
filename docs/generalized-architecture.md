# Generalized Agent-QC architecture

The core model is `risk -> capability -> target -> worker -> result -> policy verdict`.

```text
GitHub Adapter
  -> RepositoryAnalyzer
  -> CapabilityPlanner
  -> PolicyValidator
  -> ExecutionPlan (DAG)
  -> WorkerRegistry
  -> WorkerExecutor
  -> WorkerResult[]
  -> VerdictEngine
  -> GitHub Checks Publisher
```

## Boundaries

- `analyzer.py`: deterministic language/project/config/diff discovery.
- `capabilities.py`: tool-independent quality capability catalog.
- `planner.py`: repository intent and changes into versioned tasks.
- `workers.py`: trusted implementation registry, argv execution and normalization.
- `verdict.py`: tool-independent run aggregation.
- `orchestrator.py`: lifecycle and DAG coordination only.
- `github.py`: provider authentication and result publishing only.

The planner never emits a shell command. A worker adapter maps a validated task to an argument list and the executor uses `shell=False`.

## Extension procedure

1. Register a tool-independent capability and accepted target type.
2. Add or reuse a worker implementation in the registry.
3. Implement structured configuration-to-argv mapping and result parsing.
4. Add policy and contract tests.
5. Package the required tool in an isolated runtime image.

Repository-specific workers are not required. Implementations are reusable across repositories with the same language, target, or protocol.
