// assets_98/clippy_agent.js
// Clippy como Mentor IA — sprite arrastável + balão de fala fiel ao
// original (não uma janela). Dados reais de animação do clippy.js (MIT,
// github.com/pi0/clippyjs), que preserva o personagem do Office 97
// (design de Kevan Atteberry). Assets em assets_98/clippy/ (map.png +
// agent.json). Balão (posição/reposicionamento em 4 cantos, tip.png)
// portado de github.com/clippyjs/clippy.js (MIT) — mesma linhagem que
// felixrieseberg/clippy usa como referência.
//
// Não depende de nenhuma prop Dash — é chrome puramente visual, reagindo
// a mutações do DOM (#chat-historico) e a cliques/drag, então vive aqui,
// não em clientside_callback.
(function () {
  'use strict';

  var FRAME_W = 124, FRAME_H = 93;
  var IDLE_ANIMATIONS = [
    'Idle1_1', 'IdleSideToSide', 'IdleHeadScratch', 'IdleEyeBrowRaise',
    'IdleFingerTap', 'IdleAtom', 'IdleRopePile',
  ];
  var IDLE_MIN_MS = 6000, IDLE_MAX_MS = 14000;
  var DRAG_THRESHOLD = 4; // px de movimento antes de virar "arrastar" (não clique)
  var STORAGE_KEY = 'sq98:clippy:v1';

  var agentData = null;
  var el = null;       // #clippy-agent (sprite)
  var balloon = null;  // #clippy-balloon
  var idleTimer = null;
  var playToken = 0;   // invalida callbacks de uma animação anterior ao trocar
  var balloonOpen = false;
  var demoIndex = 0; // easter egg: qual animação o próximo clique mostra

  function loadData() {
    return fetch('/assets/clippy/agent.json').then(function (r) { return r.json(); });
  }

  function loadPos() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)); } catch (e) { return null; }
  }
  function savePos(left, top) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ left: left, top: top })); } catch (e) {}
  }

  function setFrame(x, y) {
    if (el) el.style.backgroundPosition = '-' + x + 'px -' + y + 'px';
  }

  function play(name, onDone) {
    if (!agentData || !agentData.animations[name]) { if (onDone) onDone(); return; }
    var myToken = ++playToken;
    var frames = agentData.animations[name].frames;
    var i = 0;
    function step() {
      if (myToken !== playToken) return; // uma animação nova começou nesse meio-tempo
      if (i >= frames.length) { if (onDone) onDone(); return; }
      var frame = frames[i];
      // Alguns frames (ex. o 1º de "Show") só têm `duration`, sem `images`
      // — são frames de espera, mantêm o sprite atual parado. Sem essa
      // checagem, `frame.images[0]` estourava TypeError logo na primeira
      // animação e travava tudo antes até de conectar as reações do chat
      // e o easter egg (nenhum dos dois rodava depois de um throw aqui).
      if (frame.images && frame.images.length) {
        var img = frame.images[0];
        setFrame(img[0], img[1]);
      }
      i += 1;
      setTimeout(step, frame.duration);
    }
    step();
  }

  function scheduleIdle() {
    clearTimeout(idleTimer);
    var delay = IDLE_MIN_MS + Math.random() * (IDLE_MAX_MS - IDLE_MIN_MS);
    idleTimer = setTimeout(function () {
      var nome = IDLE_ANIMATIONS[Math.floor(Math.random() * IDLE_ANIMATIONS.length)];
      play(nome, scheduleIdle);
    }, delay);
  }

  // -------------------------------------------------------------------------
  // Balão — reposicionamento em 4 cantos, portado de clippy.js/src/balloon.js
  // (mesma ideia: tenta top-left/top-right/bottom-right/bottom-left até um
  // que caiba inteiro na viewport). Como o agente já é position:fixed, as
  // coordenadas são direto em viewport, sem offset de scroll.
  // -------------------------------------------------------------------------
  var BALLOON_MARGIN = 12;

  function agentRect() { return el.getBoundingClientRect(); }

  function positionBalloon(side) {
    var r = agentRect();
    var bW = balloon.offsetWidth, bH = balloon.offsetHeight;
    var left, top;
    switch (side) {
      case 'top-left':     // balão acima, alinhado pela direita do agente
        left = r.left + r.width - bW; top = r.top - bH - BALLOON_MARGIN; break;
      case 'top-right':    // balão acima, alinhado pela esquerda do agente
        left = r.left; top = r.top - bH - BALLOON_MARGIN; break;
      case 'bottom-right': // balão abaixo, alinhado pela esquerda
        left = r.left; top = r.top + r.height + BALLOON_MARGIN; break;
      case 'bottom-left':  // balão abaixo, alinhado pela direita
        left = r.left + r.width - bW; top = r.top + r.height + BALLOON_MARGIN; break;
    }
    balloon.style.left = left + 'px';
    balloon.style.top = top + 'px';
    balloon.classList.remove('clippy-top-left', 'clippy-top-right', 'clippy-bottom-right', 'clippy-bottom-left');
    balloon.classList.add('clippy-' + side);
    return { left: left, top: top, width: bW, height: bH };
  }

  function fitsViewport(pos) {
    var m = 4;
    if (pos.top - m < 0 || pos.left - m < 0) return false;
    if (pos.top + pos.height + m > window.innerHeight) return false;
    if (pos.left + pos.width + m > window.innerWidth) return false;
    return true;
  }

  function repositionBalloon() {
    var sides = ['top-left', 'top-right', 'bottom-right', 'bottom-left'];
    for (var i = 0; i < sides.length; i++) {
      var pos = positionBalloon(sides[i]);
      if (fitsViewport(pos)) return;
    }
    // nenhum coube perfeitamente — fica no último tentado, só clampado
    balloon.style.left = Math.max(4, Math.min(parseInt(balloon.style.left, 10), window.innerWidth - balloon.offsetWidth - 4)) + 'px';
    balloon.style.top = Math.max(4, Math.min(parseInt(balloon.style.top, 10), window.innerHeight - balloon.offsetHeight - 4)) + 'px';
  }

  function openBalloon() {
    balloonOpen = true;
    balloon.classList.add('open');
    repositionBalloon();
  }
  function closeBalloon() {
    balloonOpen = false;
    balloon.classList.remove('open');
  }
  function toggleBalloon() {
    if (balloonOpen) closeBalloon(); else openBalloon();
  }

  // -------------------------------------------------------------------------
  // Drag — clique simples abre/fecha o balão; arrastar move o Clippy (fiel
  // à experiência original: ele pode ser clicado E arrastado).
  // -------------------------------------------------------------------------
  function wireDrag() {
    var dragging = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0, width = 0, height = 0;
    el.addEventListener('mousedown', function (e) {
      dragging = true; moved = false;
      sx = e.clientX; sy = e.clientY;
      var r = agentRect(); ox = r.left; oy = r.top;
      // Cacheado 1x (não em cada mousemove) — ler el.offsetWidth/offsetHeight
      // depois de escrever el.style.left/top força layout síncrono a cada
      // frame do arraste (mesmo bug corrigido em window_manager.js).
      width = r.width; height = r.height;
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - sx, dy = e.clientY - sy;
      if (!moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      moved = true;
      var left = Math.max(0, Math.min(ox + dx, window.innerWidth - width));
      var top = Math.max(0, Math.min(oy + dy, window.innerHeight - 30 - height));
      el.style.left = left + 'px'; el.style.top = top + 'px';
      el.style.right = 'auto'; el.style.bottom = 'auto';
      if (balloonOpen) repositionBalloon();
    });
    window.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      if (moved) {
        savePos(el.style.left, el.style.top);
      } else {
        toggleBalloon();
      }
    });
  }

  function placeInitial() {
    var saved = loadPos();
    if (saved && saved.left && saved.top) {
      el.style.left = saved.left; el.style.top = saved.top;
    } else {
      el.style.right = '24px'; el.style.bottom = '40px';
    }
  }

  function wireReactions() {
    var historico = document.getElementById('chat-historico');
    if (historico) {
      var obs = new MutationObserver(function () {
        play('GetAttention', scheduleIdle);
      });
      obs.observe(historico, { childList: true });
    }
    var enviar = document.getElementById('btn-chat-enviar');
    var input = document.getElementById('chat-input');
    var pensando = function () { play('Thinking'); };
    if (enviar) enviar.addEventListener('click', pensando);
    if (input) input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') pensando();
    });
  }

  // Easter egg: botão discreto ao lado do relógio (ver win98_taskbar() em
  // estilo_win98.py) — cada clique avança UMA animação do catálogo (não a
  // sequência toda de uma vez; primeira versão disparava as 43 em cadeia
  // e parecia estar "travado em loop").
  function wirePlayAllEasterEgg() {
    var btn = document.getElementById('clippy-play-all-btn');
    if (!btn || !agentData) return;
    var nomes = Object.keys(agentData.animations);
    btn.addEventListener('click', function () {
      clearTimeout(idleTimer);
      var nome = nomes[demoIndex % nomes.length];
      demoIndex += 1;
      play(nome, scheduleIdle);
    });
  }

  function initClippy() {
    el = document.createElement('div');
    el.id = 'clippy-agent';
    el.title = 'Mentor IA (Clippy) — clique para conversar, arraste para mover';
    el.style.width = FRAME_W + 'px';
    el.style.height = FRAME_H + 'px';
    el.style.backgroundImage = "url('/assets/clippy/map.png')";
    el.style.backgroundRepeat = 'no-repeat';
    el.style.imageRendering = 'pixelated';
    document.body.appendChild(el);
    placeInitial();
    wireDrag();

    balloon = document.getElementById('clippy-balloon');

    loadData().then(function (data) {
      agentData = data;
      play('Show', function () {
        play('Greeting', scheduleIdle);
      });
      wireReactions();
      wirePlayAllEasterEgg();
    });

    window.addEventListener('resize', function () {
      if (balloonOpen) repositionBalloon();
    });
  }

  // Sem garantia de ordem entre este <script> estático e o primeiro commit
  // do React/Dash — espera o balão (renderizado pelo Dash) existir.
  function whenBalloonExists(cb) {
    if (document.getElementById('clippy-balloon')) { cb(); return; }
    var observer = new MutationObserver(function () {
      if (document.getElementById('clippy-balloon')) { observer.disconnect(); cb(); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(function () {
      observer.disconnect();
      if (!document.getElementById('clippy-balloon')) {
        console.warn('[clippy_agent] #clippy-balloon não encontrado após 8s.');
      }
    }, 8000);
  }

  whenBalloonExists(initClippy);
})();
