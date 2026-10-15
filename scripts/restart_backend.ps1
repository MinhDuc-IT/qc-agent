# Restart only the QC backend. Preserve external workers and the current tunnel.
$ErrorActionPreference = "Stop"
$qcRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $qcRoot ".data\local-stack"
$python = Join-Path $qcRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "QC virtual environment is missing." }
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)
foreach ($processId in @($listeners.OwningProcess | Select-Object -Unique)) {
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    if (-not $existing -or $existing.CommandLine -notmatch 'uvicorn\s+app\.main:app' -or $existing.CommandLine -notmatch '--port\s+8000') {
        throw "Port 8000 is occupied by a process that is not the expected QC backend."
    }
    Stop-Process -Id $processId -Force
    Write-Output "Stopped verified QC backend ($processId)"
}

$process = Start-Process -FilePath $python `
    -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" `
    -WorkingDirectory $qcRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeRoot "orchestrator.out.log") `
    -RedirectStandardError (Join-Path $runtimeRoot "orchestrator.err.log")
$pidFile = Join-Path $runtimeRoot "pids.json"
$pids = @{}
if (Test-Path -LiteralPath $pidFile) {
    $previous = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($property in $previous.PSObject.Properties) { $pids[$property.Name] = [int]$property.Value }
}
$pids.orchestrator = $process.Id
$pids | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8
Write-Output "Started QC backend ($($process.Id)); tunnel and workers were preserved."
