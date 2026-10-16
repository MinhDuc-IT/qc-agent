import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field


class WorkspaceMutation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    action:Literal['save_case','approve_case','retire_case','restore_case','resolve_finding','save_binding','validate_binding','validate_artifact','save_reusable_step','apply_proposal','dismiss_proposal','save_requirement']
    case_id:str|None=None
    case_data:dict|None=None
    binding:dict|None=None
    reusable_step:dict|None=None
    requirement_data:dict|None=None
    finding_id:str|None=None
    reason:str=Field(default='',max_length=8000)
    acceptance_criteria:str=Field(default='',max_length=8000)
    prerequisites_confirmed:bool=False


class WorkspaceJobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    action:Literal['supplement','regression','update_sources','ai_edit','review','bind','enable_qc']
    feedback:str=Field(default='',max_length=8000)
    case_ids:list[str]=Field(default_factory=list,max_length=50)
    module_ids:list[str]=Field(default_factory=list,max_length=30)


class WorkspaceEnableRequest(WorkspaceJobRequest):
    action:Literal['enable_qc']='enable_qc'
    reason:str=Field(min_length=1,max_length=8000)
    acknowledge_gaps:bool=False


class WorkspacePublishRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    case_ids:list[str]=Field(min_length=1,max_length=200)
    acknowledge_gaps:bool=False
    activate_qc:bool=False
    reason:str=Field(min_length=1,max_length=8000)


class CompletenessPolicyRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    policy:dict


def create_workspace_router(settings,store,session,csrf,authorized,artifact_path,github):
    router=APIRouter()

    @router.get('/assets/design-workspace.js')
    def workspace_script():
        return Response(Path(__file__).with_name('design_workspace.js').read_text(encoding='utf-8'),media_type='application/javascript',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    def workspace_for(project_id):
        from tda.application.workspace import from_baseline
        workspace=store.get_workspace(project_id)
        if workspace:return workspace
        project=store.get_project(project_id);job=store.get_job(project.current_job_id) if project and project.current_job_id else None
        if job and job.status in ('queued','running'):raise HTTPException(status_code=404,detail='TDA đang chuẩn bị workspace. Tiến độ sẽ được cập nhật tự động.')
        baselines=store.list_baselines(project_id)
        if not baselines:raise HTTPException(status_code=404,detail='Chưa có bộ test. Chọn tài liệu nếu cần, rồi bấm Sinh test để bắt đầu.')
        baseline=baselines[0];directory=artifact_path(baseline)
        try:
            from tda.pipeline import verify
            verify(directory)
            artifacts={name:json.loads((directory/name).read_text(encoding='utf-8')) for name in ('knowledge.json','sources.json','test_plan.json','cases/cases.json','validation_report.json','critic_review.json')}
            workspace=from_baseline(project_id,baseline,artifacts)
            try:return store.save_workspace(workspace,0,'migration','import_baseline')
            except ValueError:
                existing=store.get_workspace(project_id)
                if existing:return existing
                raise
        except (ValueError,OSError,KeyError):raise HTTPException(status_code=409,detail='Baseline cũ chưa chuyển được sang workspace. Sinh bộ mới từ source hiện tại.') from None

    @router.get('/api/v1/projects/{project_id}/workspace')
    async def get_workspace(project_id:str,request:Request):
        await authorized(project_id,session(request))
        workspace=workspace_for(project_id)
        return {'workspace':workspace.model_dump(),'history':store.workspace_history(project_id),'publications':[{key:value for key,value in item.items() if key!='snapshot'} for item in store.workspace_publications(project_id)]}

    @router.post('/api/v1/projects/{project_id}/workspace/init',status_code=201)
    async def initialize(project_id:str,request:Request):
        identity=session(request);csrf(request,identity);project=await authorized(project_id,identity,fresh=True)
        if store.get_workspace(project_id):raise HTTPException(status_code=409,detail='Workspace đã tồn tại.')
        job=store.get_job(project.current_job_id) if project.current_job_id else None
        if job and job.status in ('queued','running'):raise HTTPException(status_code=409,detail='Chờ job hiện tại hoàn tất trước khi tạo workspace thủ công.')
        description=await github.describe_repository(project.installation_id,project.repository.full_name)
        metadata=description['sha']
        from tda.domain.workspace import TestWorkspace,DesignStrategy,WorkspaceKnowledge
        from tda.domain.contracts import SourceBundle,KnowledgeModel
        from tda.application.workspace import refresh_workspace
        workspace=refresh_workspace(TestWorkspace(project_id=project_id,source_revision=metadata,
            source_snapshots=[metadata],sources=SourceBundle(documents=[],operations=[]),
            knowledge=WorkspaceKnowledge(requirements=[],features=[],risks=[],dependencies=[],open_questions=[]),
            criteria=[],strategy=DesignStrategy(items=[]),cases=[]))
        try:return {'workspace':store.save_workspace(workspace,0,identity.login,'initialize').model_dump()}
        except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from None

    @router.get('/api/v1/projects/{project_id}/workspace/history/{version}')
    async def history(project_id:str,version:int,request:Request):
        await authorized(project_id,session(request))
        workspace=store.get_workspace(project_id,version)
        if not workspace:raise HTTPException(status_code=404,detail='Không tìm thấy version.')
        return {'workspace':workspace.model_dump()}

    @router.post('/api/v1/projects/{project_id}/workspace/actions')
    async def mutate(project_id:str,request:Request,body:WorkspaceMutation):
        identity=session(request);csrf(request,identity);await authorized(project_id,identity,fresh=True)
        if len(body.model_dump_json().encode())>100000:raise HTTPException(status_code=422,detail='Nội dung chỉnh sửa quá lớn.')
        def apply(workspace):
            from tda.domain.workspace import DesignCase,AutomationBinding,ReviewDecision,ReusableStep
            from tda.domain.contracts import SourceDocument,Evidence
            from tda.application.workspace import refresh_workspace,design_digest,binding_design_issues
            from tda.domain.rules import TestValidator
            case=next((case for case in workspace.cases if case.id==body.case_id),None)
            change={'added':[],'changed':[],'retired':[]}
            reason=body.reason.strip()
            if body.action=='save_requirement':
                from tda.domain.contracts import RequirementNode
                from tda.domain.workspace import StrategyItem
                from tda.application.workspace import criteria_for,finding
                data=body.requirement_data or {}
                if not data.get('id') or not data.get('title') or not body.acceptance_criteria.strip():raise ValueError('Cần requirement ID, title và acceptance criteria.')
                content=body.acceptance_criteria.strip();digest=hashlib.sha256((identity.login+content).encode()).hexdigest();identifier='maintainer-'+digest[:20]
                if identifier not in {doc.id for doc in workspace.sources.documents}:
                    workspace.sources.documents.append(SourceDocument(id=identifier,kind='prd',path='workspace://requirements/'+identifier+'.md',sha256=hashlib.sha256(content.encode()).hexdigest(),content=content))
                requirement=RequirementNode(id=data['id'],title=data['title'],acceptance_criteria=[line.strip() for line in content.splitlines() if line.strip()],evidence=[Evidence(source_id=identifier,quote=content)])
                old=next((item for item in workspace.knowledge.requirements if item.id==requirement.id),None)
                workspace.knowledge.requirements=[item for item in workspace.knowledge.requirements if item.id!=requirement.id]+[requirement]
                workspace.criteria=criteria_for(workspace.knowledge)
                if not any(item.requirement_id==requirement.id for item in workspace.strategy.items):workspace.strategy.items.append(StrategyItem(requirement_id=requirement.id,techniques=['happy_path'],kinds=['functional'],rationale='Requirement do maintainer cung cấp.'))
                if old and old.acceptance_criteria!=requirement.acceptance_criteria:
                    for item in workspace.cases:
                        if requirement.id in item.requirement_ids and item.status!='retired':
                            item.status='needs_clarification';workspace.findings.append(finding([item.id],'blocker','source_change','Acceptance criteria đã thay đổi; review lại case.'))
                            if item.id in workspace.bindings:workspace.bindings[item.id].state='unbound'
            elif body.action=='save_case':
                updated=DesignCase.model_validate(body.case_data)
                if body.case_id and updated.id!=body.case_id:raise ValueError('Không đổi case ID.')
                if len(workspace.cases)>=200 and not case:raise ValueError('Bộ test đã đạt giới hạn 200 cases.')
                updated.revision=case.revision+1 if case else 1
                updated.author=identity.login;updated.human_edited=True;updated.status='draft'
                criteria=body.acceptance_criteria.strip()
                if criteria:
                    content='Acceptance criteria do '+identity.login+' xác nhận cho '+updated.id+'\n\n'+criteria
                    digest=hashlib.sha256(content.encode()).hexdigest();identifier='maintainer-'+digest[:20]
                    if identifier not in {doc.id for doc in workspace.sources.documents}:
                        workspace.sources.documents.append(SourceDocument(id=identifier,kind='prd',path='workspace://criteria/'+identifier+'.md',sha256=digest,content=content))
                    for step in updated.steps:
                        for expected in step.expected_results:
                            expected.origin='maintainer_confirmed';expected.evidence=[Evidence(source_id=identifier,quote=criteria)]
                if case:workspace.cases=[updated if item.id==case.id else item for item in workspace.cases];change['changed']=[updated.id]
                else:workspace.cases.append(updated);change['added']=[updated.id]
                # New content supersedes old AI findings for this case; structural checks still run.
                retained=[]
                for issue in workspace.findings:
                    if updated.id in issue.case_ids:
                        issue.case_ids=[identifier for identifier in issue.case_ids if identifier!=updated.id]
                        if not issue.case_ids:continue
                    retained.append(issue)
                workspace.findings=retained
                if updated.id in workspace.bindings:workspace.bindings[updated.id].state='unbound'
            elif body.action in ('apply_proposal','dismiss_proposal'):
                proposed=next((item for item in workspace.proposals if item.id==body.case_id),None)
                if not case or not proposed:raise ValueError('Không tìm thấy đề xuất cho case này.')
                if proposed.revision!=case.revision+1:raise ValueError('Case đã thay đổi sau khi AI đề xuất. Yêu cầu đề xuất mới.')
                if body.action=='apply_proposal':
                    proposed.status='draft';proposed.human_edited=case.human_edited
                    workspace.cases=[proposed if item.id==case.id else item for item in workspace.cases]
                    workspace.findings=[issue for issue in workspace.findings if case.id not in issue.case_ids]
                    if case.id in workspace.bindings:workspace.bindings[case.id].state='unbound'
                    change['changed']=[case.id]
                workspace.proposals=[item for item in workspace.proposals if item.id!=case.id]
            elif body.action=='approve_case':
                refresh_workspace(workspace)
                if not case or case.status in ('retired','needs_clarification') or any(issue.severity=='blocker' and (not issue.case_ids or case.id in issue.case_ids) for issue in workspace.findings):
                    raise ValueError('Case còn blocker; xử lý expected result/câu hỏi trước khi duyệt.')
                case.status='approved'
            elif body.action in ('retire_case','restore_case'):
                if not case or not reason:raise ValueError('Chọn case và ghi lý do.')
                case.status='retired' if body.action=='retire_case' else 'draft';case.revision+=1
                if case.id in workspace.bindings:workspace.bindings[case.id].state='unbound'
                change['retired' if body.action=='retire_case' else 'changed']=[case.id]
            elif body.action=='resolve_finding':
                issue=next((issue for issue in workspace.findings if issue.id==body.finding_id),None)
                if not issue or not reason:raise ValueError('Chọn finding và ghi quyết định review.')
                if issue.category in ('evidence','structure') and issue.severity=='blocker':raise ValueError('Cần sửa nội dung/source; không thể bỏ qua lỗi evidence hoặc schema.')
                workspace.findings=[item for item in workspace.findings if item.id!=issue.id]
            elif body.action=='validate_artifact':
                from tda.application.worker_artifacts import artifact_issues
                artifact=workspace.worker_artifacts.get(case.id) if case else None
                if not artifact or case.status!='approved' or not reason:raise ValueError('Chọn artifact của case đã duyệt và ghi lý do review.')
                issues=artifact_issues(case,artifact)+artifact.validation_issues
                if issues:raise ValueError('; '.join(issues))
                if artifact.preflight.get('status') not in ('pass','defect'):raise ValueError('Artifact chưa chạy được preflight; xử lý profile/fixtures/runtime và chuẩn bị lại.')
                artifact.state='validated';artifact.reviewer=identity.login
                workspace.automation_preparation[case.id].update(status='ready',confirmed_by=identity.login,confirmation=reason)
            elif body.action in ('save_binding','validate_binding'):
                if not case or case.status=='retired':raise ValueError('Không tìm thấy case để gắn automation.')
                binding=AutomationBinding.model_validate(body.binding) if body.action=='save_binding' else workspace.bindings.get(case.id)
                if binding and body.action=='save_binding':
                    binding.case_revision=case.revision;binding.design_sha256=design_digest(case)
                    binding.executable.title=case.title
                if not binding or binding.case_id!=case.id or binding.case_revision!=case.revision or binding.design_sha256!=design_digest(case):raise ValueError('Binding không khớp case revision hiện tại.')
                if binding.executable.requirement_ids!=case.requirement_ids or binding.executable.kind!=case.kind:raise ValueError('Binding phải giữ requirement IDs và loại testing của design.')
                report=TestValidator().validate([binding.executable],workspace.sources)
                from tda.application.worker_routes import route_issues
                binding.validation_issues=[issue.message for issue in report.issues]+route_issues(binding)
                binding.state='bound';binding.reviewer=None
                if body.action=='validate_binding':
                    if case.status!='approved':raise ValueError('Duyệt test case trước khi xác nhận mapping cho QC.')
                    if binding.validation_issues or not reason:raise ValueError('Binding còn lỗi hoặc chưa xác nhận mapping expected results.')
                    issues=binding_design_issues(case,binding.executable)
                    if case.id in workspace.automation_preparation:
                        from tda.application.automation import mapping_issues
                        issues+=mapping_issues(case,binding)
                        if workspace.automation_preparation[case.id].get('runtime_blockers'):
                            raise ValueError('Cấu hình runtime còn thiếu; xử lý điều kiện được báo rồi Chuẩn bị cho QC lại. Không thể bỏ qua bằng xác nhận mapping.')
                        if workspace.automation_preparation[case.id].get('prerequisites') and not body.prerequisites_confirmed:
                            raise ValueError('Xác nhận các fixtures/điều kiện cần thiết đã được chuẩn bị trước khi xác nhận mapping.')
                    if issues:raise ValueError('; '.join(issues))
                    binding.state='validated';binding.reviewer=identity.login
                    if case.id in workspace.automation_preparation:
                        workspace.automation_preparation[case.id]['status']='ready'
                        workspace.automation_preparation[case.id]['confirmed_by']=identity.login
                        workspace.automation_preparation[case.id]['confirmation']=reason
                workspace.bindings[case.id]=binding
            elif body.action=='save_reusable_step':
                step=ReusableStep.model_validate(body.reusable_step)
                workspace.reusable_steps=[item for item in workspace.reusable_steps if item.id!=step.id]+[step]
            workspace.decisions.append(ReviewDecision(case_id=body.case_id,finding_id=body.finding_id,action=body.action,
                actor=identity.login,reason=reason or 'Lưu nội dung test case',revision=workspace.version+1))
            workspace.last_changes=change
            refresh_workspace(workspace)
        try:
            updated=store.mutate_workspace(project_id,body.version,identity.login,body.action,apply)
            return {'workspace':updated.model_dump()}
        except (ValueError,TypeError,KeyError) as error:
            from pydantic import ValidationError
            detail='Nội dung chưa đúng schema test design.' if isinstance(error,ValidationError) else str(error)
            raise HTTPException(status_code=409,detail=detail) from None

    @router.post('/api/v1/projects/{project_id}/workspace/jobs',status_code=202)
    async def job(project_id:str,request:Request,body:WorkspaceJobRequest):
        identity=session(request);csrf(request,identity);await authorized(project_id,identity,fresh=True)
        if body.action in ('bind','enable_qc'):
            workspace=workspace_for(project_id)
            selected=[c for c in workspace.cases if c.id in set(body.case_ids)]
            if not selected or len(selected)!=len(set(body.case_ids)) or any(c.status!='approved' for c in selected):
                raise HTTPException(status_code=409,detail='Chọn cases đã duyệt để chuẩn bị cho QC.')
        if body.action=='enable_qc':raise HTTPException(status_code=409,detail='Dùng endpoint enable-qc để xác nhận phạm vi baseline.')
        if not settings.agent_qc_tda_enabled:raise HTTPException(status_code=409,detail='TDA đang tắt.')
        try:
            result=store.enqueue_workspace(project_id,body.version,body.action,identity.login,body.feedback,body.case_ids,body.module_ids)
            return {'job_id':result.job_id,'status':'queued'}
        except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from None

    @router.post('/api/v1/projects/{project_id}/workspace/enable-qc',status_code=202)
    async def enable_qc(project_id:str,request:Request,body:WorkspaceEnableRequest):
        identity=session(request);csrf(request,identity);await authorized(project_id,identity,fresh=True)
        if not settings.agent_qc_tda_enabled:raise HTTPException(status_code=409,detail='TDA đang tắt.')
        workspace=workspace_for(project_id)
        selected=[c for c in workspace.cases if c.id in set(body.case_ids)]
        if not selected or len(selected)!=len(body.case_ids) or any(c.status!='approved' for c in selected):
            raise HTTPException(status_code=409,detail='Chọn các cases đã duyệt để bật cho QC.')
        if not body.acknowledge_gaps:
            from tda.application.workspace import coverage_for
            coverage=coverage_for(workspace,selected)
            if coverage['missing_criteria'] or coverage['missing_techniques']:
                raise HTTPException(status_code=409,detail='Phạm vi đã chọn còn coverage gap. Xác nhận áp dụng một phần bộ test trước khi bật cho QC.')
        try:
            result=store.enqueue_workspace(project_id,body.version,'enable_qc',identity.login,body.feedback,body.case_ids,body.module_ids,
                activation={'reason':body.reason,'acknowledge_gaps':body.acknowledge_gaps})
            return {'job_id':result.job_id,'status':'queued'}
        except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from None

    @router.get('/api/v1/projects/{project_id}/workspace/export')
    async def export(project_id:str,request:Request,format:Literal['json','csv','gherkin']='json',publication:str|None=None):
        await authorized(project_id,session(request))
        if publication:
            item=next((item for item in store.workspace_publications(project_id) if item['version']==publication),None)
            if not item:raise HTTPException(status_code=404,detail='Không tìm thấy bản publish.')
            data=item['snapshot'];cases=data['cases']
        else:data=workspace_for(project_id).model_dump();cases=data['cases']
        if format=='json':content=json.dumps(data,ensure_ascii=False,indent=2);mime='application/json';extension='json'
        elif format=='csv':
            output=io.StringIO();writer=csv.writer(output);writer.writerow(['ID','Title','Type','Priority','Technique','Requirements','Criteria','Preconditions','Test data','Steps','Expected results','Review status'])
            def safe(value):
                value=str(value)
                return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
            for case in cases:
                writer.writerow([safe(value) for value in [case['id'],case['title'],case['kind'],case['priority'],case['technique'],', '.join(case['requirement_ids']),', '.join(case['criterion_ids']), '\n'.join(case['preconditions']),json.dumps(case['test_data'],ensure_ascii=False),'\n'.join(step['action'] for step in case['steps']),'\n'.join(expected['description'] for step in case['steps'] for expected in step['expected_results']),case['status']]])
            content='\ufeff'+output.getvalue();mime='text/csv';extension='csv'
        else:
            clean=lambda text:' '.join(str(text).splitlines())
            parts=['Feature: Bộ test của repository']
            for case in cases:
                parts+=['','  @'+case['id'],'  Scenario: '+clean(case['title'])]
                parts+=['    Given '+clean(value) for value in case['preconditions']]
                for step in case['steps']:
                    parts.append('    When '+clean(step['action']));parts+=['    Then '+clean(expected['description']) for expected in step['expected_results']]
            content='\n'.join(parts)+'\n';mime='text/plain';extension='feature'
        return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="test-suite.{extension}"','Cache-Control':'no-store'})

    @router.get('/api/v1/projects/{project_id}/workspace/completeness')
    async def completeness_report(project_id:str,request:Request,format:Literal['json','markdown']='json'):
        await authorized(project_id,session(request))
        from tda.application.workspace import refresh_workspace
        from tda.application.completeness import markdown
        report=refresh_workspace(workspace_for(project_id).model_copy(deep=True)).completeness
        content=json.dumps(report,ensure_ascii=False,indent=2) if format=='json' else markdown(report)
        return Response(content,media_type='application/json' if format=='json' else 'text/markdown',
                        headers={'Content-Disposition':'attachment; filename="completeness-report.'+('json' if format=='json' else 'md')+'"','Cache-Control':'no-store'})

    @router.post('/api/v1/projects/{project_id}/workspace/completeness-policy')
    async def save_completeness_policy(project_id:str,request:Request,body:CompletenessPolicyRequest):
        identity=session(request);csrf(request,identity);await authorized(project_id,identity,fresh=True)
        from tda.domain.completeness import CompletenessPolicy
        from tda.application.workspace import refresh_workspace
        try:
            policy=CompletenessPolicy.model_validate(body.policy)
            def mutate(workspace):
                workspace.completeness_policy=policy
                return refresh_workspace(workspace)
            result=store.mutate_workspace(project_id,body.version,identity.login,'completeness_policy',mutate)
        except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from None
        return {'workspace':result.model_dump()}

    @router.post('/api/v1/projects/{project_id}/workspace/publish')
    async def publish(project_id:str,request:Request,body:WorkspacePublishRequest):
        identity=session(request);csrf(request,identity);await authorized(project_id,identity,fresh=True)
        workspace=workspace_for(project_id)
        if workspace.version!=body.version:raise HTTPException(status_code=409,detail='Workspace đã thay đổi. Tải lại trước khi publish.')
        try:
            from ..infrastructure.test_design.workspace_publish import publish_workspace
            publication,baseline=publish_workspace(settings,workspace,body.case_ids,identity.login,body.reason,body.acknowledge_gaps,activate_qc=body.activate_qc)
            updated=store.save_publication(workspace,publication,identity.login,baseline)
            return {'workspace':updated.model_dump(),'publication':{key:value for key,value in publication.items() if key!='snapshot'}}
        except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from None
    return router
