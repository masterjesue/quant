# Changelog — UX Super Quant '98

Registro cronológico das decisões e correções de UX do front-end Windows 98
(`dashboard_98.py` + `estilo_win98.py` + `assets_98/`). Complementa o
`CHANGELOG.md` principal (que cobre o pipeline de dados) — este arquivo é
só sobre a camada visual. Não é um log de commits — é o "porquê" de cada
decisão, pra consulta futura. Ver `MAPA_ESTRUTURA_SQ98.md` para a
arquitetura resultante.

---

## Pendências conhecidas (deixadas para depois, de propósito)

- **Redimensionar janela** — só existe mover (drag pela titlebar). Sem
  handle de resize ainda.
- **Placeholders Backtesting/Diário/Configurações** — janelas ainda não
  construídas em `app.layout` (ícones/entradas em `JANELAS` já existem).
- **Commit** — todo o trabalho abaixo (a partir da Fase 1) ainda não foi
  commitado no git.

---

## 2026-07-07 — Fundação e decisão de arquitetura

### Decisão — reskin autêntico, não repintura de cor/fonte
Pedido original: recriar o visual do sistema em Windows 98 genuíno —
"uma obra de arte dos anos 90", com bevel 3D, paleta correta, fontes
corretas — não uma simples troca de cores em cima do layout existente.

### Fase 0/1 — backup e fundação do tema
**Commits:** `d9e9d72` (estado inicial), `2aa5949` (Fase 1).

`estilo_win98.py` criado: paleta "Windows Standard" + semânticos de mercado
em VGA 16 cores, técnica de bevel 3D via `box-shadow` em 4 camadas
(conferida contra `98.css`, github.com/jdan/98.css, MIT), fonte MS Sans
Serif bitmap self-hosted.

### Decisão — metáfora de "Desktop completo" (não reskin estático)
Protótipo (Artifact HTML autocontido, fora do projeto) demonstrando janelas
arrastáveis, taskbar com Start + relógio, ícones de desktop. Aprovado com
entusiasmo — decisão de ir com essa versão mais ambiciosa em vez de um
reskin sem interatividade de janela. Dois ajustes pedidos desde o início
(não como polimento posterior): o botão de minimizar do protótipo não
funcionava, e o relógio precisava mostrar segundos (importa pra day trade).

### Decisão — dois front-ends, não um substituindo o outro
Pedido em meio à implementação: manter `dashboard.py` (clássico, dark mode)
ativo e atualizado, junto do novo `dashboard_98.py` — pensando em
compartilhar o sistema no futuro com quem não se adapta ao Windows 98.
`metricas.py` extraído nesta hora especificamente pra evitar duplicação de
lógica entre os dois.

### Implementação — casca de janela + mecânica de desktop
`assets_98/window_manager.js` (novo): drag, foco/z-index, minimizar/
maximizar/fechar, taskbar, menu Start, relógio, persistência via
`localStorage`. Os 2 bugs do protótipo corrigidos desde a primeira versão
real (não depois): taskbar confundia "minimizado" com "fechado" na
visibilidade do botão; relógio faltava segundos e atualizava a cada 10s em
vez de 1s.

4 janelas de trading (`win-hero`, `win-market-view`, `win-price-action`,
`win-big-players`) + chat migrado de FAB/modal pra janela de verdade
(`win-chat`).

### Correção — reskin interno incompleto
**Reportado:** protótipo não foi reproduzido fielmente — visual "ficou bem
diferente", achatado, sem profundidade. Causa: fases internas (cards,
botões, gauges) ficaram temporariamente com estilo antigo enquanto a
mecânica de janela era construída. Card/botão/gauge convertidos pra
`win98_groupbox`/`win98_button`/bevel de verdade nesta rodada.

### Correção — bug real de minimizar
**Causa raiz:** `win98_window()` define `display: flex` inline (necessário
pro layout titlebar+corpo+statusbar) — estilo inline sempre vence sobre
regra de classe, então `.win98-window.minimized { display: none }` no CSS
nunca tinha efeito nenhum. Corrigido com `!important` na regra.

### Implementação — Clippy real como Mentor IA (1ª versão)
Pedido explícito, com o github.com/felixrieseberg/clippy como referência
de qualidade. `agent.json` (43 animações) + `map.png` (sprite sheet)
extraídos do pacote npm `clippyjs@0.1.0` (MIT). Motor de animação
(`assets_98/clippy_agent.js`) vivendo dentro da janela `win-chat`.

### Implementação — ícones reais + Start button
Ícones do Windows 98 extraídos de github.com/1j01/98 (sem LICENSE
detectado no repo — flag de atenção antes de compartilhar publicamente),
substituindo todos os emojis placeholder (ícones de desktop, itens de
Start, ícone de titlebar, logo do botão Start). `win98_groupbox` corrigido
pra renderizar o LED mesmo sem `titulo` (usado pelos cards de S/R sem
título próprio).

---

## 2026-07-08 — Fidelidade visual, Clippy de verdade, correções de contraste

### Bug crítico — chat parava de responder silenciosamente
**Causa raiz:** o decorator `@app.callback` estava colado na função errada
(`_linha_chat`, um helper de formatação de 3 parâmetros) em vez de
`enviar_mensagem_chat` (a função real, 5 parâmetros) — Dash tentava chamar
`_linha_chat` com os argumentos errados a cada clique em "Enviar",
quebrando em runtime. `enviar_mensagem_chat` nunca era registrada como
callback. Corrigido; validado chamando o endpoint `_dash-update-component`
diretamente e confirmando resposta real da IA.

### Correção — bevel das janelas invisível ("flat, sem profundidade")
**Causa raiz:** `win98_window()` usava `padding: 0` no frame externo — o
bevel 3D é desenhado via `box-shadow` *dentro* da caixa, mas com padding
zero a titlebar/corpo (que cobrem 100% da área) pintavam por cima da
sombra. Ela existia no CSS mas ficava fisicamente encoberta. Corrigido com
`padding: 3px` (valor do `.window` real do 98.css) + o token de bevel
correto pra frame de janela (`_BEVEL_RAISED_WINDOW`, diferente do bevel de
botão — ver `MAPA_ESTRUTURA_SQ98.md`).

### Correção — botões de titlebar não batiam com a referência
Glifos de texto (`_`, `□`, `×`) substituídos pelos SVGs reais embutidos,
copiados literalmente do CSS compilado do 98.css (mesmos ícones de
Minimize/Maximize/Close usados pela referência do Jesué).

### Correção — scrollbar fora do padrão
Versão simplificada (só bevel, sem setas) substituída pela real do 98.css:
setas de rolagem (SVG bitmap) + padrão xadrez 2x2 do track.

### Correção — taskbar (3 bugs num só relato)
- Botão de janela minimizada sem relevo 3D: `.win98-task-btn` só ganhava
  `box-shadow` quando `.active` — todo botão não-focado ficava
  completamente chapado. Corrigido: bevel raised por padrão, sunken só
  quando em foco.
- Ícone "duplicado" no botão da taskbar: extração de ícone endurecida pra
  pegar só a `<img>` (não o `outerHTML` do wrapper inteiro), e leitura do
  título restrita ao `<span>` de texto (evita herdar texto de um emoji
  fallback dentro do mesmo wrapper).
- Fundo do relógio branco em vez de cinza: `win98_sunken()` não recebia
  `bg` explícito no tray e caía no branco padrão. Corrigido com
  `bg=C['panel']`.
- Padding geral da taskbar/botão Start reduzido (30px→28px de altura).

### Implementação — Clippy reformulado como personagem flutuante + balão fiel
Migrado de "janela" (`win-chat`) pra assistente sempre ancorado no canto:
**arrastável de verdade** (mousedown+drag move; clique simples sem
arrastar abre/fecha o balão). Balão de fala reescrito no estilo do
`clippy.js` original (github.com/clippyjs/clippy.js, MIT — a mesma
linhagem que o projeto de referência do Jesué usa): fundo creme, borda
preta, ponta triangular, reposicionamento automático em 4 cantos.

### Bug — Clippy sem animação nenhuma
**Causa raiz:** alguns frames do `agent.json` (ex. o 1º de "Show") só têm
`duration`, sem `images` — são frames de espera. O código assumia
`frame.images[0]` sempre presente e quebrava com `TypeError` no primeiro
frame da primeira animação tocada, travando toda a inicialização
subsequente (inclusive `wireReactions()` e o easter egg, que vinham depois
no mesmo `.then()` e nunca chegavam a rodar). Corrigido: frames sem
`images` só mantêm o sprite parado pelo `duration`, sem tentar redesenhar.

### Implementação — easter egg de animações
Botão discreto na bandeja da taskbar. **1ª versão** disparava as 43
animações em cadeia automática num só clique — relatado como "parece
travado em loop". **Corrigido**: cada clique avança uma animação por vez
(índice incrementando), sem sequência automática. Ícone do botão trocado
de 📎 (irreconhecível em 18px) pra ▶ (nítido em qualquer tamanho pequeno).

### Correção — contraste de cor ilegível (2 bugs)
- Badges de Price Action (`REGIÃO FORTE`, `ZONA ESPELHO`, etc.) usavam
  `C['bg']` (teal `#008080` — mistura de azul claro com verde) como cor de
  *texto* sobre fundos navy/verde/amarelo — relatado como "azul claro com
  verde, quase ilegível". `texto_sobre()` (helper já existente, calcula
  preto/branco por luminância) já existia no código mas não estava sendo
  usado nesses badges especificamente. Aplicado nos 5 badges afetados.
- Amarelo puro (`#FFFF00`) usado como cor de *texto* (velocímetro neutro,
  valores de card, mensagem de rate-limit do chat) sobre fundo branco/cinza
  claro — quase invisível (relatado como "amarelo claro com branco").
  Criado o token `amber` (`#946F00`, mesma família de cor, contraste real)
  pra todo uso de amarelo-como-texto; `yellow` puro ficou reservado só pra
  forma (arco do velocímetro, fundo de badge).

### Correção — janelas não cabiam lado a lado
Duas causas, não uma:
1. Largura das janelas ainda grande demais pra o pedido do Jesué — Quant
   Lab reduzida de 960px→640px→520px (fundida com Market View no processo,
   ver abaixo); Price Action de 620px→560px.
2. **Causa mais sutil**: o `localStorage` já tinha posição salva de
   sessões de teste anteriores, e `window_manager.js` sempre prioriza
   posição salva sobre o default do Python — mudar o percentual/largura no
   código não tinha efeito nenhum pra quem já tinha aberto/movido aquela
   janela antes. Corrigido bumpando a versão da chave de armazenamento
   (`sq98:layout:v1`→`v2`), invalidando tudo que estava salvo.

### Implementação — Quant Lab + Market View fundidas
Pedido: a janela "Quant Lab" sobrava espaço vazio sozinha. Fundida com
"Market View" numa única janela (`win-quantlab`) — velocímetros/seletor de
ativo no topo, cards do Market View logo abaixo. `JANELAS` atualizado
(entrada `win-market-view` removida, `win-hero` renomeada pra
`win-quantlab`).

### Correção — fonte pixelada demais
**Pedido:** a fonte usada era do gosto do Jesué no estilo, mas "um pouco
mais suave, menos pixelada" — referenciando o visual mais liso da
tela de apresentação do felixrieseberg/clippy. Fonte padrão trocada de "MS
Sans Serif" bitmap pra **Tahoma** (`FONTS['display']`/`FONTS['body']` em
`estilo_win98.py`) — period-correct (o próprio Windows 98/2000 já usava
Tahoma em diálogos mais novos) e muito mais legível em telas de hoje. Regra
de CSS que forçava `-webkit-font-smoothing: none` (pensada especificamente
pra bitmap) removida.
