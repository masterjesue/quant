# =============================================================================
# ai_analyst.py — Mentor IA via Google Gemini (integrado ao dashboard)
# =============================================================================

import requests
import json
from datetime import datetime
import numpy as np
import config_backtest as cfg


def _eh_preco(texto: str) -> float | None:
    """Detecta se o texto contém um preço (ex: '175.320' ou '175320')."""
    limpo = texto.replace('.', '').replace(',', '.').strip()
    try:
        val = float(limpo)
        # Plausível para WIN (100k-250k) ou WDO (4k-8k)
        if 90000 <= val <= 300000 or 3000 <= val <= 10000:
            return val
    except:
        pass
    return None


def montar_contexto(metricas: dict, ativo: str) -> str:
    """Monta contexto compacto de mercado para o prompt."""
    m = metricas
    def _f(v, d=0):
        if v is None or (isinstance(v, float) and np.isnan(v)): return '—'
        return f"{v:,.{d}f}"

    return f"""
ATIVO: {ativo} | {datetime.now().strftime('%H:%M:%S')}
PREÇO ATUAL: {_f(m.get('preco'), 0)}
TENDÊNCIA: {m.get('tendencia','—')} | FASE: {m.get('fase','—')} | SAÚDE: {m.get('saude','—')}
CANDLE ATUAL: {m.get('tipo_candle','—')}
ATR: {_f(m.get('atr'), 0)} pts
DELTA ACUMULADO (500 candles): {m.get('delta_acum','—')} {'(fluxo disponível)' if m.get('tem_fluxo') else '(sem dado de fluxo)'}
RAZÃO AGRESSÃO: {_f(m.get('razao_agressao'), 3) if m.get('razao_agressao') else '—'}
RESISTÊNCIA MAIS PRÓXIMA: {_f(m.get('sr_acima'), 0)} ({_f(m.get('dist_sr_acima_atr'), 1)} ATR | {int(m.get('qualidade_sr_acima',0))} toques)
SUPORTE MAIS PRÓXIMO: {_f(m.get('sr_abaixo'), 0)} ({_f(m.get('dist_sr_abaixo_atr'), 1)} ATR | {int(m.get('qualidade_sr_abaixo',0))} toques)
BUILD-UP: {'Ativo há ' + str(m.get('buildup_duracao',0)) + ' candles' if m.get('buildup_ativo') else 'Inativo'}
DOUBLE PRESSURE: {'Qualidade ' + str(m.get('dp_qualidade','—')) + ' (' + str(m.get('dp_streak',0)) + ' candles)' if m.get('double_pressure') else 'Não detectado'}
""".strip()


def analisar_com_pergunta(metricas: dict, pergunta: str, ativo: str) -> str:
    """Wrapper simples que retorna só o texto — para compatibilidade."""
    resultado = analisar_com_pergunta_raw(metricas, pergunta, ativo)
    if isinstance(resultado, dict):
        return resultado.get('texto') or resultado.get('erro', '⚠ Sem resposta')
    return str(resultado)


def analisar_com_pergunta_raw(metricas: dict, pergunta: str, ativo: str) -> dict:
    """
    Ponto de entrada principal do chat do dashboard.
    Retorna dict com campos:
      - texto: resposta da IA (str)
      - tokens_usados: int
      - erro: str (se houver erro não-429)
      - erro_429: bool
      - retry_after: int (segundos a aguardar, se 429)
    """
    if cfg.GEMINI_API_KEY == "SUA_API_KEY_AQUI":
        return {'erro': 'API Key do Gemini não configurada em config_backtest.py'}

    contexto = montar_contexto(metricas, ativo)
    preco_alvo = _eh_preco(pergunta)

    if preco_alvo:
        instrucao = f"""O trader quer analisar o nível de preço {preco_alvo:.0f} no {ativo}.

Com base no contexto atual do mercado abaixo, responda:
1. POSIÇÃO: esse nível está acima ou abaixo do preço atual? A que distância em pontos e ATRs?
2. HISTÓRICO: esse nível coincide com algum S/R relevante identificado?
3. PROBABILIDADE: dado o contexto atual (tendência, fase, delta, double pressure), qual a leitura para esse nível? Bounce, rompimento ou indefinido?
4. AÇÃO: o que o grafista raiz deve observar ao se aproximar desse nível?

Seja direto e objetivo. Máximo 200 palavras."""
    else:
        instrucao = f"""O trader pergunta: "{pergunta}"

Responda como mentor de price action raiz. Use os dados de mercado abaixo para embasar sua resposta.
Seja direto, objetivo e acionável. Máximo 200 palavras."""

    prompt = f"""{cfg.GEMINI_SYSTEM_PROMPT}

---CONTEXTO DO MERCADO---
{contexto}

---
{instrucao}"""

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{cfg.GEMINI_MODEL}:generateContent?key={cfg.GEMINI_API_KEY}")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": cfg.GEMINI_MAX_TOKENS,
            "temperature": 0.3
        }
    }

    try:
        r = requests.post(url, json=payload, timeout=30)

        # Rate limit
        if r.status_code == 429:
            retry_after = 60  # default conservador
            # Tenta extrair do header
            try:
                retry_after = int(r.headers.get('Retry-After', 60))
            except Exception:
                pass
            # Tenta extrair do corpo da resposta (Gemini inclui em details)
            try:
                body = r.json()
                details = body.get('error', {}).get('details', [])
                for d in details:
                    if d.get('@type', '').endswith('RetryInfo'):
                        delay = d.get('retryDelay', '')
                        # formato: "30s" ou "90s"
                        if delay.endswith('s'):
                            retry_after = int(delay[:-1])
                        break
            except Exception:
                pass
            return {'erro_429': True, 'retry_after': retry_after}

        r.raise_for_status()
        data = r.json()

        candidates = data.get('candidates', [])
        if not candidates:
            return {'erro': 'Sem candidatos na resposta da API'}

        content = candidates[0].get('content', {})
        parts = content.get('parts', [])
        texto = ' '.join(p.get('text', '') for p in parts if p.get('text')).strip()

        if not texto:
            finish = candidates[0].get('finishReason', '')
            return {'erro': f'Modelo não gerou resposta (finishReason: {finish})'}

        # Tokens usados
        usage = data.get('usageMetadata', {})
        tokens_usados = usage.get('candidatesTokenCount', 0)

        return {'texto': texto, 'tokens_usados': tokens_usados}

    except requests.exceptions.Timeout:
        return {'erro': 'Timeout — tente novamente'}
    except Exception as e:
        return {'erro': str(e)[:80]}
