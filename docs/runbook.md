# Agent-QC runbook

## Safe defaults

- Keep `mode=observe` until the repository has passed Phase-5 acceptance review.
- Schedule and deployment triggers are disabled by default.
- Jira and Slack publishers are dry-run without reviewed credentials/configuration.
- The GitHub App has no Contents write permission. Agent-QC never pushes, merges, or opens PRs.
- Mobile/iOS/emulator workers are outside the current rollout.

## Emergency controls

Edit the operator-owned `org-policy.yaml` and add an identifier under the relevant kill switch:

```yaml
kill_switches:
  capabilities: [performance.load]
  implementations: [performance-testing-agent]
  repositories: []
  domains: []
```

The change applies to the next run. To disable all execution, stop the API/worker process or disable
the GitHub App webhook. Do not change repository policy from a pull request to perform an override.

## Local TargetProvisioner incident

The local Windows provisioner executes reviewed base-branch argv directly on the host and is less
isolated than Docker. Set `AGENT_QC_LOCAL_TARGET_ENABLED=false`, terminate the Agent-QC process, and
manually verify that no child SUT process remains. Re-enable only after reviewing base-sha target config.

## Planner or Triage incident

Remove `OPENAI_API_KEY` or set `AGENT_QC_AGENT_ENABLED=false`. Planner falls back to deterministic
rules; Triage falls back to objective evidence and treats unknown findings as real failures for enforce.
DecisionLog must exist for every model-backed decision.

## Artifact retention

Run periodically:

```powershell
python scripts/prune_artifacts.py
```

Default retention is 30 days. Self-heal output is suggestion-only JSON; there is no PR/push path.

## Merge-gate rollout

Collect at least 2–4 weeks of observe data, audit suppressed flaky/environment findings weekly, and
measure false-negative rate. Only a human owner may change a repository to `enforce` and configure
the GitHub ruleset required check `Agent-QC`.
