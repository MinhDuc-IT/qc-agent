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
        return AggregateResult(verdict=verdict, tasks_total=len(results), tasks_passed=passed,
                               tasks_failed=failed, tasks_warning=warning, tasks_skipped=skipped,
                               execution_errors=errors)
