# TDA đọc project theo luồng nghiệp vụ

TDA mới dùng vòng lặp LLM + repository tools để đọc tài liệu và lần theo code của từng module. Engine workspace cũ đã được cất trong `.data/retired-tda`; không được gọi bởi backend mới.

## Luồng hoạt động

Install App chỉ đăng ký repository. Người dùng chọn file/folder tài liệu hoặc upload, rồi bấm Sinh test. Backend lấy default branch thành immutable checkout, đọc các tài liệu được chọn và đưa danh sách file cho LLM khảo sát modules.

Với mỗi module, agent có thể gọi `list_files`, `search`, `read_file`, `find_references`. Các tools chỉ đọc, dùng `rg` cho text search và có fallback khi không có rg. References là text matches, không được coi là call graph đã chứng minh. Agent đọc tiếp từ UI/API qua validation, authorization, service/model, DB/dependencies và existing tests trước khi kết luận.

Agent tạo ModuleStudy gồm requirements, acceptance criteria, các bước luồng xử lý, behaviors/conditions/outcomes và nguồn trích dẫn. Các behaviors được phân loại:

- `specified`: kỳ vọng theo tài liệu.
- `implementation_derived`: hành vi quan sát trong code; không phải bằng chứng code đúng nghiệp vụ.
- `missing_implementation`: yêu cầu có nguồn nhưng chưa tìm thấy phần triển khai trong phạm vi đọc.
- `conflict`: tài liệu và code khác nhau, cần review.
- `unclear`: thiếu căn cứ để chốt expected result.

Writer sinh tối đa tám human-readable cases trong một batch. Case có ID, objective, requirement/criterion IDs, preconditions, test data, steps, expected results, type, priority và evidence. Không tạo runner/test scripts ở bước này; expected result đọc được và kiểm chứng được là trọng tâm, không ép mọi case thành HTTP/JSON assertions.

Reviewer là LLM task riêng, không nhận kết luận coverage của writer. Nó đối chiếu study/source với cases, đánh giá semantic duplicate, oracle, input, mâu thuẫn và nhánh còn thiếu. Mỗi link được xác minh chỉ rõ behavior ID, case ID, step indexes, criterion IDs và evidence. Code tính coverage và lưu hash của case để làm mất hiệu lực review cũ khi nội dung/revision thay đổi.

Case không được tính coverage chỉ vì có requirement ID. Evidence phải khớp nguyên văn phần source đã đọc qua tools. Schema/ID mapping, nguồn được đọc và bounds do code kiểm tra. LLM không tự approve hoặc thay ground truth để tăng score.

## Scope, gaps và giới hạn

Coverage chỉ phản ánh requirements/branches đã khảo sát, không chứng minh đã tìm được mọi tính năng hoặc mọi lỗi trong repository. Các module chưa xử lý, câu hỏi còn mở, mâu thuẫn, scope exclusions và giới hạn tools/budget luôn được hiển thị. Context được cấp theo từng module; tools giới hạn đoạn đọc 120 dòng/12.000 ký tự, source excerpts cho writer/reviewer tối đa 32.000 ký tự. Files quá lớn và tree bị truncate được báo, không giả vờ đã đọc đủ.

Ngân sách LLM được dùng chung trong job. Trước writer, engine dành ít nhất một lượt gọi cho independent review. Khi hết budget/turn/case limit, các cases và module study đã có được lưu thành draft partial, những modules còn lại là pending. Job xử lý xong không đồng nghĩa suite đầy đủ.

Những `.env`, private key files, directories như `.git`, `.aws`, `.codex`, symlinks và đường dẫn thoát checkout không được repository tools đọc. Không execute source hoặc command do LLM đưa ra. Source documents là dữ liệu, không phải instructions.

## Sử dụng UI

1. Chọn tài liệu nếu cần và Sinh test. Có thể dùng codebase khi chưa có PRD; kỳ vọng theo code phải được ghi rõ.
2. Xem bảng cases. Mở case để xem/sửa preconditions, data, steps và expected results.
3. Mở Modules để xem flow UI → API → logic/data, source quotes, phân loại tài liệu/code, câu hỏi và nhánh còn thiếu.
4. Bấm Bổ sung luồng này hoặc Review luồng này để xử lý đúng module đã chọn. Sinh bổ sung giữ nguyên cases đang có; AI edit tạo proposal để người dùng áp dụng.
5. Nội dung human-edited và approved không bị writer ghi đè. Các phiên bản/source snapshots trước vẫn được giữ để audit. Đổi nguồn là action riêng và yêu cầu đọc lại code.
6. Review và publish là action của maintainer. Automation binding/test scripts thuộc bước riêng sau khi kỳ vọng đã rõ; TDA mới không chạy ứng dụng. QC vẫn có thể dùng baseline/bindings đã approved từ trước.

## Files và cấu hình

- `tda/domain/investigation.py`: schemas module, study, tool requests, writer và reviewer.
- `tda/infrastructure/repository_tools.py`: read-only tools và observed evidence.
- `tda/application/flow_design.py`: survey, investigate, write, review và deterministic report.
- `app/infrastructure/test_design/engine.py`: adapter GitHub/job/source/persistence.
- `TestWorkspace.design_method = flow_investigation`, `flow_modules`, `investigation_survey`, `investigation_limitations`: dữ liệu UI mới.
- `TDA_OPENAI_API_KEY`, `AGENT_QC_TDA_MODEL`: key/model riêng TDA; key không lấy từ QC.
- `AGENT_QC_TDA_MAX_CALLS`: ngân sách gọi LLM của job.
- `AGENT_QC_TDA_MAX_INQUIRY_TURNS`: tối đa lượt điều tra mỗi module, mặc định 6.
- `AGENT_QC_TDA_MAX_ROUNDS`: vòng writer/reviewer theo module.
- `AGENT_QC_TDA_MAX_CASES`: giới hạn cases, không phải mục tiêu completeness.

Artifacts có `tools/*.json` (request/results), `modules/{id}/round-*.json`, `llm_outputs/*`, `test_workspace.json`, `flow_report.json`. SQLite lưu version và actor, kiểm tra concurrency/superseded jobs trước khi cập nhật workspace. Không tự thay active QC baseline.

## Kiểm chứng

## Phân bổ budget và chống trùng

Mỗi lượt đi qua các modules với một vòng writer/reviewer trước khi quay lại bổ sung. Ngân sách phân tích từng module được chia từ số calls còn lại; giữ calls cho writer và independent reviewer. `supplement` ưu tiên modules chưa có cases. Giới hạn 24 calls không đảm bảo hoàn tất repo có 10 modules: cần ít nhất một call phân tích, một call sinh và một call review cho mỗi module, chưa tính survey/tools/correction. Khi thiếu budget, lưu `partial` và danh sách modules chưa xử lý.

Context phân tích chỉ chứa tài liệu và source đã đọc trong module hiện tại; entry points được đọc trước. Study phải khớp module và dẫn chứng entry code. Shared dependencies không trở thành tính năng chính của module khác. Writer nhận đầy đủ cases liên quan; dùng cùng ID để đề xuất sửa, không tự ghi đè case hiện có.

Independent reviewer trả các `duplicate_groups` có canonical case, IDs trùng và exact evidence cho một behavior/condition/outcome/test level. Candidates mới bị xác nhận trùng được chuyển thành đề xuất sửa canonical case. Cases tồn tại, đặc biệt human edits/approved, không tự bị xóa hay hợp nhất. Warning gom nhiều cặp khác nhau không đủ để tự loại cases. UI có danh sách duplicates, đề xuất sửa và action review/loại từng case. Progress hiển thị module, số modules có cases và calls đã dùng/còn lại.

Tests kiểm tra agent tìm rồi đọc file, evidence phải đã đọc, path/secret boundaries, review độc lập, case edit làm mất coverage cũ, giữ human edits, lưu partial khi hết budget, GitHub App không tự sinh, API authorization/CSRF/module selection và browser table/module rendering. Integration tests dùng fake structured LLM; không tiêu token production hoặc execute target app.
