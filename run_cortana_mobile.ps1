$ErrorActionPreference = "Stop"

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $scriptDir "Aula automacao\Controle_PC"
$frontDir   = Join-Path $scriptDir "Layout Cortana\agent-starter-react-main"
$frontPort  = 3000
$logFile    = Join-Path $scriptDir "cortana-movel.log"

Start-Transcript -Path $logFile -Force | Out-Null

try {
    $tailscaleExe = @(
        "$env:ProgramFiles\Tailscale\tailscale.exe",
        "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $tailscaleExe) {
        throw "Tailscale nao encontrado. Instale em https://tailscale.com/download"
    }

    $status = & $tailscaleExe status --json 2>$null | ConvertFrom-Json
    if (-not $status -or $status.BackendState -ne "Running") {
        throw "Tailscale nao esta conectado. Rode 'tailscale up' primeiro."
    }

    $machineDns = $status.Self.DNSName.TrimEnd('.')
    Write-Host "Tailscale OK - maquina: $machineDns" -ForegroundColor Green

    if (-not (Test-Path $backendDir)) { throw "Backend nao encontrado: $backendDir" }
    if (-not (Test-Path $frontDir))   { throw "Frontend nao encontrado: $frontDir" }

    Write-Host "Subindo backend Python (agent.py dev)..." -ForegroundColor Cyan
    $backendProc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoExit", "-Command", "Set-Location '$backendDir'; python agent.py dev" `
        -PassThru

    Write-Host "Subindo frontend Next.js (pnpm dev)..." -ForegroundColor Cyan
    $frontProc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoExit", "-Command", "Set-Location '$frontDir'; pnpm dev" `
        -PassThru

    Write-Host "Aguardando frontend em http://localhost:$frontPort ..." -ForegroundColor Yellow
    $deadline = (Get-Date).AddSeconds(120)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$frontPort" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -ge 200) { $ready = $true; break }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $ready) {
        Write-Warning "Frontend nao respondeu em 120s. Continuando, mas o serve pode ate levar mais."
    } else {
        Write-Host "Frontend OK em http://localhost:$frontPort" -ForegroundColor Green
    }

    Write-Host "Limpando config anterior do tailscale serve..." -ForegroundColor Cyan
    & $tailscaleExe serve reset 2>&1 | Out-Host

    Write-Host "Configurando tailscale serve (HTTPS automatico)..." -ForegroundColor Cyan
    & $tailscaleExe serve --bg $frontPort 2>&1 | Out-Host
    $serveExit = $LASTEXITCODE
    if ($serveExit -ne 0) {
        Write-Warning "tailscale serve retornou codigo $serveExit. Veja mensagem acima."
    }

    & $tailscaleExe serve status 2>&1 | Out-Host

    $mobileUrl = "https://$machineDns"
    Write-Host ""
    Write-Host "=========================================================" -ForegroundColor Green
    Write-Host " Cortana disponivel no iPhone em:" -ForegroundColor Green
    Write-Host "   $mobileUrl" -ForegroundColor White
    Write-Host "=========================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "PIDs - backend: $($backendProc.Id)  frontend: $($frontProc.Id)"
    Write-Host "Para parar a exposicao HTTPS: tailscale serve reset"
}
catch {
    Write-Host ""
    Write-Host "[ERRO] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
}
finally {
    Stop-Transcript | Out-Null
    Write-Host ""
    Write-Host "Log completo salvo em: $logFile" -ForegroundColor DarkGray
    Write-Host ""
    Read-Host "Pressione ENTER para fechar esta janela"
}
