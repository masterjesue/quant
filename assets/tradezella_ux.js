// Micro-interações do redesenho TradeZella (item F, Fase 2.6,
// `spec_redesenho_tradezella.md`) — count-up dos números grandes dos KPIs
// (Net P&L, Anticovardia Score etc, marcados com a classe `tz-numero-
// animado`). Nenhuma lib pronta (nem o DMC) resolve "contar até um número
// livre" sozinha — por isso um script pequeno e independente, mesmo padrão
// já usado neste projeto pra `tema.js`/`diario_zoom.js`.
//
// Estratégia: extrai o PRIMEIRO número (com sinal/decimais) do texto do
// elemento via regex, anima de 0 até ele mantendo o resto do texto (ex:
// "R$ " antes, "%" depois) intacto, sem precisar duplicar formatação
// Python em JS.
(function () {
  const DURACAO_MS = 600;
  const REGEX_NUMERO = /-?\d[\d.,]*\d|\d/;

  function animarElemento(el) {
    const textoOriginal = el.textContent;
    const match = textoOriginal.match(REGEX_NUMERO);
    if (!match) return;

    const bruto = match[0];
    const alvo = parseFloat(bruto.replace(/\./g, '').replace(',', '.')) || parseFloat(bruto);
    if (!isFinite(alvo)) return;
    const casas = (bruto.split(/[.,]/)[1] || '').length;
    const prefixo = textoOriginal.slice(0, match.index);
    const sufixo = textoOriginal.slice(match.index + bruto.length);

    const inicio = performance.now();
    function passo(agora) {
      const t = Math.min((agora - inicio) / DURACAO_MS, 1);
      const suave = 1 - Math.pow(1 - t, 3); // ease-out cúbico
      const valorAtual = alvo * suave;
      el.textContent = prefixo + valorAtual.toFixed(casas) + sufixo;
      if (t < 1) requestAnimationFrame(passo);
      else el.textContent = textoOriginal; // garante o texto exato no final
    }
    requestAnimationFrame(passo);
  }

  function varrer(raiz) {
    raiz.querySelectorAll('.tz-numero-animado:not([data-tz-animado])').forEach((el) => {
      el.setAttribute('data-tz-animado', '1');
      animarElemento(el);
    });
  }

  const observador = new MutationObserver((mutacoes) => {
    for (const m of mutacoes) {
      if (m.addedNodes.length) varrer(document.body);
    }
  });
  observador.observe(document.body, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', () => varrer(document.body));
})();
