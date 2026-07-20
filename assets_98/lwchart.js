// assets_98/lwchart.js
// Gráfico de candles via Lightweight Charts (TradingView, Apache-2.0,
// github.com/tradingview/lightweight-charts) — vendorizado em
// assets_98/lightweight-charts.js, sem nenhuma conta/plano/feed da
// TradingView: só a biblioteca de desenho, alimentada com os nossos
// próprios dados (mesmo pipeline MT5/Parquet que já roda o dashboard).
//
// Duas instâncias: a prévia (dentro da janela Quant Lab, só leitura) e o
// gráfico grande da janela "Traçados" (`win-tracados`, que SUBSTITUIU a
// antiga janela "Gráfico" — 2026-07-12: mesmo container físico
// `lw-chart-full`, só renomeado pra `lw-chart-tracados`, ganhando a
// ferramenta de desenho por cima do que já tinha). A prévia continua só
// leitura (pan/zoom/crosshair nativos) — de propósito, pra não poluir a
// mini visão da Quant Lab com traçados.
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

  var COR_TRACADO_PADRAO = '#000080';  // azul marinho — combina com a paleta Win98 (pivô já usa esse tom)
  var TOLERANCIA_DRAG_PX = 6;

  // Namespace único acessado pelo clientside_callback — só isso é
  // "público"; toda a criação/resize dos charts fica só aqui dentro.
  window.SQ98_CHARTS = {
    previewSeries: null, tracadosSeries: null,
    // Linhas de S/R ativas em cada série — guardadas pra poder remover
    // antes de redesenhar (createPriceLine sempre ADICIONA; sem isso,
    // cada tick de 1s empilharia mais uma linha em cima das anteriores).
    srLines: { preview: [], tracados: [] },
    previewMme: null, tracadosMme: null,
    tracados: [], ferramentaAtiva: 'cursor',  // 'cursor' = nenhuma ferramenta de desenho ativa
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
    desenharNiveisSRemSerie(window.SQ98_CHARTS.tracadosSeries, 'tracados', lista);
    // Feedback visual do próprio estado no ícone (esmaece quando oculto) —
    // os dois botões (prévia/traçados) refletem o mesmo booleano.
    ['btn-sr-toggle-preview', 'btn-sr-toggle-tracados'].forEach(function (id) {
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
  // Também limpa os traçados manuais — escopados por ativo (tracados.py),
  // nada da lista antiga faz sentido pro ativo novo.
  window.SQ98_CHARTS.limparTudo = function () {
    limparNiveisSR(window.SQ98_CHARTS.previewSeries, 'preview');
    limparNiveisSR(window.SQ98_CHARTS.tracadosSeries, 'tracados');
    window.SQ98_CHARTS.limparTracados();
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
    if (window.SQ98_CHARTS.tracadosMme) window.SQ98_CHARTS.tracadosMme.setData(dados);
  };
  window.SQ98_CHARTS.atualizarMME21Live = function (payload, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) return;
    var ponto = payload && payload.ponto;
    if (!ponto) return;
    if (window.SQ98_CHARTS.previewMme) window.SQ98_CHARTS.previewMme.update(ponto);
    if (window.SQ98_CHARTS.tracadosMme) window.SQ98_CHARTS.tracadosMme.update(ponto);
  };
  // Ícone de olho — esconder/mostrar sem destruir a série.
  window.SQ98_CHARTS.toggleMME21 = function (visivel) {
    if (window.SQ98_CHARTS.previewMme) window.SQ98_CHARTS.previewMme.applyOptions({ visible: !!visivel });
    if (window.SQ98_CHARTS.tracadosMme) window.SQ98_CHARTS.tracadosMme.applyOptions({ visible: !!visivel });
    ['btn-mme21-toggle-preview', 'btn-mme21-toggle-tracados'].forEach(function (id) {
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

  // =========================================================================
  // TRAÇADOS — nível de S/R horizontal desenhado à mão (janela "Traçados").
  // Só atua em `tracadosSeries`/`tracadosChart` — a prévia (Quant Lab)
  // continua só-leitura, de propósito. Ver assets/lwchart.js pro mesmo
  // raciocínio (código espelhado, cada skin com sua própria cópia — mesma
  // convenção já usada pra todo o resto do projeto).
  // =========================================================================

  // `nome === 'cursor'` sempre desativa a ferramenta de desenho; qualquer
  // outro nome funciona como toggle (clicar 2x na mesma ferramenta volta
  // pro cursor) — ver assets/lwchart.js pro mesmo raciocínio.
  window.SQ98_CHARTS.selecionarFerramenta = function (nome) {
    if (nome === 'cursor') {
      window.SQ98_CHARTS.ferramentaAtiva = 'cursor';
    } else {
      window.SQ98_CHARTS.ferramentaAtiva = (window.SQ98_CHARTS.ferramentaAtiva === nome) ? 'cursor' : nome;
    }
    document.querySelectorAll('.tracado-tool-btn-98').forEach(function (btn) {
      var ativa = btn.dataset.tracadoTool === window.SQ98_CHARTS.ferramentaAtiva;
      btn.classList.toggle('tracado-tool-ativa-98', ativa);
    });
  };

  // Mapeia o `estilo` salvo (string, vem do popup de edição/Python) pro
  // enum que a Lightweight Charts espera — só os 3 que o popup oferece.
  function estiloParaLineStyle(estilo) {
    if (estilo === 'dashed') return LightweightCharts.LineStyle.Dashed;
    if (estilo === 'dotted') return LightweightCharts.LineStyle.Dotted;
    return LightweightCharts.LineStyle.Solid;
  }

  // Redesenho AUTORITATIVO a partir do servidor (carga inicial da janela,
  // ou depois de um apagar/editar) — sempre limpa e reconstrói do zero,
  // mesmo idioma de desenharNiveisSR. Criar/arrastar NÃO passa por aqui (ver
  // comentário em store-tracado-criar/mover no Python) — o cliente já está
  // com o estado visual certo depois de uma ação sua.
  window.SQ98_CHARTS.desenharTracados = function (lista) {
    window.SQ98_CHARTS.limparTracados();
    var serie = window.SQ98_CHARTS.tracadosSeries;
    if (!serie || !lista) return;
    lista.forEach(function (t) {
      // try/catch por item — um preço inválido (NaN/null, nunca deveria
      // acontecer depois do saneamento em tracados.py, mas um dado velho
      // de antes desse saneamento é possível) não pode derrubar o forEach
      // inteiro e apagar TODOS os traçados da tela (bug relatado
      // 2026-07-14: "a linha some").
      if (typeof t.preco !== 'number' || !isFinite(t.preco)) return;
      try {
        var pl = serie.createPriceLine({
          price: t.preco,
          color: t.cor || COR_TRACADO_PADRAO,
          lineWidth: t.espessura || 2,
          lineStyle: estiloParaLineStyle(t.estilo),
          axisLabelVisible: true,
          title: 'Nível manual',
        });
        window.SQ98_CHARTS.tracados.push({ id: t.id_tracado, preco: t.preco, priceLine: pl });
      } catch (e) {
        console.warn('Traçado inválido, ignorado no redesenho:', t, e);
      }
    });
  };

  window.SQ98_CHARTS.limparTracados = function () {
    var serie = window.SQ98_CHARTS.tracadosSeries;
    window.SQ98_CHARTS.tracados.forEach(function (t) { if (serie) serie.removePriceLine(t.priceLine); });
    window.SQ98_CHARTS.tracados = [];
  };

  function wireFerramentaDesenho(el, chart, serie) {
    // Hit-test compartilhado (criar/arrastar/editar todos precisam saber
    // se o Y clicado cai em cima de uma linha já existente).
    function encontrarTracado(y) {
      for (var i = 0; i < window.SQ98_CHARTS.tracados.length; i++) {
        var t = window.SQ98_CHARTS.tracados[i];
        var yLinha = serie.priceToCoordinate(t.preco);
        if (yLinha !== null && Math.abs(yLinha - y) <= TOLERANCIA_DRAG_PX) return t;
      }
      return null;
    }

    // Criar: clique simples (LightweightCharts não usa clique simples pra
    // nada — só arraste pra pan — então nunca conflita com a navegação
    // nativa). Se o clique cai em cima de um traçado que já existe, não
    // cria outro por cima — esse clique já é tratado pelo arraste/duplo
    // clique abaixo.
    chart.subscribeClick(function (param) {
      if (window.SQ98_CHARTS.ferramentaAtiva !== 'horizontal') return;
      if (!param.point) return;
      if (encontrarTracado(param.point.y)) return;
      var preco = serie.coordinateToPrice(param.point.y);
      if (preco === null) return;
      if (window.dash_clientside && window.dash_clientside.set_props) {
        window.dash_clientside.set_props('store-tracado-criar-98', { data: { preco: preco } });
      }
    });

    // Editar: duplo clique numa linha existente abre o popup de edição
    // (preço/cor/espessura/estilo — ver store-tracado-editar-98 em
    // dashboard_98.py). Funciona com qualquer ferramenta ativa, igual o
    // arraste.
    el.addEventListener('dblclick', function (e) {
      var alvo = encontrarTracado(e.offsetY);
      if (!alvo) return;
      e.preventDefault();
      e.stopPropagation();
      if (window.dash_clientside && window.dash_clientside.set_props) {
        window.dash_clientside.set_props('store-tracado-editar-98', { data: alvo.id });
      }
    }, { capture: true });

    // Arrastar: eventos de mouse crus — ver assets/lwchart.js pro
    // raciocínio completo (sem price line arrastável nativa nesta versão
    // vendorizada, v4.2). `capture: true` + stopPropagation() só quando HÁ
    // hit numa linha, senão deixa o pan nativo do LightweightCharts agir.
    var arrastando = null;
    el.addEventListener('mousedown', function (e) {
      var alvo = encontrarTracado(e.offsetY);
      if (!alvo) return;
      e.preventDefault();
      e.stopPropagation();
      arrastando = alvo;
    }, { capture: true });

    window.addEventListener('mousemove', function (e) {
      if (!arrastando) return;
      var rect = el.getBoundingClientRect();
      var y = e.clientY - rect.top;
      var novoPreco = serie.coordinateToPrice(y);
      if (novoPreco === null) return;
      arrastando.priceLine.applyOptions({ price: novoPreco });
      arrastando.preco = novoPreco;
    });

    window.addEventListener('mouseup', function () {
      if (!arrastando) return;
      var idTracado = arrastando.id, precoFinal = arrastando.preco;
      arrastando = null;
      if (window.dash_clientside && window.dash_clientside.set_props) {
        window.dash_clientside.set_props('store-tracado-mover-98', {
          data: { id_tracado: idTracado, preco: precoFinal },
        });
      }
    });
  }

  // Liga os cliques dos botões da barra (menubar da janela "Traçados",
  // ver dashboard_98.py) — puro DOM, sem callback Dash (mesmo motivo do
  // resto desta seção: essas ações nunca precisam de round-trip com o
  // servidor, só mudam qual ferramenta fica ativa no cliente).
  function wireBotoesToolbar() {
    document.querySelectorAll('.tracado-tool-btn-98').forEach(function (btn) {
      if (btn.dataset.wired) return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', function () {
        window.SQ98_CHARTS.selecionarFerramenta(btn.dataset.tracadoTool);
      });
    });
  }

  // =========================================================================

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
    if (!window.SQ98_CHARTS.tracadosSeries) {
      var tracados = criarChart('lw-chart-tracados');
      if (tracados) {
        window.SQ98_CHARTS.tracadosChart = tracados.chart;
        window.SQ98_CHARTS.tracadosSeries = tracados.serie;
        window.SQ98_CHARTS.tracadosMme = tracados.mme;
        var el = document.getElementById('lw-chart-tracados');
        wireFerramentaDesenho(el, tracados.chart, tracados.serie);
        wireBotoesToolbar();
      }
    }
    wireBotaoResetZoom();
  }

  // Exposto pro clientside_callback de dashboard_98.py chamar logo depois
  // de setData() nos dois gráficos (ver comentário de refitEscala acima).
  // Também usado como "voltar ao zoom padrão" (troca de ativo, 1ª carga,
  // ou botão de reset — ver window.SQ98_CHARTS.resetarZoom).
  window.SQ98_CHARTS.refitEscala = function () {
    refitEscala(window.SQ98_CHARTS.previewChart);
    refitEscala(window.SQ98_CHARTS.tracadosChart);
  };

  // =========================================================================
  // ZOOM/PAN — sobrevive ao refresh periódico de 30s (mesmo raciocínio de
  // assets/lwchart.js: `setData()` reseta sozinho o range visível toda vez
  // que roda — não é só o refitEscala() explícito que já existia aqui). Só
  // reseta de verdade na 1ª carga, na troca de ativo (escala de preço muda
  // de ordem de grandeza), ou no clique do botão de reset
  // (.btn-reset-zoom-98, ver wireBotaoResetZoom abaixo).
  // =========================================================================

  var ultimoAtivoCandles98 = null;

  function capturarRangeVisivel(chart) {
    if (!chart) return null;
    try { return chart.timeScale().getVisibleLogicalRange(); } catch (e) { return null; }
  }
  function restaurarRangeVisivel(chart, range) {
    if (!chart || !range) return;
    try { chart.timeScale().setVisibleLogicalRange(range); } catch (e) { /* candles ainda não prontos */ }
  }

  // Único ponto de entrada do clientside_callback de store-candles-grafico
  // em dashboard_98.py.
  window.SQ98_CHARTS.atualizarCandlesCompletos = function (candles, ativoAtual) {
    if (!candles || !candles.length) return;
    var deveResetar = ultimoAtivoCandles98 === null || ultimoAtivoCandles98 !== ativoAtual;
    ultimoAtivoCandles98 = ativoAtual;

    var rangePreview = deveResetar ? null : capturarRangeVisivel(window.SQ98_CHARTS.previewChart);
    var rangeTracados = deveResetar ? null : capturarRangeVisivel(window.SQ98_CHARTS.tracadosChart);

    if (window.SQ98_CHARTS.previewSeries) window.SQ98_CHARTS.previewSeries.setData(candles);
    if (window.SQ98_CHARTS.tracadosSeries) window.SQ98_CHARTS.tracadosSeries.setData(candles);

    if (deveResetar) {
      window.SQ98_CHARTS.refitEscala();
    } else {
      restaurarRangeVisivel(window.SQ98_CHARTS.previewChart, rangePreview);
      restaurarRangeVisivel(window.SQ98_CHARTS.tracadosChart, rangeTracados);
    }
  };

  // Botão manual "voltar ao zoom padrão" (.btn-reset-zoom-98) — puro DOM,
  // sem round-trip Dash, mesmo idioma de wireBotoesToolbar.
  window.SQ98_CHARTS.resetarZoom = function () {
    window.SQ98_CHARTS.refitEscala();
  };

  function wireBotaoResetZoom() {
    document.querySelectorAll('.btn-reset-zoom-98').forEach(function (btn) {
      if (btn.dataset.wired) return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', function () { window.SQ98_CHARTS.resetarZoom(); });
    });
  }

  // Mesmo padrão de espera segura dos outros scripts (window_manager.js,
  // clippy_agent.js): os containers só existem depois do 1º render do
  // Dash, e a janela "Traçados" pode nem estar montada ainda na 1ª chamada
  // (por isso o observer continua vivo, tentando de novo a cada mutação —
  // não só uma vez — até os dois containers existirem).
  var observer = new MutationObserver(function () {
    initCharts();
    if (window.SQ98_CHARTS.previewSeries && window.SQ98_CHARTS.tracadosSeries) {
      observer.disconnect();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  initCharts();
})();
