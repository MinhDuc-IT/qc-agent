"""Determine worker support without asking an LLM or requiring TDA preflight."""
from tda.application.workspace import design_digest
from tda.application.worker_artifacts import artifact_issues
from tda.domain.case_execution import ReviewedCasePacket
from .execution_profiles import pytest_case_ids


def route_cases(workspace,case_ids,profiles,settings):
    native=set(pytest_case_ids(workspace,case_ids,profiles))
    documents={d.id:d for d in workspace.sources.documents}
    packets=[];blocked=[]
    from ..external_agents.registry import ExternalAgentRegistry
    registry=ExternalAgentRegistry.from_yaml(settings.agent_qc_external_agents_file)
    for case in workspace.cases:
        if case.id not in case_ids:continue
        issues=[f.message for f in workspace.findings if f.severity=='blocker' and (not f.case_ids or case.id in f.case_ids)]
        if case.status!='approved':issues.append('Case chưa được duyệt.')
        paths=list(dict.fromkeys(p for m in workspace.flow_modules if case.id in m['case_ids'] for p in m['scope']['entry_paths']))
        evidence_paths={documents[e.source_id].path for step in case.steps for expected in step.expected_results for e in expected.evidence if e.source_id in documents and documents[e.source_id].kind in ('code','existing_tests')}
        candidates=[p for p in profiles if any(p.root=='.' or path.startswith(p.root.rstrip('/')+'/') for path in evidence_paths or paths)]
        worker=None;root=None;requirements=[];capability='functional.e2e'
        scopes={r.execution_scope for r in workspace.knowledge.requirements if r.id in case.requirement_ids}
        if case.id in native and settings.agent_qc_generated_pytest_enabled and len(candidates)==1:
            worker='python-pytest-agent';root=candidates[0].root
            requirements=['python',*candidates[0].services,'repository fixtures']
        elif case.kind in ('functional','integration','security') and ('http' in scopes or 'browser' in scopes or any(p.endswith(('.tsx','.jsx','.vue','.html','.svelte')) for p in evidence_paths or paths)):
            browser='browser' in scopes or any(p.endswith(('.tsx','.jsx','.vue','.html','.svelte')) for p in evidence_paths or paths)
            capability='functional.e2e' if browser else 'functional.api'
            workers=[m for m in registry.manifests if m.enabled and 'hercules' in (m.agent_id+' '+m.name).lower() and capability in m.capabilities and ('web_app' if browser else 'http_service') in m.supported_targets]
            if workers:worker='hercules';requirements=['HTTP test target','browser','isolated test data']
        if not worker:issues.append('Chưa có worker nhận mô tả cho case này; cần script/plan hoặc recordings phù hợp.')
        if issues:
            blocked.append({'case_id':case.id,'reason':'; '.join(issues),'prerequisites':requirements});continue
        cached=workspace.worker_artifacts.get(case.id)
        state=workspace.automation_preparation.get(case.id,{})
        if not (cached and cached.preflight.get('status')=='pass' and not cached.validation_issues
                and not artifact_issues(case,cached) and cached.design_sha256==design_digest(case)
                and state.get('source_revision')==workspace.source_revision):cached=None
        packets.append(ReviewedCasePacket(case=case,design_sha256=design_digest(case),source_revision=workspace.source_revision,
            worker=worker,worker_capability=capability,profile_root=root,module_paths=paths,requirements=requirements,cached_artifact=cached))
    return packets,blocked
