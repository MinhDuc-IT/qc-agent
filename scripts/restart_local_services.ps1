$ErrorActionPreference = "Stop"
$qcRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $qcRoot ".data\local-stack"
$pidFile = Join-Path $runtimeRoot "pids.json"
$tunnelPid = $null

if (Test-Path -LiteralPath $pidFile) {
    $oldPids = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    if ($oldPids.PSObject.Properties.Name -contains "tunnel") {
        $candidate = [int]$oldPids.tunnel
        if (Get-Process -Id $candidate -ErrorAction SilentlyContinue) {
            $tunnelPid = $candidate
        }
    }
    foreach ($property in $oldPids.PSObject.Properties) {
        if ($property.Name -eq "tunnel") { continue }
        Stop-Process -Id ([int]$property.Value) -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped $($property.Name) ($($property.Value))"
    }
    Remove-Item -LiteralPath $pidFile -Force
}

& (Join-Path $PSScriptRoot "start_local_stack.ps1")

if ($null -ne $tunnelPid) {
    $newPids = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $merged = @{}
    foreach ($property in $newPids.PSObject.Properties) {
        $merged[$property.Name] = [int]$property.Value
    }
    $merged.tunnel = $tunnelPid
    $merged | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8
    Write-Output "Kept Cloudflare tunnel ($tunnelPid)"
}
