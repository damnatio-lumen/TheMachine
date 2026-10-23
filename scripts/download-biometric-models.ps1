$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$ModelDir = Join-Path $Root 'models'
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

$Models = @(
    @{
        Name = 'face_detection_yunet_2023mar.onnx'
        Url = 'https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx?download=true'
        Sha256 = '8F2383E4DD3CFBB4553EA8718107FC0423210DC964F9F4280604804ED2552FA4'
    },
    @{
        Name = 'face_recognition_sface_2021dec.onnx'
        Url = 'https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx?download=true'
        Sha256 = '0BA9FBFA01B5270C96627C4EF784DA859931E02F04419C829E83484087C34E79'
    }
)

function Get-VerifiedModel($Model) {
    $Target = Join-Path $ModelDir $Model.Name
    if (Test-Path $Target) {
        $CurrentHash = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($CurrentHash -eq $Model.Sha256) {
            Write-Host "[OK] $($Model.Name) already exists and its SHA-256 matches." -ForegroundColor Green
            return
        }
        Write-Host "[WARN] Existing $($Model.Name) has a different checksum. It will be downloaded again." -ForegroundColor Yellow
    }

    $Partial = "$Target.download"
    Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
    Write-Host "[DOWNLOAD] $($Model.Name) from the official OpenCV Zoo model repository..." -ForegroundColor Cyan
    try {
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            & curl.exe --location --fail --silent --show-error --output $Partial $Model.Url
            if ($LASTEXITCODE -ne 0) { throw "curl.exe exited with code $LASTEXITCODE" }
        } else {
            Invoke-WebRequest -Uri $Model.Url -OutFile $Partial -UseBasicParsing
        }
        $ActualHash = (Get-FileHash -LiteralPath $Partial -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($ActualHash -ne $Model.Sha256) {
            throw "SHA-256 mismatch for $($Model.Name). Expected $($Model.Sha256), got $ActualHash."
        }
        Move-Item -LiteralPath $Partial -Destination $Target -Force
        Write-Host "[OK] Verified $($Model.Name)" -ForegroundColor Green
    } catch {
        Remove-Item -LiteralPath $Partial -Force -ErrorAction SilentlyContinue
        throw "Could not download and verify $($Model.Name): $($_.Exception.Message)"
    }
}

foreach ($Model in $Models) { Get-VerifiedModel $Model }

# Keep upstream license notices beside the downloaded model files.
$Notices = @(
    @{ Name = 'yunet_LICENSE'; Url = 'https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/LICENSE' },
    @{ Name = 'sface_LICENSE'; Url = 'https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_recognition_sface/LICENSE' }
)
foreach ($Notice in $Notices) {
    $Path = Join-Path $ModelDir $Notice.Name
    if (-not (Test-Path $Path)) {
        try { Invoke-WebRequest -Uri $Notice.Url -OutFile $Path -UseBasicParsing } catch { Write-Warning "Could not fetch optional license notice $($Notice.Name). Review MODEL_SOURCES.md." }
    }
}
Write-Host ''
Write-Host 'Biometric model setup finished. Restart the application backend to load the models.' -ForegroundColor Green
Write-Host 'Then enable BIOMETRIC MATCHING in the GUI. Unknown/unmatched faces remain gray.' -ForegroundColor Green
