param([switch]$WithTunnel)

$ErrorActionPreference = "Stop"
$qcRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = (Join-Path (Split-Path $qcRoot -Parent) ".agent-qc-workspaces")
$runtimeRoot = Join-Path $qcRoot ".data\local-stack"
New-Item -ItemType Directory -Force $workspaceRoot, $runtimeRoot | Out-Null

function Read-DotEnv([string]$path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $parts = $line.Split('=', 2)
        $values[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $values
}

function Start-QcProcess(
    [string]$name, [string]$file, [string]$arguments,
    [hashtable]$setEnvironment, [string[]]$removeEnvironment
) {
    $stdout = Join-Path $runtimeRoot "$name.out.log"
    $stderr = Join-Path $runtimeRoot "$name.err.log"
    $names = @($setEnvironment.Keys) + @($removeEnvironment) | Select-Object -Unique
    $previous = @{}
    foreach ($key in $names) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
    }
    try {
        foreach ($key in $removeEnvironment) {
            [Environment]::SetEnvironmentVariable($key, $null, "Process")
        }
        foreach ($key in $setEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($key, [string]$setEnvironment[$key], "Process")
        }
        return Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $qcRoot `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
            -WindowStyle Hidden -PassThru
    } finally {
        foreach ($key in $names) {
            [Environment]::SetEnvironmentVariable($key, $previous[$key], "Process")
        }
    }
}

$config = Read-DotEnv (Join-Path $qcRoot ".env")
$mainPython = Join-Path $qcRoot ".venv\Scripts\python.exe"
$herculesPython = Join-Path $qcRoot ".venv-hercules\Scripts\python.exe"
$herculesExe = Join-Path $qcRoot ".venv-hercules\Scripts\testzeus-hercules.exe"
$keployExe = Join-Path $qcRoot ".tools\keploy-official.exe"
$toolPath = (Join-Path $qcRoot ".tools") + ";" + (Join-Path $qcRoot ".venv\Scripts") + ";" + $env:PATH

$common = @{ PYTHONPATH = $qcRoot; PYTHONUTF8 = "1"; PATH = $toolPath }
$processes = @{}

$herculesEnv = $common.Clone()
$herculesEnv.HERCULES_EXECUTABLE = $herculesExe
$herculesEnv.HERCULES_LLM_API_KEY = $config.HERCULES_LLM_API_KEY
$herculesEnv.HERCULES_LLM_MODEL = $config.HERCULES_LLM_MODEL
$herculesEnv.HERCULES_AGENT_TOKEN = $config.FUNCTIONAL_AGENT_TOKEN
$herculesEnv.HERCULES_MAX_SCENARIOS = $config.HERCULES_MAX_SCENARIOS
$herculesEnv.HERCULES_MAX_STEPS = $config.HERCULES_MAX_STEPS
$herculesEnv.HERCULES_MAX_RUNTIME_SECONDS = $config.HERCULES_MAX_RUNTIME_SECONDS
$herculesEnv.HERCULES_ALLOWED_SOURCE_ROOT = $workspaceRoot
$processes.functional = Start-QcProcess "functional" $herculesPython "-m uvicorn workers.functional_hercules.service:app --host 127.0.0.1 --port 8101" $herculesEnv @("OPENAI_API_KEY", "STRIX_LLM_API_KEY")

$keployEnv = $common.Clone()
$keployEnv.KEPLOY_EXECUTABLE = $keployExe
$keployEnv.KEPLOY_AGENT_TOKEN = $config.INTEGRATION_AGENT_TOKEN
$keployEnv.KEPLOY_ALLOWED_SOURCE_ROOT = $workspaceRoot
$processes.integration = Start-QcProcess "integration" $mainPython "-m uvicorn workers.integration_keploy.service:app --host 127.0.0.1 --port 8102" $keployEnv @("OPENAI_API_KEY", "HERCULES_LLM_API_KEY", "STRIX_LLM_API_KEY")

$k6Env = $common.Clone()
$k6Env.K6_EXECUTABLE = "C:\Program Files\k6\k6.exe"
$k6Env.K6_AGENT_TOKEN = $config.PERFORMANCE_AGENT_TOKEN
$k6Env.K6_ALLOWED_SOURCE_ROOT = $workspaceRoot
$k6Env.K6_WORK_ROOT = Join-Path $qcRoot ".agent-qc-k6"
$processes.performance = Start-QcProcess "performance" $mainPython "-m uvicorn workers.performance_k6.service:app --host 127.0.0.1 --port 8104" $k6Env @("OPENAI_API_KEY", "HERCULES_LLM_API_KEY", "STRIX_LLM_API_KEY")

$mainEnv = $common.Clone()
foreach ($key in $config.Keys) { $mainEnv[$key] = $config[$key] }
$processes.orchestrator = Start-QcProcess "orchestrator" $mainPython "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" $mainEnv @("HERCULES_LLM_API_KEY", "STRIX_LLM_API_KEY")

if ($WithTunnel) {
    $cloudflared = "C:\Users\MinhDuc\AppData\Roaming\npm\node_modules\cloudflared\bin\cloudflared.exe"
    $processes.tunnel = Start-QcProcess "tunnel" $cloudflared "tunnel --url http://127.0.0.1:8000 --no-autoupdate" @{} @()
}

$pids = @{}
foreach ($key in $processes.Keys) { $pids[$key] = $processes[$key].Id }
$pids | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeRoot "pids.json") -Encoding UTF8
$pids | ConvertTo-Json
