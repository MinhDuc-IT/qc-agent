# Test Design Agent baselines

The bundled `packages/test-design-agent` provides an evidence-based LLM pipeline and an authenticated
external worker for approved HTTP test suites. See its
[workflow guide](../packages/test-design-agent/docs/llm-workflow.md).

QC capability `baseline.http` targets `http_service`, requires target provisioning and has a
180-second timeout. Existing quality planning, base-SHA configuration reads, external registry,
transport, normalization and verdict aggregation handle this capability without a new
orchestrator branch. Enable it explicitly; it is not added to default checks.

Pin an approved baseline version in the trusted repository quality configuration:

```yaml
quality:
  baseline:
    http:
      enabled: true
      parameters:
        baseline_version: <approved-20-character-version>
```

Register the packaged standalone worker using its example external-agent YAML and provide `TDA_QC_TOKEN`
to both services. The worker reads from an operator-configured trusted baseline root, verifies
checksums/state/version and ignores PR checkout paths. PR code remains the target under test.

Results include baseline version and per-case evidence in `summary.results`. Assertion failures
produce test-failure findings and `fail`; blocked/invalid/environment cases produce execution
errors and `unknown`. The worker does not generate, repair or approve tests during PR execution.

Deployment configuration and running services are not changed automatically by adding this
capability. Load/rubric/non-HTTP adapters, project-specific baseline selection, signed storage
and durable worker state remain separate infrastructure work.
