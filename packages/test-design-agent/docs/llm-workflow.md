# LLM workflow and QC onboarding

## Configure sources

Copy `examples/design.yaml` and set `root` relative to that configuration file. Declare
explicit file paths/globs for `prd`, `srs`, `openapi`, `code`, `db_schema` and `existing_tests`.
Source paths cannot leave root; credential files and generated dependency folders are rejected.
Total source bytes are bounded (default 160 KB); oversized inputs fail instead of silently
truncating acceptance criteria. OpenAPI operations are inventoried deterministically.

Sources selected in this configuration are sent to the configured OpenAI model during design.
Choose only sources authorized for that provider. Reports also contain source snapshots and
must be stored with the same access controls as the input documents.

Choose an explicit model supporting structured outputs. `--model` overrides config; optional
`reasoning_effort` must be supported by that model. No model is resolved automatically.
The adapter uses `responses.parse(..., text_format=PydanticModel, store=False)`, following
[official OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs).
`store=False` configures response storage; it does not imply zero data retention by the provider.

## Design and review

```powershell
python -m tda design examples/design.yaml --env-file .env --output baselines --runs runs
```

Add `--base-url` to validate on a reference service during design. Mutating requests additionally
require `--allow-mutations`; fixture/data isolation remains the operator's responsibility.

Run artifacts record config, bounded source snapshot, stage outputs, each planning/repair round,
execution report, coverage, final state, provider response IDs and token usage. Provider failures
record the exception type, not raw exception text that could disclose source text or credentials.
The CLI reports stage progress and exits nonzero for blocked or failed designs.

| State | Meaning |
|---|---|
| `awaiting_execution` | Design gates passed; reference execution still required |
| `ready_for_human_review` | Design gates and reference tests passed; still a draft |
| `needs_changes` | Missing evidence, unresolved questions, invalid cases, coverage or critic blockers |
| `execution_failed` | Design gates passed, execution detected defects/environment/blocked cases |
| `failed` | Source/config/provider/stage contract failed; inspect run artifacts |

To execute later and approve after inspecting every oracle and fixture:

```powershell
python -m tda validate baselines/<version>
python -m tda run baselines/<version> --base-url http://127.0.0.1:8080 --report reports/reference.json
python -m tda approve baselines/<version> --reviewer your-name --report reports/reference.json
```

Approval is an explicit local operator command, not authenticated identity verification.
Reports are local evidence, not signed attestations. Keep storage trusted; do not use a PR's
modified manifest or assertions as its own gate. A checksum is not proof of publisher identity.

## Start baseline execution worker

Set the following in the worker environment or its `.env`:

```dotenv
TDA_BASELINE_ROOT=C:/VSF/Agent-QC/qc-agent/.data/test-design/projects/<project_id>/baselines
TDA_QC_TOKEN=<independent-service-token>
TDA_QC_ALLOWED_HOSTS=127.0.0.1,localhost
TDA_QC_ALLOW_MUTATIONS=0
```

Only configure target hosts you authorize the worker to contact. Remote target hosts must be
added explicitly. For mutation tests, both server policy and task parameter `allow_mutations`
must be enabled. Requests with `auth=bearer_env` use worker-side `TDA_AUTH_TOKEN`; request
authentication is separate from QC service authentication.

```powershell
python -m uvicorn tda.infrastructure.qc_worker:create_app --factory --host 127.0.0.1 --port 8105 --env-file .env
```

The worker exposes `POST /v1/tasks`, `GET /v1/tasks/{run_id}`, `DELETE /v1/tasks/{run_id}` and
`GET /health`. Task endpoints require `Authorization: Bearer TDA_QC_TOKEN`.

## Connect QC-Agent

1. Merge the registration in `examples/external-agents.baseline.yaml` into QC's external
   agent registry; preserve other existing registrations.
2. Provide the same `TDA_QC_TOKEN` in QC's server environment.
3. Merge `examples/agent-qc.baseline.yaml` into the target repository's trusted base-SHA
   `.agent-qc.yaml`; replace `baseline_version` with the approved version. QC provisions
   the target using its existing repository target configuration.
4. Start/restart the services through your normal deployment process. This implementation
   does not modify running configuration or restart existing services.

`CapabilityPlanner` already reads `quality.baseline.http` and passes its parameters.
`baseline.http` is a running-target capability. The registry routes it to the baseline worker;
there is no tool fallback. This works for any language project exposing the HTTP service.
In multi-project repositories the current planner applies quality intent per project;
scope your deployment accordingly until project-specific baseline selection is introduced.

Alternatively export a concrete plan for a caller that accepts `ExecutionPlan`:

```powershell
python -m tda export-qc baselines/<version> --project-id root-python --run-id run-123 --external-agent
```

The export is an artifact, not a submitted run; it contains no target URL until provisioning.

## Current execution limits

Each suite permits at most 200 requests and a 120-second budget, with per-request timeout
up to 10 seconds and response body limit 1 MiB. Cases permit up to 10 steps, 5 cleanup steps
and 20 sequential performance repetitions. Shared state across cases and dynamic ID capture
are not supported; use independent fixtures and explicit paths.

Performance here is sourced per-request latency smoke evaluation, not statistical load or
p95 verification. AI evaluation is exact/subset JSON ground-truth evaluation, not an LLM judge.
Security uses declared HTTP authorization/validation cases, not a DAST exploit engine.
UI, DB execution, code coverage, artifact signatures and durable distributed queues need
additional adapters/infrastructure. Their absence is recorded instead of reporting fabricated coverage.
