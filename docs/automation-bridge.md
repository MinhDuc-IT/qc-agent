# Từ test design đến QC baseline

1. Chọn và duyệt cases có steps/expected results rõ, đã xử lý blocker.
2. Bấm **Chuẩn bị cho QC**. Job riêng dùng TDA key/model để sinh declarative executable plans; không sinh lại test design và không chạy ứng dụng ở bước này.
3. Xem **Sẵn sàng cho QC**: chờ xác nhận mapping, thiếu điều kiện, test thủ công hoặc chưa xử lý do budget. Mở case để xem runner, inputs, assertions và mapping từng expected result.
4. Chuẩn bị fixtures/điều kiện được báo. Xác nhận assertions đúng với expected results đã duyệt; điều kiện runtime thiếu không được bỏ qua bằng một lý do review. Mapping đủ và schema/evidence hợp lệ mới được xác nhận.
5. Chọn cases sẵn sàng và bấm **Áp dụng cho QC**. Chỉ phạm vi được chọn trở thành baseline mới. Các cases manual/thiếu điều kiện vẫn nằm trong bộ test design, không được tính là tests đã chạy.
6. Khi có PR, GitHub webhook kích hoạt QC; orchestrator pin baseline version trong registry, workers chạy executable plans trên checkout code PR và báo verdict theo case ID.

**Lưu phiên bản bộ test** chỉ lưu snapshot cases đã duyệt và không cập nhật QC baseline. **Áp dụng cho QC** là action riêng, yêu cầu mọi case được chọn có binding đã xác nhận khớp revision/hash hiện tại. Sửa case làm binding cũ không còn sẵn sàng. Baseline đang dùng cho PR là artifact bất biến; job chuẩn bị không tự thay baseline.

## Executor và giới hạn

- HTTP: requests, cleanup và assertions từ executor hiện có.
- Unit: Python/JavaScript function calls; không arbitrary shell hoặc test script do LLM cung cấp.
- Browser: Playwright actions/selectors và DOM assertions.
- Load: k6 plan với SLO có nguồn; không tự đặt latency mục tiêu.
- AI eval: requests, ground truth/rubric có nguồn.

DB/mail fixtures, tài khoản test, token, dependencies, selector chưa xác định hoặc response captures không được tự invent. Executor chưa có fixture/capture adapter thì case được ghi thiếu điều kiện hoặc manual. HTTP target chỉ tự phát hiện khi adapter hiện có hỗ trợ chạy tại repo root. Với service nằm trong subdirectory, DB/email phụ thuộc môi trường, cần adapter runtime tương ứng trước khi sẵn sàng. Việc review mapping là xác nhận nội dung và prerequisites; chưa chứng minh tests đã chạy pass.

Artifacts job: `automation_report.json`, `test_workspace.json`, `llm_outputs/*`. Workspace lưu `automation_preparation` và bindings có mapping expected result → executable assertion. Baseline nằm trong `.data/test-design/projects/<project_id>/baselines/<version>`; registry SQLite lưu `active_baseline`, QC không lấy bản draft mới nhất.

Tests integration dùng fake LLM và chạy unit baseline trên code gốc/code có regression để kiểm chứng verdict pass/fail. Không tự mở PR hay kích hoạt baseline thật của người dùng.
