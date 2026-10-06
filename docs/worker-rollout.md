# Phase-1 worker rollout

## Execution matrix

| Trigger | Capability | Implementation | Model/API key |
|---|---|---|---|
| Pull request | unit tests | Existing language CLI adapters | None |
| Pull request | functional API/E2E | TestZeus Hercules 1.0.2 | Worker-owned `HERCULES_LLM_API_KEY` |
| Pull request | integration/contract replay | Keploy OSS 3.8.58 | Cloud AI disabled; no AI key |
| Pull request | secrets/SCA/SAST | Gitleaks, Trivy, Semgrep | None |
| Pull request/manual/schedule | performance | Custom external agent + k6 2.3.0 | None |
| Schedule only | DAST/agentic pentest | Strix 1.4.0 | Worker-owned `STRIX_LLM_API_KEY`, USD cap |
| Any | PentAGI | Rejected by registry | Not deployed until isolated VM |

`OPENAI_API_KEY` remains exclusive to the Agent-QC Planner, result-analysis agent,
and Triage. External worker keys never appear in `WorkerTask`, planner input, or the
orchestrator process in a separated deployment.

## Repository policy example

This file is read from the base branch, so a pull request cannot raise its own
budgets or replace commands:

```yaml
version: "1"
quality:
  functional:
    e2e:
      enabled: auto
  integration:
    service:
      enabled: auto
      parameters:
        keploy_path: keploy
        sut_command: python -m uvicorn app.main:app --port 8080
  performance:
    smoke:
      enabled: auto
      parameters:
        script_path: performance/k6.js
        vus: 1
        duration: 10s
  security:
    secrets: {enabled: auto}
    sca: {enabled: auto}
    sast: {enabled: auto}
```

Keploy replays existing recorded tests; it does not call Keploy test generation or
cloud synchronization. On native Windows, Keploy must launch the SUT itself, hence
the reviewed `sut_command`. The Windows CLI currently requires a free Keploy login,
but this is separate from cloud AI usage.

The k6 worker first reuses `parameters.script_path`, then searches these paths:
`.agent-qc/k6.js`, `performance/k6.js`, `tests/performance/k6.js`, and
`k6/script.js`. Only when none exists does it generate a bounded HTTP smoke script
in the worker artifact directory. VUs and duration are capped by capability.

## Local Windows processes

Copy `external-agents.local.example.yaml` to `external-agents.yaml`. Use a unique
transport token for each service and set the matching token in the orchestrator.
Start each installed worker from `qc-agent` in its own terminal:

```powershell
python -m uvicorn workers.functional_hercules.service:app --port 8101
python -m uvicorn workers.integration_keploy.service:app --port 8102
python -m uvicorn workers.performance_k6.service:app --port 8104
```

Install Semgrep, Trivy, and Gitleaks on the Agent-QC host and ensure `semgrep`,
`trivy`, and `gitleaks` resolve on `PATH`. They are deterministic tool adapters,
not long-running HTTP services.

Do not start Strix on the current machine while Docker is absent. When an isolated
nightly host with Docker is ready, start its service on port 8103 with:

```powershell
$env:STRIX_LLM_API_KEY = "..."
$env:STRIX_LLM_MODEL = "openai/gpt-5-mini"
$env:STRIX_MAX_BUDGET_USD = "5"
python -m uvicorn workers.security_strix.service:app --port 8103
```

Enable `AGENT_QC_SCHEDULE_ENABLED=true` only after that worker is available. Submit
nightly requests through `/api/v1/triggers/schedule` with
`requested_capabilities: ["security.dast"]`. Policy removes `security.dast` from
all non-schedule plans, and the Strix worker independently checks the trigger type.

## Container phase

Each worker has a Dockerfile and `workers/docker-compose.example.yaml` describes
the topology. Container manifests set `source_root: /workspaces`, causing the
orchestrator to translate the host checkout path to the mounted worker path. Strix
is behind the `nightly-security` profile because it requires a
Docker sandbox/socket. Before production, replace version tags with reviewed image
digests and validate builds on the target Linux worker; Docker is not currently
available on the development machine, so these images have not been built here.
