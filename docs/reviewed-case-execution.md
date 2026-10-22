# Test case mô tả và worker QC

## Luồng

TDA sinh test case → người dùng review/chỉnh sửa và duyệt → Bật cho QC → PR → workers thực thi.
Script và preflight tại TDA là tùy chọn. Bật cho QC không gọi LLM, không chạy ứng dụng và không biến lỗi preflight thành lỗi thiết kế.
Case còn blocker về nội dung hoặc chưa có worker hỗ trợ vẫn bị loại; áp dụng subset cần xác nhận coverage gap.

Baseline mới `reviewed-cases-1` lưu packet gồm case ID/revision, preconditions, test data, steps, expected results, evidence,
design SHA, source revision tham chiếu, module paths, worker, execution profile root và script cache tùy chọn.
Hash toàn bundle được pin vào QC run, lưu ở server ngoài checkout PR. Bản cũ `legacy` và `worker_artifacts` tiếp tục chạy.
Publication phân biệt `scheduled_case_ids` với `executed_case_ids`: bật case chưa đồng nghĩa đã chạy.

## Capability hiện có

| Worker | Đầu vào hiện có | Đường mô tả mới |
|---|---|---|
| Python / pytest | Script, fixtures, dependencies | Worker QC sinh script và review độc lập trên source PR; chạy trong môi trường test cô lập |
| Hercules | Gherkin, browser/HTTP target, LLM | Mỗi expected result có scenario ID riêng, giữ toàn bộ oracle; phải có JUnit tương ứng |
| Playwright | Browser actions/script | Tiếp tục đường plan/script hiện có; không nhận tự do mọi mô tả |
| k6 | Load plan/script và SLO | Tiếp tục đường plan/script hiện có; chưa nhận mô tả thiếu workload/SLO |
| Keploy | Recordings và command chạy SUT | Không suy ra recordings từ mô tả; vẫn disabled trên Docker Desktop theo quyết định vận hành |
| AI evaluation | HTTP input, rubric/judge | Tiếp tục đường plan/rubric; không tự coi mô tả là rubric đủ chuẩn |

Routing dựa vào evidence/module và execution profiles, không gắn cứng tên repo. Có worker hỗ trợ không có nghĩa môi trường runtime đã sẵn sàng.
Ví dụ Socket.IO runner thiếu ở QC vẫn trả BLOCKED; worker được phép tạo local test-only fixture dùng interface thật trong test module,
nhưng không được mock side effect cần kiểm chứng hoặc sửa production source để PASS.

## Thực thi và bằng chứng

Python worker dùng `OPENAI_API_KEY` / `OPENAI_MODEL` của QC, không dùng key TDA. `AGENT_QC_CASE_MAX_CALLS=12` giới hạn số calls của
mỗi lần thực thi task mô tả; confirmation/retry là lần thực thi riêng. Logs usage và outputs nằm ở `qc-case-execution/` trong TDA data root.
Tests phát triển dùng fake structured agents; không cần LLM thật để kiểm chứng protocol.

Script cache chỉ được lưu khi review và runtime PASS; key gồm project, design digest và version worker contract.
PR tiếp theo chạy lại script trên code PR với profile/services phát hiện lại, không lấy kết quả cache làm kết quả PR.
Nếu script không hợp lệ hoặc worker thiếu key/dependency/fixture thì BLOCKED. Khi script có assertion failure gắn được vào mapping,
expected result tương ứng là FAIL. Các expected results chưa chứng minh được không bị gán PASS.

Hercules tạo scenario có tên ổn định cho mỗi expected result, kèm preconditions/data và các bước đến điểm kiểm tra.
Adapter yêu cầu đúng scenario IDs, design digest và bằng chứng JUnit từng expected result. Report tổng hợp PASS hoặc scenario thiếu/skip/error
không đủ để kết luận case PASS. Step/scenario limit của Hercules vẫn được giữ; cần nâng cấu hình hoặc chia case nếu vượt hạn mức.
Wrapper chỉ ghi lại flags `is_assert`, `is_passed` và `assert_summary` thực tế của Hercules vào properties JUnit; không thay verdict.
JUnit không có assertion flags và summary là BLOCKED kể cả khi testcase không có failure tag.
Docker image Hercules phải rebuild để có packet contract và adapter mới.

## Existing tests, baseline và diff

Orchestrator giữ existing tests, thêm regression cases đã duyệt và cung cấp catalog cho planner để bổ sung rủi ro từ diff còn thiếu.
Chỉ loại scenario trùng tiêu đề chính xác sau chuẩn hóa; không suy ra semantic coverage từ tên case.
Không cắt regression suite chỉ vì file không xuất hiện trong diff; mặc định chạy toàn bộ baseline đã chọn để tránh bỏ sót dependency gián tiếp.
Worker không tự đưa test khám phá vào baseline. Báo cáo PR phân biệt existing tests, diff exploration và TDA baseline theo case ID/worker/evidence.

## Kiểm chứng và vận hành

Kiểm thử gồm enable không có script, không gọi TDA, fake QC agent sinh script giữ oracle, PR source cố ý sai, cache reuse,
chống sửa bundle, tương thích baseline cũ, dedup scenario và JUnit thiếu bằng chứng.
Smoke Docker trên source thật dùng script có sẵn và bản sao source test, không sửa active baseline hoặc tự publish cases.
Chạy LLM/worker thật với private source và tạo PR kiểm chứng là bước smoke riêng; không suy diễn test fake-agent thành chứng minh model/runner live hoạt động.
