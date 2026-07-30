# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Note for any Claude Code instance opening this project

Jesué runs **two separate Claude Code instances in this same folder**: this
one (general development) and a native instance that live-tests the running
dashboards via a Chrome browser extension. Both have full file access to
this directory — `LABORATORIO_CHROME.md` at the repo root is a shared
context/feedback file both instances read and write directly (no
copy/paste). Check it if Jesué mentions testing feedback.

## What this project is

A quantitative day-trading system for WIN (mini index futures) and WDO
(mini dollar futures) on B3, M5 timeframe: loads candles+ticks (ProfitChart
CSV export + MetaTrader5), computes a large vector of price-action features
over them, persists everything to Parquet, and serves two live Dash
dashboards with AI-assisted reading (Gemini) plus a full trading journal
module.

There is no `requirements.txt`/`pyproject.toml`/test suite in this repo —
dependencies are installed ad hoc into the system Python, and there is no
formal test framework. Verification happens by running the real
processes and hitting real (or scratch-copied) data — see "Testing
conventions" below.

## Commands

Four independent long-running Dash processes make up the live system —
none of them exit on their own, run each in the background:

```bash
python dashboard.py          # classic dark-mode dashboard  → http://127.0.0.1:8050
python dashboard_98.py       # Windows-98-styled dashboard  → http://127.0.0.1:8051
python diario_app_classico.py  # Diário de Trade, classic skin → http://127.0.0.1:8053
python diario_app_98.py        # Diário de Trade, '98 skin      → http://127.0.0.1:8052
```

The two `diario_app_*.py` processes are children, embedded via `<iframe>`
inside the corresponding main dashboard (see "Process isolation" below) —
`gerenciador_processos.garantir_processo_filho` auto-launches them from
`dashboard.py`/`dashboard_98.py` on startup if not already running, so in
practice starting just the two main dashboards is enough; start the
`diario_app_*` processes directly only when iterating on Diário code in
isolation.

Data pipeline, via the single CLI entry point `run_backtest.py` (mutually
exclusive flags):

```bash
python run_backtest.py --setup        # heavy, one-off: full CSV + MT5 backfill, delta calc
python run_backtest.py --atualizar    # light, day-to-day: incremental fetch since last update
python run_backtest.py --features     # recompute only the feature vector, no new data fetch
python run_backtest.py --setup --sem-tick  # --setup but skip historical tick backfill
```

`dashboard.py`/`dashboard_98.py` already call the incremental update
automatically on startup and every 30s — you don't need to run
`--atualizar` manually just to see fresh candles. Run `--features`
explicitly after changing anything in `features.py`'s pipeline, since the
computed columns are cached in the Parquet files under `data/base_processada/`
and dashboards only read from there.

### Testing conventions (no formal test suite)

- **Never test writes against production Parquet files.** Monkeypatch
  `persistencia._caminho_X`/`_caminho_X_lock` to a scratch path before
  exercising any function that saves data, e.g.:
  ```python
  persistencia._caminho_diario = lambda: Path("scratch_diario.parquet")
  ```
  **Patch ALL FIVE journal paths, not just the obvious one** — a single
  finalize touches every one of them:
  `_caminho_diario`, `_caminho_posicoes_abertas`, `_caminho_tickets_finalizados`,
  `_caminho_tickets_ignorados`, `_caminho_reconciliacao` (plus the two
  `_lock` variants). Real incident on 2026-07-27: a test of
  `finalizar_par_hedge` patched only two of them, wrote bogus tombstones to
  the production `tickets_finalizados.parquet`, and the live background sync
  then purged two REAL open positions from `posicoes_abertas.parquet`.
- **There IS a test suite now, in `testes/`** — plain scripts, no framework,
  each exits non-zero on failure. Run them after touching the MT5→journal
  engine:
  ```bash
  python testes/teste_duplicacao.py          # 1 ticket = no more than 1 trade
  python testes/teste_reconciliacao.py       # hedge legs must not become phantom pendencies
  python testes/teste_hedge_espelhado.py     # merging closed mirrored legs (+ negative cases)
  python testes/teste_agrupar_posicoes.py    # partial fills, out-of-order deals
  python testes/teste_ignorar_pendencia.py   # "this operation is not mine"
  python testes/teste_limpeza_temporarios.py # orphan .tmp cleanup
  python testes/teste_editar_trade.py        # editing a finalized trade (allowlist of fields)
  python testes/teste_opcoes_fora_cadastro.py # dropdown must never silently drop a stored tag
  python testes/auditoria_ids_callbacks.py   # cross-tab callback coupling — must print 0
  ```
  `testes/auditar_vs_profit.py <export.csv>` is read-only and compares the
  journal against a ProfitPro "Operações" CSV export, day by day.
  `testes/teste_tag_input.html` opens directly in a browser (no server).
- **A real browser IS usually available** (in-app Browser pane and/or the
  Claude-in-Chrome extension) — use it. Correction made 2026-07-28: this file
  previously claimed there was none, which pushed several sessions into
  inferring UI behaviour instead of observing it. Two hard lessons from
  actually driving the UI:
  - **Check the browser console for `ReferenceError: A nonexistent object was
    used in an Output/State`.** Dash rejects the WHOLE callback, silently, when
    any id it references isn't currently mounted. This is the single most
    common cause of "the button does nothing" here.
  - **Never fake the DOM state you're trying to observe.** Setting `.value` on
    an element that has no `.value` (e.g. a `<div>` wrapper) *creates* the
    property and makes the test pass while the widget stays broken for the
    user. Read the real element first (`tagName`, `outerHTML`), then act on it.
- If a browser really isn't reachable, verify a Dash callback end-to-end by
  POSTing directly to `/_dash-update-component` (see
  Dash's wire format — multi-output callbacks join outputs as
  `..id.prop...id2.prop2..`; any callback using `allow_duplicate=True`
  appends a `@<hash>` suffix per Output, readable from
  `app.callback_map.keys()` after importing the module). For wildcard
  (`ALL`/`MATCH`) callbacks, reconstructing the exact wire body is fragile —
  prefer calling the callback function directly in Python, simulating
  `dash.callback_context` if it reads `ctx.triggered`. For visual checks,
  `chrome.exe --headless=new --screenshot=out.png --window-size=W,H <url>`
  (a live process must already be running); expect a "Loading..." shell on
  the very first request — wait a few seconds after process start before
  trusting a screenshot as final.
- Importing `dashboard.py`/`dashboard_98.py` has a side effect: it starts a
  background thread that syncs against MT5 and mutates the real Parquet
  files. Importing either module (even from a throwaway diagnostic script)
  triggers this — expect log noise and a real incremental update.

## Architecture

### Data pipeline

```
CSV (ProfitChart) ──┐
                     ├─► merge_fontes.py ─► persistencia.py ─► Parquet (data/base_processada/)
MetaTrader5 ─────────┘                                              │
                                                                     ▼
                                                              features.py
                                                        (calcular_todos_features)
                                                                     │
                                                                     ▼
                                                    Parquet with features ─► dashboards
```

`persistencia.py` is the **only** I/O gateway to disk — never read/write
Parquet anywhere else. `features.py` runs an ordered pipeline of feature
blocks (candle anatomy → ATR → pivots → trend → S/R clustering → S/R
zones/bipolarity state machine → build-up/double-pressure → temporal →
aggregate flow → S/R probability score); each block depends on the
previous one's output columns. The S/R zone catalog lives only in
`df.attrs` in memory (never persisted — it's deterministic from candle
history, cheap to rebuild). See `MAPA_ESTRUTURA.md` for the full column
naming convention (`sr_{lado}_{rank}_{campo}`) and per-block detail.

### Two front-ends, one shared logic layer

`dashboard.py` (classic, dark "cockpit" UI) and `dashboard_98.py` (full
Windows-98 desktop metaphor — real draggable windows, taskbar, Start menu,
Clippy as the AI mentor) run independently and never duplicate data logic.
Everything below the UI layer is shared: `metricas.py` (metric extraction,
extracted specifically to eliminate duplication between the two dashboards),
`persistencia.py`, `mt5_loader.py`, `features.py`, `config_backtest.py`,
`ai_analyst.py` (Gemini mentor), `calendario_economico.py`, `diario_trade.py`,
`tracados.py`, `aprendizagem.py`. Only the UI-construction layer
(`construir_*` functions, callbacks, `estilo_win98.py`) is per-dashboard.
`config_backtest.py` is the single home for every calibratable constant in
the system — if a magic number is used in more than one place, it belongs
there, not inlined.

**Dash gotcha that recurs across this codebase**: any component the user
clicks (button, pattern-matching id) must never be recreated by its own
triggering callback — recreating it resets `n_clicks` to 0 client-side,
causing "phantom" reverted state on the next tick. Fix pattern used
everywhere: keep the clickable element static in the layout, and have
callbacks only update its `style`/`className` or a sibling element's
`children`.

### Diário de Trade — separate processes via iframe, not tabs

The trade journal module (`diario_trade.py` for pure data logic,
`diario_app_classico.py`/`diario_app_98.py` for the two UI processes) runs
as **fully separate Dash server processes**, embedded via `<iframe>` inside
the corresponding main dashboard's window/panel chrome —
`gerenciador_processos.py` manages child lifecycle (idempotent launch on
startup, heartbeat-based self-exit if the parent dies). This is not a
preference — a Dash SPA's client-side reconciliation cost scales with
*total mounted component count regardless of visibility* (`display:none`
does not help), so once the Diário grew large enough it caused real UI lag
in the main dashboard; an iframe is a hard architectural boundary (always a
separate Window/Document), the only fix that actually holds regardless of
how large the Diário gets.

Consequence: any modal/overlay inside the Diário **must not** use
`position:fixed` — the iframe grows to fit all content with no scroll of
its own, so a `fixed` element centers against the iframe's total height
(often 2000px+), not the visible viewport, and silently renders off-screen.
The established fix (see `_overlay`/`_overlay_98` in the `diario_app_*.py`
files) is: render the overlay in normal flow + auto-scroll to it on open.

The account this system reconciles against is **hedging, not netting** — a
stop/target order firing opens a new opposite position instead of closing
the original. `diario_trade.detectar_pares_hedge` merges opposite same-
volume positions into one net trade; `mt5_loader.agrupar_posicoes` +
`diario_trade._parear_hedge_deals` do the equivalent for historical MT5
deal reconciliation. Any new code that reads MT5 positions/deals directly
needs to account for this pairing — don't assume one MT5 ticket == one
trade.

### Modo Aprendizagem — bounded, auditable auto-tuning only

`config_operacional.py` (server-side JSON flag, since it must be read by
background jobs and by separate processes, not just one browser tab) +
`aprendizagem.py` implement an explicit non-goal: this is **not** a system
that rewrites its own logic. It only ever (a) logs a comparison between
engine predictions and real trade outcomes, always, and (b) — only when
the flag is on — nudges an existing named constant in
`config_backtest.py` within a pre-approved safe bound, always writing an
audit log entry. `aprendizagem.py` today never writes to
`config_backtest.py` at all (log-only) pending real trade volume.
Anything that isn't "move a number within a bound" becomes a structural
suggestion entry for a future manual implementation pass, never
auto-coded.

### Known environment quirks

- **MT5 IPC connectivity is flaky** — drops and reconnects on its own even
  while the MetaTrader 5 terminal process stays running. If a script gets
  `(-10004/-10005, 'No IPC connection')` despite the terminal being open, a
  direct `MetaTrader5.shutdown()` + `MetaTrader5.initialize()` in the same
  process often reconnects (not 100% reliable, sometimes needs 2-3 tries).
- **Windows console encoding**: printing emoji or certain special
  characters directly to stdout on this environment raises
  `UnicodeEncodeError` and can silently kill a background thread. Write to
  a UTF-8 file and read it back instead of printing directly when the
  content might contain non-cp1252 characters.

## Where deeper documentation already lives

Don't duplicate these — read them for anything beyond the summary above:

- `MAPA_ESTRUTURA.md` — full data-pipeline/module reference (classic dashboard side).
- `MAPA_ESTRUTURA_SQ98.md` — Windows-98 front-end architecture, design system, window mechanics.
- `CHANGELOG.md` / `CHANGELOG_SQ98.md` — chronological log of decisions and real bugs fixed (the "why", not a commit log).
- `LABORATORIO_CHROME.md` — live shared state between this instance and the Chrome-testing instance.
