<#
.SYNOPSIS
  One-command install of DungScraper on Windows. No admin rights needed. Safe to run again.

.EXAMPLE
  git clone https://github.com/dakisxxx-oss/DungScraper.git
  cd DungScraper
  .\install.ps1

  If PowerShell blocks the script:
  powershell -ExecutionPolicy Bypass -File .\install.ps1

.PARAMETER NoBrowser
  Skip the Chromium download. Plain HTTP scraping still works. Pages that need JavaScript will not.
#>
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }

# 1. Find Python 3.11 or newer.
Step 'Looking for Python 3.11 or newer'
$candidates = @(
    @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('python'), @('python3')
)
$python = $null
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $extra = @()
    if ($candidate.Count -gt 1) { $extra = $candidate[1..($candidate.Count - 1)] }
    try {
        $ok = & $exe @extra -c "import sys; print(int(sys.version_info >= (3, 11)))" 2>$null
    } catch { continue }
    if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq '1') {
        $python = @{ Exe = $exe; Args = $extra }
        break
    }
}
if (-not $python) {
    Fail 'Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this script again.'
}
$version = & $python.Exe @($python.Args) -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
Write-Host "Using Python $version"

# 2. Virtual environment. Re-created only when it is missing or broken.
$venv = Join-Path $PSScriptRoot '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$healthy = $false
if (Test-Path $venvPython) {
    try {
        & $venvPython -c "import sys" 2>$null
        $healthy = ($LASTEXITCODE -eq 0)
    } catch { $healthy = $false }
}
if (-not $healthy) {
    Step 'Creating .venv'
    if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
    & $python.Exe @($python.Args) -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail 'Could not create the virtual environment.' }
} else {
    Step '.venv already exists, reusing it'
}

# 3. Install the package.
Step 'Installing DungScraper (this takes a minute the first time)'
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Fail 'pip upgrade failed. Check your internet connection.' }
& $venvPython -m pip install -e . --quiet
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed. Run ".\.venv\Scripts\python.exe -m pip install -e ." to see the full error.' }

# 4. Browser for JavaScript pages.
if ($NoBrowser) {
    Step 'Skipping the Chromium download (-NoBrowser)'
} else {
    Step 'Installing Chromium for JavaScript pages (about 150 MB, once)'
    & $venvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'WARNING: Chromium did not install. HTTP scraping still works. Retry later with: .\.venv\Scripts\python.exe -m playwright install chromium' -ForegroundColor Yellow
    }
}

# 5. Smoke test.
Step 'Smoke test: scraping https://example.com'
$cli = Join-Path $venv 'Scripts\dungscraper.exe'
$output = & $cli scrape --local https://example.com 2>&1 | Out-String
if ($output -match 'Example Domain') {
    Write-Host 'Smoke test passed.' -ForegroundColor Green
} else {
    Write-Host 'WARNING: The smoke test did not return the expected page. Output:' -ForegroundColor Yellow
    Write-Host $output
    Write-Host 'If you are offline or behind a proxy, this is expected. The install itself finished.' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Done. Next steps:' -ForegroundColor Green
Write-Host '  Scrape a page:        .\.venv\Scripts\dungscraper.exe scrape --local https://example.com'
Write-Host '  Find public contacts: .\.venv\Scripts\dungscraper.exe contacts https://example.com --local'
Write-Host '  Start the local API:  .\run.ps1        (needed for the web playground and HTTP API)'
Write-Host '  Optional, Claude Desktop: .\.venv\Scripts\dungscraper.exe install-claude'
Write-Host '  Tip: .\.venv\Scripts\Activate.ps1 lets you type just "dungscraper ..."'
