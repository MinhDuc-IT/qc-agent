import os
import stat
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.domain.models import WorkerTask,TaskTarget,ProjectDescriptor
from app.infrastructure.execution.runtime import RegisteredWorkerRuntime
from app.infrastructure.execution.backends import DockerExecutionBackend,ExecutionOutput
from app.infrastructure.execution.prepared_tools import PreparedToolRuntime
from app.infrastructure.settings import Settings
from app.infrastructure.source.git import GitRepositoryManager


def task(capability='unit.run',root='.'):
    return WorkerTask(run_id='prepared',capability=capability,objective='Check',target=TaskTarget(project_id='python'),scope={'project_root':root})


def test_security_container_does_not_require_host_binary(tmp_path,monkeypatch):
    import subprocess
    monkeypatch.setattr('shutil.which',lambda name:'docker' if name=='docker' else None)
    captured=[]
    def run(argv,**kwargs):
        captured.append(argv);return SimpleNamespace(returncode=0,stdout=b'{"results":[],"errors":[]}\nScan completed successfully.\n')
    monkeypatch.setattr(subprocess,'run',run)
    result=RegisteredWorkerRuntime(backend=DockerExecutionBackend({'semgrep':'semgrep@sha256:pinned'})).execute(task('security.sast'),ProjectDescriptor(id='python',language='python'),tmp_path)
    assert result.verdict=='pass'
    assert captured[0][captured[0].index('--entrypoint')+1]=='semgrep'
    assert any('target=/qc-rules/security.yaml,readonly' in part for part in captured[0])


def test_existing_pytest_uses_profile_and_sanitized_checkout(tmp_path,monkeypatch):
    root=tmp_path/'repo';(root/'backend/tests').mkdir(parents=True)
    (root/'backend/pyproject.toml').write_text('[project]\nname="sample"\nversion="1"\nrequires-python=">=3.14"\n')
    (root/'backend/tests/test_sample.py').write_text('def test_sample(): assert 2+3==5\n')
    (root/'.env').write_text('SECRET=host-value')
    observed=[]
    def run(self,invocation,timeout,environment):
        assert self.isolation=='docker' and self.profile.python_version=='3.14'
        assert self.checkout!=root and not (self.checkout/'.env').exists()
        assert invocation.cwd==self.checkout/'backend' and 'tests' in invocation.argv
        observed.append(True);return ExecutionOutput(0,'2 passed in 0.01s')
    monkeypatch.setattr('app.infrastructure.execution.pytest_environment.ProfileBackend.run',run)
    result=PreparedToolRuntime(Settings(_env_file=None),None).execute(task(root='backend'),ProjectDescriptor(id='python',root='backend',language='python'),root)
    assert result.verdict=='pass' and result.summary['source']=='existing' and observed


def test_virtual_workspace_is_not_tested_with_service_python(tmp_path):
    (tmp_path/'pyproject.toml').write_text('[tool.uv.workspace]\nmembers=["backend"]\n')
    result=PreparedToolRuntime(Settings(_env_file=None),None).execute(task(),ProjectDescriptor(id='python',language='python'),tmp_path)
    assert result.verdict=='skipped' and 'Virtual Python workspace' in result.summary['reason']


def test_collection_failure_is_unknown():
    from app.infrastructure.execution.adapters.pytest import PytestUnitAdapter
    result=PytestUnitAdapter().parse_result(task(),2,"ModuleNotFoundError: sqlmodel\n1 error")
    assert result.execution_status=='failed' and result.verdict=='unknown'


def test_profile_provisions_declared_dev_and_smtp_settings(tmp_path):
    from app.infrastructure.test_design.execution_profiles import discover_profiles
    from tda.infrastructure.repository_tools import RepositoryTools
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_sample.py').write_text('def test_sample(): pass')
    (tmp_path/'pyproject.toml').write_text('[project]\nname="sample"\nversion="1"\n')
    (tmp_path/'config.py').write_text('from pydantic_settings import BaseSettings\nfrom typing import Literal\nclass Settings(BaseSettings):\n FASTAPI_ENV: Literal["development", "production"] = "production"\n SMTP_HOST: str | None = None\n SMTP_PORT: int = 587\n SMTP_TLS: bool = True\n EMAILS_FROM_EMAIL: str | None = None\n')
    class Artifacts:
        def write(self,*args):pass
    profile=discover_profiles(RepositoryTools(tmp_path,Artifacts()))[0]
    assert profile.services==['smtp']
    assert profile.test_environment=={'FASTAPI_ENV':'development','SMTP_HOST':'smtp','SMTP_PORT':'1025','SMTP_TLS':'false','EMAILS_FROM_EMAIL':'qc-test@example.com'}


def test_cleanup_handles_git_readonly_files_and_checks_boundary(tmp_path):
    workspaces=tmp_path/'workspaces';pack=workspaces/'run_sample/.git/objects/pack';pack.mkdir(parents=True)
    file=pack/'sample.idx';file.write_text('git data');file.chmod(stat.S_IREAD)
    source=GitRepositoryManager(workspaces,None,True)
    source.cleanup(workspaces/'run_sample');assert not (workspaces/'run_sample').exists()
    with pytest.raises(ValueError,match='escapes'):source.cleanup(tmp_path)
