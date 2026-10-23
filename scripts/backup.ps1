$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Destination = Join-Path $Root "backups\machine_backup_$Stamp"
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
foreach ($item in @('data\machine.sqlite3','data\profiles','data\snapshots')) {
    $source = Join-Path $Root $item
    if (Test-Path $source) {
        $target = Join-Path $Destination $item
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        if ((Get-Item $source).PSIsContainer) { Copy-Item $source $target -Recurse -Force } else { Copy-Item $source $target -Force }
    }
}
Write-Host "Backup creato in: $Destination" -ForegroundColor Green
Write-Host 'For a consistent backup, stop the application before running this script.' -ForegroundColor Yellow
