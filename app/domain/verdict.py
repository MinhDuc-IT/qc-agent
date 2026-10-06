from .models import AggregateResult, WorkerResult


class VerdictEngine:
    def evaluate(self, results: list[WorkerResult]) -> AggregateResult:
        errors = sum(result.execution_status != "completed" for result in results)
        failed = sum(result.verdict == "fail" for result in results)
        warning = sum(result.verdict == "warning" for result in results)
        skipped = sum(result.verdict == "skipped" for result in results)
        passed = sum(result.verdict == "pass" for result in results)
        if errors:
            verdict = "unknown"
        elif failed:
            verdict = "fail"
        elif warning:
            verdict = "warning"
        elif passed:
            verdict = "pass"
        else:
            verdict = "skipped"
        effective_failures = 0
        for result in results:
            if result.verdict != "fail":
                continue
            suppressible = bool(result.findings) and all(
                finding.triage is not None
                and finding.triage.proposed_action == "suppress"
                for finding in result.findings
            )
            if not suppressible:
                effective_failures += 1
        triaged = ("unknown" if errors else "fail" if effective_failures else
                   "warning" if warning else "pass" if passed or failed else "skipped")
        return AggregateResult(verdict=verdict, verdict_raw=verdict, verdict_triaged=triaged,
                               tasks_total=len(results), tasks_passed=passed,
                               tasks_failed=failed, tasks_warning=warning, tasks_skipped=skipped,
                               execution_errors=errors)
