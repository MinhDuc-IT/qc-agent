$qcRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $qcRoot ".data\local-stack\pids.json"
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output "No local stack PID file."; exit 0 }
$pids = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
foreach ($property in $pids.PSObject.Properties) {
    Stop-Process -Id ([int]$property.Value) -Force -ErrorAction SilentlyContinue
    Write-Output "Stopped $($property.Name) ($($property.Value))"
}
Remove-Item -LiteralPath $pidFile -Force
