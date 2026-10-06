# External testing-agent contract

External agents own their testing strategy, internal reasoning loop, tool selection, execution and evidence collection. Agent-QC owns dispatch, lifecycle, normalization, policy and GitHub publication.

## Registration

```yaml
agents:
  - agent_id: performance-testing-agent
    name: Performance Testing Agent
    version: 1.0.0
    capabilities: [performance.smoke, performance.load]
    supported_targets: [http_service]
    transport: http
    endpoint: http://performance-agent:8102
    source_root: /workspaces
    auth_secret_ref: secret://PERFORMANCE_AGENT_TOKEN
    timeout_seconds: 1800
    fallback_to_tools: false
```

When `auth_secret_ref` is present, Agent-QC resolves only the named environment
secret and sends `Authorization: Bearer <token>`. Secret values are never stored in
the manifest. `source_root` maps the checkout directory name into a container or
remote worker's shared workspace mount; omit it for a worker running on the same
host filesystem.

## Start

`POST /v1/tasks`

```json
{
  "schema_version": "1.0",
  "task": {
    "task_id": "task_123",
    "run_id": "run_123",
    "capability": "performance.load",
    "objective": "Assess checkout latency regression",
    "execution_preference": "external_agent",
    "target": {"type": "http_service", "project_id": "backend"}
  },
  "project": {"id": "backend", "root": "backend", "language": "python"},
  "source": {"type": "local_path", "ref": "/workspaces/run_123"}
}
```

Response:

```json
{"run_id": "external_run_456", "status": "queued"}
```

## Observe/result

`GET /v1/tasks/external_run_456`

Non-terminal:

```json
{"run_id": "external_run_456", "status": "running"}
```

Terminal:

```json
{
  "run_id": "external_run_456",
  "status": "completed",
  "result": {
    "schema_version": "1.0",
    "task_id": "external-value-is-normalized-by-agent-qc",
    "run_id": "external-value-is-normalized-by-agent-qc",
    "capability": "performance.load",
    "execution_status": "completed",
    "verdict": "fail",
    "summary": {"requests": 1000},
    "metrics": {"p95_latency_ms": 780},
    "findings": []
  }
}
```

## Cancel

`DELETE /v1/tasks/external_run_456`

The external agent should stop work idempotently. Agent-QC calls this endpoint when its deadline expires.

## Routing policy

```text
auto:
  compatible external agent -> external result
  transport failure + fallback allowed -> trusted tool adapter
  no compatible external agent -> trusted tool adapter

external_agent:
  compatible external agent -> external result
  no compatible external agent -> execution failure

tool:
  direct trusted tool adapter
```

Bearer service authentication and shared-mount path translation are implemented.
Production deployments should replace bearer tokens with mTLS/workload identity
where available, and add remote artifact references, health/capacity routing,
durable callbacks or queues, and per-agent concurrency limits.
