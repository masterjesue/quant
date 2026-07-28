// assets/lwchart.js
// Gráfico de candles via Lightweight Charts (TradingView, Apache-2.0,
// github.com/tradingview/lightweight-charts) — vendorizado em
// assets/lightweight-charts.js, sem nenhuma conta/plano/feed da
// TradingView: só a biblioteca de desenho, alimentada com os nossos
// próprios dados (mesmo pipeline MT5/Parquet que já roda o dashboard).
//
// 1 instância — `lw-chart-tracados`, o gráfico da seção fundida "Price
// Action + Traçados" (Modo Aprendizagem.txt, 2026-07-20). Já existiu uma
// 2ª instância menor e sem ferramenta de desenho (`lw-chart-price-action`,
// mesmos dados) — removida por ser redundante; `window.SQ_CHART.chart`/
// `series`/`mmeSeries` (o estado dessa 1ª instância) ficam permanentemente
// `null` agora, mas todo consumo já era null-safe por padrão de
// espera-segura (containers do Dash não existem no 1º tick de JS).
// `criarChart()` continua genérico (aceita `containerId`) — histórico de
// quando havia 2 instâncias aqui; copiado do molde que
// assets_98/lwchart.js já usava.
//
// Dados chegam via clientside_callback em dashboard.py, lendo
// store-candles-grafico (Output de atualizar_dashboard, mesmo tick de 1s
// que já atualiza o resto). window.SQ_CHART é o único ponto de contato
// entre os callbacks e este script.
(function () {
  'use strict';

  window.SQ_CHART = {
    chart: null, series: null, mmeSeries: null,
    tracadosChart: null, tracadosSeries: null, tracadosMme: null,
    srLines: { principal: [], tracados: [] },
    tracados: [], ferramentaAtiva: 'cursor',  // 'cursor' = nenhuma ferramenta de desenho ativa
  };

  var COR_SR = { resistencia: '#E05C5C', suporte: '#2ECC8A', pivo: '#4A90D9' };
  var COR_TRACADO_PADRAO = '#C8A96E';   // dourado — mesma cor de destaque (C['gold']) do resto do clássico
  var TOLERANCIA_DRAG_PX = 6;          // faixa clicável em volta da linha pra iniciar o arraste

  function limparNiveisSR(serie, chave) {
    var lines = window.SQ_CHART.srLines[chave];
    if (serie) lines.forEach(function (pl) { serie.removePriceLine(pl); });
    window.SQ_CHART.srLines[chave] = [];
  }

  function desenharNiveisSRemSerie(serie, chave, niveis) {
    limparNiveisSR(serie, chave);
    if (!serie || !niveis) return;
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
      window.SQ_CHART.srLines[chave].push(pl);
    });
  }

  // Único ponto de entrada chamado pelo clientside_callback em
  // dashboard.py — `payload` vem de store-niveis-sr (recalculado a cada
  // tick de 1s, junto do resto de `atualizar_dashboard`, no formato
  // {ativo, niveis}) e `visivel` do botão de olho (store-sr-visivel).
  // Sempre limpa as linhas antigas antes de redesenhar — createPriceLine
  // só ADICIONA, nunca substitui sozinho. Redesenha as DUAS instâncias
  // juntas (mesmo payload), pra nunca ficarem dessincronizadas — mesma
  // ideia de `assets_98/lwchart.js::desenharNiveisSR`.
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
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) {
      return;  // resposta de um ativo que não é mais o selecionado — descarta
    }
    var niveis = payload && payload.niveis;
    var lista = visivel ? (niveis || []) : [];
    desenharNiveisSRemSerie(window.SQ_CHART.series, 'principal', lista);
    desenharNiveisSRemSerie(window.SQ_CHART.tracadosSeries, 'tracados', lista);
    ['btn-sr-toggle', 'btn-sr-toggle-tracados'].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.style.opacity = visivel ? '1' : '0.45';
    });
  };

  // Chamado assim que o ativo troca, antes do tick de 1s recalcular
  // store-niveis-sr — WIN (~170-190 mil) e WDO (~5 mil) são ordens de
  // grandeza de preço bem diferentes; uma linha de S/R da escala antiga
  // ainda desenhada junto dos candles novos distorce o autoscale do eixo
  // (candles novos somem, espremidos numa borda). Ver mesmo comentário em
  // assets_98/lwchart.js. Também limpa os traçados manuais — eles são
  // escopados por ativo (ver tracados.py), então nada da lista antiga faz
  // sentido pro ativo novo; o próximo `desenharTracados` (disparado pela
  // troca de ativo) redesenha do zero.
  window.SQ_CHART.limparTudo = function () {
    limparNiveisSR(window.SQ_CHART.series, 'principal');
    limparNiveisSR(window.SQ_CHART.tracadosSeries, 'tracados');
    window.SQ_CHART.limparTracados();
  };

  // MME21 (EMA21) — linha, não nível fixo (ver Rastreador de Tendência).
  // Mesma arquitetura de 2 stores já usada pros candles: setData (histórico
  // completo, tick de 30s/troca de ativo) e update (ponto em formação,
  // tick de 1s) — mesmo cuidado de nunca reconstruir a linha inteira todo
  // segundo. Mesma tag de `ativo` + descarte de resposta atrasada que já
  // corrige o mesmo risco em `desenharNiveisSR` (server threaded=True).
  window.SQ_CHART.atualizarMME21Serie = function (payload, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) return;
    var dados = payload && payload.serie;
    if (!dados || !dados.length) return;
    if (window.SQ_CHART.mmeSeries) window.SQ_CHART.mmeSeries.setData(dados);
    if (window.SQ_CHART.tracadosMme) window.SQ_CHART.tracadosMme.setData(dados);
  };
  window.SQ_CHART.atualizarMME21Live = function (payload, ativoAtual) {
    if (payload && ativoAtual && payload.ativo && payload.ativo !== ativoAtual) return;
    var ponto = payload && payload.ponto;
    if (!ponto) return;
    if (window.SQ_CHART.mmeSeries) window.SQ_CHART.mmeSeries.update(ponto);
    if (window.SQ_CHART.tracadosMme) window.SQ_CHART.tracadosMme.update(ponto);
  };
  // Ícone de olho — esconder/mostrar sem destruir a série (mais barato que
  // remover/recriar, já que é uma linha única, não N linhas de preço).
  window.SQ_CHART.toggleMME21 = function (visivel) {
    if (window.SQ_CHART.mmeSeries) window.SQ_CHART.mmeSeries.applyOptions({ visible: !!visivel });
    if (window.SQ_CHART.tracadosMme) window.SQ_CHART.tracadosMme.applyOptions({ visible: !!visivel });
    ['btn-mme21-toggle', 'btn-mme21-toggle-tracados'].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.style.opacity = visivel ? '1' : '0.45';
    });
  };

  // Reforça o autoscale logo após um setData() de candles inteiro novo —
  // blindagem barata contra qualquer resíduo de escala anterior. Também
  // usado como "voltar ao zoom padrão" (troca de ativo, 1ª carga, ou
  // botão de reset — ver window.SQ_CHART.resetarZoom).
  window.SQ_CHART.refitEscala = function () {
    [window.SQ_CHART.chart, window.SQ_CHART.tracadosChart].forEach(function (chart) {
      if (!chart) return;
      try {
        chart.priceScale('right').applyOptions({ autoScale: true });
        chart.timeScale().fitContent();
      } catch (e) { /* chart ainda não pronto — próximo ciclo tenta de novo */ }
    });
  };

  // =========================================================================
  // ZOOM/PAN — sobrevive ao refresh periódico de 30s (ver
  // atualizar_candles_grafico_completo em dashboard.py). `setData()` reseta
  // sozinho o range visível toda vez que roda (comportamento da própria
  // Lightweight Charts, não só o fitContent() explícito que já existia
  // aqui) — sem isso, qualquer zoom manual de Jesué era desfeito no
  // próximo tick de 30s, mesmo sem o ativo ter mudado (bug relatado
  // 2026-07-13). Só reseta de verdade quando: é a 1ª carga da página, o
  // ativo mudou (escala de preço muda de ordem de grandeza — WIN
  // ~170-190 mil vs WDO ~5 mil, mesmo raciocínio de limparTudo), ou o
  // usuário clica o botão de reset (.btn-reset-zoom, ver
  // wireBotaoResetZoom abaixo).
  // =========================================================================

  var ultimoAtivoCandles = null;

  function capturarRangeVisivel(chart) {
    if (!chart) return null;
    try { return chart.timeScale().getVisibleLogicalRange(); } catch (e) { return null; }
  }
  function restaurarRangeVisivel(chart, range) {
    if (!chart || !range) return;
    try { chart.timeScale().setVisibleLogicalRange(range); } catch (e) { /* candles ainda não prontos */ }
  }

  // Único ponto de entrada do clientside_callback de store-candles-grafico
  // em dashboard.py — histórico completo (300 candles), tick de 30s ou
  // troca de ativo.
  window.SQ_CHART.atualizarCandlesCompletos = function (candles, ativoAtual) {
    if (!candles || !candles.length) return;
    var deveResetar = ultimoAtivoCandles === null || ultimoAtivoCandles !== ativoAtual;
    ultimoAtivoCandles = ativoAtual;

    var rangePrincipal = deveResetar ? null : capturarRangeVisivel(window.SQ_CHART.chart);
    var rangeTracados = deveResetar ? null : capturarRangeVisivel(window.SQ_CHART.tracadosChart);

    if (window.SQ_CHART.series) window.SQ_CHART.series.setData(candles);
    if (window.SQ_CHART.tracadosSeries) window.SQ_CHART.tracadosSeries.setData(candles);

    if (deveResetar) {
      window.SQ_CHART.refitEscala();
    } else {
      restaurarRangeVisivel(window.SQ_CHART.chart, rangePrincipal);
      restaurarRangeVisivel(window.SQ_CHART.tracadosChart, rangeTracados);
    }
  };

  // Botão manual "voltar ao zoom padrão" (.btn-reset-zoom) — puro DOM, sem
  // round-trip Dash, mesmo idioma de wireBotoesToolbar abaixo.
  window.SQ_CHART.resetarZoom = function () {
    window.SQ_CHART.refitEscala();
  };

  function wireBotaoResetZoom() {
    document.querySelectorAll('.btn-reset-zoom').forEach(function (btn) {
      if (btn.dataset.wired) return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', function () { window.SQ_CHART.resetarZoom(); });
    });
  }

  // =========================================================================
  // TRAÇADOS — nível de S/R horizontal desenhado à mão (aba "Traçados"). Só
  // atua na instância `tracadosChart`/`tracadosSeries` (a mini do Price
  // Action nunca ganha ferramenta de desenho, só mostra os traçados que já
  // existem via desenharNiveisSR-like redraw — na verdade nem isso: os
  // traçados só aparecem no gráfico grande, de propósito, pra não poluir a
  // mini visão do Quant Lab).
  //
  // ROADMAP (ver tracados.py pro detalhe completo): só linha horizontal
  // implementada agora. LTA/LTB/canais/figuras ficam pra depois — os botões
  // já aparecem na barra, desabilitados.
  // =========================================================================

  // `nome === 'cursor'` sempre desativa a ferramenta de desenho (botão
  // "Cursor" chama assim, incondicional). Qualquer outro nome funciona como
  // toggle: clicar de novo na MESMA ferramenta já ativa volta pro cursor —
  // não precisa clicar em "Cursor" toda vez só pra soltar a ferramenta.
  window.SQ_CHART.selecionarFerramenta = function (nome) {
    if (nome === 'cursor') {
      window.SQ_CHART.ferramentaAtiva = 'cursor';
    } else {
      window.SQ_CHART.ferramentaAtiva = (window.SQ_CHART.ferramentaAtiva === nome) ? 'cursor' : nome;
    }
    document.querySelectorAll('.tracado-tool-btn').forEach(function (btn) {
      var ativa = btn.dataset.tracadoTool === window.SQ_CHART.ferramentaAtiva;
      btn.classList.toggle('tracado-tool-ativa', ativa);
    });
  };

  // Mapeia o `estilo` salvo (string, vem do popup de edição/Python) pro
  // enum que a Lightweight Charts espera — só os 3 que o popup oferece.
  function estiloParaLineStyle(estilo) {
    if (estilo === 'dashed') return LightweightCharts.LineStyle.Dashed;
    if (estilo === 'dotted') return LightweightCharts.LineStyle.Dotted;
    return LightweightCharts.LineStyle.Solid;
  }

  // Redesenho AUTORITATIVO a partir do servidor (carga inicial da aba, ou
  // depois de um apagar/editar) — sempre limpa e reconstrói do zero, nunca
  // diff incremental (mesmo idioma de desenharNiveisSR). Criar/arrastar NÃO
  // passa por aqui (ver comentário em store-tracado-criar/mover no Python)
  // — o cliente já está com o estado visual certo depois de uma ação sua,
  // redesenhar de novo só causaria um pisca desnecessário.
  window.SQ_CHART.desenharTracados = function (lista) {
    window.SQ_CHART.limparTracados();
    var serie = window.SQ_CHART.tracadosSeries;
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
        window.SQ_CHART.tracados.push({ id: t.id_tracado, preco: t.preco, priceLine: pl });
      } catch (e) {
        console.warn('Traçado inválido, ignorado no redesenho:', t, e);
      }
    });
  };

  window.SQ_CHART.limparTracados = function () {
    var serie = window.SQ_CHART.tracadosSeries;
    window.SQ_CHART.tracados.forEach(function (t) { if (serie) serie.removePriceLine(t.priceLine); });
    window.SQ_CHART.tracados = [];
  };

  function wireFerramentaDesenho(el, chart, serie) {
    // Hit-test compartilhado (criar/arrastar/editar todos precisam saber
    // se o Y clicado cai em cima de uma linha já existente).
    function encontrarTracado(y) {
      for (var i = 0; i < window.SQ_CHART.tracados.length; i++) {
        var t = window.SQ_CHART.tracados[i];
        var yLinha = serie.priceToCoordinate(t.preco);
        if (yLinha !== null && Math.abs(yLinha - y) <= TOLERANCIA_DRAG_PX) return t;
      }
      return null;
    }

    // Criar: um clique simples no gráfico (LightweightCharts não usa clique
    // simples pra nada — só arraste pan/zoom por scroll — então isso nunca
    // conflita com a navegação nativa). Se o clique cai em cima de um
    // traçado que já existe, não cria outro por cima — esse clique já é
    // tratado pelo arraste/duplo-clique abaixo.
    chart.subscribeClick(function (param) {
      if (window.SQ_CHART.ferramentaAtiva !== 'horizontal') return;
      if (!param.point) return;
      if (encontrarTracado(param.point.y)) return;
      var preco = serie.coordinateToPrice(param.point.y);
      if (preco === null) return;
      if (window.dash_clientside && window.dash_clientside.set_props) {
        window.dash_clientside.set_props('store-tracado-criar', { data: { preco: preco } });
      }
    });

    // Editar: duplo clique numa linha existente abre o popup de edição
    // (preço/cor/espessura/estilo — ver store-tracado-editar em
    // dashboard.py). Funciona com qualquer ferramenta ativa (igual o
    // arraste, diferente de criar). `capture:true` + `stopPropagation()` só
    // quando HÁ hit, mesmo idioma do arraste abaixo — sem isso, o duplo
    // clique nativo da Lightweight Charts poderia mexer no zoom.
    el.addEventListener('dblclick', function (e) {
      var alvo = encontrarTracado(e.offsetY);
      if (!alvo) return;
      e.preventDefault();
      e.stopPropagation();
      if (window.dash_clientside && window.dash_clientside.set_props) {
        window.dash_clientside.set_props('store-tracado-editar', { data: alvo.id });
      }
    }, { capture: true });

    // Arrastar: eventos de mouse crus (LightweightCharts não expõe price
    // line arrastável nesta versão — v4.2, confirmado sem a opção
    // `draggable` no bundle vendorizado). Hit-test contra a coordenada Y
    // atual de cada traçado; funciona com QUALQUER ferramenta selecionada
    // (inclusive nenhuma/"cursor") — arrastar é sempre possível, diferente
    // de criar (que exige a ferramenta "Nível S/R" ativa).
    //
    // `capture: true` + `stopPropagation()` só quando HÁ hit: intercepta o
    // mousedown ANTES do listener interno do LightweightCharts (que iniciaria
    // um pan do eixo de tempo), sem quebrar o pan/zoom nativo quando o
    // clique não é em cima de uma linha (não faz nada, deixa passar).
    var arrastando = null;  // {id, elementoPriceLine}
    el.addEventListener('mousedown', function (e) {
      var alvo = encontrarTracado(e.offsetY);
      if (!alvo) return;  // não é em cima de uma linha — deixa o pan nativo agir
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
        window.dash_clientside.set_props('store-tracado-mover', {
          data: { id_tracado: idTracado, preco: precoFinal },
        });
      }
    });
  }

  // Liga os cliques dos botões da barra (aba "Traçados", ver dashboard.py)
  // — puro DOM, sem callback Dash (mesmo motivo do resto desta seção: essas
  // ações nunca precisam de round-trip com o servidor, só mudam qual
  // ferramenta fica ativa no cliente).
  function wireBotoesToolbar() {
    document.querySelectorAll('.tracado-tool-btn').forEach(function (btn) {
      if (btn.dataset.wired) return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', function () {
        window.SQ_CHART.selecionarFerramenta(btn.dataset.tracadoTool);
      });
    });
  }

  // =========================================================================

  // Modo claro/escuro (2026-07-17, ver assets/tema.js) — lê as variáveis
  // CSS já definidas em assets/style.css via getComputedStyle em vez de
  // duplicar uma tabela de cores aqui: fonte única de verdade continua
  // sendo o CSS. `applyOptions` atualiza os charts/séries já existentes
  // ao vivo, sem destruir/recriar (mesma lição de performance já aprendida
  // neste projeto — nunca fazer rebuild completo numa operação frequente).
  window.SQ_CHART.aplicarTema = function (tema) {
    var estilo = getComputedStyle(document.documentElement);
    function v(nome, fallback) {
      var val = estilo.getPropertyValue(nome).trim();
      return val || fallback;
    }
    var bg = v('--bg', '#0A0A0F');
    var text2 = v('--text2', '#8888AA');
    var border = v('--border', '#1C1C28');
    var green = v('--green', '#2ECC8A');
    var red = v('--red', '#E05C5C');
    var mme21 = v('--mme21', '#1B4F5C');

    [window.SQ_CHART.chart, window.SQ_CHART.tracadosChart].forEach(function (chart) {
      if (!chart) return;
      chart.applyOptions({
        layout: { background: { color: bg }, textColor: text2 },
        grid: { vertLines: { color: border }, horzLines: { color: border } },
        rightPriceScale: { borderColor: border },
        timeScale: { borderColor: border },
      });
    });
    [window.SQ_CHART.series, window.SQ_CHART.tracadosSeries].forEach(function (serie) {
      if (!serie) return;
      serie.applyOptions({
        upColor: green, downColor: red,
        borderUpColor: green, borderDownColor: red,
        wickUpColor: green, wickDownColor: red,
      });
    });
    [window.SQ_CHART.mmeSeries, window.SQ_CHART.tracadosMme].forEach(function (serie) {
      if (serie) serie.applyOptions({ color: mme21 });
    });
  };

  function criarChart(containerId, opcoesExtra) {
    var el = document.getElementById(containerId);
    if (!el || el.dataset.lwReady) return null;
    el.dataset.lwReady = '1';

    var chart = LightweightCharts.createChart(el, Object.assign({
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
    }, opcoesExtra || {}));
    var series = chart.addCandlestickSeries({
      upColor: '#2ECC8A', downColor: '#E05C5C',
      borderUpColor: '#2ECC8A', borderDownColor: '#E05C5C',
      wickUpColor: '#2ECC8A', wickDownColor: '#E05C5C',
    });
    // Azul petróleo — cor pedida por Jesué, sem relação com a paleta
    // semântica de mercado (verde/vermelho já usados pros candles/S/R).
    var mme = chart.addLineSeries({
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

    return { chart: chart, series: series, mme: mme };
  }

  // Pinta o chart recém-criado no tema já restaurado (localStorage), não
  // só depois de um toggle manual — mesma função (aplicarTema) cobre os
  // dois casos, uma fonte só de verdade pro desenho das cores.
  function repintarChartsNoTemaAtual() {
    var tema = document.documentElement.getAttribute('data-theme') || 'dark';
    if (window.SQ_CHART.aplicarTema) window.SQ_CHART.aplicarTema(tema);
  }

  function initCharts() {
    // `lw-chart-price-action` (chart "principal" duplicado) foi removido
    // do layout (Modo Aprendizagem.txt, 2026-07-20: Price Action e
    // Traçados viraram uma seção só, com um gráfico só — o de Traçados,
    // que já tinha a ferramenta de desenho). `window.SQ_CHART.chart`/
    // `series`/`mmeSeries` ficam permanentemente `null` agora — todo
    // consumo já é null-safe (`if (!chart) return`, etc.), não precisa
    // tentar criar esse chart nunca mais.
    if (!window.SQ_CHART.tracadosSeries) {
      var tracadosChart = criarChart('lw-chart-tracados');
      if (tracadosChart) {
        window.SQ_CHART.tracadosChart = tracadosChart.chart;
        window.SQ_CHART.tracadosSeries = tracadosChart.series;
        window.SQ_CHART.tracadosMme = tracadosChart.mme;
        var el = document.getElementById('lw-chart-tracados');
        wireFerramentaDesenho(el, tracadosChart.chart, tracadosChart.series);
        wireBotoesToolbar();
      }
    }
    wireBotaoResetZoom();
    repintarChartsNoTemaAtual();
  }

  // Mesmo padrão de espera segura dos outros scripts (window_manager.js,
  // clippy_agent.js, assets_98/lwchart.js): o container só existe depois
  // do 1º render do Dash — o observer continua vivo até existir.
  var observer = new MutationObserver(function () {
    initCharts();
    if (window.SQ_CHART.tracadosSeries) observer.disconnect();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  initCharts();
})();
