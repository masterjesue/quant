# Estudo das estratégias e plano de trade — agosto/2026

**Base:** 210 operações reais do Diário (fev–jul/2026), classificadas uma a uma
em 2026-07-30 a partir do `resumo_operacional` escrito por você na época, do
`estrategia_raw` e da leitura dos prints. Os 15 trades de Replay/simulador
ficaram de fora de todos os números abaixo.

**Resultado do período:** −R$ 436,00.

> Isto é uma leitura do seu próprio histórico registrado — não é recomendação
> de investimento nem previsão de mercado. Amostra de 4 meses efetivos: os
> recortes com n < 10 são indício, não conclusão.

---

## 1. O achado principal

O ano não foi perdido por falta de estratégia. Foi perdido porque **metade do
que você opera financia a outra metade**.

| Grupo | n | Resultado | Acerto | Expectativa/trade |
|---|---:|---:|---:|---:|
| **4 estratégias vencedoras** | 46 | **+R$ 776** | 50% | **+R$ 16,87** |
| **4 estratégias perdedoras** | 50 | **−R$ 740** | 16% | **−R$ 14,80** |

Quase simetria perfeita. O motor que funciona existe, está medido, e é
consistente — o que sobra em cima dele é que zera o resultado.

E as vencedoras **não** dependem de um mês de sorte:

| Mês | n | Resultado | Média/trade |
|---|---:|---:|---:|
| abril | 5 | +R$ 75 | +15,00 |
| maio | 8 | +R$ 150 | +18,75 |
| junho | 26 | +R$ 376 | +14,46 |
| julho | 7 | +R$ 175 | **+25,00** |

Positivas em **todos** os meses, com a média por trade subindo. Isso é o que
justifica o plano abaixo ser "faça mais disso e pare o resto", em vez de
"mude tudo".

---

## 2. O ranking completo

Só trades reais. Operação com 2 tags conta nas duas.

| Estratégia | n | Resultado | Acerto | Payoff | Expectativa |
|---|---:|---:|---:|---:|---:|
| 🟢 **Bounce em Resistência** | 19 | **+R$ 310** | 26% | 2,82 | +16,32 |
| 🟢 **Trowback de Pivô** | 8 | **+R$ 245** | 38% | 2,58 | +30,62 |
| 🟢 **Rompimento de Suporte** | 17 | **+R$ 221** | 29% | 1,96 | +13,00 |
| 🟢 Triângulo | 4 | +R$ 197 | 25% | — | +49,25 |
| 🟡 Rompimento de LTB | 1 | +R$ 60 | — | — | — |
| 🟡 OCO | 2 | +R$ 60 | 50% | — | +30,00 |
| 🟡 Bandeira | 3 | +R$ 50 | 33% | — | +16,67 |
| 🟡 Fibonacci | 4 | +R$ 30 | 25% | — | +7,50 |
| 🟡 Canal | 3 | +R$ 20 | 33% | — | +6,67 |
| 🔴 Bounce em LTB | 8 | −R$ 41 | 25% | — | −5,12 |
| 🔴 **Bounce MME21** | 23 | **−R$ 86** | 26% | — | −3,74 |
| 🔴 **Espadada** | 3 | **−R$ 95** | 0% | — | −31,67 |
| 🔴 **Alinhamento de Médias** | 5 | **−R$ 145** | 0% | — | −29,00 |
| 🔴 **Rompimento de Resistência** | 19 | **−R$ 211** | 16% | — | −11,11 |
| 🔴 **Bounce em Suporte** | 23 | **−R$ 289** | 13% | — | −12,57 |
| ⬛ **Outros** (sem diário) | 72 | **−R$ 585** | 25% | — | −8,12 |

---

## 3. A assimetria que explica quase tudo

| Direção | n | Resultado | Acerto |
|---|---:|---:|---:|
| **Venda** | 98 | **−R$ 3** | 40% |
| **Compra** | 112 | **−R$ 433** | 29% |

Suas vendas empataram o ano. Suas compras perderam ele inteiro.

E isso **não é uma coincidência de contexto de mercado** — é o mesmo setup
funcionando de um lado e falhando do outro:

| Par simétrico | Lado | n | Resultado |
|---|---|---:|---:|
| Nível segurou | **Bounce em Resistência** (venda) | 19 | **+R$ 310** |
| Nível segurou | Bounce em Suporte (compra) | 23 | −R$ 289 |
| Nível rompeu | **Rompimento de Suporte** (venda) | 17 | **+R$ 221** |
| Nível rompeu | Rompimento de Resistência (compra) | 19 | −R$ 211 |

Mesma lógica de leitura, mesma janela, resultado espelhado. A leitura mais
provável, olhando seus próprios resumos: nas compras você entra em **suporte
que ainda vai ser testado**, e nas vendas você entra em **resistência já
validada por 3+ toques**. Vários resumos de compra dizem literalmente "esse
suporte foi rompido e acionou o meu stop" (#34, #21, #56, #80).

**A única exceção comprada que funciona é o Trowback de Pivô** — 8 trades,
100% compra, +R$ 245, a melhor expectativa de toda a lista (+R$ 30,62/trade) e
a menor taxa de morte no zero a zero (2 em 8). Vale isolar e estudar por que
essa compra específica se comporta diferente das outras.

---

## 4. Os três vazamentos, em ordem de tamanho

### 4.1 Overtrading — o maior de longe

| Trades no dia | Dias | Resultado | Média/dia |
|---|---:|---:|---:|
| 1 a 2 | 24 | **+R$ 328** | **+13,67** |
| 3 a 4 | 31 | −R$ 137 | −4,42 |
| **5 ou mais** | **12** | **−R$ 627** | **−52,25** |

**12 dias destruíram R$ 627** — mais do que a perda do ano inteiro. Os dias de
1–2 operações são os únicos lucrativos, e por margem confortável.

Detalhe importante: o trade logo após um loss no mesmo dia rende −R$ 2,16 em
média, contra −R$ 2,08 da média geral. Ou seja — **não é "revenge trade" na
operação seguinte**. O dano não vem de uma entrada emocional isolada, vem do
**acúmulo**: quanto mais você opera no dia, pior o dia fecha. É um limite de
quantidade, não de temperamento.

### 4.2 As operações sem diário

| Grupo | n | Resultado | Média |
|---|---:|---:|---:|
| **Com resumo escrito** | 142 | **+R$ 161** | +1,13 |
| **Sem resumo** | 68 | **−R$ 597** | −8,78 |

Uma diferença de R$ 9,91 por operação. Não é o ato de escrever que ganha
dinheiro — é que operação que você não consegue justificar por escrito
normalmente é operação que não deveria ter existido. Os 72 trades em "Outros"
(−R$ 585) são quase todos disso: o bloco de fevereiro (−R$ 314) e o de julho
(−R$ 279), sem uma linha de registro.

### 4.3 O breakeven que mata operação boa

**65 de 210 operações (31%) terminam exatamente em R$ 0,00** — 43 delas
marcadas como saída por breakeven, 34 na janela do almoço.

Isso já está documentado por você mesmo, várias vezes:

- #51 — *"o preço desceu 1,8 pontos contra o meu stop e apesar de ficar enroscado chegou no alvo nos candles seguintes. Preciso rever a estratégia de breakeven nos 4 pontos de dólar."*
- #54 — *"O breakeven me tirou da operação e pagou o alvo. É a segunda vez que cometo esse erro operacional."*
- #96 — *"saímos no zero-a-zero no breakeven automático durante a formação do próprio candle de entrada."*

Em **Bounce em Resistência**, o seu melhor setup, 9 de 19 operações morreram
em zero. Se mesmo um terço delas tivesse ido ao alvo, esse setup sozinho
mudaria o ano.

Não é para tirar o breakeven — ele é o que segura o loss médio. É para
**calibrá-lo por janela e por volatilidade**, que é exatamente o que você
escreveu que precisava fazer e nunca virou regra.

---

## 5. Onde o dinheiro realmente está

Cruzando estratégia × ativo × janela:

| Recorte | n | Resultado | Média |
|---|---:|---:|---:|
| **Vencedoras + WDO + almoço** | **22** | **+R$ 607** | **+27,59** |
| Vencedoras + WIN + almoço | 8 | +R$ 70 | +8,75 |
| Todo o resto | 180 | −R$ 1.113 | −6,18 |

**22 operações produziram +R$ 607. As outras 188 perderam R$ 1.043.**

Por janela (só o núcleo com diário):

| Janela | n | Resultado | Média |
|---|---:|---:|---:|
| **almoço** | 97 | +R$ 52 | +0,54 |
| prime | 17 | **−R$ 190** | **−11,18** |
| tarde | 6 | **−R$ 69** | **−11,50** |

Prime e tarde são sangria pura, e você já sabia — #70: *"apenas 1 pois esse
não é o meu melhor horário para operar"*; #123: *"Eu não costumo operar esse
horário"*. Mesmo o seu melhor setup vira negativo fora do almoço: Bounce em
Resistência faz +R$ 370 no almoço e −R$ 30 no prime e −R$ 30 na tarde.

Por ativo, dentro das vencedoras: **WDO carrega quase tudo**. Bounce em
Resistência: WDO +R$ 330 × WIN −R$ 20. Trowback de Pivô: WDO +R$ 245, WIN R$ 0.

---

## 6. O contexto emocional (n=22, indício)

Só 22 operações têm a autoavaliação preenchida — amostra pequena, mas o sinal
é coerente:

| Resultado | Confiança | Humor | Energia | Atenção | Presença |
|---|---:|---:|---:|---:|---:|
| Gain | **4,17** | 4,00 | 3,83 | **4,00** | 3,83 |
| Loss | **3,00** | 3,38 | 3,38 | **3,25** | 3,25 |

Confiança e atenção são os dois que mais separam. Vale preencher em **todas**
as operações do mês que vem — com n maior isso vira um filtro pré-trade de
verdade, e hoje ainda não é.

---

## 7. Checklist operacional vencedor

Destilado do que efetivamente deu certo. As 5 primeiras linhas são filtro de
entrada — se qualquer uma falhar, a operação não acontece.

### Antes de abrir o dia
1. **Conta correta?** Conferir se está em conta real e não no simulador —
   aconteceu 2× (#44, #52), uma delas com a operação vencedora perdida.
2. **É dia de rolagem de contrato ou super-quarta?** Se sim, opera menor ou
   fica de fora (#73, #129, #130 e #105 confirmam o custo).
3. Marcar no gráfico os níveis do dia anterior **antes** do pregão.

### Filtro de entrada (todas precisam ser "sim")
4. **Janela:** é o **almoço**? Fora dela, só com motivo escrito antes de
   entrar.
5. **Ativo:** **WDO** por padrão. WIN só nos setups em que ele se provou.
6. **Setup:** está em Bounce em Resistência, Rompimento de Suporte ou
   Trowback de Pivô?
7. **Direção:** se for **compra**, o setup é Trowback de Pivô? Se não for,
   exigir confirmação extra ou passar.
8. **Nível:** a resistência/suporte tem **3+ toques** ou é de dia anterior?
   Sem os toques, é hipótese, não nível.

### Execução
9. Stop **técnico** (atrás da estrutura), nunca financeiro — #7 registra
   exatamente esse erro.
10. Breakeven **calibrado à volatilidade da janela**, não fixo em 4 pontos.
11. Alvo padrão. Esticar só com leitura barra a barra em andamento, como em
    #115 e #118 — as duas vezes que isso funcionou foram decisões
    conscientes no meio da operação, não ganância na entrada (#16).

### Limites do dia (não negociáveis)
12. **Máximo 2 operações por dia.** É a linha mais importante deste
    documento.
13. Depois de 2 stops, o dia acabou.
14. Depois de um gain que bate a meta, considerar encerrar — #83 e #85
    mostram você fazendo isso bem.

### Depois de cada operação
15. **Escrever o resumo operacional na hora.** Sem resumo, sem próxima
    operação.
16. Preencher a autoavaliação emocional (confiança e atenção no mínimo).

---

## 8. Plano para agosto

### Manter
- **Bounce em Resistência no WDO, janela do almoço** — o núcleo.
- **Rompimento de Suporte** (a antiga "inversão de polaridade" vendida).
- **Trowback de Pivô** — sua única compra lucrativa, melhor expectativa da
  lista.
- O hábito de escrever o resumo: vale ~R$ 10/operação em diferença medida.
- Stop técnico posicionado atrás de estrutura, e a disciplina de aguentar o
  violino (#43, #91 são exemplos seus de execução correta).

### Refinar
- **Breakeven por janela/volatilidade** — o vazamento mais caro que tem
  conserto puramente operacional. Sugestão de teste: no almoço, adiar o
  breakeven no WDO de 4 para 5–6 pontos e medir por 20 operações antes de
  decidir.
- **Trowback de Pivô** — entender por que essa compra funciona e as outras
  não. Pode ser a chave para destravar o lado comprado.
- **Autoavaliação emocional em 100% dos trades** — hoje só 10% têm.
- **Triângulo** (+R$ 197 em 4) e **OCO** (+R$ 60 em 2) — amostra pequena
  demais para concluir, mas positiva. Vale continuar registrando com atenção.

### Parar
- **Compras em suporte** (Bounce em Suporte: −R$ 289) e **compras no reteste
  de resistência rompida** (Rompimento de Resistência: −R$ 211) — juntas,
  −R$ 500. Não abandonar para sempre: suspender até revisar por que o lado
  comprado falha.
- **Alinhamento de Médias** (−R$ 145, 0% de acerto em 5) e **Espadada**
  (−R$ 95, 0% em 3). Sua própria nota em #73 já dizia: *"eu deveria priorizar
  os setups vencedores que eu já validei"*.
- **Bounce MME21 como setup principal** (−R$ 86 em 23 operações). A MME21 se
  provou boa como **confluência** — vários trades vencedores a citam como
  proteção de stop. Mantê-la nesse papel, não como gatilho.
- **Operar prime e tarde** (−R$ 259 somados em 23 operações).
- **Operar sem registrar.**

### Meta operacional do mês
Não é uma meta de dinheiro — é de comportamento, porque foi o comportamento
que custou o ano:

- **Máximo 2 operações por dia**, sem exceção.
- **≥ 80% das operações** dentro do combo vencedor (setup vencedor + WDO +
  almoço).
- **100% das operações com resumo e autoavaliação** preenchidos.
- **Zero operações** nos setups da lista "parar".

Como referência de tamanho: o combo vencedor rodou 22 operações em 4 meses —
cerca de **5 a 6 por mês**. Aplicando a expectativa medida (+R$ 27,59), um mês
disciplinado desse jeito fica na faixa de **+R$ 150**. Isso não é promessa: é
o que a sua própria amostra entregou, e ela é pequena. O ponto do mês não é o
número — é provar que dá para operar 6 vezes em vez de 60.

---

## 9. Como acompanhar isso no Super Quant

- **Diário → Estatísticas → Confiabilidade por Estratégia**: bloco novo
  (2026-07-30) que junta o Score com o saldo por estratégia. A seta ▶ de cada
  linha abre o detalhe com direção, janela, ativo, zero a zero, drawdown e
  melhor/pior trade — é onde este estudo se atualiza sozinho a cada operação
  nova.
- **Diário → Histórico → botão ✎**: reclassificar qualquer operação. A
  classificação deste estudo foi feita a partir dos seus textos e prints; onde
  você discordar, corrija ali e os números se refazem.
- Uma operação está marcada como ambígua e vale sua revisão: **#76**, cujo
  resumo diz "suporte forte" mas a operação é uma venda.

---

*Gerado em 2026-07-30 a partir de `diario_trades.parquet` (210 operações
reais). Reprodutível por `migracao_classificacao_estrategias.py` (mapa
auditável trade a trade, com o motivo de cada classificação).*
