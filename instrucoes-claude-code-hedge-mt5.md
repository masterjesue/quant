# Instruções para Claude Code — Pareamento de posições Hedge (MT5) no Diário de Trade

## Contexto

O sistema quant não executa ordens — apenas **consulta o MT5 via Python** para registrar operações no diário. A conta MT5 (BTG Pactual, conta 13372735) opera em modo **Hedge**, o que significa que cada ordem gera uma posição/ticket independente, mesmo quando ela é, na prática, o fechamento de uma operação feita no Profit (plataforma de execução real, usada em paralelo).

Isso já causou inconsistências visíveis no dashboard atual: no print mais recente, o "Status das Posições" mostra **3 registros distintos de WINQ26** com entrada e saída idênticas (176485 → 176485, Pts +0), o que sugere possível **duplicação de registro** — não necessariamente 3 operações reais diferentes.

## Objetivo geral

Garantir que o diário de trade registre **cada operação real uma única vez**, de forma correta e auditável, independentemente de a conta MT5 estar em modo Hedge — sem depender de o usuário mudar a conta para Netting.

## Requisitos técnicos

### 1. Fonte de verdade para operações fechadas

- Usar **exclusivamente `history_deals_get()`** da lib `MetaTrader5` para reconstruir operações fechadas — não usar `positions_get()` para esse fim.
- Cada deal tem os campos `entry` (`DEAL_ENTRY_IN` = abertura, `DEAL_ENTRY_OUT` = fechamento) e `position_id`.
- Agrupar deals pelo campo **`position_id`** (não pelo `ticket` individual — cada perna do hedge tem ticket próprio, mas o `position_id` amarra abertura e fechamento da mesma posição).
- Uma operação só deve ser considerada "fechada" quando existir, para o mesmo `position_id`, pelo menos um deal `IN` e um deal `OUT` cujo volume líquido resulte em zero.

### 2. Deduplicação / idempotência

- Definir uma **chave única de registro** por operação fechada — sugestão: combinação de `position_id` + `ticket` do deal `OUT`.
- O job de sincronização deve gravar por **upsert** (inserir se não existir, ignorar/atualizar se já existir), nunca `insert` simples — para que rodar o job várias vezes (ou em paralelo) não duplique registros no diário.
- Investigar especificamente por que as 3 operações WINQ26 (176485 → 176485) aparecem separadas: confirmar se são `position_id` genuinamente diferentes (coincidência de preço) ou se é o mesmo `position_id` sendo lido/gravado mais de uma vez pelo pipeline atual.

### 3. Pareamento de hedge (mesclagem em "trade líquido")

- Ao mesclar pernas opostas de hedge em "1 trade líquido", registrar no log/nota da operação os **tickets de origem** que compuseram aquele trade (ex: ticket IN e ticket OUT), para permitir auditoria manual quando o resultado parecer suspeito (como no caso dos 3 registros idênticos).
- Não mesclar automaticamente deals de `position_id` diferentes só porque preço e volume coincidem — o pareamento deve ser sempre por `position_id`, nunca por similaridade de preço/volume.

### 4. Separar "registro de operações" de "auditoria de exposição"

- Criar uma rotina **separada** (não misturar com o registro de operações fechadas) que roda periodicamente (sugestão: 1x ao dia, no fechamento) e:
  1. Lê `positions_get()`.
  2. Agrupa por símbolo, somando volume líquido (compra positivo, venda negativo).
  3. Se a soma líquida por símbolo for **zero** → apenas informativo, sem ação.
  4. Se a soma for **diferente de zero** → gerar um **alerta explícito** (essa é a única situação de exposição real, que merece atenção).
- Esse alerta deve ser destacado de forma diferente no dashboard (ex: cor/seção separada de "posições hedge residuais zeradas").

### 5. Escopo — o que NÃO fazer

- Não alterar o modo da conta (Hedge/Netting) via código — isso é configuração do servidor da corretora, fora do escopo do sistema.
- **Fora da exceção da seção 6 abaixo, o sistema continua sendo apenas leitura/registro** — nenhuma abertura de posição nova, nenhuma alteração de SL/TP, nenhum fechamento que não seja um par hedge zerado.

### 6. Limpeza automática periódica do hedge (MUDANÇA DE ESCOPO)

**Atenção**: até aqui o sistema era 100% somente-leitura. Esta seção introduz a **primeira capacidade de execução** (fechar posições) e precisa de travas de segurança rígidas, porque um erro de lógica aqui fecha posição de verdade, não só registra dados.

#### 6.1 Pré-requisitos técnicos

- O terminal MT5 precisa estar com **AutoTrading habilitado** para aceitar ordens vindas da API Python.
- Confirmar com a corretora que a conta permite envio de ordens via API (algumas corretoras bloqueiam trading algorítmico até liberação explícita, separada do login normal).

#### 6.2 Regra de elegibilidade para fechamento automático — critério único e rígido

Uma posição só pode ser fechada automaticamente se **todas** as condições abaixo forem verdadeiras:

1. Existe outra posição aberta no **mesmo símbolo**, com **direção oposta** (uma comprada, uma vendida).
2. O **volume das duas é idêntico**.
3. A soma líquida de volume para aquele símbolo, considerando **todas** as posições abertas naquele símbolo, é **exatamente zero** — não pode sobrar nenhuma exposição residual depois de parear tudo.
4. Nenhuma das posições do par pertence a um `magic number` de estratégia que ainda esteja ativa/em gestão (evitar fechar algo que outro processo ainda está controlando).

Se qualquer condição falhar, a rotina **não fecha nada** naquele símbolo — apenas registra no log para revisão manual. Nunca fechar de forma parcial ou aproximada.

#### 6.3 Mecanismo de fechamento

- Usar `order_send()` com `TRADE_ACTION_DEAL` referenciando o **ticket específico** de cada posição do par — nunca fechar "por símbolo".
- Preferir `TRADE_ACTION_CLOSE_BY` (cruzamento nativo), quando suportado pela corretora, em vez de duas ordens de mercado separadas — reduz risco de slippage entre as duas execuções.
- Sempre fechar **os dois lados do par juntos**, na mesma execução — nunca só um lado.

#### 6.4 Frequência e horário

- A rotina deve rodar **preferencialmente fora da janela operacional do usuário** — ou seja, fora do intervalo em que ele costuma abrir/gerenciar operações no Profit (tipicamente próximo à abertura e ao fechamento do pregão, quando a volatilidade e a atividade são maiores).
- Sugestão de horário: **após o fechamento do pregão** (ex: pós 18h, quando o mercado futuro B3 já encerrou o horário regular) ou, alternativamente, **antes da abertura** do próximo pregão — nunca durante o período em que o usuário está ativo operando.
- Tornar o horário **configurável** (ex: variável de ambiente ou parâmetro `janela_execucao_inicio` / `janela_execucao_fim`), para o usuário poder ajustar conforme sua rotina real sem precisar mexer no código.
- Antes de cada execução, a rotina deve **checar se o horário atual está dentro da janela permitida**; se não estiver, não executa (mesmo em modo agendado/cron) e apenas loga que pulou por estar fora da janela.
- Não rodar em intervalo curto (segundos/minutos); nunca em tempo real durante o pregão.
- Se por algum motivo for necessário rodar manualmente durante a janela operacional (ex: sob demanda do usuário), a rotina deve continuar seguindo o `dry_run` por padrão nesse caso, exigindo confirmação explícita para execução real.

#### 6.5 Log obrigatório

Toda execução da rotina deve registrar, mesmo quando não fecha nada:
- Símbolos analisados.
- Pares identificados como elegíveis (com tickets).
- Pares NÃO fechados e o motivo (ex: "volume não bate", "sobra exposição residual de X contratos").
- Confirmação de cada fechamento executado (ticket, preço, horário).

#### 6.6 Modo de segurança (dry-run)

Implementar um modo `dry_run=True` que roda toda a lógica de identificação de pares elegíveis e loga o que **faria**, sem enviar nenhuma ordem real — para validar o comportamento antes de habilitar execução de verdade em conta real.

## Critérios de aceite

- [ ] Nenhuma operação aparece duplicada no diário após rodar o job múltiplas vezes seguidas.
- [ ] Os 3 registros WINQ26 (176485 → 176485) foram investigados e a causa raiz identificada (duplicação de pipeline vs. operações genuinamente distintas).
- [ ] Existe alerta funcional e testável para exposição residual real (soma líquida ≠ 0 por símbolo).
- [ ] Cada trade líquido mesclado no dashboard mostra, em log ou nota, os tickets de origem usados no pareamento.
- [ ] A rotina de limpeza só fecha pares 100% elegíveis (critério da seção 6.2) e nunca fecha exposição residual real.
- [ ] Janela de execução configurável implementada e testada: rotina não roda (nem em modo real, nem agendada) fora do horário permitido, e loga quando pula por estar fora da janela.
- [ ] Modo `dry_run` testado e validado pelo usuário antes de qualquer execução real habilitada.
