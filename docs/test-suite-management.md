# Quản lý bộ test của TDA

Tài liệu này mô tả luồng baseline trước workspace schema 3.0. Luồng mặc định hiện tại được mô tả trong [Test Design Workspace](test-design-workspace.md).

## Luồng chính

Bộ test là đầu ra chính của TDA. Khi chọn repository, `/app` mở bảng test case của baseline mới nhất; repository chưa có baseline mở phần tài liệu nguồn. Bảng giữ các cột compact và bộ lọc; mở chi tiết khi cần xem toàn bộ steps, expected results và evidence. Blocker/coverage gap, dữ liệu kỹ thuật và lịch sử baseline nằm ở phần phụ.

| Action | Hành vi |
| --- | --- |
| Sinh bộ mới | Phân tích codebase và tài liệu được chọn từ default branch, lập lại toàn bộ suite. Không thay đổi baseline đang áp dụng. |
| Sinh bổ sung | Dùng knowledge/source của baseline đang xem, chỉ sinh scenarios và cases mới; giữ nguyên case cũ, không trùng ID hoặc nội dung runner/oracle. |
| Nhờ TDA chỉnh sửa | Sửa một case theo feedback, giữ ID và các case còn lại. |
| Sửa tay | Người dùng sửa title, priority, inputs/steps và expected results. Phần nâng cao hỗ trợ cấu trúc case JSON. Giữ case ID, scenario ID, kind và runner; dùng thêm case nếu cần đổi loại runner. |
| Thêm test case | Chọn một case làm mẫu, đặt ID/title/steps/expected results riêng. Scenario mới phải tham chiếu requirements có trong knowledge model. |
| Xóa | Loại case khỏi revision; tính lại coverage và kiểm tra dependency. |
| Review lại blocker | Giữ nguyên case, chạy lại Critic. |

Sinh bổ sung hiện pin cùng source revision với baseline gốc. Chưa tự reconcile requirements khi code hoặc tài liệu cũ thay đổi; cần sinh bộ mới từ source hiện tại. Bộ bổ sung không được tự tạo requirement ID ngoài knowledge model.

## Revision và approval

Mỗi action lưu thành một baseline draft bất biến mới. Baseline gốc giữ nguyên, có thể xem lại để đối chiếu. `review_changes.json` ghi case được thêm/sửa/xóa và số case giữ nguyên. Không tự merge nhiều draft.

Có thể tạo revision từ draft hiện tại hoặc baseline approved đang áp dụng. Không chỉnh baseline rejected; không nhận action khi project có job queued/running. API yêu cầu writer permission hiện tại, session và CSRF, kiểm tra manifest/hash trước khi đọc nguồn. Audit lưu actor, feedback và nội dung sửa.

Baseline approved tiếp tục được QC dùng cho PR trong lúc revision đang xử lý hoặc chưa được duyệt. Chỉ khi maintainer approve revision hợp lệ mới chuyển baseline đang áp dụng.

Sửa tay không gọi generator hoặc Oracle Engine để viết lại nội dung người dùng. TDA kiểm tra schema, traceability, oracle evidence, dependency và coverage; Critic dùng LLM review suite. Invalid schema bị API từ chối, lỗi cấu trúc/review có thể xuất hiện trong draft với blocker. Không coi phản hồi từ code ứng dụng là căn cứ để sửa expected result.

Acceptance criteria được nhập khi sửa/thêm case sẽ được lưu thành PRD source riêng của project, audit actor và dùng làm evidence specification cho oracle của case. Nếu để trống, giữ evidence hiện có; Critic vẫn có thể chặn khi expected result không được nguồn chứng minh hoặc xung đột requirement.

## Kết nối QC

Luồng vẫn là GitHub App → design job → package TDA → baseline registry → maintainer approval → QC execution trên PR. TDA mặc định không chạy code ứng dụng. QC pin baseline approved từ registry phía server, giữ nguyên oracle và dùng dispatcher HTTP/unit/browser/k6/AI.

TDA dùng `TDA_OPENAI_API_KEY`; QC dùng `OPENAI_API_KEY`. Sửa tay vẫn phát sinh lượt LLM ở Critic, không phát sinh lượt generator cho case đó.
