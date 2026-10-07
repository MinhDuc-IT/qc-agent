# Kiến trúc QC-Agent hiện tại

Sơ đồ đối chiếu với source code ngày 07/10/2026. Đây là kiến trúc đã triển khai trong code, không phải xác nhận các dịch vụ đang chạy.

```mermaid
flowchart TD
    GH[GitHub Pull Request] --> API
    MAN[Manual API] --> API
    TR[Schedule / Deployment<br/>Feature flags] -.-> API

    subgraph CP[QC-Agent — FastAPI / Python]
        API[HTTP API<br/>HMAC / Bearer auth / dedup]
        DB[(SQLite<br/>Queue, runs, decision logs<br/>Flaky history, comparisons)]
        API --> DB
        DB --> Q[Async queue consumer]
        Q --> O[Orchestrator]
        O --> SRC[GitRepositoryManager<br/>Checkout head SHA]
        SRC --> A[RepositoryAnalyzer<br/>Manifests + diff + policy]
        A --> P[CapabilityPlanner<br/>Deterministic baseline + AI proposal]
        P --> PV[PolicyValidator<br/>ExecutionPlan]
        PV --> TP[TargetProvisioner<br/>Khởi động target khi cần]
        TP --> EX[DAG task execution<br/>Concurrency limit + infra retry]
        EX --> HY[HybridWorkerRuntime]
        HY --> ER[ExternalAgentRegistry<br/>HTTP task submit / poll / cancel]
        HY --> LR[WorkerRegistry<br/>Trusted CLI adapters]
        RES[Normalized WorkerResult] --> CF[Confirmation reruns<br/>E2E / performance failures]
        CF --> T[TriageService<br/>Evidence + confidence validation]
        T --> V[Deterministic VerdictEngine<br/>Raw + triaged verdict]
        V --> RA[ResultAnalysisAgent<br/>Nguyên nhân + gợi ý sửa]
        RA --> PUB[Publish + hoàn tất run<br/>Cleanup target / workspace]
        O -.-> DB
        T -.-> DB
    end

    CFG[org-policy.yaml<br/>.agent-qc.yaml tại base SHA] --> A
    LLM[OpenAI agents<br/>Planning / Triage / Result analysis<br/>Fallback khi chưa cấu hình hoặc lỗi]
    LLM -.-> P
    LLM -.-> T
    LLM -.-> RA

    subgraph EXT[External worker services — cần đăng ký]
        H[Hercules :8101<br/>Functional API / E2E]
        K[Keploy :8102<br/>Integration / contract replay]
        PERF[k6 :8104<br/>Performance]
        S[Strix :8103<br/>DAST — schedule only, tùy chọn]
    end
    ER --> H & K & PERF
    ER -.-> S
    H & K & PERF & S --> RES

    subgraph CLI[Tool execution]
        TOOLS[pytest / npm / Maven / Gradle<br/>Go / Cargo / dotnet<br/>Semgrep / Trivy / Gitleaks]
        BE[Local backend mặc định<br/>Docker backend tùy cấu hình]
        TOOLS --> BE
    end
    LR --> TOOLS
    BE --> RES
    PUB --> CHECK[GitHub Check + PR review<br/>Theo cấu hình / dry-run]
    PUB --> ART[Local artifacts<br/>Gợi ý self-heal + retention]
    PUB -.-> SIDE[Jira / Slack<br/>Hiện chỉ là dry-run stubs]
```

## Cách đọc

- Orchestrator là trung tâm điều phối; model đề xuất kế hoạch và phân tích, còn PolicyValidator và VerdictEngine quyết định theo rule.
- Task dùng capability/target để chọn worker. `auto` ưu tiên external agent, rồi fallback sang tool khi được phép; `external_agent` yêu cầu worker ngoài; `tool` đi thẳng CLI adapter.
- Một queue consumer xử lý run lần lượt; các task sẵn sàng trong DAG chạy song song với semaphore, mặc định 4 slot. Retry tối đa 2 lần cho lỗi hạ tầng có thể retry.
- Sau worker execution, hệ thống chạy confirmation cho finding lỗi E2E/performance, triage rồi tổng hợp verdict. Chế độ `observe` dùng raw verdict; chế độ khác dùng triaged verdict.
- Config repository lấy tại base SHA; code kiểm thử lấy tại head SHA.

## Trạng thái và giới hạn

- Script local stack khởi động orchestrator :8000, Hercules :8101, Keploy :8102, k6 :8104; Cloudflare tunnel là tùy chọn. Script không khởi động Strix.
- Trong checkout đang đọc chỉ có `external-agents.example.yaml` và `external-agents.local.example.yaml`, chưa có `external-agents.yaml`. Registry mặc định sẽ rỗng nếu không có file đăng ký hoặc đường dẫn override.
- Local host là execution backend mặc định. Docker backend đã có code nhưng cần cấu hình image; sơ đồ không khẳng định Docker đang hoạt động.
- Schedule/deployment là endpoint sau feature flag, không có scheduler tự chạy trong sơ đồ này.
- Jira/Slack hiện trả dry-run receipt; chưa gửi ra dịch vụ thật. Self-heal chỉ lưu gợi ý, không tự push code hoặc mở PR.

## Source chính

- `app/api/http.py`: composition root, endpoints và queue consumer.
- `app/application/orchestrator.py`: pipeline và task execution.
- `app/application/analysis.py`, `planning.py`, `triage.py`: phân tích, lập kế hoạch, triage.
- `app/domain/verdict.py`: tổng hợp verdict.
- `app/infrastructure/execution/hybrid.py`, `registry.py`: routing và CLI registry.
- `app/infrastructure/external_agents/`: registry và HTTP runtime.
- `workers/`: các worker HTTP độc lập.
- `scripts/start_local_stack.ps1`: topology local Windows.
