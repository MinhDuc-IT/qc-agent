"""Bounded context and item-level checkpoints, without executing repository code."""
import json
from ..domain.investigation import ModuleStudy


def compact_sources(sources,max_chars=9000):
    result=[];remaining=max_chars
    for source in sources:
        excerpts=[]
        for text in source.get('excerpts',[]):
            if any(text in old for old in excerpts):continue
            excerpts=[old for old in excerpts if old not in text]
            if remaining<=0:break
            part=text[:min(remaining,2500)];remaining-=len(part);excerpts.append(part)
        if excerpts:result.append({**source,'excerpts':excerpts,'context_limited':True})
    return result


def citation_sources(claims,documents,max_chars=12000):
    by_id={d.id:d for d in documents};result=[];seen=set();remaining=max_chars
    for claim in claims:
        key=(claim.source_id,claim.quote)
        if key in seen:continue
        seen.add(key);document=by_id.get(claim.source_id)
        if not document or claim.quote not in document.content:continue
        # Quotes are never rewritten or clipped. Omitted citations remain explicit.
        if len(claim.quote)>remaining:continue
        remaining-=len(claim.quote)
        result.append({'source_id':document.id,'path':document.path,'kind':document.kind,'excerpts':[claim.quote]})
    return result


def retain_items(engine,scope,study,checkpoint=None):
    """Keep only individually grounded items; rejected siblings cannot erase them."""
    if study.module_id!=scope.id:return checkpoint,[{'type':'scope','id':study.module_id,'reason':'module_id does not match'}]
    accepted=ModuleStudy.model_validate(checkpoint) if checkpoint else ModuleStudy(module_id=scope.id,summary=study.summary,scope_fit=study.scope_fit,requirements=[],flow=[],behaviors=[])
    accepted.summary=study.summary;accepted.scope_fit=study.scope_fit;accepted.scope_rationale=study.scope_rationale
    issues=[];document_ids={r['id'] for r in engine.document_inventory.get('requirements',[])}
    def store(field,item):
        values=getattr(accepted,field)
        key=lambda value:getattr(value,'id',None) or (value.role,value.description)
        existing=next((i for i,value in enumerate(values) if key(value)==key(item)),None)
        if existing is None:
            if len(values)>={'requirements':20,'flow':30,'behaviors':40}[field]:raise ValueError('Checkpoint item limit reached; keep remaining scope explicit')
            values.append(item)
        else:values[existing]=item
    for field in ('requirements','flow','behaviors'):
        for original in getattr(study,field):
            item=original.model_copy(deep=True)
            try:
                identifier=getattr(item,'id','flow')
                if field!='flow' and not identifier.startswith((scope.id+'-',scope.id+'_',scope.id+'.')) and not (field=='requirements' and identifier in document_ids):raise ValueError('ID must use module prefix or explicit PRD requirement ID')
                if field=='behaviors' and not set(item.requirement_ids)<={r.id for r in accepted.requirements}:raise ValueError('Unknown requirement IDs')
                probe=ModuleStudy.model_validate({'module_id':scope.id,'summary':'','requirements':[],'flow':[],'behaviors':[],field:[item]})
                engine.ground_study(probe)
                if field=='requirements':
                    kinds={d.id:d.kind for d in engine.tools.documents.values()}
                    if item.expectation_basis=='specification' and all(kinds[e.source_id] in ('code','existing_tests','db_schema') for e in item.evidence):item.expectation_basis='implementation'
                if field=='behaviors':
                    kinds={d.id:d.kind for d in engine.tools.documents.values()}
                    evidence={kinds[e.source_id] for e in item.evidence}
                    if item.basis=='specified' and not evidence&{'prd','srs','openapi'}:raise ValueError('Missing normative evidence')
                    if item.basis=='implementation_derived' and not evidence&{'code','existing_tests','db_schema'}:raise ValueError('Missing implementation evidence')
                    if item.basis=='conflict' and not (evidence&{'prd','srs','openapi'} and evidence&{'code','existing_tests','db_schema'}):raise ValueError('Conflict needs both sources')
                store(field,item)
            except ValueError as error:issues.append({'type':field,'id':getattr(item,'id','flow'),'reason':str(error)[:250],'item':original.model_dump()})
    accepted.questions=list(dict.fromkeys(accepted.questions+study.questions))
    accepted.exclusions=list(dict.fromkeys(accepted.exclusions+study.exclusions))
    return accepted.model_dump(),issues


def checkpoint_summary(checkpoint):
    if not checkpoint:return None
    return {'requirements':[{'id':r['id'],'title':r['title'][:180]} for r in checkpoint['requirements']],
            'behaviors':[{'id':b['id'],'expected':b['expected_behavior'][:220],'requirements':b['requirement_ids']} for b in checkpoint['behaviors'][:12]],
            'other_behavior_ids':[b['id'] for b in checkpoint['behaviors'][12:]],
            'flow_nodes':[{'role':n['role'],'description':n['description'][:200]} for n in checkpoint['flow'][:8]]}


def context_size(context):return len(json.dumps(context,ensure_ascii=False))


def compact_tool_results(results,max_chars=10000):
    values=[];remaining=max_chars
    for entry in results:
        result=dict(entry['result'])
        if 'content' in result:
            text=result['content'];part=text[:min(remaining,4000)]
            if len(part)<len(text):part=part.rsplit('\n',1)[0]
            result['content']=part;result['end_line']=result.get('start_line',1)+len(part.splitlines())-1
            result['context_limited']=len(part)<len(text);remaining-=len(part)
        if isinstance(result.get('matches'),list):result['matches']=result['matches'][:10]
        values.append({**entry,'result':result})
    return values
