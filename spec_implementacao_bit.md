# Implementação do ativo BIT (futuro de Bitcoin, B3) no Super Quant

**Status:** planejado, não iniciado. Escrito em 2026-07-28 para execução em
outra sessão.

---

## Contexto — por que isto existe

Ao auditar o ano de 2026 contra o relatório de performance do ProfitPro,
apareceram **2 operações de BITG26** (19/02/2026). Elas foram registradas no
Diário, mas BIT **não existe no resto do sistema**: sem candle, sem tick, sem
features, sem Quant Lab, sem Backtesting.

O registro histórico já funciona. O que falta é o ativo virar cidadão de
primeira classe do motor.

### O que JÁ foi feito (2026-07-28)

- `cfg.ATIVOS_DIARIO = ATIVOS + ["BIT"]` — separa **"ativo que o motor
  processa"** de **"ativo que o Diário registra"**. Pôr `"BIT"` direto em
  `cfg.ATIVOS` quebraria o pipeline, que sairia procurando candles inexistentes.
- `cfg.VALOR_PONTO["BIT"] = 0.01`. Sem esta entrada o P&L de BIT sairia
  **zerado em silêncio** (`VALOR_PONTO.get(ativo, 0.0)`).
- Dropdowns de ativo do Diário (2 skins) usam `ATIVOS_DIARIO`.

### Realidade do MT5 — verificado ao vivo, não presumido

```
BITG26   digits=2   trade_tick_size=20.0   trade_tick_value=0.2
         -> R$ 0,20 por tick de 20 pontos = R$ 0,01 por ponto   ✔ bate com o relatório
         -> candles M5 disponíveis (500 candles, volume médio ~651/candle)
```

---

## B1. ⚠️ PRIMEIRO PASSO OBRIGATÓRIO — o padrão binário `if ativo == 'WIN' else <WDO>`

**8 ocorrências**, 4 em cada dashboard:

```
dashboard.py:633     m        = m_win        if ativo == 'WIN' else m_wdo
dashboard.py:2985    ticker   = contrato_win if ativo == 'WIN' else contrato_wdo
dashboard.py:3447    df_ativo = df_win       if ativo == 'WIN' else df_wdo
dashboard.py:3451    m        = m_win        if ativo == 'WIN' else m_wdo
dashboard_98.py:574, 2201, 2639, 2643   — os mesmos quatro
```

Com BIT selecionado **todos caem no `else` e devolvem dado de WDO**. O
dashboard mostraria métricas de WDO rotuladas como BIT, sem erro nenhum, sem
log — e o Jesué tomaria decisão de trade em cima disso.

**Não iniciar nenhum outro passo antes deste.** Ligar a ingestão primeiro
significa ter dado certo no disco sendo exibido errado na tela.

## B2. Refatorar o estado de "pares" para "por ativo"

**48 pares de variáveis `_win`/`_wdo`** (24 em cada dashboard): o estado é
modelado como duas variáveis paralelas em vez de um dicionário por ativo. O
mesmo em `estado`, com `contrato_win`/`contrato_wdo` como chaves planas.

**O padrão certo já existe no código:** `estado['bases']` é
`{'WIN': df, 'WDO': df}`. Usar como referência e converter o resto.

Isto **elimina a classe inteira do B1** em vez de corrigir 8 casos — é a razão
de B1 e B2 andarem juntos.

## B3. Seletor de ativo na UI

Hoje são 2 botões fixos (`btn-win`/`btn-wdo`, `dashboard.py:2167-2172`) mais
`_estilo_botao_ativo`. Gerar a partir de `cfg.ATIVOS`.

> ⚠️ **Cuidado documentado no CHANGELOG (2026-07-04):** esses botões foram
> retirados da função reconstruída a cada 1 s porque recriá-los zerava
> `n_clicks` no browser e a seleção **voltava sozinha pro WIN**. Os botões
> novos precisam continuar **estáticos** no layout — só o `style` muda por
> callback. É a mesma armadilha de "nunca recriar o componente que dispara a
> própria callback" que mordeu o Diário em 2026-07-27.

Verificar também se 3 botões cabem no header sem quebrar o layout.

## B4. Ingestão de dados

- `cfg.PADRAO_ARQUIVO_WIN`/`_WDO` são duas constantes soltas, e
  `csv_loader._identificar_ativo` (linhas 39-46) é um `if/elif` sobre elas.
  Virar `PADRAO_ARQUIVO = {ativo: padrão}` + laço, para que um ativo novo não
  exija tocar em código.
- `cfg.FATOR_ESCALA_CSV` **já é dict por ativo** — só acrescentar BIT
  (provavelmente 1, confirmar contra o CSV real; lembrar que WIN vem em
  milhares e precisa de fator).
- `cfg.CONTRATOS_PILARES_MT5` — montar a lista de BIT.
- **`mt5_loader.py` não tem NENHUM `'WIN'`/`'WDO'` hardcoded.** É genérico e
  deve funcionar com BIT sem alteração — *validar*, não reescrever.

### ⚠️ Armadilha real: 31 símbolos `BIT*` e só ~13 são futuros

```
Futuros : BITF26 BITG26 BITH26 BITJ26 BITK26 BITM26 BITN26
          BITQ25 BITQ26 BITU25 BITV25 BITX25 BITZ25
NÃO são : BITB39, BITO39            (BDR)
          BITC11, BITH11, BITI11    (ETF)
          + variantes SL91/SL92/SL93 e sufixo T
```

`BITC11`, `BITH11` e `BITI11` **casam com o mesmo padrão** de um contrato
futuro: 3 letras + letra de mês válida (C=março, H=março, I=?) + 2 dígitos
("11" lido como ano 2011). `detectar_contrato_mais_recente('BIT')` pode
escolher um ETF.

Na prática o ordenamento por vencimento *provavelmente* salva (2011 < 2026),
mas depender disso é frágil — e as variantes `SL91`/`T` poluem de qualquer
jeito. Resolver com filtro explícito: consultar o tipo de contrato no MT5
(`SYMBOL_CALC_MODE`, `path`) ou manter allowlist dos meses válidos.

**Escrever teste com os 31 símbolos reais listados acima** — é barato e trava
a regressão.

## B5. Features — limiares em pontos absolutos

Tick de BIT = **20 pontos**, contra WIN = 5 e WDO = 0,5. Uma constante em
pontos absolutos tem escala **40× diferente** entre BIT e WDO.

Varrer `config_backtest.py` atrás de constantes em pontos (largura de zona
S/R, thresholds de ATR, clustering) e decidir uma a uma:

1. vira dict por ativo, **ou**
2. vira múltiplo de ATR/tick — **preferível**, some a dependência de escala e
   resolve para qualquer ativo futuro.

As referências de `features.py` e `merge_fontes.py` a `'WIN'` são só exemplo
em `__main__` — inofensivas.

## B6. Liquidez — validar antes de confiar

Volume médio ~651/candle M5 no BITG26, bem abaixo de WIN/WDO. Antes de ligar
Quant Lab/Backtesting para BIT, **medir** se as métricas de fluxo (delta,
razão de agressão) têm massa estatística.

Se não tiverem, a tela precisa **dizer isso** — indicador cinza com aviso, não
um número bonito que finge precisão. Vale o princípio já usado no projeto:
nunca apresentar probabilidade como certeza.

## B7. Ordem de execução

1. **B1 + B2** — refatorar binário → dict. Sem isto, todo passo seguinte
   mostra dado errado em silêncio.
2. **B4** — ingestão + resolver a armadilha dos símbolos, com teste.
3. Rodar `run_backtest.py --setup` só para BIT e conferir a base.
4. **B5** — limiares; validar features contra gráfico real (o Jesué valida
   contra exemplos reais, não contra teoria).
5. **B3** — seletor na UI.
6. **B6** — liquidez; decidir o que exibir e o que ocultar.
7. Só então `"BIT"` entra em `cfg.ATIVOS` e as duas listas voltam a coincidir.

---

## Verificação

- Selecionar BIT no dashboard e conferir que **nenhum** número é igual ao de
  WDO (o sintoma exato do B1 seria os dois baterem).
- `python testes/auditoria_ids_callbacks.py` → 0 acoplamentos.
- Suítes de `testes/` passando (o motor MT5→Diário não pode regredir).
- Teste novo dos 31 símbolos `BIT*`: só os 13 futuros são aceitos.
- Base de BIT gerada com candles M5 coerentes com o gráfico do ProfitChart no
  mesmo período.
- Diário: registrar uma operação de BIT ponta a ponta e conferir o P&L contra
  o relatório da corretora (R$ 0,01/ponto).
