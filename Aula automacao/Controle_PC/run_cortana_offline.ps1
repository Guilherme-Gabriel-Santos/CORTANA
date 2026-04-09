$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$ollamaPath = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 2 | Out-Null
} catch {
    if (-not (Test-Path $ollamaPath)) {
        throw "Ollama nao encontrado em $ollamaPath"
    }

    Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 3
}

python offline_runtime.py
