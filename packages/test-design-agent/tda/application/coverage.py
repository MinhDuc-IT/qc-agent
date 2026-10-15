class CoverageAnalyzer:
    def analyze(self, knowledge, sources, cases, validation, execution, policy, scopes=None):
        valid = set(validation.valid_case_ids)
        executable = [case for case in cases if case.id in valid]
        results = {} if execution is None else {result["case_id"]: result for result in execution["results"]}
        executed = [case for case in executable if results.get(case.id, {}).get("status") in {"pass", "defect"}]
        metrics = {}
        supported=set(scopes or ['http'])
        active_requirements = {node.id for node in knowledge.requirements if node.execution_scope in supported}
        for name, nodes in (("requirement", knowledge.requirements), ("feature", knowledge.features),
                            ("risk", knowledge.risks), ("api", sources.operations)):
            discovered = list(nodes)
            if name == "requirement":
                nodes = [node for node in nodes if node.id in active_requirements]
            elif name in {"feature", "risk"}:
                nodes = [node for node in nodes if not node.requirement_ids or set(node.requirement_ids) & active_requirements]
            field = "operation_ids" if name == "api" else name + "_ids"
            known = {node.id for node in nodes}
            designed = {identifier for case in executable for identifier in getattr(case, field)} & known
            observed = {identifier for case in executed for identifier in getattr(case, field)} & known
            ratio = len(designed) / len(known) if known else None
            execution_ratio = len(observed) / len(known) if known else None
            threshold = getattr(policy, name)
            metrics[name] = {"total": len(known), "designed_covered": len(designed),
                             "total_discovered": len(discovered),
                             "excluded": sorted({node.id for node in discovered} - known),
                             "executed_covered": len(observed), "designed_ratio": ratio,
                             "executed_ratio": execution_ratio, "threshold": threshold,
                             "missing": sorted(known - designed), "not_executed": sorted(known - observed),
                             "satisfied": ratio is None or ratio >= threshold}
        return {"metrics": metrics, "code_coverage": None,
                "scope": "Coverage for enabled runners only; deferred requirements are not covered",
                "runnable_scopes": sorted(supported),
                "deferred_requirements": [node.model_dump() for node in knowledge.requirements if node.execution_scope not in supported],
                "code_coverage_reason": "No instrumented code-coverage adapter configured",
                "design_satisfied": all(metric["satisfied"] for metric in metrics.values()),
                "execution_satisfied": execution is not None and all(
                    metric["executed_ratio"] is None or metric["executed_ratio"] >= metric["threshold"]
                    for metric in metrics.values()),
                "limitations": ["Coverage links do not prove assertion completeness; critic and human review required."]}
