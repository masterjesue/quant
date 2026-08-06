// assets/lwchart_backtest.js
// Gráfico de tick do Drawer de detalhe da aba Backtesting (protótipo de
// backtesting tick a tick, ver spec_prototipo_backtesting_tick.md).
// Instância INDEPENDENTE da Lightweight Charts — nada a ver com
// window.SQ_CHART (aquele é o chart "ao vivo" do Price Action, atualizado
// a cada 1s/30s pelo tick do dashboard). Este só existe enquanto o Drawer
// de um trade está aberto, recriado do zero a cada trade clicado —
// sobreviver zoom/pan entre trades diferentes não faz sentido aqui.
//
// Marcador pontual (seta de entrada/saída) via `series.setMarkers()` —
// sem precedente no resto do repo antes deste arquivo (nem
// assets/lwchart.js nem assets_98/lwchart.js usam), confirmado presente
// no bundle vendorizado (assets/lightweight-charts.js).
(function () {
  'use strict';

  window.SQ_CHART_BACK = { chart: null, series: null };

  function limpar() {
    if (window.SQ_CHART_BACK.chart) {
      try { window.SQ_CHART_BACK.chart.remove(); } catch (e) { /* já destruído */ }
    }
    window.SQ_CHART_BACK.chart = null;
    window.SQ_CHART_BACK.series = null;
  }

  function corTema(nome, fallback) {
    var val = getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
    return val || fallback;
  }

  // payload: {ticks: [{time, value}], linhas: [{preco, cor, titulo}], marcadores: [{time, position, color, shape, text}]}
  // Chamado pelo clientside_callback em dashboard.py (Input em
  // store-backtest-chart-payload) toda vez que um trade novo é aberto no
  // Drawer. `payload` vem `null` nos casos de erro (sem tick baixado,
  // trade sem ticket) — só limpa e sai.
  //
  // A atualização do Store e a montagem do `children` do Drawer (incluindo
  // o container `lw-chart-backtest`) vêm da MESMA resposta multi-output do
  // callback Python, mas o clientside_callback (reagindo ao Store) pode
  // rodar ANTES do React terminar de montar o Drawer — achado real
  // 2026-08-04: o Drawer abria com os dados certos, mas o gráfico ficava
  // vazio (só o watermark da lib). Corrigido com espera curta em loop:
  // tenta achar o container E ele já ter largura medida (>0, garante que a
  // transição de slide-in do Drawer também já assentou) antes de desistir.
  var _geracaoAtual = 0;  // descarta tentativas de retry de um payload já substituído por um trade novo

  window.SQ_CHART_BACK.renderizar = function (payload) {
    limpar();
    _geracaoAtual += 1;
    if (!payload || !payload.ticks || !payload.ticks.length) return;
    _tentarRenderizar(payload, 0, _geracaoAtual);
  };

  var MAX_TENTATIVAS = 30;      // ~1.5s no total — cobre a transição de 220ms do Drawer com folga
  var INTERVALO_MS = 50;

  function _tentarRenderizar(payload, tentativa, geracao) {
    if (geracao !== _geracaoAtual) return;  // usuário já abriu outro trade — descarta
    var el = document.getElementById('lw-chart-backtest');
    if (!el || el.clientWidth === 0 || el.clientHeight === 0) {
      if (tentativa < MAX_TENTATIVAS) {
        setTimeout(function () { _tentarRenderizar(payload, tentativa + 1, geracao); }, INTERVALO_MS);
      }
      return;
    }
    _construirChart(el, payload);
  }

  function _construirChart(el, payload) {
    // Blindagem extra além do `limpar()`/`chart.remove()` — achado real
    // 2026-08-04: depois de vários ciclos de abrir/fechar o Drawer, o
    // container acumulou MÚLTIPLOS <canvas> de tamanhos divergentes
    // (alguns com o buffer interno desatualizado, largura 300 em vez de
    // ~1106), um por cima do outro, e o de cima (quase vazio) escondia o
    // de baixo (com a linha certa) — `series.data()`/`getVisibleRange()`
    // liam o chart certo (a referência em memória), mas a TELA mostrava
    // o canvas errado. Zerar o container explicitamente antes de criar o
    // novo chart garante que só existe UM <canvas> por vez,
    // independentemente de quantas vezes `renderizar()` já rodou aqui.
    el.innerHTML = '';

    var chart = LightweightCharts.createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: {
        background: { color: corTema('--bg', '#0A0A0F') },
        textColor: corTema('--text2', '#8888AA'),
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: corTema('--border', '#1C1C28') },
        horzLines: { color: corTema('--border', '#1C1C28') },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: corTema('--border', '#1C1C28') },
      timeScale: { borderColor: corTema('--border', '#1C1C28'), timeVisible: true, secondsVisible: true },
    });

    var series = chart.addLineSeries({
      color: corTema('--gold', '#C8A96E'),
      lineWidth: 1,
      priceLineVisible: false,
    });
    series.setData(payload.ticks);

    (payload.linhas || []).forEach(function (l) {
      try {
        series.createPriceLine({
          price: l.preco,
          color: l.cor,
          lineWidth: l.espessura || 1,
          lineStyle: l.estilo === 'dashed' ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: l.titulo || '',
        });
      } catch (e) { /* preço inválido — ignora essa linha, não derruba o resto */ }
    });

    if (payload.marcadores && payload.marcadores.length) {
      try { series.setMarkers(payload.marcadores); } catch (e) { /* marcador fora da série de tempo — ignora */ }
    }

    // NÃO usar fitContent() aqui — achado real 2026-08-04: com tick bruto
    // (centenas/milhares de pontos por trade) a lib respeita um
    // espaçamento mínimo por barra e se recusa a comprimir tudo no
    // container, então fitContent() silenciosamente só mostra as últimas
    // ~180 barras (o "rabo" mais recente), deixando a entrada (o motivo
    // de abrir o gráfico) fora da tela sem nenhum aviso. Em vez de tentar
    // caber TUDO, define o range inicial em volta da JANELA DO TRADE
    // (entrada→saída, com folga) — é o que importa pra julgar qualidade/
    // violino/estopada; dá pra arrastar/zoom pra ver o resto do buffer
    // carregado (±10min) na mão.
    var tempos = (payload.marcadores || []).map(function (m) { return m.time; });
    if (tempos.length) {
      var de = Math.min.apply(null, tempos) - 60;
      var ate = Math.max.apply(null, tempos) + 60;
      try { chart.timeScale().setVisibleRange({ from: de, to: ate }); }
      catch (e) { chart.timeScale().fitContent(); }
    } else {
      chart.timeScale().fitContent();
    }

    var ro = new ResizeObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var r = entries[i].contentRect;
        chart.resize(Math.max(50, r.width), Math.max(50, r.height));
      }
    });
    ro.observe(el);

    window.SQ_CHART_BACK.chart = chart;
    window.SQ_CHART_BACK.series = series;
  };
})();
