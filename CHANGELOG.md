# Changelog

Registro cronológico das mudanças relevantes no sistema. Objetivo: dar
contexto rápido do "porquê" de cada decisão, para consulta futura e para
alimentar a documentação completa quando ela for gerada. Não é um log de
commits — é um log de decisões e correções.

Formato de cada entrada: data, módulo(s) afetado(s), o que mudou e por quê.

---

## ⚠️ Cuidado operacional — `import dashboard` tem efeito colateral

`dashboard.py:127` chama `threading.Thread(target=carregar_bases,
daemon=True).start()` **no nível do módulo**, fora de
`if __name__=='__main__':` — é assim que o dashboard atualiza sozinho ao
abrir no browser, por design. Mas isso significa que **qualquer `import
dashboard`** (inclusive scripts de teste/diagnóstico ad-hoc, REPL, etc.)
já dispara uma atualização incremental via MT5 em background, escrevendo
no parquet. Se outro processo estiver lendo/escrevendo o mesmo parquet ao
mesmo tempo (outra sessão de teste, o próprio dashboard já aberto), corre
risco real de corromper o arquivo (aconteceu em 2026-07-02 — ver entrada
datada abaixo). **Nunca `import dashboard` de um script solto pra
inspecionar funções — chame só o necessário via subprocess isolado, ou
copie a lógica pontual sem importar o módulo inteiro.**

---

## Pendências conhecidas (deixadas para depois, de propósito)

Lista viva — mover pra uma entrada datada quando resolvida, apagar daqui.

- ~~**[Bug de design] Sinal intradiário só detectava 1 ciclo por dia**~~ —
  **corrigido em 2026-07-02** (ver entrada datada abaixo), validado contra
  o caso real WDO 5.231,50.

- ~~**[Ideia registrada] Calendário econômico**~~ — **implementado em
  2026-07-04** (`calendario_economico.py`, ver entrada datada abaixo).

- ~~**[Verificação pendente] Sinal intradiário novo vs. gráfico do
  diário**~~ — **conferido em 2026-07-04** candle a candle (ver entrada
  datada abaixo) — bate exatamente com `image76.png`.

- ~~**[Calibração pendente] Pesos do sinal intradiário nunca
  validados**~~ — **calibrados em 2026-07-04** via backtest empírico (ver
  entrada datada abaixo): `PESO_ROMPIMENTO_INTRADAY` 12→9,
  `PESO_ROMPIMENTO_INTRADAY_PULLBACK` 18→15.

- **[Arquitetural, parcialmente endereçado] Rompimento estrutural
  (`_status`) ainda ancorado no último toque de TODA a base histórica
  (`posmax`).** Continua só reconhecendo o rompimento *definitivo* de uma
  zona (quando ela nunca mais é tocada em todo o dataset) — mas isso
  agora é uma limitação aceita conscientemente, porque o sinal
  intradiário (`_rompimento_hoje`) cobre a pergunta "isso rompeu HOJE?"
  que é a que importa pro dia a dia. Não mexer no `posmax` sem motivo novo.

- ~~**[Calibração pendente] `IDADE_MINIMA_NIVEL_FORTE`/
  `TOQUES_MINIMOS_NIVEL_FORTE`**~~ — **revisado empiricamente em
  2026-07-04** (ver entrada datada abaixo): testado contra taxa de bounce
  real em várias combinações, nenhuma separa melhor que o valor atual —
  mantido sem mudança, com justificativa documentada em
  `config_backtest.py`.

- ~~**[TOP PRIORIDADE] Clustering em grade densa**~~ — **corrigido em
  2026-07-04** via `cfg.JANELA_CLUSTERING_CANDLES=2500` (ver entrada
  datada abaixo). Trade-off aceito: casos mais antigos que ~23 dias de
  pregão ficam fora do alcance de validação a partir de agora.

- **[Calibração pendente, amostra insuficiente] `JANELA_MAX_RETESTE=40` e
  `JANELA_INVALIDACAO_WICK_SWEEP=3`** (FSM estrutural). Revisado em
  2026-07-04: o FSM estrutural só transiciona de status em 2-51 candles
  de 56-57 mil — amostra baixa demais pra recalibrar com confiança agora.
  Único caso com números de candle exatos (WDO 5.231,50) teve reteste
  12-14 candles após o rompimento, dentro de 40 — mantido sem mudança até
  surgirem mais casos reais.

- ~~**[Conhecida desde a Fase 1] Zonas espelho não acumulam toques
  próprios**~~ — **corrigido em 2026-07-04** (ver entrada datada abaixo).

- ~~**[Performance] `calcular_buildup_e_double_pressure`**~~ —
  **corrigido em 2026-07-04**: de ~16-19s para ~0.04s (ver entrada datada
  abaixo, causa raiz era um bug de função duplicada, não só falta de
  vetorização).

- ~~**[UX, bug crítico] Trocar para WDO reverte sozinho para WIN**~~ —
  **corrigido em 2026-07-04** (ver entrada datada abaixo): causa raiz
  encontrada (não foi preciso reproduzir ao vivo).

- **[UX, pedido geral de revisão, parcialmente endereçado] Fluidez do
  dashboard.** Os dois pontos específicos que Jesué reportou (2026-07-02)
  — botão sem feedback visual e falta de indicação de carregamento ao
  trocar de ativo — foram endereçados em 2026-07-04 (ver entrada datada
  abaixo: hover/pressed nos botões + pulso visual imediato no clique).
  Mas o pedido original era mais amplo ("revisar toda a UX... fazer uma
  revisão mais ampla quando for atacar isso") — essa revisão mais ampla
  (além dos dois pontos específicos) ainda não foi feita.

- **[Registrado, não iniciado por pedido explícito] Módulo "Estudos"**
  (diário de trading + análises, inspirado no Tradezella). Jesué pediu
  explicitamente para deixar por último, com mais planejamento — ver
  plano em `C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md`
  e `MAPA_ESTRUTURA.md`, "Frentes futuras".

---

## 2026-07-04

### Correção — clustering limitado a uma janela recente (fim da "grade densa")
**Módulos:** `features.py`, `config_backtest.py`

Implementado o diagnóstico de 2026-07-02: `_clusterizar_pivos_sr` agora
limita o pool de pivôs a `cfg.JANELA_CLUSTERING_CANDLES` candles mais
recentes (filtro aplicado em `t_idx`/`f_idx` logo após serem calculados),
em vez de agrupar toques de toda a base histórica (2+ anos).

**Duas rodadas de teste empírico:**

1. **10.000 candles (~90 dias de pregão)**: resolveu a lentidão do WDO
   (91s → 24s) e deixou a contagem de toques mais realista (17-57 em vez
   de até 137). Mas **não resolveu o espaçamento denso dos clusters** —
   conferi o range de preço do WIN só nos últimos 90 dias e ainda é
   168.505-203.315 (~35 mil pontos!). O WIN tendenciou forte mesmo dentro
   da janela recente, não só nos últimos 2 anos — o diagnóstico original
   (histórico antigo demais) estava certo mas incompleto.

2. **2.500 candles (~23 dias de pregão) — valor mantido**: resultado bem
   melhor. No caso WIN 173.570, a zona mais próxima passou de `173.462
   [173.370-173.535]` (linha do Jesué **fora** da zona) para `173.610
   [173.550-173.675]` (linha **dentro** da zona). Mesma melhora em
   173.180 → zona `173.125 [173.045-173.190]`, também contendo a linha.
   Apareceu inclusive um vão real no ranking (174.022 → 175.150, nada no
   meio) em vez da telha uniforme de antes.

**Trade-off aceito conscientemente**: os casos WIN 171.540 (03/06) e WDO
~5.026 (18/05) — mais antigos que ~23 dias de pregão contados a partir de
hoje — ficam fora do alcance da janela e não têm mais contexto de
clustering válido pra reconferir. Jesué confirmou que vale o trade-off
(o uso real é o dashboard ao vivo, não backtest histórico distante) e
pediu pra manter 2.500 e fechar por aqui, sem buscar um meio-termo.

`run_backtest.py --atualizar` e depois `--features` rodados para WIN/WDO
(a atualização também trouxe ~2 dias de candles novos desde a última
sessão). Integridade dos parquets conferida antes e depois de cada rodada.

### Verificação — sinal intradiário conferido candle a candle contra `image76.png`
**Módulos:** nenhum código mudou; validação empírica

Pendência que ficou aberta em 2026-07-02: o sinal `_rompimento_hoje` nunca
tinha sido conferido contra o gráfico do diário, só a narrativa estrutural
geral. Conferi as duas zonas próximas das linhas do Jesué (173.570 e
173.180) candle a candle no dia 17/06: às 09:00 (fechamento 172.595, gap de
abertura) vários níveis simultâneos disparam `rompeu_hoje`; às 09:10
(máxima local, bate com a elipse do gráfico em 173.180) o rank mais
próximo confirma `pullback_confirmado_hoje`; às 09:15 o nível 173.570 (rank
1) dispara `rompeu_hoje` pela primeira vez, oscila (`pullback_confirmado_
hoje` 09:25-09:45), e às 09:50 (fechamento 173.805, exatamente onde a
elipse do gráfico marca o rompimento decisivo) dispara `rompeu_hoje` de
novo — bate quase exatamente com a leitura visual. Pendência fechada.

### Calibração — pesos e constantes do sinal intradiário e do `nivel_forte`, via backtest empírico
**Módulos:** `config_backtest.py`

Com o motor rodando bem mais rápido (ver correção de performance abaixo),
foi possível rodar um backtest real sobre os ~113 mil candles de WIN+WDO
em vez de só validar caso a caso:

1. **`PESO_ROMPIMENTO_INTRADAY` e `PESO_ROMPIMENTO_INTRADAY_PULLBACK`**:
   medi a taxa de continuação real após cada evento `rompeu_hoje`/
   `pullback_confirmado_hoje` (fechamento N candles depois ainda do lado
   esperado). Resultado: `pullback_confirmado_hoje` acerta ~63% em 30min
   caindo a ~58% em 2h (n=407); `rompeu_hoje` sozinho acerta ~60% caindo a
   ~52% (coinflip) em 2h (n=461) — confirma a doutrina (reteste confirmado
   > rompimento cru) mas com edge mais modesto do que os pesos antigos
   (12/18) sugeriam. Reduzidos para `PESO_ROMPIMENTO_INTRADAY=9` e
   `PESO_ROMPIMENTO_INTRADAY_PULLBACK=15`, preservando a ordem relativa.
2. **`IDADE_MINIMA_NIVEL_FORTE=100` e `TOQUES_MINIMOS_NIVEL_FORTE=3`**:
   testei várias combinações (idade 100-500, toques 3-10) contra a taxa de
   bounce real em candles próximos da zona (±0.3 ATR). Em todas as
   combinações a diferença entre `forte=True` (~60%) e `forte=False`
   (~59%) ficou dentro do ruído — subir a régua não separa melhor.
   Mantidos sem mudança; o achado em si (idade+toques sozinhos discriminam
   pouco) fica registrado como aprendizado, não como pendência.
3. **`JANELA_MAX_RETESTE=40` e `JANELA_INVALIDACAO_WICK_SWEEP=3`** (FSM
   estrutural, não o sinal intradiário): o FSM estrutural só transiciona
   de status em 2-51 candles de 56-57 mil no dataset atual — amostra baixa
   demais pra recalibrar com confiança. Mantidos sem mudança.

Metodologia completa (contagens, taxas por horizonte) documentada nos
comentários de `config_backtest.py`, ao lado de cada constante.

### Correção — zonas espelho agora acumulam toques próprios
**Módulos:** `features.py`

Limitação conhecida desde a Fase 1 (ver `calcular_zonas_sr`): uma zona
espelho (criada quando um rompimento é retestado e confirmado, papel
invertido) nascia com `toques=0` para sempre — só a `forca_herdada`
(decaída) da zona-mãe entrava no score, e toques reais que ela recebesse
depois de criada eram ignorados. Corrigido: depois de criar a zona
espelho, o código agora procura pivôs confirmados (topo/fundo) que caem
dentro da mesma faixa de preço e ocorrem depois da criação, do tipo
compatível com o papel invertido (resistência rompida vira suporte →
procura fundos; e vice-versa), aplicando o mesmo peso/decaimento
(`_peso_toque`) e as mesmas métricas (rejeição/agressão/impulso) do bloco
original — sem reabrir o clustering, só estendendo o mesmo cálculo.

Validado via debug pontual: nas zonas espelho existentes hoje no WIN
(4 no total), nenhuma teve toque novo ainda — conferido que é porque o
preço genuinamente não voltou a fechar dentro dessas faixas estreitas
(0-6 candles em centenas/milhares) desde a criação, não um bug do código.
O mecanismo está correto e pago a partir de agora conforme mais zonas
espelho acumularem histórico.

### Correção de performance — `calcular_buildup_e_double_pressure` (bug de função duplicada)
**Módulos:** `features.py`

Investigando a pendência de performance (~16.5s do pipeline), encontrei a
causa raiz: **havia DUAS definições da mesma função no arquivo**
(`calcular_buildup_e_double_pressure`, uma logo depois da outra) — um bug
de duplicação, provavelmente um rewrite vetorizado antigo que nunca foi
removido quando uma segunda versão foi escrita por cima. Python usa
silenciosamente a ÚLTIMA definição, então a versão vetorizada (rápida)
nunca rodava de verdade — a que rodava era a segunda, que usava
`df.loc[i, col] = valor` dentro de um loop Python por candle (mais um
loop interno de até 30 iterações por candle, para `buildup_duracao`).
`.loc` escalar em loop é uma das operações mais caras do pandas: medido
isoladamente, essa função sozinha levava ~18-19s sobre ~57 mil candles do
WDO.

Reescrevi como uma única função vetorizada (rolling+shift para a média de
buildup, técnica de streak via `groupby().cumsum()` para `buildup_duracao`
e `dp_streak`) — **preservando exatamente a semântica da versão que
estava de fato rodando** (não a da versão morta, que tinha uma semântica
sutilmente diferente: média incluindo o candle atual em vez de só os
anteriores). Validado byte a byte: rodei a implementação antiga (recriada
à parte) e a nova sobre uma fatia de 5.000 candles do WDO e todas as 6
colunas de saída (`buildup_ativo`, `buildup_duracao`, `buildup_pct_atr`,
`double_pressure`, `dp_streak`, `dp_qualidade`) bateram 100% idênticas.

**Resultado**: a função caiu de ~16-19s para ~0.04s (⁓450x mais rápido).
O pipeline completo (`calcular_todos_features`) caiu de ~27-30s (WIN) e
~91s (WDO, após o fix de re-arm do sinal intradiário) para **~3.4s (WIN)
e ~3.7s (WDO)**. `run_backtest.py --features` reexecutado para os dois
ativos.

### Correção — bug WDO→WIN (root cause encontrada e corrigida)
**Módulos:** `dashboard.py`

Bug crítico reportado em 2026-07-02: trocar pra WDO e o dashboard volta
sozinho pra WIN na atualização seguinte. Investigação anterior (leitura
estática do código) não achou a causa; desta vez, encontrei: os botões
`btn-win`/`btn-wdo`/`btn-correlacao` eram recriados do zero (com
`n_clicks=0` fixo no código) dentro de `construir_hero()`, chamada pelo
callback `atualizar_dashboard` — que dispara A CADA SEGUNDO via
`interval-live`. Cada vez que o hero era reconstruído, os 3 botões
"nasciam de novo" com `n_clicks=0`, resetando o contador de cliques no
browser. Quando o Dash detecta que `btn-win.n_clicks` (por exemplo) mudou
de valor (de 1 para 0, por causa do reset), ele dispara `selecionar_ativo`
de novo como se o usuário tivesse clicado em WIN — revertendo a escolha
de WDO, silenciosamente, no próximo tick de 1s.

**Fix:** os 3 botões saíram de `construir_hero()` (que agora só retorna os
velocímetros) e viraram estáticos em `app.layout` — nunca mais recriados
a cada tick. Um callback novo e dedicado (`atualizar_estilo_ativo_
selecionado`) só troca o `style` (cor) dos botões, disparando apenas
quando `store-ativo` muda de verdade (clique real), nunca no tick de 1s —
preservando `n_clicks` intacto no browser. Nova função `_estilo_botao_
ativo(nome, ativo_atual)` centraliza o cálculo do estilo, reusada tanto no
layout estático inicial quanto no callback. Validado via smoke-test
(import + inspeção do layout/callbacks, sem erro); **não testado num
browser real** (sem ferramenta de automação de browser disponível nesta
sessão) — recomendo confirmar visualmente na próxima vez que abrir o
dashboard.

### UX — feedback visual nos botões + pulso ao trocar de ativo
**Módulos:** `dashboard.py`, `assets/style.css` (novo)

Endereça os dois pontos específicos que Jesué reportou em 2026-07-02
(clique "seco" nos botões, sem indicação de carregamento ao trocar de
ativo) — não a revisão ampla de UX que também foi pedida (essa continua
em aberto, ver "Pendências conhecidas"):

- `assets/style.css` (novo — pasta `assets/` é servida automaticamente
  pelo Dash): `.btn-ativo:hover` (brilho) e `:active` (leve encolhimento,
  simulando o botão "afundando" no clique).
- Novo clientside callback disparado só em `store-ativo` (nunca no tick de
  1s): aplica um pulso de opacidade rápido (`trocando-ativo`, 0.35s) no
  container `conteudo-principal` assim que o clique registra — dá
  confirmação visual imediata de que o clique "pegou", sem esperar o
  round-trip do callback principal.

Não testado num browser real por falta de ferramenta de automação nesta
sessão — só validado que o app importa e registra os callbacks sem erro.

### Novo — calendário econômico (Copom/FOMC/NFP)
**Módulos:** `calendario_economico.py` (novo), `dashboard.py`

Implementa a ideia registrada em 2026-07-02 (não opera notícia, mas
horários de alto impacto distorcem a leitura de S/R — foi um payroll que
"gastou" o sinal intradiário no caso WDO 5.231,50). Pesquisei fontes reais
antes de escrever qualquer data (Jesué pediu explicitamente pra não
confiar só na Finnhub free — que só cobre EUA — e pesquisar outras
fontes):

- **Copom**: calendário oficial 2026 divulgado pelo BCB em 24/06/2025
  (8 reuniões, decisão sempre às 18:30 BRT no 2º dia), confirmado via CNN
  Brasil/InfoMoney/B3/Poder360.
- **FOMC**: as 8 reuniões de 2026 direto de
  `federalreserve.gov/monetarypolicy/fomccalendars.htm`, decisão às 14:00
  ET (convertido pra BRT considerando o DST americano via `zoneinfo` —
  o Brasil não tem mais horário de verão desde 2019, então o offset
  ET→BRT varia ao longo do ano).
- **NFP/payroll**: calculado por fórmula (1ª sexta do mês, 08:30 ET) — com
  uma exceção CONFIRMADA: o relatório de junho/2026 saiu em 02/07/2026
  (quinta), não na "1ª sexta" (03/07), porque 04/07/2026 (Independence
  Day) cai numa sexta — confirmado em
  `bls.gov/news.release/archives/empsit_07022026.htm`. **Essa é
  exatamente a mesma data do payroll que aparece no caso real WDO
  5.231,50** documentado pelo Jesué (susto de notícia 09:30-09:50 BRT em
  02/07/2026) — a exceção pesquisada bate com o caso real, não é
  coincidência, é a mesma notícia.

Nenhuma chamada de rede nova no dashboard — as datas são estáticas,
citadas com fonte em cada bloco de `calendario_economico.py`, e o módulo
retorna vazio (nunca inventa) se um ano não tiver calendário carregado.
`dashboard.py` ganhou um badge no cabeçalho (`status-calendario`): mostra
"⚠ JANELA DE NOTÍCIA: [evento]" em vermelho quando `agora` está a até 30min
de um evento, ou o próximo evento dentro de 48h caso contrário.

**Pendência implícita**: as datas de 2026 precisam ser conferidas/
atualizadas quando o calendário de 2027 for divulgado (Copom normalmente
em meados de 2026, FOMC também) — o módulo já foi desenhado pra isso
(dicionário por ano, retorna vazio se faltar).

---

## 2026-07-02

### Correção — sinal intradiário agora rearma, permitindo múltiplos ciclos por dia
**Módulos:** `features.py`

Corrigido o bug de design encontrado mais cedo hoje (ver análise abaixo):
o sinal intradiário (`_rompimento_hoje`) travava no primeiro ciclo
rompimento→pullback do dia e nunca mais reagia, mesmo que o preço
invalidasse esse rompimento e fizesse outro completamente diferente na
sequência. Reescrevi a máquina de estados em `calcular_zonas_sr` (bloco do
sinal intradiário) pra ter 3 transições em vez de 2:

- `nenhum` → `rompeu_hoje`: fechamento além da zona + corpo mínimo (igual
  antes).
- `rompeu_hoje` → `nenhum` (**novo**): se o preço fecha de volta pra
  dentro/além da zona no sentido contrário ANTES de confirmar o reteste —
  invalida o rompimento (rompimento falso) e libera nova detecção.
- `rompeu_hoje` → `pullback_confirmado_hoje`: toque na zona com rejeição
  acima do limiar (igual antes).
- `pullback_confirmado_hoje` → `nenhum` (**novo**): se o preço reverte de
  vez depois do reteste confirmado (fecha do lado oposto ao papel atual da
  zona), libera nova detecção — é isso que faltava pra pegar uma segunda
  sequência genuína no mesmo dia.

**Validado contra o caso WDO 5.231,50 (01-02/07) que expôs o bug**: rastreei
a timeline do cluster mais próximo (zona 5.228,0-5.232,5) e o resultado bate
quase candle a candle com a narrativa do Jesué:

| Timestamp real | Status calculado | Evento do Jesué |
|---|---|---|
| 02/07 09:00 | `rompeu_hoje` (alta) | abertura acima da zona |
| 02/07 09:30 | `nenhum` (invalidado) | — |
| 02/07 09:35 | `rompeu_hoje` (baixa) | susto do payroll |
| 02/07 10:00 | `pullback_confirmado_hoje` | mercado se acalma |
| 02/07 10:25 | `nenhum` (revertido) | **barra 18 — rompimento falso** |
| 02/07 10:35 | `rompeu_hoje` (baixa) | **barra 20 — "volta com a mesma força"** |
| 02/07 10:55 | `pullback_confirmado_hoje` | próximo da barra 29 (teste) |
| 02/07 11:35 | `nenhum` (revertido) | **barra 31/32 — rompimento real** |
| 02/07 11:40 | `rompeu_hoje` (alta) | continuação |
| 02/07 11:55 | `pullback_confirmado_hoje` | fase da entrada vencedora (barras 43-45) |

Pequeno desvio (~1-2 candles) explicado pela zona calculada (5.228-5.232,5)
não coincidir exatamente com o número redondo que o Jesué usa (5.231,5) —
esperado, não é um problema.

`run_backtest.py --features` reexecutado para WIN/WDO (WDO ficou mais
lento, ~91s vs ~35s antes — mais transições de estado por cluster/dia
agora que o sinal pode ciclar várias vezes; não bloqueante, mas vale
observar se isso escalar mal com mais histórico).

### Verificação — os 3 casos restantes do diário, com a lógica já corrigida
**Módulos:** nenhum código mudou; validação empírica

Rodei o sinal intradiário já corrigido contra os 3 casos limpos do diário
que ainda faltavam conferir. Todos coerentes com a descrição original, mas
nenhum tinha números de barra exatos o bastante pra validação candle a
candle (diferente do caso WDO 5.231,50 acima):

- **WDO 5.180-5.185 (retângulo, 25-26/06)**: sinal cicla ativamente
  (`rompeu_hoje`/`pullback_confirmado_hoje` alternando) bem na abertura de
  26/06 — bate direcionalmente com "rompimento de retângulo na abertura +
  segunda entrada no pullback", mas o resumo desse caso é só texto, sem
  candle numerado.
- **WIN 171.540 (03/06)**: cascata limpa de suportes sendo rompidos e
  reconfirmados conforme o preço cai ao longo do pregão
  (171.802→171.638→171.484→171.330→171.203) — bate bem com "suporte
  rompido no fundo → pullback → continuação".
- **WDO ~5.026 (18/05)**: `rompeu_hoje` às 09:40, `pullback_confirmado_hoje`
  sustentado a partir de ~10:10 — bate bem com "mudança de polaridade".

**Achado que se repete em TODOS os casos reais conferidos até agora**: o
clustering nunca converge numa zona única — sempre aparece como uma grade
densa de clusters próximos (WIN: 173.464/173.619/173.775...; WDO:
5.225,3/5.230,1/5.235,7 e 5.028,4/5.031,6/5.036,6/5.041,5...), nunca uma
linha limpa batendo com o número redondo que o trader desenha. Como já
apareceu em praticamente todo caso, esse é agora o candidato mais forte
pro próximo item de calibração (ver "Pendências conhecidas").

### Cross-check visual — WIN 173.570 (17/06) contra `image76.png`
**Módulos:** nenhum código mudou; validação empírica

Última verificação pendente da rodada de hoje. A imagem mostra duas linhas
de S/R desenhadas à mão pelo Jesué: **173.570** (tocada 2x na tarde de
16/06, ~13:35-14:35) e **173.180** (tocada 1x na noite de 16/06). O preço
cai até uma mínima de sessão ~172.500 no fim de 16/06, e na manhã de 17/06
abre em ~172,8k e sobe ao longo do pregão, retocando 173.180 no candle de
abertura (pico às 09:10) e finalmente rompendo 173.570 de forma decisiva
por volta de 09:50-10:00 (exatamente onde o cursor de tempo do gráfico
está), continuando até a faixa de 173.900+ nos últimos candles mostrados.

Isso bate quase exatamente com a sequência que o sistema calculou (mínima
de abertura, reconquista em cascata de 172.674→172.809→172.962→173.149→
173.295→173.464 subindo, rompimento decisivo por volta de 09:50-10:00,
continuação até 173.975/174.545) — **confirma que a leitura mecânica
estava certa**, não era bug, foi mesmo uma reconquista de múltiplos níveis
durante o rali.

Mas a mesma imagem também reconfirma pela 4ª vez o problema da "grade
densa" de clustering: os clusters do sistema (173.464/173.619/173.775)
nunca caem exatamente nas linhas que o Jesué desenhou (173.570/173.180) —
ficam 50-150 pontos de distância. Ver "Pendências conhecidas" — esse
achado repetido virou a prioridade máxima de calibração.

### Análise — caso real WDO 5.231,50 (01-02/07/2026), bipolaridade confirmada
**Módulos:** nenhum código mudou; validação empírica

Jesué trouxe um estudo próprio (print anotado "BIPOLARIDADE") documentando
o exato caso que eu vinha pedindo desde a validação do WDO 5.056: uma zona
formada no fundo do candle 58 do pregão de 01/07 (13:45, WDO 5m), retestada
no dia seguinte (02/07) com uma sequência completa — susto de payroll
(candles 7-11, 09:30-09:50, derruba o preço até ~5.196 e volta), mercado
acalma (candle 13, 10:00, máxima exata em 5.231), **rompimento falso**
(candle 18, 10:25, fecha em 5.235) rejeitado por um martelo invertido
vendedor (candle 19) e reversão de volta abaixo da zona com a mesma força
(candle 20, fecha 5.224,5), novo teste exato da zona (candle 29, 11:20,
máxima 5.231,5), **rompimento real** (candle 31, 11:30), nova bipolaridade
(candle 36), tríplice teste sem romper (candles 43-45) com entrada
comprada vencedora no toque exato do candle 44 (5.231,5).

Conferi cada número de candle citado contra o parquet real — bateu quase
perfeito (erro de no máximo 1 candle em alguns pontos). A leitura manual
do Jesué é extremamente precisa; tratar narrativas futuras com números de
candle dele como referência de alta confiança.

**Rodando nosso motor contra esse caso, dois achados:**
1. O clustering ainda não converge numa zona única em 5.231,50 — mostra 3
   clusters bem próximos (5.225,3 / 5.230,1 / 5.235,7, ~5pts de distância
   entre si), o mesmo padrão de "grade densa" já visto no WIN. Não
   investigado mais a fundo agora, só reconfirmado como questão em aberto.
2. **Achado novo: o sinal intradiário (`_rompimento_hoje`, implementado
   hoje mesmo) só detecta 1 ciclo de rompimento+pullback por zona por
   dia.** O susto do payroll disparou o ciclo primeiro (rompeu_hoje →
   pullback_confirmado_hoje por volta de 10:00-10:10), e o sinal ficou
   "travado" nesse status pelo resto do pregão — nunca detectou a
   sequência real que o Jesué documentou (rompimento falso candle 18,
   rompimento real candle 31). Jesué pediu pra registrar e adiar o fix
   (ver "Pendências conhecidas" acima).

**Ideia nova registrada** (não iniciada): incorporar calendário econômico
(investing.com ou similar) pra marcar horários de notícia de alto impacto
— foi exatamente o payroll que causou o achado #2 acima. Ver
"Pendências conhecidas" e `MAPA_ESTRUTURA.md`.

### Incidente — `WDO_candles.parquet` corrompido por teste de smoke, recuperado
**Módulos:** nenhum código mudou; incidente operacional

Depois de implementar o sinal intradiário (ver entrada abaixo), rodei um
teste de smoke fazendo `import dashboard` + `carregar_bases()` +
`construir_price_action()` pra validar que o dashboard renderizava sem
erro. Não sabia (até então) que `import dashboard` sozinho já dispara uma
thread de atualização incremental via MT5 em background (ver aviso no
topo deste arquivo) — ela colidiu com a chamada que eu fiz manualmente na
mesma hora, e a escrita concorrente no `WDO_candles.parquet` corrompeu o
arquivo (`ArrowInvalid: Parquet magic bytes not found in footer`). O
`WIN_candles.parquet` não foi afetado.

**Recuperação:** confirmei que não havia processo travado (arquivo com
tamanho estável, sem lock). Reconstruí o WDO do zero via
`merge_fontes.setup_inicial('WDO', buscar_tick_historico=True)` (CSV
completo + MT5 complementar + recálculo de delta a partir de 244k ticks) +
`calcular_todos_features` + `persistencia.salvar_candles` — escopado só ao
WDO, sem tocar no WIN (que estava íntegro). Concluído em ~37s, base
reconstruída com 56.916 candles (2024-07-01 a 2026-07-02 11:00), 142
features incluindo o sinal intradiário novo. Integridade confirmada lendo
os dois parquets de volta com sucesso.

Nenhum dado real foi perdido — o parquet nunca é a única cópia (CSV +
histórico MT5 são a fonte de verdade), mas fica registrado como lição: ver
aviso "Cuidado operacional" no topo deste arquivo.

### Novo — sinal de rompimento intradiário no motor de S/R
**Módulos:** `features.py`, `config_backtest.py`, `dashboard.py`

Depois de corrigir o gate de agressão (ver entrada abaixo) e ainda assim
ver o caso WIN 173.570 (17/06) não disparar o status estrutural
(`aguardando_throwback`/`rompida`), investiguei a fundo e achei que **não
era mais um bug de calibração** — era duas perguntas diferentes sendo
tratadas como uma só. O FSM estrutural (`_status`, ancorado em `posmax` =
último toque de TODA a base) responde "essa zona já foi abandonada pra
sempre?" — pergunta de dias/semanas. O que o Jesué precisa ao vivo é outra:
"rompeu com força HOJE, dentro do pregão de hoje?" — pergunta de minutos/
horas. Confirmei isso reconferindo os 4 casos limpos do diário: todos os 4
mostravam o mesmo atraso de dias/semanas entre o rompimento real e o
reconhecimento do sistema (WIN 173.570: 14 dias; WDO 5.180: 3-4 dias; WIN
171.540: 20 dias; WDO 5.026: 8-10 dias) — sistemático, não exceção. E ao
rastrear o WIN 173.570 candle a candle, vi que o preço realmente fechou de
volta abaixo da zona inteira horas depois, no mesmo pregão — ou seja, o
FSM estrutural não estava "errado", só respondendo a pergunta errada pro
uso ao vivo.

**Decisão (com o Jesué):** em vez de reformar o FSM estrutural (que
funciona bem pra sua pergunta), criar um **segundo sinal, escopado à
sessão do dia**, convivendo com o primeiro.

**Implementado:** novas colunas `sr_{lado}_{r}_rompimento_hoje` ∈
{`nenhum`, `rompeu_hoje`, `pullback_confirmado_hoje`}, calculadas em
`calcular_zonas_sr` — um passe único por cluster sobre todos os candles,
resetando a cada troca de dia de pregão (`datetime.date`), marcando
`rompeu_hoje` quando fecha além da zona com corpo mínimo (`_corpo_ok`) no
próprio dia, e `pullback_confirmado_hoje` quando, no mesmo dia, o preço
retesta a zona com rejeição acima de `LIMIAR_REJEICAO_RETESTE`. Entra no
score de probabilidade (`calcular_prob_niveis_sr`) via dois pesos novos
(`PESO_ROMPIMENTO_INTRADAY=12`, `PESO_ROMPIMENTO_INTRADAY_PULLBACK=18`,
ambos ainda não calibrados). No dashboard, dois badges novos nos cards de
S/R: "ROMPEU HOJE" (amarelo) e "PULLBACK CONFIRMADO HOJE" (verde).

**Bug encontrado e corrigido durante a implementação:** o reset de dia só
gravava um evento `'nenhum'` na timeline do cluster quando o dia que
estava terminando tinha alguma flag ativa — um dia "limpo" (sem
rompimento) não gerava evento nenhum, então a busca por `searchsorted`
podia herdar um status `pullback_confirmado_hoje` de semanas atrás.
Corrigido: o reset agora é incondicional a cada troca de dia.

`run_backtest.py --features` reexecutado para WIN/WDO. Verificado
mecanicamente contra o caso WIN 173.570 (17/06): WIN abriu com gap forte
pra baixo (~172,8k), reclamando vários suportes historicamente próximos de
uma vez (`rompeu_hoje` simultâneo em vários níveis às 09:00), cada um
virando `pullback_confirmado_hoje` conforme o preço subiu recuperando
terreno até 10:20 — narrativa coerente, mas **ainda não comparada
visualmente** com a anotação do Jesué no gráfico (`image76.png`) — ver
"Pendências conhecidas" acima.

### Correção — gate de agressão bloqueava rompimentos reais no motor de S/R
**Módulos:** `features.py`, `config_backtest.py`

Validando o motor de S/R (Fase 1, ver seção 2026-07-01) contra um caso real
do diário de trade (WIN, resistência 173.570, 17/06/2026 — rompimento com
corpo grande + pullback confirmado), descobri que `calcular_zonas_sr`
exigia `_agressao_ok` (delta/volume > `LIMIAR_AGRESSAO_ROMPIMENTO=0.15`)
como gate **obrigatório** (E) junto com `_corpo_ok`, ao lado do rompimento
— isso contradizia o próprio plano original, que definia agressão como
critério **opcional** (reforço, não bloqueio). No caso real, o candle de
rompimento tinha corpo grande (`corpo_atr` até 1.19) mas delta fraco
(~0.03–0.11 num único candle de 5 min, medida ruidosa nesse timeframe) —
o gate descartava um rompimento legítimo.

**Fix:** removido `_agressao_ok` do gate de rompimento em
`calcular_zonas_sr` — agora só `_corpo_ok` (corpo mínimo em múltiplos de
ATR) decide se um fechamento além da zona conta como rompimento. Agressão
continua entrando como reforço no score de probabilidade
(`agressao_media_zona` × `PESO_FLUXO_DELTA` em `calcular_prob_niveis_sr`).
Removida a constante órfã `LIMIAR_AGRESSAO_ROMPIMENTO` de
`config_backtest.py`. `run_backtest.py --features` reexecutado para WIN e
WDO após o fix.

**Achado arquitetural relacionado (não corrigido, decisão consciente de
adiar):** mesmo após o fix acima, o mesmo caso real (WIN 173.570) ainda não
disparava o status `aguardando_throwback`/`rompida` na data certa. Causa:
a busca de rompimento em `calcular_zonas_sr` ancora em `posmax` (o ÚLTIMO
toque do cluster em TODA a base histórica) — um valor que só é conhecido
olhando o futuro inteiro do dataset. Nesse caso, a zona (~172.370–173.845)
continuou sendo tocada/respeitada até 2026-07-01, então o sistema só
reconhece o rompimento nessa data, não em 17/06 como um trader ao vivo
percebeu na hora. É uma limitação estrutural do desenho atual (catálogo de
zonas calculado uma vez sobre o histórico inteiro, não incremental) — não
um parâmetro de calibração. Registrado para revisão futura antes de
declarar a Fase 1 "pronta". Detalhes completos na memória do projeto
(`project_sr_engine_fase1`).

### Análise — extração e mapeamento das 122 imagens do `diario.xlsm`
**Ferramentas:** agentes em background, sem mudança de código

O `diario.xlsm` (109 operações reais, 14/04 a 01/07/2026) embute um print
por linha usando o recurso moderno do Excel "imagem na célula" (rich
value), não desenho clássico. Cadeia de mapeamento reversa:
`xl/richData/richValueRel.xml` (lista ordenada de rIds) →
`xl/richData/_rels/richValueRel.xml.rels` (rId → `xl/media/imageN.png`) —
mapeamento direto confirmado. O atributo `vm=` nas células de
`xl/worksheets/sheet2.xml` (aba "Diario") aponta pro índice da imagem;
coluna M guarda o nível de preço da zona discutida, coluna P a narrativa
completa.

Todas as 122 imagens foram inspecionadas visualmente (1 a 1, sem pular
nenhuma). 120 relevantes (gráficos anotados de candlestick M5 WIN/WDO com
zonas de S/R, MME21, Fibonacci, canais/triângulos); 2 irrelevantes
(`image10.png` = tabela de resumo da corretora, `image122.jpeg` = sticker
decorativo). 7 casos de rompimento+reteste confirmado documentados
explicitamente pelo próprio Jesué nas anotações — só 4 deles são S/R
horizontal puro (dentro do escopo da Fase 1 atual); os outros envolvem
triângulos/trendlines/Fibonacci (fora de escopo, relevantes para uma
futura Fase 2 de confluência).

Essa técnica de extração fica registrada na memória do projeto para não
precisar ser redescoberta em sessões futuras.

---

## 2026-07-01

### Reconstrução do motor de S/R (Fase 1) — multi-nível, zonas, bipolaridade, score
**Módulos:** `features.py`, `config_backtest.py`, `dashboard.py`, `persistencia.py`

O bloco "Price Action" do dashboard só mostrava 1 resistência e 1 suporte
(o mais próximo de cada lado), sem checagem de violação — podia apontar
como "suporte" um nível já rompido há dezenas de candles. Pedido: 3
resistências + 3 suportes mais próximos, critério objetivo de "região
forte" (mais antiga + nunca violada por fechamento (não pavio) + 3+
toques), e uma % numérica de probabilidade de bounce vs. rompimento
combinando os sinais quantitativos já existentes (fluxo/delta, tendência,
Big Players Footprints).

Jesué trouxe depois `spec_suporte_resistencia_forte.md`, doutrina de price
action puro mais rica, que expandiu o escopo: toques não são "quanto mais
melhor" (pico de força ~3 toques, depois decai — consumo de liquidez);
violação distingue wick-sweep de rompimento real; bipolaridade
(pullback/throwback — nível rompido e retestado vira o oposto).

**Decisão de escopo:** faseado. Fase 1 (implementada) = motor operacional
central. Fase 2 (confluência: MME21/MME9/MMA200/Fibonacci/números
redondos) e Fase 3 (multi-timeframe) ficaram como roadmap, não iniciadas.

**Decisão de estrutura de dados:** híbrida — colunas escalares por rank no
DataFrame principal (`sr_{lado}_{rank}_*`, parquet-friendly, consumidas
pelo dashboard e `ai_analyst.py`) + catálogo de zonas em memória (não
persistido, reconstruído a cada `calcular_todos_features`, guarda o
bookkeeping mais rico da máquina de estados de bipolaridade).

**Implementado:**
- `config_backtest.py`: novo bloco de parâmetros (clustering, decaimento de
  toques, janelas de rompimento/reteste/wick-sweep, pesos do score).
- `features.py`: `_clusterizar_pivos_sr` (clustering compartilhado),
  `calcular_sr` (reescrito — zona + preço de referência ponderado +
  ranking), `calcular_zonas_sr` (novo — rejeição/decaimento/impulso/
  agressão por toque, máquina de estados de bipolaridade, zonas espelho,
  re-ranking), `calcular_prob_niveis_sr` (novo — score 0-100%).
- `dashboard.py`: `extrair_metricas` monta listas `niveis_sr_acima/abaixo`;
  `construir_price_action` redesenhado com 3 cards por lado, badges (REGIÃO
  FORTE, VIOLADO, AGUARDANDO PULLBACK/THROWBACK, ZONA ESPELHO).
- `persistencia.py`: fix — `df.attrs` (usado como "correio" entre
  `calcular_sr` e `calcular_zonas_sr`) quebrava `to_parquet` (tenta
  serializar attrs em JSON, falha com arrays numpy). Fix: limpa attrs antes
  de salvar.

**Dois bugs de lookahead encontrados e corrigidos** validando contra um
caso real do Jesué (WDO ~5.056, resistência que segurou o pregão inteiro
de 02/06/2026, rompida em 03/06):
1. Gate de validade usava `posmax` (último toque de TODA a base) — um
   nível retestado meses depois parecia "ainda não válido" logo após seus
   primeiros toques. Fix: novo `validade_idx` = 2º toque cronológico (ou
   1º, se só houver um).
2. Busca de violação/rompimento ancorada no PRIMEIRO toque do cluster —
   uma oscilação normal no meio da vida do nível (reclamada por toques
   posteriores) era marcada como "o" rompimento. Fix: ancorar no ÚLTIMO
   toque conhecido (`posmax`) em vez do primeiro.

### Fix — loop de erros no dashboard (`UnboundLocalError`)
**Módulo:** `dashboard.py`

Após mudanças no módulo "Big Players Footprints" (Tape/Book), o callback
principal falhava a cada segundo (`_safe_int`/`_safe_bool` usadas antes da
própria definição dentro de `extrair_metricas` — hoisting de função
aninhada não existe em Python), deixando a página presa em "Carregando".
Fix: mover as definições das duas funções para logo após `m = {}`, antes
do primeiro uso.

---

## 2026-06-30 (e anteriores)

Trabalho de infraestrutura de dados anterior a este changelog — reconstruído
por inspeção de código/comentários, não por observação direta:
- `csv_loader.py`: parsing dos CSVs do ProfitChart, corrigindo dtype
  (`string` vs `object` no pandas moderno) e escala do WIN (CSV vem em
  milhares de pontos com 3 casas decimais). Validado contra dados reais do
  MT5.
- `mt5_loader.py`: carga complementar via MetaTrader5 — candles do período
  mais recente que o CSV não cobre (sempre o contrato vigente) e ticks com
  flag de agressor via `copy_ticks_from()` com retry (`copy_ticks_range()`
  falhou de forma consistente em teste real).
- `merge_fontes.py`: fusão CSV+MT5 numa série rastreável única, com dois
  modos — `setup_inicial` (pesado, uma vez, 2 anos de tick) e
  `atualizar_incremental` (leve, diário, só o que é novo).
- `features.py` v2: reescrita de gargalos O(n²) — `calcular_sr` (clustering
  do zero a cada candle → clusters calculados uma vez + lookup vetorizado)
  e `calcular_tendencia` (filtro de DataFrame a cada candle → busca binária
  via `searchsorted`).

Vários scripts de diagnóstico/investigação pontuais na raiz do projeto
(`investiga_*.py`, `diagnostico_*.py`, `teste_*.py`, `mapeamento_*.py`) e
em `old/` documentam essa fase de depuração de dados (timezone, paginação
MT5, limites de `copy_ticks_from`, deslocamento de 3h) — não listados
individualmente aqui; ver `MAPA_ESTRUTURA.md` para o inventário completo.
