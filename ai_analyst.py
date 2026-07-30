# =============================================================================
# ai_analyst.py — Mentor IA (Gemini + OpenRouter, integrado ao dashboard)
# =============================================================================

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime

import numpy as np
import requests

import config_backtest as cfg
import diario_trade
import persistencia
import tracados as tracados_mod
import metricas as metricas_mod


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


def montar_contexto(metricas: dict, ativo: str, diario_recente: str | None = None,
                     tracados_analisados: list | None = None) -> str:
    """
    Monta contexto compacto de mercado para o prompt. `diario_recente`
    (opcional, produzido por `diario_trade.resumo_recente_para_ia`) é só
    anexado se fornecido — esta função continua pura/sem I/O, quem busca o
    diário (e os traçados) é `_montar_prompt`.

    `tracados_analisados` (opcional, produzido por
    `metricas.analisar_tracados`) substitui os níveis de S/R AUTOMÁTICOS
    como fonte de "RESISTÊNCIA/SUPORTE MAIS RELEVANTE" — Modo
    Aprendizagem.txt, 2026-07-20: o motor não sugere mais S/R sozinho, o
    Mentor IA agora comenta sobre os traçados que o próprio Jesué desenhou,
    não mais um detector em que ele decidiu não confiar.
    """
    m = metricas
    def _f(v, d=0):
        if v is None or (isinstance(v, float) and np.isnan(v)): return '—'
        return f"{v:,.{d}f}"

    # Pivô imediato (features.py::calcular_pivo_imediato) — o ÚLTIMO
    # fundo/topo confirmado, mesmo sem virar zona madura ainda. Caso real
    # que motivou (WIN, 08/07/2026): sem isso, o Mentor só via a zona
    # madura do ranking (RESISTÊNCIA/SUPORTE acima) e ignorava um fundo
    # recém-formado bem mais perto do preço — a referência que o trader de
    # fato monitora primeiro na leitura gráfica. metricas.py já filtra: só
    # vem preenchido quando NÃO é redundante com um dos níveis rankeados.
    def _fmt_pivo(pivo):
        if not pivo:
            return '(nenhum — já coberto pelo nível acima ou sem pivô recente do lado certo do preço)'
        return f"{_f(pivo['preco'], 0)} ({pivo['idade']} candles atrás, 1º toque, AINDA SEM RETESTE)"

    # Traçado mais próximo de cada lado (Modo Aprendizagem.txt, 2026-07-20)
    # — substitui o antigo "nível #1 rankeado" do detector automático.
    # `tipo` já vem calculado por analisar_linha_generica relativo ao
    # preço ATUAL (resistencia = acima do preço, suporte = abaixo), então
    # o mais próximo de cada lado é só o de menor/maior `linha_atual`.
    def _rank1(lado: str) -> dict:
        alvo_tipo = 'resistencia' if lado == 'acima' else 'suporte'
        candidatos = [t for t in (tracados_analisados or []) if t.get('tipo') == alvo_tipo]
        if not candidatos:
            return {}
        chave = min if lado == 'acima' else max
        return chave(candidatos, key=lambda t: t.get('linha_atual', 0))

    def _fmt_nivel(lado: str) -> str:
        r = _rank1(lado)
        if not r:
            return '(nenhum traçado marcado deste lado — desenhe uma linha na aba Traçados)'
        extras = []
        if r.get('forte'):
            extras.append('REGIÃO FORTE')
        # Bipolaridade/regime (mesmo campo que a UI de Traçados usa nos
        # cards de Estudos, ver dashboard.py::_card_estudo_tracado) — dá o
        # sinal já validado ao modelo, mesmo espírito do reforço de
        # 2026-07-08/2026-07-13 que motivou os campos abaixo originalmente
        # (ver GEMINI_SYSTEM_PROMPT).
        regime = r.get('regime') or {}
        regime_atual = regime.get('regime_atual') or {}
        if regime_atual.get('forte'):
            extras.append('FORTE NO REGIME ATUAL')
        cruzamento = r.get('cruzamento_automatico')
        if cruzamento:
            extras.append('COINCIDE COM ZONA JÁ ROMPIDA+RETESTADA')
        acao = r.get('acao') or {}
        postura = acao.get('postura')
        if postura:
            extras.append(f"AÇÃO: {postura.upper().replace('_', ' ')}")
            if acao.get('magnitude_esperada'):
                extras.append(f"magnitude esperada {acao['magnitude_esperada']}")
            inval = acao.get('invalidacao')
            if inval is not None and not (isinstance(inval, float) and np.isnan(inval)):
                extras.append(f"invalidação em {_f(inval, 0)}")
        prob = r.get('prob_bounce')
        prob_txt = ''
        if prob is not None and not (isinstance(prob, float) and np.isnan(prob)):
            prob_txt = f" | Probabilidade JÁ CALCULADA pelo sistema: {prob:.0f}% bounce / {100-prob:.0f}% rompimento"
        extras_txt = f" | {' | '.join(extras)}" if extras else ''
        return (f"{_f(r.get('linha_atual'), 0)} (traçado manual do Jesué | "
                f"{int(r.get('toques', 0))} toques{extras_txt}{prob_txt})")

    return f"""
ATIVO: {ativo} | {datetime.now().strftime('%H:%M:%S')}
PREÇO ATUAL: {_f(m.get('preco'), 0)}
TENDÊNCIA: {m.get('tendencia','—')} | FASE: {m.get('fase','—')} | SAÚDE: {m.get('saude','—')}
CANDLE ATUAL: {m.get('tipo_candle','—')}
ATR: {_f(m.get('atr'), 0)} pts
DELTA ACUMULADO (500 candles): {m.get('delta_acum','—')} {'(fluxo disponível)' if m.get('tem_fluxo') else '(sem dado de fluxo)'}
RAZÃO AGRESSÃO: {_f(m.get('razao_agressao'), 3) if m.get('razao_agressao') else '—'}
RESISTÊNCIA MAIS RELEVANTE: {_fmt_nivel('acima')}
SUPORTE MAIS RELEVANTE: {_fmt_nivel('abaixo')}
TOPO EM FORMAÇÃO (mais tático, pode estar mais perto que a resistência acima): {_fmt_pivo(m.get('pivo_imediato_acima'))}
FUNDO EM FORMAÇÃO (mais tático, pode estar mais perto que o suporte acima): {_fmt_pivo(m.get('pivo_imediato_abaixo'))}
BUILD-UP: {'Ativo há ' + str(m.get('buildup_duracao',0)) + ' candles' if m.get('buildup_ativo') else 'Inativo'}
DOUBLE PRESSURE: {'Qualidade ' + str(m.get('dp_qualidade','—')) + ' (' + str(m.get('dp_streak',0)) + ' candles)' if m.get('double_pressure') else 'Não detectado'}
{chr(10) + diario_recente if diario_recente else ''}""".strip()


def _montar_prompt(metricas: dict, pergunta: str, ativo: str) -> str:
    """Monta o prompt completo (system + contexto de mercado + pergunta) —
    compartilhado por todos os provedores (Gemini, OpenRouter)."""
    diario_recente = diario_trade.resumo_recente_para_ia(ativo)
    # Traçados manuais (Modo Aprendizagem.txt, 2026-07-20) — lê candles
    # fresco do disco (mesmo padrão de diario_trade/metricas.
    # montar_snapshot_diario: este processo não tem acesso ao estado em
    # memória do dashboard, é I/O independente por design). Falha (base ou
    # traçados ausentes) vira lista vazia, nunca exceção — _fmt_nivel já
    # trata isso como "nenhum traçado marcado deste lado".
    try:
        df_ativo = persistencia.carregar_candles(ativo)
        tracados_df = tracados_mod.listar_tracados(ativo)
        tracados_analisados = (
            metricas_mod.analisar_tracados(df_ativo, tracados_df)
            if df_ativo is not None else []
        )
    except Exception:
        tracados_analisados = []
    contexto = montar_contexto(metricas, ativo, diario_recente=diario_recente,
                                tracados_analisados=tracados_analisados)
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

    return f"""{cfg.GEMINI_SYSTEM_PROMPT}

---CONTEXTO DO MERCADO---
{contexto}

---
{instrucao}"""


def analisar_com_pergunta(metricas: dict, pergunta: str, ativo: str, modelo: str | None = None) -> str:
    """Wrapper simples que retorna só o texto — para compatibilidade."""
    resultado = analisar_com_pergunta_raw(metricas, pergunta, ativo, modelo)
    if isinstance(resultado, dict):
        return resultado.get('texto') or resultado.get('erro', '⚠ Sem resposta')
    return str(resultado)


def _chamar_gemini(prompt: str) -> dict:
    """Chamada ao Google Gemini (provedor padrão/original)."""
    if cfg.GEMINI_API_KEY == "SUA_API_KEY_AQUI":
        return {'erro': 'API Key do Gemini não configurada em config_backtest.py'}

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{cfg.GEMINI_MODEL}:generateContent?key={cfg.GEMINI_API_KEY}")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": cfg.GEMINI_MAX_TOKENS,
            "temperature": 0.3,
            # gemini-2.5-flash pensa por padrão (thinking) e os tokens de
            # raciocínio consomem o mesmo orçamento de maxOutputTokens —
            # descoberto em 2026-07-08 depois de respostas cortando no meio
            # da frase (finishReason=MAX_TOKENS com 1425 de 1500 tokens
            # gastos em "thoughtsTokenCount", só 71 sobrando pro texto
            # visível). Desativado porque o Mentor IA não precisa de
            # raciocínio em cadeia — precisa de respostas diretas e curtas
            # (o próprio prompt já pede isso). Ver CHANGELOG.md.
            "thinkingConfig": {"thinkingBudget": 0},
        }
    }

    try:
        r = requests.post(url, json=payload, timeout=30)

        if r.status_code == 429:
            retry_after = 60
            try:
                retry_after = int(r.headers.get('Retry-After', 60))
            except Exception:
                pass
            try:
                body = r.json()
                details = body.get('error', {}).get('details', [])
                for d in details:
                    if d.get('@type', '').endswith('RetryInfo'):
                        delay = d.get('retryDelay', '')
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

        usage = data.get('usageMetadata', {})
        tokens_usados = usage.get('candidatesTokenCount', 0)

        return {'texto': texto, 'tokens_usados': tokens_usados, 'modelo_usado': cfg.GEMINI_MODEL}

    except requests.exceptions.Timeout:
        return {'erro': 'Timeout — tente novamente'}
    except Exception as e:
        return {'erro': str(e)[:80]}


def _chamar_openrouter(prompt: str, modelo: str) -> dict:
    """
    Chamada a um modelo específico via OpenRouter (API compatível com
    OpenAI — messages, não contents/parts como o Gemini). Usada tanto pela
    seleção manual (modelo específico, grátis ou pago) quanto internamente
    pelo modo automático (`_chamar_openrouter_auto`), um modelo por vez.
    """
    if not cfg.OPENROUTER_API_KEY or cfg.OPENROUTER_API_KEY == "SUA_API_KEY_AQUI":
        return {'erro': 'API Key da OpenRouter não configurada em config_backtest.py'}

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": modelo,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": cfg.GEMINI_MAX_TOKENS,
        "temperature": 0.3,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)

        if r.status_code == 429:
            retry_after = 30  # free tier da OpenRouter é por upstream — janela curta
            try:
                body = r.json()
                retry_after = int(body.get('error', {}).get('metadata', {}).get('retry_after_seconds', retry_after))
            except Exception:
                pass
            return {'erro_429': True, 'retry_after': retry_after, 'modelo_usado': modelo}

        if r.status_code != 200:
            try:
                msg = r.json().get('error', {}).get('message', r.text[:100])
            except Exception:
                msg = r.text[:100]
            return {'erro': f'{modelo}: {msg}'[:150], 'modelo_usado': modelo}

        data = r.json()
        choices = data.get('choices', [])
        if not choices:
            return {'erro': f'{modelo}: sem choices na resposta', 'modelo_usado': modelo}

        texto = (choices[0].get('message', {}) or {}).get('content', '').strip()
        if not texto:
            return {'erro': f'{modelo}: resposta vazia (finish_reason: {choices[0].get("finish_reason")})',
                    'modelo_usado': modelo}

        usage = data.get('usage', {})
        tokens_usados = usage.get('completion_tokens', 0)

        return {'texto': texto, 'tokens_usados': tokens_usados, 'modelo_usado': modelo}

    except requests.exceptions.Timeout:
        return {'erro': f'{modelo}: timeout — tente novamente', 'modelo_usado': modelo}
    except Exception as e:
        return {'erro': f'{modelo}: {str(e)[:80]}', 'modelo_usado': modelo}


def _chamar_openrouter_auto(prompt: str) -> dict:
    """
    Modo "Automático (grátis)": tenta os modelos de `cfg.OPENROUTER_MODELOS_FREE`
    em ordem, um a um, até o primeiro que responder sem erro/429. Existe
    porque modelos gratuitos da OpenRouter ficam rate-limited pelo provedor
    upstream de forma independente uns dos outros e volátil ao longo do dia
    (confirmado em 2026-07-08: 6 de 8 modelos testados estavam
    rate-limited no mesmo instante) — um único modelo fixo bateria no
    "limite excedido" com frequência.
    """
    ultimo_erro = {'erro': 'Nenhum modelo gratuito da OpenRouter respondeu'}
    for modelo in cfg.OPENROUTER_MODELOS_FREE:
        resultado = _chamar_openrouter(prompt, modelo)
        if 'texto' in resultado:
            return resultado
        ultimo_erro = resultado
    return ultimo_erro


def opcoes_modelo() -> list[dict]:
    """Opções pro seletor de modelo do chat (dcc.Dropdown) — fonte única,
    reaproveitada por dashboard.py e dashboard_98.py, pra nunca divergir."""
    opcoes = [
        {'label': 'Gemini 2.5 Flash (padrão)', 'value': 'gemini'},
        {'label': 'OpenRouter — Automático', 'value': 'openrouter-auto'},
    ]
    for modelo_id in cfg.OPENROUTER_MODELOS_FREE:
        opcoes.append({'label': modelo_id, 'value': modelo_id})
    for modelo_id, descricao in cfg.OPENROUTER_MODELOS_PAGOS.items():
        opcoes.append({'label': descricao, 'value': modelo_id})
    return opcoes


def analisar_com_pergunta_raw(metricas: dict, pergunta: str, ativo: str, modelo: str | None = None) -> dict:
    """
    Ponto de entrada principal do chat do dashboard.

    `modelo` (novo em 2026-07-08, opcional — None preserva o comportamento
    antigo, sempre Gemini):
      - None ou 'gemini'        → Google Gemini (cfg.GEMINI_MODEL)
      - 'openrouter-auto'       → OpenRouter, tenta cfg.OPENROUTER_MODELOS_FREE
                                  em ordem até um responder
      - qualquer outro valor    → OpenRouter, modelo específico (usado pelo
                                  seletor manual — grátis ou pago, ex.
                                  'anthropic/claude-sonnet-5')

    Retorna dict com campos:
      - texto: resposta da IA (str)
      - tokens_usados: int
      - modelo_usado: str (qual modelo de fato respondeu — relevante no
        modo automático, onde pode não ser o primeiro da lista)
      - erro: str (se houver erro não-429)
      - erro_429: bool
      - retry_after: int (segundos a aguardar, se 429)
    """
    prompt = _montar_prompt(metricas, pergunta, ativo)
    return _despachar_para_modelo(prompt, modelo)


def _despachar_para_modelo(prompt: str, modelo: str | None = None) -> dict:
    """Envia um prompt JÁ PRONTO pro provedor certo — o "hub" único de
    verdade (extraído de `analisar_com_pergunta_raw` em 2026-07-30, item D
    do "upgrade diario.docx": o Mentor de IA de análise retrospectiva do
    Diário precisa do MESMO hub Gemini/OpenRouter do chat de mercado, só
    com um prompt diferente — nenhum dos dois deve ter lógica própria de
    "qual provedor chamar", ou eles divergem com o tempo)."""
    if modelo is None or modelo == 'gemini':
        return _chamar_gemini(prompt)
    if modelo == 'openrouter-auto':
        return _chamar_openrouter_auto(prompt)
    return _chamar_openrouter(prompt, modelo)


# =============================================================================
# MENTOR DE IA — análise retrospectiva do Diário (item D, "upgrade
# diario.docx", 2026-07-30)
# =============================================================================
# "Faça uma análise interpretativa do meu operacional, analisando curva de
# aprendizagem, evolução operacional/estratégica e próximos passos. Para
# controle de tokens, esse feedback deve ser diário, uma vez ao dia
# analisando do dia anterior para trás." — decisões confirmadas com o
# Jesué: tom DIRETO e objetivo (não reflexivo/coach); usa o MESMO hub
# Gemini/OpenRouter do chat (`_despachar_para_modelo`), nunca uma chamada
# de API própria.
#
# A cadência de "1x por dia" é reforçada em 2 camadas, não só de boa fé:
# (1) o dado em si (`diario_trade.montar_resumo_para_mentor_ia`) já corta
# em "até ontem", então gerar de novo no mesmo dia produziria o MESMO
# resumo; (2) o cache abaixo guarda a data da última geração, e a UI
# (`diario_app_classico.py`) usa isso pra desabilitar o botão até o dia
# seguinte — sem impedir uma geração manual extra se o Jesué realmente
# quiser (o controle de custo é undo-ável por decisão dele, não uma trava
# de sistema).

_MENTOR_LOCK_STALE_SEGUNDOS = 60


def _caminho_analise_diaria():
    return cfg.PASTA_BASE_PROCESSADA / "mentor_analise_diaria.json"


def _caminho_analise_diaria_lock():
    return cfg.PASTA_BASE_PROCESSADA / ".mentor_analise_diaria.lock"


@contextmanager
def _lock_analise_diaria():
    """Mesmo mecanismo de `config_operacional._lock` (criação atômica via
    O_EXCL) — colisão real é rara (1 usuário, 1 botão, cadência diária)."""
    caminho = _caminho_analise_diaria_lock()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if caminho.exists():
        try:
            idade = time.time() - caminho.stat().st_mtime
        except FileNotFoundError:
            idade = 0
        if idade > _MENTOR_LOCK_STALE_SEGUNDOS:
            try:
                caminho.unlink()
            except FileNotFoundError:
                pass
    try:
        fd = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        yield False
        return
    try:
        yield True
    finally:
        try:
            caminho.unlink()
        except FileNotFoundError:
            pass


def carregar_analise_diaria() -> dict | None:
    """Última análise gerada — `{'data': 'YYYY-MM-DD', 'texto': ...,
    'modelo_usado': ..., 'gerado_em': isoformat, 'tokens_usados': int}`,
    ou None se nunca foi gerada. `data` é a data em que foi gerada (hoje),
    não o período analisado — é o que a UI compara pra saber se já rodou
    hoje."""
    caminho = _caminho_analise_diaria()
    if not caminho.exists():
        return None
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _salvar_analise_diaria(dados: dict) -> None:
    caminho = _caminho_analise_diaria()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho_tmp = caminho.with_name(f"{caminho.stem}.tmp{os.getpid()}{caminho.suffix}")
    with open(caminho_tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(caminho_tmp, caminho)


def _montar_prompt_evolucao_operacional(resumo_dados: str) -> str:
    """Prompt do Mentor de IA — análise retrospectiva do Diário. Tom
    DIRETO e objetivo (decisão do Jesué, 2026-07-30): números primeiro,
    sem enrolação motivacional, nunca inventando detalhe que não esteja no
    resumo de dados."""
    return f"""Você é o mentor de trading do Jesué — day trader de WIN/WDO (mini
índice e mini dólar, B3), price action puro. Analise a evolução operacional
dele com base SOMENTE nos dados abaixo (nunca invente detalhe, número ou
trade que não esteja aqui).

---DADOS DO DIÁRIO---
{resumo_dados}
---

Responda em 3 blocos curtos, tom DIRETO e objetivo — fatos e números
primeiro, sem enrolação motivacional:

1. CURVA DE APRENDIZAGEM: o que mudou entre a 1ª e a 2ª metade das
   operações? Cite os números.
2. EVOLUÇÃO OPERACIONAL/ESTRATÉGICA: o que está funcionando de verdade
   (setup, disciplina) e o que continua vazando resultado?
3. PRÓXIMOS PASSOS: 1-2 ações concretas e específicas, baseadas nos
   números acima — não genéricas.

Máximo 250 palavras. Nunca apresente probabilidade como certeza."""


def gerar_analise_evolucao_operacional(resumo_dados: str, modelo: str | None = None) -> dict:
    """Gera a análise retrospectiva do Mentor de IA e já GRAVA no cache
    (`_salvar_analise_diaria`) se a chamada for bem-sucedida — quem chama
    (a callback da UI) não precisa se preocupar em persistir. `resumo_dados`
    vem de `diario_trade.montar_resumo_para_mentor_ia()`; passado como
    parâmetro (não buscado aqui) pra manter este módulo sem acoplamento
    direto a quando/como o Diário monta o resumo.

    Retorna o mesmo formato de dict de `analisar_com_pergunta_raw`
    (texto/tokens_usados/modelo_usado/erro/erro_429), sem side-effect no
    cache se vier erro — só sucesso é cacheado, senão um 429 temporário
    "gastaria" a cota do dia sem nunca ter mostrado nada ao Jesué."""
    if not resumo_dados.strip():
        return {"erro": "Sem operações reais suficientes pra gerar uma análise ainda."}
    with _lock_analise_diaria() as adquirida:
        if not adquirida:
            return {"erro": "Já existe uma geração em andamento — aguarde terminar."}
        prompt = _montar_prompt_evolucao_operacional(resumo_dados)
        resultado = _despachar_para_modelo(prompt, modelo)
        if resultado.get("texto"):
            _salvar_analise_diaria({
                "data": datetime.now().date().isoformat(),
                "texto": resultado["texto"],
                "modelo_usado": resultado.get("modelo_usado", modelo or "gemini"),
                "tokens_usados": resultado.get("tokens_usados", 0),
                "gerado_em": datetime.now().isoformat(),
            })
        return resultado
