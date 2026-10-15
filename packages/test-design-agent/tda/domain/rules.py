import re
import json

from .contracts import (ExecutableCase, KnowledgeModel, SourceBundle, TestPlan,
                        ValidationIssue, ValidationReport, validate_json)


def unique(items, label):
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids) or any(not identifier.strip() for identifier in ids):
        raise ValueError(f"duplicate or empty {label} IDs")
    return set(ids)


def references(actual, known, label):
    if len(actual) != len(set(actual)) or not set(actual).issubset(known):
        raise ValueError(f"unknown or repeated {label} references")


def check_evidence(evidence, sources: SourceBundle, normative=False):
    documents = {doc.id: doc for doc in sources.documents}
    for claim in evidence:
        document = documents.get(claim.source_id)
        if not document or not claim.quote.strip() or claim.quote not in document.content:
            raise ValueError("evidence does not match a supplied source excerpt")
        if normative and document.kind not in {"prd", "srs", "openapi"}:
            raise ValueError("expected behavior needs normative PRD/SRS/OpenAPI evidence")


def normalize_evidence_quotes(evidence,sources):
    """Replace formatting-only model quotes with the exact supplied code excerpt.

    Words, punctuation and quoted literal contents must remain identical. The
    validator still requires an exact substring after this normalization.
    """
    documents={doc.id:doc for doc in sources.documents}
    tokens=re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\w+|[^\s]''')
    for claim in evidence:
        document=documents.get(claim.source_id)
        original_id=claim.source_id
        if document is None and ':' in claim.source_id:
            path=claim.source_id.split(':',1)[1]
            candidates=[doc for doc in sources.documents if doc.path==path]
            if len(candidates)==1:document=candidates[0]
        if not document or not claim.quote.strip():continue
        if claim.quote in document.content:
            if original_id not in documents:claim.source_id=document.id
            continue
        canonical=claim.quote.replace('\r\n','\n')
        for candidate in (canonical,canonical.replace('\n','\r\n')):
            if candidate in document.content:
                claim.quote=candidate;break
        else:
            if document.kind not in ('code','existing_tests','db_schema'):continue
            wanted=tokens.findall(claim.quote)
            matches=list(tokens.finditer(document.content));actual=[match.group() for match in matches]
            for index in range(len(actual)-len(wanted)+1):
                if actual[index:index+len(wanted)]==wanted:
                    claim.quote=document.content[matches[index].start():matches[index+len(wanted)-1].end()]
                    break
        if document and claim.quote in document.content and original_id not in documents:
            claim.source_id=document.id


def check_expectation(item, sources):
    check_evidence(item.evidence, sources, normative=item.expectation_basis == "specification")
    if item.expectation_basis == "implementation":
        documents = {doc.id: doc for doc in sources.documents}
        if not any(documents[e.source_id].kind in {"code", "db_schema", "existing_tests"}
                   for e in item.evidence):
            raise ValueError("implementation expectations need code/schema/existing-test evidence")


def validate_knowledge(knowledge: KnowledgeModel, sources: SourceBundle):
    reqs = unique(knowledge.requirements, "requirement")
    for requirement in knowledge.requirements:
        check_expectation(requirement, sources)
        if requirement.execution_scope != "http" and not (requirement.scope_reason or "").strip():
            raise ValueError("deferred requirements need an explicit runner limitation reason")
    for nodes, label in ((knowledge.features, "feature"), (knowledge.risks, "risk"),
                         (knowledge.dependencies, "dependency")):
        unique(nodes, label)
        for node in nodes:
            check_evidence(node.evidence, sources)
            if hasattr(node, "requirement_ids"):
                references(node.requirement_ids, reqs, "requirement")


def validate_plan(plan: TestPlan, knowledge: KnowledgeModel, sources: SourceBundle, kinds, runners=None):
    unique(plan.scenarios, "scenario")
    catalog = {"requirement": {r.id for r in knowledge.requirements},
               "feature": {f.id for f in knowledge.features}, "risk": {r.id for r in knowledge.risks},
               "operation": {o.id for o in sources.operations}}
    for scenario in plan.scenarios:
        enabled = set(runners or ['http'])
        scope_runners = {'http': {'http', 'ai', 'k6'}, 'unit': {'unit'}, 'browser': {'browser'}, 'load': {'k6'}, 'dependency': set()}
        if scenario.runner not in enabled:
            raise ValueError('Planner selected an unavailable runner')
        if scenario.kind not in kinds:
            raise ValueError("planner proposed a disabled test kind")
        for name, known in catalog.items():
            references(getattr(scenario, name + "_ids"), known, name)
        check_expectation(scenario, sources)
        requirements = {requirement.id: requirement for requirement in knowledge.requirements}
        if any(scenario.runner not in scope_runners[requirements[identifier].execution_scope] for identifier in scenario.requirement_ids):
            raise ValueError('Scenario runner does not support the referenced requirement scope')
        if scenario.kind == "performance" and scenario.runner == 'http':
            check_evidence(scenario.evidence, sources, normative=True)


def path_matches(template, path):
    pattern = re.escape(template)
    pattern = re.sub(r"\\\{[^}]+\\\}", "[^/]+", pattern)
    return re.fullmatch(pattern, path) is not None


class TestValidator:
    def validate(self, cases: list[ExecutableCase], sources: SourceBundle) -> ValidationReport:
        issues = []
        known = unique(cases, "case")
        operations = {op.id: op for op in sources.operations}

        def issue(case, code, message, repairable=False):
            issues.append(ValidationIssue(case_id=case.id, code=code, message=message, repairable=repairable))

        for case in cases:
            if case.oracle.case_id != case.id:
                issue(case, "ORACLE_ID", "Oracle ID must match its case")
            if any(dep not in known or dep == case.id for dep in case.depends_on) or len(case.depends_on) != len(set(case.depends_on)):
                issue(case, "DEPENDENCY", "Unknown, duplicate or self dependency")
            if case.runner != 'http':
                from .native_rules import validate_native
                issues.extend(validate_native(case, sources))
                continue
            if not case.steps or any((case.unit, case.browser, case.load, case.grading)):
                issue(case, 'HTTP_SHAPE', 'HTTP cases require request steps only')
            for request_index, request in enumerate(case.steps + case.cleanup):
                if not request.path.startswith("/") or request.path.startswith("//") or any(
                    char in request.path for char in ("{", "}", "?", "#", "\\", "\r", "\n")
                ) or any(ord(char) < 32 for char in request.path):
                    issue(case, "REQUEST_PATH", "Request needs a concrete relative path", True)
                if len({p.name for p in request.query}) != len(request.query):
                    issue(case, "QUERY", "Duplicate query parameter names", True)
                if request.body_json is not None:
                    try:
                        validate_json(request.body_json)
                    except (ValueError, TypeError):
                        issue(case, "REQUEST_JSON", "Invalid JSON request body", True)
                code_only_step = any(step.step_index == request_index and
                    any(a.kind == "status" and a.expectation_basis == "implementation"
                        for a in step.assertions) for step in case.oracle.steps) and request_index < len(case.steps)
                if operations and not code_only_step and not any(
                        request.method == op.method and path_matches(op.path, request.path)
                        for op in operations.values()):
                    issue(case, "UNKNOWN_API", "Request is not in the canonical OpenAPI inventory", True)
            for op_id in case.operation_ids:
                operation = operations.get(op_id)
                if operation is None:
                    issue(case, "API_MAPPING", "Unknown operation reference")
                    continue
                if not any(step.method == operation.method and path_matches(operation.path, step.path) for step in case.steps):
                    issue(case, "API_MAPPING", "Claimed operation not exercised by any step", True)
            indexes = [step.step_index for step in case.oracle.steps]
            if sorted(indexes) != list(range(len(case.steps))):
                issue(case, "ORACLE_STEPS", "Oracle must cover every step exactly once")
            assertion_kinds = set()
            for oracle_step in case.oracle.steps:
                kinds = [a.kind for a in oracle_step.assertions]
                assertion_kinds.update(kinds)
                if kinds.count("status") != 1:
                    issue(case, "STATUS_ORACLE", "Each step must have exactly one status assertion")
                for assertion in oracle_step.assertions:
                    try:
                        if assertion.kind not in {'status', 'json_equals', 'json_contains', 'latency_ms', 'text_equals', 'text_contains'}:
                            raise ValueError('assertion kind does not match HTTP runner')
                        check_expectation(assertion, sources)
                        if assertion.kind == "latency_ms":
                            check_evidence(assertion.evidence, sources, normative=True)
                        expected = validate_json(assertion.expected_json)
                        if assertion.kind in {'text_equals','text_contains'} and (not isinstance(expected,str) or assertion.pointer):
                            raise ValueError('Điều kiện văn bản cần chuỗi JSON và pointer rỗng.')
                        if assertion.kind == "status" and (type(expected) is not int or not 100 <= expected <= 599):
                            raise ValueError("status requires integer 100..599")
                        if assertion.kind == "latency_ms" and (type(expected) not in {int, float} or expected <= 0):
                            raise ValueError("latency threshold requires positive number")
                        if assertion.kind.startswith("json_") and assertion.pointer and not assertion.pointer.startswith("/"):
                            raise ValueError("invalid JSON pointer")
                        if assertion.kind in {"status", "latency_ms"} and assertion.pointer:
                            raise ValueError("non-JSON assertions need empty pointer")
                    except (ValueError, TypeError) as error:
                        issue(case, "ORACLE_EVIDENCE", 'Kết quả mong đợi chưa được mã hóa JSON hợp lệ.' if isinstance(error,json.JSONDecodeError) else str(error))
            if case.kind == "performance":
                if "latency_ms" not in assertion_kinds:
                    issue(case, "SLO_REQUIRED", "Performance cases need a sourced latency SLO")
                if any(not any(a.kind == "latency_ms" for a in step.assertions) for step in case.oracle.steps):
                    issue(case, "SLO_STEPS", "Every performance step requires a latency SLO")
                if any(step.method not in {"GET", "HEAD"} for step in case.steps):
                    issue(case, "PERFORMANCE_MUTATION", "Repeated mutation requires a dedicated fixture/load adapter")
            elif case.repetitions != 1:
                issue(case, "REPETITIONS", "Only performance smoke cases may repeat requests")
            if case.kind == "ai_evaluation" and not assertion_kinds.intersection({"json_equals", "json_contains"}):
                issue(case, "GROUND_TRUTH_REQUIRED", "AI evaluation requires explicit JSON ground truth")
        by_id = {case.id: case for case in cases}
        visiting, visited = set(), set()

        def visit(identifier):
            if identifier in visiting:
                issue(by_id[identifier], "DEPENDENCY_CYCLE", "Case dependency cycle")
                return
            if identifier in visited:
                return
            visiting.add(identifier)
            for dependency in by_id[identifier].depends_on:
                if dependency in by_id:
                    visit(dependency)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in by_id:
            visit(identifier)
        invalid = {item.case_id for item in issues}
        while True:
            blocked = {case.id for case in cases if set(case.depends_on).intersection(invalid)} - invalid
            if not blocked:
                break
            for identifier in blocked:
                issue(by_id[identifier], "DEPENDENCY_INVALID", "Depends on an invalid case")
            invalid.update(blocked)
        return ValidationReport(valid_case_ids=[case.id for case in cases if case.id not in invalid], issues=issues)
