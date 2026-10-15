import hashlib
import json
import secrets
import sqlite3
import time

from ...domain.test_design import BrowserSession, DesignJob, DesignProject, ManagedBaseline, can_request_case_review
from .design_workspace import WorkspaceStoreMixin


class DesignStore(WorkspaceStoreMixin):
    """Durable installation/project/job/baseline registry, separate from QC run payloads."""

    def __init__(self, database_path):
        self.path = database_path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS design_projects (
                    project_id TEXT PRIMARY KEY, installation_id INTEGER NOT NULL,
                    repository_id INTEGER NOT NULL, full_name TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(installation_id, repository_id)
                );
                CREATE TABLE IF NOT EXISTS design_jobs (
                    job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL, enqueued_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS design_baselines (
                    project_id TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL, PRIMARY KEY(project_id, version)
                );
                CREATE TABLE IF NOT EXISTS design_documents (
                    document_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    metadata TEXT NOT NULL, content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    token_hash TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS design_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
                    action TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS design_workspaces (project_id TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS design_workspace_versions (project_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(project_id,version));
                CREATE TABLE IF NOT EXISTS design_workspace_publications (project_id TEXT NOT NULL, version TEXT NOT NULL, workspace_version INTEGER NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(project_id,version));
                UPDATE design_jobs SET status='queued' WHERE status='running';
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def _save_project(self, db, project):
        db.execute("INSERT INTO design_projects VALUES (?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
                   "installation_id=excluded.installation_id, repository_id=excluded.repository_id, "
                   "full_name=excluded.full_name,payload=excluded.payload",
                   (project.project_id, project.installation_id, project.repository_id,
                    project.repository.full_name, project.model_dump_json()))

    def save_project(self, project):
        with self.connect() as db:
            self._save_project(db, project)

    def register(self, installation_id, repository_id, repository):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM design_projects WHERE installation_id=? AND repository_id=?",
                             (installation_id, repository_id)).fetchone()
            project = DesignProject.model_validate_json(row[0]) if row else DesignProject(
                installation_id=installation_id, repository_id=repository_id, repository=repository)
            project.repository, project.enabled = repository, True
            self._save_project(db, project)
            return project

    def setup(self, project_id, setup):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM design_projects WHERE project_id=?", (project_id,)).fetchone()
            project = DesignProject.model_validate_json(row[0])
            if not project.enabled:
                raise ValueError("Project is disabled")
            project.requirements_text, project.source_paths = setup.requirements_text, setup.source_paths
            for document_id in setup.uploaded_document_ids:
                if not db.execute("SELECT 1 FROM design_documents WHERE project_id=? AND document_id=?",
                                  (project_id, document_id)).fetchone():
                    raise ValueError("Upload does not belong to project")
            project.uploaded_document_ids = list(dict.fromkeys(setup.uploaded_document_ids))
            project.allow_mutations = setup.allow_mutations
            if setup.target_command:
                project.target_profile = {"kind": "command", "command": setup.target_command,
                                          "healthcheck_path": setup.healthcheck_path, "origin": "maintainer"}
            elif setup.fastapi_module:
                project.target_profile = {"kind": "fastapi", "app": setup.fastapi_module, "origin": "maintainer"}
            else:
                project.target_profile = {}
            self._save_project(db, project)
            return project

    def add_document(self, project_id, name, kind, content):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_id = "doc_" + hashlib.sha256((project_id + name + digest).encode()).hexdigest()[:32]
        metadata = {"id": document_id, "name": name, "kind": kind,
                    "bytes": len(content.encode("utf-8")), "sha256": digest}
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM design_documents WHERE project_id=?", (project_id,)).fetchone()[0] >= 50:
                if not db.execute("SELECT 1 FROM design_documents WHERE document_id=?", (document_id,)).fetchone():
                    raise ValueError("Too many uploaded documents")
            db.execute("INSERT INTO design_documents VALUES (?,?,?,?) ON CONFLICT(document_id) DO NOTHING",
                       (document_id, project_id, json.dumps(metadata), content))
        return metadata

    def list_documents(self, project_id):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT metadata FROM design_documents WHERE project_id=? ORDER BY document_id", (project_id,))]

    def get_document(self, project_id, document_id):
        with self.connect() as db:
            row = db.execute("SELECT metadata,content FROM design_documents WHERE project_id=? AND document_id=?",
                             (project_id, document_id)).fetchone()
        return {**json.loads(row[0]), "content": row[1]} if row else None

    def get_project(self, project_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM design_projects WHERE project_id=?", (project_id,)).fetchone()
        return DesignProject.model_validate_json(row[0]) if row else None

    def find_project(self, installation_id, repository_id=None, full_name=None):
        with self.connect() as db:
            if repository_id is not None:
                row = db.execute("SELECT payload FROM design_projects WHERE installation_id=? AND repository_id=?",
                                 (installation_id, repository_id)).fetchone()
            else:
                row = db.execute("SELECT payload FROM design_projects WHERE installation_id=? AND full_name=?",
                                 (installation_id, full_name)).fetchone()
        return DesignProject.model_validate_json(row[0]) if row else None

    def list_projects(self):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM design_projects ORDER BY full_name").fetchall()
        return [DesignProject.model_validate_json(row[0]) for row in rows]

    def enqueue(self, project_id, reason, revision=None, force=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM design_projects WHERE project_id=?", (project_id,)).fetchone()
            project = DesignProject.model_validate_json(row[0])
            if not project.enabled:
                raise ValueError("Project installation is disabled")
            current_row = db.execute("SELECT payload,status FROM design_jobs WHERE job_id=?",
                                     (project.current_job_id,)).fetchone()
            if not force and current_row:
                current = DesignJob.model_validate_json(current_row[0])
                if current.revision == revision and current_row[1] in {"queued", "running", "completed"}:
                    return current
            job = DesignJob(project_id=project_id, reason=reason, revision=revision)
            db.execute("INSERT INTO design_jobs VALUES (?,?,?,?,?)",
                       (job.job_id, project_id, "queued", job.model_dump_json(), time.time_ns()))
            project.current_job_id = job.job_id
            project.state = "queued"
            project.message = "Đang chờ phân tích và sinh bộ test."
            self._save_project(db, project)
            return job

    def enqueue_case_review(self,project_id,version,request,actor):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM design_projects WHERE project_id=?',(project_id,)).fetchone()
            project=DesignProject.model_validate_json(row[0]) if row else None
            row=db.execute('SELECT payload FROM design_baselines WHERE project_id=? AND version=?',(project_id,version)).fetchone()
            baseline=ManagedBaseline.model_validate_json(row[0]) if row else None
            row=db.execute('SELECT payload FROM design_jobs WHERE job_id=?',(project.current_job_id,)).fetchone() if project else None
            current_job=DesignJob.model_validate_json(row[0]) if row else None
            row=db.execute('SELECT payload FROM design_baselines WHERE project_id=? AND version=?',(project_id,current_job.baseline_version)).fetchone() if current_job and current_job.baseline_version else None
            current_baseline=ManagedBaseline.model_validate_json(row[0]) if row else None
            if not can_request_case_review(project,baseline,current_job,current_baseline):
                raise ValueError('Baseline is stale or unavailable for case review')
            if request.acceptance_criteria.strip():
                if len(project.uploaded_document_ids)>=50 or db.execute('SELECT COUNT(*) FROM design_documents WHERE project_id=?',(project_id,)).fetchone()[0]>=50:
                    raise ValueError('Too many source documents')
                content='Acceptance criteria do maintainer bổ sung\n\nTest case: '+request.case_id+'\n\n'+request.acceptance_criteria.strip()
                digest=hashlib.sha256(content.encode('utf-8')).hexdigest()
                identifier='doc_'+hashlib.sha256((project_id+version+request.case_id+digest).encode()).hexdigest()[:32]
                safe_case=''.join(char for char in request.case_id[:80] if char.isascii() and (char.isalnum() or char in '_-'))
                metadata={'id':identifier,'name':'criteria-'+(safe_case or digest[:8])+'.md','kind':'prd','bytes':len(content.encode('utf-8')),'sha256':digest}
                db.execute('INSERT INTO design_documents VALUES (?,?,?,?) ON CONFLICT(document_id) DO NOTHING',
                    (identifier,project_id,json.dumps(metadata),content))
                if identifier not in project.uploaded_document_ids:project.uploaded_document_ids.append(identifier)
            job=DesignJob(project_id=project_id,reason='case_review',revision=baseline.source_revision,
                review_request=request,review_baseline=version,review_actor=actor)
            db.execute('INSERT INTO design_jobs VALUES (?,?,?,?,?)',(job.job_id,project_id,'queued',job.model_dump_json(),time.time_ns()))
            project.current_job_id=job.job_id;project.state='queued';project.message='Đang chờ TDA xử lý feedback cho test case '+request.case_id+'.'
            self._save_project(db,project)
            db.execute('INSERT INTO design_audit(project_id,action,actor,payload) VALUES (?,?,?,?)',
                (project_id,'case_review',actor,json.dumps({'baseline':version,'job_id':job.job_id,**request.model_dump()})))
            return job

    def claim_job(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM design_jobs WHERE status='queued' ORDER BY enqueued_at LIMIT 1").fetchone()
            if row is None:
                return None
            job = DesignJob.model_validate_json(row[0])
            job.status, job.attempts = "running", job.attempts + 1
            db.execute("UPDATE design_jobs SET status=?,payload=? WHERE job_id=?",
                       (job.status, job.model_dump_json(), job.job_id))
            return job

    def save_job(self, job):
        with self.connect() as db:
            db.execute("UPDATE design_jobs SET status=?,payload=? WHERE job_id=?",
                       (job.status, job.model_dump_json(), job.job_id))

    def get_job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT payload,status FROM design_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = DesignJob.model_validate_json(row[0])
        job.status = row[1]
        return job

    def update_job_project(self, job, **updates):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM design_projects WHERE project_id=?", (job.project_id,)).fetchone()
            project = DesignProject.model_validate_json(row[0])
            if not project.enabled or project.current_job_id != job.job_id:
                return False
            for key, value in updates.items():
                setattr(project, key, value)
            self._save_project(db, project)
            return True

    def disable(self, installation_id, repository_ids=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT payload FROM design_projects WHERE installation_id=?", (installation_id,)).fetchall()
            for row in rows:
                project = DesignProject.model_validate_json(row[0])
                if repository_ids is not None and project.repository_id not in repository_ids:
                    continue
                project.enabled, project.state = False, "disabled"
                project.message = "Repository không còn được cấp quyền cho GitHub App."
                self._save_project(db, project)
                db.execute("UPDATE design_jobs SET status='cancelled' WHERE project_id=? AND status='queued'", (project.project_id,))

    def save_baseline(self, baseline):
        with self.connect() as db:
            db.execute("INSERT INTO design_baselines VALUES (?,?,?,?) ON CONFLICT(project_id,version) "
                       "DO NOTHING", (baseline.project_id, baseline.version, baseline.model_dump_json(), time.time_ns()))

    def get_baseline(self, project_id, version):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM design_baselines WHERE project_id=? AND version=?",
                             (project_id, version)).fetchone()
        return ManagedBaseline.model_validate_json(row[0]) if row else None

    def list_baselines(self, project_id):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM design_baselines WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
        return [ManagedBaseline.model_validate_json(row[0]) for row in rows]

    def review(self, project_id, version, reviewer, action, approve_files=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM design_projects WHERE project_id=?", (project_id,)).fetchone()
            project = DesignProject.model_validate_json(row[0])
            row = db.execute("SELECT payload FROM design_baselines WHERE project_id=? AND version=?",
                             (project_id, version)).fetchone()
            if not row:
                raise ValueError("Baseline not found")
            baseline = ManagedBaseline.model_validate_json(row[0])
            if not project.enabled or baseline.job_id != project.current_job_id or baseline.state != "draft":
                raise ValueError("Baseline is stale, already reviewed or installation is disabled")
            if action == "approve":
                if baseline.blockers:
                    raise ValueError("Baseline requires no design blockers")
                approve_files(baseline, reviewer)
                baseline.state, project.active_baseline = "approved", version
                project.state, project.message = "active", "Bộ test đã được duyệt; các PR sẽ tự chạy baseline này."
            else:
                baseline.state = "rejected"
                project.state, project.message = "needs_changes", "Bộ test cần chỉnh sửa; bổ sung yêu cầu rồi sinh lại."
            baseline.reviewer = reviewer
            db.execute("UPDATE design_baselines SET payload=? WHERE project_id=? AND version=?",
                       (baseline.model_dump_json(), project_id, version))
            self._save_project(db, project)
            db.execute("INSERT INTO design_audit(project_id,action,actor,payload) VALUES (?,?,?,?)",
                       (project_id, action, reviewer, json.dumps({"version": version, "revision": baseline.source_revision})))
            return baseline

    def issue_session(self, user_id, login):
        token = secrets.token_urlsafe(32)
        session = BrowserSession(user_id=user_id, login=login, csrf=secrets.token_urlsafe(32), expires_at=time.time() + 3600)
        with self.connect() as db:
            db.execute("DELETE FROM browser_sessions WHERE expires_at<?", (time.time(),))
            db.execute("INSERT INTO browser_sessions VALUES (?,?,?)",
                       (self._hash(token), session.model_dump_json(), session.expires_at))
        return token, session

    def session(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute("SELECT payload FROM browser_sessions WHERE token_hash=? AND expires_at>?",
                             (self._hash(token), time.time())).fetchone()
        return BrowserSession.model_validate_json(row[0]) if row else None

    def revoke_session(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM browser_sessions WHERE token_hash=?", (self._hash(token),))

    def revoke_user(self, user_id):
        with self.connect() as db:
            rows = db.execute("SELECT token_hash,payload FROM browser_sessions").fetchall()
            for token_hash, payload in rows:
                if json.loads(payload)["user_id"] == user_id:
                    db.execute("DELETE FROM browser_sessions WHERE token_hash=?", (token_hash,))

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_oauth_state(self, state, verifier):
        with self.connect() as db:
            db.execute("DELETE FROM oauth_states WHERE expires_at<?", (time.time(),))
            db.execute("INSERT INTO oauth_states VALUES (?,?,?)", (self._hash(state), verifier, time.time() + 600))

    def consume_oauth_state(self, state):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT verifier,expires_at FROM oauth_states WHERE state_hash=?", (self._hash(state),)).fetchone()
            db.execute("DELETE FROM oauth_states WHERE state_hash=?", (self._hash(state),))
        return row[0] if row and row[1] > time.time() else None
