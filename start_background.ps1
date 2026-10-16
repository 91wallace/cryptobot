[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [ValidateSet("lite-3.8")]
    [string]$Estrategia = "lite-3.8",

    [Parameter(Mandatory = $false)]
    [ValidateSet("paper", "live")]
    [string]$Modo = "paper",

    [Parameter(Mandatory = $false)]
    [switch]$Backtest30d
)

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    $VenvPython = "python"
}

$LogDir = Join-Path $RootDir "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$PidFile = Join-Path $RootDir "data\bot.pid"
$MetaFile = Join-Path $RootDir "data\bot_meta.json"

# Se o usuário pediu para rodar o Backtest de 30 dias em background
if ($Backtest30d) {
    Write-Host "==================================================================" -ForegroundColor Cyan
    Write-Host " 🚀 INICIANDO BACKTEST DE 30 DIAS EM SEGUNDO PLANO (DESACOPLADO)" -ForegroundColor Cyan
    Write-Host "==================================================================" -ForegroundColor Cyan
    Write-Host "Estratégia : $Estrategia (Rompimento Trailingstop Lite-3.8)" -ForegroundColor Yellow
    Write-Host "Script     : backtests/apex_dinamics/backtest_lite38_30dias.py" -ForegroundColor Yellow
    Write-Host "Log        : logs/backtest_30d.log" -ForegroundColor Yellow

    $ScriptBacktest = "backtests/apex_dinamics/backtest_lite38_30dias.py"
    $Proc = Start-Process -FilePath $VenvPython `
        -ArgumentList $ScriptBacktest `
        -WorkingDirectory $RootDir `
        -RedirectStandardOutput "logs/backtest_30d.log" `
        -RedirectStandardError "logs/backtest_30d_err.log" `
        -WindowStyle Hidden `
        -PassThru

    Write-Host "`n✅ Backtest iniciado com PID: $($Proc.Id)!" -ForegroundColor Green
    Write-Host "Você pode desconectar com segurança. O backtest continuará executando." -ForegroundColor Green
    Write-Host "Para acompanhar: Get-Content logs/backtest_30d.log -Wait -Tail 20`n" -ForegroundColor DarkGray
    return
}

# Verifica se já existe instância rodando
if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid) {
        $Existing = Get-Process -Id $OldPid -ErrorAction SilentlyContinue
        if ($Existing) {
            Write-Host "⚠️ Já existe um bot em execução com PID $OldPid." -ForegroundColor Yellow
            Write-Host "Utilize .\status_bot.ps1 para ver os detalhes ou .\stop_bot.ps1 para interromper." -ForegroundColor Yellow
            return
        }
    }
}

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " 🚀 INICIANDO BOT EM SEGUNDO PLANO (DESACOPLADO DO SSH)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "Estratégia  : $Estrategia" -ForegroundColor Yellow
Write-Host "Modo        : $Modo" -ForegroundColor Yellow
Write-Host "Log Saída   : logs/bot_stdout.log" -ForegroundColor Yellow
Write-Host "Log Erro    : logs/bot_stderr.log" -ForegroundColor Yellow

$ScriptToRun = "main.py"

# Metadados de execução persistidos
$Meta = @{
    "estrategia" = $Estrategia
    "modo"       = $Modo
    "script"     = $ScriptToRun
    "start_time" = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
}

$env:ESTRATEGIA = $Estrategia
if ($Modo) { $env:MODO = $Modo }

$CmdLine = "`"$VenvPython`" `"$ScriptToRun`""
$CimResult = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $CmdLine
    CurrentDirectory = $RootDir
}

$NewPid = $CimResult.ProcessId
$NewPid | Set-Content -Path $PidFile
$Meta["pid"] = $NewPid
$Meta | ConvertTo-Json | Set-Content -Path $MetaFile

Write-Host "`n✅ Bot iniciado com sucesso em segundo plano!" -ForegroundColor Green
Write-Host "PID gravado : $($Proc.Id)" -ForegroundColor Green
Write-Host "Sua sessão SSH pode ser desconectada a qualquer momento sem interromper o bot." -ForegroundColor Green
Write-Host "Para checar status e profit: .\status_bot.ps1" -ForegroundColor Cyan
Write-Host "Para acompanhar logs ao vivo: Get-Content logs/bot.log -Wait -Tail 30`n" -ForegroundColor DarkGray
