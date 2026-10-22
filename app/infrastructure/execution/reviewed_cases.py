"""QC workers execute pinned intent against PR code; expected results never mutate."""
import hashlib,re
from uuid import uuid4
from tda.domain.worker_artifacts import WorkerArtifactBinding
from pathlib import Path
from tda.domain.workspace import TestWorkspace
from tda.application.worker_artifacts import artifact_issues
from tda.application.workspace import design_digest
from tda.infrastructure.repository_tools import RepositoryTools
from tda.infrastructure.artifacts import LocalArtifactStore
from ..test_design.execution_profiles import discover_profiles
from ..test_design.pytest_builder import PytestArtifactBuilder
from .pytest_environment import run_artifacts


def expected_evidence(packet,binding,status,output,report):
    failed_lines={int(n) for n in re.findall(re.escape(Path(binding.artifact.path).name)+r':(\d+):',output)}
    evidence=[]
    for mapping in binding.mapping:
        item_status='pass' if status=='pass' else 'defect' if status=='defect' and failed_lines.intersection(mapping.assertion_lines) else 'blocked'
        evidence.append({'expected_key':f'step:{mapping.design_step}/expected:{mapping.expected_result}',
            'status':item_status,'assertion_lines':mapping.assertion_lines,'assertion_sources':mapping.assertion_sources,
            'execution_report':report,'design_sha256':packet.design_sha256})
    return evidence


def run_reviewed(packets,context,workspace,settings,task,root,external_runtime=None,agent_factory=None):
    identifier=hashlib.sha256((task.run_id+task.task_id).encode()).hexdigest()[:32]+'-'+uuid4().hex[:8]
    artifacts=LocalArtifactStore(root/'qc-case-execution'/identifier)
    tools=RepositoryTools(workspace,artifacts)
    profiles=discover_profiles(tools)
    agent=None;rows=[]
    for packet in packets:
        row={'case_id':packet.case.id,'case_revision':packet.case.revision,'design_sha256':packet.design_sha256,
            'source':'tda_baseline','worker_id':packet.worker,'status':'blocked',
            'expected_results':[{'expected_key':f'step:{i}/expected:{j}','status':'blocked'} for i,s in enumerate(packet.case.steps) for j,_ in enumerate(s.expected_results)]}
        try:
            if packet.worker=='hercules':
                from .reviewed_hercules import run_hercules_case
                rows.append(run_hercules_case(packet,task,workspace,external_runtime));continue
            matches=[p for p in profiles if p.root==packet.profile_root]
            if len(matches)!=1:raise ValueError('Không tìm thấy execution profile của worker trong source PR.')
            binding=packet.cached_artifact
            cache_key=hashlib.sha256(('reviewed-case-1:'+context['project_id']+':'+packet.design_sha256).encode()).hexdigest()
            cache_path=root/'qc-case-cache'/(cache_key+'.json')
            if binding is None and cache_path.is_file() and cache_path.stat().st_size<=512000:
                try:
                    cached=WorkerArtifactBinding.model_validate_json(cache_path.read_text(encoding='utf-8'))
                    if (cached.case_id==packet.case.id and cached.design_sha256==packet.design_sha256
                        and not cached.validation_issues and not artifact_issues(packet.case,cached)):
                        binding=cached
                except (ValueError,OSError):pass
            if binding:
                if binding.validation_issues or artifact_issues(packet.case,binding):raise ValueError('Cached script không còn hợp lệ.')
                binding=binding.model_copy(deep=True);binding.profile=matches[0]
                execution=run_artifacts([binding],workspace,settings,task.run_id)
                result=next((r for r in execution.summary.get('results',[]) if r['case_id']==packet.case.id),{})
                outcome={'status':result.get('status','blocked'),'output':execution.output}
                row['execution_mode']='cached_script'
            else:
                if agent is None:
                    if agent_factory:agent=agent_factory(artifacts)
                    else:
                        if not settings.openai_api_key:raise ValueError('Worker QC cần OPENAI_API_KEY; không dùng key TDA.')
                        from tda.infrastructure.openai_agent import OpenAIStructuredAgent
                        agent=OpenAIStructuredAgent(settings.openai_api_key,settings.openai_model,artifacts,
                            max_calls=settings.agent_qc_case_max_calls,timeout_seconds=settings.agent_qc_tda_timeout_seconds)
                # Source excerpts are PR implementation; the approved oracle remains in packet/context.
                w=TestWorkspace(**context,cases=[packet.case],flow_modules=[{'scope':{'entry_paths':packet.module_paths},'case_ids':[packet.case.id]}])
                prepared=PytestArtifactBuilder(agent,tools,artifacts,settings,matches,
                    verify_source_snapshot=False,runtime_repair=False).run(w,[packet.case.id])
                binding=prepared.worker_artifacts.get(packet.case.id)
                state=prepared.automation_preparation.get(packet.case.id,{})
                if not binding or binding.validation_issues:
                    diagnostic=prepared.automation_job_diagnostic or {}
                    raise ValueError('; '.join([state.get('reason','Worker chưa chuẩn bị được test'),diagnostic.get('error_kind',''),*state.get('prerequisites',[])]))
                outcome=binding.preflight
                row['execution_mode']='description'
            if design_digest(packet.case)!=packet.design_sha256:raise ValueError('Worker changed reviewed intent')
            report='cases/'+hashlib.sha256(packet.case.id.encode()).hexdigest()[:20]+'.json'
            output=outcome.get('output','')
            evidence=expected_evidence(packet,binding,outcome.get('status','blocked'),output,str(artifacts.root/report))
            expected_count=sum(len(s.expected_results) for s in packet.case.steps)
            status='pass' if evidence and len(evidence)==expected_count and all(e['status']=='pass' for e in evidence) else 'defect' if any(e['status']=='defect' for e in evidence) else 'blocked'
            row.update(status=status,expected_results=evidence,output=output[-6000:],
                reason='' if status!='blocked' else 'Thiếu bằng chứng thực thi hoặc lỗi setup/script; không kết luận lỗi sản phẩm.')
            artifacts.write(report,{'packet':packet.model_dump(),'artifact':binding.model_dump(),'result':row})
            if status=='pass':
                cache_path.parent.mkdir(parents=True,exist_ok=True)
                temporary=cache_path.with_suffix('.'+uuid4().hex+'.tmp')
                temporary.write_text(binding.model_dump_json(),encoding='utf-8');temporary.replace(cache_path)
        except Exception as error:
            row['reason']=str(error)
        rows.append(row)
    artifacts.write('results.json',{'baseline_version':task.parameters.get('baseline_version'),'results':rows})
    return rows
