// Balão explicativo dos velocímetros do hero (2026-07-16, pedido do Jesué).
// Os gauges vivem dentro de #hero-conteudo, reconstruído do zero a cada
// tick de 1s (ver dashboard.py::atualizar_dashboard) — um listener preso
// diretamente a cada `dcc.Graph` seria destruído e perdido a cada
// reconstrução. Delegação de evento em `document` resolve isso de graça:
// não importa quantas vezes o DOM por baixo seja substituído, o clique
// ainda borbulha até aqui. Mesmo raciocínio de `.tracado-tool-btn`/
// `wireBotaoResetZoom` (ver assets/lwchart.js) — chrome de UI client-side
// puro, sem n_clicks do Dash.
document.addEventListener('click', function (e) {
  var alvo = e.target.closest('.gauge-clicavel');
  if (!alvo) return;
  var gauge = alvo.getAttribute('data-gauge');
  if (!gauge) return;
  dash_clientside.set_props('store-gauge-selecionado', { data: gauge });
});
