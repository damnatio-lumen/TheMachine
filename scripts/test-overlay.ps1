$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Ambiente non installato. Esegui prima .\scripts\install.ps1' }
Set-Location $Root
& $Python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'One or more reticle/tracking tests failed.' }
Write-Host 'Overlay and tracking tests passed.' -ForegroundColor Green
