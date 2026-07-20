// assets_98/diario_paste.js
// Permite colar (Ctrl+V) um print/gráfico direto no formulário "Novo Trade"
// da janela Diário e em qualquer card de posição, além do upload por
// arquivo (dcc.Upload). Não existe componente Dash nativo pra capturar o
// evento `paste` do navegador, então isso é JS solto em assets_98/ (mesma
// convenção de window_manager.js/clippy_agent.js) usando
// `dash_clientside.set_props` — a forma suportada de um listener puro de
// evento de browser empurrar dado pra um componente Dash sem precisar de um
// Input que já exista no lado Python pra disparar um clientside_callback.
(function () {
  'use strict';

  var STORE_COLADA = 'store-diario98-imagem-colada';
  var STORE_ARMADA = 'store-diario98-posicao-ativa-print';

  // Feedback 2026-07-20: "eu não quero ter que apertar em um botão para
  // depois dar Ctrl+V, o sistema tem que capturar o comando de colar
  // automaticamente" — ver assets/diario_paste.js pro mesmo raciocínio
  // completo. Qualquer clique/foco num campo do card (id pattern-matching
  // `{"type":"ea98-...","index":...}`) já arma aquele card sozinho.
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
