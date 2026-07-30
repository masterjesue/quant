# Upgrade do Diário — plano de execução

**Status:** rascunho, aguardando sequenciamento do Jesué. Escrito em
2026-07-30 a partir de `upgrade diario.docx` (raiz do repo), para sobreviver
a uma eventual compactação da conversa.

## Já feito nesta sessão (ver commits `ec95745`, `cb3fe26`)

- Guarda-corpo operacional do dia (contador de trades/stops + checklist
  pré-trade) na aba Posição, 2 skins.
- Pop-up de print centralizado no viewport real (era "abre no meio da
  página, preciso rolar") + botões explícitos de zoom.
- **Bug 1 do docx:** Histórico cortava em 50 linhas mesmo com período
  explícito no seletor de datas — corrigido, testado ao vivo (100→420
  linhas ao só marcar data início, sem outro filtro).
- **Bug 2 do docx:** Ganho Total / Perda Total ausentes nas estatísticas —
  adicionados, confirmados ao vivo (R$ +675,00 / −855,00 na base atual).

## Decisões do Jesué (2026-07-30)

1. **Ordem confirmada**: A (categoria + split Topo/Fundo Duplo) → B (bloco
   Análise de Estratégia) → C (reclassificação de Outros) → D (Mentor de
   IA) → **F (redesenho TradeZella) por último**, projeto separado maior.
2. **Corte de data do item C**: `data_op >= 2026-07-01` = Suporte/
   Resistência/Pivô com o Professor Igor, sem registro (sistema não
   operacional). Antes disso = heurística de erro/teste/aprendizagem.
3. **Mentor de IA (item D)**: escopo/prompt só depois dos blocos visuais
   estarem prontos e estáveis.
4. **⚠️ SQ'98 será DESCONTINUADO** (dito a meio da sessão, depois do item A
   já ter sido espelhado nos 2 skins): já que o redesenho vai seguir o
   TradeZella, o skin '98 (`diario_app_98.py`/`dashboard_98.py`) deixa de
   receber features novas a partir daqui — "depois pensaremos no que fazer
   com ele". **Não apagar nada agora, só parar de espelhar.** Daqui pra
   frente (item B em diante, e principalmente o item F), qualquer trabalho
   de UI é SÓ no skin clássico (`diario_app_classico.py`/`dashboard.py`).
   Isto muda uma regra estabelecida no `CLAUDE.md` ("2 front-ends,
   nunca duplicam lógica" e a arquitetura de espelhamento constante ao longo
   desta sessão) — vale reler o `CLAUDE.md` antes de decidir se essa seção
   precisa de nota, numa sessão futura que for tratar disso formalmente.

## Pendente — organizado por bloco do docx

### A. Bloco "Confiabilidade por Estratégia"
1. Coluna ordenável (crescente/decrescente, clique no cabeçalho) — puro UI,
   sem decisão pendente.
2. **Categoria da estratégia**: Tendência / Reversão / Lateralidade, campo
   editável no cadastro. Pedido explícito: "já classifique as categorias
   que você identificar pela própria característica da estratégia (definição
   padrão nos livros)". Também pede desmembrar Topo/Fundo Duplo em duas
   estratégias (Topo Duplo na venda, Fundo Duplo na compra) — mesmo
   raciocínio da reclassificação de 2026-07-30 (migração com mapa auditável
   trade a trade).

### B. Bloco novo "Análise de Estratégia" (acima de "Confiabilidade por Estratégia")
3. Detalhe expandido (seta ▶) vira "mini dashboard" em blocos — já existe
   estrutura (`dt.detalhe_estrategia`), é refino visual.
4. Bloco unificando Por Ativo / Por Janela / Por Sala-Análise / Por Tags
   Setup-Erro num painel só, mais: melhor estratégia por horário, por
   direção, por ativo, por categoria.
5. Destaque ilustrado (troféu/medalha) pra Estratégia Vencedora, e destaque
   equivalente pra Estratégia Perdedora.

### C. Reclassificação de "Outros" — **precisa da sua memória, não é dado puro**
O docx distingue 2 casos que os números sozinhos não conseguem separar:
- Trades "Outros" **deste mês** (julho) → você lembra que eram do seu
  operacional S/R/Pivô com análise do Professor Igor, só sem registro (o
  sistema não estava operacional). Não são erro nem teste.
- Trades "Outros" de **meses anteriores** → esses SIM entram como teste de
  estratégia nova ou erro operacional.

Proposta de 3 categorias (sua sugestão no docx):
  - **Sem Estratégia** (Erro e Aprendizagem)
  - **Não Classificado** (desafio operacional/pessoal/técnico)
  - **Outras Estratégias** (teste intencional, dentro do gerenciamento)

Eu consigo rodar uma heurística (alvos/stops muito longos ou curtos sem
justificativa técnica, trades muito curtos, sequências rápidas — possível
estado emocional alterado) pra **propor** onde cada "Outros" cai, mas — como
na reclassificação de estratégias — a decisão final passa pelo botão Editar,
igual você pediu ali: "proposta automática + você revisa". **Preciso que
você confirme**: (a) qual é o corte de data exato entre "julho sem registro
= S/R com Igor" e "meses anteriores = teste/erro"; (b) se topa essa
heurística me faltando contexto que só você tem (estado emocional, se
lembra de detalhes específicos de algum trade).

### D. Mentor de IA — análise interpretativa diária
Bloco novo dentro de Análise de Estratégias, usando `ai_analyst.py`/Gemini:
curva de aprendizagem, evolução operacional/estratégica, próximos passos.
"Para controle de tokens, uma vez ao dia, analisando do dia anterior pra
trás." Isso é uma feature nova com custo de API recorrente — cabe confirmar
o escopo exato (o que exatamente entra no prompt, tamanho do contexto) antes
de implementar, não só a cadência.

### E. Auditoria de UX
"Faça uma análise de UX dessa seção de Estratégia, veja o que pode ser
ilustrado com gráficos e recursos visuais." — depende de A-D estarem
definidos primeiro (não dá pra revisar UX de algo que ainda não existe).

### F. Segunda iniciativa, maior e separada: clonar o dashboard do TradeZella
- Pesquisar o sistema de diário do TradeZella (docx já anexa 3 screenshots
  de referência: dashboard com Net P&L/Trade Win %/Avg Win-Loss/Profit
  Factor/Current Streak, calendário mensal com estatísticas por dia
  configuráveis, Zella Score em radar chart, Trade Duration Performance,
  Progress Tracker em heatmap semanal).
- Replicar os MESMOS elementos gráficos e funcionalidades, só trocando
  nomenclatura (ex: **Zella Score → Anticovardia Score**).
- Upgrade do calendário com as mesmas funcionalidades (estatísticas
  configuráveis por dia/semana, R-Multiple, winrate do dia).
- Adicionar bloco "Trades Recentes" (Recent Trades / Open Positions do
  print de referência).
- Respeitar o padrão light/dark já existente do projeto.

Isto é, na prática, um **redesenho completo da aba Estatísticas** — maior
que tudo dos blocos A-E juntos, e seria a fonte de verdade visual pra onde
os blocos A-E deveriam nascer (não faz sentido construir "Análise de
Estratégia" duas vezes, uma no estilo atual e depois refazer no estilo
TradeZella).

---

## Perguntas que precisam da sua decisão antes de eu continuar

1. **Ordem**: dá pra ler como "faça A-E primeiro, F depois" OU como "F é a
   direção visual nova, A-E devem nascer dentro dela". Qual é a leitura
   certa?
2. **Item C (Outros)**: preciso do corte de data exato e se topa a
   heurística automática como ponto de partida (sempre revisável no Editar).
3. **Item D (Mentor de IA)**: você quer que eu já defina o prompt/escopo
   exato agora, ou isso fica para depois dos blocos visuais estarem prontos?
