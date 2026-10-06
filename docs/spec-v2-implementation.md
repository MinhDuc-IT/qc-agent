# QC Agent spec v2 implementation

## Confirmed deployment decisions

- The platform is multi-repository; `sample-app` is only a pilot fixture.
- Initial capability families: functional, integration, security, and performance.
- Repository unit tests are executed by QC Agent.
- The initial execution backend is the local Windows host.
- A Docker backend is present but stays disabled until Docker and reviewed, immutable images are configured.
- Run mode defaults to `observe`.
- Local Windows TargetProvisioner is allowed for the pilot, using trusted base-sha argv config.
- Planner and Triage use OpenAI when configured.
- `TRIAGE_MIN_CONF=0.9`, one confirmation task per finding, and planner diff budget 20,000 tokens.
- Jira/Slack remain disabled or dry-run without credentials.
- Organization policy is an operator-owned local YAML file.
- Artifact retention defaults to 30 days.
- Schedule/deployment triggers are implemented behind disabled feature flags.
- Self-heal may only publish an artifact/suggestion and must never push or open a PR.
- Mobile/iOS/emulator workers are outside the current rollout.

## Capability semantics

Planner output uses capability identifiers from `QC_AGENT_SPEC_v2.md`, never tool names:

- functional: `unit.run`, `functional.api`, `functional.e2e`
- integration: `integration.service`, `contract.consumer_provider`
- security: `security.secrets`, `security.sca`, `security.sast`, `security.dast`
- performance: `performance.smoke`, `performance.load`, `performance.stress`, `performance.soak`

The registry resolves a capability and target to a worker implementation. A missing implementation
is an explicit `NO_COMPATIBLE_WORKER` execution error, not a test pass or an implicit skip.

## Execution backends

`AGENT_QC_EXECUTION_BACKEND=local` is the Phase-1 setting and is suitable only for trusted pilot
repositories. Set it to `docker` later and provide `AGENT_QC_DOCKER_IMAGES_FILE`. The file maps an
executable to a reviewed image pinned by digest. Docker execution uses a read-only container root,
no network, CPU/memory/PID limits, and a workspace bind mount.

Do not enable the Docker backend with floating tags. `docker-images.example.yaml` intentionally
contains no guessed third-party image names.

## Security-sensitive configuration

Gate, capability, threshold, and target configuration is loaded from `.agent-qc.yaml` at the PR's
`base_sha`. The version changed by the PR is untrusted and must not affect that run.

## Implemented platform scope

The platform now includes v1.1 schemas, capability/target-based registry, deterministic plus OpenAI
planning, OpenAI plus policy-validated triage, confirmation tasks, raw/triaged verdicts, local target
provisioning, local/Docker execution backends, a recoverable SQLite queue, stale-run cancellation,
parallel DAG batches with concurrency limits, infrastructure-only retry, manual/schedule/deployment
trigger boundaries, private security publishing, dry-run Jira/Slack boundaries, artifacts and retention,
eval fixtures, and automated coverage for INV-1 through INV-15.

External worker processes remain independently deployable. A capability whose reviewed implementation
is not installed or registered returns `NO_COMPATIBLE_WORKER`; Agent-QC never fabricates a successful
result. Mobile/iOS implementations are intentionally outside the approved rollout.

The approved Phase-1 worker mapping is implemented in `docs/worker-rollout.md`:
Hercules for bounded functional scenarios, Keploy OSS replay for integration,
Semgrep/Trivy/Gitleaks for PR security, budgeted schedule-only Strix, and a custom
k6 agent that reuses repository scripts. PentAGI manifests are rejected until an
isolated worker VM is available.

Production acceptance still requires real operational evidence rather than more code: 2–4 weeks of
observe data, weekly audit of suppressions, an accepted false-negative rate, verified worker binaries
or external-agent deployments, and human approval before enabling a GitHub required check.
