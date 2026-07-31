# Redesenho TradeZella (item F) — spec de execução

**Status:** escopo levantado e decisões confirmadas (2026-07-30, ver seção
"Decisões do Jesué") — em execução, começando pela Fase 1. Escrito a
partir de `upgrade diario.docx` (3 screenshots de referência extraídos e
revisados) + `analise-tradezella-para-super-quant.md` (análise anterior já
existente no repo) + inventário do que já existe em
`diario_trade.py`/`diario_app_classico.py`.

## Objetivo

Redesenho completo da aba Estatísticas do Diário (skin clássico — '98 está
descontinuado, ver `plano_upgrade_diario.md`), clonando os MESMOS elementos
gráficos e funcionalidades do dashboard do TradeZella, só trocando
nomenclatura onde a marca aparece (**Zella Score → Anticovardia Score**).
Este é, na prática, o destino final dos achados do item E (auditoria de
UX) — nenhum gráfico solto nasce fora daqui.

Nenhuma lib nova é necessária: o projeto já usa `plotly.graph_objects`, que
cobre todo tipo de gráfico visto nas referências (radar via
`go.Scatterpolar`, área/linha via `go.Scatter` com `fill`, scatter, barras
divergentes). O trabalho é 100% de composição de dado + layout, não de
tooling novo.

## Inventário elemento-a-elemento (screenshots → Super Quant)

| # | Elemento TradeZella | Já existe? | Fonte do dado hoje | Gap |
|---|---|---|---|---|
| 1 | Net P&L (card + mini gráfico) | Sim | `calcular_estatisticas()['saldo_total']` | só falta o mini-visual (sparkline) |
| 2 | Trade Win % (card + gauge donut) | Sim | `['taxa_acerto']` | só falta o gauge (Plotly `indicator` gauge) |
| 3 | Avg Win/Loss (card + barra verde/vermelha) | Parcial | payoff existe; "avg win"/"avg loss" em R$ não estão separados hoje (só ganho_total/perda_total agregados) | precisa `ganho_total/n_win` e `perda_total/n_loss` — trivial, 2 linhas |
| 4 | Profit Factor (card + anel) | Quase | `ganho_total`/`perda_total` já existem, profit_factor GERAL não é uma chave própria hoje (só existe por-estratégia em `calcular_score_confiabilidade`) | expor `profit_factor` geral em `calcular_estatisticas` |
| 5 | Current Streak (dias/trades, card) | **Não** | `maior_sequencia_win/loss` são MÁXIMOS históricos, não a sequência ATUAL (contando de trás pra frente a partir do trade mais recente) | função nova: `sequencia_atual(df)` — conta win/loss consecutivos a partir do fim |
| 6 | Daily net cumulative P&L (área) | Sim | já é "Evolução de Saldo" (`evolucao_data`/`evolucao_saldo_acumulado`) | só restilizar como área com gradiente (hoje é linha simples) |
| 7 | **Anticovardia Score** (radar hexagonal + barra gradiente 0-100) | **Não** (só existe versão por-estratégia, `calcular_score_confiabilidade`) | 6 eixos do TradeZella: Win%, Profit Factor, Avg Win/Loss, Recovery Factor, Max Drawdown, Consistency | precisa: (a) fórmula de normalização 0-100 por eixo em nível de CONTA (não por estratégia — reaproveitar a lógica de caps/pesos já usada em `SCORE_CAP_*`/`SCORE_PESO_*`, mas recalibrada), (b) "Recovery Factor" = `saldo_total / abs(drawdown_maximo_reais)`, hoje não calculado, (c) "Consistency" — já usado por estratégia como `1 - maior_sequencia_loss/n`; decidir se o geral usa a mesma definição |
| 8 | Calendário mensal (cores por dia + $) | Sim, mas só o básico | `_grade_calendario` (`diario_app_classico.py:1918`) já mostra célula colorida (verde/vermelho/cinza) + saldo do dia, clicável pro Histórico filtrado | falta "N trades"/"win%" na própria célula — hoje só tem saldo |
| 9 | Calendário: stats configuráveis por dia (R-Multiple, Daily P/L, Ticks, N trades, Day Winrate) — toggle "Display stats" | **Não** | R-Multiple por trade é NOVO (precisa de `alvo_pontos`/`stop_pontos` como "R" — já existem no schema); Ticks não é um conceito usado no projeto (é WIN/WDO/BIT, não Forex — provavelmente vira "pontos" aqui) | (a) função `r_multiple_trade(trade)`, (b) UI de checkboxes controlando quais métricas aparecem em cada célula, (c) "Ticks" vira "Pontos" (adaptação de nomenclatura, não tradução literal) |
| 10 | Calendário: total semanal (P&L + dias operados) na lateral | **Não** — conferido em `_grade_calendario`: a grade só desenha células de dia, não há coluna de resumo por semana | — | bloco novo: uma coluna extra por linha de semana, somando saldo + contando dias com operação |
| 11 | Recent Trades / Open Positions (tabs, tabela) | Sim, mas em abas SEPARADAS (Histórico e Posição) | `listar_trades()` / posições abertas | condensar um mini-bloco (últimos N) dentro da própria Estatísticas, sem duplicar a aba inteira |
| 12 | Trade Duration Performance (scatter duração × P&L) | **Não**, mas o DADO existe | `datetime_entrada`/`datetime_saida` já estão no schema — duração = diferença | gráfico novo, cálculo de duração novo (trivial) |
| 13 | Progress Tracker (heatmap semanal estilo GitHub contribution) | **Não**, e a MÉTRICA não está clara na referência | — | **decisão do Jesué necessária** (ver abaixo) — parece ser "dias operados com atividade" no TradeZella genérico, mas aqui poderia ser algo mais útil: aderência ao guarda-corpo operacional (`dt.status_do_dia`), ou aderência ao checklist pré-trade, ou simplesmente frequência de registro |
| 14 | "Ask Zella AI" (botão no header) | Equivalente já existe | Mentor de IA (item D, já implementado) | não duplicar — o Mentor de IA já cobre esse papel; não precisa de botão de chat novo aqui |

## Riscos e armadilhas já conhecidas no projeto (aplicam-se DIRETAMENTE aqui)

- **Task #29 [CRÍTICO], já registrado**: Histórico fica lento com o `<table>`
  HTML montado linha a linha. Este redesenho é a oportunidade natural de
  resolver isso de vez — ao trocar a tabela "Recent Trades"/Histórico por
  `dash_table.DataTable` (paginação nativa) em vez de reconstruir a mesma
  arquitetura antiga. Não adiar de novo sem necessidade.
- **Nunca recriar o componente que dispara sua própria callback** (regra já
  documentada em `CLAUDE.md`) — os cards de stat, o toggle do calendário e
  os botões de navegação do calendário precisam seguir o mesmo padrão já
  usado (`_estilo_botao_ativo`, seta/rótulo como estado).
- **Modais/overlays dentro do Diário nunca usam `position:fixed`** — o
  iframe cresce pra caber todo o conteúdo (ver `_overlay`/viewport
  postMessage já implementado). Qualquer novo popup (ex: detalhe de um dia
  do Progress Tracker) precisa seguir o mesmo padrão de overlay em fluxo
  normal + auto-scroll.
- **'98 está descontinuado** — todo este redesenho é SÓ em
  `diario_app_classico.py`/`dashboard.py`/`assets/`. Nenhuma mudança em
  `diario_app_98.py`/`dashboard_98.py`/`assets_98/`, nem espelhamento.
- **Tema claro/escuro já existe** (`C[...]` tokens, `PALETA_GAUGE`) — todo
  gráfico novo usa esses tokens, nunca hex literal do TradeZella. As
  referências são SÓ pra estrutura/tipo de gráfico, nunca paleta de cor.
- **Ganho real na v1**: com "Outros" desmembrado (item C) e o Mentor de IA
  (item D) já rodando, os dados que alimentam o Anticovardia Score e o
  calendário configurável já são mais ricos que quando o docx foi escrito
  — o redesenho parte de uma base de dado melhor do que a original previa.

## Fases sugeridas de execução (menor risco → maior)

1. **✅ CONCLUÍDO (2026-07-30)** — Gaps de dado triviais: `profit_factor`
   geral + `media_ganho_reais`/`media_perda_reais` em
   `calcular_estatisticas`; `sequencia_atual(df)` (streak ATUAL, distinto
   do máximo histórico); `r_multiple_trade(trade)`; `duracao_minutos_trade
   (trade)`; `calcular_anticovardia_score(df)` (score final + 6 eixos,
   fórmula da seção acima). Achados reais na base ao implementar: só
   37% dos trades (83/225) têm `datetime_entrada`/`datetime_saida`
   preenchidos — "Trade Duration Performance" (fase 5) nasce com amostra
   parcial; ~30% não têm `stop_pontos` — R-multiple também parcial. Ambas
   as funções retornam `None` graciosamente nesses casos, nunca erro.
   Testado em `testes/teste_anticovardia_fase1.py`.
2. **✅ CONCLUÍDO (2026-07-30)** — Header de stat cards: Net P&L, Trade
   Win% (gauge), Avg Win/Loss (barra divergente verde/vermelho), Profit
   Factor (gauge), Current Streak (`sequencia_atual`, ALL-TIME de
   propósito — um streak cortado no limite do mês selecionado não faz
   sentido conceitual). `_cards_header_tradezella`/`_grafico_gauge`/
   `_grafico_barra_avg_win_loss` em `diario_app_classico.py`. Os grupos
   antigos (Quantidade/Taxas/Valores) NÃO foram descartados — viraram um
   bloco "Detalhes" logo abaixo (`_cards_detalhe_estatisticas`), preservando
   Wins/Losses/Breakeven/Sequências/Max Gain-Loss/Drawdown/Ganho-Perda
   Total. Verificado ao vivo no navegador: os 3 gráficos novos renderizam
   com as dimensões e valores corretos, sem erro de console, "Detalhes"
   intacto. Reafirma o achado de ambiente da Fase D: o processo filho
   precisou ser reiniciado pra pegar o código novo (não recarrega sozinho).
3. **✅ CONCLUÍDO (2026-07-30)** — Anticovardia Score: radar hexagonal
   (`_grafico_radar_anticovardia`, mesmo padrão de `_radar_roda_vida` já
   usado em Bem-Estar) + barra horizontal com zonas vermelho/amarelo/verde
   e a posição do score marcada (`_grafico_barra_score_anticovardia`, gauge
   Plotly `shape='bullet'`). `_bloco_anticovardia_score` no topo de
   "Análise de Estratégia" (mesma posição de destaque do "Zella score" na
   referência). Verificado ao vivo: os 6 eixos aparecem com os rótulos
   certos (Win Rate/Profit Factor/Avg Win-Loss/Recovery Factor/Max
   Drawdown/Consistency), score real da base = 32.0 (baixo, mas CORRETO —
   a conta está no negativo no período, então um score baixo é o sinal
   certo, não um bug). `_construir_diario_estatisticas`/
   `atualizar_estatisticas` ganharam `tema` (via `State('store-tema',
   'data')`, mesmo padrão já usado em `atualizar_periodo_estatisticas`) pra
   os gráficos novos pegarem a paleta certa.
4. **✅ CONCLUÍDO (2026-07-30)** — Calendário configurável:
   `dt.detalhe_dias_calendario` (R-multiple médio/Day Winrate/pontos
   totais por dia, testado em `testes/teste_calendario_configuravel.py`)
   + painel "Display stats" (5 checkboxes: R-Multiple/Daily P/L/Pontos/Nº
   de Trades/Day Winrate — conferido em detalhe contra `image2.png`
   anotada: Daily P/L É de fato um toggle real no produto, não fixo como
   eu tinha assumido inicialmente) + coluna de total semanal (saldo + dias
   operados) por linha do calendário. R-Multiple e Day Winrate ficam NA
   MESMA linha da célula (ex: "-1.14R, 33.33%"), conferido pixel a pixel
   contra a referência anotada, não em linhas separadas como eu tinha
   errado na primeira tentativa. Verificado ao vivo nos dois temas.
5. **✅ CONCLUÍDO (2026-07-31)** — **Trade Duration Performance** (scatter):
   `dt.dados_duracao_performance()` (só trades com os 2 timestamps
   preenchidos) + `_grafico_duracao_performance`/`_bloco_duracao_performance`
   em `diario_app_classico.py`, logo abaixo de Trades Recentes.
6. **✅ CONCLUÍDO (2026-07-31)** — **Recent Trades condensado**
   (`_bloco_trades_recentes`/`dt.trades_recentes`) — mantido SEPARADO do
   Task #29, sem paginação, N mais recentes por `data_op`+`id_trade`.
7. **✅ CONCLUÍDO (2026-07-31)** — **Progress Tracker**: heatmap estilo
   GitHub contribution graph (`_bloco_progress_tracker`), 52 semanas,
   intensidade = Nº de trades/dia (`por_dia['n']`, decisão já registrada
   acima — sem inventar métrica de disciplina).

## Decisões do Jesué (2026-07-30)

1. **Progress Tracker**: "analise o padrão do TradeZella e implemente da
   mesma forma, veja o que já temos e o que precisamos construir." Decisão
   de design (minha, dado o mandato): a referência (`image3.png` do docx)
   é um heatmap estilo GitHub contribution graph (dias da semana × semanas,
   legenda "Less → More"). A leitura mais literal e honesta, usando dado
   que já existe sem inventar julgamento novo, é **intensidade = número de
   trades operados naquele dia** (0 = célula vazia/mais clara, mais trades
   = mais escura) — vem direto de `calcular_estatisticas()['por_dia']`
   (já conta operações por dia). Não usei `dt.status_do_dia` (guarda-corpo)
   como métrica porque isso seria uma leitura de "disciplina", e o próprio
   `calcular_score_confiabilidade` já documenta a decisão de **não
   fabricar um componente de disciplina/aderência sem dado de regra real
   cadastrada** (ver `analise-tradezella-para-super-quant.md` seção 3 e o
   docstring da função) — mantenho a mesma cautela aqui. Sempre revisável
   depois de ver na tela.
2. **Anticovardia Score**: "Sim, crie a fórmula. Reaproveite o que temos e
   modele o TradeZella." Fórmula final (6 eixos, conferidos contra o radar
   real do TradeZella em `image1.png` — Win%, Profit Factor, Avg Win/Loss,
   Recovery Factor, Max Drawdown, Consistency; **não** inclui "disciplina",
   pela mesma razão do item 1 acima):

   | Eixo | Fórmula (0-100) | Fonte |
   |---|---|---|
   | Win Rate | `taxa_acerto * 100` | já existe, sem cap necessário |
   | Profit Factor | `min(profit_factor / SCORE_CAP_PROFIT_FACTOR, 1) * 100` | reaproveita cap já calibrado (2.5) |
   | Avg Win/Loss | `min(payoff / SCORE_CAP_PAYOFF, 1) * 100` | reaproveita cap já calibrado (2.5) |
   | Recovery Factor | `min((saldo_total / abs(drawdown_maximo_reais)) / ANTICOVARDIA_CAP_RECOVERY, 1) * 100` | NOVO — `saldo_total`/`drawdown_maximo_reais` já existem em `calcular_estatisticas`; cap novo a calibrar (ponto de partida: 5.0, recovery factor acima disso já é excelente) |
   | Max Drawdown | `100 - min(abs(drawdown_maximo_reais) / ANTICOVARDIA_CAP_DRAWDOWN_REAIS * 100, 100)` | NOVO — **cap em R$ fixo, não em % do pico**: `calcular_estatisticas` já abandonou drawdown em % de propósito (comentário na linha ~1061 — o pico inicial da série é pequeno, então drawdown/pico explode sem sentido, ex: -1185%, porque o projeto não rastreia saldo de conta, só P&L acumulado a partir de zero). Repetir esse erro aqui seria reintroduzir o mesmo bug já corrigido. Cap novo a calibrar em R$ (ponto de partida: mesma ordem de grandeza do maior drawdown histórico real da base) |
   | Consistency | `(dias_positivos / dias_operados) * 100` | NOVO ("Day Win %", já citado em `analise-tradezella-para-super-quant.md` seção 2 como métrica distinta do win rate por trade) — usa `por_dia` (já existe) |

   Score final = média dos 6 (pesos iguais, mesmo espírito do score por
   estratégia — `SCORE_PESO_* = 0.25` cada lá, aqui `1/6` cada eixo).
   Caps ficam em `diario_trade.py`, ao lado de `SCORE_*` (mesmo precedente
   já estabelecido — os pesos/caps de score já moram ali, não em
   `config_backtest.py`, apesar da regra geral do CLAUDE.md; sigo o
   precedente real do código, não a regra escrita), nomeados
   `ANTICOVARDIA_*` pra não colidir com `SCORE_*` (que é por-estratégia).
   Calibração inicial de `ANTICOVARDIA_CAP_DRAWDOWN_REAIS`: conferido contra
   a base real (drawdown máximo histórico = R$ -1.152,00), cap inicial
   R$ 1.500,00. Sempre revisável depois de ver o número real na tela —
   nenhum destes pesos/caps é definitivo.
3. **Recent Trades**: mantido SEPARADO do Task #29. "No histórico eu deveria
   poder ver tudo. No Dashboard, apenas os recentes — quero idêntico [ao
   TradeZella]." Implicação: o bloco novo é só as N operações mais recentes
   (tabs "Recent Trades"/"Open Positions" como no `image3.png`, tabela
   compacta, sem paginação — não precisa de `dash_table.DataTable` porque
   nunca cresce). Task #29 continua registrado à parte, adiado, sem re-uso
   forçado aqui.
4. **Ordem de execução**: seguir a ordem de fases já sugerida (1→7) abaixo.

## Fase 2.5 — Redesenho visual fiel ao TradeZella — ✅ CONCLUÍDO (2026-07-30)

Depois das fases 2-4 entregarem DADO/ESTRUTURA reaproveitando o visual
"cockpit" antigo do Diário, o Jesué apontou (2x) que isso era "adaptar os
elementos do TradeZella pro nosso UX", não "modelagem fiel" — pediu estudo
mais detalhado das 3 imagens de referência + pesquisa na internet, e então
uma reescrita visual de verdade. Plano completo em
`C:\Users\jesue\.claude\plans\modular-herding-salamander.md` (aprovado via
ExitPlanMode). Decisão de escopo (AskUserQuestion): **rollout progressivo**
— (1) aba Estatísticas inteira agora, (2) Diário inteiro depois de
validado, (3) app inteiro por último. Este item cobre só a etapa (1).

Achados de re-análise das 3 imagens (`image1.png`=claro, `image2.png`=
calendário escuro anotado, `image3.png`=escuro): cantos bem arredondados
(~14px cards, ~8px células), sombra suave em vez de borda grossa, números
em fonte sans-serif normal (nunca monospace — diferente da convenção do
resto do app), rótulos em title-case (não caixa alta), e — achado mais
importante — **o calendário usa paleta DIFERENTE por tema**: claro = fundo
pastel + texto escuro; escuro = fundo saturado + texto claro (o tema
escuro do TradeZella já parecia com o que o Diário tinha).

Implementado em `diario_app_classico.py` (único arquivo tocado, tokens
`TZ_*` novos, não mexe em `C`/`PALETA_GAUGE` do resto do app):
`_tz_estilo_card`/`_tz_badge_icone` (helpers novos), `_bloco_tradezella`
(equivalente a `_bloco_diario` só pro visual novo — as outras abas do
Diário continuam com `_bloco_diario`, sem mudança), `_grade_calendario`/
`_grade_calendario_anual` ganharam paleta pastel-ou-saturada dependente de
`tema`, e todos os cards/tabelas da aba Estatísticas reescritos pra usar
os tokens novos.

**Bug real encontrado e corrigido durante a implementação**: badges de
emoji tentaram usar `_hex_para_rgba(C['green'], 0.15)` — mas `C[...]` são
strings CSS `var(--x)`, não hex literal (`PALETA_GAUGE[tema][...]` é que é
hex real) — `_hex_para_rgba` quebrava tentando fatiar `"var(--green)"`
como hex. Corrigido usando `color-mix(in srgb, {cor} 15%, transparent)`
(CSS puro, resolvido pelo navegador) em vez de computar em Python — mantém
a mesma filosofia de reatividade automática de tema que o resto do `C`
já tem.

Verificado ao vivo no navegador nos dois temas (alternando `store-tema` via
localStorage, já que o toggle de tema mora no `dashboard.py` pai, não
dentro do iframe do Diário acessado direto): confirmado via
`getComputedStyle` que `border-radius: 14px`/`box-shadow` suave/
`font-family: Inter` aplicam nos cards, calendário mostra
`rgb(254,226,226)`/`rgb(220,252,231)` (pastel vermelho/verde, tema claro)
com texto escuro, e `rgb(224,92,92)`/`rgb(46,204,138)` (saturado, tema
escuro) com texto claro — exatamente como projetado. Suíte completa (14
suítes) passando + auditoria de acoplamento em 0.

**Correção do Jesué sobre esta fase**: "parece que você está apenas
mudando as cores e as fontes dentro da mesma estrutura" — a 2.5 mexeu só
em CSS por cima da MESMA árvore de componentes (`html.Div`/`html.Table`/
Plotly Indicator estático), não no "tipo de biblioteca/elementos/
animações" que ele pediu. Ver Fase 2.6 abaixo pra correção real.

## Fase 2.6 — Biblioteca real (DMC + AG Grid) + animações — ✅ CONCLUÍDO (2026-07-31)

Terceira correção de rumo do Jesué sobre o mesmo pedido: "reavalie a
estrutura de UX do sistema, isso tem a ver com o tipo de biblioteca que
usa, os elementos, os recursos, as animações... descubra como o UX do
TradeZella é estruturado no nível mais profundo." Pesquisa confirmou um
limite real: TradeZella é SaaS fechado, sem documentação pública de
código/animação — não dá pra "descobrir" a implementação interna deles.
O que É possível (e o que este item faz): adotar bibliotecas Dash reais
que produzem o MESMO NÍVEL de sofisticação de componente/animação.
Plano completo aprovado via EnterPlanMode/ExitPlanMode em
`C:\Users\jesue\.claude\plans\modular-herding-salamander.md`.

**Bibliotecas novas instaladas** (`dash-mantine-components` 2.8.0,
`dash-ag-grid` 35.3.0 — compatibilidade com `dash==4.3.0` confirmada por
import isolado antes de qualquer código em cima):

- `dash_mantine_components` (DMC) — substitui `html.Div`/flexbox cru por
  componentes reais: `dmc.Card` (elevação/hover nativos), `dmc.Grid`/
  `dmc.GridCol`/`dmc.SimpleGrid` (grid responsivo real, não flexbox
  manual), `dmc.RingProgress` (gauge que ANIMA o preenchimento — o antigo
  Plotly `go.Indicator` não animava), `dmc.ProgressRoot`/`ProgressSection`
  (barra Avg Win/Loss), `dmc.ThemeIcon` (badges de emoji), `dmc.Skeleton`
  (placeholder pulsante do Mentor de IA antes da 1ª geração), `dmc.Button`.
  Requer `from dash import _dash_renderer;
  _dash_renderer._set_react_version('18.2.0')` ANTES de `dash.Dash(...)`
  ser instanciado (exigência do DMC 2.x) e `app.layout` inteiro envolto em
  `dmc.MantineProvider`. Ponte de tema: `forceColorScheme` do
  MantineProvider ligado ao MESMO `store-tema` via `clientside_callback`
  (`assets`-style, mesmo padrão de `tema.js`).
- `dash_ag_grid` — substitui o `html.Table` montado à mão de
  "Confiabilidade por Estratégia" por um grid real: sort/filter/resize
  nativos do componente (removeu por completo a callback manual
  `ordenar_confiabilidade_estrategia` + `store-ordenacao-estrategia` — o
  grid ordena sozinho no cliente). Expansão de detalhe por linha mudou de
  "seta ▶ + callback de linha-irmã" pra `Input('grid-...', 'cellClicked')`
  — clique em qualquer linha atualiza um painel de detalhe fixo abaixo do
  grid (`atualizar_detalhe_estrategia_grid`), mais simples que o padrão
  anterior porque o grid já resolve "qual linha" sozinho, sem precisar de
  pattern-matching id por tag.

**Animações novas** (`assets/tradezella_ux.css` + `assets/tradezella_ux.js`,
mesmo padrão de asset já usado no projeto pra `tema.js`/`diario_zoom.js`):
hover elevation (`translateY(-2px)` + sombra mais forte) e fade-in
escalonado por card (`@keyframes tz-fade-in-up` + `animation-delay`
incremental via `:nth-of-type`) nos `.tz-card`; count-up de números (Net
P&L, Anticovardia Score) — JS pequeno que extrai o primeiro número do
texto via regex e anima de 0 até o valor real em 600ms com ease-out
cúbico, sem precisar duplicar formatação Python↔JS. Cuidado documentado no
código: `animar_numero=True` só é seguro pra valores de STRING simples,
nunca pra conteúdo composto (o JS reescreve `el.textContent`, que apagaria
filhos não-texto como o badge do Current Streak).

**Bug real corrigido durante a implementação**: `c=None`/`color=None`
passado a componentes DMC quebra o parser de cor no navegador — Python
`None` vira JSON `null`, e como `typeof null === 'object'` em JS, o
parser do Mantine (que só aceita string ou `undefined`) rejeita. Corrigido
omitindo o prop inteiro (não passar `c=`) quando não há cor, em vez de
passar `None` explicitamente — em `_card_header_tradezella` e
`_card_estatistica`.

**Achado de ambiente, não resolvido**: um aviso de console recorrente
("[@mantine/core] Failed to parse color. Expected color to be a string,
instead got object") aparece durante a renderização real da aba
Estatísticas dentro do processo `diario_app_classico.py` — MAS não
reproduz em nenhuma tentativa isolada (testado: render direto, render via
callback, com/sem a ponte `forceColorScheme`, com dado real idêntico).
Confirmado que é **cosmético, não bloqueante**: a página renderiza 100% do
conteúdo corretamente nos dois temas, os componentes DMC/AG Grid funcionam
(sort, clique, hover, animação), verificado via `getComputedStyle` e
inspeção de rede. Fica anotado pra investigar depois com mais tempo (não
prioridade agora — não afeta nenhuma funcionalidade real).

**Correção de layout pós-instalação** (feedback do Jesué com screenshots
anotados: "não gosto desses espaços vazios, organize melhor os
elementos") — `dmc.Card` NÃO estica sozinho pra preencher o container
(diferente de um `html.Div` de bloco comum), deixando espaço morto à
direita quando usado sozinho numa linha, ou quando dois cards ficavam lado
a lado dentro de um `display:flex` cru (um card mais estreito não força o
irmão a crescer). Corrigido com: `w='100%'` em `_bloco_tradezella`/
`_bloco_anticovardia_score` (preenche o container quando sozinho);
`dmc.SimpleGrid` (não `html.Div(display:flex)`) pra TODOS os pares
lado-a-lado (Vencedora/Perdedora, Melhor por Horário/Direção/Ativo/
Categoria, Por Ativo/Janela/Sala/Tags) — SimpleGrid divide a linha em
células IGUAIS de verdade, então cada card ocupa sua metade
independente da largura do vizinho; `dcc.Graph` (Evolução de Saldo,
radar do Anticovardia) ganhou `config={'responsive': True}` +
`style={'width': '100%'}` — sem isso o Plotly não redimensionava pra
acompanhar a coluna do grid assimétrico. Também reorganizado o CONTEÚDO
interno de cada card do header: número e mini-gráfico agora lado a lado
(`dmc.Group justify='space-between'`) em vez de empilhados, eliminando o
vão vazio abaixo de gauges pequenos. Verificado ao vivo: coluna esquerda
(Evolução de Saldo + Anticovardia Score) e direita (Calendário) agora
preenchem exatamente a largura alocada pelo grid assimétrico (511px/722px,
sem sobra), pares "Melhor por X" em 50/50 real (596px cada).

Verificado ao vivo no navegador (import isolado das 2 libs antes de
qualquer código; suíte completa de 14 testes + auditoria de acoplamento em
0; conteúdo real renderizado corretamente nos dois temas; grid
"Confiabilidade por Estratégia" com dado real; larguras de coluna
conferidas via `getBoundingClientRect`).

**Tradução + fidelidade visual dos gauges** (2026-07-31, feedback do
Jesué com 3 screenshots comparando o dashboard aqui contra o TradeZella
real: "Coloque as métricas do dashboard e do gráfico de score em
português. Faça os gráficos, barras e elementos visuais idênticos ao
TradeZella.") —

- Todos os rótulos em inglês trocados por português: os 5 cards do
  header (`Net P&L`→Saldo Líquido, `Trade Win %`→Taxa de Acerto,
  `Avg Win/Loss`→Ganho/Perda Médio, `Profit Factor`→Fator de Lucro,
  `Current Streak`→Sequência Atual), os 6 eixos do radar Anticovardia
  (`_ROTULO_EIXO_ANTICOVARDIA`) e a coluna solta em inglês na grid de
  Confiabilidade por Estratégia.
- Gauge de Taxa de Acerto deixou de ser um arco único verde de % e virou
  um `dmc.RingProgress` de 3 seções de verdade (vermelho=losses,
  cinza=breakeven, verde=wins) com as 3 contagens em texto embaixo —
  bate com o padrão real do TradeZella (anel segmentado + contagens tipo
  "14/4/17" sob o gauge), confirmado via inspeção do DOM
  (`getAttribute`/`--curve-color` dos `<circle>` do SVG: 4 círculos no
  anel de Taxa de Acerto = base + 3 seções, contra 2 círculos = base + 1
  seção nos anéis de Fator de Lucro/Score, que continuam de seção única).
- Barra de Ganho/Perda Médio ganhou os valores em R$ (`R$ +68`/`R$ -37`)
  abaixo dos dois lados coloridos da barra, não só o número de payoff
  acima — bate com o card real do TradeZella que mostra os dois valores
  em dólar sob a barra.
- Verificado ao vivo: `get_page_text` confirma todos os rótulos em
  português e os valores batendo com `calcular_estatisticas`; nenhum erro
  no console (nem o já conhecido/inofensivo de parse de cor apareceu
  desta vez).

**2ª rodada do mesmo feedback de espaço vazio + gráficos + Fator de
Recuperação** (2026-07-31, screenshot novo apontando vão embaixo dos
cards + "gráficos não mudaram" + pergunta sobre o Fator de Recuperação) —

- Cards do header com só texto (Saldo Líquido/Sequência Atual) sobravam
  vão em branco embaixo do conteúdo: `SimpleGrid` estica todo card da
  linha pra altura do card mais alto (o que tem `RingProgress`/barra),
  mas não centraliza o conteúdo dentro dessa altura. Corrigido com
  `display:flex, flexDirection:column, justifyContent:center` no
  `dmc.Card` — confirmado via `getBoundingClientRect`: os 5 cards agora
  têm 115.2px de altura idêntica, com `topGap`≈`bottomGap` em todos (ex:
  30.9px/31.0px no card de Saldo Líquido), ou seja, conteúdo realmente
  centralizado, não só esticado.
- "Taxa de Acerto" removida do bloco Detalhes ("já tem no cabeçalho, não
  precisa repetir") — sobrou só Payoff (+ Fator de Recuperação, novo) no
  grupo Taxas.
- **Gráfico de Evolução de Saldo não tinha mudado**: era uma linha/
  preenchimento de UMA cor só (dourado), o resto do redesenho tinha
  tocado só os cards DMC, não os `dcc.Graph` Plotly. Reescrito pra
  colorir verde quando o saldo acumulado está >= 0 e vermelho quando <
  0, com o ponto de cruzamento de zero interpolado matematicamente
  (`_segmentos_cor_por_sinal`) pra transição limpa exatamente no cruzamento
  — bate com o equity curve real do TradeZella (verde/vermelho conforme
  sinal, não cor única). `plot_bgcolor` também alinhado ao `paper_bgcolor`
  do card (antes usava um cinza de fundo diferente, um resquício do estilo
  Plotly antigo que destoava do card branco ao redor). Testado com série
  sintética cruzando zero 3x — 6 segmentos corretos, cores alternando
  certo, e com dado real (só negativo) rendeu 6 segmentos verde/vermelho
  no navegador, confirmado via inspeção de `fig.data` (`lineColor`/
  `fillcolor` de cada trace).
- **Fator de Recuperação** (saldo total ÷ |drawdown máximo| — quantas
  vezes o lucro líquido "paga" o pior tombo da conta) já existia como
  eixo normalizado 0-100 do radar (`recovery_factor` em
  `calcular_anticovardia_score`), mas nunca aparecia em valor CRU no
  dashboard. Adicionado `fator_recuperacao` em
  `diario_trade.calcular_estatisticas` (calculado uma vez, reusado por
  `calcular_anticovardia_score` em vez de recalculado) + card novo em
  Detalhes > Taxas. Testado: fórmula bate (`saldo_total/|drawdown|`),
  `calcular_anticovardia_score` continua funcionando com o campo
  reaproveitado, `_cards_detalhe_estatisticas` constrói sem erro.
- Verificado ao vivo: suíte completa (11 suítes rodadas desta vez,
  scratch dirs do OneDrive limpos) + `get_page_text` mostrando "Fator de
  Recuperação -0.39" batendo com dado real (saldo -180/drawdown -458) +
  inspeção de `fig.data` das 2 figuras Plotly da página + nenhum erro no
  console.

**Bloco Trades Recentes** (2026-07-31, feedback: "você não está mostrando
o bloco de trades recentes no dashboard, como é no TradeZella") —
adicionado `dt.trades_recentes(n=8)` (só trades reais, ordena por
`data_op` + `id_trade` como desempate — não há timestamp de execução
confiável em toda a base) e `_bloco_trades_recentes(tema)` no dashboard,
posicionado entre o grid Evolução/Calendário e "Análise de Estratégia".
Nome de estratégia resolvido via `_mapa_nomes_estrategias` (slug→nome de
exibição) — a 1ª versão mostrava o slug cru (`bounce_em_suporte`), corrigido
antes de restart final. Verificado ao vivo: 8 linhas reais, ordem
decrescente por data, nomes de estratégia legíveis, nenhum erro no
console; suíte completa (11 suítes) passou.
