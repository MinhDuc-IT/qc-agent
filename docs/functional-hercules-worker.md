# Functional worker: TestZeus Hercules

This service adapts TestZeus Hercules 1.0.2 to the Agent-QC external-agent
contract. It is used for `functional.e2e` and `functional.api`; repository unit
tests continue to use deterministic tool adapters.

## Local Windows pilot

Hercules requires Python 3.11-3.13, its Python package, and a Playwright browser:

```powershell
cd qc-agent
python -m venv .venv-hercules
.\.venv-hercules\Scripts\Activate.ps1
python -m pip install -r .\workers\functional_hercules\requirements.txt
python -m playwright install chromium
```

Set worker-owned secrets and bounds, then start the service:

```powershell
$env:HERCULES_LLM_API_KEY = "..."
$env:HERCULES_LLM_MODEL = "gpt-4o-mini"
$env:HERCULES_AGENT_TOKEN = "use-a-long-random-token"
$env:FUNCTIONAL_AGENT_TOKEN = $env:HERCULES_AGENT_TOKEN
$env:HERCULES_ALLOWED_SOURCE_ROOT = (Resolve-Path .\.agent-qc-workspaces).Path
$env:AGENT_QC_EXTERNAL_AGENTS_FILE = (Resolve-Path .\external-agents.local.example.yaml).Path
python -m uvicorn workers.functional_hercules.service:app --host 127.0.0.1 --port 8101
```

Start the Agent-QC API in a second terminal with the same
`FUNCTIONAL_AGENT_TOKEN` and `AGENT_QC_EXTERNAL_AGENTS_FILE`. The LLM key is deliberately
not required by the orchestrator and is never included in a `WorkerTask`.

The planner must provide a reachable HTTP target in `task.target.ref` (or
`parameters.base_url`) plus up to `HERCULES_MAX_SCENARIOS` scenarios. An explicit
Gherkin document can instead be supplied in `parameters.feature`.

Artifacts are written below `HERCULES_WORK_ROOT`. Telemetry, interactive prompts,
and headed browser mode are disabled by the bridge. For later container execution,
build `workers/functional_hercules/Dockerfile` from the `qc-agent` directory.
