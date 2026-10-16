[CmdletBinding()]
param()

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $RootDir "data\bot.pid"
$MetaFile = Join-Path $RootDir "data\bot_meta.json"

$Running = $false
$PidNum = $null

if (Test-Path $PidFile) {
    $PidNum = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($PidNum) {
        $Proc = Get-Process -Id $PidNum -ErrorAction SilentlyContinue
        if ($Proc) {
            $Running = $true
        }
    }
}

if (-not $Running) {
    # Busca por processos python executando main.py especificamente deste projeto
    $Fallback = Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" | Where-Object { 
        $_.CommandLine -like "*cryptobot*main.py*" 
    }
    if ($Fallback) {
        $Running = $true
        $PidNum = $Fallback[0].ProcessId
    }
}

Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host " 🤖 STATUS DO BOT & PERFORMANCE EM TEMPO REAL" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

if (-not $Running) {
    Write-Host "Status: PARADO (Nenhum bot em execução no momento)" -ForegroundColor Yellow
    Write-Host "==================================================================`n" -ForegroundColor Cyan
    return
}

$Meta = $null
if (Test-Path $MetaFile) {
    try {
        $Meta = Get-Content $MetaFile -Raw | ConvertFrom-Json
    } catch {}
}

$Estrategia = if ($Meta -and $Meta.estrategia) { $Meta.estrategia } else { "Rompimento Trailingstop Lite-3.8" }
$Modo = if ($Meta -and $Meta.modo) { $Meta.modo } else { "paper" }
$StartTime = if ($Meta -and $Meta.start_time) { $Meta.start_time } else { "N/D" }

Write-Host "Status      : RODANDO EM SEGUNDO PLANO ✅" -ForegroundColor Green
Write-Host "PID         : $PidNum" -ForegroundColor White
Write-Host "Estratégia  : $Estrategia" -ForegroundColor White
Write-Host "Modo        : $Modo" -ForegroundColor White
Write-Host "Iniciado em : $StartTime" -ForegroundColor White

# Leitura do Saldo/Profit de acordo com o modo
if ($Modo -eq "live") {
    $VenvPy = Join-Path $RootDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPy)) { $VenvPy = "python" }
    
    try {
        $RealBalJson = & $VenvPy -c "import os, ccxt, dotenv, json; dotenv.load_dotenv(); ex = ccxt.kucoinfutures({'apiKey': os.getenv('KUCOIN_API_KEY'), 'secret': os.getenv('KUCOIN_API_SECRET'), 'password': os.getenv('KUCOIN_API_PASSPHRASE')}); b = ex.fetch_balance(); print(json.dumps({'free': b.get('USDT',{}).get('free', 0.0), 'used': b.get('USDT',{}).get('used', 0.0), 'total': b.get('USDT',{}).get('total', 0.0)}))" 2>$null
        $RealBal = $RealBalJson | ConvertFrom-Json
        
        Write-Host "------------------------------------------------------------------" -ForegroundColor DarkGray
        Write-Host "Ambiente        : CONTA REAL (KuCoin Futures)" -ForegroundColor Yellow
        Write-Host "Saldo Total     : $ $("{0:N2}" -f [double]$RealBal.total) USDT" -ForegroundColor White
        Write-Host "Saldo Livre     : $ $("{0:N2}" -f [double]$RealBal.free) USDT" -ForegroundColor White
        Write-Host "Margem em Uso   : $ $("{0:N2}" -f [double]$RealBal.used) USDT" -ForegroundColor White
    } catch {
        Write-Host "------------------------------------------------------------------" -ForegroundColor DarkGray
        Write-Host "Ambiente        : CONTA REAL (Falha ao obter saldo da KuCoin via API)" -ForegroundColor Red
    }
} else {
    $PaperFile = Join-Path $RootDir "data\paper_futures.json"
    if (Test-Path $PaperFile) {
        try {
            $Paper = Get-Content $PaperFile -Raw | ConvertFrom-Json
            $CapInicial = [double]$Paper.capital_inicial
            $Saldo = [double]$Paper.saldo
            $ProfitDolar = $Saldo - $CapInicial
            $ProfitPct = if ($CapInicial -gt 0) { ($ProfitDolar / $CapInicial) * 100 } else { 0.0 }
            $TradesCount = if ($Paper.historico) { $Paper.historico.Count } else { 0 }
            $PosicoesCount = if ($Paper.posicoes) { ($Paper.posicoes | Get-Member -MemberType NoteProperty).Count } else { 0 }

            Write-Host "------------------------------------------------------------------" -ForegroundColor DarkGray
            Write-Host "Ambiente        : SIMULAÇÃO (Paper Trading)" -ForegroundColor Cyan
            Write-Host "Capital Inicial : $ $("{0:N2}" -f $CapInicial) USDT" -ForegroundColor White
            Write-Host "Saldo Simulado  : $ $("{0:N2}" -f $Saldo) USDT" -ForegroundColor White
            
            if ($ProfitDolar -ge 0) {
                Write-Host "Profit Atual ($): +$ $("{0:N2}" -f $ProfitDolar) USDT" -ForegroundColor Green
                Write-Host "Retorno Atual (%): +$("{0:N2}" -f $ProfitPct)%" -ForegroundColor Green
            } else {
                Write-Host "Profit Atual ($): -$ $("{0:N2}" -f [math]::Abs($ProfitDolar)) USDT" -ForegroundColor Red
                Write-Host "Retorno Atual (%): $(" {0:N2}" -f $ProfitPct)%" -ForegroundColor Red
            }
            
            Write-Host "Posições Abertas: $PosicoesCount" -ForegroundColor White
            Write-Host "Trades Fechados : $TradesCount" -ForegroundColor White
        } catch {
            Write-Host "Não foi possível carregar os dados financeiros de data\paper_futures.json" -ForegroundColor DarkGray
        }
    }
}

Write-Host "==================================================================`n" -ForegroundColor Cyan
