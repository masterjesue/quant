// Balão explicativo dos velocímetros do hero (2026-07-16, pedido do Jesué).
// Mesmo raciocínio de assets/hero_gauges.js (skin clássico): os gauges
// vivem dentro de #hero-conteudo, reconstruído do zero a cada tick de 1s
// (ver dashboard_98.py::atualizar_dashboard) — delegação de evento em
// `document` sobrevive a essa reconstrução sem precisar re-anexar listener
// nenhum. Mesmo padrão de `.tracado-tool-btn`/`wireBotaoResetZoom` (ver
// assets_98/lwchart.js) — chrome de UI client-side puro, sem n_clicks.
document.addEventListener('click', function (e) {
  var alvo = e.target.closest('.gauge-clicavel');
  if (!alvo) return;
  var gauge = alvo.getAttribute('data-gauge');
  if (!gauge) return;
  dash_clientside.set_props('store-gauge-selecionado', { data: gauge });
});
