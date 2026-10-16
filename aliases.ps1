# ==========================================================================
# Aliases do projeto CryptoBot:  /iniciar   /status   /dash
#
# Os comandos so funcionam quando o terminal esta DENTRO da pasta do projeto
# (ou de uma subpasta). Fora dela, mostram um aviso e nao fazem nada.
#
# Carregado pelo perfil do PowerShell (instalar com .\instalar_aliases.ps1).
# ==========================================================================

$script:CryptoBotRoot = $PSScriptRoot

function script:Test-CryptoBotRepo {
    $atual = (Get-Location).ProviderPath.TrimEnd('\')
    $raiz  = $script:CryptoBotRoot.TrimEnd('\')
    if ($atual -ieq $raiz -or $atual.StartsWith($raiz + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    Write-Host "Este comando so funciona dentro do repositorio do projeto:" -ForegroundColor Yellow
    Write-Host "  cd $raiz" -ForegroundColor Yellow
    return $false
}

function script:Get-EnvValor([string]$Chave, [string]$Padrao) {
    $arq = Join-Path $script:CryptoBotRoot ".env"
    if (Test-Path $arq) {
        $linha = Select-String -Path $arq -Pattern "^\s*$Chave\s*=" | Select-Object -First 1
        if ($linha) {
            return ($linha.Line -split '=', 2)[1].Trim().Trim('"').Trim("'")
        }
    }
    return $Padrao
}

# --------------------------------------------------------------------------
# /iniciar - pergunta a estrategia e inicia o bot desacoplado da sessao SSH
# --------------------------------------------------------------------------
function global:/iniciar {
    param([string]$Escolha, [string]$Modo)
    if (-not (Test-CryptoBotRepo)) { return }

    $estrategias = [ordered]@{ "1" = "lite-3.8"; "2" = "apex-dinamics" }

    if (-not $Escolha) {
        Write-Host ""
        Write-Host "Qual estrategia deseja iniciar?" -ForegroundColor Cyan
        foreach ($k in $estrategias.Keys) { Write-Host "  [$k] $($estrategias[$k])" }
        Write-Host "  [0] cancelar"
        $Escolha = Read-Host "Opcao"
    }
    if ($Escolha -eq "0" -or -not $Escolha) { Write-Host "Cancelado."; return }

    if ($estrategias.Contains($Escolha)) { $estrategia = $estrategias[$Escolha] }
    elseif ($estrategias.Values -contains $Escolha) { $estrategia = $Escolha }
    else { Write-Host "Opcao invalida: $Escolha" -ForegroundColor Red; return }

    if (-not $Modo) {
        $modoEnv = (Get-EnvValor "MODO" "paper").ToLower()
        Write-Host ""
        Write-Host "Em qual ambiente deseja executar?" -ForegroundColor Cyan
        Write-Host "  [1] paper (Simulador em tempo real com precos da KuCoin - Sem risco financeiro)" -ForegroundColor Green
        Write-Host "  [2] live  (MERCADO REAL - Envia ordens reais com saldo da KuCoin)" -ForegroundColor Red
        $escolhaModo = Read-Host "Opcao (Padrao: $modoEnv)"
        if ($escolhaModo -eq "1" -or $escolhaModo -eq "paper") {
            $Modo = "paper"
        } elseif ($escolhaModo -eq "2" -or $escolhaModo -eq "live") {
            $Modo = "live"
        } else {
            $Modo = if ($modoEnv -eq "live") { "live" } else { "paper" }
        }
    }

    $cor = if ($Modo -eq "live") { "Red" } else { "Green" }
    Write-Host "Iniciando: Estrategia = $estrategia | Modo = $Modo" -ForegroundColor $cor

    & (Join-Path $script:CryptoBotRoot "start_background.ps1") -Estrategia $estrategia -Modo $Modo
}

# --------------------------------------------------------------------------
# /status - lista todos os processos do projeto rodando + profit
# --------------------------------------------------------------------------
function global:/status {
    if (-not (Test-CryptoBotRepo)) { return }
    $raiz = $script:CryptoBotRoot

    $procs = @(Get-CimInstance Win32_Process -Filter "Name LIKE '%python%'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape("cryptobot") -or
                       ($_.CommandLine -match "main\.py|backtest" -and $_.ExecutablePath -like "$raiz*") } |
        Where-Object { $_.CommandLine -match "main\.py|backtest" })

    # Metadados gravados pelo start_background.ps1
    $meta = $null
    $metaFile = Join-Path $raiz "data\bot_meta.json"
    if (Test-Path $metaFile) { try { $meta = Get-Content $metaFile -Raw | ConvertFrom-Json } catch {} }

    Write-Host ""
    Write-Host "================ STATUS CRYPTOBOT ================" -ForegroundColor Cyan

    if ($procs.Count -eq 0) {
        Write-Host "Bot: PARADO (nenhum processo em execucao)" -ForegroundColor Yellow
    } else {
        Write-Host "Processos ativos: $($procs.Count)" -ForegroundColor Green
        foreach ($p in $procs) {
            $tipo = if ($p.CommandLine -match "backtest") { "backtest" } else { "bot" }
            $uptime = (Get-Date) - $p.CreationDate
            $tempo = "{0}d {1:00}h{2:00}m" -f $uptime.Days, $uptime.Hours, $uptime.Minutes
            $extra = ""
            if ($tipo -eq "bot" -and $meta -and "$($meta.pid)" -eq "$($p.ProcessId)") {
                $extra = " | estrategia=$($meta.estrategia) modo=$($meta.modo)"
            }
            Write-Host ("  [ATIVO] {0,-8} PID {1,-6} rodando ha {2}{3}" -f $tipo, $p.ProcessId, $tempo, $extra) -ForegroundColor Green
        }
    }

    # Dashboard
    $dash = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($dash) { Write-Host "  [ATIVO] dashboard PID $($dash.OwningProcess) porta 8501" -ForegroundColor Green }
    else { Write-Host "  [PARADO] dashboard" -ForegroundColor DarkGray }

    # Profit (simulador paper: o arquivo mais recente de cada mercado)
    Write-Host "--------------------------------------------------" -ForegroundColor DarkGray
    $achou = $false
    foreach ($mercado in "futures", "spot") {
        $arqs = @(@("data", "core\data") | ForEach-Object { Join-Path $raiz "$_\paper_$mercado.json" } |
            Where-Object { Test-Path $_ } | Sort-Object { (Get-Item $_).LastWriteTime } -Descending)
        if ($arqs.Count -eq 0) { continue }
        try { $paper = Get-Content $arqs[0] -Raw | ConvertFrom-Json } catch { continue }
        $achou = $true
        $ini = [double]$paper.capital_inicial
        $saldo = [double]$paper.saldo
        $lucro = $saldo - $ini
        $pct = if ($ini -gt 0) { $lucro / $ini * 100 } else { 0 }
        $abertas = if ($paper.posicoes) { @($paper.posicoes.PSObject.Properties).Count } else { 0 }
        $trades = if ($paper.historico) { @($paper.historico).Count } else { 0 }
        $cor = if ($lucro -ge 0) { "Green" } else { "Red" }
        Write-Host ("Paper {0,-8}: saldo {1:N2} USDT | profit {2:+0.00;-0.00} USDT ({3:+0.00;-0.00}%) | posicoes abertas {4} | execucoes {5}" -f `
            $mercado, $saldo, $lucro, $pct, $abertas, $trades) -ForegroundColor $cor
    }
    if (-not $achou) { Write-Host "Sem dados do simulador paper." -ForegroundColor DarkGray }

    if ((Get-EnvValor "MODO" "paper").ToLower() -eq "live") {
        Write-Host "Modo live: o profit real esta na KuCoin (veja o dashboard > Conta Real)." -ForegroundColor Yellow
    }
    Write-Host "==================================================" -ForegroundColor Cyan
}

# --------------------------------------------------------------------------
# /dash - inicia o dashboard (acessivel no celular)
# --------------------------------------------------------------------------
function global:/dash {
    if (-not (Test-CryptoBotRepo)) { return }
    & (Join-Path $script:CryptoBotRoot "iniciar_tudo.ps1") -SemBot
}

# --------------------------------------------------------------------------
# /tui - inicia o dashboard interativo via terminal (TUI)
# --------------------------------------------------------------------------
function global:/tui {
    param(
        [switch]$Demo,
        [double]$Interval = 1.0
    )
    if (-not (Test-CryptoBotRepo)) { return }
    & (Join-Path $script:CryptoBotRoot "tui.ps1") @PSBoundParameters
}

