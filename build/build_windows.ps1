# Bygger dist\Musikquiz-windows.zip (mappen Musikquiz med Musikquiz.exe).
#   powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
# Kräver Python 3.12 i PATH och internet första gången (PyInstaller, Pillow och cloudflared hämtas).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Work = Join-Path $env:TEMP "musikquiz-build"
$Dist = Join-Path $Root "dist"
$CfVersion = "2026.10.7"
New-Item -ItemType Directory -Force -Path $Work, $Dist, (Join-Path $Work "bin") | Out-Null

if (-not (Test-Path "$Work\venv\Scripts\pyinstaller.exe")) {
  python -m venv "$Work\venv"
  & "$Work\venv\Scripts\pip.exe" install -q pyinstaller==6.11.1 pillow
}

$Cf = Join-Path $Work "bin\cloudflared.exe"
if (-not (Test-Path $Cf)) {
  Write-Host "Hämtar cloudflared $CfVersion…"
  Invoke-WebRequest -UseBasicParsing -OutFile $Cf "https://github.com/cloudflare/cloudflared/releases/download/$CfVersion/cloudflared-windows-amd64.exe"
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "$Dist\Musikquiz", "$Dist\Musikquiz-windows.zip"
& "$Work\venv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed --name Musikquiz `
  --icon "$Root\assets\icon.png" `
  --add-data "$Root\src\static;static" `
  --add-binary "$Cf;bin" `
  --paths "$Root\src" `
  --distpath "$Dist" --workpath "$Work\pyi" --specpath "$Work" `
  "$Root\src\app.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller misslyckades" }

Compress-Archive -Path "$Dist\Musikquiz" -DestinationPath "$Dist\Musikquiz-windows.zip"
Write-Host "Klart: $Dist\Musikquiz-windows.zip"
