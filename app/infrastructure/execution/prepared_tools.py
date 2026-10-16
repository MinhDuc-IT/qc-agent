"""Provision repository-specific pytest environments and pinned security containers."""
import shutil
import tempfile
import tomllib
from pathlib import Path
from .runtime import RegisteredWorkerRuntime
from .backends import DockerExecutionBackend
from ...domain.models import WorkerResult


class PreparedToolRuntime:
    def __init__(self,settings,fallback):self.settings=settings;self.fallback=fallback

    def execute(self,task,project,workspace):
        if task.capability in ('security.sast','security.secrets'):
            try:
                import yaml
                path=self.settings.agent_qc_docker_images_file
                images=yaml.safe_load(Path(path).read_text(encoding='utf-8')) if path else {}
                return RegisteredWorkerRuntime(backend=DockerExecutionBackend(images or {})).execute(task,project,workspace)
            except (ValueError,OSError) as error:return self.blocked(task,str(error))
        if task.capability=='unit.run' and project.language=='python':
            try:return self.python_tests(task,project,workspace)
            except (ValueError,OSError) as error:return self.blocked(task,str(error))
        return self.fallback.execute(task,project,workspace)

    def python_tests(self,task,project,workspace):
        from ..test_design.execution_profiles import discover_profiles,safe_file
        from tda.infrastructure.repository_tools import RepositoryTools
        from .pytest_environment import ProfileBackend
        class Artifacts:
            def write(self,*args):pass
        profiles=discover_profiles(RepositoryTools(workspace,Artifacts()))
        root=task.scope.get('project_root',project.root)
        selected=[p for p in profiles if p.root==root]
        if not selected:
            manifest=safe_file(workspace,str(Path(root)/'pyproject.toml'))
            if manifest.is_file():
                data=tomllib.loads(manifest.read_text(encoding='utf-8'))
                if 'project' not in data and data.get('tool',{}).get('uv',{}).get('workspace'):
                    return WorkerResult(task_id=task.task_id,run_id=task.run_id,capability=task.capability,
                        worker_id='python-pytest',worker_kind='tool',execution_status='completed',verdict='skipped',
                        summary={'reason':'Virtual Python workspace; tests run through member project profiles.'})
            return self.blocked(task,'No unambiguous pytest execution profile; do not run repository tests in QC service Python.')
        if len(selected)!=1:raise ValueError('Ambiguous pytest profile')
        with tempfile.TemporaryDirectory(prefix='qc-existing-') as directory:
            checkout=Path(directory)/'source'
            def ignore(folder,names):return [n for n in names if n in {'.git','.venv','venv','node_modules','.data','.tools','.aws','.codex','.agents','__pycache__'} or n.startswith(('.env','.test-')) or Path(n).suffix in ('.pem','.key') or (Path(folder)/n).is_symlink()]
            shutil.copytree(workspace,checkout,ignore=ignore)
            prepared=task.model_copy(deep=True);prepared.scope={**task.scope,'project_root':root}
            # Only the repository's existing suite is run; generated artifacts live in a different copy.
            prepared.scope.setdefault('paths',[selected[0].test_root])
            result=RegisteredWorkerRuntime(backend=ProfileBackend(checkout,selected[0],'docker')).execute(prepared,project,checkout)
            result.summary.update(source='existing',profile_root=root,python_version=selected[0].python_version)
            return result

    @staticmethod
    def blocked(task,reason):
        return WorkerResult(task_id=task.task_id,run_id=task.run_id,capability=task.capability,worker_kind='tool',
            execution_status='failed',verdict='unknown',summary={'reason':reason},output=reason)
