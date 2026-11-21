<#
.SYNOPSIS
  Start the local DungScraper API (and playground) on this computer only.

.EXAMPLE
  .\run.ps1                 # runs in this window, Ctrl+C stops it
  .\run.ps1 -Background     # runs hidden, logs go to run.out.log and run.err.log
  .\run.ps1 -Port 9000
#>
param(
    [int]$Port = 8741,
    [switch]$Background
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$cli = Join-Path $PSScriptRoot '.venv\Scripts\dungscraper.exe'
if (-not (Test-Path $cli)) {
    Write-Host 'DungScraper is not installed here. Run .\install.ps1 first.' -ForegroundColor Red
    exit 1
}

$env:DUNGSCRAPER_HOST = '127.0.0.1'
$env:DUNGSCRAPER_PORT = "$Port"
$health = "http://127.0.0.1:$Port/health"

try {
    Invoke-RestMethod $health -TimeoutSec 3 | Out-Null
    Write-Host "Already running at http://127.0.0.1:$Port"
    exit 0
} catch {}

if ($Background) {
    Start-Process -FilePath $cli -ArgumentList 'serve' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $PSScriptRoot 'run.out.log') `
        -RedirectStandardError (Join-Path $PSScriptRoot 'run.err.log')
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            Invoke-RestMethod $health -TimeoutSec 2 | Out-Null
            Write-Host "Started. Playground: http://127.0.0.1:$Port  API docs: http://127.0.0.1:$Port/docs"
            exit 0
        } catch {}
    }
    Write-Host 'The server did not answer in 30 seconds. See run.err.log.' -ForegroundColor Red
    exit 1
}

Write-Host "Starting on http://127.0.0.1:$Port (Ctrl+C to stop)"
& $cli serve
