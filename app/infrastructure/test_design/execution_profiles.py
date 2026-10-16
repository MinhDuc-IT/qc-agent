"""Bounded, read-only profile discovery; never executes project imports."""
import ast
import re
import tomllib
import yaml
from pathlib import Path
from tda.domain.worker_artifacts import ExecutionProfile


def safe_file(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or any(p.is_symlink() for p in (root/relative).parents if p!=root.parent) or (root/relative).is_symlink():
        raise ValueError('Profile path escapes checkout or contains symlink')
    return path


def pytest_case_ids(workspace,case_ids,profiles):
    """Route only Python behavior to pytest; keep UI/load/AI cases on their own runners."""
    documents={d.id:d for d in workspace.sources.documents}
    chosen=[]
    for case in workspace.cases:
        if case.id not in case_ids or case.kind in ('performance','ai_evaluation'):continue
        paths={documents[e.source_id].path for step in case.steps for expected in step.expected_results
               for e in expected.evidence if e.source_id in documents and documents[e.source_id].kind in ('code','existing_tests')}
        entries={p for module in workspace.flow_modules if case.id in module['case_ids'] for p in module['scope']['entry_paths']}
        if not paths:
            paths=entries
        # Module entry paths can cover both API and UI; case evidence is more specific.
        if any(Path(p).suffix in ('.tsx','.jsx','.vue','.html','.js','.ts','.svelte') for p in paths):continue
        if any(p.endswith('.py') and any(profile.root=='.' or p.startswith(profile.root.rstrip('/')+'/') for profile in profiles) for p in paths):chosen.append(case.id)
    return chosen


def discover_profiles(tools):
    if '.qc/execution.yaml' in tools.files:
        data=yaml.safe_load(tools.snapshot('.qc/execution.yaml').content)
        if not isinstance(data,dict) or data.get('schema_version')!='1.0' or not isinstance(data.get('profiles'),list):raise ValueError('Execution profile manifest không hợp lệ.')
        profiles=[ExecutionProfile.model_validate(p) for p in data['profiles']]
        for profile in profiles:
            for value in [profile.root,*profile.dependency_files,*profile.fixture_files]:safe_file(tools.root,value)
            profile.evidence_paths=list(dict.fromkeys(profile.evidence_paths+['.qc/execution.yaml']))
        return profiles
    profiles=[]
    manifests=[p for p in tools.files if Path(p).name in ('pyproject.toml','pytest.ini','requirements.txt')]
    roots=sorted({str(Path(p).parent).replace('\\','/') for p in manifests})
    for relative in roots:
        prefix='' if relative=='.' else relative+'/'
        dependencies=[p for p in manifests if str(Path(p).parent).replace('\\','/')==relative]
        fixture_files=[p for p in tools.files if (p.startswith(prefix+'tests/') or p==prefix+'conftest.py') and Path(p).name=='conftest.py']
        if not fixture_files and not any(p.startswith(prefix+'tests/') and p.endswith('.py') for p in tools.files):continue
        profile=ExecutionProfile(root=relative,dependency_files=dependencies,fixture_files=fixture_files[:15],evidence_paths=dependencies+fixture_files[:15])
        content=''
        for path in dependencies:
            document=tools.snapshot(path);content+='\n'+document.content
            if path.endswith('pyproject.toml'):
                try:
                    spec=tomllib.loads(document.content).get('project',{}).get('requires-python','')
                    version=re.search(r'(?:>=|==|~=)\s*(3\.\d+)',spec)
                    if version:profile.python_version=version[1]
                except tomllib.TOMLDecodeError:profile.blockers.append('pyproject.toml không hợp lệ.')
        if not profile.python_version:profile.python_version='3.12'
        config_paths=[p for p in tools.files if p.startswith(prefix) and Path(p).name in ('config.py','settings.py')][:10]
        for path in config_paths:
            try:tree=ast.parse(tools.snapshot(path).content)
            except SyntaxError:continue
            for node in ast.walk(tree):
                if isinstance(node,ast.ClassDef) and any((isinstance(b,ast.Name) and b.id=='BaseSettings') for b in node.bases):
                    profile.evidence_paths.append(path)
                    fields={n.target.id:n for n in node.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name)}
                    if 'FASTAPI_ENV' in fields and 'development' in ast.unparse(fields['FASTAPI_ENV'].annotation):profile.test_environment['FASTAPI_ENV']='development'
                    if 'SMTP_HOST' in fields:
                        profile.services.append('smtp')
                        for key,value in {'SMTP_HOST':'smtp','SMTP_PORT':'1025','SMTP_TLS':'false','SMTP_SSL':'false','EMAILS_FROM_EMAIL':'qc-test@example.com'}.items():
                            if key in fields:profile.test_environment[key]=value
                    profile.required_environment.extend(n.target.id for n in node.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id.isupper() and n.value is None)
        fixture_content='\n'.join(tools.snapshot(p).content for p in fixture_files[:15])
        if any(driver in content for driver in ('psycopg','asyncpg')) and ('engine' in fixture_content or 'DATABASE_URL' in fixture_content):profile.services.append('postgres')
        # Read configuration contracts as data, including dataclass-based settings.
        if 'postgres' in profile.services:
            for path in config_paths:
                text=tools.snapshot(path).content
                profile.evidence_paths.append(path)
                keys=re.findall(r'os\.getenv\(\s*[\'\"]([A-Z][A-Z0-9_]*)[\'\"]',text)
                keys+=re.findall(r'auth_value\(\s*[\'\"]([A-Z][A-Z0-9_]*)[\'\"]',text)
                for key in keys:
                    if key.endswith('_ENVIRONMENT'):profile.test_environment[key]='test'
                    elif key.endswith('_TOKEN_SECRET'):profile.test_environment[key]='${QC_SECRET}'
                    elif key.endswith('_BROWSER_STATE_KEY'):profile.test_environment[key]='${QC_FERNET_KEY}'
            # Preserve explicit repository safeguards for clearing a dedicated test DB.
            if 'require_test_database' in fixture_content:
                guards=[p for p in tools.files if p.startswith(prefix) and p.endswith('/postgres.py')]
                for path in guards[:4]:
                    text=tools.snapshot(path).content
                    for key in re.findall(r'os\.environ\.get\([\'\"]([A-Z_]+)[\'\"]\)\s*!=\s*[\'\"]test-only[\'\"]',text):
                        profile.test_environment[key]='test-only';profile.evidence_paths.append(path)
        profile.services=sorted(set(profile.services))
        profile.migrations=prefix+'alembic.ini' in tools.files
        if profile.migrations:profile.evidence_paths.append(prefix+'alembic.ini')
        dockerfile=prefix+'Dockerfile'
        if dockerfile in tools.files:
            text=tools.snapshot(dockerfile).content
            workdirs=re.findall(r'(?im)^WORKDIR\s+(/[^\s$]+)\s*$',text)
            if workdirs:
                workdir=workdirs[-1].rstrip('/')
                suffix='' if relative=='.' else '/'+relative
                if not suffix or workdir.endswith(suffix):
                    profile.dockerfile=dockerfile
                    profile.container_root=workdir[:-len(suffix)] if suffix else workdir
                    profile.evidence_paths.append(dockerfile)
        supported={'SECRET_KEY','DATABASE_URL','PROJECT_NAME','FIRST_SUPERUSER','FIRST_SUPERUSER_PASSWORD'}
        unknown=set(profile.required_environment)-supported
        if unknown:profile.blockers.append('Chưa có chiến lược tạo test environment cho: '+', '.join(sorted(unknown)))
        profile.required_environment=sorted(set(profile.required_environment))
        profiles.append(profile)
    return profiles
