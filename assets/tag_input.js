// assets/tag_input.js
// Widget de tags "de verdade" (feedback 2026-07-20, print de referência:
// chips grandes/arredondados, X pra remover, Enter OU vírgula/ponto-e-vírgula
// confirmam, rodapé com contador + "Remover todas") — ver
// diario_app_classico.py::_campo_tag_widget, que monta o HTML/CSS (classes
// .tag-widget-*) mas depende inteiramente DESTE arquivo pra interatividade.
//
// BUG REAL (achado 2026-07-22): este arquivo nunca tinha sido criado — o
// widget renderizava com aparência correta (caixa, dica, contador "0
// tag(s)", botão "Remover todas"), mas digitar e apertar Enter/vírgula não
// fazia NADA, porque não existia nenhum listener escutando o campo de
// texto. Isso também explica por que o `dcc.Store` por trás nunca recebia
// nada digitado — não tinha quem escrevesse nele.
//
// Delegação de eventos em `document` (não por elemento) de propósito: os
// cards da aba Posição são recriados inteiros a cada mudança real (ver
// convenção do projeto sobre `em-andamento-conteudo`), então um listener
// preso a um elemento específico se perderia no primeiro re-render. Com
// delegação, novos widgets montados depois funcionam sem nenhum registro
// adicional.
(function () {
  'use strict';

  function containerDoWidget(el) {
    return el.closest('.tag-widget');
  }

  // Devolve o <input> DE VERDADE a partir do alvo de um evento, ou null se o
  // evento não veio do campo de tag.
  //
  // BUG REAL 2026-07-27: antes isto era só `e.target.closest('.tag-widget-input')`.
  // Funcionava enquanto o campo era um `<input class="tag-widget-input">` nu,
  // mas o `dcc.Input` desta versão passou a renderizar um wrapper
  // (`<div class="...tag-widget-input"><input class="dash-input-element">`) —
  // o `closest` subia pra DIV, e `.value` numa div é `undefined`, então o
  // texto digitado virava '' e o Enter não criava tag nenhuma. O campo voltou
  // a ser `html.Input` puro (ver `_campo_tag_widget`), mas esta função aceita
  // as DUAS formas de propósito: nunca mais depender do formato interno de um
  // componente de terceiro pra um widget que é todo nosso.
  function campoDeTexto(alvo) {
    if (!alvo || !alvo.closest) return null;
    if (alvo.tagName === 'INPUT' && alvo.closest('.tag-widget')) return alvo;
    var wrapper = alvo.closest('.tag-widget-input');
    if (!wrapper) return null;
    return wrapper.tagName === 'INPUT' ? wrapper : wrapper.querySelector('input');
  }

  // Limpar via `.value` direto não avisa o React — se o campo um dia voltar a
  // ser controlado, o texto reaparece no render seguinte. O setter nativo +
  // evento `input` é a forma suportada de mexer num input controlado por fora.
  function limparCampo(inp) {
    var descritor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    if (descritor && descritor.set) {
      descritor.set.call(inp, '');
    } else {
      inp.value = '';
    }
    inp.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function idDoStore(widget) {
    try {
      return JSON.parse(widget.dataset.storeId);
    } catch (e) {
      return null;
    }
  }

  function opcoesDoWidget(widget) {
    try {
      return JSON.parse(widget.dataset.opcoes || '[]');
    } catch (e) {
      return [];
    }
  }

  function chipsAtuais(widget) {
    return Array.from(widget.querySelectorAll('.tag-chip'));
  }

  function atualizarContador(widget) {
    var n = chipsAtuais(widget).length;
    var contador = widget.querySelector('.tag-widget-contador');
    if (contador) contador.textContent = n + ' tag(s)';
  }

  // O dcc.Store guarda só os VALUES (lista de strings) — mesmo formato que
  // o dcc.Dropdown(multi=True) antigo já produzia em `.value`, pra nenhum
  // callback Python que já lê esse campo precisar mudar.
  function sincronizarStore(widget) {
    var storeId = idDoStore(widget);
    if (!storeId || !window.dash_clientside || !window.dash_clientside.set_props) return;
    var valores = chipsAtuais(widget).map(function (chip) { return chip.dataset.valor; });
    window.dash_clientside.set_props(storeId, { data: valores });
  }

  function criarChip(valor, rotulo) {
    var chip = document.createElement('span');
    chip.className = 'tag-chip';
    chip.setAttribute('data-valor', valor);
    var texto = document.createElement('span');
    texto.textContent = rotulo;
    var remover = document.createElement('button');
    remover.type = 'button';
    remover.className = 'tag-chip-remover';
    remover.setAttribute('aria-label', 'Remover tag');
    remover.textContent = '×';
    chip.appendChild(texto);
    chip.appendChild(remover);
    return chip;
  }

  function adicionarTag(widget, textoDigitado) {
    var texto = (textoDigitado || '').trim();
    if (!texto) return;
    var caixaChips = widget.querySelector('.tag-widget-chips');
    if (!caixaChips) return;
    var textoMin = texto.toLowerCase();
    var jaExiste = chipsAtuais(widget).some(function (c) {
      return (c.getAttribute('data-valor') || '').toLowerCase() === textoMin;
    });
    if (jaExiste) return;
    // Vocabulário aberto (feedback original): se o texto bater com uma
    // opção já cadastrada (por label OU value, sem diferenciar maiúsculas),
    // usa o par oficial; senão cria uma tag livre nova (value=label=texto).
    var opcoes = opcoesDoWidget(widget);
    var oficial = opcoes.find(function (o) {
      return String(o.label).toLowerCase() === textoMin || String(o.value).toLowerCase() === textoMin;
    });
    var valor = oficial ? oficial.value : texto;
    var rotulo = oficial ? oficial.label : texto;
    caixaChips.appendChild(criarChip(valor, rotulo));
    atualizarContador(widget);
    sincronizarStore(widget);
  }

  function removerChip(chip) {
    var widget = containerDoWidget(chip);
    chip.remove();
    if (widget) {
      atualizarContador(widget);
      sincronizarStore(widget);
    }
  }

  function removerTodas(widget) {
    chipsAtuais(widget).forEach(function (c) { c.remove(); });
    atualizarContador(widget);
    sincronizarStore(widget);
  }

  document.addEventListener('keydown', function (e) {
    var input = campoDeTexto(e.target);
    if (!input) return;
    if (e.key === 'Enter' || e.key === ',' || e.key === ';') {
      e.preventDefault();
      var widget = containerDoWidget(input);
      if (widget) adicionarTag(widget, input.value);
      limparCampo(input);
    } else if (e.key === 'Backspace' && input.value === '') {
      // Backspace num campo vazio remove a ÚLTIMA tag — atalho comum desse
      // tipo de widget (ver print de referência do feedback original).
      var widgetBS = containerDoWidget(input);
      if (widgetBS) {
        var chips = chipsAtuais(widgetBS);
        if (chips.length) removerChip(chips[chips.length - 1]);
      }
    }
  });

  document.addEventListener('click', function (e) {
    var botaoRemover = e.target.closest ? e.target.closest('.tag-chip-remover') : null;
    if (botaoRemover) {
      var chip = botaoRemover.closest('.tag-chip');
      if (chip) removerChip(chip);
      return;
    }
    var botaoLimpar = e.target.closest ? e.target.closest('.tag-widget-limpar') : null;
    if (botaoLimpar) {
      var widget = containerDoWidget(botaoLimpar);
      if (widget) removerTodas(widget);
    }
  });
})();
