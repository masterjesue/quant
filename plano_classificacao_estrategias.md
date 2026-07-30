# Classificação de estratégias do Diário — decisões e plano de execução

**Status: CONCLUÍDO em 2026-07-30.** Executado por
`migracao_classificacao_estrategias.py` (mapa auditável trade a trade). O
resultado do estudo que isto viabilizou está em
[`PLANO_DE_TRADE_AGOSTO_2026.md`](PLANO_DE_TRADE_AGOSTO_2026.md).

O que foi feito, em relação ao que estava planejado abaixo:

- Criadas as 4 categorias: `oco`, `alinhamento_de_medias`, `espadada`,
  `outros`; reativada `topo_fundo_duplo` (usada de verdade em #143).
- `inversao_polaridade` (25) e `reteste` (2) distribuídos nas estratégias
  reais. O eixo adotado — e que a decisão do Jesué implicava — é
  **(nível) × (interação)**: `bounce_em_X` quando o nível segurou,
  `rompimento_de_X` quando rompeu e a entrada foi no reteste/throwback. Isso
  explicou por que `rompimento_de_suporte`/`_de_resistencia` estavam
  cadastradas com checklist e **zero** uso: os trades delas estavam todos
  rotulados como `inversao_polaridade`.
- **Não** foi criado campo separado de "característica do movimento" — a
  informação original continua auditável em `estrategia_raw`, que a migração
  não toca. Se o Jesué quiser o campo próprio depois, dá pra derivar dali.
- 206 trades reclassificados, 19 preservados, 0 slugs legados em uso, 0 trades
  sem estratégia.
- Bug de causa raiz corrigido junto: `calcular_score_confiabilidade` varria a
  lista de bootstrap congelada no código em vez do cadastro editável — era
  isso que fazia "Score de Confiabilidade" e "Por Estratégia" mostrarem
  vocabulários diferentes. Ver `dt._tags_para_pontuar`.

---

## Diagnóstico original (2026-07-28)

Escrito para sobreviver à compactação da conversa. Contém as decisões do Jesué
(não reinterpretar) e o diagnóstico que as motivou.

---

## Diagnóstico — dois vocabulários que nunca foram reconciliados

Levantado sobre **210 trades reais**:

| Origem | Situação |
|---|---|
| **Slugs em uso** (criados pela importação do XLSM) | `suporte_resistencia` 51, `inversao_polaridade` 25, `pullback_mme21` 19, `outro` 11, `rompimento` 7, `lta_ltb` 6, `medias_moveis` 4, `reteste` 2, `ombro_cabeca_ombro` 1 — **126 trades**. **Nenhum existe no cadastro.** |
| **Estratégias cadastradas** (16, com checklist) | apenas **22 trades** usam de fato |
| **Sem estratégia nenhuma** | **67 trades** |

É por isso que os três painéis discordam: "Score de Confiabilidade" lista os
slugs antigos; "Estratégias Cadastradas" e "Por Estratégia" listam o cadastro.

Cadastradas **sem nenhum uso**: `rompimento_de_lta`, `rompimento_de_ltb`,
`rompimento_de_resistencia`, `rompimento_de_suporte`, `bounce_em_lta`,
`triangulo`.

**Sinal disponível para classificar:** `resumo_operacional` preenchido em
**142** trades (texto descritivo e rico) e **124 prints** legíveis (mostram
níveis traçados FORTE/1 TOQUE/TOP GAP, MME21, entrada).

---

## ⚠️ Por que a classificação automática pura NÃO é confiável

Um classificador por palavra-chave sobre o `resumo_operacional` acerta os
casos claros mas erra feio nos ambíguos. Erros reais medidos:

- **#27** *"pullback na média de **9**"* → caiu em `bounce_mme21`
- **#28** *"pullback na média de **20**"* → idem
- **#29** *"setup da **espadada**, cruzamento de médias"* → idem
- **#24** *"pivot de baixa que rompeu um **fundo** forte"* → classificado como
  `rompimento_de_resistencia`, sendo de **suporte**
- **#10** *"…busquei oportunidades…"* → virou `canal` por a palavra aparecer solta

A regra de médias sozinha jogava **28 trades** num balde só. Por isso a
decisão abaixo: aplicar só onde o texto é **inequívoco**, marcar como
*sugerido*, e o Jesué confirma na tela.

---

## Decisões do Jesué (2026-07-28) — seguir ao pé da letra

### 1. Inversão de Polaridade e Reteste NÃO são estratégias

> *"Inversão de Polaridade e Reteste são características de uma estratégia,
> então eles devem ser classificados corretamente: identifique se foi uma
> operação de Suporte/Resistência, se foi bounce/rompimento, se foi operação
> de pivô e etc."*

Ou seja: são **qualificadores** do movimento, não o setup em si. Os 25 trades
de `inversao_polaridade` e os 2 de `reteste` precisam ser lidos e distribuídos
nas estratégias reais (bounce/rompimento de suporte/resistência, pivô…).

Consequência de design: talvez valha um campo separado para "característica
do movimento", em vez de forçar dentro de `estrategia_tags`. **A confirmar com
o Jesué antes de implementar.**

### 2. Criar 3 categorias novas

| Categoria | Definição dada pelo Jesué |
|---|---|
| **OCO (Ombro-Cabeça-Ombro)** | figura gráfica — categoria própria |
| **Médias** | **alinhamento** de médias — explicitamente ≠ usar a MME21 isolada |
| **Espadada** | cruzamento da **MMA21 com a MME9**. Mesma família de Médias, mas categoria específica própria |

Atenção: `bounce_mme21` (cadastrada, MME21 isolada) é **coisa distinta** de
`Médias` (alinhamento) e de `Espadada` (cruzamento). Não fundir os três.

### 3. Classificação: proposta automática + revisão

Aplicar só onde o texto for inequívoco; o resto vai para **Outros**. Cada
sugestão deve ficar identificável para o Jesué confirmar/corrigir pelo botão
Editar do Histórico.

### 4. Ordem de execução pedida

> *"Comece com /compact. Depois item 2, 1 e 3."*

1. **`/compact`** — o Jesué roda no CLI (não é acionável por mim)
2. **Item 2** — botão **Editar** no Histórico, para reclassificar trades
3. **Item 1** — organizar/validar/classificar + criar a categoria **Outros**
4. **Item 3** — juntar **Score de Confiabilidade** + **Por Estratégia** num
   bloco só, com seta na linha para expandir estatísticas
5. **Item 4** (não numerado por ele, depende do item 1) — estudo das
   estratégias vencedoras e **plano de trade do mês seguinte**: checklist
   operacional vencedor, o que deu certo, o que não deu, onde focar

---

## Estado do sistema quando isto foi escrito

- 210 trades reais; ano alinhado ao ProfitPro (206 no período do relatório,
  R$ −351,00, batendo dia a dia)
- 0 pendências no Histórico
- Suítes em `testes/` passando; acoplamento de callback entre abas em 0
- Último commit: `3d5fd45` (local, **push ainda pendente** — foi bloqueado
  pela camada de permissão)
