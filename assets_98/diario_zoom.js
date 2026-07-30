// assets_98/diario_zoom.js
// Ver assets/diario_zoom.js pro raciocínio completo (feedback 2026-07-20:
// "preencher o bloco inteiro, zoom pela rolagem do scroll, clicar e
// arrastar"). Mesma lógica, duplicado propositalmente (assets/ e assets_98/
// nunca compartilham arquivo, mesma convenção de diario_paste.js).
(function () {
  'use strict';

  var SELETOR_CONTAINER = '.diario-zoom-container';
  var SELETOR_IMG = '.diario-zoom-img';
  var ESCALA_MIN = 1;
  var ESCALA_MAX = 5;
  var VELOCIDADE_ZOOM = 0.0018;

  function estadoDe(img) {
    if (!img._zoom) img._zoom = { escala: ESCALA_MIN, x: 0, y: 0 };
    return img._zoom;
  }

  function aplicar(img, estado) {
    img.style.transform = 'translate(' + estado.x + 'px, ' + estado.y + 'px) scale(' + estado.escala + ')';
  }

  function limitar(estado, container) {
    var largura = container.clientWidth * estado.escala;
    var altura = container.clientHeight * estado.escala;
    var minX = Math.min(0, container.clientWidth - largura);
    var minY = Math.min(0, container.clientHeight - altura);
    estado.x = Math.max(minX, Math.min(0, estado.x));
    estado.y = Math.max(minY, Math.min(0, estado.y));
  }

  document.addEventListener('wheel', function (evento) {
    var container = evento.target.closest ? evento.target.closest(SELETOR_CONTAINER) : null;
    if (!container) return;
    var img = container.querySelector(SELETOR_IMG);
    if (!img) return;
    evento.preventDefault();

    var estado = estadoDe(img);
    var retangulo = container.getBoundingClientRect();
    var cursorX = evento.clientX - retangulo.left;
    var cursorY = evento.clientY - retangulo.top;
    var conteudoX = (cursorX - estado.x) / estado.escala;
    var conteudoY = (cursorY - estado.y) / estado.escala;

    var fator = Math.exp(-evento.deltaY * VELOCIDADE_ZOOM);
    var novaEscala = Math.max(ESCALA_MIN, Math.min(ESCALA_MAX, estado.escala * fator));

    estado.escala = novaEscala;
    if (novaEscala <= ESCALA_MIN) {
      estado.x = 0;
      estado.y = 0;
    } else {
      estado.x = cursorX - conteudoX * novaEscala;
      estado.y = cursorY - conteudoY * novaEscala;
    }
    limitar(estado, container);
    aplicar(img, estado);
    container.classList.toggle('diario-zoom-ativo', novaEscala > ESCALA_MIN);
  }, { passive: false });

  document.addEventListener('pointerdown', function (evento) {
    var container = evento.target.closest ? evento.target.closest(SELETOR_CONTAINER) : null;
    if (!container) return;
    var img = container.querySelector(SELETOR_IMG);
    if (!img) return;
    var estado = estadoDe(img);
    if (estado.escala <= ESCALA_MIN) return;

    evento.preventDefault();
    container.setPointerCapture(evento.pointerId);
    var origX = estado.x, origY = estado.y;
    var startX = evento.clientX, startY = evento.clientY;
    container.classList.add('diario-zoom-arrastando');

    function mover(e2) {
      estado.x = origX + (e2.clientX - startX);
      estado.y = origY + (e2.clientY - startY);
      limitar(estado, container);
      aplicar(img, estado);
    }
    function soltar() {
      container.classList.remove('diario-zoom-arrastando');
      container.removeEventListener('pointermove', mover);
      container.removeEventListener('pointerup', soltar);
      container.removeEventListener('pointercancel', soltar);
    }
    container.addEventListener('pointermove', mover);
    container.addEventListener('pointerup', soltar);
    container.addEventListener('pointercancel', soltar);
  });

  // Botões explícitos de zoom (feedback 2026-07-30: "com a opção de expandir
  // e dar zoom"). A roda do mouse já fazia zoom desde 2026-07-20, mas era
  // invisível — nada na tela dizia que dava. Delegação pelo atributo
  // `data-zoom-acao`, no mesmo `document`, pra não precisar de id Dash nem
  // callback Python (zoom é puramente client-side).
  function containerVisivel() {
    var todos = document.querySelectorAll(SELETOR_CONTAINER);
    return todos.length ? todos[todos.length - 1] : null;
  }

  function aplicarAcao(acao) {
    var container = containerVisivel();
    if (!container) return;
    var img = container.querySelector(SELETOR_IMG);
    if (!img) return;
    var estado = estadoDe(img);
    if (acao === 'reset') {
      estado.escala = ESCALA_MIN; estado.x = 0; estado.y = 0;
    } else {
      var anterior = estado.escala;
      var fator = acao === 'mais' ? 1.35 : 1 / 1.35;
      estado.escala = Math.max(ESCALA_MIN, Math.min(ESCALA_MAX, estado.escala * fator));
      // Zoom pelo CENTRO do container (a roda usa o cursor como âncora; sem
      // cursor, o centro é o que não desloca a imagem de forma estranha).
      var cx = container.clientWidth / 2, cy = container.clientHeight / 2;
      var razao = estado.escala / anterior;
      estado.x = cx - (cx - estado.x) * razao;
      estado.y = cy - (cy - estado.y) * razao;
    }
    limitar(estado, container);
    aplicar(img, estado);
    container.classList.toggle('diario-zoom-ativo', estado.escala > ESCALA_MIN);
  }

  document.addEventListener('click', function (evento) {
    var alvo = evento.target.closest ? evento.target.closest('[data-zoom-acao]') : null;
    if (!alvo) return;
    evento.preventDefault();
    aplicarAcao(alvo.getAttribute('data-zoom-acao'));
  });

  // Teclado: + / - / 0 enquanto um pop-up de imagem estiver aberto, e Esc
  // fechando (o Esc dispara o clique no backdrop, que a callback Python já
  // trata — evita duplicar a lógica de fechamento aqui).
  document.addEventListener('keydown', function (evento) {
    if (!containerVisivel()) return;
    if (evento.key === '+' || evento.key === '=') aplicarAcao('mais');
    else if (evento.key === '-' || evento.key === '_') aplicarAcao('menos');
    else if (evento.key === '0') aplicarAcao('reset');
    else if (evento.key === 'Escape') {
      var fundo = document.querySelector('.diario-overlay-backdrop');
      if (fundo) fundo.click();
    }
  });
})();
