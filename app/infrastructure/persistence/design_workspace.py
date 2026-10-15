"""Versioned design workspace persistence with optimistic concurrency and audit."""
import json
import time


class WorkspaceStoreMixin:
    def get_workspace(self,project_id,version=None):
        from tda.domain.workspace import TestWorkspace
        with self.connect() as db:
            if version is None:row=db.execute('SELECT payload FROM design_workspaces WHERE project_id=?',(project_id,)).fetchone()
            else:row=db.execute('SELECT payload FROM design_workspace_versions WHERE project_id=? AND version=?',(project_id,version)).fetchone()
        return TestWorkspace.model_validate_json(row[0]) if row else None

    def workspace_history(self,project_id):
        with self.connect() as db:
            return [{'version':row[0],'actor':row[1],'action':row[2],'created_at':row[3]} for row in db.execute(
                'SELECT version,actor,action,created_at FROM design_workspace_versions WHERE project_id=? ORDER BY version DESC LIMIT 100',(project_id,))]

    def _save_workspace(self,db,workspace,actor,action):
        payload=workspace.model_dump_json()
        db.execute('INSERT INTO design_workspaces VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE SET version=excluded.version,payload=excluded.payload',
            (workspace.project_id,workspace.version,payload))
        db.execute('INSERT INTO design_workspace_versions VALUES (?,?,?,?,?,?)',
            (workspace.project_id,workspace.version,payload,actor,action,time.time()))
        db.execute('INSERT INTO design_audit(project_id,action,actor,payload) VALUES (?,?,?,?)',
            (workspace.project_id,'workspace_'+action,actor,json.dumps({'version':workspace.version,'changes':workspace.last_changes})))

    def save_workspace(self,workspace,expected_version,actor,action,job_id=None):
        from ...domain.test_design import DesignProject
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM design_projects WHERE project_id=?',(workspace.project_id,)).fetchone()
            project=DesignProject.model_validate_json(row[0]) if row else None
            if not project or not project.enabled or job_id and project.current_job_id!=job_id:raise ValueError('Project or design job is stale')
            if action=='initialize' and db.execute("SELECT 1 FROM design_jobs WHERE project_id=? AND status IN ('queued','running')",(workspace.project_id,)).fetchone():raise ValueError('Design job is processing')
            row=db.execute('SELECT version FROM design_workspaces WHERE project_id=?',(workspace.project_id,)).fetchone()
            current=row[0] if row else 0
            if current!=expected_version:raise ValueError('Workspace version changed; reload before saving')
            workspace.version=current+1
            self._save_workspace(db,workspace,actor,action)
            return workspace

    def mutate_workspace(self,project_id,expected_version,actor,action,mutation):
        from tda.domain.workspace import TestWorkspace
        from ...domain.test_design import DesignProject
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM design_projects WHERE project_id=?',(project_id,)).fetchone()
            project=DesignProject.model_validate_json(row[0]) if row else None
            if not project or not project.enabled:raise ValueError('Project unavailable')
            if db.execute("SELECT 1 FROM design_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",(project_id,)).fetchone():raise ValueError('Design job is processing')
            row=db.execute('SELECT payload FROM design_workspaces WHERE project_id=?',(project_id,)).fetchone()
            if not row:raise ValueError('Workspace unavailable')
            workspace=TestWorkspace.model_validate_json(row[0])
            if workspace.version!=expected_version:raise ValueError('Workspace version changed; reload before saving')
            mutation(workspace)
            workspace.version+=1
            self._save_workspace(db,workspace,actor,action)
            return workspace

    def enqueue_workspace(self,project_id,version,action,actor,feedback='',case_ids=None,module_ids=None):
        from ...domain.test_design import DesignJob,DesignProject
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM design_projects WHERE project_id=?',(project_id,)).fetchone()
            project=DesignProject.model_validate_json(row[0]) if row else None
            row=db.execute('SELECT version,payload FROM design_workspaces WHERE project_id=?',(project_id,)).fetchone()
            if not project or not project.enabled or not row or row[0]!=version:raise ValueError('Workspace unavailable or stale')
            if db.execute("SELECT 1 FROM design_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",(project_id,)).fetchone():raise ValueError('Design job is processing')
            current=json.loads(row[1]);known={case['id'] for case in current['cases'] if case['status']!='retired'}
            if not set(case_ids or [])<=known:raise ValueError('Unknown selected cases')
            known_modules={m['scope']['id'] for m in current.get('flow_modules',[])}
            if not set(module_ids or [])<=known_modules:raise ValueError('Unknown selected modules')
            if action in ('ai_edit','bind') and not case_ids:raise ValueError('Select at least one case')
            job=DesignJob(project_id=project_id,reason='workspace_'+action,revision=None if action in ('update_sources','regression') else current['source_revision'],
                workspace_request={'version':version,'action':action,'actor':actor,'feedback':feedback,'case_ids':case_ids or [],'module_ids':module_ids or []})
            db.execute('INSERT INTO design_jobs VALUES (?,?,?,?,?)',(job.job_id,project_id,'queued',job.model_dump_json(),time.time_ns()))
            project.current_job_id=job.job_id;project.state='queued';project.message='Đang chờ cập nhật Test Design Workspace.'
            self._save_project(db,project)
            db.execute('INSERT INTO design_audit(project_id,action,actor,payload) VALUES (?,?,?,?)',
                (project_id,'workspace_job',actor,json.dumps(job.workspace_request)))
            return job

    def save_publication(self,workspace,publication,actor,baseline=None):
        from ...domain.test_design import DesignProject,DesignJob
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT version FROM design_workspaces WHERE project_id=?',(workspace.project_id,)).fetchone()
            if not row or row[0]!=workspace.version:raise ValueError('Workspace changed during publication')
            if db.execute("SELECT 1 FROM design_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",(workspace.project_id,)).fetchone():raise ValueError('Design job is processing')
            row=db.execute('SELECT payload FROM design_projects WHERE project_id=?',(workspace.project_id,)).fetchone()
            project=DesignProject.model_validate_json(row[0])
            if not project.enabled:raise ValueError('Project disabled')
            db.execute('INSERT INTO design_workspace_publications VALUES (?,?,?,?,?)',
                (workspace.project_id,publication['version'],workspace.version,json.dumps(publication,ensure_ascii=False),time.time()))
            workspace.published_version=publication['version'];workspace.published_workspace_version=workspace.version
            workspace.last_changes={'published':publication['version'],'executed_case_ids':publication['executed_case_ids']}
            workspace.version+=1
            if baseline:
                baseline.state='approved';baseline.reviewer=actor
                job=DesignJob(job_id=baseline.job_id,project_id=project.project_id,reason='workspace_publish',status='completed',revision=baseline.source_revision,baseline_version=baseline.version)
                db.execute('INSERT INTO design_jobs VALUES (?,?,?,?,?)',(job.job_id,job.project_id,job.status,job.model_dump_json(),time.time_ns()))
                db.execute('INSERT INTO design_baselines VALUES (?,?,?,?)',(baseline.project_id,baseline.version,baseline.model_dump_json(),time.time_ns()))
                project.active_baseline=baseline.version;project.current_job_id=job.job_id;project.state='active';project.message='Đã publish bộ test và cập nhật baseline cho QC.'
                self._save_project(db,project)
            self._save_workspace(db,workspace,actor,'publish')
            return workspace

    def workspace_publications(self,project_id):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM design_workspace_publications WHERE project_id=? ORDER BY created_at DESC',(project_id,))]

    def recover_workspace_draft(self,job_id,workspace,expected_version):
        from ...domain.test_design import DesignJob,DesignProject
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM design_jobs WHERE job_id=?',(job_id,)).fetchone()
            job=DesignJob.model_validate_json(row[0]) if row else None
            if not job or job.status!='failed' or job.project_id!=workspace.project_id or job.revision!=workspace.source_revision:raise ValueError('Recovery job is unavailable or source revision differs')
            row=db.execute('SELECT payload FROM design_projects WHERE project_id=?',(workspace.project_id,)).fetchone()
            project=DesignProject.model_validate_json(row[0]) if row else None
            if not project or not project.enabled or project.current_job_id!=job_id:raise ValueError('A newer job superseded this failed job')
            row=db.execute('SELECT version FROM design_workspaces WHERE project_id=?',(workspace.project_id,)).fetchone()
            if (row[0] if row else 0)!=expected_version:raise ValueError('Workspace changed; recovery must not overwrite edits')
            workspace.version=expected_version+1;workspace.generation_job_id=job_id
            self._save_workspace(db,workspace,'system','recover_partial_generation')
            job.status='completed';job.error='LLM_OUTPUT_VALIDATION'
            job.error_message='Đã khôi phục draft từ các vòng hoàn tất; vòng sinh tiếp theo gặp lỗi JSON/schema.'
            db.execute('UPDATE design_jobs SET status=?,payload=? WHERE job_id=?',(job.status,job.model_dump_json(),job_id))
            project.state='needs_changes';project.message=f'Đã khôi phục {len(workspace.cases)} test case. Review draft hoặc Sinh bổ sung để tiếp tục; baseline QC giữ nguyên.'
            self._save_project(db,project)
            return workspace
