$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "Iniciando app desktop da Cortana Offline..."

$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollama) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 2 | Out-Null
    }
    catch {
        Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 2
    }
}

python offline_desktop_app.py
