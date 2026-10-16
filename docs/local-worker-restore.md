# Khôi phục local worker stack

Security workers dùng image pin digest trong `docker-images.yaml`: Semgrep và Gitleaks chạy qua Docker, không yêu cầu binary trên Windows. Máy chạy QC cần Docker CLI/daemon và tải được hai image đã cấu hình. Rules Semgrep được mount read-only vào container.

Existing pytest chạy trong checkout tạm và execution profile của repo, không dùng Python/dependencies của QC service. Profile phát hiện Python, dependency files, migrations và test services; môi trường test được tạo mới với PostgreSQL/SMTP khi cần, không sao chép `.env` của repo. Virtual uv workspace được bỏ qua ở root; tests chạy tại member project. Lỗi collection hoặc môi trường trả `unknown`, không kết luận lỗi sản phẩm.

Từ thư mục `qc-agent`:

```powershell
.\scripts\start_docker_workers.ps1 -Build
```

Lệnh dùng `workers/docker-compose.local.yaml`, chỉ mở cổng loopback 8101/8102/8104. Mỗi container chỉ nhận token/key worker tương ứng từ `.env`; không nhận GitHub hoặc TDA keys. Checkout dùng shared mount `/workspaces`; artifacts lưu riêng dưới `.data/worker-artifacts`. Không cấp Docker socket hoặc quyền eBPF trong cấu hình mặc định.

Registry local `external-agents.yaml` dùng `source_root: /workspaces`. Hercules/k6 thêm `target_host_override: host.docker.internal` để gọi test target trên host. Transport chỉ đổi loopback target và dòng Given mở trang; giữ nguyên assertions và task gốc.

Hercules pin Python 3.12/Debian Bookworm vì Playwright 1.49 của Hercules 1.0.2 không tương thích Debian mặc định mới. PyTorch dùng CPU index để tránh CUDA dependencies không cần thiết.

Keploy worker dùng [OSS release v3.6.126](https://github.com/keploy/keploy/releases/tag/v3.6.126), binary tải từ GitHub và pin SHA-256 trong Dockerfile. Image tag `ghcr.io/keploy/keploy:v3.8.58` trước đó không tồn tại khi khôi phục. Service health/executable không đủ chứng minh replay sẵn sàng: instrumentation còn cần eBPF trên worker VM.

Theo quyết định maintainer, Keploy bị disable trong registry local (`enabled: false`, `blocked_reason` ghi rõ cần VM riêng). Registry vẫn hiển thị worker và lý do; QC trả unknown/blocked và không âm thầm fallback. File `workers/docker-compose.keploy-ebpf.yaml` chỉ là cấu hình opt-in để review trên VM riêng, không tự được áp dụng trên Docker Desktop hiện tại.

Smoke TDA ngày 15/10/2026 dùng 2 trong tối đa 3 calls được chấp thuận cho một login case. Artifact pass Docker preflight trên source thật, được import thành proposal vào workspace v19 qua `scripts/import_prepared_artifact.py`; chưa được xác nhận artifact hoặc áp dụng baseline. Maintainer cần review trên UI trước khi thử PR thật.
