# Mapa de Estrutura — Super Quant '98 (SQ'98)

Referência de arquitetura do front-end Windows 98 do Super Quant. Complementa
`MAPA_ESTRUTURA.md` (que cobre o pipeline de dados e o dashboard clássico) —
consultar os dois: este arquivo cobre só a camada visual nova, que vive em
`dashboard_98.py` + `estilo_win98.py` + `assets_98/`.

Não é auto-gerado — mantido manualmente. Atualizar quando um módulo/janela
novo entrar, ou quando a mecânica de desktop mudar.

---

## Por que existem dois front-ends

Jesué decidiu manter os dois dashboards ativos, não substituir um pelo
outro:

- **`dashboard.py`** (porta 8050) — o dashboard clássico "cockpit financeiro"
  em dark mode. Continua sendo o candidato natural pra compartilhar com
  outras pessoas no futuro, já que nem todo mundo se adapta ao Windows 98.
- **`dashboard_98.py`** (porta 8051) — **Super Quant '98**, o favorito do
  Jesué. Metáfora de desktop completa: janelas de verdade (arrastar, focar,
  minimizar, maximizar, fechar), taskbar, menu Start, Clippy como Mentor IA.

Os dois **compartilham toda a lógica de negócio** (carga de dados, cálculo
de features, IA, calendário econômico) via módulos comuns — nenhum dos dois
duplica lógica de dados, só a camada de construção de UI é própria de cada
um. `metricas.py` foi extraído nesta sessão especificamente pra isso (antes
vivia duplicado dentro de `dashboard.py`).

```
                         ┌─────────────────────┐
                         │   metricas.py        │
                         │   persistencia.py     │  ← lógica compartilhada,
                         │   mt5_loader.py        │    sem nenhuma linha de
                         │   features.py          │    UI
                         │   config_backtest.py    │
                         │   ai_analyst.py          │
                         │   calendario_economico.py│
                         └──────────┬───────────┬──┘
                                    │           │
                    ┌───────────────┘           └───────────────┐
                    ▼                                           ▼
          dashboard.py (porta 8050)                 dashboard_98.py (porta 8051)
          assets/ (CSS próprio, mínimo)              estilo_win98.py + assets_98/
          "cockpit financeiro" dark mode             Super Quant '98 (Windows 98)
```

**Isolamento de assets**: cada front-end tem sua própria pasta de estáticos
(`assets/` vs. `assets_98/`, via `dash.Dash(..., assets_folder=...)`) — CSS/JS
de um nunca vaza pro outro. Detalhe não óbvio de infraestrutura Dash: a URL
pública de qualquer asset é **sempre** `/assets/<arquivo>`, mesmo quando
`assets_folder='assets_98'` — é o disco que muda, não a rota HTTP.

---

## Como rodar

```
python dashboard.py       # clássico  → http://127.0.0.1:8050
python dashboard_98.py    # SQ'98     → http://127.0.0.1:8051
```

Os dois podem rodar ao mesmo tempo (portas diferentes). Cada um dispara sua
própria atualização incremental de dados ao abrir — **evitar rodar os dois
simultaneamente fazendo setup/atualização pesada ao mesmo tempo** (mesmo
risco de concorrência em disco documentado no `CHANGELOG.md` principal).

---

## Arquivos e responsabilidades

| Arquivo | Papel |
|---|---|
| `dashboard_98.py` | Ponto de entrada do SQ'98. Layout, callbacks, e os `construir_*` que viram HTML a partir das métricas — mesma responsabilidade que o `dashboard.py` clássico, só que gerando markup no estilo Win98. |
| `estilo_win98.py` | Sistema de design inteiro: paleta `C`, fontes `FONTS`, técnica de bevel 3D, e todo helper de "chrome" (`win98_window`, `win98_titlebar`, `win98_button`, `win98_groupbox`, ícones de desktop/Start, taskbar). Só é importado por `dashboard_98.py`. |
| `metricas.py` | `extrair_metricas(df)` — extraído do `dashboard.py` original nesta sessão pra ser compartilhado pelos dois front-ends sem duplicação. |
| `assets_98/window_manager.js` | Mecânica de janela 100% client-side: drag, foco/z-index, minimizar/maximizar/fechar, taskbar, menu Start, relógio, persistência via `localStorage`. Não depende de nenhuma prop do Dash. |
| `assets_98/clippy_agent.js` | Motor de animação do Clippy (Mentor IA): sprite arrastável, balão de fala reposicionável em 4 cantos, reações ao chat, easter egg de animações. |
| `assets_98/style.css` | O que não dá pra fazer em `style=` inline do Python: `:active`/`:hover`/`:focus-visible`, `@font-face`, scrollbar, `@keyframes`, e as classes de estado (`.minimized`, `.closed`, `.maximized`, `.inactive`). |
| `assets_98/clippy/` | `map.png` (sprite sheet) + `agent.json` (43 animações) — dados reais do `clippyjs` (MIT). |
| `assets_98/icons/` | Ícones do Windows 98 (`.png`) e o frame do Clippy usado como ícone de janela — ver seção de proveniência abaixo. |
| `assets_98/fonts/` | `ms_sans_serif(.woff2|_bold.woff2)` — a fonte bitmap original. Hoje **não é mais a fonte padrão** (ver seção Tipografia), mas fica disponível via `@font-face` caso algum elemento específico queira o efeito pixelado de propósito no futuro. |

---

## Sistema de design (`estilo_win98.py`)

### Paleta (`C`)

Paleta "Windows Standard" (a mais reconhecível) + semânticos de mercado em
VGA 16 cores. Chaves principais: `desktop`/`bg` (teal `#008080`), `panel`
(cinza `#C0C0C0`), `window` (branco), `shadow`/`shadow_dk`/`light`/`hilight`
(as 4 cores do bevel), `titlebar`/`titlebar_to` (gradiente navy→azul),
`green`/`red`/`yellow`/`amber`/`blue`/`gold`.

**`yellow` vs. `amber`** (decisão de 2026-07-08, ver changelog): `yellow`
(`#FFFF00`) é só para **forma** — arco do velocímetro, fundo de badge.
Como **texto** em cima de branco/cinza claro ele é quase ilegível
(luminância ~226, quase igual ao branco). `amber` (`#946F00`) é a mesma
família de cor, mas com contraste de verdade — usar em qualquer lugar onde
amarelo é cor de *texto* (valor de card, LED com rótulo ao lado, etc.).

### Bevel 3D — a técnica central

Todo elemento "raised" (botão, painel solto) ou "sunken" (campo, área de
gráfico) usa 4 camadas de `box-shadow` inset, **conferidas literalmente
contra o CSS compilado do 98.css** (github.com/jdan/98.css, MIT):

```python
_BEVEL_RAISED = (          # botões, groupbox
    f"inset -1px -1px {shadow_dk}, inset 1px 1px {hilight}, "
    f"inset -2px -2px {shadow}, inset 2px 2px {light}"
)
_BEVEL_RAISED_WINDOW = (   # moldura externa da janela — camadas diferentes!
    f"inset -1px -1px {shadow_dk}, inset 1px 1px {light}, "
    f"inset -2px -2px {shadow}, inset 2px 2px {hilight}"
)
_BEVEL_SUNKEN = (          # inputs, área de gráfico
    f"inset -1px -1px {hilight}, inset 1px 1px {shadow}, "
    f"inset -2px -2px {light}, inset 2px 2px {shadow_dk}"
)
```

**Armadilha real que já mordeu esse código duas vezes:** bevel de botão e
bevel de janela **não são o mesmo token** no 98.css — a janela troca as
camadas de 1px/2px claras entre si. Copiar um pelo outro faz a moldura
parecer "flat". E o bevel só aparece se o elemento tiver `padding` — sem
padding, o filho (titlebar/corpo, que cobre 100% da área) pinta por cima da
sombra e ela some visualmente mesmo estando correta no CSS (bug real,
corrigido em 2026-07-08: `win98_window` tinha `padding: 0`).

### Helpers principais

- `win98_raised(bg, padding, extra)` / `win98_sunken(...)` — retornam dict
  de `style` pronto pra spread (`**win98_raised(...)`).
- `win98_button(pressed, extra)` — raised por padrão, sunken quando
  `pressed=True` (seletor de ativo ativo, por exemplo).
- `win98_field(extra)` — campo de texto sunken branco, 21px de altura.
- `win98_titlebar(texto, icone, minimizavel, maximizavel, fechavel)` —
  botões `_`/`□`/`×` são SVGs reais embutidos (data URI, copiados do
  98.css), não glifos de texto.
- `win98_window(window_id, titulo, children, icone, width, min_width,
  default_top_pct, default_left_pct, start_closed, menubar, statusbar,
  estilo_extra)` — a "casca" de uma janela inteira. `window_id` é ao mesmo
  tempo o `id` do DOM e a chave que `window_manager.js` usa pra
  taskbar/`localStorage` — o JS nunca tem id de janela hardcoded.
- `win98_groupbox(titulo, children, cor_led, estilo_extra)` — caixa de
  grupo com rótulo "entalhado" no topo (efeito fieldset/legend) e LED
  colorido opcional (renderiza mesmo sem `titulo`, ver changelog).
- `win98_desktop_icon` / `win98_start_menu_item` / `win98_taskbar` —
  genéricos por `data-open-window` — nunca hardcoded pra uma janela
  específica.
- `_icone_visual(icone, tamanho)` — `html.Img` se `icone` for um caminho
  (`/assets/icons/*.png`), `html.Span` (emoji) como fallback.
- `texto_sobre(cor_fundo)` — preto ou branco por luminância, pra qualquer
  badge/botão colorido nunca depender de uma cor de texto fixa.

### `JANELAS` — fonte única de verdade

```python
JANELAS = [
    {'id': 'win-quantlab',     'titulo': 'Quant Lab', ...},
    {'id': 'win-price-action', 'titulo': 'Price Action', ...},
    {'id': 'win-big-players',  'titulo': 'Big Players', ...},
    {'id': 'win-backtesting',  'titulo': 'Backtesting', ...},   # placeholder, ainda sem janela em app.layout
    {'id': 'win-diario',       'titulo': 'Diário', ...},         # placeholder
    {'id': 'win-config',       'titulo': 'Configurações', ...},  # placeholder
]
```

Usada pros ícones de desktop **e** pros itens do menu Start — nunca duas
listas separadas, pra nunca haver drift entre "o que existe" e "o que
aparece". Hoje só `JANELAS[:3]` (as 3 janelas de trading já implementadas)
aparecem nos dois lugares — os 3 placeholders existem na lista mas ainda
não têm janela correspondente em `app.layout` (fase futura, ver seção
"O que falta").

O Mentor IA (Clippy) **não está nessa lista** — deixou de ser uma "janela"
(sem titlebar/taskbar/minimizar) e virou o assistente flutuante sempre
ancorado no canto, com seu próprio balão (ver seção Clippy).

---

## Metáfora de desktop (`assets_98/window_manager.js`)

Script estático, carregado uma única vez, gerencia **toda** a mecânica de
janela sem nenhum `clientside_callback` — não existe prop do Dash que mude
quando alguém arrasta uma titlebar, então isso vive fora do ciclo
Dash/React.

**Invariante importante**: depois do mount, `top`/`left`/`width`/`height`/
`zIndex` inline de `.win98-window` são propriedade exclusiva deste script.
Nenhum callback Python deve escrever essas mesmas chaves via
`Output(<id>, 'style')` — colidiria com o drag.

**Race condition tratada**: um `<script>` em `assets/` roda quando o
browser processa a tag, sem garantia de que o primeiro render do React/Dash
já aconteceu. Em vez de `DOMContentLoaded` (que pode rodar cedo demais e
achar o DOM vazio, silenciosamente), o script espera via `MutationObserver`
a primeira `.win98-window` aparecer, com timeout de 8s que loga aviso no
console.

**Persistência**: posição/tamanho/z-index/minimizado/fechado de cada janela
salvam em `localStorage` (`STORAGE_KEY = 'sq98:layout:v2'`), debounced
(250ms). **A versão no nome da chave existe por um motivo concreto**: mudar
o default de posição/tamanho no Python não tem efeito nenhum pra quem já
tem uma entrada salva — o `localStorage` antigo é restaurado e mascara a
mudança por completo. Bump a versão (`v2`→`v3`...) sempre que os defaults
mudarem de um jeito que valha a pena forçar todo mundo a ver.

**O que ainda não existe**: redimensionar janela arrastando a borda (só
mover pela titlebar). O tamanho de cada janela é fixo (`width`/`min_width`
do Python) até isso ser implementado.

---

## Clippy / Mentor IA (`assets_98/clippy_agent.js`)

Reformulado em 2026-07-08 depois do pedido explícito do Jesué de trazer o
Clippy de verdade (referência: github.com/felixrieseberg/clippy). Duas
decisões centrais:

1. **Não é uma janela.** É um personagem sempre ancorado no canto,
   clicável e **arrastável** (mousedown+drag move; clique simples sem
   arrastar abre/fecha o balão). Vive fora de `JANELAS`.
2. **O balão de fala é fiel ao clippy.js original**
   (github.com/clippyjs/clippy.js, MIT — mesma linhagem que o projeto de
   referência do Jesué usa): fundo creme (`#FFFFCC`), borda preta, ponta
   triangular, reposicionamento automático em 4 cantos
   (`top-left`/`top-right`/`bottom-right`/`bottom-left`) portado do
   `balloon.js` original, tentando cada um até achar um que caiba na
   viewport. O conteúdo do balão (histórico/input/scrollbar) é o "ajuste
   inevitável" pra virar chat de verdade — o Jesué pediu fidelidade
   *exceto* nisso.

**Dados de animação**: `assets_98/clippy/agent.json` (43 animações) +
`map.png` (sprite sheet), extraídos do pacote npm `clippyjs@0.1.0` (dados
puros, sem runtime). **Detalhe não óbvio do formato**: alguns frames só têm
`duration` (sem `images`) — são frames de "espera" que mantêm o sprite
parado. Um `play()` ingênuo que assume `frame.images[0]` sempre existe
quebra com `TypeError` no primeiro frame de "Show" e trava tudo — inclusive
o resto da inicialização que vem depois no mesmo `.then()` (reações do chat
e o easter egg nunca chegavam a ser conectados). Corrigido em 2026-07-08.

**Ciclo de vida**:
- `Show` → `Greeting` ao carregar a página.
- Animação idle aleatória (`Idle1_1`, `IdleSideToSide`, etc.) em loop com
  intervalo randômico (6-14s).
- `Thinking` ao clicar Enviar / apertar Enter no chat.
- `GetAttention` quando uma mensagem nova aparece no histórico
  (`MutationObserver` em `#chat-historico`) — reage mesmo com o balão
  fechado.

**Easter egg**: botão `▶` na bandeja da taskbar, ao lado do relógio
(`#clippy-play-all-btn`). Cada clique avança **uma** animação do catálogo
(`demoIndex` incrementando, não uma cadeia automática — a primeira versão
disparava as 43 em sequência e parecia travada em loop).

---

## Tipografia

Decisão de 2026-07-08: a fonte padrão é **Tahoma** (`FONTS['display']` e
`FONTS['body']` em `estilo_win98.py`), não a "MS Sans Serif" bitmap. A
bitmap pixelada é period-correct mas fica quase ilegível em telas de hoje;
Tahoma é a fonte que o próprio Windows 98/2000 passou a usar em diálogos
mais novos, e é a que aparece na referência do Jesué
(felixrieseberg/clippy). A regra de CSS que forçava `-webkit-font-
smoothing: none` (desligava o antialiasing, pensada especificamente pra
bitmap) foi removida. Os arquivos `.woff2` da bitmap continuam em
`assets_98/fonts/` via `@font-face`, disponíveis caso algum elemento
específico queira o efeito pixelado de propósito no futuro — hoje nada usa
mais `'Pixelated MS Sans Serif'` como fonte primária.

---

## Callbacks de `dashboard_98.py`

| Callback | Dispara em | Faz |
|---|---|---|
| `selecionar_ativo` | clique em WIN/WDO/WIN×WDO | Atualiza `store-ativo`. |
| `atualizar_estilo_ativo_selecionado` | `store-ativo` | Só troca o `style` (pressed/raised) dos 3 botões — nunca recria os `html.Button`, preserva `n_clicks` no browser (bug histórico documentado no `CHANGELOG.md` principal, 2026-07-04). |
| `atualizar_candle_atual` | `interval-live` (1s) | Busca preço/candle atual do MT5. |
| `atualizar_incremental_background` | `interval-slow` (30s) | Dispara atualização incremental em thread separada. |
| `atualizar_dashboard` | `interval-live`, `store-ativo`, `store-candle-atual` | Callback principal — reconstrói `hero-conteudo`, `market-view-conteudo` (ambos dentro da janela "Quant Lab" fundida), `price-action-conteudo`, `big-players-conteudo`, e os 3 campos da statusbar + badge de calendário econômico. |
| `enviar_mensagem_chat` | clique em Enviar / Enter no input | Chama `ai_analyst.py`, adiciona linha ao `chat-historico`. **Atenção histórica**: em algum ponto desta sessão o decorator `@app.callback` ficou colado na função errada (`_linha_chat`, um helper de formatação) em vez desta — o chat parava de responder silenciosamente. Corrigido em 2026-07-08; se `enviar_mensagem_chat` algum dia parecer "morto", checar isso primeiro. |

**Clientside callbacks** (JS puro, via `app.clientside_callback`): scroll
automático do `chat-historico`; pulso visual (`trocando-ativo`) nas 4
divs de conteúdo (`hero-conteudo`, `market-view-conteudo`,
`price-action-conteudo`, `big-players-conteudo`) ao trocar de ativo.

---

## Proveniência / licenças dos assets visuais

| Fonte | Licença | Uso |
|---|---|---|
| `98.css` (github.com/jdan/jdan/98.css) | MIT | Referência exata pros valores de bevel, botões de titlebar (SVG), scrollbar. Não é vendorizado como arquivo — os valores foram extraídos e adaptados pros helpers Python/CSS deste projeto. |
| `clippyjs` (npm `clippyjs@0.1.0`, github.com/pi0/clippyjs) | MIT | `agent.json` + `map.png` (dados de animação do Clippy). |
| `clippy.js` original (github.com/clippyjs/clippy.js) | MIT | Referência pra estrutura/CSS do balão de fala (`.clippy-balloon`, `.clippy-tip`, algoritmo de reposicionamento em 4 cantos). |
| Ícones do Windows 98 (github.com/1j01/98) | **Sem LICENSE detectado no repo** — recriação de assets originais da Microsoft de 1998, uso comum em projetos de preservação/nostalgia (o próprio projeto de referência do Jesué usa a mesma linhagem de assets). Ok pra uso pessoal; **revisar antes de compartilhar publicamente** — alternativa com licença explícita: win98icons.alexmeub.com. |

---

## O que falta / débito técnico conhecido

- **Redimensionar janela** — só existe mover (drag pela titlebar). Sem
  handle de resize ainda.
- **Placeholders Backtesting/Diário/Configurações** — já têm entrada em
  `JANELAS` e ícone extraído (`calculator.png`, `notepad.png`,
  `settings.png`), mas ainda não têm `win98_window(...)` correspondente em
  `app.layout`, nem aparecem em `JANELAS[:3]` (ícones/Start). Fase futura.
- **`secao()` e `grafico_candles()`** em `dashboard_98.py` — definidas mas
  não chamadas em lugar nenhum (o título de cada seção passou a vir da
  titlebar da própria janela, e o gráfico de candle não é mostrado nesta
  interface). Candidatas a remoção se confirmado que não vão ser usadas.
- **`assets_98/icons/network.png`** — foi baixado pro extinto ícone de
  "Market View" (janela fundida com Quant Lab em 2026-07-08). Arquivo
  órfão, inofensivo, mas pode ser removido.
- **`git`**: o repositório tem só 2 commits (`d9e9d72` estado inicial,
  `2aa5949` Fase 1). Tudo documentado aqui — dual front-end, metáfora de
  desktop completa, Clippy, todos os fixes de fidelidade — ainda está
  **não commitado** no momento em que este arquivo foi escrito.

## Como estender (próximos módulos)

Pra adicionar uma janela nova ao SQ'98:

1. Adicionar uma entrada em `JANELAS` (`estilo_win98.py`) com `id`, `titulo`,
   `icone` (caminho em `/assets/icons/...` ou emoji fallback),
   `start_closed`.
2. Incluir essa entrada na fatia usada pelos ícones de desktop/Start em
   `dashboard_98.py` (hoje `JANELAS[:3]`).
3. Adicionar um `win98_window('id-da-janela', 'Título', ...)` em
   `app.layout`, com `children` apontando pro seu conteúdo (estático ou
   `html.Div(id='...-conteudo')` se for dinâmico via callback).
4. Se o conteúdo for dinâmico, adicionar o `Output` correspondente no
   callback principal (`atualizar_dashboard`) ou criar um callback
   dedicado — não precisa mexer em `window_manager.js` nem em
   `estilo_win98.py` além do passo 1: a mecânica de janela já é genérica.
