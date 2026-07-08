# Mapa de Estrutura do Sistema

Referência rápida da arquitetura do projeto (`quant`) — o que cada módulo
faz, como os dados fluem, e onde cada coisa mora. Consultar antes de mudar
algo, e usar como base quando for gerar documentação completa.

Não é auto-gerado — mantido manualmente. Atualizar quando um módulo novo
entrar, mudar de responsabilidade, ou sair do fluxo principal.

---

## Visão geral em uma frase

Sistema de day trading quantitativo para WIN (mini índice) e WDO (mini
dólar) na B3, timeframe M5: carrega candles+ticks (CSV do ProfitChart +
MetaTrader5), calcula um vetor grande de features de price action sobre
eles, persiste tudo em Parquet, e serve um dashboard Dash ao vivo com
leitura assistida por IA (Gemini).

## Ativos e timeframe

- **Ativos:** WIN (índice futuro) e WDO (dólar futuro) — `cfg.ATIVOS`.
- **Timeframe:** M5 (5 minutos) — único timeframe hoje, ver nota MTF na
  seção "Frentes futuras" abaixo.

---

## Fluxo de dados (pipeline)

```
CSV ProfitChart ──┐
                   ├──► merge_fontes.py ──► persistencia.py ──► Parquet
MetaTrader5 ───────┘         │                (data/base_processada/)
                              │
                              ▼
                        features.py
                  (calcular_todos_features)
                              │
                              ▼
                   Parquet com features ──► dashboard.py ──► browser
                                                  │
                                                  ▼
                                            ai_analyst.py (Gemini)
```

**Dois modos de carga** (decisão de 30/06/2026, em `merge_fontes.py`):
- `setup_inicial(ativo)` — pesado, roda uma vez ou sob comando explícito.
  Lê CSV completo + MT5 complementar, calcula delta onde há tick, salva
  tudo em Parquet. Nunca roda automaticamente.
- `atualizar_incremental(ativo)` — leve, dia a dia. Lê o Parquet já salvo,
  busca só o que é novo desde a última atualização, concatena, salva de
  novo.

`dashboard.py` chama `atualizar_incremental` automaticamente ao abrir, sem
ação manual.

---

## Módulos principais (raiz do projeto)

| Arquivo | Responsabilidade |
|---|---|
| `config_backtest.py` | Configuração central — caminhos, ativos, timeframe, e **todo** parâmetro calibrável do sistema (ATR, clustering de S/R, pesos do score, janelas de rompimento/reteste). Se é um número mágico usado em mais de um lugar, deveria estar aqui. |
| `csv_loader.py` | Lê e faz parsing dos CSVs exportados do ProfitChart (formato BR, correção de escala do WIN). |
| `mt5_loader.py` | Carga complementar via MetaTrader5 — candles recentes (contrato vigente) + ticks com flag de agressor (`copy_ticks_from` com retry). |
| `merge_fontes.py` | Funde CSV+MT5 numa série única rastreável. `setup_inicial` (pesado) e `atualizar_incremental` (leve) — ver acima. |
| `persistencia.py` | **Única** porta de entrada/saída em disco (Parquet). Nunca ler/escrever Parquet fora daqui. |
| `features.py` | O motor de cálculo — transforma candles crus num vetor grande de features de price action. Ver seção dedicada abaixo. |
| `run_backtest.py` | Ponto de entrada CLI único do motor de dados: `--setup`, `--atualizar`, `--features` (só recalcula features sem buscar dado novo). |
| `dashboard.py` | Ponto de entrada visual clássico (`python dashboard.py`, abre em `localhost:8050`), dark mode "cockpit financeiro". App Dash com callbacks ao vivo (1s) e incrementais (30s). Ver seção dedicada abaixo. |
| `dashboard_98.py` | Segundo front-end — **Super Quant '98**, visual Windows 98 completo (`python dashboard_98.py`, abre em `localhost:8051`). Roda em paralelo ao clássico, compartilhando toda a lógica de dados via `metricas.py` e os módulos abaixo. Ver `MAPA_ESTRUTURA_SQ98.md` para a arquitetura própria (sistema de design, janelas, Clippy). |
| `metricas.py` | `extrair_metricas(df)` — extração de métricas do snapshot, compartilhada pelos dois front-ends (extraído do `dashboard.py` original pra eliminar duplicação). |
| `ai_analyst.py` | Mentor IA via Google Gemini, integrado ao chat de ambos os dashboards. |
| `calendario_economico.py` | Janelas de notícia de alto impacto (Copom/FOMC/NFP) — dados estáticos com fonte citada, sem chamada de rede. Ver `CHANGELOG.md` 2026-07-04. |
| `extrator_mt5.py` | Script utilitário standalone para explorar símbolos/dados via MT5 (não faz parte do pipeline principal). |

### `assets/` — estáticos servidos pelo Dash

`assets/style.css` — feedback visual dos botões de seleção de ativo
(hover/pressed) e pulso de transição ao trocar de ativo (ver
`CHANGELOG.md` 2026-07-04). Pasta `assets/` é servida automaticamente pelo
Dash — qualquer `.css`/`.js` colocado ali entra sem registro manual.

### Scripts de diagnóstico/investigação (raiz + `old/`)

Não fazem parte do pipeline em produção — foram usados para depurar
problemas pontuais de dado (timezone, paginação do MT5, limites de
`copy_ticks_from`, deslocamento de 3h, validação cruzada CSV×MT5). Ficam
na raiz (`investiga_*.py`, `diagnostico_*.py`, `teste_*.py`,
`mapeamento_*.py`, `reconfirma_*.py`, `valida_*.py`, `verifica_*.py`) ou já
foram movidos para `old/` quando a investigação fechou. Útil como
histórico de "por que isso é assim", não para rodar de novo sem revisar.

`vai.py` — outro script solto, não integrado ao pipeline principal
(conferir antes de assumir que está em uso).

### Documentos normativos / de contexto

| Arquivo | Papel |
|---|---|
| `spec_suporte_resistencia_forte.md` | Doutrina de price action puro (fonte normativa dos critérios de S/R forte, bipolaridade, confluência) — trazida pelo Jesué, guia decisões de design do motor de S/R. |
| `CHANGELOG.md` | Log cronológico de mudanças e decisões (o "porquê"), não um log de commits. |
| `MAPA_ESTRUTURA.md` | Este arquivo. |
| `MAPA_ESTRUTURA_SQ98.md` | Mesma ideia deste arquivo, mas pro front-end Windows 98 (`dashboard_98.py`) — arquitetura, sistema de design, Clippy, mecânica de janela. |
| `CHANGELOG_SQ98.md` | Changelog de UX do front-end Windows 98 — separado deste porque cobre só decisão/correção visual, não pipeline de dados. |
| `diario.xlsm` | Diário de trade real do Jesué (109 operações, 14/04–01/07/2026), com print anotado por operação via rich-value do Excel. Fonte valiosa de calibração empírica — ver `CHANGELOG.md` de 2026-07-02 pra técnica de extração das imagens. Candidato a virar o módulo "Estudos" (ver plano de S/R, seção "Fases futuras"). |

---

## `data/` — dados persistidos

```
data/
├── csv/                          ← CSVs brutos exportados do ProfitChart
│   ├── WDOFUT_F_0_5min.csv
│   └── WINFUT_F_0_5min.csv
└── base_processada/               ← única fonte de verdade pro dashboard/features
    ├── WDO_candles.parquet        ← candles M5 + features calculadas
    ├── WDO_metadata.parquet       ← metadados de atualização (última busca, etc.)
    ├── WIN_candles.parquet
    └── WIN_metadata.parquet
```

Nunca editar os Parquet diretamente — sempre via `persistencia.py`
(chamado por `merge_fontes.py`/`run_backtest.py --features`).

---

## `features.py` — blocos do pipeline (`calcular_todos_features`)

Ordem de execução (cada bloco depende do(s) anterior(es)):

1. **Anatomia de candle** (`calcular_anatomia`) — corpo, sombras,
   direção, amplitude, em % relativos.
2. **ATR e tamanho relativo** (`calcular_atr`).
3. **Pivôs estruturais** (`detectar_pivos`) — topos/fundos confirmados
   (metodologia B), usados como base pra tendência e clustering de S/R.
4. **Tendência e fase** (`calcular_tendencia`) — estado/fase/saúde da
   tendência via busca binária sobre os pivôs (reescrito p/ performance
   em 30/06/2026, era O(n²)).
5. **S/R por clustering** (`calcular_sr` + `_clusterizar_pivos_sr`) —
   agrupa pivôs em zonas (não pontos), calcula preço de referência
   ponderado, rankeia os `N_NIVEIS_SR` (=3) mais próximos por lado.
6. **Zonas S/R e bipolaridade** (`calcular_zonas_sr`) — rejeição/
   decaimento de toques/impulso/agressão por zona, máquina de estados
   (`ativa` → `aguardando_pullback`/`throwback` → `rompida` + zona
   espelho), re-ranking incluindo espelhos. **Catálogo de zonas fica só
   em memória** (`df.attrs`, nunca persistido — reconstruído a cada
   rodada, é determinístico a partir do histórico de candles). Zonas
   espelho acumulam toques próprios a partir da criação (corrigido em
   2026-07-04 — antes ficavam travadas em `toques=0`). Calcula também um
   **sinal de rompimento intradiário** (`_rompimento_hoje`, 2026-07-02)
   separado do status estrutural acima — responde "rompeu HOJE, dentro do
   próprio pregão?" em vez de "essa zona foi abandonada pra sempre?" (a
   pergunta que o status estrutural responde, ancorada no histórico
   inteiro — ver `CHANGELOG.md` de 2026-07-02 pro raciocínio completo de
   por que são duas perguntas diferentes, não uma).
7. **Build-up e double pressure** (`calcular_buildup_e_double_pressure`)
   — reescrito vetorizado em 2026-07-04 (havia um bug de função duplicada
   fazendo a versão lenta, com `.loc` em loop Python, rodar por engano —
   ver `CHANGELOG.md`). Caiu de ~16-19s para ~0.04s sobre ~56-57k candles;
   deixou de ser o gargalo do pipeline.
8. **Temporal** (`calcular_temporal`) — janela de pregão, dia da semana.
9. **Fluxo agregado** (`calcular_fluxo_agregado`) — delta acumulado,
   razão de agressão.
10. **Score de probabilidade S/R** (`calcular_prob_niveis_sr`) — roda
    por último porque depende de fluxo/build-up já calculados. Score
    0-100% de bounce vs. rompimento por nível, sempre clipado em
    `[SCORE_PROB_MIN, SCORE_PROB_MAX]` = [5,95] pra nunca soar como
    certeza absoluta.

**Convenção de nomenclatura de colunas de S/R:**
`sr_{lado}_{rank}_{campo}`, onde `lado` ∈ {`acima`,`abaixo`}, `rank` ∈
{1,2,3}, `campo` ∈ {vazio (preço), `toques`, `idade`, `violado`, `forte`,
`dist_atr`, `zona_min`, `zona_max`, `status`, `rejeicao_media`,
`agressao_media`, `impulso_medio`, `forca_toques`, `forca_herdada`,
`zona_espelho_id`, `prob_bounce`, `rompimento_hoje`}. Colunas legadas (`sr_acima`,
`qualidade_sr_acima`, etc.) são mantidas como alias do rank 1, pra
`ai_analyst.py` não precisar mudar.

---

## `dashboard.py` — estrutura do app Dash

- **Carga de dados:** `carregar_bases()` — lê os Parquet via
  `persistencia`.
- **Componentes visuais reutilizáveis:** `gauge_cockpit`,
  `grafico_candles`, `card`, `secao` (paleta/tokens definidos no topo do
  arquivo).
- **Extração de métricas:** `extrair_metricas(df)` — converte o DataFrame
  num dict plano de métricas prontas pro template (inclui
  `niveis_sr_acima`/`niveis_sr_abaixo`, listas de dict por rank).
- **Construtores de seção** (cada um monta um bloco visual a partir das
  métricas):
  - `construir_hero` — só os velocímetros (força/contexto). O seletor de
    ativo (WIN/WDO/WIN×WDO) SAIU daqui em 2026-07-04 e virou estático em
    `app.layout` (ver nota abaixo).
  - `construir_market_view` — visão geral de mercado.
  - `construir_price_action` — bloco de S/R (3 cards por lado, badges de
    status/força/probabilidade/rompimento intradiário).
  - `construir_big_players` — footprint de tape/book.
- **Layout do app:** dois `dcc.Interval` — `interval-live` (1s, atualiza
  preço/candle atual e reconstrói hero/market-view/price-action/
  big-players) e `interval-slow` (30s, dispara atualização incremental em
  background). Botões `btn-win`/`btn-wdo`/`btn-correlacao` são estáticos
  (nunca recriados) — **importante**: um bug real (2026-07-02 a
  2026-07-04) fazia esses botões serem recriados a cada tick de 1s dentro
  de `construir_hero`, resetando `n_clicks` no browser e revertendo
  qualquer troca de ativo pro WIN sozinho no tick seguinte. Corrigido
  movendo os botões pra fora do conteúdo dinâmico; só o `style` (cor) deles
  muda, via callback dedicado `atualizar_estilo_ativo_selecionado`
  disparado apenas em `store-ativo`. Ver `CHANGELOG.md` 2026-07-04.
- **Callbacks** (`@app.callback`): seleção de ativo, estilo do botão de
  ativo selecionado, atualização do candle atual, atualização incremental
  em background, atualização geral do dashboard (inclui o badge de
  calendário econômico), toggle do chat, envio de mensagem ao mentor IA.
- **Clientside callbacks** (`app.clientside_callback`, JS puro): scroll
  automático do chat; pulso visual de transição (`trocando-ativo`) ao
  trocar de ativo, disparado só em `store-ativo` — ver `assets/style.css`.

**Ponto de entrada:** `python dashboard.py` → `http://127.0.0.1:8050`.

---

## Frentes futuras (roadmap, ver plano completo)

Plano de referência:
`C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md`
(motor de S/R — Fase 1 implementada, Fase 2/3 e módulo "Estudos" ainda
não iniciados).

- **Fase 2 — Confluência:** MME21/MME9/MMA200/Fibonacci/números redondos
  como bônus no score de S/R. Requer implementar médias móveis e lógica
  de Fibonacci do zero (não existem hoje).
- **Fase 3 — Multi-timeframe (MTF):** sistema é 100% M5 hoje; avaliar
  custo/arquitetura antes de tratar como "mais um critério do score".
- **Módulo "Estudos" (registrado em 2026-07-02, deliberadamente adiado
  para o final, com mais planejamento — pedido explícito do Jesué em
  2026-07-04):** diário de operações de trading + análises de estudo,
  inspirado na plataforma **Tradezella**, servindo tanto o
  desenvolvimento do trader quanto o refinamento do motor (fonte de casos
  reais de calibração, como já usamos com `diario.xlsm` nesta sessão). Ver
  plano acima, seção "Fases futuras", pra detalhes e pontos em aberto.
  **Único item de todo o backlog de 2026-07-04 que ficou de fora — não
  iniciar sem pedido explícito.**
- ~~**Calendário econômico**~~ — **implementado em 2026-07-04**
  (`calendario_economico.py`, ver tabela de módulos acima e
  `CHANGELOG.md`).
