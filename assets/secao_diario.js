// assets/secao_diario.js — menu lateral (rollout TradeZella, 2026-07-31)
// controla qual seção do Diário aparece dentro do iframe. Mesmo princípio
// de assets/tema.js: dois processos Dash (pai :8050, filho :8053 embutido
// via iframe), sincronia só por postMessage (portas diferentes = origens
// diferentes, sem DOM/Store compartilhado entre eles).
(function () {
  'use strict';

  var MSG_SECAO = 'sq-secao';
  var MSG_SECAO_PEDIR = 'sq-secao-pedir';
  var MSG_HIST_FILTRO = 'sq-hist-filtro-data';

  if (window.self !== window.top) {
    // FILHO (diario_app_classico.py embutido no iframe) — escuta qual
    // seção o pai quer mostrar e escreve direto em `store-diario-sub-aba`
    // (mesmo Store que os botões internos escreviam antes de serem
    // removidos — a navegação agora vem só de fora). Também escuta o
    // filtro de data do Histórico (clique num dia do calendário do
    // Dashboard, que agora mora no PAI).
    window.addEventListener('message', function (evento) {
      if (!evento.data || !window.dash_clientside || !window.dash_clientside.set_props) return;
      if (evento.data.tipo === MSG_SECAO) {
        window.dash_clientside.set_props('store-diario-sub-aba', { data: evento.data.secao });
      } else if (evento.data.tipo === MSG_HIST_FILTRO) {
        window.dash_clientside.set_props('diario-hist-filtro-data-calendario', { data: evento.data.data });
      }
    });
    // Pede a seção atual ao pai assim que este documento carrega — fecha a
    // corrida entre o iframe terminar de montar e a próxima troca de seção
    // acontecer antes disso (mesmo princípio de MSG_PEDIR em tema.js).
    window.parent.postMessage({ tipo: MSG_SECAO_PEDIR }, '*');
  } else {
    // PAI (dashboard.py) — responde ao pedido de sincronia do filho
    // reenviando a seção atual (guardada em window.SQ_SECAO.atual pelo
    // clientside_callback de store-aba-ativa em dashboard.py).
    window.SQ_SECAO = window.SQ_SECAO || {};
    window.addEventListener('message', function (evento) {
      if (!evento.data || evento.data.tipo !== MSG_SECAO_PEDIR) return;
      var iframe = document.querySelector('#painel-diario iframe');
      if (iframe && window.SQ_SECAO.atual) {
        iframe.contentWindow.postMessage({ tipo: MSG_SECAO, secao: window.SQ_SECAO.atual }, '*');
      }
    });
  }
})();
