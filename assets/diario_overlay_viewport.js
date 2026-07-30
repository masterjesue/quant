// assets/diario_overlay_viewport.js
//
// Centraliza os pop-ups do Diário (print ampliado, edição de pendência,
// edição de trade) no VIEWPORT REAL da janela — não na altura do iframe.
//
// O PROBLEMA (feedback do Jesué, 2026-07-30: "a visualização dos prints abre
// no meio da página, preciso rolar para chegar lá"):
// o Diário roda num <iframe> que se auto-ajusta pra caber TODO o conteúdo
// (ver diario_iframe_resize.js) — o iframe nunca tem scroll próprio. A
// consequência é que, DENTRO dele, `100vh` e `position:fixed` se referem à
// altura TOTAL do conteúdo, não ao que o usuário está vendo. Um `88vh`
// dentro de um iframe de 3000px vira uma caixa de 2640px: o pop-up abre
// gigante, muito abaixo da dobra, e a imagem (object-fit: contain dentro de
// um container enorme) fica perdida no meio de uma área cinza. Foi
// exatamente o que o screenshot mostrou.
//
// Medido ao vivo em 2026-07-30: em :8053 standalone `innerHeight` = 720 e
// tudo funciona; embutido em :8050 o mesmo `innerHeight` é a altura crescida
// do iframe. Por isso o bug nunca aparecia em teste standalone.
//
// A tentativa anterior (renderizar em fluxo normal + scrollIntoView, ver
// `_overlay` no diario_app_classico.py) contornava o sintoma de "invisível"
// mas produzia exatamente o "abre no meio da página, preciso rolar".
//
// A SOLUÇÃO: o PAI (dashboard.py/dashboard_98.py) sabe onde o iframe está em
// relação ao viewport de verdade. Ele mede e manda pro FILHO, que publica os
// valores como CSS custom properties. O CSS do overlay
// (.diario-overlay-viewport, em style.css) posiciona por elas. Quando o
// Diário roda standalone (:8053 direto no browser), nada disso é necessário:
// o fallback do CSS é `position: fixed; inset: 0`, que já é o certo.
(function () {
  'use strict';

  var MENSAGEM = 'diario-viewport';

  if (window.self !== window.top) {
    // ---------------------------------------------------------------- FILHO
    // Recebe a fatia visível e publica em custom properties na raiz.
    // `--sq-vp-topo` é o quanto do iframe já passou acima da dobra; somado a
    // `--sq-vp-altura` define exatamente o retângulo que o usuário enxerga.
    var raiz = document.documentElement;
    raiz.classList.add('diario-embutido');
    window.addEventListener('message', function (evento) {
      var d = evento.data;
      if (!d || d.tipo !== MENSAGEM) return;
      raiz.style.setProperty('--sq-vp-topo', d.topo + 'px');
      raiz.style.setProperty('--sq-vp-altura', d.altura + 'px');
    });
    // Pede a medida assim que carrega — o pai só emite em scroll/resize, e
    // sem isto o primeiro pop-up abriria antes de qualquer evento acontecer.
    window.parent.postMessage({ tipo: MENSAGEM + '-pedido' }, '*');
  } else {
    // ------------------------------------------------------------------ PAI
    // Uma única função de medida, chamada em scroll/resize (passivo, e com
    // rAF pra não medir mais de uma vez por frame) e sob demanda quando um
    // filho pede.
    var agendado = false;

    function medirEEnviar() {
      agendado = false;
      var iframes = document.querySelectorAll('iframe');
      for (var i = 0; i < iframes.length; i++) {
        var iframe = iframes[i];
        if (!iframe.contentWindow) continue;
        var r = iframe.getBoundingClientRect();
        // Interseção do retângulo do iframe com o viewport da janela.
        var topoVisivel = Math.max(r.top, 0);
        var baseVisivel = Math.min(r.bottom, window.innerHeight);
        var altura = Math.max(baseVisivel - topoVisivel, 0);
        if (altura <= 0) continue;  // iframe fora da tela — nada a posicionar
        try {
          iframe.contentWindow.postMessage({
            tipo: MENSAGEM,
            // Offset DENTRO do iframe onde começa a parte visível.
            topo: Math.round(topoVisivel - r.top),
            altura: Math.round(altura),
          }, '*');
        } catch (e) { /* iframe de outra origem — ignora */ }
      }
    }

    function agendar() {
      if (agendado) return;
      agendado = true;
      window.requestAnimationFrame(medirEEnviar);
    }

    window.addEventListener('scroll', agendar, { passive: true });
    window.addEventListener('resize', agendar, { passive: true });
    window.addEventListener('message', function (evento) {
      if (evento.data && evento.data.tipo === MENSAGEM + '-pedido') medirEEnviar();
    });
    // O iframe do Diário cresce sozinho (diario_iframe_resize.js); toda vez
    // que isso acontece a fatia visível muda de posição relativa.
    if (window.ResizeObserver) {
      new ResizeObserver(agendar).observe(document.body);
    }
    agendar();
  }
})();
