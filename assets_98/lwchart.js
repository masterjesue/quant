// assets_98/lwchart.js
// Gráfico de candles via Lightweight Charts (TradingView, Apache-2.0,
// github.com/tradingview/lightweight-charts) — vendorizado em
// assets_98/lightweight-charts.js, sem nenhuma conta/plano/feed da
// TradingView: só a biblioteca de desenho, alimentada com os nossos
// próprios dados (mesmo pipeline MT5/Parquet que já roda o dashboard).
//
// Duas instâncias: a prévia (dentro da janela Quant Lab) e o gráfico cheio
// (janela própria, win-grafico) — mesmos dados, tamanhos diferentes.
// Contexto visual da formação dos candles, sem traçado/estudo — pan/zoom/
// crosshair nativos da biblioteca bastam pro que foi pedido.
//
// Dados chegam via clientside_callback em dashboard_98.py, lendo
// store-candles-grafico (Output de atualizar_dashboard, mesmo tick de 1s
// que já atualiza o resto). Ver window.SQ98_CHARTS abaixo — é o único
// ponto de contato entre o callback e este script.
(function () {
  'use strict';

  // Funções (não objetos no top-level do módulo) — evita depender da ordem
  // de carregamento dos <script> do assets_98/ pra `LightweightCharts` já
  // existir no momento em que este arquivo é avaliado (só precisa existir
  // quando `criarChart()` roda de fato, bem depois).
  function opcoesComuns() {
    return {
      layout: {
        background: { color: '#FFFFFF' },
        textColor: '#000000',
        fontFamily: 'Tahoma, "Segoe UI", Verdana, Arial, sans-serif',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#DFDFDF' },
        horzLines: { color: '#DFDFDF' },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#808080' },
      timeScale: { borderColor: '#808080', timeVisible: true, secondsVisible: false },
    };
  }

  var OPCOES_SERIE = {
    upColor: '#008000', downColor: '#FF0000',
    borderUpColor: '#008000', borderDownColor: '#FF0000',
    wickUpColor: '#008000', wickDownColor: '#FF0000',
  };

  // Namespace único acessado pelo clientside_callback — só isso é
  // "público"; toda a criação/resize dos charts fica só aqui dentro.
  window.SQ98_CHARTS = {
    previewSeries: null, fullSeries: null,
    // Linhas de S/R ativas em cada série — guardadas pra poder remover
    // antes de redesenhar (createPriceLine sempre ADICIONA; sem isso,
    // cada tick de 1s empilharia mais uma linha em cima das anteriores).
    srLines: { preview: [], full: [] },
    previewMme: null, fullMme: null,
  };

  var COR_SR = { resistencia: '#FF0000', suporte: '#008000', pivo: '#000080' };

  function limparNiveisSR(serie, chave) {
    var lines = window.SQ98_CHARTS.srLines[chave];
    if (serie) lines.forEach(function (pl) { serie.removePriceLine(pl); });
    window.SQ98_CHARTS.srLines[chave] = [];
  }

  function desenharNiveisSRemSerie(serie, chave, niveis) {
    limparNiveisSR(serie, chave);
    if (!serie || !niveis) return;
    niveis.forEach(function (n) {
      var ehPivo = n.tipo === 'pivo';
      var pl = serie.createPriceLine({
        price: n.preco,
        color: COR_SR[n.tipo] || '#000000',
        lineWidth: n.forte ? 2 : 1,
        lineStyle: ehPivo ? LightweightCharts.LineStyle.Dotted
          : (n.forte ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed),
        axisLabelVisible: true,
        title: n.label || '',
      });
      window.SQ98_CHARTS.srLines[chave].push(pl);
    });
  }

  // Único ponto de entrada chamado pelo clientside_callback em
  // dashboard_98.py — `payload` vem de store-niveis-sr (recalculado a cada
  // tick de 1s, junto do resto de `atualizar_dashboard`, no formato
  // {ativo, niveis}) e `visivel` do botão de olho (store-sr-visivel).
  // Sempre redesenha os DOIS gráficos juntos, pra prévia e janela cheia
  // nunca ficarem dessincronizadas.
  //
  // `ativoAtual` vem de store-ativo (como State, não Input) — guarda
  // contra uma resposta ATRASADA de `atualizar_dashboard`: o servidor
  // roda com threaded=True, então nada garante que a resposta calculada
  // ANTES da troca de ativo chegue ao navegador ANTES da resposta
  // calculada DEPOIS (disparada pela própria troca). Se a atrasada
  // (ainda do ativo antigo) chegasse por último, ela reintroduziria
  // linhas de S/R na escala errada (WIN ~170-190 mil vs WDO ~5 mil) por
  // cima dos candles já trocados — bug relatado em 2026-07-10.
  window.SQ98_CHARTS.desenharNiveisSR = function (payload, visivel, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) {
      return;  // resposta de um ativo que não é mais o selecionado — descarta
    }
    var niveis = payload && payload.niveis;
    var lista = visivel ? (niveis || []) : [];
    desenharNiveisSRemSerie(window.SQ98_CHARTS.previewSeries, 'preview', lista);
    desenharNiveisSRemSerie(window.SQ98_CHARTS.fullSeries, 'full', lista);
    // Feedback visual do próprio estado no ícone (esmaece quando oculto) —
    // os dois botões (prévia/cheia) refletem o mesmo booleano.
    ['btn-sr-toggle-preview', 'btn-sr-toggle-full'].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.style.opacity = visivel ? '1' : '0.45';
    });
  };

  // Chamado assim que o ativo troca (antes do recálculo de níveis do tick
  // de 1s chegar) — troca de WIN (~170-190 mil) pra WDO (~5 mil), ou
  // vice-versa, é uma mudança de ORDEM DE GRANDEZA no preço. Se uma linha
  // de S/R da escala antiga ficasse desenhada mesmo por um instante junto
  // dos candles novos (setData ainda não rodou, ou o tick de 1s ainda não
  // recalculou store-niveis-sr), o autoscale do eixo de preço se ajusta
  // pra caber os dois extremos, esmagando os candles novos perto de uma
  // das bordas — some visualmente. Limpar tudo já na troca de ativo (e
  // deixar o próximo tick de 1s redesenhar do zero) evita essa janela.
  window.SQ98_CHARTS.limparTudo = function () {
    limparNiveisSR(window.SQ98_CHARTS.previewSeries, 'preview');
    limparNiveisSR(window.SQ98_CHARTS.fullSeries, 'full');
  };

  // MME21 (EMA21) — linha, não nível fixo (ver Rastreador de Tendência).
  // Mesma arquitetura de 2 stores já usada pros candles: setData (histórico
  // completo, tick de 30s/troca de ativo) e update (ponto em formação,
  // tick de 1s). Mesma tag de `ativo` + descarte de resposta atrasada já
  // usada em `desenharNiveisSR` (server threaded=True).
  window.SQ98_CHARTS.atualizarMME21Serie = function (payload, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) return;
    var dados = payload && payload.serie;
    if (!dados || !dados.length) return;
    if (window.SQ98_CHARTS.previewMme) window.SQ98_CHARTS.previewMme.setData(dados);
    if (window.SQ98_CHARTS.fullMme) window.SQ98_CHARTS.fullMme.setData(dados);
  };
  window.SQ98_CHARTS.atualizarMME21Live = function (payload, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) return;
    var ponto = payload && payload.ponto;
    if (!ponto) return;
    if (window.SQ98_CHARTS.previewMme) window.SQ98_CHARTS.previewMme.update(ponto);
    if (window.SQ98_CHARTS.fullMme) window.SQ98_CHARTS.fullMme.update(ponto);
  };
  // Ícone de olho — esconder/mostrar sem destruir a série.
  window.SQ98_CHARTS.toggleMME21 = function (visivel) {
    if (window.SQ98_CHARTS.previewMme) window.SQ98_CHARTS.previewMme.applyOptions({ visible: !!visivel });
    if (window.SQ98_CHARTS.fullMme) window.SQ98_CHARTS.fullMme.applyOptions({ visible: !!visivel });
    ['btn-mme21-toggle-preview', 'btn-mme21-toggle-full'].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.style.opacity = visivel ? '1' : '0.45';
    });
  };

  // Reforça o autoscale logo após um setData() de candles inteiro novo
  // (troca de ativo ou refresh de 30s) — não deveria ser necessário (a
  // biblioteca já reajusta a escala sozinha por padrão), mas blindagem
  // barata contra qualquer resíduo de escala anterior (ex. linha de S/R
  // desenhada bem perto no tempo, mesmo já removida acima).
  function refitEscala(chart) {
    if (!chart) return;
    try {
      chart.priceScale('right').applyOptions({ autoScale: true });
      chart.timeScale().fitContent();
    } catch (e) { /* chart ainda não pronto — próximo ciclo tenta de novo */ }
  }

  function criarChart(containerId, opcoesExtra) {
    var el = document.getElementById(containerId);
    if (!el || el.dataset.lwReady) return null;
    el.dataset.lwReady = '1';

    var chart = LightweightCharts.createChart(el, Object.assign(
      {}, opcoesComuns(), { width: el.clientWidth, height: el.clientHeight }, opcoesExtra || {}
    ));
    var serie = chart.addCandlestickSeries(OPCOES_SERIE);
    // Azul petróleo — cor pedida por Jesué, sem relação com a paleta
    // semântica de mercado (verde/vermelho já usados pros candles/S/R).
    var mme = chart.addLineSeries({
      color: '#1B4F5C', lineWidth: 2,
      priceLineVisible: false, lastValueVisible: false,
    });

    // Redimensiona se o container mudar de tamanho (ex. janela maximizada).
    var ro = new ResizeObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var r = entries[i].contentRect;
        chart.resize(Math.max(50, r.width), Math.max(50, r.height));
      }
    });
    ro.observe(el);

    return { chart: chart, serie: serie, mme: mme };
  }

  function initCharts() {
    if (!window.SQ98_CHARTS.previewSeries) {
      var previa = criarChart('lw-chart-preview', {
        timeScale: { borderColor: '#808080', timeVisible: true, secondsVisible: false },
      });
      if (previa) {
        window.SQ98_CHARTS.previewChart = previa.chart;
        window.SQ98_CHARTS.previewSeries = previa.serie;
        window.SQ98_CHARTS.previewMme = previa.mme;
      }
    }
    if (!window.SQ98_CHARTS.fullSeries) {
      var cheia = criarChart('lw-chart-full');
      if (cheia) {
        window.SQ98_CHARTS.fullChart = cheia.chart;
        window.SQ98_CHARTS.fullSeries = cheia.serie;
        window.SQ98_CHARTS.fullMme = cheia.mme;
      }
    }
  }

  // Exposto pro clientside_callback de dashboard_98.py chamar logo depois
  // de setData() nos dois gráficos (ver comentário de refitEscala acima).
  window.SQ98_CHARTS.refitEscala = function () {
    refitEscala(window.SQ98_CHARTS.previewChart);
    refitEscala(window.SQ98_CHARTS.fullChart);
  };

  // Mesmo padrão de espera segura dos outros scripts (window_manager.js,
  // clippy_agent.js): os containers só existem depois do 1º render do
  // Dash, e a janela "Gráfico" pode nem estar montada ainda na 1ª chamada
  // (por isso o observer continua vivo, tentando de novo a cada mutação —
  // não só uma vez — até os dois containers existirem).
  var observer = new MutationObserver(function () {
    initCharts();
    if (window.SQ98_CHARTS.previewSeries && window.SQ98_CHARTS.fullSeries) {
      observer.disconnect();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  initCharts();
})();
