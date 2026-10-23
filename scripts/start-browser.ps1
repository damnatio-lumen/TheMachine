$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'The project is not installed. Run .\scripts\install.ps1 first.' }
$Port = 8765
$Url = "http://127.0.0.1:$Port"
try {
    $Existing = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($Existing) { throw "Port $Port is already listening on 127.0.0.1. Stop the existing backend first." }
} catch {
    if ($_.Exception.Message -like 'Port *') { throw }
}
$OutLog = Join-Path $Root 'logs\backend-stdout.log'
$ErrLog = Join-Path $Root 'logs\backend-stderr.log'
$Process = Start-Process -FilePath $Python -ArgumentList 'app.py' -WorkingDirectory $Root -PassThru -WindowStyle Minimized -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
$PidFile = Join-Path $Root 'data\machine.pid'
Set-Content -LiteralPath $PidFile -Value $Process.Id -NoNewline -Encoding ascii
Start-Sleep -Seconds 2
try {
    $Response = Invoke-WebRequest -Uri "$Url/api/health" -TimeoutSec 3 -UseBasicParsing
    if ($Response.StatusCode -ne 200) { throw 'Health check did not return HTTP 200.' }
} catch {
    Write-Warning "Backend did not respond yet. PID: $($Process.Id). Check logs/machine.log and logs/backend-stderr.log."
}
Write-Host "MACHINE CAMERA MODE is running locally at $Url" -ForegroundColor Green
Write-Host 'This app is bound to loopback (127.0.0.1); it is not exposed to the LAN.' -ForegroundColor Yellow
Start-Process $Url
