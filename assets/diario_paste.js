// assets/diario_paste.js
// Permite colar (Ctrl+V) um print/gráfico direto no formulário "Novo Trade"
// da aba Diário e em qualquer card de posição, além do upload por arquivo
// (dcc.Upload). Não existe componente Dash nativo pra capturar o evento
// `paste` do navegador, então isso é JS solto em assets/ usando
// `dash_clientside.set_props` — a forma suportada de um listener puro de
// evento de browser empurrar dado pra um componente Dash sem precisar de um
// Input que já exista no lado Python pra disparar um clientside_callback.
// Mesma lógica de assets_98/diario_paste.js — duplicado propositalmente
// porque assets/ e assets_98/ são isolados por front-end, nunca compartilham
// arquivo.
(function () {
  'use strict';

  var STORE_COLADA = 'store-diario-imagem-colada';
  var STORE_ARMADA = 'store-diario-posicao-ativa-print';

  // Feedback 2026-07-20: "eu não quero ter que apertar em um botão para
  // depois dar Ctrl+V, o sistema tem que capturar o comando de colar
  // automaticamente" — antes existia um botão "📋 Ou clique aqui e cole"
  // por card, que "armava" qual posição devia receber o próximo Ctrl+V
  // (o evento `paste` do navegador é global, não sabe sozinho "em qual
  // card" o usuário está). Substituído por isto: QUALQUER clique ou foco
  // dentro de um campo de um card (Resumo, Nota, Alvo, dropdown, etc. —
  // todos usam o id pattern-matching `{"type":"ea-...","index":...}` do
  // Dash) já arma aquele card sozinho, sem nenhum clique extra.
  function armarCardMaisProxima(elementoAlvo) {
    var el = elementoAlvo;
    while (el && el.nodeType === 1 && el !== document.body) {
      if (el.id && el.id.charAt(0) === '{') {
        try {
          var analisado = JSON.parse(el.id);
          if (analisado && analisado.index !== undefined &&
              typeof analisado.type === 'string' && analisado.type.indexOf('ea') === 0) {
            if (window.dash_clientside && window.dash_clientside.set_props) {
              window.dash_clientside.set_props(STORE_ARMADA, { data: analisado.index });
            }
            return;
          }
        } catch (e) { /* id não é um pattern-matching JSON, ignora */ }
      }
      el = el.parentElement;
    }
  }

  document.addEventListener('focusin', function (evento) { armarCardMaisProxima(evento.target); });
  document.addEventListener('click', function (evento) { armarCardMaisProxima(evento.target); });

  document.addEventListener('paste', function (evento) {
    var itens = (evento.clipboardData || window.clipboardData || {}).items;
    if (!itens) return;

    for (var i = 0; i < itens.length; i++) {
      if (itens[i].type.indexOf('image') === -1) continue;

      var arquivo = itens[i].getAsFile();
      if (!arquivo) continue;

      var leitor = new FileReader();
      leitor.onload = function (e) {
        if (window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props(STORE_COLADA, { data: e.target.result });
        }
      };
      leitor.readAsDataURL(arquivo);

      evento.preventDefault();
      break;
    }
  });
})();
