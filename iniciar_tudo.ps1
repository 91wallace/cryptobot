# ========================================================
# Inicia TUDO: bot em segundo plano + dashboard (acessivel no celular)
# Uso:  .\iniciar_tudo.ps1 [-Modo paper|live] [-Estrategia lite-3.8|apex-dinamics] [-SemBot]
# ========================================================
[CmdletBinding()]
param(
    [ValidateSet("paper", "live")]
    [string]$Modo = "paper",

    [ValidateSet("lite-3.8", "apex-dinamics")]
    [string]$Estrategia = "lite-3.8",

    [int]$Porta = 8501,

    [switch]$SemBot
)

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$Streamlit = Join-Path $RootDir ".venv\Scripts\streamlit.exe"
if (-not (Test-Path $Streamlit)) {
    Write-Host "[ERRO] Ambiente virtual (.venv) nao encontrado!" -ForegroundColor Red
    return
}

# 1) Bot em segundo plano
if (-not $SemBot) {
    & (Join-Path $RootDir "start_background.ps1") -Estrategia $Estrategia -Modo $Modo
}

# 2) Dashboard (se ja estiver rodando na porta, nao sobe outro)
$EmUso = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
if ($EmUso) {
    Write-Host "Dashboard ja esta rodando na porta $Porta." -ForegroundColor Yellow
} else {
    New-Item -ItemType Directory -Force -Path (Join-Path $RootDir "logs") | Out-Null
    # app.py da raiz configura o sys.path (core/, strategies/, dashboard/)
    Start-Process -FilePath $Streamlit `
        -ArgumentList "run", "app.py", "--server.address", "0.0.0.0", "--server.port", $Porta, "--server.headless", "true" `
        -WorkingDirectory $RootDir `
        -RedirectStandardOutput "logs/dashboard_stdout.log" `
        -RedirectStandardError "logs/dashboard_stderr.log" `
        -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

$Ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " TUDO NO AR" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "Dashboard no PC      : http://localhost:$Porta" -ForegroundColor Green
if ($Ip) { Write-Host "Dashboard no celular : http://${Ip}:$Porta  (mesma rede Wi-Fi)" -ForegroundColor Green }
Write-Host "Status do bot        : .\status_bot.ps1" -ForegroundColor White
Write-Host "Parar tudo           : .\parar_tudo.ps1" -ForegroundColor White
Start-Process "http://localhost:$Porta"
