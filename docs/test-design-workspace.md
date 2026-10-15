# Test Design Workspace — TDA mới

GitHub App mặc định sinh test case nghiệp vụ độc lập với runner, dùng workspace schema `3.0`. Baseline executable cũ được giữ và có thể nhập vào workspace. QC dùng baseline approved hiện hành cho đến khi publish một automation subset mới.

## Kiến trúc

```text
Tài liệu repo/upload + codebase + existing tests
  → Source snapshot → Requirement Analyzer → Strategy Planner
  → Test Case Writer → Quality Reviewer → Versioned workspace
  → Human review → Published design suite
  → Validated automation bindings → Approved QC baseline
```

- `tda/domain/workspace.py`: DesignCase, ExpectedResult, Criterion, Strategy, Finding, ReviewDecision, ReusableStep, AutomationBinding và TestWorkspace.
- `tda/application/workspace.py`: các stage LLM `analysis`, `workspace.strategy`, `workspace.design`, `workspace.review`, `workspace.bind`; validation và coverage bằng code.
- `app/infrastructure/persistence/design_workspace.py`: version snapshots, optimistic concurrency, publication registry và audit.
- `app/api/design_workspace.py` / `design_workspace.js`: API, bảng test case và editor.
- `app/infrastructure/test_design/workspace_publish.py`: chuyển approved design/bindings thành baseline QC.

Case không bắt buộc có runner. Expected result có provenance `specified`, `implementation_derived`, `maintainer_confirmed`, `unresolved`. Assertion type/pointer/JSON là metadata tùy chọn phục vụ automation. Code không tự trở thành yêu cầu nghiệp vụ.

## Luồng người dùng

Install GitHub App chỉ đăng ký repository ở trạng thái **Chưa có bộ test**; không tạo design job hoặc gọi LLM. Người dùng chọn tài liệu nếu cần, rồi bấm **Lưu và sinh test** để bắt đầu. Đồng bộ installation khi backend khởi động, lần đầu nhận PR và push vào repo chưa bắt đầu sinh test cũng không kích hoạt TDA. Sau khi người dùng đã bắt đầu, cập nhật default branch tiếp tục dùng luồng source update hiện có.

| Action | Hành vi |
| --- | --- |
| Sinh bộ mới | Đọc default branch và tài liệu đã chọn, tạo suite mới; giữ version trước trong history. |
| Sinh bổ sung | Dùng snapshot/knowledge đã lưu, thêm cases; giữ case cũ và nội dung sửa tay. |
| Cập nhật nguồn | Đọc source mới, giữ case cũ, đánh dấu ảnh hưởng và tạo proposals để review. |
| Nhờ AI sửa | Lưu proposal riêng; chỉ đổi case khi người dùng áp dụng. |
| Sửa tay / thêm case | Editor cho objective, preconditions, data, steps, expected results, references, priority, technique, assumptions và questions. Lưu không gọi LLM. |
| Thêm requirement | Lưu acceptance criteria do maintainer cung cấp thành source và catalog. |
| Ngừng dùng / khôi phục | Giữ case, history và lý do; tính lại coverage. |
| Review | Duyệt từng case hoặc nhóm case; chỉnh nội dung/criteria, trả lời câu hỏi và ghi quyết định cho finding. |
| Binding | AI đề xuất hoặc người dùng sửa; xác nhận mapping expected results riêng. |
| Publish | Chọn cases approved, ghi scope/exclusions và xác nhận coverage gap nếu có. |

Repo chưa có suite có thể tạo workspace thủ công, thêm requirements và cases không cần LLM. Case unresolved vẫn được lưu; chỉ case liên quan bị chặn. Có thể publish các case approved trong khi các case khác còn câu hỏi. Lỗi schema/evidence không được bỏ qua bằng quyết định review.

```text
Review: Draft → Needs clarification / Ready for review → Approved → Retired
Automation: Unbound → Bound → Validated
```

Strategy chọn happy path, negative, Boundary Value Analysis, Equivalence Partitioning, Decision Table, State Transition, Permission Matrix, Workflow, Pairwise và Risk-based theo requirement; không ép mọi repo đủ năm nhóm testing.

Coverage ánh xạ từng acceptance criterion và techniques đã lập kế hoạch. Cases có blocker không được tính là coverage hợp lệ. Metrics dựa trên catalog trong workspace, không chứng minh toàn bộ hành vi hay code coverage.

## Regression inventory

Static scanner đọc repository mà không import/chạy project. Inventory ghi API decorators, UI routes, Python functions/methods, existing tests và outcomes true/false/exception mà parser nhận diện được. IDs ổn định theo source path, symbol và điều kiện. Python 3.14 exception headers được hỗ trợ; source không parse được báo **Chưa xác định**, không suy luận là bug. JS/TS dùng lexical extraction nên closures, JSX conditions và routes động vẫn có limitations.

Inventory được đối chiếu với evidence và endpoint criteria trong catalog. LLM có tối đa bốn batch bổ sung catalog có evidence; không sửa requirements/cases cũ. Generation target gồm requirements chưa có case, acceptance criteria còn thiếu và branch outcomes chưa có mapping. Output ngoài target được correction có giới hạn. `inventory_ids` và `branch_ids` trong case phải khớp source/evidence; mapping branch không chứng minh code đã chạy nhánh đó.

Nút **Bổ sung regression gaps** đọc default branch và scan toàn repo, giữ cases/source IDs nếu commit còn khớp. Source đổi thì yêu cầu **Cập nhật theo nguồn thay đổi**. Snapshot behavior sources có giới hạn 500 KB/300 documents; context mỗi lượt LLM vẫn theo `AGENT_QC_TDA_MAX_SOURCE_BYTES`. File không đọc được hoặc vượt budget được báo rõ, không coi là covered. Chỉ synthesize evidence từ source đã lưu; không chạy shell/code do LLM sinh.

Giao diện và JSON export báo riêng **Đã bao phủ**, **Catalog bỏ sót**, **Chưa có case hợp lệ**, **Branches và outcomes**, **Chưa xác định**, **Ngoài scope**, **Existing tests tham chiếu**. Build/dependency criteria không nằm trong denominator behavior coverage. Catalog thiếu, branch gap, scope exclusions và budget limits vẫn được giữ khi job kết thúc; không tự gắn nhãn regression suite đầy đủ. Baseline đã publish với cách tính coverage cũ giữ nguyên verification policy.

Source selection ưu tiên runtime manifests và behavior sources, chia ngân sách giữa backend/frontend và existing tests; generated clients, UI primitives và email templates được lấy sau. Tài liệu người dùng chọn vẫn được ưu tiên. Trong vòng generation, nếu còn requirement chưa có case, các lượt bổ sung được xen kẽ với refinement để tránh chỉ sửa lặp lại nhóm case đầu tiên.

Expected results do agent sinh với exact evidence chỉ từ code/DB/existing tests được phân loại `implementation_derived`, không phải `specified`. Trích dẫn được chuẩn hóa LF/CRLF hoặc formatting code nếu các tokens và nội dung string literal không thay đổi, sau đó phải khớp exact substring trong source. Trích dẫn sai nội dung vẫn bị chặn. Project có finding blocker hiển thị `needs_changes`, không báo toàn suite đã sẵn sàng review.

Managed workspace dùng `AGENT_QC_TDA_MAX_OUTPUT_TOKENS=16000` (1000–32000) để đủ output cho repo lớn. Analyzer có một lượt correction cho JSON/schema hoặc evidence sai, kèm node ID và source thực tế; không lặp vô hạn hoặc bỏ validation để hoàn thành job.

Khi nhập baseline cũ, chỉ tự liên kết criterion khớp acceptance criteria của scenario; không coi requirement mapping cũ là bằng chứng đã bao phủ mọi criterion. Các liên kết còn lại cần review trong editor.

Vòng coverage/quality/refinement có giới hạn. Agent chỉ tự sửa/ngừng dùng cases mới sinh trong chính lượt đó; cases cũ hoặc người dùng sửa được bảo vệ. Source updates không tự ghi đè nội dung: giữ nguồn lịch sử và proposals để người dùng đối chiếu.

## Memory, jobs và publication

`design_workspaces` lưu version hiện tại; `design_workspace_versions` lưu snapshots bất biến; `design_workspace_publications` lưu các bản publish. Mỗi mutation kiểm tra workspace version và ghi actor/action/reason. Giao diện giữ nội dung chưa lưu khi polling; version cũ chỉ xem, không sửa.

Supplement, AI edit, review và binding dùng snapshot đã lưu, không clone lại hoặc chạy ứng dụng. Sinh mới/source update đọc repository. Default-branch updates giữ cases cũ; explicit Sinh bộ mới thay suite và giữ history.

Published design chứa source versions, case revisions, decisions, scope, coverage và exclusions. Export JSON, CSV, Gherkin; CSV escape spreadsheet formula cells.

Nếu bật cập nhật QC, chỉ cases approved có bindings validated, reviewer và đúng design hash/revision vào executable subset. Không có subset đó hoặc chọn publish design-only thì giữ QC baseline hiện hành. Executable dependency phải khép kín; thiếu runtime/fixture khi QC chạy không được tính là pass.

QC artifacts dùng manifest schema `2.0`, policy `workspace-reviewed-subset-v1`, cùng `test_workspace.json` và `workspace_publication.json`. `verify()` kiểm tra hashes, revision, binding inventory, oracle mapping và coverage thực tế. Partial coverage cần acknowledgement/lý do; không đổi metrics thành 100%.

TDA không khởi động/test ứng dụng trong workflow mới. Binding validation xác nhận schema và mapping, không xác nhận code pass. Sửa design làm binding cũ unbound. QC pin baseline approved, chạy trên PR bằng dispatcher HTTP/unit/browser/k6/AI hiện có.

## API và cấu hình

- `GET /api/v1/projects/{id}/workspace`: workspace, history và publication summaries.
- `POST .../workspace/init`: tạo workspace thủ công.
- `POST .../workspace/actions`: case/requirement/reusable step, review decisions, approve/retire/restore, apply/dismiss proposal, save/validate binding.
- `POST .../workspace/jobs`: supplement, update_sources, ai_edit, review, bind.
- `GET .../workspace/history/{version}`: snapshot chỉ đọc.
- `GET .../workspace/export?format=json|csv|gherkin`: draft hoặc bản publish qua tham số `publication`.
- `POST .../workspace/publish`: published design và optional QC activation.

API dùng GitHub writer permission, fresh permission check khi mutation, session, CSRF và storage boundary checks. Không mutation/publish khi job đang xử lý; không ghi đè version mới hơn. Artifact chỉ được QC sử dụng sau registry commit thành công.

Mặc định `AGENT_QC_TDA_WORKSPACE_ENABLED=true`, tối đa 100 cases, 3 rounds và 24 model calls. Cấu hình qua `AGENT_QC_TDA_MAX_CASES`, `AGENT_QC_TDA_MAX_ROUNDS`, `AGENT_QC_TDA_MAX_CALLS`. TDA dùng `TDA_OPENAI_API_KEY`, QC dùng `OPENAI_API_KEY`.

Mỗi phản hồi generation chứa tối đa 8 cases để hạn chế output bị cắt. Lỗi JSON/schema có tối đa một lượt correction khi `max_repairs > 0`. Nếu các vòng trước đã có cases, lỗi vòng sau lưu partial draft cùng diagnostic; không bỏ toàn suite. Diagnostic chỉ ghi stage/error kind và vị trí/loại lỗi schema, không ghi input hoặc nội dung exception của provider. Có thể tiếp tục bằng Sinh bổ sung.

Package/CLI và baseline formats cũ vẫn được giữ để tương thích artifacts trước đây. Giao diện `/app` và managed generation dùng workspace mới.
