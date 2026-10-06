# Invariant verification matrix

| Invariant | Enforcement | Automated verification |
|---|---|---|
| INV-1 | GitHub App has no Contents write; no push/merge implementation | `test_inv1_github_manifest_has_no_write_content_permission` |
| INV-2 | GitHub publisher redacts `security.*` details and annotations | `test_security_finding_details_are_not_published` |
| INV-3 | Repository policy is loaded from immutable `base_sha` | `test_repository_policy_is_loaded_from_base_sha` |
| INV-4 | Suppression requires confidence and objective evidence | `test_triage_cannot_suppress_without_objective_evidence` |
| INV-5 | Unknown triage escalates and remains gate-counted | Triage eval `unknown-no-evidence.json` |
| INV-6 | Planner/Triage decisions are append-only records | `test_inv6_decision_log_is_append_only` |
| INV-7 | `observe` is default; raw and triaged verdicts are retained | `test_inv7_observe_is_default` and verdict tests |
| INV-8 | Org policy capability/implementation kill switches | `test_inv8_capability_kill_switch` |
| INV-9 | Golden datasets reject common PII | `test_inv9_golden_dataset_rejects_common_pii` |
| INV-10 | Target secret values require `secret://`; payload root is strict | `test_inv10_target_secrets_require_secret_refs` |
| INV-11 | Manual/internal triggers require token and actor allowlist | `test_inv11_manual_run_requires_authentication_and_allowlist` |
| INV-12 | WorkerTask rejects unknown root fields | `test_worker_task_rejects_unknown_root_fields` |
| INV-13 | LLM proposal is additive and VerdictEngine remains deterministic | `test_inv13_agent_cannot_remove_rule_required_capability` |
| INV-14 | PR text cannot alter deterministic required rules | `test_inv14_prompt_injection_in_diff_cannot_remove_rules` |
| INV-15 | Docker backend applies network/filesystem/resource isolation | `test_inv15_docker_backend_applies_isolation_flags` |
