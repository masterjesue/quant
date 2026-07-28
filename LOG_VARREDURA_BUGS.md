# Log de Varredura de Bugs — Super Quant

Log corrido de uma varredura manual (via Chrome conectado + MT5 real) pedida
por Jesué em 2026-07-21: "Faça uma varredura completa pelo sistema Super
Quant e procure por bugs operacionais. Teste tudo." Começou pela aba Posição
do Diário, aproveitando uma operação real aberta na conta (BTG Pactual,
login 13372735, conta REAL — não demo).

**Status (2026-07-21, mesmo dia): plano de correção completo escrito e
implementado.** Ver `C:\Users\jesue\.claude\plans\modular-herding-salamander.md`
pro plano detalhado (causa raiz de cada bug, com arquivo:linha) e a seção
"Correções aplicadas" no fim deste arquivo pro que foi de fato mudado,
testado (sempre em parquet de scratch, nunca direto em produção — exceção:
limpeza pontual do órfão real já confirmado) e o que ainda depende de
confirmação manual do Jesué.

Convenção: cada achado tem status 🔴 crítico / 🟠 real, precisa de correção /
🟡 investigar mais / 🟢 cosmético, cruzado sempre que possível contra dado
real do MT5 (`positions_get`) ou contra o backend via POST direto ao
`/_dash-update-component` (nunca só "achei que parecia errado").

## Resumo executivo (ordem de prioridade pro plano de correção)

1. 🔴 **Exposição líquida WDO reportada como zerada quando não está** —
   reproduzido IDENTICAMENTE nos dois skins (clássico e '98) → bug na
   camada compartilhada (`diario_trade.py`/reconciliação de posições, não
   em código de UI). Maior risco real: some com a visibilidade de posição
   aberta no relatório de risco.
2. 🟠 **Contagem "5 WIN" parece 1 unidade abaixo do esperado** (8 posições
   cruas − 4 usadas em 2 pares deveriam sobrar 6 unidades, não 5).
3. 🟠 **Registro órfão em "PRONTA PRA FINALIZAR — WIN"** (176925/176775,
   não bate com nenhuma posição real aberta) — mesma classe de bug já
   corrigida antes ("hedge orphans").
4. 🟠 **Histórico × Posição não batem** pros mesmos 3 pares WIN de hoje
   (+1,00/-30,00/+1,00 vs +1,00/-30,00/-30,00) — reproduzido nos 2 skins.
5. 🟠 **Botão WIN/WDO em Posição não responde a clique de mouse
   simulado** (só via `.click()` JS direto) — precisa confirmação manual
   do Jesué.
6. 🟡 **Sub-abas do Diário não respondem via iframe do dashboard
   principal** — precisa confirmação manual do Jesué (pode ser limitação
   da automação, não do app).
7. 🟡 **Padrões: texto de resultado não atualiza** após rodar detecção —
   reproduzido nos 2 skins.
8. 🟡 Performance ruim (8-30s) na 1ª troca pra aba Posição (lazy-load de
   JS assíncrono).
9. 🟡 '98 não tem janela de Backtesting (assimetria entre skins) —
   confirmar se é esperado.
10. 🟢 Bug cosmético de acessibilidade (id JSON cru em `label[for=...]`).
11. 🟢 2 tags emocionais cadastradas em minúsculo (inconsistência de
    cadastro, não de código).

---

## Sessão 1 — 2026-07-21, aba Posição (diario_app_classico, porta 8053)

### 🔴 Exposição líquida reportada como zerada quando havia par WDO não classificado

Ao trocar pra sub-aba WDO dentro de Posição, o painel mostrou:

> "5 posição(ões) rastreada(s) — 5 WIN, **0 WDO**." · "**Exposição líquida
> zerada em todos os contratos — nenhum risco de mercado residual**." ·
> "Nenhuma posição WDO aberta no momento."

Conferido ao vivo via `positions_get` do MT5 no mesmo instante (conta real):
10 posições abertas — 8 WIN + **2 WDO** (venda ticket 308287712 @ 5083,5 +
compra ticket 308292132 @ 5088,5, a compra claramente um SL/TP-oposto-virou-
posição-nova da venda, o mesmo padrão de hedge que o app já reconhece e
mostra pros pares WIN). Reconfirmado 7s+ depois (passou do ciclo do
`interval-em-andamento` de 6s) — resultado idêntico, não era só atraso de
1 tick.

Nesse caso específico o par WDO é ~neutro de risco de verdade (a diferença é
só a distância de pontos entre as duas entradas), mas a lógica que deveria
ter detectado esse par e mostrado como "pronta pra finalizar" (igual faz com
WIN) simplesmente não rodou — o contador foi pra 0 e o texto afirmou "nenhum
risco residual" quando havia 2 posições reais abertas. Em um cenário onde o
par NÃO fosse coincidentemente neutro, isso esconderia exposição real de
Jesué. Prioridade de correção: alta.

Também reparei, de passagem, que a contagem "5 WIN" parece estar 1 unidade
abaixo do esperado: 8 posições WIN cruas, 2 pares mesclados (ver abaixo)
consomem 4 delas → deveriam sobrar 4 individuais + 2 pares = 6 unidades, não
5. Não tive tempo de isolar a causa exata — só registrado aqui, precisa
investigar a função de contagem/pareamento (provavelmente em
`diario_trade.detectar_pares_hedge` ou equivalente usado por
`diario_app_classico.py`).

### 🟠 Registro órfão em "PRONTA PRA FINALIZAR — WIN"

Terceiro item da lista: `Entrada 176925 · Saída 176775 · Pts -150 · R$ -30.00`
marcado "⚠ FECHADA — AGUARDANDO CONFIRMAÇÃO" (diferente dos outros dois, que
vêm com "✓ FECHADA (par hedge — pronta pra finalizar)"). Esses preços não
correspondem a NENHUMA das 10 posições reais abertas hoje (todas entre
173865-174705 pro WIN). `history_deals_get` do MT5 pro range 2026-06-01 a
2026-07-22 (com e sem filtro de símbolo) voltou vazio — não consegui
confirmar de onde vem esse preço via MT5 diretamente (pode não ser
relacionado a deals recentes, ou o filtro de data/símbolo da minha consulta
não bateu com o formato esperado pelo servidor MT5 MCP — não tive tempo de
depurar isso à parte). Suspeita: resíduo órfão do mesmo tipo já corrigido
antes no projeto (ver memória `project_diario_trade_module` — "hedge
orphans"). Precisa achar de onde esse registro é lido (provavelmente um
cache/parquet com um par nunca limpo) e decidir se deve ser descartado.

### 🟠 Botão de alternância WIN/WDO não responde a clique de mouse simulado

Dentro da aba Posição, clicar (via automação, coordenada confirmada correta
por `getBoundingClientRect`/`elementFromPoint` — o botão não está coberto
por nada, `pointer-events:auto`, não disabled) no botão `btn-posicao-ativo-
WDO` não mudava a sub-aba. Disparar `document.getElementById('btn-posicao-
ativo-WDO').click()` via JS funcionou imediatamente. Isolar se é bug real do
app (React/Dash não registrando o clique físico nesse botão específico) ou
limitação da minha ferramenta de automação exigiria testar com mouse de
verdade — **pedido pra Jesué**: confirmar se esse botão responde normalmente
ao clique manual dele.

### 🟡 Sub-abas do Diário não respondiam quando acessadas via iframe do dashboard principal

No dashboard principal (`localhost:8050` → aba DIÁRIO, que embute
`diario_app_classico` num `<iframe src="http://127.0.0.1:8053/">`), clicar
em qualquer sub-aba (Posição/Histórico/Estratégias/Novo Trade, todas
testadas) não trocava o conteúdo, mesmo esperando 10s+. O MESMO clique,
nas MESMAS coordenadas relativas, funcionou normalmente acessando a porta
8053 direto (fora do iframe). Provável limitação de como minha ferramenta
de automação entrega cliques dentro de um iframe cross-origin (8050 vs
8053 são origens diferentes por causa da porta) — **não confirmado como bug
real do app**. Peço confirmação manual de Jesué: ao usar o dashboard
principal de verdade (seu mouse, seu Chrome), as sub-abas do Diário
respondem normalmente?

### 🟡 Performance ruim na primeira troca de sub-aba

Primeira troca pra "Posição" (a partir de "Estatísticas") trava a página por
8-30s. Rede confirma a causa: carregamento de 3 chunks JS assíncronos do
Dash na primeira vez (`async-dropdown.js`, `async-slider.js`, `async-
upload.js`, ~sob demanda porque a aba Posição é a primeira a renderizar um
Dropdown/Slider/Upload na sessão). Depois da primeira vez as trocas de
sub-aba ficam rápidas. Vale considerar pré-carregar esses chunks no boot da
página em vez de lazy-load na primeira interação, se a demora incomodar no
uso real.

### 🟢 Bug cosmético — crash da árvore de acessibilidade da extensão do Chrome

Um id de componente Dash em formato dict (`{"index":"...","type":"timeline-
toggle"}` — o toggle "Ano Inteiro" do calendário do Diário) é usado cru
dentro de um atributo `label[for=...]`. As aspas internas não escapadas
quebram qualquer código que tente montar um seletor CSS tipo
`label[for="..."]` a partir desse atributo (a extensão de acessibilidade do
Chrome faz isso e lança exceção repetidamente). Não afeta o uso normal do
app, só ferramentas de terceiros que tentem inspecionar a página dessa
forma. Baixa prioridade.

---

## Sessão 1 (cont.) — demais sub-abas do Diário

### 🟠 Histórico: lista "PENDENTE" com valores em ordem diferente da aba Posição

A lista "PENDENTE" no topo do Histórico mostra, pros 3 trades WIN de hoje
(2026-07-21): `R$ +1.00`, `R$ -30.00`, `R$ +1.00` (estimado). Mas a aba
Posição, na mesma sessão, mostrou pros mesmos 3 pares: `R$ +1.00`,
`R$ -30.00`, `R$ -30.00` (o 3º sendo o registro órfão 176925/176775 já
citado acima). As duas telas deveriam estar mostrando os MESMOS 3 pares —
os valores não batem 1:1 entre as duas visões. Não tive tempo de isolar se
é a mesma fonte de dados renderizada diferente ou duas fontes
desincronizadas — precisa investigar mais.

Também confirmado: a lista PENDENTE do Histórico **não tem nenhuma linha
WDO de 2026-07-21** — reforça o achado acima de que o par WDO de hoje
(venda 5083,5 + compra 5088,5) não está sendo rastreado em NENHUM lugar do
app, nem em Posição nem em Histórico.

### 🟡 Padrões: texto de resultado não atualiza após rodar a detecção

Cliquei em "Detectar padrões" — o botão em si reportou "Concluído — 0
padrões detectados", mas o texto abaixo continuou "Nenhum resultado ainda —
clique em 'Detectar padrões'." (o placeholder de antes de rodar). Parece
que o callback que atualiza o texto de resultado não está sincronizado com
o que roda a detecção — cosmético, mas confunde (parece que não rodou).

### 🟢 Sem achados — Estratégias, Novo Trade, Bem-Estar

As 3 sub-abas restantes carregaram e renderizaram normalmente (CRUD de
estratégias/presets R:R/tags, formulário de Novo Trade, check-in de
Bem-Estar com histórico de 2026-07-15). Nada de errado identificado nessa
inspeção estática (não tentei salvar/editar/desativar nada pra não mutar
dado real sem necessidade). Único detalhe cosmético: 2 tags emocionais
("confiança"/"tranquilidade") estão em minúsculo enquanto todas as outras
tags cadastradas começam maiúsculo — inconsistência de cadastro, não bug de
código.

---

## Sessão 2 — mesmo dia, resto do dashboard clássico (porta 8050)

### 🟢 Quant Lab — aba WDO

Troquei o seletor de ativo pra WDO no dashboard clássico. Tudo consistente
com dado real: preço atual mostrado 5.088 (bate com MT5, WDOQ26 em
5088,5 no momento), MME21 5.087, traçados do Jesué (5098 e 5095,
resistência, ver [[project_modo_aprendizagem]]) presentes com contagem de
toques e blocos de força coerentes. Único ponto de atenção, não
necessariamente bug: `ATR: 1 pts` (volatilidade 13,4× ATR) parece muito
baixo pro WDO — mas o mercado estava "Fora do pregão" nesse instante
(18:26), o que pode legitimamente produzir ATR baixo em candles finais de
pregão fraco. Não investiguei o cálculo a fundo.

### 🟢 Backtesting (clássico)

Aba mostra só "Backtesting — em construção." Nada a testar, é um
placeholder intencional (feature ainda não implementada), não bug.

---

## Sessão 2 (cont.) — skin '98 (dashboard_98.py porta 8051, diario_app_98.py porta 8052)

### 🔴 Bug de exposição WDO zerada REPRODUZIDO IDENTICAMENTE no '98

Abri `http://127.0.0.1:8052/?aba=posicao` (a janela "Posição" do '98 é um
`<iframe src="http://127.0.0.1:8052/?aba=posicao">` dentro de
`dashboard_98.py` — descobri que o app já usa query string pra abrir
direto na sub-aba certa, um padrão melhor que o do clássico). Resultado:
exatamente o mesmo texto do clássico — "5 posição(ões) — 5 WIN, 0 WDO" +
"Exposição líquida zerada em todos os contratos — nenhum risco de mercado
residual" — quando a conta real tinha (no momento desse teste) 2 posições
WDO abertas (hedge pair). **Isso confirma que o bug está na camada de
lógica/dados COMPARTILHADA entre os dois skins** (muito provavelmente em
`diario_trade.py` ou equivalente, não em código específico de UI de
nenhum dos dois `diario_app_*.py`) — prioridade máxima pro plano de
correção, já que resolver na camada compartilhada conserta os dois front-
ends de uma vez.

### 🟠 Histórico '98: mesma divergência de valores PENDENTE vs Posição

`http://127.0.0.1:8052/?aba=historico` mostra a mesma lista "PENDENTE" do
clássico, com os MESMOS 3 valores WIN de hoje na MESMA ordem (+1,00 /
-30,00 / +1,00) — de novo batendo diferente da aba Posição (+1,00 / -30,00
/ -30,00). Confirma que essa inconsistência também vem da camada
compartilhada de dados, não é um bug de renderização de uma UI específica.

### 🟡 Padrões '98: mesmo problema de texto não atualizando

`http://127.0.0.1:8052/?aba=padroes` — clicar "Detectar padrões" não
atualizou o texto de resultado (ficou "Nenhum resultado ainda..."), igual
ao clássico. Mesma causa provável (compartilhada).

### 🟢 Config '98 — consistente com o clássico

Abri a janela "Configurações" do '98 (desktop tem que ser aberto via botão
da BARRA DE TAREFAS, não pelo ícone da área de trabalho — um único clique
no ícone só "seleciona" ele, não abre; comportamento igual ao Windows real,
não é bug). Conteúdo idêntico ao clássico: Modo Aprendizagem em "Padrão",
Recalibração "dados insuficientes (0 amostras)" na mesma rodada de
2026-07-21 12:57, mesma sugestão estrutural pendente — confirma que
`config_operacional.py`/os arquivos de aprendizagem são de fato
compartilhados entre os 2 front-ends como o CLAUDE.md descreve.

### 🟡 Assimetria entre skins: '98 não tem janela de Backtesting

A barra de tarefas do '98 só lista 6 janelas: Quant Lab, Price Action,
Diário, Posição, Configurações, Rastreador de Tendência — não existe
nenhuma janela "Backtesting" (nem um placeholder). No clássico existe (só
que também "em construção"). Pode ser intencional (feature ainda não
portada pro '98), mas vale confirmar com Jesué se é esperado.

### 🟡 Ferramentas de automação nunca veem "idle" nesse app

Registrando pra referência futura (minha, ou de outra sessão que for
testar via browser): `document.readyState` chega a `"complete"` rapidamente,
mas ferramentas que esperam o evento de "rede ociosa" do Chrome (usadas
por screenshot/extração de texto) ficam esperando 45s+ e falham, porque o
app tem polling contínuo (`dcc.Interval`) de dado de mercado ao vivo — a
rede NUNCA fica realmente ociosa enquanto o pregão roda. Não é bug do app
(é o comportamento correto e esperado de um dashboard ao vivo), só uma
limitação de quem for testar via automação de navegador: prefira
`document.body.innerText` via JS direto em vez de esperar
screenshot/idle nesse tipo de página.

### 🟢 Estratégias, Novo Trade, Bem-Estar — não testados separadamente no '98

Como Histórico/Posição/Padrões já confirmaram dado 100% compartilhado com
o clássico (mesmos arquivos parquet, mesma camada `diario_trade.py`), não
testei essas 3 sub-abas de novo no '98 — a expectativa razoável é que
espelhem o mesmo resultado limpo já visto no clássico. Se quiser
confirmação explícita depois, é rápido de fazer.

---

## Achados de sessões anteriores relevantes (contexto, não desta varredura)

Ver memória `project_diario_trade_module` — bugs reais já achados e
corrigidos antes: hedge orphans, OUT_BY com lucro zero, cache zerado por
instabilidade de IPC do MT5. O achado do registro órfão (176925/176775)
acima pode ser uma recorrência da MESMA classe de bug, não necessariamente
uma regressão nova.

---

*(varredura concluída — cobriu as 7 sub-abas do Diário nos 2 skins, Quant
Lab WIN+WDO, Backtesting, Configurações/Modo Aprendizagem. Ver seção
"Correções aplicadas" abaixo pro plano de correção já implementado.)*

---

## Correções aplicadas (2026-07-21, mesmo dia da varredura)

Todos os itens testados em parquet de scratch antes de qualquer mudança em
produção (nunca monkeypatch/teste rodando contra `data/base_processada/`
real, exceto o item 2 abaixo, que é uma limpeza pontual explicitamente
aprovada).

1. **🔴 Sincronização MT5 blindada contra falha silenciosa**
   (`dashboard.py`, `dashboard_98.py`, `diario_trade.py`). A chamada
   `diario_trade.sincronizar_posicoes_abertas(df_pos)` (que não tinha
   nenhuma proteção) e a sub-etapa `mt5_loader.buscar_execucoes_periodo`
   dentro dela agora têm `try/except` com `logging.exception(...)`
   gravado em `log_sync_posicoes.log` (arquivo UTF-8, nunca console —
   evita o UnicodeEncodeError já documentado). Uma falha na detecção de
   fechamento não aborta mais o registro de posições novas/ainda abertas.
   Testado: `_secao_status_posicoes` também ganhou uma linha nova
   mostrando o horário da última sincronização bem-sucedida, com aviso
   "⚠ Sincronização com MT5 pode estar atrasada" se o dado tiver mais de
   15s — nunca mais afirma "0 risco residual" sem dizer de quando é o
   dado. Testado com DataFrame fake (caso fresco: sem aviso; caso com 30s/
   45s de atraso: aviso aparece corretamente).
2. **🟠 Registro órfão limpo + função de descarte criada**
   (`diario_trade.descartar_rascunho_pendencia`, novo botão "Descartar
   (não é uma operação real)" no pop-up de edição de pendência, nos 2
   skins). Testado em scratch: remove rascunho limpo (True), recusa
   remover posição `aberta` de verdade (False), recusa remover rascunho
   que já tem alguma classificação preenchida (False). **O órfão real
   (ticket 306272514, 176925→176775) foi removido da produção** — 4
   posições WIN legítimas continuam intactas, confirmado antes/depois.
3. **🟠 Reforço de dedup em `listar_pendencias_historico`** — qualquer
   ticket já rastreado ao vivo em `posicoes_abertas.parquet` (mesmo só
   `aberta`, sem par) agora é excluído da lista PENDENTE do Histórico
   vinda da reconciliação separada, fechando um gap que antes só protegia
   tickets já pareados/fechados. **Nota de honestidade**: reinvestigando
   com dado real após os fixes 1-2, a maior parte da divergência de
   valores que eu tinha registrado como bug era na verdade explicada
   pelo próprio órfão (coincidência de R$ igual a um par legítimo) e por
   diferença de ESCOPO entre as duas listas (Histórico legitimamente
   mostra mais itens, vindos só da reconciliação) — não uma divergência
   de valor pro MESMO trade. Esse item virou mais um reforço de robustez
   pra um gap plausível (nunca observado de fato acontecendo) do que a
   correção de um bug ativamente reproduzido. Testado com cenário
   sintético (ticket `aberta` presente nas duas fontes com valores
   conflitantes): confirmado que não vaza mais.
4. **🟡 Texto de "Padrões" agora distingue "nunca rodei" de "rodei, achei
   0"** (`_tabela_padroes`/`_tabela_padroes_98`, parâmetro `ja_rodou`).
   Testado nos 2 skins.
5. **🟠 `assets_98/window_manager.js`** — `setInteracting` ganhou timeout
   de segurança (4s) + listener de `blur` da janela, pra nunca mais
   travar `pointer-events` de TODOS os iframes indefinidamente se um
   `mouseup` de arraste/resize não chegar ao documento. Não consegui
   testar num browser real nesta sessão (extensão do Chrome desconectou)
   — revisão de código feita com cuidado, mas vale confirmar visualmente
   arrastando uma janela e soltando fora da viewport.
6. **id de pattern-matching `timeline-toggle`** (item cosmético do
   resumo executivo) — confirmado que É necessário de verdade (usado num
   `Input({'type':'timeline-toggle','index':ALL})` real pros toggles de
   traçados, não o toggle "Ano Inteiro" do calendário como eu tinha
   registrado por engano) — nenhuma mudança feita, item fechado como "não
   é um bug, é uso correto de pattern-matching".

### Ainda dependem de confirmação manual do Jesué (não são mudanças de código)

- Item 5 do resumo executivo (botão WIN/WDO não responde a clique real):
  causa raiz confirmada e corrigida pro skin '98 (ver item 5 acima); pro
  skin clássico (porta 8053 direto, fora do '98) não achei mecanismo
  equivalente no código — se ainda travar lá, precisa investigação nova
  com DevTools ao vivo.
- Item 6 (sub-abas do Diário não respondem via iframe do dashboard
  principal): nenhuma causa encontrada no HTML/CSS/JS dos dois skins —
  provável limitação da minha ferramenta de automação com iframe
  cross-origin, não bug do app. Peço confirmação com clique manual.
- Tag emocionais em minúsculo ("confiança"/"tranquilidade"): correção de
  dado via a própria tela de Estratégias, não entrou no código.

### Arquivos tocados nesta rodada de correção

`dashboard.py`, `dashboard_98.py`, `diario_trade.py`,
`diario_app_classico.py`, `diario_app_98.py`, `assets_98/window_manager.js`.

### 🔴 Bug novo achado no uso real (2026-07-22): par de hedge finalizava jogando fora o resultado da perna que TAMBÉM fechou sozinha

Depois de reiniciar os processos, Jesué reportou "Pronta pra Finalizar — WIN
(2)... SHORT FECHADA (par hedge)... isso está errado" com as 4 posições
WIN de ontem já todas fechadas de verdade no MT5 (0 posições abertas).

Causa raiz: `detectar_pares_hedge` sempre mescla a perna de ENTRADA (A)
com a perna de SAÍDA (B, a que nasceu do SL/TP de A) usando só o preço de
ENTRADA de B como "saída confirmada" de A — matematicamente correto
ENQUANTO B continua genuinamente aberta (nesse caso o preço de entrada de
B é um fato fixo, e o resultado dos 2 juntos é invariante ao preço atual
de mercado). Mas quando B **também fecha por conta própria** (ganha seu
PRÓPRIO `preco_saida`/`pnl_reais_final`, independente), ela passa a ter
um resultado econômico separado que a mesclagem simplesmente IGNORA — no
caso real: a perna longa (173865→174690) rendeu sozinha **R$+165,00**,
mas o par mostrado ("SHORT +5pts, R$+1,00") teria descartado esse valor
por completo se finalizado como estava. Confirmado numericamente: as 4
posições individuais somam R$-29,00 (o mesmo total dos 2 pares antigos),
mas só aparecendo separadas cada uma preserva o resultado de verdade de
cada perna.

**Primeira correção (revertida em parte a pedido do Jesué)**: fiz
`detectar_pares_hedge` parar de formar par quando a perna de saída
também fecha sozinha, passando a mostrar as 2 pernas separadas. Jesué
reportou de volta: "o sistema está trazendo os hedges do MT5 como se
fossem trades completos... não quero sujeira do MT5 no diário, preciso
registrar minhas operações com clareza" — ou seja, ver os 2 tickets
crus como se fossem 2 operações distintas é tão errado quanto perder o
resultado da perna B. Pedi pra ele escolher entre 3 designs; escolheu
"1 linha só, somando o resultado real das 2 pernas".

**Correção final**: `detectar_pares_hedge` volta a formar o par SEMPRE
(1 linha = 1 operação, sem sujeira de ticket), mas o cálculo muda
conforme o estado: se a perna de saída ainda está genuinamente aberta,
usa o preço de ENTRADA dela como fechamento de A (matemática antiga,
correta pra hedge travado); se ela TAMBÉM já fechou, soma o `pnl_reais_
final` (ou `pnl_reais` se ainda não resolvido) de CADA perna
separadamente — nunca mais descarta o que a perna de saída rendeu
depois. `saida_estimada` do par passa a refletir se qualquer uma das
pernas ainda depende de confirmação real do MT5 (antes vinha sempre
`False`) — a UI (`_bloco_posicao_live`, 2 skins) agora mostra "⚠ FECHADA
(par hedge) — AGUARDANDO CONFIRMAÇÃO" em vez do "✓" confiante quando for
o caso.

Testado com 4 cenários: (1) par genuinamente aberto — sem mudança; (2)
caso de corrida já documentado — sem mudança; (3) as duas fecharam com
valores DIFERENTES (não coincidentes) — soma corretamente; (4) dado real
de produção — volta a mostrar 2 pares (R$+1,00 e R$-30,00, os MESMOS
totais de antes, confirmando que a soma bate matematicamente), agora
com `saida_estimada=True` e o rótulo "AGUARDANDO CONFIRMAÇÃO" correto.

**Pendência conhecida, não resolvida ainda**: as entradas mais antigas do
Histórico PENDENTE (2026-07-10, 2026-07-03) vêm só da reconciliação
histórica (`listar_pendencias_diario`), que NÃO passa pelo pareamento de
hedge — se alguma delas também for uma perna solta de um par antigo,
ainda aparece separada. Resolver isso exigiria estender o pareamento pra
also rodar sobre o cache de reconciliação, não só sobre `posicoes_
abertas.parquet` — não fiz isso agora, fica registrado pra próxima.

**Editar não fazia nada**: era a mesma tela desatualizada (antes do
restart) tentando reabrir um id de par que deixou de existir depois da
minha primeira correção. Resolvido pelo simples fato de recarregar a
página — mas vale lembrar: qualquer sessão de navegador aberta ANTES de
um restart do servidor pode ficar com ids obsoletos até recarregar.

### 🔴 Erro meu (2026-07-22): teste de "Finalizar" vazou pra produção e apagou 2 posições reais — corrigido

Ao investigar "Finalizar não acontece nada", testei `finalizar_par_hedge`
isolando `posicoes_abertas.parquet` e `diario_trades.parquet` em cópias de
scratch — mas esqueci de isolar um TERCEIRO arquivo,
`tickets_finalizados.parquet` (usado por `sincronizar_posicoes_abertas`
pra nunca "ressuscitar" um ticket já virado trade definitivo). Meu teste
gravou os tickets 308222706/308222792 nesse arquivo de PRODUÇÃO com um
`id_trade=1` fictício (que não existe em `diario_trades.parquet` de
verdade). A sincronização em background (rodando o tempo todo) purgou os
2 tickets de `posicoes_abertas.parquet` no ciclo seguinte (3s depois),
por achar que já eram "zumbis" finalizados — mesmo sem nenhum trade real
ter sido gravado.

**Corrigido imediatamente**: removi as 3 entradas bogus de
`tickets_finalizados.parquet` (308222706, 308222792, e uma `999003` de um
teste anterior, todas com `id_trade=1`) e **restaurei as 2 posições** em
`posicoes_abertas.parquet` com os dados exatos que eu mesmo já tinha
capturado nesta sessão antes do incidente (preços, status
`fechada_pendente`, `saida_estimada=True`, `pnl_reais_final` -164/+165).
Confirmado que persistem depois de vários ciclos de sync (não foram
purgadas de novo). Nenhum trade real foi perdido — só a rastreabilidade
temporária ficou instável por alguns minutos.

**Lição pra mim mesmo**: `finalizar_posicao`/`finalizar_par_hedge` tocam
3 arquivos (`posicoes_abertas`, `diario_trades`, `tickets_finalizados`) —
qualquer teste isolado precisa redirecionar os TRÊS, nunca só os dois
"óbvios". Refiz o teste isolando os 3 corretamente antes de seguir.

### 🔴 `finalizar_par_hedge` recalculava P&L do zero, divergindo do valor já corrigido

Mesma causa raiz da seção anterior (par de hedge cuja perna de saída
também fecha sozinha) — mas agora no momento de GRAVAR o trade, não só
de exibir. `finalizar_par_hedge` tinha sua PRÓPRIA cópia da matemática
antiga (entrada-vs-entrada), nunca atualizada quando corrigi
`detectar_pares_hedge`. Corrigido: agora busca o par já calculado por
`detectar_pares_hedge` (única fonte de verdade) e usa o `pnl_pontos`/
`pnl_reais`/`preco_saida`/`datetime_saida` de lá — nunca mais recalcula
por conta própria. Testado isolado (3 arquivos em scratch desta vez):
grava `preco_saida=174690` (real) em vez do antigo `173980` (só entrada
da perna B), `saldo_operacao_reais=-30.00` (igual antes, matemática bate).

### Design change (pedido direto do Jesué): trade fechado some da aba Posição

"Trade fechado vai para o histórico, não deve ficar em posição." A seção
"Pronta pra Finalizar" foi REMOVIDA de `_renderizar_posicao_completa`
(`diario_app_classico.py`)/`_renderizar_posicao_completa_98`
(`diario_app_98.py`) — Posição agora mostra SÓ posições genuinamente
`aberta` no MT5. Tudo que já fechou mora exclusivamente no Histórico
(lista PENDENTE + botão "Editar", que já reaproveita o mesmo formulário).
Testado: renderização atual mostra só "Nenhuma posição WIN aberta no
momento" (correto — as posições WIN de hoje já fecharam todas).

### 🔴 CAUSA RAIZ REAL confirmada: callback rejeitada inteira por `ReferenceError` (não era clique, não era iframe)

Jesué testou 8053 DIRETO (sem passar pelo dashboard principal) e "Editar"
continuava sem funcionar — isso descartou de vez a teoria do iframe/resize
acima. Reabri a investigação e achei o erro real no console do navegador:

```
ReferenceError: A nonexistent object was used in an `Output` of a Dash
callback. The id of this object is `em-andamento-conteudo`...
```

`abrir_pendencia_editar` (botão "Editar" do Histórico) tinha 3 Outputs:
`store-pendencia-editar` (o que de fato abre o pop-up) +
`em-andamento-conteudo`/`store-em-andamento-ids-conhecidos` (tentativa de
já deixar a aba Posição atualizada, pra quando o usuário for lá depois).
O problema: esses 2 últimos ids SÓ existem no DOM quando a sub-aba Posição
está montada — mas `montar_sub_aba_diario` troca o conteúdo INTEIRO ao
trocar de sub-aba (não usa `display:none`), então enquanto o usuário está
no Histórico (único lugar de onde esse botão pode ser clicado),
`em-andamento-conteudo` simplesmente não existe. O Dash rejeita a
callback INTEIRA quando qualquer Output aponta pra um id inexistente —
inclusive o Output crítico que abriria o pop-up. Resultado: clique real,
nenhum efeito, nem visual (exatamente o sintoma relatado) — porque o
JAVASCRIPT que trata o clique roda normalmente, só a atualização de
estado é descartada pelo Dash.

**A mesma exata falha existia em 4 callbacks** (`abrir_pendencia_editar`,
`fechar_pendencia_editar`, `finalizar_em_andamento`, e meu próprio
`descartar_pendencia_editar` de ontem — todos compartilhavam o mesmo
padrão de Outputs), nos 2 skins (8 callbacks no total). Isso explica
"Editar" E "Finalizar" com o MESMO bug.

**Corrigido**: removidos os Outputs pra `em-andamento-conteudo`/
`store-em-andamento-ids-conhecidos`/`em-andamento98-conteudo`/
`store-em-andamento98-ids-conhecidos` das 8 callbacks — a aba Posição já
se re-renderiza sozinha, corretamente, na próxima vez que for exibida
(`atualizar_em_andamento`/`_98` já reage a `store-diario-sub-aba` mudando
+ já lê `store-pendencia-editar` como State pra excluir o ticket certo).
Nenhuma perda de funcionalidade — só a atualização deixa de ser
instantânea nesse 1 caso específico (finalizar um trade estando
literalmente na aba Posição ao mesmo tempo), passando a acontecer no
próximo tick do interval de 6s ou troca de aba.

**Testado ao vivo, com o navegador de verdade, direto na porta 8053**:
clique em "Editar" agora abre o pop-up corretamente, mostrando o par
308243589-308243654 com o cálculo já corrigido ("SHORT ⚠ FECHADA (par
hedge) — AGUARDANDO CONFIRMAÇÃO", "R$ -30.00") — e "Fechar" também
funciona, fechando o pop-up sem alterar nada em produção (confirmado:
5 posições intactas depois do teste). Não testei "Finalizar" de propósito
— criaria um trade real na produção sem sua autorização explícita.

### 🟡 Hipótese anterior (disjuntor do auto-resize) — não confirmada como causa, mas mantida como melhoria válida

Jesué confirmou: "não acontece nada, nem efeito de clique no botão. Não
carrega, não atualiza." — clique não produzindo NEM o feedback visual
:active do próprio botão indica que o clique não está nem chegando no
elemento, não que o callback falhou em silêncio.

`assets/diario_iframe_resize.js` (dashboard.py, iframe do Diário
clássico) tem um disjuntor de segurança do lado PAI contra um loop de
crescimento infinito (bug já corrigido na raiz em 2026-07-12) — mas a
implementação do disjuntor em si tinha um bug: contava mensagens de
altura numa janela de 2s que se REARMAVA a cada mensagem nova, mesmo as
ignoradas. Em uso normal (Histórico com 160+ trades, popup abrindo, sync
de 6s da Posição), várias mensagens legítimas podem chegar em sequência
rápida sem nunca ficar 2s em silêncio — uma vez disparado, o disjuntor
ficava ligado PRA SEMPRE na mesma sessão. Com o disjuntor travado, o
`<iframe>` para de crescer e congela numa altura antiga — qualquer botão
renderizado ABAIXO dessa altura fica fora da área clicável do iframe
(cortado por `overflow:hidden` do lado filho), sem nenhum jeito de se
recuperar sozinho. Isso bate exatamente com "nem o efeito de clique
aparece" (o clique cai fora do iframe, na página por trás).

**Corrigido**: o disjuntor agora só dispara se a altura vier SEMPRE
aumentando, sem NUNCA estabilizar, por 200+ ciclos seguidos — uma
atualização legítima sempre para de crescer em poucos ciclos e nunca
aciona isso, não importa a frequência das mensagens. `ALTURA_MAXIMA`
também subiu de 20.000 pra 50.000px (o Histórico já com 164 trades pode
legitimamente chegar perto do limite antigo).

**Nível de confiança**: não consegui reproduzir/confirmar ao vivo (minha
extensão do Chrome está desconectada nesta sessão) — é uma hipótese bem
fundamentada no código, não uma correção verificada com clique real.
Preciso que Jesué teste de novo depois deste restart e confirme se
"Finalizar"/"Editar" voltaram a responder.

### Achado ao subir os processos de verdade (2026-07-22): logging poluído, corrigido

Ao reiniciar os 4 processos pra testar as correções acima, reparei que o
`logging.basicConfig()` usado no item 1 configura o logger RAIZ — isso
também capturava o log de rotina do Werkzeug/Flask (uma linha por
request) no mesmo `log_sync_posicoes.log`, o que teria poluído o arquivo
rapidamente e escondido os erros de sync de verdade em meio a ruído.
Trocado por um logger NOMEADO (`logging.getLogger('sync_posicoes')`, com
handler próprio e `propagate=False`) em `dashboard.py`/`dashboard_98.py`/
`diario_trade.py` — testado: depois de subir os 4 processos e passar por
alguns ciclos de sync, o arquivo existe mas fica vazio (só grava em
falha de verdade).

---

# 2026-07-27 — Duplicação de trades e a família de bugs "o botão não faz nada"

Reportado pelo Jesué depois de clicar no botão "✓" (finalização rápida):
*"Quando eu clico no botão check a pendência continua lá. Eu cliquei várias
vezes e o trade foi gravando todas as vezes que eu cliquei. O botão finalizar
dentro da edição ainda está com o mesmo problema, o pop-up não fecha."*

Não eram bugs independentes. Eram **4 defeitos estruturais**, dois deles
reincidências de bugs já "corrigidos" antes pela metade.

## 1. 🔴 A lápide de "já finalizado" não era consultada por quem lista pendências

`tickets_finalizados.parquet` existe exatamente pra impedir finalizar o mesmo
ticket duas vezes. Ele registrou o ticket `308536027` sendo finalizado **14
vezes** (trades #169–#182, em 90 segundos). A marca foi gravada certo todas as
vezes — só nunca era lida.

Cadeia exata: `finalizar_posicao` grava o trade, grava a lápide e **remove a
linha de `posicoes_abertas.parquet`**. Mas `listar_pendencias_historico`
montava `ids_ja_cobertos` **só a partir desse arquivo**. Então finalizar
DESPROTEGIA o ticket: a linha do cache de reconciliação (atualizado só a cada
5 min, pelo processo pai) voltava como PENDENTE e cada clique novo gravava
outro trade idêntico. `materializar_pendencia_como_rascunho` tinha o mesmo
ponto cego e recriava o rascunho a cada clique.

Efeito colateral não percebido: **2 linhas PENDENTE na tela já eram trades
gravados** — `307609756` era o #147 e `307636545` era o #149, ambos de
2026-07-16, ainda listados em 2026-07-27. Um clique nelas duplicaria trades de
11 dias atrás.

O docstring de `_marcar_tickets_finalizados` descreve esta MESMA classe de bug
e cita os duplicados #154–158. Foi corrigida só no caminho do sync ao vivo em
2026-07-16; voltou pelo caminho da reconciliação.

**Correção:** a lápide virou autoridade em 3 pontos —
`listar_pendencias_historico` (soma ao `ids_ja_cobertos`),
`materializar_pendencia_como_rascunho` (recusa ticket já marcado) e, o que de
fato garante, uma **trava de idempotência dentro do lock** em
`finalizar_posicao`/`finalizar_par_hedge`. Guarda de UI é defesa secundária e
sempre fura (clique duplo, aba stale, os 2 processos do Diário concorrendo).

## 2. 🔴 Callbacks disparavam sozinhas ao abrir o pop-up — e apagavam dados

Causa real do *"Finalizar não faz nada / o pop-up não fecha"*, reportado
várias vezes e nunca resolvido de verdade até aqui.

`prevent_initial_call` só cobre a carga inicial do app — **não** a inserção
dinâmica de um componente novo no layout. Como `renderizar_overlay_pendencia`
CRIA os botões (`n_clicks=0`) toda vez que o pop-up monta, essa inserção
disparava as callbacks deles. Sem guarda de `n_clicks`:

- `descartar_pendencia_editar` rodava na ABERTURA e **apagava o rascunho** que
  o "Editar" tinha acabado de materializar. O usuário via o formulário, clicava
  "Finalizar", e `atualizar_rascunho` não achava mais a linha → devolvia
  `False` → a UI traduzia como "⚠ Ocupado". **Nunca foi disputa de trava**: o
  registro era apagado pelo próprio ato de abrir a janela.
- `fechar_pendencia_editar` também disparava na abertura e zerava
  `store-pendencia-editar`. Depois, no clique real em "Finalizar", o `State`
  dessa store já era `None`, então `fechar_popup` virava `no_update` e **o
  pop-up nunca fechava — mesmo com o trade gravado certo**.
- `finalizar_em_andamento` tinha o mesmo buraco: abrir o pop-up podia gravar
  o trade sozinho.

**Correção:** guarda de `n_clicks` em todas (`if not n_clicks: return`), e
`ctx.triggered[0]['value']` nas que têm gatilho múltiplo. Regra que fica:
**callback que apaga ou grava dado nunca pode agir com `n_clicks` falsy.**

## 3. 🟠 Pareamento de hedge sem limite de tempo fabricava trades

`detectar_pares_hedge` casava long+short por contrato+volume **sem nenhuma
restrição de data**. Pareou o ticket `308391086` (WDO short, entrada 5073,
aberto em 22/07 11:55) com o `308536474` (saída 5095.5, de 23/07) e produziu o
trade **#167 com R$ −225 que nunca existiu** — o mesmo −225 que o Jesué já
tinha sinalizado como errado.

**Correção:** as 2 pernas precisam ter aberto no **mesmo dia de pregão**. Um
par de hedge nasce sempre do OCO/SL-TP da própria posição, no mesmo pregão.
Sem par, cada perna segue pelo caminho avulso e mostra o número real do MT5.

## 4. 🟠 Ações acopladas a ids de outra aba (a fonte da recorrência)

Trocar de sub-aba substitui a árvore inteira. Um `Output`/`State` apontando pra
id não montado faz o Dash rejeitar a callback **inteira, em silêncio**. A
correção anterior (2026-07-27, mais cedo) tinha ligado
`finalizar_em_andamento`/`descartar`/`✓` direto em `diario-historico-tabela` e
nos filtros `diario-hist-*` — todos exclusivos do Histórico. Era a MESMA
armadilha de 2026-07-22 (`em-andamento-conteudo`, exclusivo da Posição), só
que apontando pro outro lado.

**Correção estrutural:** ninguém escreve mais direto na tabela. Foi criado o
`store-diario-refresh` (global, sempre montado); as ações só incrementam esse
contador e quem remonta a tabela é a dona dela, `atualizar_historico`. Os 2
overlays também subiram pro layout global (eram Output de callbacks
disparadas por Store globais, mas só existiam no Histórico), preservando a
posição no topo que o feedback de 2026-07-20 pedia.

## Verificação

Dois scripts novos, em `testes/` — rodáveis a qualquer momento:

- `testes/teste_duplicacao.py` — 12 asserts em parquet de scratch (os **4**
  caminhos de persistência isolados, não 3: houve incidente real por esquecer
  `_caminho_tickets_finalizados`). Cobre: 5 cliques no "✓" → **1 trade só**;
  pendência já finalizada não aparece; hedge de dias diferentes não pareia;
  par não finaliza 2x; pendência legítima continua aparecendo.
- `testes/auditoria_ids_callbacks.py` — monta o layout de cada sub-aba e
  denuncia toda callback cujos Outputs/States não existem em alguma aba onde
  os Inputs dela existem. Achou 8 acoplamentos latentes (4 por skin) que já
  foram corrigidos. **Hoje: 0.** É a rede que impede a classe #4 de voltar.

Verificação ao vivo no navegador (não por inferência): "✓" grava 1 trade, a
linha some da lista na hora e o 2º clique é impossível; "Editar" → "Finalizar
e gravar" fecha o pop-up e some com a linha, sem trocar de aba.

## Limpeza de dados de produção (com backup)

Feita com os 4 processos parados, backup em
`data/base_processada/_backup_limpeza_<carimbo>/`:

- removidos os trades **#170–#182** (13 duplicados), mantido o **#169** como o
  registro real da operação — a lápide do #169 fica, travando o ticket pra
  sempre;
- removido o trade **#167** (o −225 fabricado) e **liberadas** as lápides dos
  tickets `308391086`/`308536474`, pra voltarem como PENDENTE e serem
  registrados corretamente com o pareamento já corrigido.

Total: 182 → 168 trades.

---

# 2026-07-27 (parte 2) — Widget de tags quebrado + "×" pra remover print

## 🔴 O widget de tags nunca funcionou — e meu teste anterior era inválido

Jesué: *"O mecanismo de inserção de tags que eu te pedi anteriormente não está
funcionando."* Digitava a tag, apertava Enter, e o contador seguia em
"0 tag(s)".

**Causa raiz:** no Dash 4.3 o `dcc.Input` não renderiza mais um `<input>` nu —
ele envolve o campo num wrapper, e a `className` vai parar na DIV de fora:

```html
<div class="dash-input-container dash-input tag-widget-input">
  <input class="dash-input-element" type="text">
</div>
```

O `assets/tag_input.js` fazia `e.target.closest('.tag-widget-input')` e subia
até a DIV. `.value` numa div é `undefined` → o texto digitado virava string
vazia → `adicionarTag` saía na primeira linha. Nenhuma tag, nenhum erro.

**Meu erro de método, que é o mais importante aqui:** eu tinha "validado" esse
widget antes fazendo `elemento.value = 'calma'` no `.tag-widget-input`. Como
aquilo é uma div, a atribuição **criou** a propriedade — o teste passou
enquanto o widget seguia quebrado pro usuário. Um teste que fabrica a condição
que deveria observar não vale nada.

**Correção:** `campoDeTexto()` no JS resolve o `<input>` REAL e aceita as duas
formas (nu ou aninhado), pra nunca mais depender do formato interno de um
componente de terceiro. `limparCampo()` usa o setter nativo + evento `input`
(forma suportada de mexer num input controlado por fora). CSS passou a
estilizar os dois níveis. `html.Input` não é alternativa: não existe no
Dash 4.3.

**Verificação:** `testes/teste_tag_input.html` — 10 asserts contra uma réplica
exata do DOM que o Dash 4.x produz, com o texto escrito no `<input>` de
verdade. Cobre Enter, vírgula, vocabulário oficial, não-duplicar, Backspace,
× do chip e "Remover todas". Abrir no navegador, sem servidor.

## 🟢 "×" pra remover um print da operação

Pedido: *"a thumb do print (tanto no campo abaixo quanto acima) deve ter um X
que aparece ao passar o mouse, caso eu queira remover um print do trade."*

- `diario_trade.remover_imagem_rascunho(id_posicao, nome)` — tira o print de
  `imagens_em_andamento`. O arquivo em disco só é apagado se NENHUM outro
  registro citar aquele nome (outro rascunho, ou `imagem_arquivo`/
  `imagens_adicionais` de um trade já gravado) — barato de conferir e evita o
  pior caso: sumir com a capa de um trade antigo por um clique numa tela de
  rascunho.
- `_thumb_com_remover` envolve a miniatura; o × é irmão da imagem (clicar na
  thumb continua ampliando/escolhendo a capa) e aparece 100% por CSS
  (`.diario-thumb-wrap:hover .diario-thumb-x`), sem callback pra isso.
- Vale nos DOIS lugares que o Jesué citou: a galeria pequena do bloco
  só-leitura e a de escolha de capa. Espelhado no skin '98 (com borda dura,
  sem arredondamento).

**Detalhe de arquitetura, pra não repetir a armadilha da parte 1:** o × é
renderizado tanto na sub-aba Posição quanto no pop-up do Histórico. Por isso a
callback escreve SÓ em ids `MATCH` do próprio card (as 2 galerias + o status),
nunca num store global — um Output global quebraria num dos dois contextos.
E leva a guarda de `n_clicks`, porque reconstruir as galerias recria os
próprios botões e redispara a callback.

---

# 2026-07-27 (parte 3) — Diário × relatório de performance do ProfitPro

Jesué: *"As estatísticas (mensal) do diário não batem com o relatório de
performance do ProfitPro. Descubra o problema."*
Diário: 46 operações / R$ −228. Profit: 42 / R$ −110.

## 🔴 Causa raiz: cada par de hedge deixava uma "perna fantasma" pendente

`reconciliar_com_mt5` casa trade↔posição por `(ativo, data)` e **cada trade
consome UMA posição**. Mas um par de hedge são **DUAS** posições do MT5 que
viram **UM** trade. A segunda perna sobrava e virava
`sem_correspondencia_diario` — permanentemente.

Isso enchia a lista PENDENTE do Histórico de operações **que já estavam
registradas**. Comprovado no dado real: os 3 tickets ainda marcados como
pendentes tinham trade gravado na mesma data:

```
307609756 WDO 15/07 -> trade #147 já existia
307636545 WIN 15/07 -> trade #149 já existia
308768994 WDO 27/07 -> trade #173 já existia
```

E foi assim que 2 dessas falsas pendências (306272514/306331658, de 03/07)
foram finalizadas de novo e viraram os trades duplicados #171/#172.

**Correção:** `reconciliar_com_mt5` ganhou um **passo 0 identitário** — antes
de qualquer heurística, usa `tickets_finalizados.parquet`
(`_mapa_tickets_finalizados`, novo) pra ligar ticket→trade diretamente. Marca
as N posições que compõem um trade como reconciliadas de uma vez, e pula esses
trades na heurística (senão consumiriam a posição de outro trade e recriariam
o furo). A lápide é a única fonte que sabe que 2 pernas são o MESMO trade.

Efeito imediato: a lista PENDENTE caiu de 6 para **1** — e a que sobrou é
legítima (o par WDO que fechou hoje com R$0, que o Profit também tem).

## Decomposição da divergência (era +4 operações e −R$118)

| Dia | Causa | Δ ops | Δ R$ |
|---|---|---|---|
| 03/07 | #171/#172 duplicatas geradas pela pendência fantasma | +2 | −35 |
| 03/07 | #140/#141 (XLSM) — o Profit não tem NENHUMA op em 03/07 | +2 | −35 |
| 06/07 | #144 (XLSM) inexistente no Profit; preços dão −40, saldo dizia −50 | +1 | −50 |
| 01/07 | #136 com saída 174.030 vs 174.040 do Profit | 0 | +2 |
| 27/07 | par WDO fechado hoje (R$0) ainda não finalizado — normal | −1 | 0 |

## Limpeza aplicada (decidida com o Jesué, com backup)

`_backup_julho_20260727_145530/`. Removidos os trades **#140, #141, #144,
#171, #172**. As lápides dos tickets 306272514/306331658 foram
**preservadas de propósito** — sem elas os tickets voltariam como pendência e
poderiam ser registrados de novo.

**Resultado: 13 dos 14 dias batem exatamente com o Profit.** Restam só o
1 par não finalizado (27/07, R$0) e os R$2 do #136.

## Verificação

`testes/teste_reconciliacao.py` — 8 asserts em scratch com os 4 caminhos de
persistência isolados. Cobre: par de hedge não gera perna fantasma; as 2
pernas apontam pro mesmo trade; **posição genuinamente sem registro CONTINUA
sendo reportada** (teste negativo — a correção não pode engolir pendência
real); ticket avulso segue reconciliando.

---

# 2026-07-27 (parte 4) — Revisão geral do motor MT5 → Diário

Pedido do Jesué: *"implemente para que não fique ordens fantasmas de hedge no
diário. Faça uma revisão geral em toda a lógica desse motor... certifique-se
de que não há pontas soltas que causem divergência."*

## 🔴 1. Pernas de hedge já cruzadas não eram fundidas

`_parear_hedge_deals` só fundia pernas **sem** preço de saída. Quando o BTG
cruza as duas pernas (`DEAL_ENTRY_OUT_BY`), **as duas ganham preço de saída** e
escapavam. Como o MT5 devolve `profit=0` nesse cruzamento,
`agrupar_posicoes` recalculava o resultado de **cada perna separadamente** —
uma operação real de −R$30 virava duas posições de −R$30, e a perna sem trade
ficava para sempre como "operação sem registro no diário".

Eram as "ordens fantasmas" que o Jesué via. Casos reais no diário dele:
`306271796`/`306272514` (WIN 03/07) e `306278318`/`306331658` (WDO 03/07) —
perfeitamente espelhados (a entrada de uma é a saída da outra).

**Correção:** `_fundir_pernas_fechadas_espelhadas` (nova). Assinatura apertada
de propósito: mesmo contrato, mesmo volume, mesmo dia, direções opostas e
**preços espelhados**. Esse espelhamento exato é a marca do OUT_BY — dois
round-trips independentes não produzem isso.

Havia ainda um `return` antecipado em `_parear_hedge_deals` (`if orfas.empty`)
que pulava a fusão justamente no caso mais comum: o dia inteiro já fechado.

## 🟠 2. O pareamento histórico não tinha as travas do pareamento ao vivo

`_parear_hedge_deals` (histórico) agrupava por **raiz** (`WDO`) em vez de
**contrato** (`WDOQ26`) e **não checava o dia** — exatamente o bug que
fabricou o trade #167 de −R$225, e que eu tinha corrigido só em
`detectar_pares_hedge` (ao vivo). São gêmeos: se as regras divergem, a aba
Posição e o Histórico discordam sobre o que é um par. Agora as duas exigem
contrato exato + mesmo dia + mesmo volume.

## 🟠 3. `agrupar_posicoes` quebrava com preenchimento parcial

Três pontas soltas na mesma função:

- **volume vinha só do primeiro fill de entrada** — uma posição de 2 contratos
  preenchida como 1+1 virava volume 1 e o resultado saía **pela metade**;
- **preço de entrada/saída vinha de UM fill**, não da média ponderada por
  volume (que é como a corretora reporta);
- **`iloc[0]`/`iloc[-1]` sem ordenar por tempo** — `history_deals_get` não
  garante ordem, então "primeiro/último fill" podia estar errado.

O Jesué opera 2 contratos com frequência (10/07 e 15/07 no relatório do
Profit), então isso era divergência esperando acontecer.

## ✅ Revisado e OK

- `sincronizar_ordens_pendentes` — replace-all informativo; falha de MT5
  (`None`) não apaga o que existe. Correto.
- Fallback de `saida_estimada=True` em `sincronizar_posicoes_abertas` — marca
  explicitamente o valor como não confirmado e a UI trava a finalização até o
  usuário confirmar. Correto por design.
- `reconciliar_com_mt5` — o passo identitário pela lápide (parte 3) já cobre
  "1 trade = N posições".

## Falso positivo que quase virou "correção"

Uma varredura de duplicatas por `(data, ativo, direção, saldo)` acusou 8
pares. Conferindo no relatório do Profit: **são operações legítimas** — em
16/07 existem mesmo DUAS WIN idênticas de R$0 (11:57:01 e 12:06:02). A chave
era fraca demais. Nada foi apagado por causa disso.

## Ferramenta nova: auditoria contra o relatório da corretora

`testes/auditar_vs_profit.py <export.csv>` — somente leitura. Compara dia a
dia, lista as pendências abertas **marcando as que já têm trade na mesma data**
(sinal de perna de hedge não fundida) e checa a consistência interna
preço × saldo, distinguindo o caso de par de hedge (onde a divergência é
esperada, porque o saldo é a soma das 2 pernas).

## Testes

- `testes/teste_hedge_espelhado.py` — 12 asserts, incluindo os 2 pares REAIS
  do diário e **6 casos negativos** (independentes, dias diferentes,
  vencimentos diferentes, volumes diferentes, contratos diferentes).
- `testes/teste_agrupar_posicoes.py` — 14 asserts: caso simples travado contra
  regressão, entrada/saída parcial, deals fora de ordem, e OUT_BY recalculando
  com o volume total.

## Pendente de confirmação ao vivo

O IPC do MT5 caiu durante a verificação (quirk conhecido; o terminal estava
aberto). A reconciliação preservou o cache anterior — comportamento defensivo
correto — então as 3 pendências fantasma (`306271796`, `306278318`,
`306426742`) só vão sumir no primeiro ciclo com MT5 acessível. A lógica está
validada por teste contra os tickets reais.

---

# 2026-07-27 (parte 5) — "Ignorar pendência" e a fonte de verdade

## O que a investigação com MT5 ao vivo revelou

Reconectei ao MT5 e rodei o motor corrigido no dado real:
**108 pernas → 54 operações** (fundiu 54 pernas de hedge). As "ordens
fantasma" que o Jesué viu **estavam sendo fundidas corretamente** — os pares
`306271796+306272514` (WIN 03/07) e `306278318+306331658` (WDO 03/07) viram
UMA operação cada.

Elas continuavam pendentes por outro motivo: eu tinha **apagado os trades
delas**, com base em "o Profit não tem operação em 03/07".

Comparando MT5 × Profit em julho:

```
MT5    : 46 operações   R$ -195,00
Profit : 42 operações   R$ -110,00

03/07   MT5=2  Profit=0
06/07   MT5=3  Profit=2
27/07   MT5=4  Profit=3   (a extra e R$0)
```

**Não é o motor divergindo — são as duas fontes da corretora discordando.**
O relatório de performance do Profit é filtrado por CARTEIRA ("SnR Price
Action"), então operações da conta fora dessa carteira não constam nele,
embora existam no MT5.

**Decisão do Jesué:** o relatório do Profit é o guia **até 27/07**; de
**28/07 em diante** (ainda não exportado) vale o MT5. As exclusões de #140,
#141 e #144 ficam.

## 🟠 Falta funcional: não havia como recusar uma operação

Consequência da decisão acima: essas 3 operações existem no MT5 e não têm
trade — então a reconciliação as reportava como PENDENTE **a cada ciclo, pra
sempre**. A única forma de calar era registrá-las: ou seja, aceitar
exatamente o que se queria recusar. `descartar_rascunho_pendencia` não
resolvia — remove o rascunho, mas o MT5 continua tendo a operação e ela volta
no ciclo seguinte.

**Correção:** `tickets_ignorados.parquet` + `diario_trade.ignorar_pendencia`.
Semanticamente distinto da lápide: lá o ticket **virou** um trade; aqui ele
**nunca vai virar**, por decisão explícita. Entra em
`listar_pendencias_historico` (some da lista) e em
`materializar_pendencia_como_rascunho` (não pode virar rascunho de novo).
Aceita ticket avulso ou par `"a-b"` (marca as 2 pernas). Reversível: basta
apagar a linha do parquet.

**UI:** o botão "Descartar (não é uma operação real)" agora também chama
`ignorar_pendencia` — antes o descarte durava só até a próxima reconciliação,
o que fazia o botão parecer não ter feito nada. E passou a aparecer para
**qualquer** pendência já fechada, inclusive par de hedge (antes só ticket
avulso), porque agora ele tem o que fazer nesses casos.

## Aplicado

Os 6 tickets (3 operações × 2 pernas) marcados como ignorados com o motivo
registrado. Lista PENDENTE: de 6 para **3** — e as 3 restantes são as
operações reais de **28/07**, que o Jesué ainda não exportou e devem mesmo ser
registradas pelo MT5.

## Testes

`testes/teste_ignorar_pendencia.py` — 10 asserts: some da lista; **não volta
mesmo com o cache de reconciliação reescrito do zero**; não pode virar
rascunho; idempotente; par marca as 2 pernas; **ignorar um ticket não engole
os outros** (negativo); id inválido não grava lixo.

---

# 2026-07-28 — Conferência de 28/07 e a data errada nas pendências

## ✅ O dia bate exatamente nas três fontes

```
Profit  : 3 operações   R$ +100,00
MT5     : 3 operações   R$ +100,00   (após o pareamento de hedge)
Diário  : 3 trades      R$ +100,00   (#179, #180, #181)

  WIN  178240 -> 178090   R$  -30,00
  WDO  5118   -> 5131     R$ +130,00
  WDO  5131   -> 5131     R$    0,00
```

Vale registrar o que isso prova: a conta tem **6 posições abertas** no MT5
agora, que são 3 travas de hedge (`308853001+308853936`,
`308852210+308857929`, `308865801+308866172`). O Profit conta cada trava como
UMA operação concluída — e o motor corrigido chega no mesmo número. Era
exatamente aqui que ele errava antes.

## 🟠 As 2 pendências restantes eram legítimas — mas com a data errada

Não são fantasma. São operações reais de **27/07** ainda não registradas:

- `308768994-308772025` — WDO 5108→5108, R$0. Bate **exato** com a linha
  `WDOQ26;27/07/2026 12:22:55;27/07/2026 13:25:12;5.108,00;5.108,00;0` do
  export do Profit.
- `308765826-308805394` — WIN 176540→176540, R$0, às 16:41. Posterior ao
  horário em que o export foi gerado, por isso não consta nele.

**O bug:** as duas apareciam datadas como **28/07**. `listar_pendencias_historico`
usava `datetime_saida` pra montar a data — mas quando o fechamento real não é
encontrado no MT5, o sync grava uma saída ESTIMADA com
`datetime_saida = agora`. Resultado: a operação "andava" de dia em dia
enquanto não fosse registrada, bem na hora em que o Jesué estava conferindo o
Diário contra o relatório da corretora **por data**.

Pior: era inconsistente com o próprio ato de finalizar —
`finalizar_posicao`/`finalizar_par_hedge` gravam `data_op` a partir da
**entrada**. A lista mostrava uma data e o trade nasceria com outra.

**Correção:** a data da pendência passa a vir de `datetime_entrada`, igual ao
que o trade vai receber. Teste de regressão em
`testes/teste_reconciliacao.py` (entrada ontem + saída estimada hoje → a
pendência tem que mostrar ONTEM).

## Estado

Lista PENDENTE: **2**, ambas datadas 27/07 corretamente, ambas reais e
prontas pra registrar.

---

# 2026-07-28 — Auditoria anual contra o ProfitPro e alinhamento do Diário

## 🔴 Correção de um erro MEU: 3 operações reais apagadas

O export ANUAL (`profit2026.csv`, 01/01→28/07, 204 operações) **tem** as 3
operações que eu recomendei apagar em 27/07:

```
WINQ26  03/07 12:03:03  176.925 -> 176.775   R$ -30,00   (era #140/#172)
WDOQ26  03/07 12:30:00  5.206   -> 5.205,50  R$  -5,00   (era #141/#171)
WDOQ26  06/07 12:28:58  5.198   -> 5.193     R$ -50,00   (era #144)
```

O arquivo de julho que serviu de base pra decisão estava **incompleto** — não
era filtro de carteira, faltavam linhas mesmo. O MT5 já vinha dizendo que elas
existiam (ver parte 5); eu tratei a divergência como "duas fontes da corretora
discordando" quando na verdade uma das fontes estava truncada. Restauradas do
backup `_backup_julho_20260727_145530/`.

**Lição:** quando MT5 e relatório discordam, o relatório só é autoridade se a
janela dele cobrir de fato o período — conferir `Data Inicial`/`Data Final` e o
total de linhas antes de tratar ausência como prova de inexistência.

## Alinhamento executado (backup `_backup_anual_20260728_120713/`)

Estado inicial: 162 operações / R$ +33,00 — contra 204 / R$ −351,00 do Profit.

| | O quê | Ops | R$ |
|---|---|---|---|
| 0 | Restaurar #140/#141/#144 | +3 | −85 |
| A | Inserir fev (41) + mar (2) — meses que o Diário não tinha | +43 | −323 |
| B | Apagar as 4 de 15/04 (o Profit não tem o dia) | −4 | −1 |
| C | Corrigir o saldo de 14 dias com imprecisão do XLSM | 0 | −25 |

**Resultado: 204 operações / R$ −351,00 — todos os dias batem.**

Os 43 inseridos entram com `fonte_registro='importado_profit_2026'`, separando-
os do `importado_xlsm` e do `manual`.

Sobram 18 trades cujo saldo não reconstrói a partir dos próprios preços (ex:
`#119` dá −R$1.050 nos preços e −R$50 no saldo). São imprecisões de PREÇO da
importação do XLSM; o **saldo** — que é o que alimenta as estatísticas — está
alinhado ao relatório. Os `manual` nessa lista são pares de hedge, onde a
divergência é esperada por design.

## 🟢 Suporte inicial a BIT (futuro de Bitcoin)

A auditoria trouxe 2 operações `BITG26` (19/02). Elas expuseram que
`cfg.ATIVOS` mistura dois conceitos:

- **`ATIVOS`** = ativos que o motor carrega candle/tick/features
  (`csv_loader`, dashboards varrem essa lista) — pôr "BIT" aqui faria o
  pipeline procurar candles que não existem;
- **`ATIVOS_DIARIO`** (novo) = o que pode ser REGISTRADO no Diário, conjunto
  maior. Os dropdowns do Diário nos 2 skins passaram a usar esta.

`VALOR_PONTO["BIT"] = 0.01`, derivado do próprio relatório (−700,00 pontos →
−R$7,00). Sem isso o P&L de BIT sairia **zerado em silêncio**, porque o lookup
tem default `0.0`.

### Pendente — implementação completa do BIT (planejar com o Jesué)

Hoje BIT só existe no Diário. Falta: ingestão de candle/tick (CSV e/ou MT5),
`features.py`, Quant Lab, Backtesting e os contratos pilares. Quando isso
existir, "BIT" entra em `ATIVOS` e as duas listas voltam a coincidir.
