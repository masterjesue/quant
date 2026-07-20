// assets/tema.js — modo claro/escuro do SQ Padrão (2026-07-17)
// Compartilhado pelos dois processos (dashboard.py na porta 8050 e
// diario_app_classico.py embutido via iframe na porta 8053, mesma pasta
// assets/) — mesmo princípio de assets/diario_iframe_resize.js: um
// arquivo só, o branch `window.self !== window.top` decide qual papel
// rodar. Portas diferentes contam como origens diferentes pra
// postMessage (mesmo host, sem DOM/Store compartilhado entre os dois
// processos Dash), por isso a sincronia entre pai e filho é só via
// mensagens, nunca acesso direto ao documento um do outro.
(function () {
  'use strict';

  var MSG_TEMA = 'sq-tema';
  var MSG_PEDIR = 'sq-tema-pedir';
  window.SQ_TEMA = window.SQ_TEMA || {};

  // Comum aos dois processos: aplica o tema no documento ATUAL. Só troca
  // o atributo — todo o resto (cards, badges, botões, dropdowns...) reage
  // sozinho via variáveis CSS (ver assets/style.css). Gauges Plotly não
  // são cobertos aqui (Plotly não reage a mudança de DOM depois de
  // desenhado) — esses recebem o tema certo já na próxima reconstrução do
  // hero (atualizar_dashboard, a cada 1s, lê store-tema como State).
  window.SQ_TEMA.aplicarLocal = function (tema) {
    var t = (tema === 'dark') ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', t);
    if (window.SQ_CHART && window.SQ_CHART.aplicarTema) window.SQ_CHART.aplicarTema(t);
  };

  if (window.self !== window.top) {
    // FILHO (diario_app_classico.py embutido no iframe) — escuta o tema
    // que o pai manda, aplica local e grava em store-tema (pra qualquer
    // callback Python que precise saber o tema atual, ex.: os gráficos
    // Plotly da Estatísticas/Bem-Estar).
    window.addEventListener('message', function (evento) {
      if (!evento.data || evento.data.tipo !== MSG_TEMA) return;
      window.SQ_TEMA.aplicarLocal(evento.data.tema);
      if (window.dash_clientside && window.dash_clientside.set_props) {
        window.dash_clientside.set_props('store-tema', { data: evento.data.tema });
      }
    });
    // Pede o tema atual ao pai assim que este documento carrega — fecha a
    // corrida entre o iframe terminar de montar e o próximo toggle
    // acontecer antes disso (sem isso, o Diário só sincronizaria no
    // PRÓXIMO clique do usuário, nunca na carga inicial).
    window.parent.postMessage({ tipo: MSG_PEDIR }, '*');
  } else {
    // PAI (dashboard.py) — responde ao pedido de sincronia do filho
    // reenviando o tema atual (guardado em window.SQ_TEMA.atual pelo
    // clientside_callback de store-tema em dashboard.py).
    window.addEventListener('message', function (evento) {
      if (!evento.data || evento.data.tipo !== MSG_PEDIR) return;
      var iframe = document.querySelector('#painel-diario iframe');
      if (iframe && window.SQ_TEMA.atual) {
        iframe.contentWindow.postMessage({ tipo: MSG_TEMA, tema: window.SQ_TEMA.atual }, '*');
      }
    });
  }
})();
