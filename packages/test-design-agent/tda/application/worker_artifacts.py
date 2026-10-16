"""Static checks are a gate, never a Python security sandbox or semantic proof."""
import ast
import hashlib
import re
from pathlib import PurePosixPath
from .workspace import design_digest


def artifact_path(case_id):return 'tests/.qc_generated/test_'+hashlib.sha256(case_id.encode()).hexdigest()[:20]+'.py'


def normalize_test_name(code):
    """Normalize the reserved entry point without changing test behavior or line numbers."""
    try:tree=ast.parse(code)
    except SyntaxError:return code
    tests=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')]
    if len(tests)!=1:return code
    test=tests[0]
    if test.name=='test_case' or any(isinstance(n,ast.Name) and n.id in (test.name,'test_case') for n in ast.walk(tree)):return code
    if any(getattr(n,'name',None)=='test_case' for n in tree.body):return code
    lines=code.splitlines(keepends=True)
    lines[test.lineno-1]=re.sub(r'(\bdef\s+)'+re.escape(test.name)+r'(?=\s*\()',r'\1test_case',lines[test.lineno-1],count=1)
    return ''.join(lines)


def expected_catalog(case):
    return [{'key':f'step:{i}/expected:{j}','design_step':i,'expected_result':j,
             'description':expected.description}
            for i,step in enumerate(case.steps) for j,expected in enumerate(step.expected_results)]


def resolve_mapping(case,code,mappings):
    """Resolve explicit stable keys and exact assertion text; never guess index offsets."""
    lookup={row['key']:(row['design_step'],row['expected_result']) for row in expected_catalog(case)}
    try:tree=ast.parse(code)
    except SyntaxError:return mappings
    assertions={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Assert) or (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='raises'):
            text=ast.get_source_segment(code,node)
            if text:assertions.setdefault(text.strip(),[]).append(node.lineno)
    resolved=[]
    for original in mappings:
        from ..domain.worker_artifacts import ArtifactMapping
        item=original.model_copy(deep=True) if isinstance(original,ArtifactMapping) else ArtifactMapping(
            expected_key=original.expected_key,assertion_sources=original.assertion_sources,
            design_step=0,expected_result=0,assertion_lines=[1],explanation=original.explanation)
        if item.expected_key is not None:
            if item.expected_key not in lookup:raise ValueError('Mapping có expected_key không thuộc case đã duyệt.')
            item.design_step,item.expected_result=lookup[item.expected_key]
        if item.assertion_sources:
            lines=[]
            for source in item.assertion_sources:
                matches=assertions.get(source.strip(),[])
                if not matches:
                    # Whitespace/quote formatting can differ in review output. Require
                    # the exact same AST and a unique assertion; never relax the oracle.
                    try:
                        target=ast.dump(ast.parse(source.strip()),include_attributes=False)
                        matches=[line for text,locations in assertions.items()
                                 if ast.dump(ast.parse(text),include_attributes=False)==target for line in locations]
                    except SyntaxError:pass
                if len(matches)!=1:raise ValueError('Assertion source phải khớp duy nhất với code đã review.')
                lines.append(matches[0])
            item.assertion_lines=list(dict.fromkeys(lines))
        resolved.append(item)
    return resolved


def artifact_issues(case,binding):
    issues=[]
    if binding.case_id!=case.id or binding.case_revision!=case.revision or binding.design_sha256!=design_digest(case):issues.append('Artifact không khớp revision/hash của case.')
    if binding.artifact.path!=artifact_path(case.id) or binding.test_selector!=binding.artifact.path+'::test_case':issues.append('Artifact path/selector không đúng contract.')
    if binding.artifact.sha256!=hashlib.sha256(binding.artifact.content.encode()).hexdigest():issues.append('Artifact hash không khớp.')
    for value in [binding.profile.root,binding.profile.test_root,*binding.profile.fixture_files,*binding.profile.dependency_files,*([binding.profile.dockerfile] if binding.profile.dockerfile else [])]:
        path=PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or ':' in value or '\\' in value:issues.append('Execution profile path không an toàn.')
    if not binding.profile.container_root.startswith('/') or '..' in PurePosixPath(binding.profile.container_root).parts or not all(c.isalnum() or c in '/_-' for c in binding.profile.container_root):issues.append('Container working directory không hợp lệ.')
    try:tree=ast.parse(binding.artifact.content)
    except SyntaxError:return issues+['Python syntax không hợp lệ.']
    tests=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')]
    if len(tests)!=1 or tests[0].name!='test_case':issues.append('Mỗi artifact phải có đúng một test_case.')
    lines=set()
    test_nodes=list(ast.walk(tests[0])) if len(tests)==1 else []
    for node in ast.walk(tree):
        if isinstance(node,ast.Assert):
            if isinstance(node.test,ast.Constant):issues.append('Không chấp nhận assert hằng số thay cho kiểm chứng hành vi.')
            if node in test_nodes:lines.add(node.lineno)
        if isinstance(node,ast.Attribute) and node.attr in ('skip','skipif','xfail'):issues.append('Generated test không được tự skip/xfail.')
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
            if node.func.attr in ('skip','skipif','xfail','exit'):issues.append('Generated test không được tự skip/xfail/exit.')
            if node.func.attr=='raises' and node in test_nodes:lines.add(node.lineno)
    expected={(i,j) for i,s in enumerate(case.steps) for j,_ in enumerate(s.expected_results)}
    mapped=set()
    for mapping in binding.mapping:
        key=(mapping.design_step,mapping.expected_result)
        if key not in expected or not set(mapping.assertion_lines)<=lines or not mapping.explanation.strip():issues.append('Mapping phải trỏ tới assertion/raises thực tế và expected result hợp lệ.')
        else:mapped.add(key)
    if mapped!=expected:issues.append('Chưa kiểm chứng tất cả expected results.')
    return list(dict.fromkeys(issues))
