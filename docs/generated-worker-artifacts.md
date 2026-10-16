# Generated tests qua QC workers

## Luồng pytest đầu tiên

`Cases đã duyệt → execution profile → pytest artifact → static checks → independent review → preflight → maintainer xác nhận → immutable baseline → PR worker`.

Generated pytest chạy qua `GeneratedPytestAdapter` và `RegisteredWorkerRuntime`, không qua HTTP declarative executor. Existing tests vẫn chạy trên checkout gốc. Generated tests được overlay trong bản copy riêng và pytest chỉ chạy selectors trong baseline, tránh chạy trùng hoặc sửa working tree gốc.

## Contract và profile

`ExecutionProfile` lưu repo-relative root, Python version, dependency/fixture files, required environment, services và migrations. Detector đọc manifest và fixtures bằng code, không import ứng dụng. Không có nhánh theo tên repo. Một profile khai báo rõ có thể nằm ở `.qc/execution.yaml`:

```yaml
schema_version: "1.0"
profiles:
  - root: backend
    runner: pytest
    python_version: "3.14"
    test_root: tests
    dependency_files: [backend/pyproject.toml]
    fixture_files: [backend/tests/conftest.py]
    required_environment: [DATABASE_URL, SECRET_KEY, PROJECT_NAME, FIRST_SUPERUSER, FIRST_SUPERUSER_PASSWORD]
    services: [postgres]
    migrations: true
```

Contract đầu tiên giới hạn test root là `tests`; artifact nằm ở `tests/.qc_generated/test_<case-hash>.py::test_case`. Test có thể dùng conftest, TestClient, factories, monkeypatch/mocks và pytest.raises. Mapping nối mỗi expected result với assertion lines đã được reviewer kiểm tra. Static AST checks không phải security sandbox hay bằng chứng semantic đầy đủ; code và mapping vẫn cần maintainer review.

## Môi trường

Mặc định Docker: tái sử dụng Dockerfile của repo khi xác định được build context/working directory; nếu không thì dựng Python version theo profile và dependencies từ uv.lock/pyproject/requirements. Dockerfile nhiều stage có thể cung cấp frontend build/backend cần dùng khi import app. Profile lưu `dockerfile` và `container_root` làm bằng chứng, không thêm nhánh theo tên repo.

Runtime dùng network test riêng, PostgreSQL riêng, Alembic migration nếu khai báo. Test credentials được sinh cho sandbox; không copy `.env`, private keys hoặc truyền QC/GitHub/LLM keys vào test process. Containers, anonymous volumes, network và image của run được cleanup. Giai đoạn image build cần network để tải dependencies; network test bị giới hạn vào services nội bộ.

`local` chỉ là chế độ pilot/test có chủ ý; không dùng với profile cần services. Cần Docker daemon hoạt động và khả năng tải images/dependencies để dùng môi trường mặc định. Unknown required environment hoặc thiếu fixtures/dependencies được báo blocked/needs_setup, không được coi là product failure.

## Review, publish và kết quả

Preflight `pass` hoặc `defect` là kết quả quan sát trên source chuẩn bị. Agent không sửa expected results để pass. Maintainer phải review artifact, profile, fixtures và outcome; preflight blocked không được xác nhận sẵn sàng.

`Áp dụng cho QC` lưu bundle hash dưới registry server. Worker trên PR kiểm tra installation/repository, artifact hashes, approved case revision và reviewer trước khi chạy. JUnit được map về case ID. Assertion failure là defect; collection/fixture/runtime errors hoặc missing/skipped results là blocked/unknown. Kết quả có nhãn generated và so sánh với preflight; baseline đã fail không tự được gọi là regression mới.

Baseline declarative cũ vẫn hoạt động. Bundle mới chứa đồng thời pytest artifacts và reviewed plans cho HTTP, unit, Playwright, k6 và AI evaluation. Mỗi case có đúng một binding, khớp revision/hash và được maintainer xác nhận. Dependencies chỉ được nằm trong nhóm plans có thể thực thi cùng nhau; chưa hỗ trợ dependency nối pytest artifact với runner khác.

Phân tuyến dựa trên loại case và source paths. Repo có Python profile không có nghĩa mọi case được đưa vào pytest: UI, performance và AI vẫn đi theo runner tương ứng. Flag `AGENT_QC_GENERATED_PYTEST_ENABLED=false` giữ đường chuẩn bị declarative cũ.

Review pytest dùng contract khóa ổn định `expected_key` và nguyên văn `assertion_sources`. Reviewer không trả index/số dòng; server phân giải bằng AST và chặn assertion không khớp duy nhất. Expected result không thay đổi khi chuẩn hóa tên entry point hoặc mapping.

## External workers

Binding JSON có `worker` (`auto`, `hercules`, `keploy`, `strix`) và `worker_parameters`. Sau khi sửa route phải xác nhận lại binding. `auto` dùng fixed runner tương ứng; external route được pin trong bundle và chọn đúng worker trong registry, không tự fallback sang worker khác.

- Hercules nhận browser actions và exact approved assertions dưới dạng feature, một task cho một case.
- Keploy cần recordings thật, `keploy_path`, `sut_command` được review và `recording_sha256` (map path tương đối bên trong keploy_path tới SHA-256). Hash inventory phải khớp checkout; PR không được tự thay oracle trong recordings.
- Strix chỉ chạy scheduled/nightly, budget explicit tối đa 5 USD và vẫn bị giới hạn bởi cấu hình worker. PR không kích hoạt Strix.

Keploy bridge đối chiếu từng approved request với actual observations từ report mới, rồi chạy lại approved assertions trên actual status/body. Không dùng recording response làm kết quả thực tế; report cũ hoặc matching không duy nhất bị blocked. Hiện hỗ trợ status, JSON và text assertions; auth/cleanup/latency cần bằng chứng riêng nên bị blocked. Report parser tham chiếu [Keploy TestReport/TestResult](https://github.com/keploy/keploy/blob/main/pkg/models/testrun.go), schema khác không được suy đoán.

Strix scan là bằng chứng bổ sung. Sau scan hoàn tất, security HTTP assertions chỉ đọc được kiểm chứng riêng trên test target; scan findings được giữ độc lập trong QC report. Case thay đổi dữ liệu cần fixture/reset sau scan nên bị blocked. Không biến scanner/replay pass thành case pass khi thiếu actual assertion evidence.

AI evaluation cần rubric, test target và judge được cấu hình riêng trên QC. Không tự gọi model trong kiểm thử local nếu không có cấu hình/chấp thuận phù hợp. Môi trường repo thiếu dependencies, fixture, test target hoặc credential test được báo blocked; chưa có adapter tự dựng được mọi loại repo.

## Kiểm chứng

Integration tests dùng LLM giả có structured output để kiểm tra fixture reuse, preflight, review, publish, PR regression và chống sửa bundle. Smoke test Docker repo thật dùng test xác định trước, không thay bộ test hoặc active baseline. Smoke test gửi private source/tài liệu qua LLM cần chấp thuận cụ thể trước khi chạy.

Smoke test trên `full-stack-fastapi-template` đã chạy thành công case login sai mật khẩu bằng form-urlencoded, tái sử dụng fixture `client`, Python 3.14 + PostgreSQL + Alembic + frontend build từ Dockerfile. Kết quả lưu ở artifact smoke riêng, không phải baseline production đã duyệt.
# Chuẩn bị và sửa pytest artifacts

Context chuẩn bị bao gồm fixtures, models, routes, existing tests và các Python imports liên quan, trong giới hạn số file và dung lượng. Các đoạn source không còn chỉ lấy 120 dòng đầu. Fixture không tồn tại và login dùng tài khoản cố định chưa được tạo sẽ bị chặn trước preflight.

Mapping được đối chiếu với assertion AST để chấp nhận khác biệt định dạng; assertion phải khớp duy nhất, không thay đổi biểu thức hoặc expected result. Script/mapping lỗi có tối đa một lần retry sau bản đầu; sau preflight có tối đa một vòng repair script/setup. Mỗi lần đều cần review độc lập và tuân thủ call budget của job.

Generated artifacts chạy với sandbox riêng cho từng case, bao gồm DB và SMTP nếu profile yêu cầu. Việc này tránh session-scoped fixtures làm rò dữ liệu giữa cases; đổi lại có thêm thời gian khởi tạo môi trường. Kết quả từng case giữ output riêng. `defect` trong preflight chỉ là assertion không đạt, chưa đủ để tự kết luận lỗi sản phẩm.

Chuẩn bị lại giữ artifacts đã PASS nếu case hash và source revision còn khớp.

## Luồng Bật cho QC

Luồng chính: Sinh test → review/chỉnh sửa → Duyệt → Bật cho QC → tạo PR.
`POST /workspace/enable-qc` tạo job có actor, case IDs, workspace version, lý do và chấp thuận coverage gap.
Job tự chuẩn bị script và mapping, review độc lập, chạy preflight rồi lưu publication và kích hoạt baseline.
Artifact được tự xác nhận dưới danh tính `system:enable-qc:<actor>` chỉ khi case đã duyệt, hash/revision khớp, không còn blocker, mapping hợp lệ và preflight PASS. Không tự sửa expected results đã duyệt.

Nếu có cases bị chặn, chỉ kích hoạt subset khi người dùng đã chấp nhận coverage gap; nếu không có case đạt điều kiện thì giữ baseline đang dùng. Baseline mới thay thế baseline cũ theo phạm vi được chọn. Publication và baseline được ghi trong transaction với kiểm tra job hiện hành, scope và version.

Các thao tác bind, xác nhận mapping và publish riêng vẫn có trong mục kỹ thuật cho quy trình cũ. Runner plans chưa có preflight tự động không được tự xác nhận chỉ vì schema hợp lệ. Thiếu live runner/fixtures vẫn là blocker, không hạ chuẩn integration thành mock để PASS.

Profile discovery nhận cả `psycopg` và `asyncpg`, dataclass environment và test-only database guard. Mỗi artifact chạy trong DB riêng, dùng driver đúng và cài dev extras khi repo khai báo. Lỗi hạ tầng được chạy lại với script đã review, không dùng LLM viết lại script. Thiếu capability đã xác định được giữ theo source/case/profile để lần Bật cho QC sau không gọi LLM lặp lại; thao tác bind kỹ thuật cho phép điều tra lại.
