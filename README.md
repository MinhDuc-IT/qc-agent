# Agent-QC GitHub demo

Vertical slice theo tài liệu production integration:

`pull_request webhook -> HMAC + dedup -> analyze -> plan capabilities -> resolve workers -> execute -> verdict -> GitHub Check`

## Generalized QC pipeline

QC-Agent no longer depends on a hard-coded test command. For every immutable commit it now:

1. Detects projects from manifests such as `pyproject.toml`, `package.json`, `pom.xml`, `go.mod`, `Cargo.toml`, and `.csproj`.
2. Reads `.agent-qc.yaml` as quality intent.
3. Produces a versioned `ExecutionPlan` of capabilities and targets.
4. Validates the plan and resolves trusted worker adapters.
5. Executes argument lists with `shell=False` and normalizes every tool result.
6. Aggregates results into one GitHub verdict.

Built-in executable adapters currently include pytest, npm test, Maven, Gradle, Go test, Cargo test, dotnet test, Ruff, ESLint, Semgrep, and Trivy. Missing implementations are reported as `skipped`; they are never converted into arbitrary shell commands.

```yaml
version: "1"
quality:
  static:
    lint:
      enabled: auto
  functional:
    unit:
      enabled: true
  security:
    sast:
      enabled: auto
```

The API run payload exposes `analysis`, `plan`, `results`, and `aggregate`, making planner decisions inspectable. The control-plane contracts are generalized, but the current executor still runs on the host. Add container/VM isolation before accepting untrusted public-fork code.

## Demo local (không cần GitHub App)

Yêu cầu Python 3.11+ và Git. Từ thư mục `qc-agent`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Ở terminal khác, bảo đảm `sample-app` đã là Git repo có commit, rồi gửi webhook:

```powershell
python scripts/send_demo_webhook.py --repo ../sample-app
```

Script ký payload, gửi webhook, poll run và in verdict. Sửa `calculator.add` để trả kết quả sai, commit lại rồi chạy script lần nữa để thấy verdict `fail`.

`AGENT_QC_WORKSPACE_DIR` mặc định nằm ngoài thư mục `qc-agent`; không chuyển nó vào trong source tree khi dùng `uvicorn --reload`, vì việc clone file Python có thể kích hoạt server restart.

## Kết nối GitHub thật

1. Tạo GitHub App với permissions: Metadata read, Contents read, Pull requests read, Checks read/write.
2. Subscribe sự kiện **Pull request** và dùng webhook URL HTTPS `<public-url>/webhooks/github`.
3. Tạo webhook secret, download private key `.pem`, rồi cấu hình `.env`:

```env
GITHUB_WEBHOOK_SECRET=<same-secret-on-github>
GITHUB_APP_ID=<app-id>
GITHUB_PRIVATE_KEY_PATH=C:\secure\agent-qc.private-key.pem
AGENT_QC_DRY_RUN=false
```

4. Install App vào repo `sample-app` và mở PR. Check `Agent-QC` sẽ xuất hiện trên đúng head SHA.

Để public localhost có thể dùng tunnel HTTPS (ví dụ ngrok hoặc Cloudflare Tunnel). Không commit `.env` hay private key.

## API

- `GET /health`
- `POST /webhooks/github`
- `GET /api/v1/runs/{run_id}`
- Swagger UI: `http://127.0.0.1:8000/docs`

## Giới hạn có chủ ý của demo

Worker hiện chạy trực tiếp trên host. Trước production cần container sandbox, queue bền vững, timeout/concurrency policy, stale-run cancellation, artifact storage và secret manager. SQLite đã cung cấp delivery dedup và run persistence ở mức demo.
