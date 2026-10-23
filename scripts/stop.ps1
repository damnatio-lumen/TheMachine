$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root 'data\machine.pid'
if (-not (Test-Path $PidFile)) { Write-Host 'No local PID is recorded.' -ForegroundColor DarkGray; exit 0 }
$pidText = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
$targetPid = 0
if ([int]::TryParse([string]$pidText, [ref]$targetPid)) {
    $proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
    if ($proc) {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction SilentlyContinue
        if ($cim -and $cim.CommandLine -match 'app\.py') {
            Stop-Process -Id $targetPid -Force
            Write-Host 'Backend stopped.' -ForegroundColor Green
        } else {
            Write-Warning 'The saved PID does not appear to belong to MACHINE CAMERA MODE; it will not be stopped.'
        }
    } else { Write-Host 'The backend is already stopped.' -ForegroundColor DarkGray }
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
