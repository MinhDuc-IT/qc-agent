# Generalized Agent-QC architecture

The core model is `risk -> capability -> target -> worker -> result -> policy verdict`.

```text
app/
├── domain/                 # pure contracts, capabilities and verdict rules
│   ├── models.py
│   ├── capabilities.py
│   └── verdict.py
├── application/            # use cases; depends on domain and ports
│   ├── ports.py
│   ├── agents.py
│   ├── analysis.py
│   ├── planning.py
│   └── orchestrator.py
├── infrastructure/         # outbound adapters implementing application ports
│   ├── execution/
│   │   ├── contracts.py
│   │   ├── registry.py
│   │   ├── runtime.py
│   │   └── adapters/
│   ├── agents/openai.py
│   ├── github/client.py
│   ├── github/webhook.py
│   ├── persistence/sqlite.py
│   ├── source/git.py
│   └── settings.py
├── api/http.py             # inbound FastAPI adapter and composition root
└── main.py                 # stable ASGI entrypoint
```

Dependency direction:

```text
API/Infrastructure -> Application -> Domain
```

Application code accesses GitHub checks, persistence, source checkout and worker execution through protocols in `application/ports.py`. Concrete adapters are assembled only in `api/http.py`.

## Agentic control loop

```text
Deterministic repository analysis
        ↓
PlanningAgent: diff → risks + capability proposals
        ↓
Schema validation + deterministic baseline + PolicyValidator
        ↓
Trusted tool adapters execute the accepted plan
        ↓
Deterministic VerdictEngine
        ↓
ResultAnalysisAgent: findings → root cause + remediation
```

`OpenAIPlanningAgent` and `OpenAIResultAnalysisAgent` use structured model outputs. Model output cannot contain executable commands and cannot override the verdict. Fallback agents keep the system available while exposing `agent_mode=fallback`; they do not masquerade as model-backed execution.

## Boundaries

- `application/analysis.py`: deterministic language/project/config/diff discovery.
- `domain/capabilities.py`: tool-independent quality capability catalog.
- `application/planning.py`: repository intent and changes into versioned tasks.
- `infrastructure/execution/contracts.py`: adapter and invocation contracts.
- `infrastructure/execution/registry.py`: adapter discovery and selection only.
- `infrastructure/execution/runtime.py`: timeout, environment and process lifecycle only.
- `infrastructure/execution/adapters/`: one module per reusable tool/ecosystem adapter.
- `domain/verdict.py`: tool-independent run aggregation.
- `application/orchestrator.py`: lifecycle and DAG coordination only.
- `infrastructure/github/client.py`: provider authentication and result publishing only.
- `infrastructure/source/git.py`: immutable checkout and workspace cleanup.

The planner never emits a shell command. A worker adapter maps a validated task to an argument list and the executor uses `shell=False`.

## Extension procedure

1. Register a tool-independent capability and accepted target type.
2. Add or reuse a worker implementation in the registry.
3. Implement structured configuration-to-argv mapping and result parsing.
4. Add policy and contract tests.
5. Package the required tool in an isolated runtime image.

Repository-specific workers are not required. Implementations are reusable across repositories with the same language, target, or protocol.
