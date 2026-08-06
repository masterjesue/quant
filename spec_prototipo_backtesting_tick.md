# Protótipo de Backtesting Tick a Tick — plano completo (2026-08-03)

**Status: planejado, pronto pra execução numa sessão nova e dedicada.**
Este arquivo é autossuficiente — uma sessão de Claude Code sem nenhum
contexto desta conversa deve conseguir implementar a partir daqui. O
RELATÓRIO inicial (análise sobre uma amostra de trades, pra o Jesué
estudar enquanto este módulo é construído) foi entregue separadamente,
nesta mesma sessão que escreveu este plano — não é parte do trabalho
pendente abaixo.

## Contexto e objetivo

Jesué quer estudar a qualidade real das suas operações históricas (228
trades, fev/2026 a ago/2026) no nível de TICK, não só candle M5,
reconstruindo o que aconteceu entre a entrada e a saída de cada trade,
pra responder 6 perguntas que o sistema hoje não responde:

1. **Qualidade das entradas e saídas** — o preço obtido foi bem posicionado
   em relação ao movimento real de tick a tick dentro do próprio candle?
2. **"Violinos"** — movimentos de ida-e-volta rápidos (whipsaw) ao redor da
   entrada/saída que sugerem ruído em vez de sinal real.
3. **Operações corretas mas estopadas por stop curto/volatilidade** — a
   tese estava certa (preço foi na direção esperada depois), mas o stop
   foi atingido antes por estar curto demais pra volatilidade do momento.
4. **Estudo de breakevens** — o momento em que o stop foi movido pra
   proteção foi cedo demais, tarde demais, ou certo?
5. **Simulação "e se"** — quantas operações teriam sido vencedoras/
   perdedoras com stop/breakeven ajustados.
6. **Dashboard sob cenários alternativos** — como as métricas agregadas
   ficariam sob decisões/setups diferentes.

Pedido explícito de arquitetura: o módulo deve seguir os padrões de UX
TradeZella já estabelecidos no resto do sistema desde o início, pra não
ter retrabalho visual depois — ver "Padrões de UI a reaproveitar" abaixo,
com referências de arquivo:linha concretas.

## Achados da pesquisa que restringem o design (já investigado, não repetir)

- **Tick histórico não é persistido hoje.** `mt5_loader.
  buscar_ticks_periodo` (mt5_loader.py:431-515) busca do MT5 sob demanda
  (usado hoje só pro cálculo de delta/agressor em `merge_fontes.py`) e
  descarta o resultado depois de calcular `vol_comprador`/`vol_vendedor`.
  Não existe `salvar_ticks`/`carregar_ticks` em `persistencia.py`. Candles
  M5 SÃO persistidos (`WIN_candles.parquet`/`WDO_candles.parquet`, 58k+
  linhas cada, via `persistencia.salvar_candles`/`carregar_candles`,
  persistencia.py:194-215) — isso já existe e não muda.

- **`buscar_ticks_periodo` tem 2 correções empíricas importantes já
  aplicadas** (não redescobrir): (1) MT5 soma 3h a um `datetime` naive
  passado pra `copy_ticks_from` — corrigido subtraindo
  `DESLOCAMENTO_TIMEZONE_MT5` (mt5_loader.py:164) antes de cada chamada;
  (2) passar um `pandas.Timestamp` em vez de `datetime.datetime` nativo
  pra `copy_ticks_from` faz a paginação voltar o batch ANTERIOR
  incorretamente — sempre usar `.to_pydatetime()` (mt5_loader.py:468-469).
  A saída de `buscar_ticks_periodo` tem só `['datetime', 'price',
  'volume_ticks', 'agressor', 'flags']` — bid/ask/time_msc/volume_real são
  descartados após uso interno; não é preciso recuperar esses campos pro
  escopo atual (só importariam pra simular o gatilho exato do stop no
  book, fica pra depois se o Jesué pedir).

- **Custo de backfill é real e documentado**: `diagnostico_performance_
  ticks.py` (raiz do repo) mediu ~166k ticks/dia em 98s (~1.700 ticks/s)
  — 2 anos de histórico ficariam em ~13h de busca. **Backfill de tick tem
  que ser decoupled da atualização diária de candles** (não pode entrar no
  ciclo de 30s do dashboard nem no `--atualizar` de todo dia).

- **Profundidade real de retenção de tick — CONFIRMADO nesta sessão
  (2026-08-03), testado direto contra o MT5 real** (não repetir o teste,
  só usar o resultado): tick fica disponível só pra **~4 meses pra trás**
  a partir de hoje — `WINM26` (contrato de junho) retorna tick normalmente
  a partir de ~01/04/2026 em diante; `WINJ26` (contrato de abril) e
  `WING26` (contrato de fevereiro, usado nos trades mais antigos da base)
  retornam **zero ticks** em qualquer data testada, incluindo dias com
  trade real confirmado (`last_error=(1,'Success')` — a chamada "funciona"
  mas não há dado). **Isso é uma limitação de TICK especificamente, não de
  candle**: `mt5.copy_rates_from('WING26', ...)` no mesmo dia/símbolo
  retorna candles M5 normalmente — o histórico de candle é retido por
  muito mais tempo que o de tick pelo terminal/corretora. Ou seja: dos 228
  trades, só os de ~abril/2026 em diante têm QUALQUER chance de
  reconstrução tick-precisa (isso é mais amplo que os 19 elegíveis por
  ticket+stop+timestamp da Parte 1 — aquele filtro é mais restritivo que
  só a disponibilidade de tick). Trades de fevereiro/março só podem ser
  analisados no nível de candle M5, nunca tick — não adianta tentar
  backfill de tick pra esse período, a corretora não guarda mais.

- **Alinhamento preciso trade↔tick tem uma fonte certa, não é
  `datetime_entrada`/`datetime_saida` do registro do Diário** (esses vêm do
  heartbeat de sync a cada 6s e podem estar `saida_estimada=True`, ou seja,
  aproximados). O jeito certo:
  1. `tickets_finalizados.parquet` (mapa permanente `id_trade → ticket(s)
     MT5`, escrito em `finalizar_posicao`/`finalizar_par_hedge`, ver
     `diario_trade._marcar_tickets_finalizados`, diario_trade.py:3486-3507,
     e `_mapa_tickets_finalizados`, diario_trade.py:3568-3577) — é a fonte
     de verdade permanente, não confundir com `COLUNAS_RECONCILIACAO`
     (diario_trade.py:991-1001), que é um cache recomputável e só cobre
     uma janela rolante de 30 dias por padrão (`reconciliar_com_mt5`,
     diario_trade.py:2614-2748) — não usar esse pros 228 trades históricos.
  2. Com o(s) ticket(s) do trade em mãos, chamar `mt5_loader.
     buscar_execucoes_periodo(inicio, fim)` (mt5_loader.py:552-590, wraps
     `mt5.history_deals_get`) pra pegar os deals reais.
  3. `mt5_loader.agrupar_posicoes(df_deals)` (mt5_loader.py:593+) agrega
     deals em entrada/saída com preço médio ponderado por volume (cobre
     fill parcial) — os timestamps daqui são o instante EXATO de entrada/
     saída, não a aproximação do journal.
  4. Pra trades que são PAR DE HEDGE (2 posições opostas MT5 viram 1 trade
     líquido no Diário), tem 2 tickets/position_ids a buscar tick, não 1 —
     ver `detectar_pares_hedge`/`_parear_hedge_deals`
     (diario_trade.py:3790-3949, diario_trade.py:2440).

- **Limitação real, SEM solução retroativa**: o sistema nunca registrou
  histórico de QUANDO um stop foi movido pro breakeven — só o estado
  final. `stop_pontos_preliminar`/`sl`/`tp` são sobrescritos in-place em
  `posicoes_abertas.parquet` (editar_rascunho, diario_trade.py:4046-4051)
  sem versionamento; ao finalizar, o trade herda só o ÚLTIMO valor
  (diario_trade.py:4257-4288). `tipo_saida='breakeven'` é auto-derivado
  (`derivar_tipo_saida`, diario_trade.py:3428-3449) comparando o preço de
  saída ao `sl`/`tp` FINAL — diz QUE fechou perto da entrada, nunca QUANDO
  a proteção foi armada. **Não inventar esse dado pros 228 trades já
  fechados.** Pra trades a partir de 2026-08-03: **já implementado** — ver
  "Parte 2" abaixo (log de eventos de ordens pendentes), não
  `notas_cronologicas` como cogitado originalmente aqui.

- **Cobertura desigual da base**: `diario_trade.r_multiple_trade`
  (diario_trade.py:1293-1305) já documenta que ~30% dos 228 trades não têm
  `stop_pontos` preenchido (R-multiple incalculável); `duracao_minutos_
  trade` (diario_trade.py:1308-1318) documenta ~63% sem
  `datetime_entrada`/`datetime_saida` (trades antigos/importados via
  `fonte_registro='importado_xlsm'`). Só um SUBCONJUNTO real dos 228 é
  candidato a reconstrução tick-precisa — não prometer cobertura de 100%.

- **`COLUNAS_DIARIO`** (diario_trade.py:952-986) tem os campos relevantes:
  `preco_entrada`, `datetime_entrada`, `preco_saida`, `datetime_saida`,
  `tipo_saida`, `stop_pontos`, `alvo_pontos`, `resultado_pontos`. **Não
  existe** campo `breakeven_pontos` na tabela de trades (só existe no
  cadastro de Gerenciamento de Risco, que é por ativo/estratégia, não por
  trade individual).

- **"Backtesting" no menu já existe e está vazio.** `_GRUPOS_MENU`
  (dashboard.py:2117-2128) já tem a entrada `('back', 'Backtesting',
  'tabler:player-track-prev', None)`; o painel correspondente
  (`painel-back`, dashboard.py:4305-4309) é hoje só
  `_placeholder_aba('Backtesting')` — é o lar natural do módulo, não criar
  aba nova. Não existe função `_construir_backtesting` ainda (Quant Lab
  também não tem uma função dedicada — é construído inline como bloco
  estático em `dashboard.py`, mesmo padrão a seguir).

## Padrões de UI a reaproveitar (pesquisado, pronto pra copiar — sem retrabalho)

- **Card/seção**: `_bloco_diario` (diario_app_classico.py:560-574) /
  `_bloco_tradezella` (dashboard.py:2222-2239) — mesmo shell:
  `dmc.Card([titulo, children], withBorder=True, shadow='sm', radius='lg',
  p='lg', mb='md', className='tz-card')`. Sem helper único compartilhado
  entre os dois arquivos (convenção deliberada do projeto: sem import
  cruzado entre `dashboard.py`/`diario_app_classico.py`/módulo novo) —
  duplicar localmente no arquivo novo, igual o resto do projeto já faz.
  Título no padrão `_bloco_diario`: `dmc.Text(titulo, size='sm', fw=600,
  tt='uppercase', style={'letterSpacing':'0.05em','color':C['gold'],
  'marginBottom':'10px'})`.
- **Grid de resultados**: `dag.AgGrid`, molde exato em `_grid_historico`
  (diario_app_classico.py:1943-2046) — `dashGridOptions={'domLayout':
  'autoHeight', 'pagination': True, 'paginationPageSize': 20,
  'paginationPageSizeSelector': [10,20,50,100]}`, `className='ag-theme-
  alpine'`. **Bug crítico já resolvido a reaproveitar**: `cellClicked`
  NUNCA inclui `data` nesta versão do dash-ag-grid — usar `rowIndex` +
  `State('grid-id', 'rowData')` pra recuperar a linha clicada (ver
  `atualizar_detalhe_historico_grid`, diario_app_classico.py:4255-4301);
  `getRowId` foi tentado e revertido (colapsou a grid pra 1 linha); toda
  coluna clicável precisa `sortable: True` mesmo que o sort não faça
  sentido (senão `cellClicked` não dispara nessa versão).
- **Detalhe por trade**: `dmc.Drawer` molde em `_conteudo_detalhe_trade`
  (diario_app_classico.py:2381-2535) — shell do drawer
  (diario_app_classico.py:1839-1851): `size='1160px'`, `offset=16`,
  `radius='md'`, `shadow='xl'`, `overlayProps={'opacity':0.15,'blur':2}`,
  `transitionProps={'duration':220,'transition':'slide-left'}`. Estrutura
  do conteúdo: hero visual no topo (aqui, o GRÁFICO de tick reconstruído,
  no lugar do print), cabeçalho colorido com P&L, depois seções via
  `_secao_detalhe_trade(titulo, conteudo)` (diario_app_classico.py:2538-2553
  — rótulo dourado maiúsculo + borda superior) com campos em grid
  responsivo (`gridTemplateColumns:'repeat(auto-fit, minmax(160px,1fr))'`).
  Conteúdo do Drawer NÃO usa `_bloco_diario`/card (o Drawer já dá o frame).
- **Gráfico de tick**: Lightweight Charts (`assets/lwchart.js`), mesmo
  padrão do Price Action — `window.SQ_CHART` global mutado via
  `clientside_callback`, dado alimentado como `{'time': epoch_segundos,
  'open','high','low','close'}` (ver `metricas.candles_para_grafico`).
  Linhas horizontais de entrada/stop/alvo/breakeven: estender
  `desenharNiveisSR` (linhas de S/R já usam `LightweightCharts.LineStyle`
  tracejado/sólido conforme força/pivô) — mesmo mecanismo. **Marcador
  pontual num tick exato (seta/círculo de entrada/saída) NÃO tem
  precedente no repo** — `setMarkers`/`createSeriesMarkers` nunca foi
  usado; é código genuinamente novo a escrever.
- **Ícones**: `DashIconify(icon='tabler:...')` — duplicar localmente os
  helpers já padronizados (idênticos em ambos arquivos hoje):
  ```python
  def _icone(nome_tabler, tamanho=13, cor=None, **kwargs):
      if cor: kwargs['color'] = cor
      return DashIconify(icon=nome_tabler, width=tamanho, **kwargs)

  def _texto_com_icone(nome_tabler, texto, cor=None, tamanho=13):
      return [_icone(nome_tabler, tamanho=tamanho, cor=cor), f' {texto}']
  ```
  Larguras típicas 13-20px; nunca passar `cor=None` explicitamente pra um
  prop DMC de cor (`c=None` vira `null` no JSON e quebra o parser Mantine
  — só passar quando houver valor real).
- **Cores/fontes**: dict `C` (`C['gold']`,`C['green']`,`C['red']`,
  `C['text2']`, todos `var(--x)` CSS) e `FONTS` (`display`/`mono`/`body`) —
  duplicar localmente, mesma convenção. `PALETA_GAUGE` (hex literal) só é
  necessário se o módulo usar Plotly (`dcc.Graph`) além do Lightweight
  Charts — Plotly SVG não resolve `var(--x)`.

## Parte 1 — Persistência de tick (implementar primeiro)

- Armazenamento **particionado por dia**, não um arquivo monolítico
  regravado inteiro como os candles (na escala de tick isso não escala —
  um dia de pregão fechado nunca muda, então nunca precisa ser reescrito):
  `data/base_processada/ticks/{ativo}/{YYYY-MM-DD}.parquet`.
- `persistencia.py`: novas funções `_caminho_ticks_dia(ativo, dia)`,
  `salvar_ticks_dia(ativo, dia, df)`/`carregar_ticks_dia(ativo, dia)`,
  usando `_salvar_parquet_atomico` (mesmo idioma dos candles,
  persistencia.py:90-115) — escreve uma vez, nunca mais toca no arquivo
  daquele dia.
- Novo flag em `run_backtest.py` (ex.: `--backfill-ticks --dias N` ou
  `--backfill-ticks --de DATA --ate DATA`) — decoupled da cadência normal
  de `--atualizar`, rodar sob demanda/manualmente, nunca automático no
  ciclo do dashboard.
- Primeiro backfill: escopar só os DIAS em que houve trade real (não os 6
  meses inteiros) — usar `diario_trade.listar_trades()['data_op'].unique()`
  como a lista de dias a buscar, cruzando com `tickets_finalizados.parquet`
  pra saber quais têm ticket exato.

## Parte 2 — Motor de análise

Módulo novo (ex.: `backtesting_tick.py`), funções puras sobre tick+trade já
carregados (sem I/O MT5 dentro do cálculo em si — I/O só na etapa de
backfill acima):
- Qualidade de entrada/saída (preço obtido vs. melhor preço disponível
  numa janela em torno do timestamp exato de entrada/saída).
- Detecção de violino — limiar de amplitude/tempo a CALIBRAR com dados
  reais quando esta sessão começar (não travar em cima de um número
  chutado agora).
- Correto-mas-estopado — stop batido, preço reverte na direção esperada
  dentro de N minutos/pontos — threshold também a calibrar com o Jesué.
- Timing de breakeven — **já implementado prospectivamente em 2026-08-03**
  (só aplicável a trades a partir de agora, nunca aos 228 já fechados):
  `diario_trade.detectar_eventos_ordens_pendentes`/`sincronizar_ordens_
  pendentes` logam em `data/base_processada/eventos_ordens_pendentes.parquet`
  (append-only) toda vez que uma ordem pendente aparece/muda de preço/some,
  comparando o snapshot de cada ciclo de sync com o anterior. Achado que
  motivou esse desenho: a conta do Jesué é hedging e NÃO usa SL/TP nativo
  do MT5 (`sl`/`tp` sempre `None`, confirmado empiricamente em posições e
  ordens reais) — o único sinal objetivo é a ordem pendente (limitada/stop)
  que faz esse papel. Ler via `diario_trade.listar_eventos_ordens_
  pendentes()`. Esta sessão futura deve USAR esse log (já vai ter semanas/
  meses de dado acumulado por essa data), não reimplementar a captura.
- Simulação "e se" — recalcular resultado com stop/breakeven alternativos
  sobre a série de tick já carregada.

## Parte 3 — Módulo UI

Ver "Padrões de UI a reaproveitar" acima. Home: aba Backtesting
(`painel-back`, hoje placeholder). Fluxo esperado: grid de trades
(resultado real vs. simulado) → clique abre Drawer com gráfico de tick +
linhas de entrada/stop/alvo + veredito (violino/estopado-mas-certo/etc).

## Verificação

- `python -c "import ast; ast.parse(...)"` em todo arquivo tocado.
- `testes/auditoria_ids_callbacks.py` = 0 acoplamentos.
- Suíte `testes/teste_*.py` completa (esperar só a falha conhecida e não
  relacionada em `teste_historico_filtros_estatisticas.py`).
- **Regra reforçada após incidente real de 2026-08-03** (ver memória
  `feedback_producao_oficial_nunca_testar_direto`): toda escrita de teste
  SEMPRE contra caminho de scratch (monkeypatch de `persistencia.py`),
  NUNCA contra `data/base_processada/` direto — um teste real já apagou
  check-ins reais do Jesué por causa disso.
- Verificação ao vivo no navegador da aba Backtesting depois de reiniciar
  `dashboard.py`/`diario_app_classico.py` — igual ao padrão já seguido em
  toda a sessão anterior.
