# Tích hợp open-source testing tools vào một hệ thống QC-Agent

Tài liệu này trình bày cách xây dựng một QC-Agent tổng quát bằng các dự án
open-source chuyên biệt. Nội dung không phụ thuộc vào một repository cụ thể.
Các đường dẫn như `<qc-agent-root>` và `<target-repo>` cần được thay bằng đường
dẫn thực tế của hệ thống triển khai.

## 1. Mục tiêu kiến trúc

QC-Agent không nên tự triển khai lại browser automation, load generator, static
analysis hoặc vulnerability database. Hệ thống nên đóng vai trò control plane:

1. Nhận trigger từ GitHub/GitLab hoặc scheduler.
2. Phân tích repository và thay đổi của revision bất biến.
3. Lập kế hoạch các capability cần kiểm tra.
4. Giao task cho external agent hoặc trusted CLI adapter.
5. Chuẩn hóa kết quả về một schema chung.
6. Áp policy để tạo verdict và review dễ hiểu.

```text
SCM webhook
  -> QC Orchestrator
     -> Planner
     -> Functional agent
     -> Integration agent
     -> Performance agent
     -> Security scanners/agent
     -> Unit-test adapters
     -> Triage + Verdict
     -> Check Run / PR review / artifacts
```

Không cần tải source code upstream vào QC-Agent. Mỗi dự án được sử dụng dưới
một trong các dạng sau:

- Python package cài bằng `pip`.
- Executable/CLI phát hành sẵn.
- Docker image.
- Service riêng giao tiếp qua HTTP task contract.

## 2. Danh sách open-source đề xuất

| Open-source | Loại | Mục đích | Cần LLM key |
|---|---|---|---|
| TestZeus Hercules | Agent + Python package | Functional API/E2E bằng browser | Có |
| Keploy OSS | CLI/engine | Record-replay integration và API | Không bắt buộc |
| k6 | CLI/engine | Performance, load, stress, soak | Không |
| Semgrep | Python package/CLI | SAST, tìm lỗi bảo mật trong source | Không |
| Trivy | CLI/engine | SCA, image và IaC vulnerability scanning | Không |
| Gitleaks | CLI/engine | Tìm secret và credential | Không |
| Strix | Agent + Python package/container | Agentic DAST/pentest | Có |
| pytest | Test framework | Unit/integration test Python | Không |

Hercules và Strix là agent theo nghĩa có reasoning loop và sử dụng model.
Keploy, k6, Semgrep, Trivy và Gitleaks là specialized engines. QC-Agent có thể
bọc mọi engine bằng worker service để thống nhất lifecycle, nhưng việc bọc một
CLI không biến bản thân CLI thành LLM agent.

## 3. Contract chung giữa Orchestrator và worker

External worker nên triển khai contract tối thiểu:

```http
POST /v1/tasks
GET /v1/tasks/{worker_run_id}
DELETE /v1/tasks/{worker_run_id}
GET /health
```

Task mẫu:

```json
{
  "task_id": "task_123",
  "run_id": "run_123",
  "capability": "functional.e2e",
  "objective": "Validate checkout",
  "source": {
    "type": "local_path",
    "ref": "/workspaces/run_123"
  },
  "target": {
    "type": "web_app",
    "ref": "http://sut:8080"
  },
  "parameters": {},
  "timeout_seconds": 300
}
```

Kết quả chuẩn hóa:

```json
{
  "task_id": "task_123",
  "run_id": "run_123",
  "capability": "functional.e2e",
  "worker_id": "hercules-functional",
  "implementation": "testzeus-hercules@<pinned-version>",
  "execution_status": "completed",
  "verdict": "pass",
  "exit_code": 0,
  "summary": {
    "tests": 1,
    "failures": 0
  },
  "metrics": {},
  "findings": [],
  "error": null
}
```

Worker phải xác thực transport token, kiểm tra capability, giới hạn workspace,
timeout, CPU/memory và không chấp nhận shell command tùy ý do model sinh ra.

## 4. Cấu trúc project QC-Agent gợi ý

```text
<qc-agent-root>/
  orchestrator/
  workers/
    functional-hercules/
    integration-keploy/
    performance-k6/
    security-strix/
  adapters/
    semgrep/
    trivy/
    gitleaks/
    unit-tests/
  policies/
  schemas/
  artifacts/
  workspaces/
  docker-compose.yaml
  .env
```

Không đặt checkout của target repository bên trong thư mục source đang được
development server theo dõi, vì file thay đổi có thể làm server reload giữa run.

## 5. TestZeus Hercules

### 5.1 Vai trò

Hercules nhận functional scenario, dùng LLM để quyết định thao tác và điều khiển
browser qua Playwright. Nó phù hợp với:

- Functional web journey.
- E2E flow.
- API behavior có UI hoặc OpenAPI endpoint.
- Exploratory flow có scope chặt.

### 5.2 Cài đặt

Nên dùng virtual environment riêng:

```powershell
cd <qc-agent-root>
python -m venv .venv-hercules
.\.venv-hercules\Scripts\python.exe -m pip install --upgrade pip
.\.venv-hercules\Scripts\python.exe -m pip install testzeus-hercules==<pinned-version>
.\.venv-hercules\Scripts\python.exe -m playwright install chromium
```

Linux/container cần system dependencies của Playwright:

```bash
python -m playwright install --with-deps chromium
```

Biến môi trường:

```env
HERCULES_LLM_API_KEY=<dedicated-key>
HERCULES_LLM_MODEL=<supported-model>
HERCULES_MAX_SCENARIOS=1
HERCULES_MAX_STEPS=8
HERCULES_MAX_RUNTIME_SECONDS=300
HERCULES_AGENT_TOKEN=<transport-token>
```

### 5.3 Tích hợp worker

Worker thực hiện:

1. Nhận feature/scenario và `base_url` từ task đã qua policy validation.
2. Tạo working directory riêng cho run.
3. Giới hạn số scenario/step và thời gian.
4. Gọi Hercules CLI bằng argument list, không dùng `shell=True`.
5. Đọc JUnit XML và artifacts.
6. Trả `WorkerResult`.

Không cho Hercules tự chọn target ngoài allowlist. LLM key của Hercules phải
tách khỏi key của Planner/Triage để đo chi phí và revoke độc lập.

### 5.4 Dockerfile tối thiểu

```dockerfile
FROM python:3.12-slim
WORKDIR /service
RUN pip install --no-cache-dir testzeus-hercules==<pinned-version> fastapi uvicorn \
    && playwright install --with-deps chromium
COPY workers/functional-hercules /service/worker
ENV HEADLESS=true AUTO_MODE=1 ENABLE_TELEMETRY=0
CMD ["uvicorn", "worker.app:app", "--host", "0.0.0.0", "--port", "8101"]
```

## 6. Keploy OSS

### 6.1 Vai trò

Keploy record HTTP requests/responses và dependency interactions thành test
fixture, sau đó replay để phát hiện regression. Nó phù hợp với:

- Service integration test.
- API regression.
- Consumer/provider interaction đã record.
- Mock dependency trong replay.

### 6.2 Cài đặt

Tải executable từ official release của Keploy và pin version:

```text
<qc-agent-root>/tools/keploy.exe
```

Kiểm tra:

```powershell
<qc-agent-root>\tools\keploy.exe --help
```

Hoặc dùng Docker image:

```text
ghcr.io/keploy/keploy:<pinned-version>
```

### 6.3 Chuẩn bị target project

Từ `<target-repo>`, chạy record và gửi representative traffic:

```powershell
<qc-agent-root>\tools\keploy.exe record `
  -c "<command-start-sut>" `
  --record-timer 60s `
  --disable-ansi
```

Commit fixture `keploy/` vào target repository. Trước khi bật QC gate, chạy:

```powershell
<qc-agent-root>\tools\keploy.exe test `
  --path . `
  --command "<command-start-sut>" `
  --disable-ansi
```

### 6.4 Tích hợp worker

Worker chỉ nhận `sut_command` từ trusted base-branch/org policy. Không thực thi
command được lấy trực tiếp từ PR hoặc model. Với nhiều ngôn ngữ/framework, Keploy
worker không đổi; chỉ target provisioner và command profile thay đổi.

Native instrumentation có thể khác nhau giữa Windows và Linux. Production nên
test trên cùng OS/container model với worker thực tế trước khi dùng làm merge gate.

## 7. k6

### 7.1 Vai trò

k6 tạo tải và đo latency, throughput, error rate và threshold. Nó hỗ trợ:

- Smoke test.
- Load test.
- Stress test.
- Soak test.

### 7.2 Cài đặt

Cài k6 từ distribution chính thức và bảo đảm lệnh sau hoạt động:

```powershell
k6 version
```

Hoặc dùng Docker image:

```text
grafana/k6:<pinned-version>
```

### 7.3 Script trong target project

```javascript
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500"],
  },
};

export default function () {
  const response = http.get(`${__ENV.BASE_URL}/health`);
  check(response, { "status is 200": (r) => r.status === 200 });
  sleep(1);
}
```

### 7.4 Tích hợp worker

Performance worker nên:

- Ưu tiên script đã commit và được review.
- Giới hạn VUs, duration và total runtime theo capability/policy.
- Chỉ truyền `BASE_URL` do TargetProvisioner cấp.
- Parse k6 JSON output thành p95, error rate, request count và thresholds.
- Không cho Planner tự nâng smoke test thành stress/soak test.

## 8. Semgrep

### 8.1 Vai trò

Semgrep thực hiện SAST dựa trên rules. Nó hỗ trợ nhiều ngôn ngữ và trả finding
theo file/dòng.

### 8.2 Cài đặt

```powershell
python -m venv .venv-tools
.\.venv-tools\Scripts\python.exe -m pip install semgrep==<pinned-version>
.\.venv-tools\Scripts\semgrep.exe --version
```

Lệnh tích hợp điển hình:

```powershell
semgrep scan `
  --config <reviewed-ruleset> `
  --metrics off `
  --json `
  --error `
  <target-repo>
```

Production nên vendor/mirror ruleset đã review và pin revision. Không nên phụ
thuộc `--config auto` tại mỗi PR vì nó phụ thuộc network, telemetry và rules thay
đổi ngoài kiểm soát.

### 8.3 Adapter

Adapter cần parse JSON, chuẩn hóa severity, giữ `path/start_line`, tạo fingerprint
ổn định và giới hạn số annotation. Luôn có timeout; network/rule resolution không
được làm Check Run treo vô hạn.

## 9. Trivy

### 9.1 Vai trò

Trivy kiểm tra:

- Dependency/SCA từ manifest và lockfile.
- Container image.
- IaC/misconfiguration.
- Một số secret/configuration class tùy scanner được bật.

### 9.2 Cài đặt

Tải executable từ official release và pin version:

```text
<qc-agent-root>/tools/trivy.exe
```

Kiểm tra và preload vulnerability database:

```powershell
trivy --version
trivy image --download-db-only
```

Filesystem SCA:

```powershell
trivy fs --scanners vuln --format json --exit-code 1 <target-repo>
```

### 9.3 Adapter và policy

Adapter parse `Results[].Vulnerabilities[]`, bao gồm:

- Vulnerability ID.
- Package name.
- Installed version.
- Fixed version.
- Severity.
- Target manifest.

Nên tách detection khỏi policy:

```yaml
security_policy:
  sca:
    fail_on: [critical, high]
    warn_on: [medium, low]
```

Nhờ vậy Trivy vẫn báo đầy đủ nhưng QC-Agent quyết định mức nào chặn merge.

Target project nên commit lockfile để kết quả dependency có tính tái lập.

## 10. Gitleaks

### 10.1 Vai trò

Gitleaks tìm API key, token, password, private key và credential pattern.

### 10.2 Cài đặt

Tải executable từ official release và pin version:

```text
<qc-agent-root>/tools/gitleaks.exe
```

Kiểm tra:

```powershell
gitleaks version
```

Lệnh tích hợp:

```powershell
gitleaks dir <target-repo> `
  --report-format json `
  --report-path <temporary-report> `
  --redact=100 `
  --no-banner `
  --exit-code 1
```

### 10.3 Bảo vệ dữ liệu

- Không đưa secret value vào model prompt, log hoặc PR review.
- Xóa report tạm sau khi parse.
- Chỉ công bố category/severity đã redact.
- Nếu phát hiện secret thật: revoke/rotate trước, sau đó xử lý history.
- Allowlist phải được security review; không allowlist để làm check xanh.

## 11. Strix

### 11.1 Vai trò

Strix là agentic DAST/pentest. Nó có thể lập kế hoạch nhiều bước, tương tác với
target và gọi security tools. Do đó nó có rủi ro và chi phí cao hơn scanner
deterministic.

### 11.2 Cài đặt

Chỉ cài trên dedicated worker VM/container host:

```powershell
python -m venv .venv-strix
.\.venv-strix\Scripts\python.exe -m pip install strix-agent==<pinned-version>
```

Biến môi trường:

```env
STRIX_LLM_API_KEY=<dedicated-key>
STRIX_LLM_MODEL=<supported-model>
STRIX_MAX_BUDGET_USD=<hard-cap>
STRIX_MAX_RUNTIME_SECONDS=7200
STRIX_AGENT_TOKEN=<transport-token>
```

### 11.3 Điều kiện an toàn bắt buộc

- Chỉ chạy schedule/manual được phê duyệt, không chạy mặc định trên mọi PR.
- Dedicated VM/network namespace.
- Target allowlist rõ ràng.
- Hard cap chi phí và thời gian.
- Không quét production nếu chưa có authorization.
- Không dùng chung Docker socket với untrusted PR worker.
- Thu thập artifact nhưng không tự mở remediation PR.

## 12. pytest và unit-test adapters

pytest là framework test của target project, không phải external agent. QC-Agent
chỉ gọi test suite có sẵn:

```powershell
python -m pytest -q
```

Tương tự cho ecosystem khác:

```text
Node.js: npm test
Maven:   mvn test
Gradle:  gradle test
Go:      go test ./...
Rust:    cargo test
.NET:    dotnet test
```

Adapter phải nhận dạng project từ manifest, dùng argument list cố định và không
đoán arbitrary shell command bằng LLM.

## 13. Docker Compose tham khảo

```yaml
services:
  hercules-worker:
    image: registry.example/agent-qc/hercules@sha256:<digest>
    env_file: [secrets/hercules.env]
    ports: ["8101:8101"]
    volumes:
      - qc-workspaces:/workspaces:ro

  keploy-worker:
    image: registry.example/agent-qc/keploy@sha256:<digest>
    ports: ["8102:8102"]
    volumes:
      - qc-workspaces:/workspaces

  k6-worker:
    image: registry.example/agent-qc/k6@sha256:<digest>
    ports: ["8104:8104"]
    volumes:
      - qc-workspaces:/workspaces:ro

volumes:
  qc-workspaces:
```

Semgrep, Trivy và Gitleaks có thể chạy trong orchestrator worker pool hoặc image
tool-runner riêng. Với code không tin cậy, ưu tiên ephemeral container/VM thay vì
chạy trực tiếp trên host.

## 14. Registry external agents

```yaml
version: "1"
agents:
  - agent_id: functional-hercules
    capabilities: [functional.api, functional.e2e]
    endpoint: http://hercules-worker:8101
    auth_secret_ref: secret://FUNCTIONAL_AGENT_TOKEN
    timeout_seconds: 600

  - agent_id: integration-keploy
    capabilities: [integration.service, contract.consumer_provider]
    endpoint: http://keploy-worker:8102
    auth_secret_ref: secret://INTEGRATION_AGENT_TOKEN
    timeout_seconds: 1200

  - agent_id: performance-k6
    capabilities: [performance.smoke, performance.load,
                   performance.stress, performance.soak]
    endpoint: http://k6-worker:8104
    auth_secret_ref: secret://PERFORMANCE_AGENT_TOKEN
    timeout_seconds: 3600
```

Chỉ thêm Strix vào registry của scheduled security environment, không thêm vào
registry mặc định của PR pipeline.

## 15. Cấu hình target project

Mỗi target repository nên khai báo quality intent, không khai báo credential:

```yaml
version: "1"
quality:
  functional:
    unit:
      enabled: true
    e2e:
      enabled: auto

  integration:
    service:
      enabled: auto
      parameters:
        fixture_path: keploy

  performance:
    smoke:
      enabled: auto
      parameters:
        script_path: performance/k6.js
        vus: 1
        duration: 10s

  security:
    secrets: {enabled: true}
    sca: {enabled: true}
    sast: {enabled: true}
```

Start command, network scope, maximum budget và security thresholds phải đến từ
trusted organization policy trên control plane. PR không được tự nâng quyền,
budget hoặc thay command để chạy code ngoài scope.

## 16. Trình tự triển khai đề xuất

### Giai đoạn 1: deterministic baseline

1. Unit-test adapters.
2. Gitleaks.
3. Semgrep với ruleset pin.
4. Trivy với DB/cache được quản lý.
5. Verdict và GitHub Check.

### Giai đoạn 2: target-aware testing

1. TargetProvisioner.
2. k6 smoke test.
3. Keploy fixture/replay.
4. Artifact retention và cancellation.

### Giai đoạn 3: agentic workers

1. Hercules với scenario/step cap.
2. Planner/Triage với structured output.
3. Strix trên isolated scheduled environment.

Không bật merge gate trước khi từng worker phân biệt được:

- Product failure.
- Infrastructure failure.
- Timeout/cancelled.
- Unsupported/skipped.

## 17. Checklist production

```text
[ ] Pin package/image/binary version hoặc digest
[ ] Xác minh license của từng upstream theo chính sách tổ chức
[ ] SBOM và vulnerability scan cho chính worker images
[ ] Secret tách riêng cho từng worker
[ ] Transport authentication giữa Orchestrator và worker
[ ] Immutable checkout theo commit SHA
[ ] Workspace allowlist và path traversal protection
[ ] shell=False / argument-list execution
[ ] Timeout, cancellation, retry có giới hạn
[ ] CPU, memory, PID và concurrency quota
[ ] Network egress policy
[ ] Security finding được redact trước khi publish/model prompt
[ ] Ruleset và vulnerability DB có provenance/version
[ ] Artifact retention và access control
[ ] Không để agent tự merge hoặc tự mở remediation PR
[ ] Strix chỉ chạy khi có authorization và isolation
[ ] Observability cho queue, worker latency, token/cost và failure category
```

## 18. Nguyên tắc chọn agent hay CLI

Sử dụng deterministic CLI khi input/output đã rõ và việc reasoning không tạo
thêm giá trị: unit test, SAST, SCA, secret scan, load execution.

Sử dụng agent khi cần thích nghi theo trạng thái target: khám phá UI, lựa chọn
chuỗi thao tác, phân tích kết quả nhiều bước hoặc pentest có scope. Dù dùng agent,
quyết định merge cuối cùng vẫn phải qua schema validation, organization policy và
deterministic VerdictEngine.

