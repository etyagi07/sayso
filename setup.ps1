# One-command setup for Windows.
#   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "sayso setup" -ForegroundColor DarkGray
Write-Host ""

# --- python -----------------------------------------------------------
$py = $null
foreach ($candidate in @("python3.13", "python3.12", "python")) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        $v = & $candidate -c "import sys; print(f'{sys.version_info[0]}{sys.version_info[1]:02d}')" 2>$null
        if ($v -and [int]$v -ge 312) { $py = $candidate; break }
    }
}

if (-not $py) {
    Write-Host "Python 3.12 or newer is required." -ForegroundColor Red
    Write-Host "  Install from https://python.org/downloads (tick 'Add to PATH')"
    exit 1
}
Write-Host "ok python  $(& $py --version)" -ForegroundColor Green

# --- environment ------------------------------------------------------
if (-not (Test-Path .venv)) {
    & $py -m venv .venv
    Write-Host "ok created .venv" -ForegroundColor Green
} else {
    Write-Host "ok .venv exists" -ForegroundColor Green
}

& .\.venv\Scripts\pip.exe install -q --upgrade pip
Write-Host "   installing dependencies (a few minutes on first run)..." -ForegroundColor DarkGray
& .\.venv\Scripts\pip.exe install -q -r requirements.txt
Write-Host "ok dependencies installed" -ForegroundColor Green

Write-Host ""
Write-Host "Next steps:" -ForegroundColor DarkGray
Write-Host "  1. .\.venv\Scripts\python.exe -m shoonya.credentials  your API credentials"
Write-Host "  2. .\.venv\Scripts\python.exe -m voice.calibrate      measures your microphone"
Write-Host "  3. .\.venv\Scripts\python.exe -m shoonya.login        once per trading day"
Write-Host "  4. .\.venv\Scripts\python.exe -m voice.doctor         checks everything"
Write-Host "  5. .\.venv\Scripts\python.exe -m voice.main           run it"
Write-Host ""
