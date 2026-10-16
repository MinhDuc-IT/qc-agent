# Phân tích PRD và source theo module

## Context và checkpoint

Khảo sát nhận danh mục tóm tắt, không lặp toàn PRD. Mỗi module lấy tối đa 4 sections/9.000 ký tự, source excerpts dùng budget chung 9.000 ký tự và tool results 10.000 ký tự. Write/review dùng các citations duy nhất thay vì gửi lại toàn bộ files/sections; write nhắm tối đa 4 behaviors/cases còn thiếu.

Các requirements, flow nodes và behaviors được validate riêng rồi giữ trong investigation checkpoint. LLM nhận bản tóm tắt phần đã giữ và tối đa 3 mục lỗi cần sửa. Sau tối đa 2 studies bị từ chối, phần có evidence và scope phù hợp vẫn có thể chuyển sang sinh draft; phần chưa xác minh được giữ là câu hỏi/partial, không được certify là coverage. Scope unclear/mismatch không tự chuyển thành matched. Checkpoints và feedback được giữ khi tiếp tục cùng source revision.

Review tách batch tối đa 4 cases; mỗi batch có hash của study và cases. Batch không đổi được tái sử dụng, thay đổi nội dung phải review lại. Approval/publish vẫn do maintainer thực hiện.

`AGENT_QC_TDA_MAX_INPUT_CHARS=60000` chặn request quá lớn trước khi gửi provider, không tiêu một call LLM. Đây là budget ký tự JSON, không phải token count. Log lưu input_chars, input/output tokens và cached_input_tokens khi provider trả usage. Không tự tăng call budget.

TDA đọc/index toàn bộ tài liệu nguồn đã chọn trước khi khảo sát module. Markdown có ID FR/NFR/REQ/BR/AC được ghi nhận nguyên văn cùng source ID, vị trí dòng và section; tài liệu không có ID vẫn có section context. Đây là catalog đầu vào, không phải số requirements đã được LLM phân tích hoặc bằng chứng coverage. Cùng ID nhưng khác nội dung được giữ là mâu thuẫn cần xác nhận.

Khảo sát nhận đủ danh sách yêu cầu tường minh, section index, repo tree và API inventory. Điều tra từng module nhận relevant sections trong giới hạn context, requirement IDs và static inventory của các file liên quan. Handler Python được đọc thêm các khoảng dòng phía sau và lần theo imports cục bộ với giới hạn depth/files. Source/Markdown là dữ liệu, không phải instructions.

Module có tối thiểu 6 calls trong allowance nếu job còn budget; hard cap của job vẫn giữ nguyên. TDA không mặc định coi helper triển khai rule của chính module là ngoài scope. Requirements có ID trong PRD giữ nguyên ID; code-only requirements dùng prefix module. UI hiển thị riêng catalog, phân tích theo ID và case mappings; static scan không chứng minh runtime coverage.

Timeout TDA mặc định 180 giây, cấu hình bằng `AGENT_QC_TDA_TIMEOUT_SECONDS` (10–600). SDK không retry ngầm. Orchestrator có tối đa một retry timeout/rate-limit cho stage nếu còn allowance; các calls đều tính vào budget. Module bị timeout được lưu partial và các modules khác vẫn được xử lý trong budget. Khi tiếp tục cùng source/analysis version, TDA có thể resume bước review mà không sinh lại cases. Workspace tạo bằng engine trước được khảo sát lại với PRD đầy đủ; cases cũ vẫn được cung cấp để tránh trùng.

Source ID sai chỉ được phục hồi nếu exact quote khớp duy nhất trong nguồn đã đọc. Provenance sửa từ specified sang implementation-derived chỉ khi toàn bộ evidence là code/schema/existing tests hợp lệ. Không đổi expected result hoặc coi code hiện tại là ground truth nghiệp vụ.

24 calls không bảo đảm xử lý đủ repo 11 modules trong một lượt: riêng investigate/write/review đã cần ít nhất 33 calls nếu mọi module kết thúc điều tra sau một call. Cần tiếp tục từ checkpoints hoặc chủ động cấu hình budget phù hợp; hệ thống không tự tăng budget hay certify phần chưa phân tích.
