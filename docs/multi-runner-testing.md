# Sinh và chạy test với nhiều runner

TDA dùng LLM đọc codebase và tài liệu được chọn, lập kế hoạch rồi sinh dữ liệu test có cấu trúc. Runner chạy template cố định; LLM không sinh shell command hoặc chương trình được thực thi trực tiếp. Loại kiểm thử và runner là hai thuộc tính riêng.

| Loại kiểm thử | Runner thực thi | Phạm vi hiện tại |
| --- | --- | --- |
| Chức năng | HTTP, pytest/node:test, Playwright | API JSON/văn bản, hàm Python/JavaScript, thao tác DOM |
| Tích hợp | HTTP, Playwright | Chuỗi request hoặc thao tác trong ứng dụng có fixture phù hợp |
| Bảo mật | HTTP | Phân quyền, xác thực, kiểm tra đầu vào API có hợp đồng nguồn |
| Hiệu năng | HTTP, k6 | Thời gian phản hồi và tải đồng thời có ngưỡng được dẫn nguồn |
| Đánh giá AI | AI executor | Ground truth JSON/văn bản hoặc LLM chấm theo rubric đã duyệt |

Planner chỉ chọn loại áp dụng cho repo. Repo không có AI sẽ không bị yêu cầu tạo AI evaluation. Dependency-only requirements vẫn được ghi nhận là ngoài phạm vi; đây chưa phải công cụ quét toàn bộ lỗ hổng, kiểm thử mọi ngôn ngữ hay tải sản xuất.

## Cài đặt backend Windows

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-tda.txt
docker info
.tools\k6\k6.exe version
```

Docker Desktop phải đang chạy. Playwright dùng Chrome đã cài trên máy theo `AGENT_QC_BROWSER_CHANNEL=chrome`. Có thể dùng Chromium do Playwright quản lý bằng `python -m playwright install chromium` và để channel rỗng. k6 trên workspace này nằm tại `.tools/k6/k6.exe`; máy khác cài k6 và đặt `AGENT_QC_K6_BINARY` thành đường dẫn đầy đủ tới binary. TDA cần `TDA_OPENAI_API_KEY`; QC dùng `OPENAI_API_KEY` để chấm AI trong lượt PR. Hai phần lưu sự kiện sử dụng model riêng.

`AGENT_QC_TDA_RUNNERS=http,unit,browser,k6,ai` bật các runner. `AGENT_QC_TEST_ISOLATION=docker` là mặc định. Mã dự án chạy trong container có giới hạn tài nguyên. Unit không có mạng; HTTP target nằm trong mạng Docker nội bộ, chỉ được tiếp cận qua cầu TCP cố định. Backend key không được chuyển vào container. Thiếu Docker, runtime, fixture hoặc grader sẽ trả trạng thái chưa chạy được, không được tính là pass. Các dependency được cài ở bước build image; build cần mạng để tải package.

Ứng dụng hiện tự phát hiện entrypoint cho Python/FastAPI/Flask và Node đơn giản. Repo cần nhiều dịch vụ, cơ sở dữ liệu hoặc framework khác sẽ cần adapter/fixture bổ sung. UI không yêu cầu maintainer nhập lệnh khởi động.

## Baseline và QC

Trong review, mỗi test case có phần **Gợi ý và action**. Critic có thể đề xuất `request_changes`, `add_acceptance_criteria` hoặc `review_blocker`, kèm hướng xử lý và câu hỏi. Với baseline cũ chưa có suggestion của agent, UI cung cấp hướng xử lý mặc định để maintainer viết feedback.

Maintainer có thể chỉnh feedback trước khi gửi. Action tạo một revision: chỉ sửa case được chọn, giữ case ID và nguyên nội dung các case khác. `review_blocker` chỉ chạy lại Critic trên suite hiện tại. Source phải khớp baseline đang review; nếu source thay đổi, hệ thống yêu cầu sinh lại thay vì âm thầm thay suite. Job pin source revision, không lấy code PR. Feedback là context, không tự trở thành bằng chứng cho expected result. Acceptance criteria được maintainer nhập rõ ràng sẽ được lưu thành PRD source của repository.

Giao diện giữ màn review khi gửi feedback, hiển thị progress tại đó và tự mở revision mới khi job hoàn tất. `review_changes.json` ghi baseline gốc, case ID thay đổi và số case được giữ nguyên. Revision vẫn qua validation, coverage và Critic; nếu còn blocker thì UI nêu rõ thay vì tự approve. Có thể quay lại baseline gốc để tạo một revision khác khi revision hiện tại chưa được review; các draft vẫn được giữ độc lập và không tự ghép thay đổi.

Baseline cũ được giữ nguyên. Action không đồng nghĩa reject hay approve. Job lưu actor, case ID, baseline nguồn và feedback trong audit; `onboarding.json` của draft mới ghi lineage. API yêu cầu quyền write/maintain/admin mới xác minh, session và CSRF; baseline stale/đã review không nhận action. Sau khi có draft mới, maintainer review và duyệt theo gate thiết kế như bình thường.

Baseline vẫn lưu dưới `.data/test-design/projects/<project_id>/baselines/<version>/` (hoặc `AGENT_QC_TDA_DIR`). `cases/cases.json` chứa `runner` và cấu hình có kiểu; `ground_truth/`, traceability, báo cáo validation/coverage và các template runner được băm trong manifest.

Maintainer xem request/thao tác/hàm gọi, expected results, bằng chứng và độ bao phủ thiết kế trên `/app`. Duyệt khi không có blocker, mọi case qua validation, độ bao phủ thiết kế đạt yêu cầu và Critic cho phép review. Không yêu cầu code tham chiếu chạy đạt. QC pin phiên bản **đã duyệt** từ registry phía server, chạy trên checkout của PR và giữ nguyên oracle. Suite chỉ có unit dùng capability `baseline.repository`, không cần HTTP target; suite còn lại dùng `baseline.http` và dispatcher nhiều runner. Chưa tự chia suite thành nhiều worker song song.

GitHub App mặc định `AGENT_QC_TDA_REFERENCE_EXECUTION=false`: TDA sinh và kiểm tra thiết kế, không khởi động ứng dụng hoặc chạy suite. Có thể bật `true` để lưu kết quả tham chiếu; lỗi ứng dụng hoặc thiếu runtime không tự chặn duyệt. CLI workflow có `reference_execution` trong cấu hình; baseline v2 có thể duyệt không cần `--report`. Baseline v1 vẫn giữ điều kiện passing report cũ.

Kết quả tham chiếu nếu có được lưu trong `reference_execution.json` và băm cùng baseline. QC chỉ đối chiếu snapshot này, không tin báo cáo ngoài manifest. Các nhãn: `existing_failure` (cùng case/assertion đã lỗi ở tham chiếu), `new_failure` (tham chiếu đạt, PR lỗi), `changed_failure`, `resolved_failure`. Không có tham chiếu hoặc tham chiếu chưa chạy được thì dùng `unclassified_failure`; không tự khẳng định lỗi do PR. So sánh này mô tả kết quả quan sát, không chứng minh nguyên nhân; verdict vẫn phản ánh test pass/fail của code PR.

Trước khi thực thi, generator có tối đa một lượt sửa cấu trúc theo validation; Oracle Engine mã hóa chuỗi thành JSON và có tối đa một lượt sửa index/nhóm assertion theo từng thao tác. Các assertion có JSON hợp lệ được giữ nguyên kỳ vọng, bằng chứng và selector. Bản sửa làm mất hoặc thay đổi chúng sẽ không được áp dụng. Không cung cấp kết quả chạy ứng dụng cho bước sửa thiết kế. `max_repairs=0` tắt các lượt sửa này; tổng lượt gọi vẫn chịu `max_calls`.

Báo cáo phân biệt `subset_passed` (nhóm test hợp lệ đã chạy đạt) với `passed` của toàn bộ baseline. Test bị loại vì sai cấu trúc vẫn ngăn baseline đạt và duyệt. Kỳ vọng HTTP không được nguồn xác nhận, như mã lỗi 422/500, vẫn phải qua kiểm tra bằng chứng và Critic; việc sửa cấu trúc không bổ sung căn cứ nghiệp vụ.

k6 bị giới hạn tối đa 5 VU và 15 giây mỗi case, chỉ GET/HEAD. Ngưỡng lấy từ benchmark code phải ghi rõ là hành vi triển khai, không được gọi là SLO nghiệp vụ. Quyền thay đổi dữ liệu vẫn theo policy của project.

## Chạy baseline xuất ra ngoài backend

Đặt `TDA_BASE_URL` cho target cô lập, `TDA_WORKSPACE` cho source checkout, `TDA_K6_BINARY` và `TDA_BROWSER_CHANNEL` theo máy chạy rồi chạy pytest entrypoint trong manifest. Với rubric AI cần cài package TDA và đặt `TDA_EVAL_API_KEY`, `TDA_EVAL_MODEL`, `TDA_EVAL_OUTPUT`. Lệnh pytest xuất ra dùng runtime cục bộ: CI phải cung cấp container/sandbox riêng cho code không tin cậy; luồng managed của GitHub App tự dùng Docker.

Image `Dockerfile.with-tda` có Playwright/Chromium và k6. Nếu backend chạy trong container, Docker executor cần daemon riêng và đường dẫn bind-mount workspace được chia sẻ giống nhau giữa backend với daemon. Image này không tự cấu hình các mount/daemon đó.

## Kiểm chứng

```powershell
.venv\Scripts\python.exe -m pytest tests packages/test-design-agent/tests -q
.venv\Scripts\python.exe scripts/verify_isolated_runners.py
```

Regression dùng fixture cục bộ, chạy HTTP, pytest, node:test, Playwright và k6 thật. AI grader trong test là bộ chấm giả xác định, không gọi API trả phí. Docker smoke check kiểm tra HTTP container, unit container, không kế thừa key và không có mạng ngoài cho unit.
