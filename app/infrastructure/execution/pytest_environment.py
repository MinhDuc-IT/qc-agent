"""A fresh checkout copy and per-run containers; no production env or DB is reused."""
import os
import base64
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from .contracts import Invocation
from .backends import ExecutionOutput
from .runtime import RegisteredWorkerRuntime
from ...domain.models import WorkerTask,TaskTarget,ProjectDescriptor,WorkerResult
from ..test_design.execution_profiles import safe_file


def _command(argv,cwd=None,timeout=120,environment=None):
    result=subprocess.run(argv,cwd=cwd,env=environment,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,shell=False,timeout=timeout,check=False)
    return ExecutionOutput(result.returncode,result.stdout.decode('utf-8',errors='replace'))


class ProfileBackend:
    def __init__(self,checkout,profile,isolation):self.checkout=checkout;self.profile=profile;self.isolation=isolation

    def run(self,invocation,timeout_seconds,environment):
        profile=self.profile
        if profile.blockers:raise ValueError('; '.join(profile.blockers))
        secret=secrets.token_urlsafe(32)
        test_env={'SECRET_KEY':secret,'PROJECT_NAME':'QC isolated test','FIRST_SUPERUSER':'qc-admin@example.com',
            'FIRST_SUPERUSER_PASSWORD':secret,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':'.'}
        replacements={'${QC_SECRET}':secret,'${QC_FERNET_KEY}':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
        test_env.update({k:replacements.get(v,v) for k,v in profile.test_environment.items()})
        if self.isolation=='local':
            if profile.services:raise ValueError('Profile services require Docker isolation')
            version=f'{sys.version_info.major}.{sys.version_info.minor}'
            if profile.python_version and tuple(map(int,version.split('.')))<tuple(map(int,profile.python_version.split('.'))):raise ValueError('Python runtime is older than execution profile')
            inherited={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME','USERPROFILE'}}
            return _command([sys.executable,*invocation.argv[1:]],invocation.cwd,timeout_seconds,{**inherited,**test_env,**environment})
        if self.isolation!='docker' or not shutil.which('docker'):raise ValueError('Docker executable is required for generated tests')
        version=profile.python_version or '3.12'
        if not re.fullmatch(r'3\.\d+',version):raise ValueError('Unsupported Python version')
        suffix=secrets.token_hex(8);image='qc-pytest:'+suffix;base_image='qc-pytest-base:'+suffix;container='qc-pytest-'+suffix;network='qc-pytest-net-'+suffix;database='qc-pytest-db-'+suffix;smtp='qc-pytest-smtp-'+suffix
        def docker(*args,timeout=120):
            value=_command(['docker',*args],timeout=timeout)
            if value.exit_code:raise ValueError('Docker environment failed: '+value.output[-1500:])
            return value
        try:
            with tempfile.TemporaryDirectory(prefix='qc-pytest-build-') as build:
                build=Path(build);shutil.copytree(self.checkout,build/'source')
                root=profile.root
                cwd=safe_file(self.checkout,root)
                container_root=profile.container_root
                if profile.dockerfile:
                    original=safe_file(build/'source',profile.dockerfile)
                    docker('build','-f',str(original),'-t',base_image,str(build/'source'),timeout=300)
                    dockerfile=f'FROM {base_image}\nCOPY source {container_root}\nWORKDIR {container_root}/{root}\nRUN python -m ensurepip && python -m pip install --no-cache-dir pytest\n'
                    uv=False
                else:
                    dockerfile=f'FROM python:{version}-slim\nRUN pip install --no-cache-dir pytest uv\nCOPY source {container_root}\nWORKDIR {container_root}/{root}\n'
                    uv=(cwd/'uv.lock').is_file() and (cwd/'pyproject.toml').is_file()
                    if uv:dockerfile+='RUN uv sync --locked --group dev\n'
                    elif (cwd/'pyproject.toml').is_file():
                        import tomllib
                        metadata=tomllib.loads((cwd/'pyproject.toml').read_text(encoding='utf-8'))
                        extra='.[dev]' if 'dev' in metadata.get('project',{}).get('optional-dependencies',{}) else '.'
                        dockerfile+='RUN pip install --no-cache-dir '+extra+'\n'
                    elif (cwd/'requirements.txt').is_file():dockerfile+='RUN pip install --no-cache-dir -r requirements.txt\n'
                    if (cwd/'requirements-dev.txt').is_file():dockerfile+='RUN pip install --no-cache-dir -r requirements-dev.txt\n'
                (build/'Dockerfile').write_text(dockerfile,encoding='utf-8')
                docker('build','-t',image,str(build),timeout=300)
            docker('network','create','--internal',network)
            if 'smtp' in profile.services:
                docker('run','-d','--name',smtp,'--network',network,'--network-alias','smtp','--network-alias','smtp.example.com',
                    '--cap-drop=ALL','-e','MP_SMTP_AUTH_ACCEPT_ANY=1','-e','MP_SMTP_AUTH_ALLOW_INSECURE=1','axllent/mailpit:v1.27.8')
            if 'postgres' in profile.services:
                docker('run','-d','--name',database,'--user','postgres','--network',network,'--network-alias','postgres','--cap-drop=ALL',
                    '-e','POSTGRES_USER=qc','-e','POSTGRES_PASSWORD='+secret,'-e','POSTGRES_DB=qc_test','postgres:17-alpine')
                for _ in range(40):
                    if _command(['docker','exec',database,'pg_isready','-U','qc','-d','qc_test'],timeout=5).exit_code==0:break
                    time.sleep(.5)
                else:raise ValueError('PostgreSQL test service did not become ready')
                dependencies='\n'.join(safe_file(self.checkout,p).read_text(encoding='utf-8') for p in profile.dependency_files)
                driver='asyncpg' if 'asyncpg' in dependencies and 'psycopg' not in dependencies else 'psycopg'
                test_env['DATABASE_URL']='postgresql+'+driver+'://qc:'+secret+'@postgres:5432/qc_test'
            prefix=['uv','run','--no-sync'] if uv else []
            pytest=prefix+list(invocation.argv)
            commands=([prefix+['python','-m','alembic','upgrade','head']] if profile.migrations else [])+[pytest]
            bootstrap='import subprocess,sys,socket,time\n'
            if 'smtp' in profile.services:
                bootstrap+='for attempt in range(60):\n try:\n  socket.create_connection(("smtp",1025),timeout=1).close(); break\n except OSError:\n  if attempt==59: raise RuntimeError("Isolated SMTP service did not become ready")\n  time.sleep(.5)\n'
            bootstrap+='commands='+repr(commands)+'\nfor command in commands:\n result=subprocess.run(command)\n if result.returncode: sys.exit(result.returncode)\n'
            args=['run','--name',container,'--network',network,'--cap-drop=ALL','--security-opt','no-new-privileges','--cpus','2','--memory','2g','--pids-limit','256']
            for k,v in {**test_env,**environment}.items():args+=['-e',k+'='+v]
            result=_command(['docker',*args,image,'python','-c',bootstrap],timeout=timeout_seconds)
            report=safe_file(self.checkout,profile.root)/'.qc-generated-results.xml'
            _command(['docker','cp',container+':'+container_root+'/'+profile.root+'/.qc-generated-results.xml',str(report)],timeout=15)
            return result
        finally:
            for command in [('rm','-fv',container),('rm','-fv',database),('rm','-fv',smtp),('network','rm',network),('image','rm','-f',image),('image','rm','-f',base_image)]:
                try:_command(['docker',*command],timeout=20)
                except (OSError,subprocess.TimeoutExpired):pass


def run_artifacts(bindings,workspace,settings,run_id='preflight'):
    if not bindings:raise ValueError('No generated artifacts')
    if len(bindings)>1:
        # Repo fixtures may be session-scoped and commit transactions. A fresh
        # database/container per artifact isolates cases without modifying them.
        runs=[run_artifacts([binding],workspace,settings,run_id) for binding in bindings]
        outcomes=[]
        for binding,result in zip(bindings,runs):
            rows=result.summary.get('results') or [{'case_id':binding.case_id,'status':'blocked'}]
            for row in rows:outcomes.append({**row,'output':result.output[-6000:]})
        blocked=any(row['status']=='blocked' for row in outcomes)
        return runs[0].model_copy(update={
            'execution_status':'failed' if blocked else 'completed',
            'verdict':'unknown' if blocked else 'fail' if any(row['status']=='defect' for row in outcomes) else 'pass',
            'summary':{'source':'generated','results':outcomes,'isolation':'per_case'},
            'output':'\n'.join(binding.case_id+'\n'+result.output[-6000:] for binding,result in zip(bindings,runs)),
        })
    profile=bindings[0].profile
    if any(b.profile!=profile for b in bindings):raise ValueError('A worker task must contain one execution profile')
    with tempfile.TemporaryDirectory(prefix='qc-generated-') as temporary:
        checkout=Path(temporary)/'source'
        def ignore(directory,names):return [n for n in names if n in {'.git','.venv','venv','node_modules','.data','.tools','.aws','.codex','.agents','__pycache__','.qc-generated-results.xml'} or n.startswith(('.env','.test-')) or Path(n).suffix in ('.pem','.key') or (Path(directory)/n).is_symlink()]
        shutil.copytree(workspace,checkout,ignore=ignore)
        selectors=[];cases=[]
        for binding in bindings:
            file=safe_file(checkout,str(Path(profile.root)/binding.artifact.path))
            if file.exists():raise ValueError('PR checkout collides with generated artifact path')
            file.parent.mkdir(parents=True,exist_ok=True);file.write_text(binding.artifact.content,encoding='utf-8')
            selectors.append(binding.test_selector);cases.append({'case_id':binding.case_id,'path':binding.artifact.path})
        task=WorkerTask(run_id=run_id,capability='unit.run',objective='Run approved generated tests',target=TaskTarget(type='repository',project_id='generated'),
            scope={'project_root':profile.root,'paths':selectors},parameters={'generated_cases':cases},timeout_seconds=300,execution_preference='tool')
        runtime=RegisteredWorkerRuntime(backend=ProfileBackend(checkout,profile,settings.agent_qc_test_isolation))
        return runtime.execute(task,ProjectDescriptor(id='generated',language='python',root=profile.root,build_system='pytest'),checkout)
