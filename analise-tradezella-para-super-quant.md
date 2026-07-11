# TradeZella — Análise Completa para o Módulo de Diário de Trade do Super Quant

> Documento de referência técnica/produto. Objetivo: destrinchar telas, recursos e lógica de funcionamento do TradeZella (líder de mercado em diário de trade, 100k+ usuários) para servir de benchmark ao módulo de diário que estamos construindo dentro do Super Quant, e apontar o que se conecta diretamente com o **Mentor** (nosso componente de análise interpretativa).
>
> **Contexto importante do Super Quant**: não é um robô/sistema de trading algorítmico — não executa ordens automaticamente. É uma **ferramenta de análise quantitativa que apoia decisões de price action tomadas manualmente por Jesué**. O Mentor comenta/interpreta sinais e dados para apoiar um trader discricionário, e não substitui a execução humana. Essa distinção muda a leitura de "o que aplicar" do TradeZella: como o próprio Jesué entra e sai das operações manualmente, os recursos do TradeZella voltados a comportamento humano, emoção e qualidade de execução (Trade Replay, revenge trading, aderência a regras, notas manuais) tendem a ser **mais relevantes**, não menos, do que numa leitura "robô 100% automático".

---

## 1. O que é o TradeZella, em uma frase

Uma plataforma de **journaling + analytics + backtesting** para traders: importa trades automaticamente do corretor, calcula dezenas de métricas, e usa uma camada de IA (Zella AI) para transformar dados brutos em diagnóstico comportamental. Não executa ordens — é puramente pós-trade e pré-trade (planejamento), nunca broker.

Estrutura de navegação (sidebar): **Dashboard → Journal (Trade Log + Daily Journal/Notebook) → Calendar → Reports → Strategies/Playbooks → Backtesting → Trade Replay → Zella AI → Community → Zella University → Prop Firm Sync → Settings**.

---

## 2. Dashboard (tela inicial)

Tela de "health check" imediato, dividida em duas seções de widgets configuráveis:

**Seção superior (KPIs rápidos):**
- Net P&L (lucro/prejuízo líquido total)
- Account Balance & P&L
- Trade Win % (percentual de trades vencedores)
- Profit Factor (lucro bruto ÷ prejuízo bruto — acima de 1.0 = lucrativo)
- Day Win % (percentual de *dias* positivos — métrica diferente do win rate por trade)
- Avg Win/Loss Trade

**Seção inferior (aprofundamento):**
- **Zella Score** (ver seção 3)
- Daily Net Cumulative P&L
- Win % / Avg Win / Avg Loss ao longo do dia
- Trade Time Performance (lucratividade por horário)
- Trade Duration Performance (lucratividade por tempo de operação em posição)
- Net Daily P&L (gráfico de colunas por dia)
- Daily & Cumulative Net P&L (colunas + área combinadas)
- Recent Trades & Open Positions
- Calendar (heatmap verde/vermelho/cinza de dias)
- Account Balance
- Report (widget customizável, até 3 métricas)
- Yearly Calendar (visão do ano inteiro)

**Modos de visualização (troca via botão "View"):**
- Dollars (padrão) / Percentage / **Privacy** (esconde saldo e P&L — ótimo para prints/compartilhamento) / **R-Multiple** (baseado no risco inicial da operação) / Ticks (futuros) / Pips (forex) / Points (futuros)

**Multi-conta**: seleção de uma ou várias contas simultaneamente no mesmo dashboard.

---

## 3. Zella Score — a métrica-âncora do produto

Um número único de **0 a 100** que resume "saúde" da operação, combinando 6 métricas ponderadas por importância (cada uma pontuada de 0–100 individualmente):

1. Win Rate (% de trades vencedores)
2. Avg Win/Loss Ratio (tamanho médio do ganho vs. perda)
3. Profit Factor
4. Max Drawdown % = (maior queda pico-vale) ÷ (P&L acumulado máximo antes da queda) × 100
5. Consistência (variação de performance ao longo do tempo)
6. Disciplina/aderência a regras

**Por que isso importa para nós**: é o principal artifício de produto do TradeZella — um "score de confiança" visual que resume dados complexos em algo que cabe num crachá. Isso é **exatamente o tipo de saída que o Mentor poderia gerar** para cada sinal/estratégia do Super Quant: um "Score de Confiabilidade do Sinal" combinando profit factor, drawdown, consistência etc. da estratégia que gerou aquele sinal.

---

## 4. Journal — Trade Log (registro de operações)

- Toda operação (importada via corretor ou manual/CSV) aparece com: preço de entrada/saída, tamanho, P&L, timestamp.
- **Busca e filtro** por data, símbolo, faixa de lucro, tags.
- Campos por trade: stop loss, target, comissões (tracking automático), notas de tese, screenshots/anexos de gráfico, **rating manual** da execução (para filtrar depois "meus melhores/piores trades").
- Vínculo direto de cada trade a uma **Strategy/Playbook**.
- Tags customizadas livres: setup, emoção, erro cometido, etc.
- "Jump to Trade Replay" — abre o replay do preço exatamente daquela operação, sem sair da tela.
- Marcação automática de opções vencidas como fechadas (housekeeping automático).
- Upload em lote (CSV) para quem não tem corretor integrado.

**Aplicação direta ao Super Quant**: como Jesué opera manualmente (o Super Quant analisa, não executa), esse bloco é diretamente equivalente ao caso de uso original do TradeZella — a diferença é que aqui o Mentor já tem, no momento da entrada, o sinal/contexto quantitativo que gerou a decisão, então o trade log pode nascer pré-preenchido com a leitura que o Mentor fez *antes* da execução (não só depois, como no diário tradicional). Isso permite comparar sistematicamente "o que o Mentor sinalizou" vs. "o que Jesué de fato executou" — um relatório de aderência mais rico do que o do próprio TradeZella, porque nasce do dado quantitativo, não de uma regra escrita manualmente num Playbook.

---

## 5. Daily Journal / Notebook

- Espaço de texto livre por sessão/dia — plano de trading, checklist pré-mercado, reflexões pós-mercado.
- Organização de notas por tópico, estratégia ou período.
- Seleção de intervalo (semanal/mensal/trimestral) para revisão retroativa.
- Ícone de "nota" aparece no Calendar nos dias que têm entrada de diário.

**Aplicação**: aqui faz sentido um modelo híbrido — o **Mentor escreve automaticamente** um rascunho do resumo interpretativo do dia/sessão (equivalente ao "Session Review" do Zella AI, seção 11.3), e Jesué complementa com notas manuais de contexto que o dado quantitativo não captura (ex.: leitura discricionária de contexto macro, notícia, sensação de mercado). É exatamente o padrão que o próprio TradeZella recomenda ("deixe a IA fazer o scoring, adicione notas manuais só onde há insight que o número não mostra") — muito aplicável aqui, já que Jesué é quem efetivamente decide e executa.

---

## 6. Calendar

- Visão mensal em heatmap: dias verdes (lucro), vermelhos (prejuízo), cinza (breakeven).
- Clique no dia abre o detalhamento e o journal daquele dia.
- **Yearly Calendar** — a mesma lógica só que o ano inteiro numa tela (bom para revisão macro).
- Cálculo do P&L do dia documentado publicamente em artigo de ajuda (transparência de metodologia).

---

## 7. Reports (50+ relatórios)

Esse é o motor analítico principal — a fonte de "insight" bruto que depois vira material para IA:

- **Strategy Report** — compara profit factor, win rate, P&L por estratégia/playbook cadastrada.
- **Day & Time Report** — lucratividade por dia da semana e por faixa de horário.
- **Symbol Report** — performance por ativo/ticker.
- **Tags Report** — performance filtrada por tag customizada (setup, emoção, erro).
- **Risk Report** — R-multiple, tamanho de posição vs. risco planejado.
- **Win vs. Loss Comparison**.
- **Zella Scale** — compara P&L realizado contra o P&L potencial máximo do movimento ("você capturou 40% ou 80% do movimento disponível?").
- **Best Exit Analysis** — recalcula, com os dados reais do trade, "e se eu tivesse saído em outro ponto?" (ex: "sair via trailing de 1 ATR teria melhorado o resultado em 15%").
- Recursos visuais: **multi-layered charts** (cruzar duas variáveis, ex. contagem de trades × P&L por hora), **dynamic tables** (quebra por dia/hora/símbolo), **cross-analysis** (cruzamento livre de dois filtros).
- 30+ filtros combináveis (estratégia, hora, dia, ticker, tempo de posição etc.).

**Aplicação direta**: Zella Scale e Best Exit Analysis são os dois relatórios mais originais/diferenciados do produto — nenhum concorrente tem. Para o Super Quant, um "Best Exit Analysis" faria MUITO sentido: como as saídas são regidas por regras algorítmicas, o Mentor pode simular contrafactuais ("se o stop tivesse sido X ATR, o resultado do mês teria sido Y") e isso vira insumo direto para calibrar a estratégia — não é feature de "produto para o usuário final trader", é uma ferramenta de **otimização do próprio robô**.

---

## 8. Strategies / Playbooks

- Cada Playbook documenta: condições de entrada, critérios, parâmetros de risco, regras de saída.
- Trades e sessões de backtesting são **vinculados** a um Playbook.
- Relatório de aderência: performance de trades que seguiram 100% as regras do Playbook vs. os que desviaram.
- Templates prontos de estratégias reais (25+) para não começar do zero.
- Limite de playbooks varia por plano (3 no básico, ilimitado nos planos superiores).

**Aplicação**: mapeia bem para os **setups/estratégias de price action que Jesué opera**. Cada padrão que o Mentor reconhece (ex. um tipo de rompimento, uma configuração específica de estrutura de mercado) pode virar um Playbook formal com critérios de entrada/saída documentados. O relatório de aderência do TradeZella aqui é totalmente aplicável no sentido original: como Jesué decide manualmente, existe desvio humano real entre "o que o Mentor sinalizou" e "o que foi de fato executado" — esse é justamente o tipo de gap que vale medir (entrou antes do sinal completo? saiu antes do alvo por ansiedade? aumentou o tamanho fora da regra?).

---

## 9. Backtesting 2.0

- Disponível em todos os planos, ilimitado, dados históricos desde **2014** (ações, opções, futuros, forex, cripto).
- Até 5 símbolos simultâneos, até 8 gráficos abertos ao mesmo tempo em timeframes diferentes.
- Execução de ordens simuladas: market, limit e stop (não só "market" como ferramentas mais simples do mercado).
- Position sizing automático baseado em parâmetros de risco definidos previamente (evita lote arbitrário).
- Toda operação de backtest é **automaticamente logada no diário ao vivo** com entrada/saída/P&L e pode ser tagueada e linkada a um Playbook sem trocar de tela.
- Função "Go-To" e replay para revisitar momentos-chave do histórico.
- Calendário econômico integrado (contexto de notícia).
- Indicadores nativos (ex.: HTF Po3, conceitos ICT) já embutidos.

**Aplicação (revisado)**: aqui vale separar duas coisas que o TradeZella funde numa só tela. (1) **Backtesting estatístico** — rodar um setup de price action identificado pelo Mentor contra o histórico e devolver win rate, profit factor, expectância e tamanho de amostra: isso é leve de implementar, não exige motor de simulação visual, e é o que dá ao Mentor legitimidade quantitativa antes de Jesué confiar num padrão ao vivo — **alta prioridade**. (2) **Backtesting estilo plataforma** (múltiplos gráficos simultâneos, ordens limit/stop simuladas, replay tick-a-tick) — é a camada pesada de engenharia do TradeZella e não é o core do que o Mentor precisa entregar; fica para uma fase avançada, se algum dia for necessário simular execução visualmente. O padrão arquitetural que vale copiar de qualquer forma: **todo trade "de teste" cai no mesmo journal e usa a mesma estrutura de tags/relatórios dos trades reais**, evitando duplicar lógica de relatório para dado simulado vs. real.

---

## 10. Trade Replay

- Rebobina o gráfico exatamente até o momento da entrada e reproduz o candle-a-candle até a saída, no timeframe real.
- Controle de velocidade, segundo gráfico simultâneo, tags de erro/estratégia durante o replay.
- Recurso mais citado como diferencial competitivo (poucos concorrentes têm).
- "Session Replay" (dia inteiro, não só um trade) trancado nos planos superiores.

**Aplicação (revisado — média prioridade)**: como Jesué é quem decide e executa manualmente, este é um recurso conceitualmente valioso — rebobinar o candle exatamente até o momento da entrada e assistir o preço se desenrolar até a saída é o jeito mais direto de revisitar *hesitação, entrada antecipada, saída por medo*. Na prática, porém, é a peça de maior custo de engenharia deste documento: exige dados de candle históricos em granularidade fina, um player de replay sincronizado, e integração com o momento exato em que o Mentor emitiu o sinal. Por isso fica em **média prioridade**: vale revisitar depois que o núcleo analítico (trade log com dupla camada, reports, score, backtesting estatístico) estiver maduro.

---

## 11. Zella AI — a peça mais relevante para o nosso Mentor

Esta é a seção mais importante do documento, porque é o análogo direto do que estamos construindo.

### 11.1 Filosofia declarada
"A IA veio primeiro, o diário é como ela lembra de você" — ou seja, o produto foi redesenhado para que o **journal seja a memória de longo prazo da IA**, não um chat solto. Não dá "picks" nem sinais de compra/venda — só analisa dados que já existem.

### 11.2 Estrutura: 3 estágios do dia
1. **Plan** (pré-mercado): sentimento de mercado, calendário de eventos, plano do dia construído em cima dos setups historicamente mais fortes do próprio usuário.
2. **Trade** (intra-sessão): monitora execuções em tempo real contra o plano e avisa quando o trader "desvia".
3. **Analyze** (pós-sessão): revisão com insights de IA por trade, auto-tagging, recomendações específicas para o dia seguinte.

### 11.3 Agentes autônomos (não é "chat que você precisa acionar" — roda sozinho)
- **Market Sentiment Briefing**: analisa condições de mercado antes da sessão, gera um briefing pronto para colar no plano do dia.
- **Trade Auto-Tagger**: tagueia cada trade no momento da importação, segundo regras definidas em linguagem natural pelo usuário (ex.: "marque trades que bateram 2R", "marque por timeframe de entrada", "marque a favor ou contra a tendência").
- **Session Review**: ao fim do dia, compara o que foi planejado com o que realmente aconteceu, trade a trade, sinaliza trades de vingança (revenge trades) e desvios de tamanho de posição, e escreve a narrativa da sessão automaticamente.
- É possível criar **agentes customizados** descrevendo em português/inglês simples o que o agente deve observar e fazer.

### 11.4 Detecção de padrões comportamentais (Zella Insights)
Isso é o coração do "Mentor" deles — vale a pena olhar com atenção porque é isso que dá "personalidade analítica" ao produto:

- **Revenge trading**: perda seguida de nova entrada em minutos, com tamanho maior, no mesmo instrumento ou correlacionado. O sistema soma o custo em $ desse padrão ao longo do histórico.
- **Tilt cascade**: 3+ trades em sucessão rápida após um drawdown, cada um violando pelo menos uma regra — assinatura de dados: tempo entre entradas comprimido, tamanho crescente, tempo de posição decrescente.
- **FOMO entries**: entradas que não batem com nenhuma Strategy cadastrada, feitas logo após um movimento grande no ativo que o trader estava observando.
- **Análise comparativa histórica automática**: ex. "você tem 23 trades de breakout no NQ entre 10:00–10:30 nos últimos 6 meses, com 39% de acerto e profit factor 0.8; à tarde, os mesmos setups têm 58% de acerto e PF 1.9" — o sistema já entrega o corte pronto, sem o usuário precisar montar o filtro manualmente.
- Confiabilidade estatística escalona com volume: **30-50 trades = insight limitado; 200+ = padrão comportamental confiável; 500+ = edge estatístico real**.

**Aplicação direta (correção)**: como Jesué executa manualmente, esses padrões — revenge trading, tilt cascade, FOMO entries — são exatamente os que se aplicam sem nenhuma adaptação, porque são comportamentos humanos reais, não hipotéticos. É um dos blocos de mais alto valor para o Mentor: cruzar os próprios sinais/entradas de Jesué ao longo do tempo para identificar se, por exemplo, ele costuma entrar cedo demais em relação ao sinal do Mentor, aumentar o tamanho depois de uma perda, ou ignorar o Mentor com mais frequência em certos horários/ativos.

### 11.5 Tom e postura da IA (diferencial declarado deles)
- Declaradamente **não-agradável por design**: "Zella empurra de volta quando os dados dizem o contrário" — ex. resposta real citada: *"quarta-feira não é seu melhor dia em Tesla. Sua taxa de acerto cai para 28% depois das 14h. Seus revenge trades depois de uma perda custaram $3.200 no mês passado."*
- Contraste explícito com "IA generalista" (ChatGPT/Claude/Gemini genérico): a diferença alegada é que a IA deles nunca fala em abstrato — toda resposta cita números da base de dados real do próprio usuário, nunca teoria genérica.
- Memória persistente: "o que você sabe sobre mim?" é uma pergunta suportada — a IA guarda estilo, objetivos e erros recorrentes entre conversas.

### 11.6 Chat conversacional
- Atalho global (⌘+Z) abrível de qualquer tela — a IA já chega sabendo o contexto da tela aberta (trade específico, relatório, dashboard etc.), sem precisar re-explicar.
- Exemplos de perguntas suportadas: "Por que eu perco toda sexta-feira?", "Mostra meu padrão de setup A+", "Quando eu aumentei o tamanho rápido demais na semana passada?"
- Sistema de créditos por mensagem (consumo varia por complexidade do pedido), renovado por ciclo de cobrança.
- Vocabulário nativo de trading (ICT concepts, order flow, VWAP exhaustion, breaker blocks, fair value gaps, regras de prop firm) — ou seja, foi ajustado/fine-tuned para a linguagem do trader, não é um LLM genérico "cru".

### 11.7 O que a IA explicitamente NÃO faz
- Não dá sinal de compra/venda, não prevê mercado, não executa ordem nem gerencia posição.
- Só acessa dados que já estão dentro da própria conta TradeZella do usuário — sem enviar dados para fora.

**Por que essa seção 11 é a mais importante do documento**: o Mentor do Super Quant já está no caminho certo ao ser "interpretativo/analítico" em vez de preditivo. O modelo do Zella AI valida essa escolha de produto (evitar ser "signal service" e ser "coach dos próprios dados"). As peças que fazem mais sentido priorizar para nós, na ordem de valor/esforço:

1. **Detecção de padrões com assinatura de dados** (tipo revenge trade / tilt cascade) — isso é aplicável mesmo a um robô, adaptado para "padrões de degradação de sinal" (ex.: sequência de perdas seguidas de aumento de exposição automática, ou uma estratégia específica operando fora do regime de mercado em que foi validada).
2. **Narrativa automática pós-sessão** (equivalente ao Session Review) — o Mentor já faz comentário interpretativo; formalizar isso em 3 estágios (plano → execução → revisão) dá estrutura editorial ao que já existe.
3. **Respostas ancoradas em dado real, nunca genéricas** — é o único jeito de a IA ser útil e não apenas "bonita"; qualquer resposta do Mentor deveria citar número/trade específico, não fazer afirmação vaga.
4. **Postura "não-agradável"**: o Mentor deveria ser capaz de dizer "essa estratégia está underperformando, considere pausar" mesmo que isso seja uma notícia ruim — replicar viés de concordância bajuladora anularia o valor do produto.
5. Agentes autônomos configuráveis em linguagem natural — mais avançado, fica para uma fase 2.

---

## 12. Prop Firm Sync

- Sincroniza contas de prop firms (FTMO, TopStep, Apex, Tradeify e mais 12) num painel único.
- Monitoramento em tempo real de: meta de lucro, drawdown diário, regra de consistência — com aviso **antes** de a regra ser quebrada.
- **Failure Analysis**: quando uma conta falha, identifica o padrão de causa (violação de regra, overtrading, erro comportamental específico) e liga de volta aos trades reais do diário.
- **Challenge Simulator (beta)**: estima probabilidade de passar/reprovar numa avaliação antes mesmo de comprá-la, com base no histórico do próprio trader.

**Aplicação**: não é diretamente aplicável ao Super Quant hoje (não operamos capital de prop firm), mas o padrão de **"alerta preventivo antes de violar limite de risco"** é um recurso de altíssimo valor que vale muito a pena portar: ex. alertar antes que o robô ultrapasse um limite de drawdown diário/mensal configurado, não só registrar depois do fato.

---

## 13. Community, Zella University e Mentoria

- **Community**: salas privadas com amigos/mentor/grupo, compartilhamento de trades, reação ao vivo, "challenges" — recriar a vibe de mesa de operações sem o custo físico.
- **Mentor tools (para coaches/mentores)**: acesso de leitura completo à conta do aluno (dashboard, trade log, notebook, estratégias, relatórios), possibilidade de deixar feedback direto em entradas específicas do diário, pastas de coaching organizadas, controle de privacidade por parte do aluno (escolhe quais contas compartilhar, revoga acesso quando quiser).
- **Zella University**: hub de educação incluso em todos os planos — webinars, tutoriais do próprio app, trilha sobre psicologia de trading, gestão de risco, order flow.

**Aplicação**: baixa prioridade imediata — é a camada "social/educacional" do produto, mais relevante para um SaaS B2C com milhares de usuários do que para uma ferramenta interna de um robô proprietário. Se o Super Quant algum dia virar produto para terceiros, vale revisitar.

---

## 14. Integrações e dados

- Sincronização com **500+ corretoras/plataformas** (Interactive Brokers, Tradovate, Rithmic, DXtrade, TradeLocker, NinjaTrader, ThinkorSwim, Schwab, Robinhood, MetaTrader 4/5, cTrader etc.), via API ou upload CSV para quem não tem integração direta.
- Cobertura de ativos: ações, opções, futuros, forex e cripto — em todos os planos, sem segmentação por tier.
- Alegação de propriedade dos dados pelo usuário ("your data is yours") como princípio declarado de produto.

---

## 15. Estrutura de planos (contexto de mercado, não é recomendação de compra)

| Plano | Preço aprox. | Limites-chave |
|---|---|---|
| Basic | ~$29/mês | 1 conta, 3 Playbooks, replay só de trade individual (não sessão inteira) |
| Essential | intermediário | similar ao Basic com ajustes |
| Premium | $49/mês | Playbooks ilimitados, Session Replay liberado |
| Pro | ~$399/ano | Mesmos recursos do Premium, mais barato em compromisso anual |

Sem trial gratuito, sem reembolso — modelo de assinatura direta. *(Preços podem variar; não usar como cotação definitiva sem checar o site oficial.)*

---

## 16. Síntese — o que priorizar no módulo de diário do Super Quant

*(Revisado considerando que o Super Quant apoia decisões manuais de price action de Jesué — não executa nada sozinho.)*

### Alta prioridade (alto valor / bom encaixe com o que já existe)
- **Trade log com dupla camada**: o que o Mentor sinalizou no momento vs. o que Jesué de fato executou (preço de entrada real, tamanho, saída). Essa comparação sinal-vs-execução é o dado mais valioso que o TradeZella não tem por padrão (porque ele não gera sinal nenhum) e que o Super Quant tem de graça, por natureza.
- **Detecção de padrões comportamentais com assinatura de dados** (revenge trading, tilt cascade, FOMO entries, adaptados ao vocabulário de price action de Jesué) — aplica-se sem nenhuma adaptação, é comportamento humano real.
- **Backtesting estatístico** (não estilo plataforma com múltiplos gráficos/ordens simuladas, e sim validação estatística de setups): rodar um padrão de price action identificado pelo Mentor contra o histórico e devolver win rate, profit factor, expectância e amostra — para Jesué saber, antes de confiar num setup ao vivo, se ele tem edge estatístico real ou é ilusão de poucos casos. É o tipo de camada que dá ao Mentor legitimidade quantitativa sem exigir motor de simulação visual complexo.
- **Narrativa automática pós-sessão do Mentor** (equivalente ao Session Review): compara plano/sinal do dia com execução real, sinaliza desvios, escreve o resumo da sessão.
- **Dashboard com Score equivalente ao Zella Score**: 0–100 combinando win rate, profit factor, drawdown, consistência — por setup de price action, não por "estratégia algorítmica".
- **Reports por Setup/Padrão, Day & Time, Symbol e Tags** — base analítica mínima; sem isso não existe diário, só um log.

### Média prioridade
- **Playbooks = setups de price action documentados** (critérios de entrada/saída/risco de cada padrão que Jesué opera), com relatório de aderência às regras do próprio setup.
- **Trade Replay** (rebobinar o candle até a entrada/saída real) — recurso de alto valor conceitual, mas rebaixado para média prioridade porque adiciona uma camada grande de complexidade técnica (dados de candle históricos tick-a-tick ou intraday, player de replay, sincronização com o momento exato do sinal do Mentor). Vale revisitar depois que o núcleo analítico estiver maduro.
- **Best Exit Analysis / Zella Scale** — simular saídas alternativas com o preço real, útil tanto para Jesué (educação/calibração pessoal) quanto para o Mentor recalibrar critérios de sinal.
- **Calendar heatmap** — visualização simples e de alto impacto, baixo custo de implementação.
- **Alerta preventivo de risco** (inspirado no Prop Firm Sync) — avisar antes de um limite de drawdown/exposição diário ser estourado, não só registrar depois.
- **Modo Privacy** (esconder valores em $) — útil se telas forem compartilhadas externamente.

### Baixa prioridade / não se aplica bem ao nosso contexto
- **Backtesting completo estilo plataforma** (motor de simulação com múltiplos gráficos, ordens limit/stop simuladas, replay tick-a-tick) — a versão "pesada" do backtesting, distinta do backtesting estatístico já listado em alta prioridade. Só vale considerar numa fase avançada, se o Mentor precisar simular execução visual, não só validar estatística de setups.
- **Prop Firm Sync completo** — só relevante se Jesué operar conta de prop firm no futuro.
- **Community / mentoria social / Zella University** — camada B2C de produto para massa de usuários; não é prioridade para uma ferramenta pessoal/interna.

---

## 17. Observação final sobre o "Mentor"

Com Jesué operando manualmente, o Super Quant está estruturalmente mais próximo do caso de uso original do TradeZella do que eu havia considerado inicialmente — a diferença central é que o Mentor entra *antes* da decisão (sinal/leitura quantitativa de price action), enquanto o TradeZella só analisa *depois* que o trade já foi fechado. Isso abre uma vantagem de produto que o TradeZella não tem: dá para medir sistematicamente o gap entre "o que o dado quantitativo indicava" e "o que o trader discricionário de fato fez" — e é exatamente aí que vive o maior valor de um mentor: não em documentar o passado, mas em quantificar onde o julgamento humano diverge do sinal e se essa divergência historicamente ajuda ou atrapalha.

O ponto mais transferível do design da Zella AI continua valendo integralmente: a IA nunca fala em abstrato, sempre ancorada em número real e específico do próprio histórico; ela é construída para *discordar* do usuário quando os dados discordam; e ela tem memória entre sessões, então cada nova análise soma contexto à anterior em vez de recomeçar do zero. Se o Mentor seguir essas três regras — **(1) sempre citar dado concreto, (2) não validar automaticamente a decisão de Jesué, (3) manter memória de padrões comportamentais já identificados entre sessões** — ele replica o que faz o Zella AI funcionar, com a vantagem extra de já nascer sabendo qual era o sinal antes da execução.
