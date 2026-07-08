// assets/diario_paste.js
// Permite colar (Ctrl+V) um print/gráfico direto no formulário "Novo Trade"
// da aba Diário, além do upload por arquivo (dcc.Upload). Não existe
// componente Dash nativo pra capturar o evento `paste` do navegador, então
// isso é JS solto em assets/ usando `dash_clientside.set_props` — a forma
// suportada de um listener puro de evento de browser empurrar dado pra um
// componente Dash (aqui, `store-diario-imagem-colada`) sem precisar de um
// Input que já exista no lado Python pra disparar um clientside_callback.
// Mesma lógica de assets_98/diario_paste.js — duplicado propositalmente
// porque assets/ e assets_98/ são isolados por front-end, nunca compartilham
// arquivo.
(function () {
  'use strict';

  var STORE_ID = 'store-diario-imagem-colada';

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
          window.dash_clientside.set_props(STORE_ID, { data: e.target.result });
        }
      };
      leitor.readAsDataURL(arquivo);

      evento.preventDefault();
      break;
    }
  });
})();
