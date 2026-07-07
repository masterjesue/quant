# Suporte e Resistência Forte — Spec para Sistema Quantitativo (Price Action Puro)

**Contexto operacional:** B3, mini-índice (WIN) e mini-dólar (WDO), sistema estruturado no timeframe de **5 minutos (M5)**. Todas as regras abaixo devem assumir M5 como timeframe base de operação, salvo indicação contrária.

## 1. Definição conceitual

Suporte/resistência forte **não é um preço exato**, é uma **zona** onde há evidência histórica de desequilíbrio agressivo entre oferta e demanda, validada por múltiplos critérios independentes — não por um único fator isolado (ex: "toques" sozinho é fraco e enganoso).

> Um nível é forte quando a reversão que ele produziu foi **rápida, ampla, defendida em timeframe macro, e ainda não teve suas ordens pendentes esgotadas por excesso de testes**.

Isso precisa virar um **score composto (0–100)**, não um booleano "é/não é suporte". Um sistema quantitativo ganha muito mais tratando força como variável contínua do que como classificação binária.

---

## 2. Pilares quantificáveis (o que o código precisa calcular)

### 2.1 Detecção da zona — e o preço de referência (linha)
A zona continua sendo a unidade estatística correta (é o que captura a imprecisão real do mercado), mas para operar e desenhar no gráfico é preciso um **preço de referência único** dentro da zona — a "linha" que o trader efetivamente traça.

- Identificar pivôs (swing highs/lows) via `fractal` ou `argrelextrema` (janela N configurável, ex: 5 candles de cada lado).
- Agrupar pivôs próximos em uma **zona** com largura baseada em volatilidade: `largura_zona = k × ATR(14)` (k tipicamente 0.5–1.0).
- Zona = `[preço_pivô - largura/2, preço_pivô + largura/2]`.
- **Preço de referência (linha ideal):** não é necessariamente o pivô original nem o meio aritmético da zona. Calcular como o preço dentro da zona com **maior concentração de rejeição** (ex: maior número de fechamentos/mechas próximos, ou o nível de maior volume dentro da zona, via tape). Isso evita que a linha fique "deslocada" quando o primeiro toque foi um outlier.

```
preco_referencia = argmax(concentracao_de_toques_e_volume, dentro_da_zona)
```

**Requisito de dashboard:** exibir simultaneamente (a) a linha do preço de referência, exatamente como o trader desenharia manualmente, e (b) o range da zona (ex: faixa translúcida em torno da linha), combinando a precisão de uma linha com a honestidade estatística de uma zona.

### 2.2 Força da rejeição (wick ratio)
Para cada toque na zona, medir:
```
rejeicao_score = (sombra_na_direcao_da_zona) / (range_total_do_candle)
```
Quanto mais próximo de 1, mais forte a rejeição. Pavio pequeno + fechamento perto do extremo = rejeição fraca (nível pode estar "cansando").

### 2.3 Contagem de toques com decaimento (não é "quanto mais, melhor")
Cada toque adicional **consome liquidez**. Modelar com peso decrescente:
```
peso_toque_n = base_weight × decay_factor^(n-1)
```
Sugestão: 1º e 2º toque somam força; a partir do 3º-4º, cada toque **reduz** a probabilidade de o nível segurar de novo (a menos que haja acúmulo de volume forte reforçando).

### 2.4 Magnitude e velocidade do movimento de saída
```
impulso_score = (variação_preço_pós_toque) / (ATR × n_candles_até_o_movimento)
```
Movimento rápido e amplo = zona validada com convicção. Movimento lento e raso = zona fraca, provável rompimento no próximo teste.

### 2.5 Confluência — indicadores e Fibonacci
Camada de alto valor prático. Cada critério contribui como um bônus independente no score:

- **MME21**: indicador primário de operação do trader. Confluência entre a zona de S/R e a MME21 deve ter o **maior peso** entre as médias móveis.
- **MME9** e **MMA200**: incluídas para fins comparativos/analíticos no dashboard (contexto de curto prazo vs. tendência macro), não como referência operacional principal.
- **Fibonacci**: retrações e extensões relevantes (38.2%, 50%, 61.8%, 78.6%) sobre a última perna de impulso relevante. Confluência S/R + Fibo tem peso alto.
- **Números redondos**: relevante especialmente em WIN (pontos cheios/meios) e WDO (centavos redondos).

```
confluencia_score = w_mme21×flag_mme21 + w_mme9×flag_mme9 + w_mm200×flag_mm200
                   + w_fibo×flag_fibo + w_redondo×flag_numero_redondo
```

### 2.6 Volume e agressão (tape reading via MetaTrader5)
- Dados de tape (times & trades) via MT5 permitem medir **agressão real** (compradora vs. vendedora), mais forte que volume de candle agregado.
- Volume/agressão alto no momento da formação da zona = mais participantes com posição defendendo aquele preço.
- Sugestão de métrica: `delta_agressao = volume_comprador - volume_vendedor` nos candles que tocam a zona; picos de delta na direção da rejeição reforçam o score.

### 2.7 Confluência multi-timeframe (MTF) — ⚠️ Atenção arquitetural para o Claude Code
Conceitualmente, uma zona é mais estrutural quando aparece de forma independente em mais de um timeframe (ex: M5 + M15, ou M5 + H1).

**Porém:** o sistema atual está estruturado inteiramente em **M5**. Trazer confluência multi-timeframe pode exigir repensar a arquitetura de dados (múltiplos streams de candles, sincronização de timestamps, re-cálculo de zonas em paralelo) — não é necessariamente incremental. **Avaliar escopo e custo/benefício antes de implementar**, não tratar como "mais um critério do score" sem essa análise prévia.

### 2.8 Idade e tempo de formação
- Zonas formadas ao longo de mais tempo (múltiplas sessões/semanas) pesam mais que zonas formadas em poucos candles.
- Zonas muito antigas sem nenhum reteste recente devem ter peso decrescente por relevância temporal (`time_decay`).

### 2.9 Bipolaridade, Pullback e Throwback — ⭐ Núcleo operacional
Este é um dos critérios centrais da operação do trader e deve ter tratamento explícito e destacado no sistema, não apenas embutido no score genérico de rompimento.

**Bipolaridade (memória de mercado):** uma resistência rompida com convicção tende a virar suporte (e vice-versa). O sistema deve reclassificar `tipo` da zona somente após rompimento válido confirmado (ver regra abaixo) — a zona "espelhada" herda o histórico de força da zona original, mas começa a acumular seus próprios toques como nova referência.

**Pullback:** após rompimento de **suporte** (movimento de baixa), o preço retorna a testar o suporte rompido — agora atuando como **resistência** — antes de continuar a queda. É a confirmação clássica de continuação bearish.

**Throwback:** após rompimento de **resistência** (movimento de alta), o preço retorna a testar a resistência rompida — agora atuando como **suporte** — antes de continuar a alta. É a confirmação clássica de continuação bullish.

```
rompimento_valido = fechamento_alem_da_zona
                     AND corpo_candle > k × ATR
                     AND (opcional) delta_agressao_na_direção_do_rompimento > limiar

pullback_confirmado   = rompimento_valido(suporte) 
                         AND retest_por_cima_da_zona_rompida 
                         AND rejeicao_score_no_retest > limiar
                         AND continuidade_baixa_apos_retest

throwback_confirmado  = rompimento_valido(resistencia) 
                         AND retest_por_baixo_da_zona_rompida 
                         AND rejeicao_score_no_retest > limiar
                         AND continuidade_alta_apos_retest
```

Reclassificar a zona (de resistência para suporte, ou de suporte para resistência) **apenas** após rompimento válido + pullback/throwback confirmado — nunca no momento do rompimento isolado. Isso evita reclassificar em falsos rompimentos ("violinas").

**Sugestão de flag no dashboard:** sinalizar explicitamente `aguardando_pullback` / `aguardando_throwback` quando um rompimento válido ocorrer mas o reteste ainda não tiver sido confirmado — é o setup mais relevante para o fluxo operacional do trader.

### 2.10 Resistência a stop hunts (ativos voláteis: WIN, WDO, B3)
- Diferenciar **wick sweep** (mecha ultrapassa a zona mas fechamento retorna) de **rompimento real** (fechamento e candles subsequentes consolidam do outro lado).
- Regra prática: ignorar rompimento se o fechamento voltar para dentro da zona em até N candles.

---

## 3. Score composto sugerido

```
score_forca = w1×rejeicao_score
            + w2×confluencia_score        # MME21/9/200 + Fibo + número redondo
            + w3×impulso_score
            + w4×agressao_relativa        # tape via MT5
            + w5×idade_normalizada
            − w6×penalidade_toques_excessivos
            (+ w7×confluencia_mtf         # opcional, sujeito à avaliação arquitetural — ver 2.7)
```
Pesos (`w1..w7`) devem ser parâmetros configuráveis/otimizáveis (grid search ou walk-forward), não fixos no código — isso vai variar por ativo e timeframe.

---

## 4. Estrutura de dados sugerida (para o Claude Code implementar)

```python
class ZonaSR:
    preco_min: float
    preco_max: float
    preco_referencia: float          # "linha" ideal para plotagem, dentro da zona
    tipo: Literal["suporte", "resistencia"]
    timeframe_origem: str             # M5 no momento; MTF fica como extensão futura
    toques: list[Toque]               # cada um com timestamp, rejeicao_score, delta_agressao
    confluencias: ConfluenciaFlags    # mme21, mme9, mma200, fibo, numero_redondo
    score_forca: float
    status: Literal["ativa", "rompida", "aguardando_pullback", "aguardando_throwback", "invalidada"]
    zona_espelho: Optional["ZonaSR"]  # se rompida e confirmada, zona oposta gerada
```

## 5. O que evitar (armadilhas comuns em implementação ingênua)
- Não tratar toque como binário (tocou/não tocou) — sempre medir intensidade da rejeição.
- Não usar apenas linha exata — sempre acompanhar a zona com largura proporcional à volatilidade do ativo (mas manter o preço de referência para plotagem e operação).
- Não contar "quanto mais toques, mais forte" sem decaimento — é o oposto na prática.
- Não reclassificar suporte↔resistência apenas no rompimento — exigir confirmação de pullback/throwback.
- Não implementar confluência MTF como "só mais um flag" — avaliar impacto arquitetural primeiro (sistema é M5).
