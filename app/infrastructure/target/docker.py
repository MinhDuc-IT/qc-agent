"""Per-run containers for generated baseline tests; no source code runs on the app host."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

import httpx

from ...domain.models import ProvisionedTarget
from .local import LocalTargetProvisioner, TargetUnavailable


class DockerTargetProvisioner:
    def __init__(self):
        self._containers={}
        self._images={}
        self._networks={}
        self._proxies={}
        self._lock=Lock()
        self.secrets=LocalTargetProvisioner()

    def prepare_unit(self,run,workspace,language='python'):
        key=(run.run_id,language)
        with self._lock:
            if key in self._images:return self._images[key]
        if language not in {'python','javascript'}:raise TargetUnavailable('Unit language unsupported')
        image='agent-qc-test:'+hashlib.sha256((run.run_id+language).encode()).hexdigest()[:20]
        with tempfile.TemporaryDirectory(prefix='qc-container-') as temporary:
            context=Path(temporary)/'source'
            def ignore(directory,names):
                return [name for name in names if name in {'.git','.venv','venv','node_modules','.tda-input','__pycache__'}
                        or name.startswith('.env') or (Path(directory)/name).is_symlink()]
            shutil.copytree(workspace,context,ignore=ignore)
            if language=='python':
                dockerfile='FROM python:3.12-slim\nWORKDIR /workspace\nRUN pip install --no-cache-dir pytest==8.3.5\n'
                if (context/'requirements.txt').is_file():
                    dockerfile+='COPY requirements.txt /tmp/requirements.txt\nRUN pip install --no-cache-dir -r /tmp/requirements.txt\n'
                dockerfile+='COPY . /workspace\n'
                if (context/'pyproject.toml').is_file():dockerfile+='RUN pip install --no-cache-dir .\n'
            else:
                dockerfile='FROM node:22-alpine\nWORKDIR /workspace\nCOPY . /workspace\n'
                if (context/'package-lock.json').is_file():dockerfile+='RUN npm ci --no-audit --no-fund\n'
                elif (context/'package.json').is_file():dockerfile+='RUN npm install --no-audit --no-fund\n'
            path=Path(temporary)/'Dockerfile';path.write_text(dockerfile,encoding='utf-8')
            build=subprocess.run(['docker','build','--quiet','--tag',image,'--file',str(path),str(context)],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300,check=False)
            if build.returncode:raise TargetUnavailable('Isolated test image build failed')
        with self._lock:self._images[key]=image
        return image

    def image_for_run(self,run_id):
        with self._lock:return {language:image for (identifier,language),image in self._images.items() if identifier==run_id}

    def provision(self,run,analysis,workspace):
        config=analysis.config.get('target')
        if not isinstance(config,dict):raise TargetUnavailable('Automatic target is unavailable')
        command=config.get('command',[])
        if not command:raise TargetUnavailable('Target entrypoint is unavailable')
        executable=Path(command[0]).name.lower()
        language='python' if executable in {'python','python3','python.exe'} else 'javascript' if executable in {'node','node.exe','npm'} else None
        if not language:raise TargetUnavailable('Isolated target adapter does not support this runtime')
        image=self.prepare_unit(run,workspace,language)
        base=config['base_url'];parts=urlsplit(base)
        if parts.hostname not in {'localhost','127.0.0.1'} or not parts.port:
            raise TargetUnavailable('Target must use the isolated local service URL')
        argv=list(command)
        argv[0]='python' if language=='python' else executable.removesuffix('.exe')
        argv=[('0.0.0.0' if item=='127.0.0.1' else item) for item in argv]
        refs=config.get('environment',{})
        prefix=f"secret://PROJECT_{run.trigger_context.installation.id}_{run.managed_baseline['repository_id']}_" if getattr(run,'managed_baseline',None) else None
        if prefix and any(not isinstance(ref,str) or not ref.startswith(prefix) for ref in refs.values()):
            raise TargetUnavailable('Target secrets must be scoped to this project')
        environment=self.secrets._resolve_environment(refs)
        name='qc-target-'+hashlib.sha256(run.run_id.encode()).hexdigest()[:20]
        network='qc-net-'+hashlib.sha256(run.run_id.encode()).hexdigest()[:20]
        created_network=subprocess.run(['docker','network','create','--internal',network],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
        if created_network.returncode:raise TargetUnavailable('Isolated test network failed to start')
        with self._lock:self._networks[run.run_id]=network
        args=['docker','run','--detach','--name',name,'--cap-drop','ALL','--security-opt','no-new-privileges',
              '--network',network,'--cpus','1','--memory','1g','--pids-limit','256']
        for key,value in environment.items():args.extend(['--env',key+'='+value])
        created=subprocess.run([*args,image,*argv],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if created.returncode:
            self.cleanup(run.run_id);raise TargetUnavailable('Isolated target failed to start')
        with self._lock:self._containers[run.run_id]=name
        # Docker Desktop does not publish ports from an internal network. A fixed
        # TCP relay bridges host traffic without giving project code an egress route.
        proxy=name+'-relay'
        with self._lock:self._proxies[run.run_id]=proxy
        relay=subprocess.run(['docker','run','--detach','--name',proxy,'--cap-drop','ALL',
            '--security-opt','no-new-privileges','--publish',f'127.0.0.1:{parts.port}:{parts.port}',
            'alpine/socat:1.8.0.3',f'TCP-LISTEN:{parts.port},fork,reuseaddr',f'TCP:{name}:{parts.port}'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        connected=subprocess.run(['docker','network','connect',network,proxy],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20) if relay.returncode==0 else None
        if relay.returncode or connected.returncode:
            self.cleanup(run.run_id);raise TargetUnavailable('Isolated HTTP relay failed to start')
        deadline=time.monotonic()+min(config.get('startup_timeout_seconds',30),60)
        try:
            while time.monotonic()<deadline:
                try:
                    if httpx.get(config['healthcheck'],timeout=2,trust_env=False).is_success:
                        return ProvisionedTarget(type='http_service',ref=base,healthcheck_url=config['healthcheck'])
                except httpx.HTTPError:pass
                time.sleep(.3)
            raise TargetUnavailable('Isolated target readiness check failed')
        except Exception:
            self.cleanup(run.run_id);raise

    def cleanup(self,run_id):
        with self._lock:
            name=self._containers.pop(run_id,None)
            network=self._networks.pop(run_id,None)
            proxy=self._proxies.pop(run_id,None)
        if proxy:subprocess.run(['docker','rm','--force',proxy],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=False)
        if name:subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=False)
        if network:subprocess.run(['docker','network','rm',network],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=False)
        self.cleanup_images(run_id)

    def cleanup_images(self,run_id):
        with self._lock:
            keys=[key for key in self._images if key[0]==run_id]
            images=[self._images.pop(key) for key in keys]
        for image in images:
            subprocess.run(['docker','image','rm',image],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=False)
