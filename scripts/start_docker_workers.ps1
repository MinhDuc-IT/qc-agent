param([switch]$Build)
$ErrorActionPreference = 'Stop'
$qcRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $qcRoot
try {
    if (-not (Test-Path -LiteralPath 'external-agents.yaml')) {
        Copy-Item -LiteralPath 'external-agents.docker.example.yaml' -Destination 'external-agents.yaml'
    }
    $qcArguments = @('compose','--env-file','.env','-f','workers/docker-compose.local.yaml','up','-d')
    if ($Build) { $qcArguments += '--build' }
    & docker @qcArguments
    if ($LASTEXITCODE -ne 0) { throw 'Worker stack did not start; inspect Docker build/service logs.' }
    Write-Output 'Worker stack started. eBPF opt-in is not enabled by this script.'
} finally { Pop-Location }
