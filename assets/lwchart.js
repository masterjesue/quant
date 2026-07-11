// assets/lwchart.js
// Gráfico de candles via Lightweight Charts (TradingView, Apache-2.0,
// github.com/tradingview/lightweight-charts) — vendorizado em
// assets/lightweight-charts.js, sem nenhuma conta/plano/feed da
// TradingView: só a biblioteca de desenho, alimentada com os nossos
// próprios dados (mesmo pipeline MT5/Parquet que já roda o dashboard).
//
// Uma única instância, na seção Price Action (morava no Hero até
// 2026-07-10; mudou de lugar pra ficar junto da leitura de S/R) — tema
// dark pra combinar com o resto do dashboard clássico (ver
// dashboard.py::C). Mesma ideia do gráfico do SQ'98 (assets_98/lwchart.js),
// cores diferentes.
//
// Dados chegam via clientside_callback em dashboard.py, lendo
// store-candles-grafico (Output de atualizar_dashboard, mesmo tick de 1s
// que já atualiza o resto). window.SQ_CHART é o único ponto de contato
// entre o callback e este script.
(function () {
  'use strict';

  window.SQ_CHART = { chart: null, series: null, srLines: [], mmeSeries: null };

  var COR_SR = { resistencia: '#E05C5C', suporte: '#2ECC8A', pivo: '#4A90D9' };

  // Único ponto de entrada chamado pelo clientside_callback em
  // dashboard.py — `payload` vem de store-niveis-sr (recalculado a cada
  // tick de 1s, junto do resto de `atualizar_dashboard`, no formato
  // {ativo, niveis}) e `visivel` do botão de olho (store-sr-visivel).
  // Sempre limpa as linhas antigas antes de redesenhar — createPriceLine
  // só ADICIONA, nunca substitui sozinho.
  //
  // `ativoAtual` vem de store-ativo (como State, não Input) — guarda
  // contra uma resposta ATRASADA de `atualizar_dashboard`: o servidor
  // roda com threaded=True, então nada garante que a resposta calculada
  // ANTES da troca de ativo chegue ao navegador ANTES da resposta
  // calculada DEPOIS (disparada pela própria troca). Se a atrasada
  // (ainda do ativo antigo) chegasse por último, ela reintroduziria
  // linhas de S/R na escala errada (WIN ~170-190 mil vs WDO ~5 mil) por
  // cima dos candles já trocados — bug relatado em 2026-07-10.
  window.SQ_CHART.desenharNiveisSR = function (payload, visivel, ativoAtual) {
    var niveis = payload && payload.niveis;
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) {
      return;  // resposta de um ativo que não é mais o selecionado — descarta
    }
    var serie = window.SQ_CHART.series;
    window.SQ_CHART.srLines.forEach(function (pl) { if (serie) serie.removePriceLine(pl); });
    window.SQ_CHART.srLines = [];
    if (!serie || !visivel || !niveis) return;
    niveis.forEach(function (n) {
      var ehPivo = n.tipo === 'pivo';
      var pl = serie.createPriceLine({
        price: n.preco,
        color: COR_SR[n.tipo] || '#8888AA',
        lineWidth: n.forte ? 2 : 1,
        lineStyle: ehPivo ? LightweightCharts.LineStyle.Dotted
          : (n.forte ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed),
        axisLabelVisible: true,
        title: n.label || '',
      });
      window.SQ_CHART.srLines.push(pl);
    });
    var btn = document.getElementById('btn-sr-toggle');
    if (btn) btn.style.opacity = visivel ? '1' : '0.45';
  };

  // Chamado assim que o ativo troca, antes do tick de 1s recalcular
  // store-niveis-sr — WIN (~170-190 mil) e WDO (~5 mil) são ordens de
  // grandeza de preço bem diferentes; uma linha de S/R da escala antiga
  // ainda desenhada junto dos candles novos distorce o autoscale do eixo
  // (candles novos somem, espremidos numa borda). Ver mesmo comentário em
  // assets_98/lwchart.js.
  window.SQ_CHART.limparTudo = function () {
    var serie = window.SQ_CHART.series;
    window.SQ_CHART.srLines.forEach(function (pl) { if (serie) serie.removePriceLine(pl); });
    window.SQ_CHART.srLines = [];
  };

  // MME21 (EMA21) — linha, não nível fixo (ver Rastreador de Tendência).
  // Mesma arquitetura de 2 stores já usada pros candles: setData (histórico
  // completo, tick de 30s/troca de ativo) e update (ponto em formação,
  // tick de 1s) — mesmo cuidado de nunca reconstruir a linha inteira todo
  // segundo. Mesma tag de `ativo` + descarte de resposta atrasada que já
  // corrige o mesmo risco em `desenharNiveisSR` (server threaded=True).
  window.SQ_CHART.atualizarMME21Serie = function (payload, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) return;
    var serie = window.SQ_CHART.mmeSeries;
    var dados = payload && payload.serie;
    if (serie && dados && dados.length) serie.setData(dados);
  };
  window.SQ_CHART.atualizarMME21Live = function (payload, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) return;
    var serie = window.SQ_CHART.mmeSeries;
    var ponto = payload && payload.ponto;
    if (serie && ponto) serie.update(ponto);
  };
  // Ícone de olho — esconder/mostrar sem destruir a série (mais barato que
  // remover/recriar, já que é uma linha única, não N linhas de preço).
  window.SQ_CHART.toggleMME21 = function (visivel) {
    var serie = window.SQ_CHART.mmeSeries;
    if (serie) serie.applyOptions({ visible: !!visivel });
    var btn = document.getElementById('btn-mme21-toggle');
    if (btn) btn.style.opacity = visivel ? '1' : '0.45';
  };

  // Reforça o autoscale logo após um setData() de candles inteiro novo —
  // blindagem barata contra qualquer resíduo de escala anterior.
  window.SQ_CHART.refitEscala = function () {
    var chart = window.SQ_CHART.chart;
    if (!chart) return;
    try {
      chart.priceScale('right').applyOptions({ autoScale: true });
      chart.timeScale().fitContent();
    } catch (e) { /* chart ainda não pronto — próximo ciclo tenta de novo */ }
  };

  function criarChart() {
    var el = document.getElementById('lw-chart-price-action');
    if (!el || el.dataset.lwReady) return;
    el.dataset.lwReady = '1';

    var chart = LightweightCharts.createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: {
        background: { color: '#0A0A0F' },
        textColor: '#8888AA',
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1C1C28' },
        horzLines: { color: '#1C1C28' },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#1C1C28' },
      timeScale: { borderColor: '#1C1C28', timeVisible: true, secondsVisible: false },
    });
    window.SQ_CHART.chart = chart;
    window.SQ_CHART.series = chart.addCandlestickSeries({
      upColor: '#2ECC8A', downColor: '#E05C5C',
      borderUpColor: '#2ECC8A', borderDownColor: '#E05C5C',
      wickUpColor: '#2ECC8A', wickDownColor: '#E05C5C',
    });
    // Azul petróleo — cor pedida por Jesué, sem relação com a paleta
    // semântica de mercado (verde/vermelho já usados pros candles/S/R).
    window.SQ_CHART.mmeSeries = chart.addLineSeries({
      color: '#1B4F5C', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
    });

    var ro = new ResizeObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var r = entries[i].contentRect;
        chart.resize(Math.max(50, r.width), Math.max(50, r.height));
      }
    });
    ro.observe(el);
  }

  // Mesmo padrão de espera segura dos outros scripts (window_manager.js,
  // clippy_agent.js, assets_98/lwchart.js): o container só existe depois
  // do 1º render do Dash.
  var observer = new MutationObserver(function () {
    if (!window.SQ_CHART.series) criarChart();
    if (window.SQ_CHART.series) observer.disconnect();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  criarChart();
})();
