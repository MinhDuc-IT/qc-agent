import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..domain.test_design import ProjectSetup, CaseReviewRequest, can_request_case_review
import yaml


def create_design_router(settings, store, onboarding, github, browser_auth):
    router = APIRouter()
    secure = settings.agent_qc_public_url.startswith("https://")
    writers = {"admin", "write", "maintain"}
    permission_cache = {}

    def session(request):
        value = store.session(request.cookies.get("qc_session"))
        if value is None:
            raise HTTPException(status_code=401, detail="Đăng nhập GitHub để quản lý bộ test.")
        return value

    def csrf(request, identity):
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), identity.csrf):
            raise HTTPException(status_code=403, detail="Yêu cầu không có CSRF token hợp lệ.")

    async def authorized(project_id, identity, fresh=False):
        project = store.get_project(project_id)
        if project is None or not project.enabled:
            raise HTTPException(status_code=404, detail="Project không tồn tại hoặc đã bị gỡ quyền.")
        try:
            key = (project.installation_id, project.repository.full_name, identity.login, identity.user_id)
            cached = permission_cache.get(key)
            if cached and not fresh and cached[1] > time.monotonic():
                permission = cached[0]
            else:
                permission = await github.repository_permission(project.installation_id, project.repository.full_name, identity.login, identity.user_id)
                permission_cache[key] = (permission, time.monotonic() + 60)
        except (httpx.HTTPError, RuntimeError):
            raise HTTPException(status_code=503, detail="Chưa xác minh được quyền repository với GitHub.") from None
        if permission not in writers:
            raise HTTPException(status_code=403, detail="Cần quyền write, maintain hoặc admin của repository.")
        return project

    def artifact_path(baseline):
        directory = Path(baseline.baseline_dir).resolve()
        expected = settings.agent_qc_tda_dir.resolve() / "projects" / baseline.project_id / "baselines"
        if directory.parent != expected or directory.name != baseline.version:
            raise HTTPException(status_code=409, detail="Artifact không thuộc kho baseline của project.")
        return directory

    @router.get("/app", response_class=HTMLResponse)
    @router.get("/app/onboarding", response_class=HTMLResponse)
    def portal():
        return HTMLResponse(Path(__file__).with_name("design_portal.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'"})

    @router.get("/auth/github/login")
    def login():
        if not settings.github_client_id or not settings.github_client_secret:
            raise HTTPException(status_code=503, detail="Quản trị App cần cấu hình GitHub OAuth client ID và secret.")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        store.issue_oauth_state(state, verifier)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = urlencode({"client_id": settings.github_client_id, "redirect_uri": browser_auth.callback_url,
                           "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        response = RedirectResponse("https://github.com/login/oauth/authorize?" + query, status_code=302)
        response.set_cookie("qc_oauth_state", state, httponly=True, secure=secure, samesite="lax", max_age=600)
        return response

    @router.get("/auth/github/callback")
    async def callback(request: Request, code: str, state: str):
        cookie = request.cookies.get("qc_oauth_state", "")
        if not cookie or not hmac.compare_digest(cookie, state):
            raise HTTPException(status_code=400, detail="OAuth state không hợp lệ.")
        verifier = store.consume_oauth_state(state)
        if verifier is None:
            raise HTTPException(status_code=400, detail="OAuth state hết hạn hoặc đã sử dụng.")
        try:
            identity = await browser_auth.identity(code, verifier)
        except (ValueError, KeyError, httpx.HTTPError):
            raise HTTPException(status_code=401, detail="Không xác thực được tài khoản GitHub.") from None
        token, _ = store.issue_session(identity["id"], identity["login"])
        response = RedirectResponse("/app", status_code=303)
        response.delete_cookie("qc_oauth_state")
        response.set_cookie("qc_session", token, httponly=True, secure=secure, samesite="lax", max_age=3600)
        return response

    @router.get("/api/v1/design/session")
    def get_session(request: Request):
        identity = store.session(request.cookies.get("qc_session"))
        return {"login": identity.login if identity else None, "csrf": identity.csrf if identity else None,
                "oauth_configured": bool(settings.github_client_id and settings.github_client_secret),
                "enabled": settings.agent_qc_tda_enabled,
                "generation_available": settings.agent_qc_tda_enabled,
                "install_url": f"https://github.com/apps/{settings.github_app_slug}/installations/new" if settings.github_app_slug else None}

    @router.post("/auth/logout")
    def logout(request: Request):
        identity = session(request)
        csrf(request, identity)
        store.revoke_session(request.cookies["qc_session"])
        response = RedirectResponse("/app", status_code=303)
        response.delete_cookie("qc_session")
        return response

    @router.get("/api/v1/projects")
    async def projects(request: Request):
        identity = session(request)
        visible = []
        for project in store.list_projects():
            if not project.enabled:
                continue
            try:
                await authorized(project.project_id, identity)
                visible.append(project.model_dump(mode="json", exclude={"requirements_text", "target_profile"}))
            except HTTPException as error:
                if error.status_code != 403:
                    raise
        return {"projects": visible}

    @router.get("/api/v1/projects/{project_id}")
    async def project_detail(project_id: str, request: Request):
        project = await authorized(project_id, session(request))
        job = store.get_job(project.current_job_id) if project.current_job_id else None
        progress = None
        if job and job.artifact_dir:
            directory = Path(job.artifact_dir).resolve()
            expected = (settings.agent_qc_tda_dir / "runs" / job.job_id / f"attempt-{job.attempts}").resolve()
            events = directory / "events.jsonl"
            if directory == expected and events.resolve().is_relative_to(expected):
                try:
                    with events.open("rb") as stream:
                        stream.seek(0, 2)
                        stream.seek(max(0, stream.tell() - 65536))
                        lines = stream.read().decode("utf-8", errors="ignore").splitlines()
                    for line in reversed(lines):
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        stage = event.get("stage", "")
                        if stage in {"sources", "analysis", "analysis.correction", "planning", "planning.correction",
                                     "oracle", "repair", "critic", "critic.correction", "coverage", "workflow"} or \
                                stage.startswith(('flow.','automation.')) or stage in {"generation.functional", "generation.integration", "generation.security", "generation.performance", "generation.ai_evaluation"}:
                            progress = {"stage": stage, "status": event.get("status")}
                            if isinstance(event.get('details',{}).get('call'),int):progress['calls_used']=event['details']['call']
                            break
                except OSError:
                    pass
                try:
                    details=json.loads((directory/'generation_progress.json').read_text(encoding='utf-8'))
                    progress={**details,**(progress or {})}
                    if progress.get('calls_limit') is not None:
                        progress['calls_remaining']=max(0,progress['calls_limit']-progress.get('calls_used',0))
                except (OSError,ValueError):
                    pass
        return {"project": project.model_dump(mode="json"), "job": job.model_dump(mode="json") if job else None,
                "progress": progress,
                "documents": store.list_documents(project_id),
                "upload_max_bytes": settings.agent_qc_tda_max_source_bytes,
                "baselines": [baseline.model_dump(mode="json", exclude={"baseline_dir", "report_path"})
                              for baseline in store.list_baselines(project_id)]}

    @router.post("/api/v1/projects/{project_id}/setup", status_code=202)
    async def setup(project_id: str, body: ProjectSetup, request: Request):
        identity = session(request)
        csrf(request, identity)
        await authorized(project_id, identity, fresh=True)
        if not settings.agent_qc_tda_enabled:
            raise HTTPException(status_code=409, detail="Test Design đang được quản trị dịch vụ tắt.")
        # Paths are explicit relative files; no traversal, globs or credential selections.
        if any(Path(path).is_absolute() or ".." in Path(path).parts or ":" in path for path in body.source_paths):
            raise HTTPException(status_code=422, detail="Nguồn phải là file bên trong repository.")
        if body.target_command and any(not item.strip() or "\x00" in item for item in body.target_command):
            raise HTTPException(status_code=422, detail="Lệnh khởi động cần danh sách arguments hợp lệ.")
        try:
            store.setup(project_id, body)
        except ValueError:
            raise HTTPException(status_code=422, detail="Tài liệu upload không thuộc project này.") from None
        job = store.enqueue(project_id, "maintainer_setup", force=True)
        return {"status": "queued", "job_id": job.job_id}

    @router.get("/api/v1/projects/{project_id}/document-paths")
    async def document_paths(project_id: str, request: Request):
        project = await authorized(project_id, session(request))
        try:
            return await github.document_paths(project.installation_id, project.repository.full_name)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(status_code=502, detail="Không lấy được danh sách tài liệu từ repository; thử lại sau.") from None

    @router.post("/api/v1/projects/{project_id}/documents", status_code=201)
    async def upload_document(project_id: str, request: Request):
        identity = session(request)
        csrf(request, identity)
        await authorized(project_id, identity, fresh=True)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.agent_qc_tda_max_source_bytes * 6 + 4096:
                raise HTTPException(status_code=413, detail="Tài liệu vượt ngân sách đầu vào.")
        try:
            body = json.loads(raw)
            name, content = body["name"], body["content"]
            if not isinstance(name, str) or not isinstance(content, str):
                raise ValueError()
            suffix = Path(name).suffix.lower()
            if (not name or len(name) > 200 or any(char in name for char in '/\\:\x00\r\n')
                    or name.startswith(".env") or suffix not in {".md", ".txt", ".rst", ".json", ".yaml", ".yml"}
                    or not content.strip() or "\x00" in content):
                raise ValueError()
            if len(content.encode("utf-8")) > settings.agent_qc_tda_max_source_bytes:
                raise HTTPException(status_code=413, detail="Tài liệu vượt ngân sách đầu vào.")
            kind = "srs" if "srs" in name.lower() else "prd"
            if suffix in {".json", ".yaml", ".yml"}:
                document = yaml.safe_load(content)
                if isinstance(document, dict) and "openapi" in document:
                    kind = "openapi"
            return store.add_document(project_id, name, kind, content)
        except (ValueError, KeyError, TypeError, UnicodeError, yaml.YAMLError):
            raise HTTPException(status_code=422, detail="Upload cần file văn bản UTF-8 hợp lệ (MD, TXT, RST, JSON, YAML), tối đa 50 tài liệu.") from None

    @router.post("/api/v1/projects/{project_id}/regenerate", status_code=202)
    async def regenerate(project_id: str, request: Request):
        identity = session(request)
        csrf(request, identity)
        await authorized(project_id, identity, fresh=True)
        if not settings.agent_qc_tda_enabled:
            raise HTTPException(status_code=409, detail="Test Design đang được quản trị dịch vụ tắt.")
        job = store.enqueue(project_id, "maintainer_regenerate", force=True)
        return {"status": "queued", "job_id": job.job_id}

    @router.get("/api/v1/projects/{project_id}/baselines/{version}")
    async def baseline_detail(project_id: str, version: str, request: Request):
        project=await authorized(project_id, session(request))
        baseline = store.get_baseline(project_id, version)
        if baseline is None:
            raise HTTPException(status_code=404, detail="Baseline không tồn tại.")
        directory = artifact_path(baseline)
        try:
            from tda.pipeline import verify
            manifest = verify(directory)
            files = {name: json.loads((directory / name).read_text(encoding="utf-8")) for name in (
                "cases/cases.json", "test_plan.json", "validation_report.json", "coverage.json", "critic_review.json", "review_gate.json", "traceability.json", "sources.json")}
            for name in ('review_changes.json','onboarding.json'):
                if name in manifest['files']:files[name]=json.loads((directory/name).read_text(encoding='utf-8'))
            report = None
            if 'reference_execution.json' in manifest['files']:
                report=json.loads((directory/'reference_execution.json').read_text(encoding='utf-8'))
            elif baseline.report_path:
                report_path = Path(baseline.report_path).resolve()
                if not report_path.is_relative_to(settings.agent_qc_tda_dir.resolve() / "runs"):
                    raise ValueError("Invalid execution report path")
                report = json.loads(report_path.read_text(encoding="utf-8"))
            current_job=store.get_job(project.current_job_id)
            current_baseline=store.get_baseline(project_id,current_job.baseline_version) if current_job and current_job.baseline_version else None
            return {"baseline": baseline.model_dump(mode="json", exclude={"baseline_dir", "report_path"}),
                    "manifest": {key: manifest[key] for key in ("baseline_version", "source_revision", "state", "cases")},
                    "artifacts": files, "execution": report,
                    "can_approve_baseline":project.enabled and baseline.state=='draft' and project.current_job_id==baseline.job_id,
                    "can_request_case_review":can_request_case_review(project,baseline,current_job,current_baseline)}
        except (ValueError, OSError, KeyError, ImportError):
            raise HTTPException(status_code=409, detail="Không đọc được baseline hoặc artifact không còn hợp lệ.") from None

    @router.post('/api/v1/projects/{project_id}/baselines/{version}/case-review',status_code=202)
    async def case_review(project_id: str,version: str,request: Request,body: CaseReviewRequest):
        identity=session(request);csrf(request,identity)
        await authorized(project_id,identity,fresh=True)
        raise HTTPException(status_code=410,detail='Engine TDA retired; generation is unavailable until the new design is implemented.')
        if not settings.agent_qc_tda_enabled:raise HTTPException(status_code=409,detail='TDA đang được quản trị dịch vụ tắt.')
        baseline=store.get_baseline(project_id,version)
        if baseline is None:raise HTTPException(status_code=404,detail='Baseline không tồn tại.')
        directory=artifact_path(baseline)
        try:
            from tda.pipeline import verify
            manifest=verify(directory)
            if len(body.model_dump_json().encode('utf-8'))>64000:raise HTTPException(status_code=422,detail='Nội dung chỉnh sửa vượt giới hạn 64 KB.')
            if body.action not in ('supplement','add_case') and body.case_id not in manifest['cases']:raise HTTPException(status_code=422,detail='Test case không thuộc baseline này.')
            if body.action=='add_case' and body.case_id in manifest['cases']:raise HTTPException(status_code=422,detail='Case ID đã tồn tại; hãy dùng sửa case.')
            if body.case_data:
                from tda.domain.contracts import ExecutableCase, PlannedScenario
                try:
                    case=ExecutableCase.model_validate(body.case_data)
                    if case.id!=body.case_id:raise ValueError('Case ID mismatch')
                    if body.scenario_data:
                        scenario=PlannedScenario.model_validate(body.scenario_data)
                        if scenario.id!=case.scenario_id:raise ValueError('Scenario mismatch')
                except ValueError:
                    raise HTTPException(status_code=422,detail='Nội dung test case hoặc scenario chưa đúng schema.') from None
            job=store.enqueue_case_review(project_id,version,body,identity.login)
            return {'status':'queued','job_id':job.job_id,'baseline_version':version,'case_id':body.case_id}
        except (ValueError,OSError,ImportError):
            raise HTTPException(status_code=409,detail='Không thể gửi feedback: baseline đã có lượt xử lý mới, đã được review hoặc source chưa hợp lệ.') from None

    @router.post("/api/v1/projects/{project_id}/baselines/{version}/approve")
    async def approve(project_id: str, version: str, request: Request):
        identity = session(request)
        csrf(request, identity)
        await authorized(project_id, identity, fresh=True)
        def approve_files(baseline, reviewer):
            from tda.pipeline import approve as approve_baseline
            directory = artifact_path(baseline)
            approve_baseline(directory, reviewer)
        try:
            result = store.review(project_id, version, identity.login, "approve", approve_files)
            return {"status": "approved", "version": result.version}
        except (ValueError, OSError, ImportError):
            raise HTTPException(status_code=409, detail="Baseline chưa đủ điều kiện duyệt hoặc đã có job mới; xem blocker và kết quả kiểm chứng.") from None

    @router.post("/api/v1/projects/{project_id}/baselines/{version}/reject")
    async def reject(project_id: str, version: str, request: Request):
        identity = session(request)
        csrf(request, identity)
        await authorized(project_id, identity, fresh=True)
        try:
            store.review(project_id, version, identity.login, "reject")
            return {"status": "rejected"}
        except ValueError:
            raise HTTPException(status_code=409, detail="Baseline không còn ở trạng thái chờ review.") from None

    from .design_workspace import create_workspace_router
    router.include_router(create_workspace_router(settings,store,session,csrf,authorized,artifact_path,github))
    return router
