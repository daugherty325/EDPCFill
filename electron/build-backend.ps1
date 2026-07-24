$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = if ($env:MTG_ART_PICKER_BUILD_PYTHON) { $env:MTG_ART_PICKER_BUILD_PYTHON } else { "python" }

Push-Location $root
try {
    & $python -c "import PIL, reportlab"
    if ($LASTEXITCODE -ne 0) {
        throw "Backend build dependencies are incomplete. Install Pillow and ReportLab in the selected build Python environment."
    }

    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --name mtg-art-picker-backend `
        --distpath build/backend-stage `
        --workpath build/pyinstaller `
        --specpath build `
        outputs/mtg_art_picker_web.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    $source = Join-Path $root "build/backend-stage/mtg-art-picker-backend"
    $destination = Join-Path $root "build/backend"
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
    Move-Item -LiteralPath $source -Destination $destination
} finally {
    Pop-Location
}
