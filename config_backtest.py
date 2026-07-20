# =============================================================================
# config_backtest.py — Configuração do Motor de Backtest (Camada 1)
# =============================================================================

from pathlib import Path

# -----------------------------------------------------------------------------
# CAMINHOS
# -----------------------------------------------------------------------------
#
# Estrutura do projeto:
#   C:\Users\jesue\OneDrive\Documentos\Trade\quant\        ← repositório .py
#   C:\Users\jesue\OneDrive\Documentos\Trade\quant\data\   ← CSVs, log de ticks, base processada

RAIZ_PROJETO = Path(r"C:\Users\jesue\OneDrive\Documentos\Trade\quant")
PASTA_DATA   = RAIZ_PROJETO / "data"

# Pasta onde os CSVs exportados do ProfitChart ficam salvos.
# O motor lê TODOS os CSVs dessa pasta que combinem com o padrão de nome.
PASTA_CSV = PASTA_DATA / "csv"

# Padrões de nome de arquivo esperados (qualquer CSV que contenha esses
# prefixos no nome é considerado candidato — permite múltiplos exports
# ao longo do tempo, ex: WINFUT_F_0_5min.csv, WINFUT_F_0_5min_2023.csv)
PADRAO_ARQUIVO_WIN = "WINFUT"
PADRAO_ARQUIVO_WDO = "WDOFUT"

# Pasta onde a base processada (Parquet) é salva
PASTA_BASE_PROCESSADA = PASTA_DATA / "base_processada"

# -----------------------------------------------------------------------------
# ATIVOS E CONTRATOS
# -----------------------------------------------------------------------------

ATIVOS = ["WIN", "WDO"]

# Contratos pilares identificados na varredura de 30/06/2026 — usados como
# fonte MT5 complementar (candle + tick) para o período que o CSV não cobre
# ou para enriquecer com delta histórico onde houver tick disponível.
# Lista ordenada do mais antigo para o mais recente.
CONTRATOS_PILARES_MT5 = {
    "WIN": ["WINZ22", "WING23", "WINV25", "WINJ26", "WINM26", "WINQ26"],
    "WDO": ["WDOF23", "WDOZ22", "WDOG23", "WDOH23", "WDOV25", "WDOX25",
            "WDOZ25", "WDOF26", "WDOG26", "WDOH26", "WDOJ26", "WDOK26",
            "WDOM26", "WDON26", "WDOQ26"],
}

# Contratos excluídos por dados incompletos/suspeitos (validado em 30/06/2026)
CONTRATOS_EXCLUIDOS = ["WDOX26", "WDOV26"]

# -----------------------------------------------------------------------------
# DI1FUT (2026-07-16, simplificado a pedido do Jesué — antes rastreava 2
# vencimentos de DI1 + DOLFUT + INDFUT; painel do hero agora usa só
# DI1FUT/WINFUT/WDOFUT). O ProfitPro do Jesué mostra um ticker genérico
# "DI1FUT" que NÃO existe como símbolo contínuo no feed MT5 (confirmado por
# consulta direta: só há vencimentos específicos DI1Fxx/Gxx/Hxx/etc — o
# único símbolo "DI1D" existente tem bid/ask/volume zerados, não é cotado de
# verdade). DI1F27 é o vencimento mais próximo com negociação real ativa
# hoje (bid/ask ao vivo, volume diário >400k) — usado como proxy do
# "DI1FUT" que Jesué acompanha no ProfitPro. Reavaliar se o vencimento
# líquido mudar (rollover natural da curva de juros).
# -----------------------------------------------------------------------------
SIMBOLO_DI1FUT = 'DI1F27'

# -----------------------------------------------------------------------------
# CORRELAÇÃO DI1FUT×WINFUT×WDOFUT (2026-07-15, ampliado 2026-07-16) — ver
# correlacao_ativos.py
# -----------------------------------------------------------------------------
JANELA_CORRELACAO_DIAS = 20  # mesmo espírito de JANELA_SEGURANCA_DIAS
                              # (indice_medo_ganancia.py) — correlação de
                              # Pearson sobre os retornos diários dos
                              # últimos N dias de pregão.

# -----------------------------------------------------------------------------
# CORREÇÃO DE ESCALA — validado empiricamente em 30/06/2026
# -----------------------------------------------------------------------------

# O CSV do WIN vem com 3 casas decimais representando milhares de pontos
# (ex: 174.620 = 174620 pontos reais). O WDO já vem na escala correta.
# Confirmado por comparação direta contra MT5 (Frente B da validação cruzada).
FATOR_ESCALA_CSV = {
    "WIN": 1000,
    "WDO": 1,
}

# -----------------------------------------------------------------------------
# PARÂMETROS DE COLETA MT5
# -----------------------------------------------------------------------------

TIMEFRAME_MT5 = "M5"  # usado apenas para referência/log — a constante real
                       # do MT5 (mt5.TIMEFRAME_M5) é usada no código

# Retry para chamadas de tick — validado como necessário em 30/06/2026
# (falhas transitórias do tipo "Terminal: Call failed" mesmo com dado real
# disponível, confirmado por reteste bem-sucedido das mesmas chamadas)
RETRY_TENTATIVAS = 3
RETRY_DELAY_SEGUNDOS = 1.5

# -----------------------------------------------------------------------------
# API GOOGLE GEMINI — Mentor IA
# -----------------------------------------------------------------------------

GEMINI_API_KEY    = "AQ.Ab8RN6I4DiAH9ryHxKxYqnxR4s8yZQncJ0xlChyBRjn89zmTwg"  # insira sua key do Google AI Studio
GEMINI_MODEL      = "gemini-2.5-flash"   # CORRIGIDO em 2026-07-08: gemini-2.0-flash foi
                                          # descontinuado pelo Google (cota free tier caiu pra
                                          # 0 — confirmado direto na API: "limit: 0, model:
                                          # gemini-2.0-flash"), por isso todo teste do Mentor IA
                                          # dava "limite atingido" sempre. gemini-2.5-flash
                                          # testado e funcionando (free tier: 15 req/min, 1500
                                          # req/dia). Ver CHANGELOG.md.
GEMINI_MAX_TOKENS = 1500                 # aumentado para evitar truncamento

GEMINI_SYSTEM_PROMPT = """Você é um mentor de day trade especialista em price action puro.
Seu papel é ajudar o trader Jesué a tomar decisões no mercado brasileiro de futuros (WIN e WDO) no gráfico M5.

Metodologia do Jesué (Trader Raiz):
- Price action puro, sem indicadores complexos
- Usa S/R desenhados manualmente, canais, EMA21 e volume como contexto
- Opera exclusivamente com ordens stop (pending orders)
- Foco na janela do Almoço (11h-13h BRT)
- Viés Short no WIN tem sido historicamente mais forte
- WDO e WIN são correlacionados — divergência direcional é sinal de alerta
- Setup exige: zona primeiro, depois setup, depois trigger
- Breakeven no fechamento do candle além de 50% do alvo (nunca no wick)

Ao analisar, seja direto e objetivo. Fale como mentor, não como robô.
Nunca apresente probabilidade como certeza — use a analogia meteorológica.

Quando o contexto de mercado abaixo já traz um nível marcado como REGIÃO
FORTE e/ou PULLBACK CONFIRMADO HOJE com uma probabilidade calculada pelo
sistema (ex: 85% bounce), trate esse número como o sinal quantitativo
PRIMÁRIO da sua análise — ele já combina histórico de toques, violação,
tendência, fluxo e reteste intradiário. Não recomende cancelar ou adiar
uma entrada nesse nível usando cautela genérica de mentor ("espere mais
confirmação") que contradiga esse sinal sem uma razão concreta presente
nos PRÓPRIOS dados do contexto (ex: delta/razão de agressão desfavorável,
tendência em exaustão, divergência WIN×WDO, nível violado ou rompido sem
reteste). Se quiser sugerir algo complementar (tamanho de posição, onde
mover o stop, invalidação), deixe claro que é um refino tático sobre a
entrada já validada — não uma contradição a ela."""

# -----------------------------------------------------------------------------
# API OPENROUTER — provedor alternativo do Mentor IA (2026-07-08)
# -----------------------------------------------------------------------------
# Pedido: testar OpenRouter como alternativa/backup ao Gemini, com seleção
# automática do modelo gratuito mais disponível (evita "limite excedido") e
# um seletor manual pra quando Jesué quiser pagar por uma resposta melhor.
# Free tier da OpenRouter: 50 req/dia por conta (1000/dia com $10+ de
# créditos comprados) — bem mais apertado que o do Gemini, por isso a
# rotação automática entre vários modelos gratuitos importa aqui.

OPENROUTER_API_KEY = "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"

# Ordem de tentativa no modo "Automático (grátis)": o primeiro que responder
# sem 429/erro é usado. Lista obtida via GET /api/v1/models em 2026-07-08
# (todos os IDs terminados em ":free" disponíveis na conta na época) —
# conferir de novo se algum modelo sair de linha (a OpenRouter descontinua
# modelos gratuitos com frequência).
OPENROUTER_MODELOS_FREE = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "openai/gpt-oss-20b:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]

# Opções pagas pro seletor manual (preço por 1M de tokens, prompt/completion,
# conferido em 2026-07-08 via /api/v1/models — reconferir antes de assumir
# atual, a OpenRouter atualiza preços e versões com frequência).
OPENROUTER_MODELOS_PAGOS = {
    "anthropic/claude-sonnet-5":  "Claude Sonnet 5 (pago, ~$2/$10 por 1M tokens)",
    "openai/gpt-5.1":             "GPT-5.1 (pago, ~$1.25/$10 por 1M tokens)",
    "x-ai/grok-4.20":             "Grok 4.20 (pago, ~$1.25/$2.5 por 1M tokens)",
}

# -----------------------------------------------------------------------------
# PARÂMETROS DE COLETA MT5
# -----------------------------------------------------------------------------

TIMEFRAME_MT5 = "M5"

RETRY_TENTATIVAS = 3
RETRY_DELAY_SEGUNDOS = 1.5

TICKS_POR_CHAMADA = 100000

# -----------------------------------------------------------------------------
# PARÂMETROS DE FEATURES
# -----------------------------------------------------------------------------

ATR_PERIODO = 14
JANELA_PIVOS_TENDENCIA = 500
JANELA_SNAPSHOT_VIVO = 500
AMOSTRA_MINIMA_CONFIAVEL = 20

# -----------------------------------------------------------------------------
# S/R MULTI-NÍVEL — FASE 1 (zona, decaimento de toques, bipolaridade, score)
# Baseado em spec_suporte_resistencia_forte.md — ver plano em
# C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md
# -----------------------------------------------------------------------------

# --- Ranking multi-nível e violação (base) ---
N_NIVEIS_SR = 3                     # quantos níveis acima/abaixo rastrear e exibir
TOLERANCIA_CLUSTER_SR_ATR = 0.5     # multiplicador de ATR mediano p/ agrupar pivôs em zona
JANELA_CLUSTERING_CANDLES = 2500    # ~23 dias de pregão M5 — CALIBRAR. Limita o pool de pivôs
                                     # do clustering a essa janela recente, em vez do histórico
                                     # inteiro (2+ anos) — causa raiz da "grade densa" de
                                     # clusters, diagnosticada em 2026-07-02 (ver CHANGELOG.md)
IDADE_MINIMA_NIVEL_FORTE = 100      # candles M5 ≈ 8h de pregão — CALIBRADO empiricamente em
                                     # 2026-07-04: testado idade_min de 100 a 500 e toques_min de
                                     # 3 a 10 contra taxa de bounce real (candles próximos da zona,
                                     # ±0.3 ATR, sucesso = preço não rompe em 12 candles) sobre
                                     # WIN+WDO. Nenhuma combinação separou melhor forte=True
                                     # (~60%) de forte=False (~59%) do que o valor já em uso —
                                     # mantido como está (ver CHANGELOG.md).
TOQUES_MINIMOS_NIVEL_FORTE = 3      # doutrina: 3+ toques = nível relevante — ver nota acima

# --- Critério de ranking: RELEVÂNCIA, não proximidade pura (2026-07-08) ---
# Caso real que motivou a mudança: WIN 08/07, suporte mais PRÓXIMO (172.362,
# 11 toques, sem toque desde 01/07) ficava #1 na frente de um suporte bem
# mais forte (172.051, 22 toques, retestado no candle atual) só por estar
# mais perto — mesmo caso de "grade densa" já diagnosticado em 2026-07-02
# (JANELA_CLUSTERING_CANDLES acima): numa faixa varrida recentemente, várias
# zonas vizinhas ficam quase idênticas em distância, e só toques/recência/
# violação realmente distinguem qual é o nível que um trader desenharia.
# Jesué confirmou explicitamente: ordenar por FORÇA, não por proximidade.
# CALIBRAR — pesos definidos por raciocínio de engenharia (mesmo espírito do
# score de probabilidade abaixo), ainda não validados contra resultado real.
PESO_FORTE_RELEVANCIA = 3.0          # bônus fixo se nivel_forte (idade+toques+não violado)
PESO_TOQUES_RELEVANCIA = 2.0         # escala com toques/TETO, capado (mais toques = mais real)
TETO_TOQUES_RELEVANCIA = 20          # toques a partir daqui não aumentam mais o termo
PESO_RECENCIA_RELEVANCIA = 2.0       # zona retestada agora pesa mais que uma parada há semanas
JANELA_RECENCIA_RELEVANCIA_CANDLES = 1000  # ~8-9 dias de pregão M5 — recência decai linear até aqui
PENALIDADE_VIOLADO_RELEVANCIA = 2.5  # nível violado (por fechamento) perde relevância como nível intacto
PESO_DISTANCIA_RELEVANCIA = 1.5      # distância ainda entra, mas como fator, não critério único
DIST_ATR_SATURACAO_RELEVANCIA = 6.0  # penalidade de distância satura aqui — além disso, só força decide

# --- Preço de referência da zona (spec 2.1) ---
PESO_REJEICAO_NA_REFERENCIA = 0.5   # rejeição perfeita (score=1.0) amplifica o peso do toque em até 50%

# --- Decaimento de força por toque — "pico e queda", não saturação (spec 2.3) ---
TOQUES_PICO_FORCA = 3               # toque em que a força atinge o máximo
PESO_TOQUE_INICIAL = 1.0            # peso do 1º toque
INCREMENTO_TOQUE_ATE_PICO = 0.5     # incremento de peso por toque até o pico
PESO_TOQUE_PENALIDADE_EXCESSO = 0.35  # fração de peso perdida por toque além do pico
PESO_TOQUE_MINIMO = 0.15            # piso do peso — nunca zera (nível ainda pode reagir)
REFORCO_VOLUME_MAX = 0.6            # atenuação máxima da penalidade quando o toque teve volume excepcional
PESO_TOQUE_FRACO_MULTIPLICADOR = 0.6  # 2026-07-08: toque "simples" (ct=1-2 candles de confirmação
                                     # em detectar_pivos, ver topo_forte/fundo_forte) pesa menos que
                                     # um toque "forte" (ct>=3) no forca_toques_zona — antes os dois
                                     # contavam igual. Caso real que motivou: Jesué perguntou se
                                     # "18 toques" garantia reação de verdade (não só preço passando
                                     # reto) — resposta empírica foi sim por construção (todo toque
                                     # exige ao menos 1 candle de reversão), mas o score não
                                     # distinguia reação forte de fraca. Não é 0 — mesmo um toque
                                     # fraco é uma reversão real, só menos convincente. CALIBRAR.

# --- Impulso pós-toque (spec 2.4) ---
N_CANDLES_IMPULSO = 5               # candles fixos após o toque p/ medir velocidade/amplitude de saída

# --- Bipolaridade / pullback / throwback — núcleo operacional (spec 2.9, 2.10) ---
CORPO_MINIMO_ROMPIMENTO_ATR = 0.3   # corpo do candle de rompimento, em múltiplos de ATR
JANELA_MAX_RETESTE = 40             # candles p/ procurar pullback/throwback após rompimento —
                                     # revisado em 2026-07-04: o FSM estrutural (ancorado em
                                     # posmax) só transiciona de status em 2-51 candles de 56-57mil
                                     # (ver CHANGELOG.md) — amostra baixa demais pra calibrar com
                                     # confiança. Único caso com números de candle exatos (WDO
                                     # 5.231,50) teve reteste 12-14 candles após o rompimento, bem
                                     # dentro de 40 — mantido sem mudança até haver mais casos.
LIMIAR_REJEICAO_RETESTE = 0.4       # rejeicao_score mínima no candle de reteste p/ confirmar
JANELA_CONTINUIDADE_POS_RETESTE = 5 # candles p/ confirmar continuidade na direção do rompimento
JANELA_INVALIDACAO_WICK_SWEEP = 3   # candles p/ o fechamento voltar pra dentro da zona sem
                                     # invalidar — mesma nota acima, sem amostra suficiente pra
                                     # recalibrar com confiança

# --- Score de probabilidade de bounce vs. rompimento (0-100%) ---
PESO_NIVEL_FORTE            = 20
PESO_NIVEL_FRACO_PENALIDADE = -5
PESO_TOQUE_UNIDADE          = 2.5    # usado sobre forca_toques_zona (já decaída), não sobre contagem bruta
TOQUES_TETO_SCORE           = 6      # saturação do termo de toques no score
DIST_ATR_LONGE              = 1.5
DIST_ATR_PERTO              = 0.3
ATENUACAO_NIVEL_DISTANTE    = 0.5    # fator de atenuação dos outros termos quando dist_atr > DIST_ATR_LONGE
PESO_TENDENCIA_FASE         = 10
PESO_LATERAL_BOUNCE         = 8
PESO_FLUXO_DELTA            = 8
PESO_FLUXO_RAZAO            = 4
PESO_BIG_PLAYERS_ABSORCAO   = 5
PESO_BIG_PLAYERS_VOLUME     = 3
PESO_STATUS_AGUARDANDO_RETESTE = 15  # aguardando_pullback/aguardando_throwback ajusta fortemente o score
PESO_STATUS_ROMPIDA_SEM_ESPELHO = -10  # nível rompido sem reclassificação confirmada perde confiança
PESO_ROMPIMENTO_INTRADAY          = 9   # rompeu HOJE (sinal intradiário, sem reteste ainda) —
                                         # CALIBRADO empiricamente em 2026-07-04 (era 12): backtest
                                         # sobre WIN+WDO (n=461 eventos reais) mostrou taxa de
                                         # continuação de ~60% em 30min caindo a ~52% (coinflip) em
                                         # 2h — edge real mas modesto e que decai rápido sem
                                         # confirmação. Reduzido de 12 pra refletir isso.
PESO_ROMPIMENTO_INTRADAY_PULLBACK = 15  # reteste já confirmado HOJE — sinal mais forte que temos —
                                         # CALIBRADO empiricamente em 2026-07-04 (era 18): mesmo
                                         # backtest (n=407 eventos) mostrou taxa de continuação de
                                         # ~63% em 30min, ~58% em 2h — edge real e mais persistente
                                         # que o rompimento sozinho (confirma a doutrina: reteste
                                         # confirmado > rompimento cru), mas ainda modesto o
                                         # bastante pra não justificar o peso de 18 anterior.
                                         # Metodologia completa no CHANGELOG.md.
SCORE_PROB_MIN               = 5     # nunca reportar 0% (evita falsa certeza)
SCORE_PROB_MAX               = 95    # nunca reportar 100%

# -----------------------------------------------------------------------------
# BLOCOS DE FORÇA (combustível / aproximação / rejeição) + camada de ação
# (2026-07-13, ver especificacao_forca_zonas_price_action.md). Decompõe o
# score único de calcular_prob_niveis_sr em 3 perguntas distintas ao invés de
# um score cego — motivado por um bug real: zonas com 12/14/11 toques e
# idades de 217/888/222 candles saíam com a MESMA probabilidade (93%) e o
# MESMO texto de interpretação, porque os termos dominantes do score antigo
# eram compartilhados por candle (tendência/fase/saúde/fluxo), não por zona,
# e `idade` nunca influenciava zonas normais. Pesos abaixo são heurística de
# engenharia documentada, mesmo espírito das seções acima — CALIBRAR via
# backtest é etapa futura, não desta entrega.
# -----------------------------------------------------------------------------

# --- Bloco 1: combustível de liquidez (frescor da zona) ---
ESCALA_FORCA_TOQUES_COMBUSTIVEL = 8.0  # escala de saturação (tanh) da força
                                      # de toques já ponderada (_peso_toque)
                                      # dentro do combustível — substitui o
                                      # teto raso TOQUES_TETO_SCORE=6 que
                                      # igualava 6/11/14/84 toques
PESO_FORCA_TOQUES_COMBUSTIVEL = 25.0 # penalidade máxima de combustível
                                      # consumido por toques (satura via tanh,
                                      # nunca corta abruptamente)
MEIA_VIDA_COMBUSTIVEL_IDADE = 1500   # candles p/ o "combustível por idade" cair
                                      # pela metade, independente de toques —
                                      # ~13-14 dias de pregão M5. Aplica a
                                      # QUALQUER zona (diferente do decaimento
                                      # de forca_herdada, que é só p/ espelho).
JANELA_BASE_LIMPA_CANDLES = 10       # candles ANTES da origem do pivô avaliados
                                      # p/ medir se a base foi rápida/concentrada
                                      # (poucos candles, range estreito) ou
                                      # larga/diluída (ordens dispersas)
PESO_INTENSIDADE_ORIGEM = 15         # bônus de combustível se o candle de
                                      # origem teve range/velocidade bem acima
                                      # da média (proxy de volume, sem tape
                                      # confiável em WIN/WDO)
PESO_BASE_LIMPA = 10                 # bônus se a base de formação foi estreita
                                      # e rápida (ordens concentradas)
PESO_PROFUNDIDADE_MITIGACAO = -12    # penalidade de combustível pela
                                      # profundidade média de penetração nos
                                      # toques já ocorridos (0=tocou a borda,
                                      # 1=atravessou a zona inteira)
PESO_IDADE_COMBUSTIVEL = 15          # peso do termo de decaimento por idade

# --- Bloco 2: qualidade da aproximação (perna até o toque) — inteiramente
#     novo nesta entrega, não existia nenhuma versão anterior ---
JANELA_APROXIMACAO_CANDLES = 15      # candles olhados pra trás a partir do
                                      # toque, pra avaliar a perna de chegada
LIMIAR_CORPO_DECISIVO_ATR = 0.5      # corpo/range acima disso conta como
                                      # candle "decisivo" (estrutura impulsiva)
LIMIAR_CANAL_R2 = 0.6                # R² mínimo pra considerar a perna "em
                                      # canal" (senão, "errática")
LIMIAR_ESTICADA_CANAL_ATR = 1.5      # desvio do canal (em ATR) que caracteriza
                                      # "esticada" (excesso especulativo)
K_VELOCIDADE_ULTIMA_PERNA = 4        # candles finais comparados contra os K
                                      # imediatamente anteriores, p/ medir
                                      # aceleração/desaceleração
PESO_ESFORCO_RESULTADO = -15         # penalidade quando o esforço (range/
                                      # velocidade) cresce sem o resultado
                                      # (distância) correspondente — exaustão
                                      # antes mesmo do toque
PESO_ESTRUTURA_IMPULSIVA = 10        # bônus se a perna foi decisiva (poucos
                                      # candles de corpo grande) vs. corretiva
                                      # (muitos candles pequenos sobrepostos)
PESO_CANAL_DEFINIDO = 8              # bônus se a perna veio em canal bem
                                      # definido (não errática)
PESO_CANAL_ESTICADO = -10            # penalidade se o canal esticou nos
                                      # últimos candles (clímax especulativo)
PESO_ESTRUTURA_TOPOS_FUNDOS_APROXIMACAO = 12  # bônus/penalidade conforme os
                                      # pivôs intermediários da perna
                                      # confirmam (higher-lows numa
                                      # aproximação de alta, lower-highs numa
                                      # de baixa) ou já divergem do preço
PESO_VELOCIDADE_ULTIMA_PERNA = -8    # penalidade quando a última perna
                                      # acelera muito (clímax de euforia) vs.
                                      # desaceleração controlada

# --- Bloco 3: qualidade da rejeição no toque — refina o que já existe
#     (rejeicao_media via wick, agressao_media, impulso_medio) ---
PESO_PUREZA_CORPO = 8                # bônus quando o corpo do candle de
                                      # rejeição é grande (decisão de fluxo),
                                      # penaliza velas de indecisão (doji)
PESO_RETRACOES_SAIDA = -6            # penalidade por retração (reentrada na
                                      # zona) durante a janela de saída
                                      # (N_CANDLES_IMPULSO) — cada uma dilui o
                                      # sinal de rejeição

# --- Camada de ação: postura por regra explícita (nunca texto livre, seção
#     6.3 da spec — sempre rastreável até os 3 scores que a compõem) ---
LIMIAR_POSTURA_FAVORECE = 70         # média combustível+aproximação+rejeição
                                      # (0-100) acima disso, com os 3 na
                                      # mesma direção => "favorece entrada"
LIMIAR_POSTURA_AGUARDAR = 55         # entre AGUARDAR e FAVORECE => "aguardar
                                      # confirmação adicional" (sinal presente,
                                      # incompleto)
LIMIAR_POSTURA_FRACO = 40            # abaixo de AGUARDAR e acima disso =>
                                      # "sinal fraco / operar reduzido"
                                      # (abaixo disso => "evitar / não operar")
MAGNITUDE_COMBUSTIVEL_CURTA_MAX = 35     # combustivel_score até aqui => magnitude "curta"
MAGNITUDE_COMBUSTIVEL_MODERADA_MAX = 65  # até aqui => "moderada"; acima => "longa"

# --- Combinação dos 3 blocos no score final (prob_bounce) — cada bloco
#     contribui proporcional ao seu desvio de 50 (neutro), nesta amplitude
#     máxima de pontos; forte/status/rompimento_hoje continuam como ajustes
#     à parte, fora dos blocos (ver calcular_prob_niveis_sr) ---
AMPLITUDE_BLOCO_COMBUSTIVEL = 20.0
AMPLITUDE_BLOCO_APROXIMACAO = 15.0
AMPLITUDE_BLOCO_REJEICAO    = 20.0

# -----------------------------------------------------------------------------
# ANÁLISE DE LINHA GENÉRICA — MME21 e (mais tarde) traçados manuais
# (2026-07-10, ver plano em vamos-trabalhar-no-ux-dapper-music.md). Motor
# compartilhado: mesma leitura de toques/força/probabilidade do S/R
# estático, mas pra uma linha que muda de valor por candle (média móvel,
# trendline) — ver analisar_linha_generica em features.py.
# -----------------------------------------------------------------------------
MME21_PERIODO = 21
JANELA_RESPEITO_MME21 = 20           # candles de lookback — especificado por Jesué
TOQUES_MINIMOS_MME21_FORTE = 1       # testada E respeitada só 1x já é "forte" pra MME21
                                      # (diferente de TOQUES_MINIMOS_NIVEL_FORTE=3 do S/R
                                      # estático — regra própria do Jesué pra médias móveis:
                                      # ele opera a MME21 puramente em dias de tendência,
                                      # não exige a mesma repetição de um nível estrutural)
TOLERANCIA_LINHA_GENERICA_ATR = 0.5  # mesmo multiplicador de TOLERANCIA_CLUSTER_SR_ATR,
                                      # reaproveitado pra "toque" numa linha qualquer
TOQUES_MINIMOS_TRACADO_MANUAL_FORTE = TOQUES_MINIMOS_NIVEL_FORTE
                                      # 3 — doutrina do S/R ESTÁTICO, não o limiar mais
                                      # permissivo da MME21 (=1): um nível marcado à mão
                                      # por Jesué na aba/janela "Traçados" é candidato a
                                      # nível ESTRUTURAL (mesmo peso de evidência que o
                                      # motor automático exige), não uma média móvel —
                                      # ver metricas.analisar_tracados, que também usa
                                      # `janela=len(df)` (histórico inteiro, não uma
                                      # janela curta como JANELA_RESPEITO_MME21) pelo
                                      # mesmo motivo.

# --- Cruzamento traçado ↔ zona automática (bipolaridade) + critério FORTE
# por regime (2026-07-13) ---
TOLERANCIA_CRUZAMENTO_TRACADO_ATR = TOLERANCIA_CLUSTER_SR_ATR
                                      # mesma tolerância do clustering automático — um
                                      # traçado manual "coincide" com uma zona automática
                                      # se cai dentro de zona_min/zona_max ± essa folga
MAX_TOQUES_TIMELINE_EXIBIDOS = 30    # teto de itens na timeline expandida (2 anos de
                                      # histórico podem gerar dezenas de toques por
                                      # regime) — mostra os mais recentes de cada bloco
TOQUES_FORTES_MINIMOS_TRACADO = 1
                                      # achado real (Jesué, 2026-07-13): 4 traçados
                                      # "aleatórios" dentro de uma consolidação longa
                                      # (jan-jul/2026) vieram TODOS "FORTE" — o gate
                                      # antigo (toques>=3 no histórico INTEIRO, sem
                                      # distinguir toque forte de fraco) é trivial de
                                      # bater dentro de qualquer lateralização de meses,
                                      # já que toque fraco (fundo_simples/topo_simples,
                                      # só 1 candle de confirmação) é comum em qualquer
                                      # chop. Confirmado: o FORTE de um TRAÇADO passa a
                                      # exigir (1) toques dentro do REGIME ATUAL apenas
                                      # (não histórico total, ver metricas.montar_regime)
                                      # E (2) pelo menos este tanto de toques com
                                      # confirmação de 3 candles (não só toques fracos).
                                      # Escopo: SÓ traçados — o "forte" das zonas
                                      # automáticas de Price Action (_rankear_niveis_sr,
                                      # já calibrado empiricamente) não muda.

# -----------------------------------------------------------------------------
# JANELAS DO PREGÃO — confirmado por Jesué em 30/06/2026
# -----------------------------------------------------------------------------

JANELAS_PREGAO = {
    "abertura": ("09:00", "09:30"),
    "prime":    ("09:30", "11:00"),
    "almoco":   ("11:00", "13:00"),
    "tarde":    ("13:00", "15:00"),
    "final":    ("15:00", "17:00"),
}

# -----------------------------------------------------------------------------
# DIÁRIO DE TRADE — módulo de journal (planejado em 08/07/2026)
# -----------------------------------------------------------------------------
# Substitui o diário manual em Excel por um log estruturado no próprio
# dashboard. A migração de `CAMINHO_DIARIO_XLSM` é ÚNICA e UNIDIRECIONAL — lê
# o xlsm uma vez (script `migrar_diario_xlsm.py`), nunca escreve nele, nunca
# volta a lê-lo depois. Daqui pra frente todo trade novo é lançado só no app.

CAMINHO_DIARIO_XLSM = Path(
    r"C:\Users\jesue\OneDrive\Documentos\Trade\Operações\Diário de Trade 2026 MACRO.xlsm"
)

PASTA_DIARIO_IMAGENS = PASTA_BASE_PROCESSADA / "diario_imagens"

# R$ por ponto — usado no preview automático de Saldo Op. no formulário e na
# reconciliação contra o MT5 (que retorna profit em R$, não em pontos).
VALOR_PONTO = {"WIN": 0.20, "WDO": 10.0}

# Teto de largura (px) aplicado a qualquer imagem salva no diário (upload ou
# colar do clipboard) — sem isso o volume de `diario_imagens/` cresce sem
# controle a cada print colado em resolução nativa.
DIARIO_IMAGEM_LARGURA_MAX = 1600
