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
4. **⚠️⚠️ SQ'98 ESTÁ DESCONTINUADO — decisão reforçada 2x na mesma sessão.**
   Primeiro aviso (dito a meio da sessão, depois do item A já ter sido
   espelhado nos 2 skins): já que o redesenho vai seguir o TradeZella, o
   skin '98 deixa de receber features novas — "depois pensaremos no que
   fazer com ele". **Segundo aviso, mais forte** (depois de eu ainda ter
   espelhado a correção do travamento do Histórico pro '98, julgando ser
   "correção de regressão, não feature nova"): *"Eu vejo que está gastando
   tokens com o '98, não vamos mexer mais lá. Essa versão do Super Quant
   está descontinuada. Registre isso."*

   **Regra a partir daqui, sem exceção nenhuma**: NENHUMA mudança em
   `diario_app_98.py`/`dashboard_98.py`/`assets_98/` — nem feature nova, nem
   correção de bug, nem "só espelhar uma correção de 2 linhas". Se um bug
   for encontrado no '98, **anotar e seguir em frente**, nunca corrigir ali.
   Todo trabalho de UI daqui pra frente é 100% `diario_app_classico.py`/
   `dashboard.py`/`assets/`. **Não apagar nada do '98 agora** — só parar de
   tocar. Isto substitui a regra anterior do `CLAUDE.md` ("2 front-ends,
   nunca duplicam lógica" e o espelhamento constante ao longo desta sessão)
   precisa de nota, numa sessão futura que for tratar disso formalmente.

## 🔴 Problema CRÍTICO conhecido, adiado de propósito

**Histórico: lentidão absurda ao selecionar período e mostrar os trades.**
Reportado pelo Jesué em 2026-07-30 depois do fix do travamento (commit
`87532b7`) — o travamento parou, mas a troca de filtro/período ficou muito
lenta. Causa provável: o mesmo limite estrutural documentado em
MAPA_ESTRUTURA.md (custo de reconciliação do Dash escala com a contagem
TOTAL de componentes montados) — mesmo o teto de 100 trades (200 `<tr>`)
ainda é pesado o bastante pra ficar perceptível, rodando dentro do iframe
embutido ao lado do gráfico candlestick ao vivo.

**Decisão do Jesué: NÃO consertar agora.** Resolver só depois do redesenho
TradeZella (item F) — faz sentido, porque o redesenho provavelmente troca a
abordagem inteira (paginação de verdade, virtualização de lista, ou um
componente de tabela nativo em vez de uma `<table>` HTML montada linha a
linha pelo Python) em vez de só ajustar o teto de novo.

Quando for atacar isto: não é só "baixar o teto ainda mais" — vale
investigar se dá pra:
- usar `dash_table.DataTable` (paginação nativa, já importado no projeto
  noutro contexto) em vez de `html.Table` montada à mão;
- ou paginar de verdade (botão "página seguinte", em vez de "top N por
  recência");
- medir COM profiling real (não só adivinhar) onde o tempo vai — pode não
  ser só contagem de `<tr>`, pode ser `dt.avaliar_aderencia_tracados()`
  (chamado toda vez que o Histórico remonta) ou o registro de estratégias
  sendo recarregado do disco por linha.

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

### C. Reclassificação de "Outros" — ✅ CONCLUÍDO (2026-07-30, `migracao_reclassificacao_outros.py`)

69 trades reclassificados em 5 estratégias novas: `estrela_da_tarde` (1),
`srp_sem_registro` (23, o bloco de julho), `outras_estrategias` (2, #16 e
#70 — seguiram call de mentor sem setup próprio), `sem_estrategia_
aprendizagem` (16, dias com 4+ trades = overtrading mensurável, não
achismo), `nao_classificado` (27, sem nenhum sinal). "Outros" continua
cadastrada e ativa (fallback futuro), mas sem trade real associado.
Achado ao vivo: com "Outros" desmembrado, a **Estratégia Perdedora real
virou "Bounce em Suporte"** (−R$289/23) em vez do genérico "Outros" — o
sistema ficou mais útil, não só mais granular.

~~precisa da sua memória, não é dado puro~~ (texto original abaixo, mantido
pro histórico da decisão):
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

### D. Mentor de IA — análise interpretativa diária — ✅ CONCLUÍDO (2026-07-30)

Bloco novo dentro de "Análise de Estratégia", reusando o MESMO hub
Gemini/OpenRouter do chat (`ai_analyst._despachar_para_modelo`, extraído de
`analisar_com_pergunta_raw` — decisão explícita do Jesué: "lembra que
estamos usando um hub de IA e não somente o Gemini"), nunca uma chamada de
API própria.

- `dt.montar_resumo_para_mentor_ia(ate_data=None)` — resumo textual puro
  (default: até ontem, "para controle de tokens... analisando do dia
  anterior pra trás"): curva de aprendizagem (1ª vs 2ª metade), disciplina
  (dias com 4+ operações), top 3 / piores 3 estratégias, vencedora/
  perdedora geral. Testado em `testes/teste_resumo_mentor_ia.py`.
- `ai_analyst.gerar_analise_evolucao_operacional(resumo, modelo=None)` —
  monta o prompt (tom DIRETO e objetivo, decisão do Jesué; 3 blocos: CURVA
  DE APRENDIZAGEM / EVOLUÇÃO OPERACIONAL-ESTRATÉGICA / PRÓXIMOS PASSOS;
  "nunca apresente probabilidade como certeza"), despacha pelo hub, e só
  cacheia em disco (`mentor_analise_diaria.json`, escrita atômica +
  trava O_EXCL) se vier sucesso — um 429 temporário não gasta a "cota do
  dia" sem nunca ter mostrado nada.
- UI em `diario_app_classico.py` (`_bloco_mentor_ia`): botão estático
  ("Gerar análise de hoje" → "Gerar nova análise" depois da 1ª geração do
  dia — só um RÓTULO, nunca um bloqueio real, pra permitir regeração
  manual), `dcc.Loading` cobrindo a chamada bloqueante, callback
  `gerar_mentor_ia`.
- **Verificado com chamada real de API** (aprovação explícita do Jesué:
  "Sim, pode chamar a API para testar a chamada real"): rodou 2x contra os
  225 trades reais da base, `gemini-2.5-flash`, ~415-437 tokens por
  chamada, texto coerente e no tom pedido, sem inventar números fora do
  resumo. Confirmado ao vivo no navegador (clique real no botão, resposta
  renderizada, cache lido de volta).
- **Achado de ambiente durante a verificação** (não é bug de código): o
  processo filho `diario_app_classico.py` NÃO recarrega sozinho quando o
  arquivo muda no disco (sem reloader) — depois de editar o arquivo com o
  processo já no ar, foi preciso matar e resubir `dashboard.py` (que
  relança o filho) pra ver o código novo valendo. Vale lembrar em sessões
  futuras: se uma mudança de UI "não aparece" mesmo sem erro nenhum, checar
  se os 2 processos foram reiniciados DEPOIS da última edição salva.

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
