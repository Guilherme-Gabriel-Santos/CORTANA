$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$distDir = Join-Path $scriptDir "dist-offline"
$workDir = Join-Path $scriptDir "build-offline"

if (Test-Path $distDir) {
    Remove-Item -Recurse -Force $distDir
}

if (Test-Path $workDir) {
    Remove-Item -Recurse -Force $workDir
}

pyinstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name "Cortana Offline" `
  --distpath $distDir `
  --workpath $workDir `
  --specpath $workDir `
  --exclude-module PyQt5 `
  --exclude-module PyQt6 `
  --exclude-module PySide2 `
  --exclude-module matplotlib `
  --exclude-module IPython `
  --exclude-module pytest `
  --exclude-module sphinx `
  --exclude-module tkinter `
  --collect-all edge_tts `
  --collect-all pygame `
  --collect-all pyttsx3 `
  --collect-all comtypes `
  --collect-all faster_whisper `
  --collect-all ctranslate2 `
  --collect-all av `
  --collect-all sounddevice `
  --collect-all soundfile `
  --collect-submodules cv2 `
  --collect-submodules PySide6 `
  offline_desktop_app.py

$exePath = Join-Path $distDir "Cortana Offline\Cortana Offline.exe"
if (-not (Test-Path $exePath)) {
    throw "O build terminou sem gerar o executavel esperado: $exePath"
}
Write-Host "Build concluido em: $exePath"
