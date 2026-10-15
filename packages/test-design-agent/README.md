# Test Design Agent — LLM workflow v2

**GitHub App mode:** TDA is now integrated inside the QC-Agent repository with automatic
onboarding, durable jobs, browser review and a managed baseline registry. Repository users
do not need the CLI steps below. See [App onboarding](../../docs/github-app-test-design.md).
The commands below remain useful for standalone design and development.

Test design now includes both documented requirements and codebase behavior. Requirements,
scenarios and assertions carry `expectation_basis`: `specification` requires exact evidence
from PRD/SRS/OpenAPI; `implementation` requires exact code/schema/existing-test evidence.
Implementation assertions characterize current behavior and require maintainer review;
they do not prove business correctness. Explicit specifications take precedence. Code-only
projects can onboard without a PRD. Execution output never supplies expected results.
Performance acceptance thresholds still require normative specification evidence. The current
runner executes HTTP/JSON cases; browser interaction and load tests need additional adapters.

Each discovered requirement now declares `execution_scope` (`http`, `browser`, `unit`,
`dependency`, `load`) and a reason when deferred. HTTP coverage explicitly excludes deferred
requirements and reports them as untested; it never claims full-project coverage. Dependency
manifests and existing k6 scripts cannot create mandatory HTTP coverage targets. Informational
`open_questions` remain review notes; `blocking_questions`, invalid HTTP cases, missing oracles,
insufficient HTTP coverage and critic blockers still prevent approval. Existing baselines are
immutable and must be regenerated to use this scope policy.

Agent độc lập phân tích nguồn dự án, lập kế hoạch và sinh baseline kiểm thử bằng LLM.
Kiến trúc gồm domain, application và infrastructure, với các stage riêng cho Analyzer,
Planner, năm Specialized Generators, Oracle, Validator, Repair, Coverage và Critic.
Xem [kiến trúc](docs/architecture.md) và [hướng dẫn LLM/QC](docs/llm-workflow.md).

LLM dùng OpenAI Responses API với structured output Pydantic. Không có fallback ngầm:
thiếu API key, refusal, timeout hay output không hợp lệ đều tạo lỗi rõ ràng.
Kiểm tra evidence, references, dependency, coverage và approval được thực hiện bằng code.
Không suy diễn expected result từ output sản phẩm.

## Cài đặt và chạy

Từ thư mục `test-design-agent`, Python 3.11+:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Điền OPENAI_API_KEY trong .env và chọn model trong examples/design.yaml.
.venv\Scripts\python -m tda design examples/design.yaml --env-file .env
```

Lệnh design in thư mục run và baseline. Không có `--base-url` thì chỉ thiết kế,
trạng thái chờ execution; không được approve. Dùng đường dẫn in ra thay `<baseline>`:

```powershell
python -m tda validate <baseline>
python -m tda run <baseline> --base-url http://127.0.0.1:8080 --report reports/reference.json
python -m tda approve <baseline> --reviewer your-name --report reports/reference.json
python -m tda export-qc <baseline> --project-id backend --run-id run-123 --external-agent
```

Ví dụ LLM nằm trong `examples/project/`, gồm PRD và OpenAPI cho `/health` và `/api/add`.
`approve` là hành động review rõ ràng của người vận hành; generator không tự duyệt.
Approval chỉ chấp nhận report đủ mọi case, đúng baseline version, pass, không có design blockers,
validation issues hoặc critic blockers, và đạt coverage policy.
Report là bằng chứng local do người vận hành cung cấp, chưa có chữ ký hay cơ chế xác thực reviewer.

## Artifact

- `sources.json`: snapshot nguồn được chọn và checksum.
- `knowledge.json`: yêu cầu, features, dependencies, risks và evidence.
- `test_plan.json`: scenario, priority, risk, oracle source và câu hỏi còn thiếu.
- `cases/cases.json`: test case có cấu trúc và oracle.
- `tests/`: pytest và HTTP runtime cố định, không phụ thuộc package `tda` khi chạy.
- `traceability.json`: requirement → test IDs.
- `ground_truth/oracles.json`: ground truth có evidence cho AI evaluation.
- `validation_report.json`: kiểm tra contract, request, oracle, dependency và executability.
- `coverage.json`: requirement, feature, risk và API coverage, tách thiết kế và execution.
- `critic_review.json`, `review_gate.json`: chất lượng và blocker cần giải quyết.
- `test_manifest.yaml`: baseline version, checksum, entrypoint và trạng thái review.

Runtime v2 kiểm tra HTTP status, JSON pointer equality/contains và latency smoke SLO;
chạy integration steps, dependencies và cleanup. Redirect không được tự đi theo.
Kết quả gồm `pass`, `defect`, `environment_error`, `blocked`, `invalid`;
repair chỉ sửa request không hợp lệ, giữ nguyên oracle, không sửa product defect.
POST/PUT/PATCH/DELETE cần `--allow-mutations`, chỉ chạy trên môi trường test có dữ liệu và cleanup thích hợp.
Token tùy chọn lấy từ `TDA_AUTH_TOKEN`, không lưu vào artifact.

## Bàn giao QC-Agent

Khuyến nghị dùng HTTP worker với capability `baseline.http` đã thêm vào catalog QC-Agent.
Worker nhận baseline version, lấy artifact đã duyệt từ `TDA_BASELINE_ROOT`, kiểm tra checksum
và chạy against target do QC provision. Không lấy test từ PR checkout.
Đăng ký worker bằng `examples/external-agents.baseline.yaml` và bật quality intent theo
`examples/agent-qc.baseline.yaml` trong config tại base SHA. Xem hướng dẫn chi tiết.

Đường tương thích pytest vẫn có: bỏ `--external-agent`, đặt `--relative-root baseline`.
Đường này xuất `ExecutionPlan` 1.1 dùng `unit.run` và chỉ thích hợp với project Python.

Copy baseline đã duyệt vào checkout dự án Python tại đường dẫn `--relative-root`;
chạy integrity validation trên bản copy trước khi thực thi. QC runtime cần pytest và
biến môi trường `TDA_BASE_URL` trỏ tới service của revision đang kiểm tra.
Đảm bảo project ID khớp ProjectDescriptor của QC. Với HTTP worker, QC planner đã đọc
quality intent và baseline version từ cấu hình; bạn cần đăng ký/start worker và provision target.
Chưa có cơ chế tự chọn latest baseline hoặc test selection theo diff.

Chạy độc lập:

```powershell
$env:TDA_BASE_URL = 'http://127.0.0.1:8080'
python -m pytest <baseline>/tests/test_baseline.py -q
```

## Kiểm chứng

```powershell
python -m pytest tests -q
```

Tests khởi động HTTP server local, kiểm tra pass, đưa lỗi số học vào để kiểm tra phát hiện
regression, chạy pytest đã sinh và xác nhận contract với QC-Agent.

## Tương thích v1

`python -m tda build examples/requirements.yaml` vẫn hỗ trợ YAML scenario/oracle
khai báo, không gọi LLM. v1 dùng `/add`, khác endpoint của ví dụ LLM.

## Giới hạn và phần mở rộng

1. Adapter load/concurrency cho performance và rubric/LLM-judge cho AI evaluation.
2. Fixture capture/substitution, DB assertions, UI và non-HTTP runners.
3. Code coverage instrumentation, signed artifacts và authenticated human review.
4. Durable worker queue, test selection theo diff và baseline lifecycle management.

Worker HTTP đã có submit/poll/cancel, bearer auth, giới hạn active tasks và bounded HTTP execution;
job store hiện nằm trong memory, cancellation hợp tác giữa request (request đang chạy có timeout 10s).
Không có sandbox, scheduler, code coverage hay artifact signing.
Hash kiểm tra thay đổi nội dung, không xác thực nguồn phát hành. `source_revision` là metadata
người dùng cung cấp, chưa xác minh với Git. Coverage phản ánh các entities được Analyzer trích xuất,
không chứng minh Analyzer đã đọc đủ yêu cầu. Người review cần kiểm tra đầy đủ nguồn, oracle và fixtures.
Baseline chưa đạt policy được lưu draft để review, không được approve hoặc dùng làm merge gate.
