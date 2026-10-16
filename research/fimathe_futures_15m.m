% =========================================================================
% FIMATHE_FUTURES_15M.M - Backtest Fimathe Futuros 15m (Long & Short)
% =========================================================================
% Dados: KuCoin Futures (BTC/USDT:USDT)
% Timeframe: 15 minutos
% Modos de Stop Loss: fora_caixinha (1:1), linha_50 e rompimento
% =========================================================================

clear; clc; close all;

arquivo = fullfile('data', 'fimathe_matlab_15m_500.csv');
if ~isfile(arquivo)
    arquivo = fullfile('data', 'fimathe_matlab_15m.csv');
end

if ~isfile(arquivo)
    error('Arquivo de dados nao encontrado. Execute export_matlab.py primeiro.');
end

fprintf('=== Carregando dados de Futuros da KuCoin: %s ===\n', arquivo);
opts = detectImportOptions(arquivo);
dados = readtable(arquivo, opts);

n = height(dados);
timestamps = datetime(dados.timestamp, 'InputFormat', 'yyyy-MM-dd HH:mm:ss');
precos_open  = dados.open;
precos_high  = dados.high;
precos_low   = dados.low;
precos_close = dados.close;
atr = dados.atr_14;

fprintf('Total de candles de 15m: %d (de %s ate %s)\n', n, datestr(timestamps(1)), datestr(timestamps(end)));

%% 1. Destaque das Ultimas 2 Horas (8 candles de 15m)
idx_2h = max(1, n - 7):n;
fprintf('\n--- Candles das Ultimas 2 Horas (15m) ---\n');
disp(dados(idx_2h, {'timestamp', 'open', 'high', 'low', 'close', 'atr_14'}));

%% 2. Simulacao do Motor Fimathe para Futuros (Compra & Venda)
modos = {'fora_caixinha', 'linha_50', 'rompimento'};
janela_pivo = 3;
qtd_niveis = 3;
folga_stop = 0.10;
taxa_corretagem = 0.001; % 0.1% por ponta
slippage = 0.0005;       % 0.05%

figure('Name', 'Backtest Fimathe Futuros 15m', 'NumberTitle', 'off', 'Position', [100 100 1000 650]);

subplot(2, 1, 1);
plot(timestamps, precos_close, 'k-', 'LineWidth', 1.2);
hold on; grid on;
title('Preco BTC Futures (15m) e Trades Executados');
ylabel('Preco USDT');

% Destacar a janela das ultimas 2 horas no grafico
x_box = [timestamps(idx_2h(1)) timestamps(idx_2h(end)) timestamps(idx_2h(end)) timestamps(idx_2h(1))];
y_lim = [min(precos_low) max(precos_high)];
patch(x_box, [y_lim(1) y_lim(1) y_lim(2) y_lim(2)], [0.9 0.9 0.9], 'FaceAlpha', 0.4, 'EdgeColor', 'none', 'DisplayName', 'Ultimas 2h');

cores = {'b', 'm', 'g'};

for m_idx = 1:length(modos)
    modo = modos{m_idx};
    pos_aberta = false;
    pos_lado = 0; % +1 long, -1 short
    pos_entrada = 0;
    pos_stop = 0;
    pos_alvo = 0;
    
    trades_pnl = [];
    trades_tipo = {};
    trades_in_idx = [];
    trades_out_idx = [];
    
    min_candles = 4 * janela_pivo + 2;
    for i = min_candles:n
        % Gerenciamento de posicao aberta
        if pos_aberta
            h = precos_high(i);
            l = precos_low(i);
            saida = false;
            preco_saida = 0;
            
            if pos_lado == 1 % LONG
                if h >= pos_alvo
                    saida = true;
                    preco_saida = pos_alvo * (1 - slippage);
                elseif l <= pos_stop
                    saida = true;
                    preco_saida = pos_stop * (1 - slippage);
                end
            else % SHORT
                if l <= pos_alvo
                    saida = true;
                    preco_saida = pos_alvo * (1 + slippage);
                elseif h >= pos_stop
                    saida = true;
                    preco_saida = pos_stop * (1 + slippage);
                end
            end
            
            if saida
                pnl = (preco_saida - pos_entrada) / pos_entrada * 100 * pos_lado - (taxa_corretagem * 2 * 100);
                trades_pnl(end+1) = pnl; %#ok<AGROW>
                trades_out_idx(end+1) = i; %#ok<AGROW>
                pos_aberta = false;
            end
            continue;
        end
        
        % Deteccao de Pivos nos candles fechados
        idx_sub = 1:i;
        highs_sub = precos_high(idx_sub);
        lows_sub  = precos_low(idx_sub);
        
        % Pivôs simples janela_pivo
        len_sub = length(idx_sub);
        p_topos = []; p_fundos = [];
        for k = (janela_pivo + 1):(len_sub - janela_pivo)
            if highs_sub(k) == max(highs_sub(k-janela_pivo:k+janela_pivo))
                p_topos(end+1) = highs_sub(k); %#ok<AGROW>
            end
            if lows_sub(k) == min(lows_sub(k-janela_pivo:k+janela_pivo))
                p_fundos(end+1) = lows_sub(k); %#ok<AGROW>
            end
        end
        
        if length(p_topos) < 2 || length(p_fundos) < 2
            continue;
        end
        
        topo_ref = p_topos(end);
        fundo_ref = p_fundos(end);
        if fundo_ref >= topo_ref
            continue;
        end
        
        amp = topo_ref - fundo_ref;
        if (amp / topo_ref) * 100 < 0.2
            continue;
        end
        
        % Tendencia
        tend_alta  = (p_topos(end) > p_topos(end-1)) && (p_fundos(end) > p_fundos(end-1));
        tend_baixa = (p_topos(end) < p_topos(end-1)) && (p_fundos(end) < p_fundos(end-1));
        
        fech_atual = precos_close(i);
        fech_ant   = precos_close(i-1);
        
        % COMPRA (LONG)
        if tend_alta
            zona_topo = topo_ref + amp;
            zona_fundo = topo_ref;
            if (fech_ant <= zona_topo) && (fech_atual > zona_topo)
                alvo_1 = zona_topo + amp;
                if strcmp(modo, 'fora_caixinha')
                    dist = abs(alvo_1 - fech_atual);
                    stop_val = fech_atual - dist; % 1:1
                elseif strcmp(modo, 'linha_50')
                    stop_val = (zona_fundo + zona_topo) / 2 - (folga_stop * amp);
                else
                    stop_val = zona_topo - (folga_stop * amp);
                end
                
                pos_aberta = true;
                pos_lado = 1;
                pos_entrada = fech_atual * (1 + slippage);
                pos_stop = stop_val;
                pos_alvo = alvo_1;
                trades_tipo{end+1} = 'LONG'; %#ok<AGROW>
                trades_in_idx(end+1) = i; %#ok<AGROW>
                plot(timestamps(i), fech_atual, 'g^', 'MarkerFaceColor', 'g', 'MarkerSize', 8);
            end
        end
        
        % VENDA (SHORT)
        if tend_baixa
            zona_fundo = fundo_ref - amp;
            zona_topo  = fundo_ref;
            if (fech_ant >= zona_fundo) && (fech_atual < zona_fundo)
                alvo_1 = zona_fundo - amp;
                if strcmp(modo, 'fora_caixinha')
                    dist = abs(alvo_1 - fech_atual);
                    stop_val = fech_atual + dist; % 1:1
                elseif strcmp(modo, 'linha_50')
                    stop_val = (zona_fundo + zona_topo) / 2 + (folga_stop * amp);
                else
                    stop_val = zona_fundo + (folga_stop * amp);
                end
                
                pos_aberta = true;
                pos_lado = -1;
                pos_entrada = fech_atual * (1 - slippage);
                pos_stop = stop_val;
                pos_alvo = alvo_1;
                trades_tipo{end+1} = 'SHORT'; %#ok<AGROW>
                trades_in_idx(end+1) = i; %#ok<AGROW>
                plot(timestamps(i), fech_atual, 'rv', 'MarkerFaceColor', 'r', 'MarkerSize', 8);
            end
        end
    end
    
    fprintf('\n======================================================\n');
    fprintf(' MODO: %s (Futuros 15m)\n', modo);
    fprintf('======================================================\n');
    fprintf(' Total Trades:  %d\n', length(trades_pnl));
    if ~isempty(trades_pnl)
        wins = sum(trades_pnl > 0);
        fprintf(' Win Rate:      %.1f%% (%dW / %dL)\n', (wins/length(trades_pnl))*100, wins, length(trades_pnl)-wins);
        fprintf(' Retorno Total: %+.2f%%\n', sum(trades_pnl));
    else
        fprintf(' Sem operacoes disparadas neste periodo.\n');
    end
end

subplot(2, 1, 2);
plot(timestamps, atr, 'b-', 'LineWidth', 1.1);
grid on;
title('ATR 14 Periodos (Volatilidade Intra-Day 15m)');
xlabel('Timestamp UTC');
ylabel('ATR (USDT)');
