"""Reconcile a deterministic inventory against evidence-backed design mappings."""
import hashlib
import re
from collections import Counter
from ..domain.contracts import KnowledgeModel
from ..domain.rules import check_evidence,normalize_evidence_quotes,validate_knowledge
from .stages import classify_implementation_expectations,classify_runner_scope
from .ports import AgentFailure


def evidence_items(claims,workspace):
    documents={doc.id:doc for doc in workspace.sources.documents};found=set()
    for claim in claims:
        doc=documents.get(claim.source_id)
        if not doc or claim.quote not in doc.content:continue
        # Repeated short assertions cannot identify which handler/function is meant.
        if doc.content.count(claim.quote)!=1:continue
        start=doc.content.index(claim.quote);line=doc.content[:start].count('\n')+1
        end=line+claim.quote.count('\n')
        for item in workspace.inventory.items:
            if item.source_path==doc.path and item.kind!='branch' and line<=item.end_line and end>=item.line:
                found.add(item.id)
    return found


def claims_for(case):
    return [claim for step in case.steps for expected in step.expected_results for claim in expected.evidence]


def claim_in_span(claim,item,workspace):
    doc=next((doc for doc in workspace.sources.documents if doc.id==claim.source_id),None)
    if not doc or doc.path!=item.source_path or not claim.quote or claim.quote not in doc.content:return False
    if hashlib.sha256(doc.content.encode()).hexdigest()!=item.source_sha256:return False
    offset=0
    while True:
        start=doc.content.find(claim.quote,offset)
        if start<0:return False
        line=doc.content[:start].count('\n')+1;end=line+claim.quote.count('\n')
        if line<=item.end_line and end>=item.line:return True
        offset=start+1


def explicit_reference(case,item):
    text=' '.join([case.title,case.objective]+[step.action for step in case.steps])
    if item.kind=='api':
        declared=re.findall(r'\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b[^/\n]{0,40}(/[A-Za-z0-9_./{}-]+)',text)
        pattern=re.escape(item.route)
        pattern=re.sub(r'\\\{[^}]+\\\}',r'[^/]+',pattern)
        return any(method==item.method and re.fullmatch(pattern,path) for method,path in declared)
    return bool(re.search(r'\b'+re.escape(item.symbol.split('.')[-1])+r'\b',text))


def behavior_supported(case,item,workspace):
    claims=claims_for(case)
    if item.id in evidence_items(claims,workspace):return True
    return explicit_reference(case,item) and any(claim_in_span(claim,item,workspace) for claim in claims)


def branch_supported(case,item,workspace):
    documents={doc.id:doc for doc in workspace.sources.documents}
    parent=next((parent for parent in workspace.inventory.items if parent.id==item.parent_id),None)
    body_available=parent is not None and item.line<=parent.end_line
    return any(claim.source_id in documents and documents[claim.source_id].path==item.source_path
        and claim.quote in documents[claim.source_id].content
        and (item.excerpt.strip() in claim.quote or body_available and claim_in_span(claim,item,workspace)) for claim in claims_for(case))


def normalize_inventory_parents(case,workspace):
    items={item.id:item for item in workspace.inventory.items}
    for identifier in case.branch_ids:
        branch=items.get(identifier);parent=items.get(branch.parent_id) if branch else None
        if branch and branch.kind=='branch' and parent and branch.parent_id not in case.inventory_ids and behavior_supported(case,parent,workspace) and branch_supported(case,branch,workspace):
            case.inventory_ids.append(branch.parent_id)


def mapping_issues(case,workspace):
    items={item.id:item for item in workspace.inventory.items}
    if any(identifier not in items or items[identifier].kind=='branch' for identifier in case.inventory_ids):
        return ['Inventory reference không hợp lệ.']
    if any(identifier not in items or items[identifier].kind!='branch' for identifier in case.branch_ids):
        return ['Branch reference không hợp lệ.']
    claims=[claim for step in case.steps for expected in step.expected_results for claim in expected.evidence]
    issues=[]
    for identifier in case.inventory_ids:
        if not behavior_supported(case,items[identifier],workspace):issues.append('Inventory mapping chưa có evidence của behavior: '+identifier)
    for identifier in case.branch_ids:
        item=items[identifier]
        if item.parent_id not in case.inventory_ids or not branch_supported(case,item,workspace):
            issues.append('Branch mapping cần parent behavior và exact evidence của điều kiện: '+identifier)
    return issues


def report_for(workspace,cases=None,invalid=None,global_blocker=False):
    usable=[case for case in (workspace.cases if cases is None else cases) if case.status!='retired' and case.id not in (invalid or set()) and not global_blocker]
    items=workspace.inventory.items;documents={doc.path:doc for doc in workspace.sources.documents}
    requirement_map={item.id:[] for item in items}
    for requirement in workspace.knowledge.requirements:
        for identifier in evidence_items(requirement.evidence,workspace):requirement_map[identifier].append(requirement.id)
        # Explicit endpoint criteria disambiguate repeated decorators in one router file.
        declared={(method,re.sub(r'\{[^}]+\}','{}',route)) for text in requirement.acceptance_criteria
                  for method,route in re.findall(r'\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s`"\',;]+)',text)}
        evidence_paths={doc.path for doc in workspace.sources.documents if any(claim.source_id==doc.id and claim.quote in doc.content for claim in requirement.evidence)}
        for item in items:
            if item.kind=='api' and item.source_path in evidence_paths and (item.method,re.sub(r'\{[^}]+\}','{}',item.route)) in declared:
                if requirement.id not in requirement_map[item.id]:requirement_map[item.id].append(requirement.id)
    for item in items:
        if item.kind=='branch':requirement_map[item.id]=list(requirement_map.get(item.parent_id,[]))
    case_map={item.id:[] for item in items}
    for case in usable:
        inferred=evidence_items([claim for step in case.steps for expected in step.expected_results for claim in expected.evidence],workspace)
        for identifier in inferred|set(case.inventory_ids)|set(case.branch_ids):
            if identifier in case_map:case_map[identifier].append(case.id)
    behaviors=[item for item in items if item.kind in ('api','ui_route','logic')]
    unknown=list(workspace.inventory.unknown)
    gaps=[];missing=[];covered=[];branches=[];existing_tests=[]
    for item in items:
        doc=documents.get(item.source_path)
        row={'id':item.id,'kind':item.kind,'source_path':item.source_path,'symbol':item.symbol,'method':item.method,
             'route':item.route,'requirement_ids':requirement_map[item.id],'case_ids':case_map[item.id],
             'outcome':item.outcome,'conditional':item.conditional,'extractor':item.extractor}
        loaded=doc is not None and hashlib.sha256(doc.content.encode()).hexdigest()==item.source_sha256
        if not loaded:
            row['reason']='Source chưa nằm trong snapshot đầy đủ hoặc không khớp version.';row['state']='unknown'
            if item.kind!='existing_test':unknown.append(row)
        elif item.kind=='existing_test':row['state']='reference_only'
        elif item.kind=='branch':row['state']='covered' if case_map[item.id] else 'missing_case'
        elif not requirement_map[item.id]:row['state']='missing_catalog';gaps.append(row)
        elif not case_map[item.id]:row['state']='missing_case';missing.append(row)
        else:row['state']='covered';covered.append(row)
        if item.kind=='branch':branches.append(row)
        if item.kind=='existing_test':existing_tests.append(row)
    exclusions=[{'requirement_id':req.id,'reason':req.scope_reason or 'Dependency/build metadata, ngoài behavior regression.'}
                for req in workspace.knowledge.requirements if req.execution_scope=='dependency']
    exclusions+=workspace.inventory.exclusions
    gaps.sort(key=lambda row:({'api':0,'ui_route':1,'logic':3 if '/alembic/' in row['source_path'] else 2}.get(row['kind'],4),row['source_path'],row['symbol']))
    return {'completeness':workspace.inventory.completeness,'files_scanned':workspace.inventory.files_scanned,
        'counts':dict(Counter(item.kind for item in items)),'behaviors_total':len(behaviors),
        'covered':covered,'missing_catalog':gaps,'missing_cases':missing,'unknown':unknown,'out_of_scope':exclusions,
        'branches':branches,'existing_tests':existing_tests,'branch_total':len(branches),
        'branches_mapped':sum(row['state']=='covered' for row in branches),
        'limitations':workspace.inventory.limitations+['Mapping chứng minh traceability của thiết kế; không chứng minh branch đã chạy.']}


def reconcile_catalog(agent,workspace,store,max_batches=4):
    """Only add grounded catalog entries; preserve previous/human requirements."""
    existing={req.id for req in workspace.knowledge.requirements}
    for _ in range(max_batches):
        gaps=report_for(workspace)['missing_catalog'][:8]
        if not gaps:break
        identifiers={row['id'] for row in gaps}
        inventory=[item.model_dump() for item in workspace.inventory.items if item.id in identifiers]
        paths={row['source_path'] for row in gaps}
        context={'missing_inventory':inventory,'existing_requirements':[req.model_dump() for req in workspace.knowledge.requirements],
                 'source_ids_by_path':{doc.path:doc.id for doc in workspace.sources.documents if doc.path in paths},
                 'sources':{'documents':[doc.model_dump() for doc in workspace.sources.documents if doc.path in paths],'operations':[]}}
        instructions=('Bổ sung catalog regression cho đúng missing_inventory. Không chạy code. Trả KnowledgeModel chỉ chứa '
            'requirements mới, tối đa 8; features/risks có thể rỗng. Mỗi requirement có acceptance criteria cụ thể '
            'cho outcomes/điều kiện, exact source evidence; source code dùng expectation_basis=implementation. '
            'execution_scope=http cho API, browser cho UI, unit cho hàm thuần; xác định đúng scope theo source. '
            'Không đưa dependency/build metadata hay thiếu SLO thành requirement chức năng. '
            'Không tự đặt SLO hay business rule. Viết tiếng Việt; không sửa requirement cũ.')
        try:
            knowledge=agent.propose('workspace.catalog',instructions,context,KnowledgeModel)
            for node in knowledge.requirements+knowledge.features+knowledge.risks:normalize_evidence_quotes(node.evidence,workspace.sources)
            classify_implementation_expectations(knowledge.requirements,workspace.sources);classify_runner_scope(knowledge,workspace.sources)
            validate_knowledge(knowledge,workspace.sources)
            additions=[]
            for requirement in knowledge.requirements[:8]:
                if requirement.id in existing or not evidence_items(requirement.evidence,workspace).intersection(identifiers):continue
                additions.append(requirement);existing.add(requirement.id)
            if not additions:break
            workspace.knowledge.requirements.extend(additions)
            store.event('workspace.catalog','completed',{'added':len(additions)})
        except (ValueError,AgentFailure):
            store.event('workspace.catalog','partial_completed',{'reason':'Catalog bổ sung chưa qua validation; inventory gap vẫn được báo.'})
            break
    return workspace


def generation_targets(workspace):
    report=report_for(workspace)
    criteria={item.id:item for item in workspace.criteria}
    in_scope={req.id for req in workspace.knowledge.requirements if req.execution_scope!='dependency'}
    missing_criteria=[criteria[identifier] for identifier in workspace.coverage.get('missing_criteria',[]) if identifier in criteria and criteria[identifier].requirement_id in in_scope]
    unrepresented=[req.id for req in workspace.knowledge.requirements if req.id in in_scope and not any(req.id in case.requirement_ids and case.status!='retired' for case in workspace.cases)]
    branches=[row for row in report['branches'] if row['state']=='missing_case' and set(row['requirement_ids'])&in_scope]
    candidates=list(dict.fromkeys(unrepresented+[item.requirement_id for item in missing_criteria]+[r for row in branches for r in row['requirement_ids'] if r in in_scope]))
    docs={doc.id:doc.path for doc in workspace.sources.documents};reqs={req.id:req for req in workspace.knowledge.requirements}
    def priority(identifier):
        paths=[docs.get(claim.source_id,'') for claim in reqs[identifier].evidence]
        if any('/api/routes/' in path or path.endswith(('/security.py','/deps.py')) for path in paths):return 0
        if any('/src/routes/' in path for path in paths):return 1
        if any('/components/' in path for path in paths):return 3
        if any('/alembic/' in path for path in paths):return 4
        return 2
    focus=sorted(candidates,key=priority)[:8]
    target_criteria=[item.id for item in missing_criteria if item.requirement_id in focus][:16]
    target_branches=[row['id'] for row in branches if set(row['requirement_ids'])&set(focus)][:16]
    inventory_ids={row['id'] for row in report['missing_cases'] if set(row['requirement_ids'])&set(focus)}
    inventory_ids.update(item.parent_id for item in workspace.inventory.items if item.id in target_branches)
    inventory_ids.update(row['id'] for row in report['covered'] if set(row['requirement_ids'])&set(focus))
    return {'focus_requirement_ids':focus,'target_criterion_ids':target_criteria,'target_branch_ids':target_branches,
            'inventory_targets':[item.model_dump() for item in workspace.inventory.items if item.id in inventory_ids or item.id in target_branches]}
