"""Collect bounded Python dependencies without importing repository code."""
import ast
from pathlib import PurePosixPath

BUILTIN_FIXTURES=set('request pytestconfig record_property record_testsuite_property record_xml_attribute capsys capsysbinary capfd capfdbinary caplog monkeypatch tmp_path tmp_path_factory tmpdir tmpdir_factory cache recwarn doctest_namespace'.split())


def preparation_context(tools,profile,paths):
    prefix='' if profile.root=='.' else profile.root+'/'
    stems={PurePosixPath(p).stem for p in paths}
    tests=sorted((p for p in tools.files if p.startswith(prefix+'tests/') and PurePosixPath(p).name.startswith('test_') and p.endswith('.py')),
                 key=lambda p:(PurePosixPath(p).stem.removeprefix('test_') not in stems,p))
    models=[p for p in tools.files if p.startswith(prefix) and PurePosixPath(p).name in ('models.py','factories.py')][:8]
    queue=list(dict.fromkeys(profile.fixture_files+models+sorted(paths)+tests[:3]+profile.evidence_paths))
    # Conventional models/factories complement imports and route excerpts.
    sources=[];fixtures=set(BUILTIN_FIXTURES);seen=set();remaining=48000
    shared=set(profile.fixture_files)
    while queue and len(seen)<32:
        path=queue.pop(0)
        if path in seen or not tools.allowed(path):continue
        seen.add(path);content=tools.snapshot(path).content
        # Include whole relevant files when small; large files are explicitly bounded.
        chunks=[tools.read_file(path,start,start+119) for start in range(1,min(len(content.splitlines()),480)+1,120)]
        excerpt=''.join(chunk['content'] for chunk in chunks)[:min(12000,remaining)]
        if excerpt:
            sources.append({'path':path,'source_id':chunks[0]['source_id'],'excerpts':[excerpt],'truncated':len(excerpt)<len(content)})
            remaining-=len(excerpt)
        try:tree=ast.parse(content)
        except SyntaxError:continue
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    target=decorator.func if isinstance(decorator,ast.Call) else decorator
                    if path in shared and getattr(target,'attr',getattr(target,'id',''))=='fixture':
                        name=node.name
                        if isinstance(decorator,ast.Call):
                            for keyword in decorator.keywords:
                                if keyword.arg=='name' and isinstance(keyword.value,ast.Constant):name=keyword.value.value
                        fixtures.add(name)
            if isinstance(node,ast.ImportFrom) and node.module and not node.level:
                base=prefix+node.module.replace('.','/')
                for candidate in [base+'.py',base+'/__init__.py',*[base+'/'+alias.name+'.py' for alias in node.names]]:
                    if candidate in tools.files:
                        queue.append(candidate)
                        if path in shared:shared.add(candidate)
    return sources,sorted(fixtures)


def script_setup_issues(code,fixtures):
    try:tree=ast.parse(code)
    except SyntaxError:return ['Python syntax không hợp lệ.']
    local=set(fixtures)
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            if any(getattr(d.func if isinstance(d,ast.Call) else d,'attr',getattr(d,'id',''))=='fixture' for d in node.decorator_list):local.add(node.name)
    issues=[]
    for test in [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='test_case']:
        arguments=test.args.posonlyargs+test.args.args
        required=arguments[:len(arguments)-len(test.args.defaults)]
        required += [arg for arg,value in zip(test.args.kwonlyargs,test.args.kw_defaults) if value is None]
        for arg in required:
            if arg.arg not in local:issues.append('Fixture không tồn tại trong context repo: '+arg.arg)
        params={a.arg for a in required}
        literal_login=any(isinstance(n,ast.Constant) and isinstance(n.value,str) and '/login/access-token' in n.value for n in ast.walk(test))
        credentials=any(isinstance(n,ast.Dict) and any(isinstance(k,ast.Constant) and k.value=='username' and isinstance(v,ast.Constant) for k,v in zip(n.keys,n.values)) for n in ast.walk(test))
        creates_user=any(isinstance(n,ast.Call) and getattr(n.func,'attr',getattr(n.func,'id','')) in ('create_user','authentication_token_from_email','get_superuser_token_headers') for n in ast.walk(test))
        if literal_login and credentials and not creates_user and not any('token' in p or p=='db' for p in params):
            issues.append('Login dùng tài khoản cố định nhưng chưa có fixture/factory tạo tài khoản sandbox.')
    return issues
