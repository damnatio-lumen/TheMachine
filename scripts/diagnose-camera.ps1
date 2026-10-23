$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Url = 'http://127.0.0.1:8765'
Write-Host 'MACHINE CAMERA MODE // CAMERA DIAGNOSTICS' -ForegroundColor Cyan
Write-Host "Project: $Root"
Write-Host "Backend: $Url"
Write-Host ''
try {
    $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 3
    Write-Host '[OK] Backend raggiungibile.' -ForegroundColor Green
} catch {
    Write-Host '[ERROR] Backend is not reachable. Run .\scripts\start-browser.ps1.' -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}
try {
    $status = Invoke-RestMethod -Uri "$Url/api/status" -TimeoutSec 3
    Write-Host ("Camera running: {0}" -f $status.running)
    Write-Host ("Frame ready:    {0}" -f $status.frame_ready)
    Write-Host ("Frames encoded: {0}" -f $status.frame_count)
    Write-Host ("Backend video:  {0}" -f $(if ($status.backend) {$status.backend} else {'not selected'}))
    Write-Host ("Last frame:     {0}" -f $(if ($status.last_frame_at) {$status.last_frame_at} else {'none'}))
    Write-Host ("Read failures:  {0}" -f $status.consecutive_read_failures)
    if ($status.last_error) { Write-Host ("Last error:     {0}" -f $status.last_error) -ForegroundColor Yellow }
    if (-not $status.running) {
        Write-Host 'Action: select the correct device index in the GUI and click INITIALIZE FEED.' -ForegroundColor Yellow
    } elseif (-not $status.frame_ready) {
        Write-Host 'Action: the camera opened but no frames arrived. Try device index 1 or 2 and close Teams, Zoom, or the Windows Camera app.' -ForegroundColor Yellow
    } elseif ($status.frame_count -lt 1) {
        Write-Host 'Action: the first frame has not been processed yet. Wait a few seconds and try again.' -ForegroundColor Yellow
    } else {
        Write-Host 'The backend is producing frames. If the preview remains blank, press F5 and try another browser window.' -ForegroundColor Green
    }
} catch {
    Write-Host '[ERROR] Could not read /api/status.' -ForegroundColor Red
    Write-Host $_.Exception.Message
}
$log = Join-Path $Root 'logs\machine.log'
if (Test-Path $log) {
    Write-Host ''
    Write-Host '--- Last log lines ---' -ForegroundColor Cyan
    Get-Content $log -Tail 25
}
