"""Lossless Markdown requirement inventory; extraction is not semantic coverage."""
import re
import math
from ..domain.contracts import RequirementNode,Evidence


def index_documents(documents):
    sections=[];requirements=[];seen=set()
    for document in documents:
        if document.kind not in ('prd','srs','openapi'):continue
        heading='';start=1;buffer=[]
        def flush():
            if buffer:sections.append({'source_id':document.id,'path':document.path,'heading':heading,'start_line':start,'content':'\n'.join(buffer)})
        for line_number,line in enumerate(document.content.splitlines(),1):
            if re.match(r'^#{1,6}\s+',line):
                flush();heading=re.sub(r'^#+\s+','',line);buffer=[];start=line_number
            buffer.append(line)
            # Explicit identifiers in normative tables, not references in prose.
            row=re.match(r'^\s*\|\s*((?:FR|NFR|REQ|BR|AC)[-_][A-Za-z0-9_-]+)\s*\|\s*([^|]+)\|',line)
            if row and (document.id,row[1],row[2].strip()) not in seen:
                seen.add((document.id,row[1],row[2].strip()))
                requirements.append({'id':row[1],'text':row[2].strip(),'source_id':document.id,'path':document.path,
                    'line':line_number,'heading':heading,'quote':line.strip(),
                    'priority':next(iter(re.findall(r'\bP[012]\b',line)),'P1')})
        flush()
    return {'schema_version':'1','sections':sections,'requirements':requirements,
            'limitations':['Inventory ghi nhận yêu cầu tường minh trong tài liệu; không chứng minh behavior/branch coverage. Tài liệu không có ID được đọc theo sections, không tự suy diễn IDs nghiệp vụ.']}


def inventory_requirements(inventory):
    values={}
    for row in inventory['requirements']:
        if row['id'] in values:
            old=values[row['id']]
            if row['text'] not in old.acceptance_criteria:
                old.clarity='AMBIGUOUS';old.acceptance_criteria.append(row['text'])
                old.evidence.append(Evidence(source_id=row['source_id'],quote=row['quote']))
            continue
        values[row['id']]=RequirementNode(id=row['id'],title=row['text'],acceptance_criteria=[row['text']],
            category='non_functional' if row['id'].startswith('NFR') else 'business_rule' if row['id'].startswith('BR') else 'functional',priority=row['priority'],
            expectation_basis='specification',
            clarity='INCOMPLETE' if re.search(r'đề xuất|chưa chốt|còn M\b|undecided',row['text'],re.I) else 'CONFIRMED',
            evidence=[Evidence(source_id=row['source_id'],quote=row['quote'])])
    return list(values.values())


def module_documents(inventory,scope,max_chars=9000):
    tokens=set(re.findall(r'[\w]+',(scope.id+' '+scope.title+' '+scope.objective+' '+' '.join(scope.entry_paths)).lower()))
    tokens-={'the','and','api','app','apps','src','server','web','ui','của','cho','và','các','theo','với','để','trong','luồng','soát','rã','rà','py','ts','tsx'}
    documents=[set(re.findall(r'[\w]+',(section['heading']+' '+section['content']).lower())) for section in inventory['sections']]
    weights={word:math.log(1+len(documents)/(1+sum(word in document for document in documents))) for word in tokens}
    def relevance(pair):
        index,section=pair;heading=set(re.findall(r'[\w]+',section['heading'].lower()))
        return sum(weights[word] for word in tokens&documents[index])+3*sum(weights[word] for word in tokens&heading)
    ranked=sorted(enumerate(inventory['sections']),key=lambda pair:(-relevance(pair),pair[0]))
    selected=[];remaining=max_chars
    for _,section in ranked[:4]:
        if remaining<=0:break
        text=section['content'][:remaining];remaining-=len(text)
        selected.append({**section,'content':text,'truncated':len(text)<len(section['content'])})
    return selected


def module_requirements(inventory,scope):
    """Select from indexed sections; retain omitted IDs for subsequent retrieval."""
    sections=module_documents(inventory,scope)
    keys={(s['source_id'],s['heading']) for s in sections}
    return [r for r in inventory.get('requirements',[]) if (r['source_id'],r['heading']) in keys]


def document_traceability(inventory,workspace):
    catalog={r.id for r in workspace.knowledge.requirements}
    studied={r['id'] for m in workspace.flow_modules for r in (m.get('study') or {}).get('requirements',[])}
    return [{'id':r['id'],'path':r['path'],'line':r['line'],'heading':r['heading'],
             'cataloged':r['id'] in catalog,'analyzed':r['id'] in studied,
             'case_ids':[c.id for c in workspace.cases if c.status!='retired' and r['id'] in c.requirement_ids]}
            for r in inventory.get('requirements',[])]
