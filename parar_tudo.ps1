# Para o bot e o dashboard
[CmdletBinding()]
param([int]$Porta = 8501)

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $RootDir "stop_bot.ps1")

$Conn = Get-NetTCPConnection -LocalPort $Porta -State Listen -ErrorAction SilentlyContinue
foreach ($c in $Conn) {
    Write-Host "Parando dashboard (PID $($c.OwningProcess))..." -ForegroundColor Yellow
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
}
if (-not $Conn) { Write-Host "Dashboard nao estava rodando." -ForegroundColor Gray }
