// assets/diario_iframe_resize.js
// O Diário roda embutido via <iframe src="http://127.0.0.1:8053/"> dentro do
// SQ clássico (dashboard.py) com altura fixa (80vh) — quando o conteúdo da
// aba ativa (ex: Estatísticas, com cards + calendário + gráfico) é mais alto
// que isso, o IFRAME ganha seu PRÓPRIO scroll interno, independente do
// scroll da página — duas barras de rolagem aninhadas, uma delas "sobrando"
// (feedback 2026-07-12). Este script faz o iframe se auto-ajustar à altura
// real do conteúdo em vez de rolar por conta própria: o FILHO (rodando
// dentro do iframe) observa o próprio tamanho e avisa o PAI via
// postMessage; o PAI aplica a altura recebida no elemento <iframe>, deixando
// só o scroll da página como única barra de rolagem. Um arquivo só porque
// assets/ é compartilhado pelos dois processos — o branch abaixo
// (`window.self !== window.top`) decide qual papel rodar.
(function () {
  'use strict';

  var MENSAGEM = 'diario-altura';

  if (window.self !== window.top) {
    // Lado FILHO — dentro do iframe (diario_app_classico.py). `overflow:
    // hidden` no próprio documento do iframe é o que garante UMA barra só:
    // sem isso, entre o conteúdo crescer e o PAI aplicar a nova altura
    // (round-trip de postMessage, nunca instantâneo) o iframe mostra a
    // própria barra de rolagem por uma fração de segundo — com overflow
    // escondido, esse intervalo só corta o conteúdo (invisível) em vez de
    // rolar, e a barra nunca chega a aparecer.
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';

    // BUG GRAVE (feedback 2026-07-12, "barra de rolagem diminuindo
    // infinitamente"): o layout raiz do app (diario_app_classico.py) tem
    // `minHeight: '100vh'` — quando embutido, "100vh" É a altura do
    // PRÓPRIO iframe. Isso cria um loop de realimentação: medir altura →
    // PAI aumenta o iframe → 100vh cresce junto → nova medição maior →
    // PAI aumenta de novo → ... sem nunca convergir, crescendo pra sempre.
    // Zerar esse `minHeight` só no caso embutido quebra o ciclo na raiz:
    // a altura medida passa a refletir só o conteúdo real, nunca o
    // tamanho do próprio iframe.
    var raiz = document.querySelector('#react-entry-point > div');
    if (raiz) raiz.style.minHeight = '0';

    var ultimaAltura = 0;
    function enviarAltura() {
      var altura = document.documentElement.scrollHeight;
      if (altura === ultimaAltura) return;
      ultimaAltura = altura;
      window.parent.postMessage({ tipo: MENSAGEM, altura: altura }, '*');
    }
    new ResizeObserver(enviarAltura).observe(document.body);
    enviarAltura();
  } else {
    // Lado PAI — dashboard.py, dono do <iframe src=".../8053/">. Disjuntor
    // de segurança (defesa em profundidade, além do fix na raiz do loop no
    // lado FILHO acima): se em algum momento voltarem a chegar mensagens de
    // altura crescendo sem parar, para de aplicar em vez de deixar a
    // página crescer pra sempre e travar a aba.
    var ALTURA_MAXIMA = 20000;
    var mensagensRecentes = 0;
    var janelaContagem = null;
    window.addEventListener('message', function (evento) {
      if (!evento.data || evento.data.tipo !== MENSAGEM) return;
      clearTimeout(janelaContagem);
      janelaContagem = setTimeout(function () { mensagensRecentes = 0; }, 2000);
      mensagensRecentes += 1;
      if (mensagensRecentes > 30 || evento.data.altura > ALTURA_MAXIMA) {
        console.warn('diario_iframe_resize: muitas mensagens de altura ou altura implausível — ignorando (possível loop).', evento.data);
        return;
      }
      var iframe = document.querySelector('#painel-diario iframe');
      if (iframe) iframe.style.height = evento.data.altura + 'px';
    });
  }
})();
