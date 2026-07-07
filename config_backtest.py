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
GEMINI_MODEL      = "gemini-2.0-flash"   # 2.0-flash: sem reasoning tokens, resposta direta
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
Nunca apresente probabilidade como certeza — use a analogia meteorológica."""

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

# --- Preço de referência da zona (spec 2.1) ---
PESO_REJEICAO_NA_REFERENCIA = 0.5   # rejeição perfeita (score=1.0) amplifica o peso do toque em até 50%

# --- Decaimento de força por toque — "pico e queda", não saturação (spec 2.3) ---
TOQUES_PICO_FORCA = 3               # toque em que a força atinge o máximo
PESO_TOQUE_INICIAL = 1.0            # peso do 1º toque
INCREMENTO_TOQUE_ATE_PICO = 0.5     # incremento de peso por toque até o pico
PESO_TOQUE_PENALIDADE_EXCESSO = 0.35  # fração de peso perdida por toque além do pico
PESO_TOQUE_MINIMO = 0.15            # piso do peso — nunca zera (nível ainda pode reagir)
REFORCO_VOLUME_MAX = 0.6            # atenuação máxima da penalidade quando o toque teve volume excepcional

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
# JANELAS DO PREGÃO — confirmado por Jesué em 30/06/2026
# -----------------------------------------------------------------------------

JANELAS_PREGAO = {
    "abertura": ("09:00", "09:30"),
    "prime":    ("09:30", "11:00"),
    "almoco":   ("11:00", "13:00"),
    "tarde":    ("13:00", "15:00"),
    "final":    ("15:00", "17:00"),
}
