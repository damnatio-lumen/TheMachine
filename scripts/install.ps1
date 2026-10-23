$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    $PythonCommand = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { & py -3.11 --version *> $null; if ($LASTEXITCODE -eq 0) { $PythonCommand = @('py','-3.11') } } catch {}
        if (-not $PythonCommand) { try { & py -3 --version *> $null; if ($LASTEXITCODE -eq 0) { $PythonCommand = @('py','-3') } } catch {} }
    }
    if (-not $PythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) { $PythonCommand = @('python') }
    if (-not $PythonCommand) { throw 'Python 3.11+ was not found. Install Python and reopen PowerShell.' }
    $exe = $PythonCommand[0]
    $pyArgs = @($PythonCommand | Select-Object -Skip 1) + @('-m','venv','.venv')
    & $exe @pyArgs
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
Write-Host '[1/2] Installing Flask and OpenCV...' -ForegroundColor Cyan
& $VenvPython -m pip install -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check Internet access or package cache availability.' }
Write-Host '[2/2] Checking Python syntax...' -ForegroundColor Cyan
& $VenvPython -m py_compile (Join-Path $Root 'app.py')
if ($LASTEXITCODE -ne 0) { throw 'Python syntax check failed.' }
Write-Host 'Installation complete. Run .\scripts\start-browser.ps1 to launch the browser GUI.' -ForegroundColor Green
Write-Host 'Optional: run .\scripts\download-biometric-models.ps1 to enable local biometric matching.' -ForegroundColor Yellow
