[CmdletBinding()]
param()

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $RootDir "data\bot.pid"
$MetaFile = Join-Path $RootDir "data\bot_meta.json"

$Stopped = $false

if (Test-Path $PidFile) {
    $PidNum = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($PidNum) {
        $Proc = Get-Process -Id $PidNum -ErrorAction SilentlyContinue
        if ($Proc) {
            Write-Host "Interrompendo processo PID $PidNum..." -ForegroundColor Yellow
            Stop-Process -Id $PidNum -Force
            $Stopped = $true
        }
    }
    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

# Limpeza de qualquer processo residual específico deste projeto
$Processes = Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" | Where-Object { 
    $_.CommandLine -like "*cryptobot*main.py*" 
}
foreach ($p in $Processes) {
    Write-Host "Interrompendo processo residual main.py (PID: $($p.ProcessId))..." -ForegroundColor Yellow
    Stop-Process -Id $p.ProcessId -Force
    $Stopped = $true
}

if ($Stopped) {
    Write-Host "🛑 Bot interrompido com sucesso!" -ForegroundColor Green
} else {
    Write-Host "ℹ️ Nenhum bot ativo para interromper." -ForegroundColor Gray
}

if (Test-Path $MetaFile) {
    Remove-Item -Path $MetaFile -Force -ErrorAction SilentlyContinue
}

