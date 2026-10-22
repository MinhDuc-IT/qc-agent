"""GitHub job adapter for the new read-only flow investigation agent."""
import asyncio
import hashlib
from pathlib import Path
from threading import Event
from ...domain.models import Actor,Trigger,TriggerContext,Installation,Revision,QCRun


class ManagedDesignEngine:
    def __init__(self,store,github,source,settings,agent_factory=None):
        self.store=store;self.github=github;self.source=source;self.settings=settings
        self.agent_factory=agent_factory;self.stop=Event()

    def stopped(self,job):
        project=self.store.get_project(job.project_id)
        return self.stop.is_set() or not project or not project.enabled or project.current_job_id!=job.job_id

    def documents(self,project,root,previous):
        from tda.domain.contracts import SourceDocument
        from tda.infrastructure.repository_tools import RepositoryTools
        temporary=RepositoryTools(root,type('NoArtifacts',(),{'write':lambda *args:None})())
        docs=[]
        for selection in project.source_paths:
            prefix=selection.rstrip('/')
            choices=[p for p in temporary.files if p==prefix or p.startswith(prefix+'/')]
            if not choices:raise ValueError('Tài liệu nguồn không tồn tại trong revision: '+selection)
            for path in choices:
                if Path(path).suffix.lower() not in ('.md','.rst','.txt','.json','.yaml','.yml'):continue
                doc=temporary.snapshot(path).model_copy(deep=True)
                doc.kind='openapi' if Path(path).suffix.lower() in ('.json','.yaml','.yml') and any(t in doc.content for t in ('openapi:','"openapi"','swagger:','"swagger"')) else 'prd'
                docs.append(doc)
        for identifier in project.uploaded_document_ids:
            doc=self.store.get_document(project.project_id,identifier)
            if not doc:raise ValueError('Tài liệu upload không còn tồn tại.')
            docs.append(SourceDocument(id=identifier,kind=doc['kind'],path='uploads/'+identifier+'/'+doc['name'],
                sha256=doc['sha256'],content=doc['content']))
        if previous:
            docs.extend(d for d in previous.sources.documents if d.path.startswith('workspace://'))
        if sum(len(d.content.encode('utf-8')) for d in docs)>500000:raise ValueError('Tài liệu đầu vào vượt 500 KB; chọn bớt tài liệu.')
        return docs

    def run(self,job,project,root,previous,artifacts):
        from tda.infrastructure.repository_tools import RepositoryTools
        from tda.infrastructure.openai_agent import OpenAIStructuredAgent
        from tda.application.flow_design import FlowDesignAgent
        action=(job.workspace_request or {}).get('action','new')
        docs=self.documents(project,root,previous)
        tools=RepositoryTools(root,artifacts,docs)
        # Maintainer requirements are trusted as data, never as instructions.
        for doc in docs:
            if doc.path.startswith('workspace://'):
                tools.observed[doc.id]=[doc.content]
        if action=='enable_qc':
            if not previous:raise ValueError('No reviewed workspace')
            from .execution_profiles import discover_profiles
            updated=previous.model_copy(deep=True)
            updated.execution_profiles=discover_profiles(tools)
            updated.generation_job_id=job.job_id
            return updated
        agent=self.agent_factory(artifacts) if self.agent_factory else OpenAIStructuredAgent(
            self.settings.tda_openai_api_key,self.settings.agent_qc_tda_model or self.settings.openai_model,artifacts,
            max_calls=self.settings.agent_qc_tda_max_calls,max_output_tokens=self.settings.agent_qc_tda_max_output_tokens,
            reasoning_effort=self.settings.agent_qc_tda_reasoning_effort,timeout_seconds=self.settings.agent_qc_tda_timeout_seconds,
            max_input_chars=self.settings.agent_qc_tda_max_input_chars)
        def progress(message):
            if self.stopped(job):return
            self.store.update_job_project(job,state='designing',message=message)
        if action in ('bind','enable_qc'):
            if not previous:raise ValueError('Chưa có bộ test để chuẩn bị cho QC.')
            case_ids=(job.workspace_request or {}).get('case_ids',[])
            if self.settings.agent_qc_generated_pytest_enabled:
                from .execution_profiles import discover_profiles,pytest_case_ids
                profiles=discover_profiles(tools)
                native_ids=pytest_case_ids(previous,case_ids,profiles)
                if native_ids:
                    from .pytest_builder import PytestArtifactBuilder
                    previous=PytestArtifactBuilder(agent,tools,artifacts,self.settings,profiles,stopped=lambda:self.stopped(job),progress=progress,reuse_unavailable=action=='enable_qc').run(previous,native_ids)
                    case_ids=[identifier for identifier in case_ids if identifier not in native_ids]
                    if not case_ids:
                        previous.generation_job_id=job.job_id
                        return previous
            from tda.application.automation import AutomationBuilder
            if not project.target_profile:
                from .discovery import SourceDiscovery
                detected=SourceDiscovery().discover(root,max_bytes=self.settings.agent_qc_tda_max_source_bytes)['target_profile']
                # The existing target adapter runs at repo root. Do not pretend a nested service is configured.
                if detected.get('kind') in ('fastapi','flask') and '.' in detected.get('app','').split(':')[0]:
                    detected={'kind':'unavailable','reason':'Service nằm trong subdirectory; cần adapter khởi động và dependencies tương ứng.'}
                if detected.get('kind') in ('fastapi','flask','node','declared','command'):
                    project.target_profile=detected
                    self.store.update_job_project(job,target_profile=detected)
            environment={'mutations_allowed':project.allow_mutations,'target_profile':project.target_profile,
                'test_isolation':self.settings.agent_qc_test_isolation,'browser_channel':self.settings.agent_qc_browser_channel,
                'credentials_available':bool(__import__('os').getenv(f'PROJECT_{project.installation_id}_{project.repository_id}_API_TOKEN'))}
            updated=AutomationBuilder(agent,tools,artifacts,stopped=lambda:self.stopped(job),progress=progress,environment=environment).run(previous,case_ids)
            updated.generation_job_id=job.job_id
            return updated
        engine=FlowDesignAgent(agent,tools,artifacts,max_turns=self.settings.agent_qc_tda_max_inquiry_turns,
            max_rounds=self.settings.agent_qc_tda_max_rounds,max_cases=self.settings.agent_qc_tda_max_cases,
            stopped=lambda:self.stopped(job),progress=progress)
        request=job.workspace_request or {}
        if action in ('review','ai_edit') and previous:
            by_id={d.id:d for d in previous.sources.documents}
            # Re-observe exact cited ranges before accepting a stored flow's evidence.
            for payload in previous.flow_modules:
                study=payload.get('study') or {}
                claims=[e for r in study.get('requirements',[]) for e in r['evidence']]+[e for n in study.get('flow',[]) for e in n['evidence']]+[e for b in study.get('behaviors',[]) for e in b['evidence']]
                for claim in claims:
                    doc=by_id.get(claim['source_id'])
                    if not doc:continue
                    if doc.path.startswith('workspace://'):continue
                    actual=tools.snapshot(doc.path)
                    if actual.sha256!=doc.sha256:raise ValueError('Source thay đổi; dùng Cập nhật theo nguồn thay đổi.')
                    position=actual.content.find(claim['quote']);line=actual.content[:max(position,0)].count('\n')+1
                    tools.read_file(doc.path,line,line+119)
        if action=='regression':action='supplement'
        updated=engine.run(project.project_id,job.revision,previous,action,request.get('feedback',''),request.get('case_ids',[]),request.get('module_ids',[]))
        updated.generation_job_id=job.job_id
        return updated

    async def execute(self,job):
        root=None
        try:
            project=self.store.get_project(job.project_id)
            if self.stopped(job):job.status='superseded';return
            if job.attempts>3:raise ValueError('Job đã vượt số lần recovery cho phép.')
            if (job.workspace_request or {}).get('action')!='enable_qc' and not self.agent_factory and not self.settings.tda_openai_api_key:raise ValueError('Quản trị cần cấu hình TDA_OPENAI_API_KEY riêng.')
            previous=self.store.get_workspace(project.project_id);expected=previous.version if previous else 0
            request=job.workspace_request or {}
            if request and request['version']!=expected:raise ValueError('Workspace đã thay đổi trước khi job bắt đầu.')
            metadata=await self.github.describe_repository(project.installation_id,project.repository.full_name)
            job.revision=metadata['sha']
            if request.get('action') in ('review','ai_edit','bind') and previous and previous.source_revision!=job.revision:
                raise ValueError('Source thay đổi; dùng Cập nhật theo nguồn thay đổi trước.')
            self.store.save_job(job)
            run=QCRun(run_id='tda_'+job.job_id,trigger_context=TriggerContext(
                trigger=Trigger(type='manual',mode='manual',event='test_design',action='investigate',delivery_id=job.job_id,
                                actor=Actor(id='tda',login=request.get('actor','maintainer'))),
                installation=Installation(id=project.installation_id),repository=project.repository,
                revision=Revision(head_sha=job.revision,base_sha=job.revision,head_ref=project.repository.default_branch,base_ref=project.repository.default_branch)))
            self.store.update_job_project(job,state='discovering',message='Đang lấy source snapshot và chuẩn bị repository tools.')
            root=await self.source.prepare(run)
            from tda.infrastructure.artifacts import LocalArtifactStore
            directory=self.settings.agent_qc_tda_dir/'runs'/job.job_id/f'attempt-{job.attempts}'
            artifacts=LocalArtifactStore(directory);job.artifact_dir=str(directory);self.store.save_job(job)
            updated=await asyncio.to_thread(self.run,job,project,root,previous,artifacts)
            if self.stopped(job):job.status='superseded';return
            updated=self.store.save_workspace(updated,expected,request.get('actor','agent'),request.get('action','flow_generate'),job_id=job.job_id)
            partial=updated.automation_job_diagnostic if request.get('action') in ('bind','enable_qc') else updated.generation_diagnostic or any(m['status'] in ('pending','partial','investigating') for m in updated.flow_modules)
            job.status='partial' if partial else 'completed'
            message=f'Đã lưu {len(updated.cases)} test cases theo {len(updated.flow_modules)} modules. '
            message+='Draft chưa hoàn tất; xem module và phần còn thiếu.' if partial else 'Review expected results và đối chiếu tài liệu/code trước khi publish.'
            if request.get('action') in ('bind','enable_qc'):
                from tda.application.automation import preparation_state
                states=[preparation_state(updated,c) for c in updated.cases if c.id in request.get('case_ids',[])]
                message=f'Chuẩn bị cho QC: {states.count("needs_review")} cases chờ xác nhận mapping; {states.count("needs_setup")} cần bổ sung điều kiện; {states.count("manual")} kiểm tra thủ công.'
                if partial:message+=' Lượt chuẩn bị chưa hoàn tất; cases đã xử lý được giữ.'
            if request.get('action')=='enable_qc':
                from .case_routing import route_cases
                from .reviewed_baselines import publish_reviewed
                options=request.get('activation') or {}
                if not options.get('reason'):raise ValueError('Missing explicit activation request')
                packets,blocked=route_cases(updated,request.get('case_ids',[]),updated.execution_profiles,self.settings)
                ready=[p.case.id for p in packets]
                artifacts.write('activation_report.json',{'ready_case_ids':ready,'blocked_cases':blocked,'activated':False})
                if ready and (not blocked or options.get('acknowledge_gaps')):
                    publication,baseline=publish_reviewed(self.settings,updated,packets,blocked,request['actor'],options['reason'],options.get('acknowledge_gaps',False))
                    publication['automation_exclusions']=blocked
                    updated=self.store.save_publication(updated,publication,request['actor'],baseline,activation_job_id=job.job_id)
                    job.baseline_version=baseline.version
                    job.status='partial' if blocked else 'completed'
                    message=f'Đã bật {len(ready)} cases cho QC; {len(blocked)} cases bị chặn. Mở case để xem lý do.'
                    artifacts.write('activation_report.json',{'ready_case_ids':ready,'blocked_cases':blocked,'activated':True,'baseline':baseline.version})
                    artifacts.write('test_workspace.json',updated.model_dump())
                    self.store.update_job_project(job,state='active',message=message)
                else:
                    job.status='partial'
                    for item in blocked:
                        updated.qc_enrollment[item['case_id']]={'status':'unsupported','reason':item['reason'],
                            'case_revision':next(c.revision for c in updated.cases if c.id==item['case_id'])}
                    updated=self.store.save_workspace(updated,updated.version,request['actor'],'qc_routing_blocked',job_id=job.job_id)
                    artifacts.write('test_workspace.json',updated.model_dump())
                    self.store.update_job_project(job,state='needs_changes',message=f'Chưa bật cho QC: {len(blocked)} cases bị chặn. Baseline đang dùng được giữ nguyên.')
                return
            self.store.update_job_project(job,state='needs_changes' if partial else 'ready_for_review',message=message)
        except Exception as error:
            import logging
            logging.getLogger(__name__).exception('Test design job failed: %s',job.job_id)
            job.status='cancelled' if self.stopped(job) else 'failed';job.error=type(error).__name__
            from tda.application.ports import AgentFailure
            job.error_message=('LLM chưa hoàn tất: '+error.error_kind) if isinstance(error,AgentFailure) else str(error) if isinstance(error,ValueError) else 'Job chưa hoàn tất; xem artifact diagnostic.'
            self.store.update_job_project(job,state='failed',message=job.error_message)
        finally:
            self.store.save_job(job)
            if root is not None:await asyncio.to_thread(self.source.cleanup,root)
