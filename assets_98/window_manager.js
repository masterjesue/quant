// assets_98/window_manager.js
// Gerencia a "casca" das janelas Win98 — drag, foco/z-index, minimizar,
// maximizar, fechar, taskbar, menu Start e relógio — inteiramente no
// cliente. Os nós DOM das janelas (.win98-window) são criados uma única
// vez pelo app.layout do Dash e NUNCA destruídos por callback (só o
// conteúdo interno, ex. #hero-conteudo, é substituído a cada tick de 1s/
// 30s). Por isso um script estático em assets_98/ (mesma convenção de
// assets_98/style.css, servido pelo Dash via assets_folder='assets_98' em
// dashboard_98.py, carregado uma vez no load da página) é suficiente:
// nenhuma dessas mecânicas depende de dado vindo do Python.
//
// INVARIANTE: depois do mount, top/left/width/height/zIndex inline de
// .win98-window são propriedade exclusiva deste script. Nenhum callback
// Python deve escrever essas mesmas chaves via Output(<id>, 'style').
(function () {
  'use strict';

  var PERSIST_LAYOUT = true;   // false = sempre reseta pro layout padrão a cada load
  // v4: bump sempre que os defaults de posição/tamanho mudarem no Python —
  // sem isso, quem já abriu/moveu uma janela nunca vê o novo default (o
  // localStorage antigo é restaurado e mascara a mudança por completo).
  var STORAGE_KEY = 'sq98:layout:v5';
  var TASKBAR_HEIGHT = 28;
  // Coluna fixa dos ícones de desktop (top:10/left:10, 76px de largura +
  // folga — ver win98_desktop_icon em estilo_win98.py). Reservada na hora
  // de calcular a posição/largura padrão de cada janela, senão em telas
  // menores a primeira coluna de janelas nascia tampando os ícones por
  // completo (bug encontrado em 2026-07-10).
  var ICON_RAIL = 100;

  var taskbarButtons, windows, zTop;
  var layout = loadLayout();
  var saveTimer = null;

  function loadLayout() {
    if (!PERSIST_LAYOUT) return {};
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
    catch (e) { return {}; }
  }
  function scheduleSave() {
    if (!PERSIST_LAYOUT) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout)); } catch (e) {}
    }, 250);
  }
  function rememberWindow(win) {
    layout[win.dataset.window] = {
      top: win.style.top, left: win.style.left,
      width: win.style.width, height: win.style.height,
      z: parseInt(win.style.zIndex, 10) || 0,
      minimized: win.classList.contains('minimized'),
      closed: win.classList.contains('closed'),
    };
    scheduleSave();
  }
  function clamp(v, min, max) { return Math.max(min, Math.min(v, max)); }

  // Arrastar (mover OU redimensionar) uma janela cujo conteúdo é um
  // <iframe> (Diário/Posição) parava de responder no meio do arraste
  // sempre que o cursor passava por cima do iframe — feedback 2026-07-12
  // ("já tinha pedido isso, não sei porque não deu certo"). Causa: um
  // <iframe> é um browsing context separado; `mousemove` sobre ele dispara
  // no DOCUMENTO DELE, não no `window` do documento pai onde os listeners
  // de drag/resize escutam — o evento simplesmente não chega. `.win98-
  // interacting iframe { pointer-events: none }` (ver style.css) desliga
  // temporariamente a captura de mouse dos iframes enquanto QUALQUER
  // arraste estiver ativo, deixando o `mousemove` atravessar até o
  // `window` do pai normalmente; volta ao normal no mouseup.
  var interactingTimeout = null;
  function setInteracting(ligado) {
    document.documentElement.classList.toggle('win98-interacting', ligado);
    clearTimeout(interactingTimeout);
    if (ligado) {
      // Rede de segurança (bug real 2026-07-21, achado numa varredura de
      // testes): os handlers de mousedown/mouseup de arraste/resize abaixo
      // usam variáveis `dragging` locais por janela/alça — se o `mouseup`
      // nunca chegar ao `window` (solto fora da viewport do browser, ou
      // qualquer outro caso em que o evento não propaga), `win98-
      // interacting` ficava preso pra sempre e TODOS os iframes (Diário e
      // Posição, nas duas janelas) perdiam clique de mouse real
      // indefinidamente (`element.click()` via JS continuava funcionando,
      // o que mascarou o problema em teste automatizado — só clique real
      // de mouse é afetado por `pointer-events`). Força destravar depois
      // de alguns segundos em vez de depender só do `mouseup` chegar.
      interactingTimeout = setTimeout(function () { setInteracting(false); }, 4000);
    }
  }
  // Foco do browser perdido no meio de um arraste (troca de app/janela com
  // o botão ainda pressionado) — o `mouseup` nunca vai chegar nesse caso,
  // não precisa esperar o timeout acima pra destravar.
  window.addEventListener('blur', function () { setInteracting(false); });

  function placeInitial(win, index) {
    var saved = layout[win.dataset.window];
    var maxLeft = function () { return Math.max(0, window.innerWidth - win.offsetWidth - 4); };
    var maxTop = function () { return Math.max(0, window.innerHeight - TASKBAR_HEIGHT - win.offsetHeight - 4); };

    if (saved) {
      if (saved.width) win.style.width = saved.width;
      if (saved.height) win.style.height = saved.height;
      win.style.top = clamp(parseInt(saved.top, 10) || 0, 0, maxTop()) + 'px';
      win.style.left = clamp(parseInt(saved.left, 10) || 0, 0, maxLeft()) + 'px';
      if (saved.minimized) win.classList.add('minimized');
      if (saved.closed) win.classList.add('closed'); else win.classList.remove('closed');
      win.style.zIndex = saved.z || (10 + index);
      return;
    }

    // primeira visita: usa a posição padrão percentual (data-default-top/
    // left, definida em estilo_win98.py), calculada em cima do viewport
    // atual — nunca pixel fixo. Sem cascata por índice: os defaults atuais
    // já formam uma grade deliberada (Quant Lab / Price Action / Big
    // Players / Gráfico lado a lado); somar um offset crescente por índice
    // desalinhava essa grade (bug encontrado em 2026-07-10 — Big Players e
    // Gráfico, índices vizinhos, recebiam deslocamentos diferentes e
    // paravam de se encaixar direito na coluna da direita).
    if (win.dataset.startClosed === 'true') win.classList.add('closed');

    // Largura recalculada em cima da largura ÚTIL (viewport menos a
    // coluna de ícones), não do viewport inteiro — mesma unidade de
    // medida usada no `left` abaixo, senão as duas contas usam bases
    // diferentes e a grade desalinha nas bordas. Precisa vir ANTES de ler
    // maxLeft()/win.offsetWidth (a largura inline definida por
    // estilo_win98.py, em % do viewport cheio, está errada até aqui rodar).
    var pctWidth = parseFloat(win.dataset.defaultWidth || '0');
    if (pctWidth) {
      win.style.width = Math.round((window.innerWidth - ICON_RAIL) * (pctWidth / 100)) + 'px';
    }

    var pctTop = parseFloat(win.dataset.defaultTop || '8');
    var pctLeft = parseFloat(win.dataset.defaultLeft || '6');
    var top = (window.innerHeight - TASKBAR_HEIGHT) * (pctTop / 100);
    var left = ICON_RAIL + (window.innerWidth - ICON_RAIL) * (pctLeft / 100);
    win.style.top = clamp(top, 0, maxTop()) + 'px';
    win.style.left = clamp(left, 0, maxLeft()) + 'px';
    win.style.zIndex = 10 + index;
  }

  function clampAllToViewport() {
    windows.forEach(function (win) {
      if (win.classList.contains('maximized')) return;
      var maxLeft = Math.max(0, window.innerWidth - win.offsetWidth - 4);
      var maxTop = Math.max(0, window.innerHeight - TASKBAR_HEIGHT - win.offsetHeight - 4);
      win.style.left = clamp(parseInt(win.style.left, 10) || 0, 0, maxLeft) + 'px';
      win.style.top = clamp(parseInt(win.style.top, 10) || 0, 0, maxTop) + 'px';
    });
  }

  function titleOf(win) {
    var t = win.querySelector('.win98-titlebar-text > span:last-child');
    return t ? t.textContent.trim() : win.dataset.window;
  }
  // Extrai só a <img> do ícone (não o outerHTML do wrapper inteiro) —
  // evita que markup extra do wrapper (ou um fallback de emoji com texto)
  // acabe duplicado visualmente ao lado do <span> de título no botão.
  function iconOf(win) {
    var img = win.querySelector('.win98-titlebar-icon img');
    if (img) return '<img src="' + img.getAttribute('src') + '" style="width:14px;height:14px;image-rendering:pixelated;vertical-align:middle">';
    var span = win.querySelector('.win98-titlebar-icon');
    return span ? '<span>' + span.textContent + '</span>' : '';
  }

  function buildTaskbarButtons() {
    taskbarButtons.innerHTML = '';
    windows.forEach(function (win) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'win98-task-btn';
      btn.dataset.win = win.dataset.window;
      btn.innerHTML = iconOf(win) + '<span>' + titleOf(win) + '</span>';
      btn.addEventListener('click', function () { toggleFromTaskbar(win); });
      taskbarButtons.appendChild(btn);
    });
    windows.forEach(refreshTaskbarState);
  }

  // FIX do bug encontrado no protótipo: a visibilidade do botão da taskbar
  // depende só de "fechada" (.closed). Minimizada mantém o botão visível
  // (só perde o estado "active") — é assim que dá pra restaurar clicando
  // na taskbar. Antes a checagem usava uma classe presente nos dois casos
  // (minimizada E fechada), e o botão sumia também ao minimizar.
  function refreshTaskbarState(win) {
    var btn = taskbarButtons.querySelector('[data-win="' + win.dataset.window + '"]');
    if (!btn) return;
    var closed = win.classList.contains('closed');
    var minimized = win.classList.contains('minimized');
    btn.style.display = closed ? 'none' : 'flex';
    btn.classList.toggle('active', !closed && !minimized && parseInt(win.style.zIndex, 10) === zTop);
  }

  function focusWindow(win) {
    zTop += 1;
    win.style.zIndex = zTop;
    windows.forEach(function (w) { w.classList.toggle('inactive', w !== win); });
    windows.forEach(refreshTaskbarState);
    rememberWindow(win);
  }
  function openWindow(win) {
    win.classList.remove('closed', 'minimized');
    focusWindow(win);
  }
  function minimizeWindow(win) {
    win.classList.add('minimized');
    windows.forEach(refreshTaskbarState);
    rememberWindow(win);
  }
  function closeWindow(win) {
    win.classList.remove('minimized');
    win.classList.add('closed');
    windows.forEach(refreshTaskbarState);
    rememberWindow(win);
  }
  function toggleFromTaskbar(win) {
    if (win.classList.contains('closed') || win.classList.contains('minimized')) {
      openWindow(win);
      return;
    }
    if (parseInt(win.style.zIndex, 10) === zTop) {
      minimizeWindow(win);
      return;
    }
    focusWindow(win);
  }

  function wireWindow(win) {
    win.addEventListener('mousedown', function () { focusWindow(win); });

    var titlebar = win.querySelector('.win98-titlebar');
    var dragging = false, sx = 0, sy = 0, ox = 0, oy = 0, width = 0;
    titlebar.addEventListener('mousedown', function (e) {
      if (win.classList.contains('maximized')) return;
      if (e.target.closest('[data-action]')) return;  // não arrasta clicando em _/□/×
      dragging = true;
      setInteracting(true);
      sx = e.clientX; sy = e.clientY;
      var rect = win.getBoundingClientRect();
      ox = rect.left; oy = rect.top;
      // Cacheado 1x aqui (não em cada mousemove): ler win.offsetWidth depois
      // de escrever win.style.left/top força um forced synchronous layout
      // (flush do documento inteiro) a cada frame do arraste — o "layout
      // thrashing" que causava o travamento ao arrastar janelas, agravado
      // pelo tamanho total do DOM (ex. janela Diário montada, mesmo fechada).
      width = rect.width;
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      win.style.left = clamp(ox + (e.clientX - sx), -width + 80, window.innerWidth - 80) + 'px';
      win.style.top = clamp(oy + (e.clientY - sy), 0, window.innerHeight - TASKBAR_HEIGHT - 30) + 'px';
    });
    window.addEventListener('mouseup', function () {
      if (dragging) { dragging = false; setInteracting(false); rememberWindow(win); }
    });

    var actions = { close: closeWindow, minimize: minimizeWindow };
    win.querySelectorAll('[data-action]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var action = btn.dataset.action;
        if (action === 'maximize') { win.classList.toggle('maximized'); rememberWindow(win); }
        else if (actions[action]) actions[action](win);
      });
    });
  }

  // Cada direção mexe num subconjunto de {largura, altura, left, top}, com
  // sinal próprio — ex. arrastar a borda esquerda (w) pra direita encolhe
  // a largura E empurra o left, mantendo a borda direita parada no lugar.
  // Só bordas/cantos de baixo+direita+esquerda (sem topo): a borda de cima
  // fica colada na titlebar (arrastável) e um handle ali em cima disputaria
  // clique com ela e com os botões _/□/×.
  var RESIZE_DIRS = {
    e:  { w: 1 },
    w:  { w: -1, l: 1 },
    s:  { h: 1 },
    se: { w: 1, h: 1 },
    sw: { w: -1, l: 1, h: 1 },
  };
  var RESIZE_MIN_W = 300, RESIZE_MIN_H = 160;

  function wireResize(win) {
    Object.keys(RESIZE_DIRS).forEach(function (dir) {
      var spec = RESIZE_DIRS[dir];
      var handle = document.createElement('div');
      handle.className = 'win98-resize-handle win98-resize-' + dir;
      win.appendChild(handle);

      var dragging = false, sx = 0, sy = 0, startW = 0, startH = 0, startL = 0, startT = 0;
      handle.addEventListener('mousedown', function (e) {
        if (win.classList.contains('maximized')) return;
        e.stopPropagation();  // não deve também iniciar o drag da janela
        e.preventDefault();
        dragging = true;
        setInteracting(true);
        focusWindow(win);
        sx = e.clientX; sy = e.clientY;
        var rect = win.getBoundingClientRect();
        startW = rect.width; startH = rect.height; startL = rect.left; startT = rect.top;
      });
      window.addEventListener('mousemove', function (e) {
        if (!dragging) return;
        var dx = e.clientX - sx, dy = e.clientY - sy;
        if (spec.w) {
          var w = Math.max(RESIZE_MIN_W, startW + spec.w * dx);
          win.style.width = w + 'px';
          if (spec.l) win.style.left = (startL + (startW - w)) + 'px';
        }
        if (spec.h) {
          win.style.height = Math.max(RESIZE_MIN_H, startH + spec.h * dy) + 'px';
        }
      });
      window.addEventListener('mouseup', function () {
        if (dragging) { dragging = false; setInteracting(false); rememberWindow(win); }
      });
    });
  }

  function wireDesktopIcons() {
    document.querySelectorAll('.win98-icon').forEach(function (icon) {
      icon.addEventListener('click', function () {
        document.querySelectorAll('.win98-icon').forEach(function (i) { i.classList.remove('selected'); });
        icon.classList.add('selected');
      });
      icon.addEventListener('dblclick', function () {
        var win = document.getElementById(icon.dataset.openWindow);
        if (win) openWindow(win);
      });
    });
  }

  function wireStartMenu() {
    var startBtn = document.getElementById('start-btn');
    var startMenu = document.getElementById('start-menu');
    if (!startBtn || !startMenu) return;
    startBtn.addEventListener('click', function (e) {
      startMenu.classList.toggle('open');
      startBtn.classList.toggle('open');
      e.stopPropagation();
    });
    document.addEventListener('click', function () {
      startMenu.classList.remove('open');
      startBtn.classList.remove('open');
    });
    startMenu.querySelectorAll('[data-open-window]').forEach(function (item) {
      item.addEventListener('click', function () {
        var win = document.getElementById(item.dataset.openWindow);
        if (win) openWindow(win);
      });
    });
  }

  // FIX do relógio: o protótipo tinha dois bugs — faltava ":SS" E o
  // intervalo real era a cada 10s (setInterval(tick, 1000 * 10)), não 1s.
  // Day trade precisa de segundos certos.
  function pad2(n) { return String(n).padStart(2, '0'); }
  function tickClock() {
    var el = document.getElementById('taskbar-clock');
    if (!el) return;
    var d = new Date();
    el.textContent = pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }

  // FIX do scroll voltando pro topo a cada tick de 1s/30s: o callback do
  // Dash substitui o `children` inteiro de #hero-conteudo/#market-view-
  // conteudo (dentro do mesmo .win98-window-body rolável da Quant Lab).
  // Ao trocar, o React remove a árvore antiga antes de inserir a nova —
  // nesse instante, com o conteúdo temporariamente menor, o navegador
  // "clampa" o scrollTop do container pai pra caber (geralmente pra 0) e
  // não devolve sozinho quando o conteúdo novo (do mesmo tamanho de antes)
  // volta a caber. Fix: guardar o scrollTop em todo evento de 'scroll' e
  // reimpor esse valor sempre que o conteúdo do body mudar.
  function preserveScrollOnUpdate() {
    document.querySelectorAll('.win98-window-body').forEach(function (el) {
      if (el.dataset.scrollGuard) return;
      el.dataset.scrollGuard = '1';
      var lastTop = el.scrollTop;
      el.addEventListener('scroll', function () { lastTop = el.scrollTop; });
      var mo = new MutationObserver(function () {
        if (el.scrollTop !== lastTop) el.scrollTop = lastTop;
      });
      mo.observe(el, { childList: true, subtree: true });
    });
  }

  function initDesktop() {
    taskbarButtons = document.getElementById('taskbar-buttons');
    windows = Array.prototype.slice.call(document.querySelectorAll('.win98-window'));
    zTop = 10;
    if (!windows.length || !taskbarButtons) return;

    windows.forEach(function (win, i) {
      placeInitial(win, i);
      wireWindow(win);
      if (win.dataset.resizable === 'true') wireResize(win);
    });
    buildTaskbarButtons();
    wireDesktopIcons();
    wireStartMenu();
    preserveScrollOnUpdate();

    // zTop precisa começar no MAIOR zIndex já distribuído em placeInitial
    // (10 + index, ou o `z` salvo) — senão focusWindow (que só faz
    // `zTop += 1`) podia devolver um zIndex MENOR que o de uma janela não
    // focada, empurrando a janela "em foco" pra trás visualmente (bug
    // encontrado em 2026-07-10: a Gráfico nascia com zIndex 13, mas ao ser
    // focada no boot recebia 11 — 1 a menos que a Big Players, então a
    // Big Players cobria a titlebar/topo da Gráfico por completo).
    windows.forEach(function (w) {
      var z = parseInt(w.style.zIndex, 10) || 0;
      if (z > zTop) zTop = z;
    });

    var topWin = windows
      .filter(function (w) { return !w.classList.contains('closed'); })
      .sort(function (a, b) { return (parseInt(b.style.zIndex, 10) || 0) - (parseInt(a.style.zIndex, 10) || 0); })[0];
    if (topWin) focusWindow(topWin);

    window.addEventListener('resize', clampAllToViewport);

    tickClock();
    setInterval(tickClock, 1000);
  }

  // Não há garantia de ordem entre a execução deste <script> estático e o
  // primeiro commit do React/Dash-renderer — DOMContentLoaded sozinho não
  // é suficiente (pode rodar antes do Dash montar a árvore e retornar
  // vazio, silenciosamente). Espera a primeira .win98-window aparecer.
  function whenWindowsExist(cb) {
    if (document.querySelectorAll('.win98-window').length) { cb(); return; }
    var observer = new MutationObserver(function () {
      if (document.querySelectorAll('.win98-window').length) {
        observer.disconnect();
        cb();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(function () {
      observer.disconnect();
      if (!document.querySelectorAll('.win98-window').length) {
        console.warn('[window_manager] nenhuma .win98-window encontrada após 8s — ' +
                     'confira se app.layout renderizou e se ids/classes batem.');
      }
    }, 8000);
  }

  whenWindowsExist(initDesktop);
})();
