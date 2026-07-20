# Especificação Conceitual — Força de Zonas de Suporte/Resistência

## Propósito deste documento

Este documento consolida uma discussão conceitual sobre como o sistema deve avaliar
a força de zonas de suporte e resistência, separando claramente **O QUÊ** deve ser
calculado e **POR QUE**, sem prescrever **COMO** implementar. As decisões de arquitetura,
estrutura de dados, localização no código e escolha de técnicas de cálculo devem ser
feitas pelo Claude Code, investigando o projeto existente e seus padrões atuais.

Nenhum conceito abaixo deve ser descartado ou simplificado sem antes ser discutido —
cada bloco e cada fator têm uma justificativa própria que precisa ser preservada na
implementação.

---

## 1. Lacuna identificada no sistema atual (ponto de partida da investigação)

O sistema hoje (print de referência: painel "Price Action") já calcula e exibe:
- Preço da zona e range (ex: `177,658 · zona 177,600–177,725`)
- Número de toques
- Idade da zona em candles
- ATR da zona
- Tags de estado: `REGIÃO FORTE`, `PULLBACK CONFIRMADO HOJE`, `ROMPIDA`, `ROMPEU HOJE`
- Uma probabilidade única de "bounce" e uma de "rompimento" (que hoje somam 100%,
  ex: 93% bounce / 7% rompimento)
- Um parágrafo de interpretação
- Um bloco separado de "topo/fundo em formação", com contagem de candles de confirmação

**O problema observado:** ao comparar os cards do print, zonas com números de toques e
idades bem diferentes (ex: 12 toques/217 candles vs. 14 toques/888 candles vs. 11
toques/222 candles) receberam a **mesma probabilidade** (93%) e o **mesmo texto de
interpretação**, palavra por palavra. Isso é uma forte evidência de que a probabilidade
hoje é herdada de um bucket genérico por regime de mercado (ex: "mercado lateral → 93%"),
e não de um cálculo específico por zona, que leve em conta os fatores individuais de
cada uma.

**Tarefa de investigação para o Claude Code:** antes de qualquer mudança, mapear no
código atual:
- Onde e como a probabilidade de bounce/rompimento é hoje calculada;
- Se de fato existe um bucket/lookup por regime de mercado em vez de um cálculo
  por zona;
- Quais dos fatores dos três blocos abaixo (se algum) já influenciam esse cálculo,
  mesmo que parcialmente ou de forma implícita;
- Onde a lógica de "topo/fundo em formação" (rodapé do painel) está implementada,
  já que ela é uma peça-chave do Bloco 2 abaixo e hoje parece estar desconectada da
  análise de zona.

Essa investigação deve ser feita e reportada antes de qualquer alteração de cálculo,
para confirmar ou refutar essa hipótese e entender o alcance real do problema.

---

## 2. Conceito central: três perguntas diferentes, não uma força única

Um único "score de força" mistura variáveis que se comportam de forma **oposta**
conforme os fatores mudam (ex: mais toques aumenta confirmação direcional mas reduz
combustível). Por isso, a força de uma zona deve ser decomposta em respostas
separadas para perguntas distintas:

1. **P(bounce)** — probabilidade de o preço reagir/reverter nessa região.
2. **P(rompimento)** — probabilidade de o preço romper a região.
   - Importante: **isso deve continuar sendo exibido explicitamente como hoje**
     (ex: "71% bounce / 29% rompimento"), lado a lado, mesmo que sejam
     estatisticamente complementares. Não abstrair, não remover, não substituir por
     um único número. O usuário quer ver os dois valores lado a lado, sempre.
3. **Magnitude esperada, condicional ao bounce** — dado que o preço reagiu, qual o
   alcance provável desse movimento (curto, moderado, longo). Essa é uma informação
   nova que não existe hoje e que vem do conceito de "combustível de liquidez"
   (Bloco 1 abaixo): zonas fresh têm mais potencial de movimento mas são menos
   testadas; zonas testadas 2-3 vezes tendem a confirmar melhor a intenção
   direcional, mas com menos combustível para um movimento grande.

Essa distinção (probabilidade de reação vs. magnitude do movimento resultante) é a
espinha dorsal de toda a especificação. Nenhum cálculo deve colapsar essas duas
dimensões de volta em um único número.

---

## 3. Bloco 1 — Combustível de Liquidez (frescor do nível)

Mede quanto "estoque" de ordens/interesse ainda existe represado na região —
determina principalmente o **potencial de magnitude** do movimento, não
diretamente a probabilidade de reação.

**Fatores a considerar:**

1. **Número de toques.** Fator já existente no sistema. Zona nunca testada = mais
   combustível; cada toque adicional reduz o combustível, mas não de forma linear —
   os primeiros toques (0→1→2) reduzem mais que os toques seguintes (o efeito marginal
   de cada toque adicional diminui). A partir de um número alto de toques (ex: 4+),
   considerar a zona como tendo combustível residual baixo.

2. **Idade do nível.** Fator já existente no sistema (idade em candles), mas hoje não
   parece influenciar a probabilidade calculada. Zonas envelhecem e perdem
   relevância mesmo sem serem tocadas — não é um corte binário, é um decaimento
   gradual: quanto mais candles desde a formação, menor o peso do combustível
   original, independentemente do número de toques.

3. **Volume/intensidade na origem do impulso.** Quanto maior o desequilíbrio
   comprador/vendedor que originou o nível, mais ordens reais presume-se que
   ficaram represadas ali. Na ausência de volume confiável para os contratos WIN/WDO,
   considerar proxies como range/velocidade da vela de origem em relação à média
   recente.

4. **Tamanho/limpeza da base que originou o nível.** Base estreita e rápida (poucos
   candles antes do impulso) sugere ordens concentradas; base larga e demorada
   sugere ordens diluídas, zona mais difusa e menos confiável.

5. **Confluência com timeframe maior.** Nível que aparece tanto no timeframe
   operacional (5min) quanto em timeframes maiores tem mais peso — reflete decisão
   de mais participantes, não ruído de curto prazo.

6. **Profundidade de mitigação.** Não é só "tocou ou não" — é quanto do range da
   zona o preço já percorreu para dentro dela em cada toque. Um toque superficial
   na borda consome menos combustível que uma penetração profunda na zona.

---

## 4. Bloco 2 — Qualidade da Aproximação (como o preço chega na região)

Este bloco **não existe hoje no sistema** e é o mais importante dos três para
determinar a **intenção direcional / probabilidade de reação vs. continuação**.
Analisa o comportamento do preço na perna que leva até a zona, antes de qualquer
contato com ela.

**Fatores a considerar:**

1. **Lei de Esforço vs. Resultado.** Compara o esforço investido (proxy de volume,
   ou range/velocidade das velas na ausência de volume confiável) com o resultado
   obtido (distância percorrida) nas velas que antecedem o toque. Se o esforço
   aumenta mas o resultado (distância) não acompanha, é sinal de enfraquecimento de
   momentum antes mesmo do toque — um alerta de possível exaustão.

2. **Estrutura do movimento — impulsivo vs. corretivo.** Poucos candles com corpo
   grande e range alto (movimento decisivo, direto) versus muitos candles pequenos
   e sobrepostos consumindo tempo para percorrer pouca distância (indecisão,
   corretivo). A "limpeza" do movimento de aproximação é um sinal relevante de
   convicção.

3. **Chegada em canal vs. chegada solta/errática.** Se o preço se move dentro de um
   canal bem definido até tocar a região, isso é um contexto diferente de uma
   aproximação errática. Atenção especial a canais que "esticam" (aceleração além
   do canal normal) pouco antes do toque — sinal de excesso especulativo.

4. **Tipo de correção antes da chegada — em preço vs. em tempo.** Correção em tempo
   (lateralização sem devolver muito preço) geralmente indica força retida por
   quem está posicionado. Correção em preço mais profunda (retração maior) sugere
   mais dúvida entre os participantes. Ambas as métricas devem ser reportadas
   separadamente, não combinadas num único número.

5. **Estrutura de topos e fundos na perna de aproximação.** Este é o fator mais
   direto de todos e deve se conectar com a lógica de "topo/fundo em formação" que
   já existe no sistema (hoje isolada, no rodapé do painel). Numa aproximação de
   alta em direção a uma resistência: se os fundos intermediários estão subindo
   (higher lows), é sinal de força retida pelos compradores. Se os fundos
   intermediários já estão caindo mesmo com o preço ainda subindo, é uma
   divergência estrutural — sinal de enfraquecimento antes mesmo do toque na
   região. Vale simetricamente para aproximações de baixa em direção a suporte.

6. **Velocidade da última perna antes do toque (aceleração ou desaceleração).**
   Comparar o tamanho/velocidade das últimas velas antes do toque com as
   anteriores. Aceleração forte sugere clímax de euforia (padrão de exaustão
   clássico); desaceleração suave sugere aproximação mais controlada.

---

## 5. Bloco 3 — Qualidade da Rejeição no Toque

Mede o que acontece no momento exato do contato com a zona — é a **confirmação em
tempo real** de que a reação está de fato ocorrendo (ou não). Parcialmente coberto
hoje pela tag `PULLBACK CONFIRMADO HOJE`, mas sem indicar a qualidade dessa reação.

**Fatores a considerar:**

1. **Velocidade/decisão da saída da zona.** Rejeição afiada e rápida é o sinal mais
   forte de atividade real na zona; um afastamento lento e arrastado é um sinal
   fraco, mesmo que tecnicamente conte como "toque confirmado".

2. **Tamanho do corpo da vela vs. range total (pureza da vela de rejeição).** Vela
   de corpo grande indica decisão/fluxo de ordens claro; vela de indecisão (doji,
   corpo pequeno) indica rejeição fraca, mesmo com pavio longo.

3. **Magnitude do movimento de afastamento, normalizado por volatilidade (ATR).**
   Permite comparar rejeições em momentos de volatilidade diferente de forma justa,
   em vez de comparar pontos brutos.

4. **Confirmação no momento do toque.** Distinto da rejeição em si — é o padrão
   específico observado: pavio de rejeição, vela de engolfo, mudança de estrutura
   de curto prazo, ou fechamento forte de volta na direção esperada.

5. **Número de retrações durante a saída.** Mesmo dentro de um movimento de saída,
   quantas vezes o preço hesitou ou voltou parcialmente para dentro da zona antes
   de sair de vez — mais retrações diluem a força do sinal de rejeição.

6. **Alinhamento com a tendência do timeframe maior.** Rejeição a favor da
   tendência maior tem mais peso do que rejeição contra ela.

---

## 6. Camada de saída — três níveis, sempre nessa ordem

A saída para o usuário deve manter três níveis, sem remover nenhum dos dados que já
existem hoje:

### 6.1 Dados brutos (manter tudo que já existe + adicionar os novos scores)
Preço, range da zona, toques, idade, ATR, tags de estado — tudo isso continua.
Adicionar os scores dos três blocos (combustível, aproximação, rejeição) como dados
explícitos, não escondidos dentro de um texto.

### 6.2 Interpretação
Texto que conecta os números entre si — não repete o dado, explica o que ele
significa em conjunto com os outros fatores. Deve ser gerado a partir dos fatores
específicos daquela zona (não um texto fixo por regime de mercado, como acontece
hoje — essa é a lacuna central identificada na seção 1).

### 6.3 Ação
Texto objetivo sobre o que fazer com a informação, mas sempre com linguagem
probabilística (usar "favorece", nunca "compre"/"vai subir"). Regras para essa
camada:

- **Vocabulário fixo de postura**, para nunca soar mais confiante do que os dados
  sustentam e para manter rastreabilidade:

  | Postura | Quando usar |
  |---|---|
  | Favorece entrada | Confluência forte entre os três blocos, na mesma direção |
  | Aguardar confirmação adicional | Sinal presente mas incompleto (ex: aproximação favorável, mas rejeição ainda não confirmada) |
  | Sinal fraco / operar reduzido | Fatores conflitantes entre blocos, ou zona já desgastada |
  | Evitar / não operar | Fatores predominantemente contrários, ou dados insuficientes |

- **Toda vez que a postura for "Favorece entrada", deve vir junto o nível de
  invalidação** (o que cancela a tese) — nunca uma sugestão de entrada sem a
  condição que a anula.

- A ação deve ser derivada por regras explícitas a partir dos scores dos três
  blocos, e não gerada como texto livre — isso é o que permite ao usuário
  rastrear exatamente qual combinação de fatores levou àquela postura, e permite
  recalibrar as regras via backtest no futuro.

- A magnitude esperada (curta/moderada/longa, derivada do Bloco 1) deve aparecer
  junto da ação, não só a probabilidade de bounce.

---

## 7. Requisito explícito de exibição

- **P(bounce) e P(rompimento) devem sempre ser exibidos lado a lado**, como já é
  feito hoje — mesmo sendo estatisticamente complementares, ambos os números devem
  aparecer de forma explícita na interface. Não remover, não simplificar para um
  único valor.
- Nenhum dos campos hoje existentes no painel deve ser removido. Tudo que está
  descrito na seção 1 como "já coberto" continua existindo; o trabalho é
  complementar, não substituir.

---

## 8. Princípios gerais que devem guiar a implementação

- **Nada de black box:** cada camada (combustível, aproximação, rejeição, ação)
  deve ser rastreável até os fatores individuais que a compõem — o usuário precisa
  conseguir entender por que o sistema chegou a determinada leitura, não apenas ver
  o resultado final.
- **Probabilidade de reação (P(bounce)/P(rompimento)) e magnitude esperada do
  movimento são conceitos distintos e não devem ser colapsados em um único score.**
- **Toques e idade, isoladamente, não são suficientes para explicar a força de uma
  zona** — a lacuna identificada na seção 1 prova isso empiricamente com os dados
  do próprio painel atual.
- **A ação sugerida nunca deve soar mais confiante do que os dados sustentam** —
  sempre linguagem probabilística, sempre com condição de invalidação quando houver
  sugestão de entrada.
- Todos os conceitos acima (blocos 1, 2 e 3) devem eventualmente ter seus pesos e
  limiares calibrados via backtest com dados históricos de WIN/WDO em 5 minutos, em
  vez de herdar valores arbitrários — mas essa calibração é uma etapa futura,
  separada da implementação inicial dos cálculos.
