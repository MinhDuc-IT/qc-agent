"""Read-only static scanner. Never imports or starts project code."""
import ast
import hashlib
import os
import re
from collections import Counter
from pathlib import Path
from ..domain.inventory import InventoryItem,ProjectInventory
from ..domain.contracts import SourceDocument,SourceBundle

EXCLUDED={'.git','.venv','venv','node_modules','__pycache__','.data','dist','build','.aws','.codex','.agents','.tda-input'}
EXTENSIONS={'.py','.js','.jsx','.ts','.tsx'}


def identity(path,kind,symbol):
    return 'INV-'+hashlib.sha256((path+'|'+kind+'|'+symbol).encode()).hexdigest()[:20]


def scan_texts(texts,completeness='selected_sources',unknown=None,exclusions=None):
    result=ProjectInventory(completeness=completeness,unknown=unknown or [],exclusions=exclusions or [])
    route_maps={}
    for path,text in texts.items():
        if not path.endswith('routeTree.gen.ts'):continue
        prefix=path.split('src/',1)[0]
        for match in re.finditer(r'''(['"])(/[^'"]*)\1\s*:\s*\{([^}]+)\}''',text,re.S):
            full=re.search(r'''fullPath\s*:\s*(['"])(.*?)\1''',match.group(3))
            if full:route_maps[(prefix,match.group(2))]=full.group(2)
    api_prefix=''
    for path,text in texts.items():
        match=re.search(r'API_V1_STR\s*(?::[^=\n]+)?=\s*["\']([^"\']+)',text)
        if match:api_prefix=match.group(1);break
    for path,text in sorted(texts.items()):
        result.files_scanned+=1
        if path.endswith('routeTree.gen.ts'):continue  # Routing metadata, not generated business behavior.
        lines=text.splitlines();digest=hashlib.sha256(text.encode()).hexdigest()
        tests='tests' in Path(path).parts or Path(path).name.startswith('test_') or '.spec.' in path or '.test.' in path
        counters=Counter()
        def add(kind,symbol,line,end_line=None,excerpt=None,**kwargs):
            counters[(kind,symbol)]+=1
            key=symbol if counters[(kind,symbol)]==1 else symbol+'#'+str(counters[(kind,symbol)])
            item=InventoryItem(id=identity(path,kind,key),kind=kind,source_path=path,source_sha256=digest,
                symbol=symbol,line=line,end_line=end_line or line,excerpt=excerpt or (lines[line-1][:800] if lines else ''),**kwargs)
            result.items.append(item);return item
        if path.endswith('.py'):
            # Python 3.14 PEP 758 changes only this header; preserve original lines for evidence.
            normalized=re.sub(r'(?m)^(\s*except )([^:\n]+,[^:\n]+)(:)',r'\1(\2)\3',text)
            try:tree=ast.parse(normalized)
            except SyntaxError:
                result.unknown.append({'source_path':path,'reason':'Parser Python hiện tại chưa hiểu file; không kết luận source sai.'})
                for match in re.finditer(r'(?m)^\s*(?:async\s+)?def\s+(\w+)\s*\(',text):
                    add('existing_test' if tests else 'logic',match.group(1),text[:match.start()].count('\n')+1,extractor='lexical')
                continue
            prefixes={}
            for node in ast.walk(tree):
                if isinstance(node,(ast.Assign,ast.AnnAssign)) and isinstance(node.value,ast.Call):
                    name=ast.unparse(node.value.func)
                    if name.endswith(('APIRouter','FastAPI','Flask')):
                        prefix=next((kw.value.value for kw in node.value.keywords if kw.arg=='prefix' and isinstance(kw.value,ast.Constant) and isinstance(kw.value.value,str)),'')
                        targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                        for target in targets:
                            if isinstance(target,ast.Name):prefixes[target.id]=prefix
            def visit_functions(node,qualifier=''):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child,ast.ClassDef):visit_functions(child,qualifier+child.name+'.')
                    elif isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef)):
                        name=qualifier+child.name;routes=[]
                        for decorator in child.decorator_list:
                            if not isinstance(decorator,ast.Call) or not isinstance(decorator.func,ast.Attribute):continue
                            method=decorator.func.attr.lower()
                            if method not in {'get','post','put','patch','delete','head','options','route','api_route'}:continue
                            if not decorator.args or not isinstance(decorator.args[0],ast.Constant) or not isinstance(decorator.args[0].value,str):
                                result.unknown.append({'source_path':path,'reason':'Route động: '+name});continue
                            router=ast.unparse(decorator.func.value)
                            methods=[method.upper()] if method not in ('route','api_route') else ['DYNAMIC']
                            for kw in decorator.keywords:
                                if kw.arg=='methods' and isinstance(kw.value,(ast.List,ast.Tuple)):
                                    methods=[value.value.upper() for value in kw.value.elts if isinstance(value,ast.Constant) and isinstance(value.value,str)]
                            for verb in methods:
                                route=prefixes.get(router,'')+decorator.args[0].value
                                if router!='app':route=api_prefix+route
                                routes.append(add('api',name+':'+verb,decorator.lineno,child.end_lineno,
                                    method=verb,route=route,excerpt=lines[decorator.lineno-1][:800],conditional='private' in Path(path).stem))
                        parent=routes[0] if routes else add('existing_test' if tests else 'logic',name,child.lineno,child.end_lineno)
                        if not tests:
                            counts=Counter()
                            def visit_branches(current):
                                for branch in ast.iter_child_nodes(current):
                                    if isinstance(branch,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Lambda)):continue
                                    if isinstance(branch,ast.If):
                                        condition=ast.get_source_segment(text,branch.test) or ast.unparse(branch.test)
                                        counts[condition]+=1
                                        for outcome in ('true','false'):
                                            start=branch.lineno if outcome=='true' else branch.orelse[0].lineno if branch.orelse else branch.end_lineno+1
                                            end=branch.body[-1].end_lineno if outcome=='true' else branch.orelse[-1].end_lineno if branch.orelse else child.end_lineno
                                            add('branch',name+':'+condition+':'+str(counts[condition])+':'+outcome,start,max(start,end),
                                                parent_id=parent.id,outcome=outcome,excerpt=lines[branch.lineno-1][:800])
                                    elif isinstance(branch,ast.ExceptHandler):
                                        label=ast.unparse(branch.type) if branch.type else 'exception'
                                        add('branch',name+':except:'+label,branch.lineno,branch.end_lineno,parent_id=parent.id,outcome='exception',excerpt=lines[branch.lineno-1][:800])
                                    visit_branches(branch)
                            visit_branches(child)
                        visit_functions(child,name+'.')
                    else:visit_functions(child,qualifier)
            visit_functions(tree)
        else:
            if tests:
                for match in re.finditer(r'\b(?:test|it)(?:\.\w+)?\s*\(\s*(["\'])(.*?)\1',text):
                    add('existing_test',match.group(2),text[:match.start()].count('\n')+1,extractor='lexical')
                continue
            routes=[]
            for match in re.finditer(r'create(?:File)?Route\s*\(\s*(["\'])(.*?)\1',text):
                routes.append(add('ui_route',match.group(2),text[:match.start()].count('\n')+1,len(lines),route=route_maps.get((path.split('src/',1)[0],match.group(2)),match.group(2)),extractor='lexical'))
            for match in re.finditer(r'(?m)(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(',text):
                if not routes:add('logic',match.group(1),text[:match.start()].count('\n')+1,len(lines),extractor='lexical')
            if routes:
                for match in re.finditer(r'\bif\s*\(([^\n]+)\)',text):
                    line=text[:match.start()].count('\n')+1
                    for outcome in ('true','false'):
                        add('branch',routes[0].symbol+':'+match.group(1)+':'+outcome,line,parent_id=routes[0].id,outcome=outcome,extractor='lexical')
    result.limitations=['Inventory là static analysis; branch mapping không phải runtime code coverage.',
        'JS/TS dùng lexical extraction; closures, JSX conditions, dynamic routes và router prefixes động có thể chưa được phân giải.',
        'API prefix lấy giá trị mặc định trong source; nested include_router và cấu hình runtime cần review.']
    return result


def from_sources(sources):
    texts={doc.path:doc.content for doc in sources.documents if doc.path.endswith(tuple(EXTENSIONS)) and not doc.path.startswith(('history://','workspace://'))}
    return scan_texts(texts)


def build_inventory(root,max_files=3000,max_file_bytes=1000000):
    root=Path(root).resolve();texts={};unknown=[];exclusions=[]
    for directory,folders,files in os.walk(root,followlinks=False):
        folders[:]=sorted(folder for folder in folders if folder not in EXCLUDED and not folder.startswith('.test-') and not (Path(directory)/folder).is_symlink())
        for filename in sorted(files):
            path=Path(directory)/filename
            if filename.startswith('.env'):continue
            if path.suffix in {'.java','.go','.rs','.cs','.rb','.php','.swift','.kt','.vue','.svelte'}:
                unknown.append({'source_path':path.relative_to(root).as_posix(),'reason':'Chưa có static extractor cho ngôn ngữ/framework này.'});continue
            if path.suffix not in EXTENSIONS:continue
            relative=path.relative_to(root).as_posix()
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                unknown.append({'source_path':relative,'reason':'Symlink không được đọc.'});continue
            if ('.gen.' in filename and filename!='routeTree.gen.ts') or '/client/' in relative or '/components/ui/' in relative:
                exclusions.append({'source_path':relative,'reason':'Generated client hoặc UI primitive; không coi là behavior nghiệp vụ.'});continue
            if len(texts)>=max_files or path.stat().st_size>max_file_bytes:
                unknown.append({'source_path':relative,'reason':'Vượt giới hạn static scan.'});continue
            try:texts[relative]=path.read_bytes().decode('utf-8-sig')
            except (OSError,UnicodeError):unknown.append({'source_path':relative,'reason':'Không đọc được source UTF-8.'})
    return scan_texts(texts,'repository_scan',unknown,exclusions)


def enrich_sources(root,sources,inventory,max_snapshot_bytes=500000,max_documents=300):
    """Capture omitted behavior files; per-call context selection remains bounded."""
    root=Path(root).resolve();sources=sources.model_copy(deep=True)
    known={doc.path for doc in sources.documents};used=sum(len(doc.content.encode()) for doc in sources.documents)
    paths=sorted({item.source_path for item in inventory.items}-known)
    for path in paths:
        file=(root/path).resolve()
        if not file.is_relative_to(root) or file.is_symlink() or not file.is_file():continue
        raw=file.read_bytes()
        if used+len(raw)>max_snapshot_bytes or len(sources.documents)>=max_documents:
            inventory.unknown.append({'source_path':path,'reason':'Vượt budget source snapshot; file chưa được gửi tới LLM.'});continue
        try:content=raw.decode('utf-8-sig')
        except UnicodeError:continue
        sources.documents.append(SourceDocument(id='inventory-source-'+hashlib.sha256(path.encode()).hexdigest()[:20],
            kind='existing_tests' if any(item.source_path==path and item.kind=='existing_test' for item in inventory.items) else 'code',
            path=path,sha256=hashlib.sha256(raw).hexdigest(),content=content));used+=len(raw)
    return sources


def context_sources(sources,paths=None,max_bytes=160000):
    paths=set(paths or []);chosen=[];used=0;omitted=[]
    ordered=sorted(enumerate(sources.documents),key=lambda pair:(0 if pair[1].kind in ('prd','srs','openapi') else 1 if pair[1].path in paths else 2,pair[0]))
    for _,doc in ordered:
        size=len(doc.content.encode())
        if used+size>max_bytes:omitted.append(doc.path);continue
        chosen.append(doc.model_dump());used+=size
    ids={doc['id'] for doc in chosen}
    return {'documents':chosen,'operations':[op.model_dump() for op in sources.operations if op.source_id in ids],
            'omitted_source_paths':omitted,'context_budget_bytes':max_bytes}
