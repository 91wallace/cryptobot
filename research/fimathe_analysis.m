% =========================================================================
% FIMATHE_ANALYSIS.M - Script MATLAB para Pesquisa e Calibração Fimathe
% =========================================================================
% Como usar no MATLAB:
%   1. Execute no terminal Python: python export_matlab.py --limit 1000
%   2. No MATLAB, navegue até a pasta do projeto e execute este script:
%      >> fimathe_analysis
% =========================================================================

clear; clc; close all;

arquivo = fullfile('data', 'fimathe_matlab.csv');
if ~isfile(arquivo)
    error('Arquivo %s nao encontrado. Gere com export_matlab.py primeiro.', arquivo);
end

fprintf('Carregando dados de %s...\n', arquivo);
opts = detectImportOptions(arquivo);
dados = readtable(arquivo, opts);

precos = dados.close;
altas = dados.high;
baixas = dados.low;
atr = dados.atr_14;
volatilidade = dados.volatilidade_20;
timestamps = datetime(dados.timestamp, 'InputFormat', 'yyyy-MM-dd HH:mm:ss');

%% 1. Grafico de Preco e ATR (Volatilidade)
figure('Name', 'Analise Fimathe & Volatilidade', 'NumberTitle', 'off');

subplot(2, 1, 1);
plot(timestamps, precos, 'k-', 'LineWidth', 1.2);
hold on;
grid on;
title('Preco de Fechamento com Pivos Identificados');
ylabel('Preco (USDT)');

% Destacar pivos
idx_topos = find(dados.pivo_topo == 1);
idx_fundos = find(dados.pivo_fundo == 1);
plot(timestamps(idx_topos), altas(idx_topos), 'r^', 'MarkerFaceColor', 'r', 'DisplayName', 'Pivo Topo');
plot(timestamps(idx_fundos), baixas(idx_fundos), 'bv', 'MarkerFaceColor', 'b', 'DisplayName', 'Pivo Fundo');
legend('show', 'Location', 'best');

subplot(2, 1, 2);
plot(timestamps, atr, 'm-', 'LineWidth', 1.2);
grid on;
title('ATR (14 periodos) - Medida de Amplitude Natural da Caixinha');
xlabel('Data/Hora');
ylabel('ATR (USDT)');

%% 2. Estatisticas para Calibracao de Hiperparametros
amplitudes_pivos = [];
for i = 2:length(idx_topos)
    fundo_anterior = max(baixas(idx_fundos(idx_fundos < idx_topos(i))));
    if ~isempty(fundo_anterior) && altas(idx_topos(i)) > fundo_anterior
        amp = altas(idx_topos(i)) - fundo_anterior;
        atr_local = atr(idx_topos(i));
        if atr_local > 0
            amplitudes_pivos = [amplitudes_pivos; amp / atr_local]; %#ok<AGROW>
        end
    end
end

if ~isempty(amplitudes_pivos)
    fprintf('\n=== Calibracao Estatistica de Amplitude / ATR ===\n');
    fprintf('Media da razao Amplitude/ATR: %.2f\n', mean(amplitudes_pivos));
    fprintf('Mediana da razao: %.2f\n', median(amplitudes_pivos));
    fprintf('Percentil 10%% (minimo recomendado): %.2f\n', prctile(amplitudes_pivos, 10));
    fprintf('Percentil 90%% (maximo recomendado): %.2f\n', prctile(amplitudes_pivos, 90));
end

fprintf('\nAnalise concluida! Use esses percentis para calibrar FATOR_MIN_ATR e FATOR_MAX_ATR no .env.\n');
