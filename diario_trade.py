# =============================================================================
# diario_trade.py — Diário de Trade: lógica pura, sem Dash/HTML
# =============================================================================
# Mesmo espírito de `metricas.py`/`calendario_economico.py`: módulo de dados
# compartilhado pelos dois front-ends (dashboard.py e dashboard_98.py), zero
# construção de HTML aqui. Planejado em 08/07/2026 — ver
# `C:\Users\jesue\.claude\plans\t-tulo-super-quant-eager-knuth.md`.
#
# Uma operação = uma linha em `data/base_processada/diario_trades.parquet`
# (via `persistencia.salvar_diario`/`carregar_diario`). Vem de duas fontes:
# migração única do `diario.xlsm` (`fonte_registro='importado_xlsm'`,
# ver `migrar_diario_xlsm.py`) ou lançamento manual no app
# (`fonte_registro='manual'`).
# =============================================================================

from __future__ import annotations

import io
import json
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
from PIL import Image

import config_backtest as cfg
import persistencia
import mt5_loader

# -----------------------------------------------------------------------------
# VOCABULÁRIOS CANÔNICOS
# -----------------------------------------------------------------------------
# `Estratégia`/`Sala Op.\Análise` na planilha original são texto livre com
# deriva de acentuação (~26 grafias pra ~15-18 conceitos reais, ver
# CHANGELOG.md da migração) — aqui viram um vocabulário fechado, revisado
# pelo Jesué via `migrar_diario_xlsm.py --dry-run` antes da escrita real.

ESTRATEGIA_TAGS_CANONICAS = [
    "suporte_resistencia",
    "lta_ltb",
    "canal",
    "inversao_polaridade",
    "bandeira",
    "pullback_mme21",
    "medias_moveis",
    "triangulo",
    "fibonacci",
    "rompimento",
    "reteste",
    "topo_fundo_duplo",
    "ombro_cabeca_ombro",
    "abc",
    "outro",
]

SALA_ANALISE_CANONICA = [
    "btv_igor",
    "btv_marquinhos",
    "btv_kita",
    "analise_propria",
    "outro",
]

TIPO_OPERACAO = ["long", "short"]

# 'Simulador' NÃO entra aqui — vira a flag `is_simulated` própria (unifica
# o que a planilha original espalhava entre a aba Diario e a aba Sim).
TIPO_SAIDA = ["stop", "breakeven", "gain", "manual"]

# Vocabulário ABERTO (não travado) — a UI permite adicionar tags novas além
# desta lista inicial, que só serve de sugestão/autocomplete.
TAGS_SETUP_ERRO_INICIAIS = [
    "entrada_antecipada",
    "entrada_atrasada",
    "sem_confirmacao",
    "contra_tendencia",
    "protecao_tardia",
    "alvo_mal_dimensionado",
    "operou_fora_da_janela",
    "gerenciamento_correto",
    "disciplina_apos_loss",
]

FONTE_MANUAL = "manual"
FONTE_IMPORTADO = "importado_xlsm"

# Ordem canônica de colunas de `diario_trades.parquet` — mantém o schema
# estável entre migração e lançamento manual.
COLUNAS_DIARIO = [
    "id_trade", "data_op", "datetime_entrada", "datetime_saida",
    "ativo", "contrato", "tempo_grafico", "janela_pregao",
    "estrategia_tags", "estrategia_raw",
    "sala_analise", "sala_analise_raw",
    "tipo_operacao", "alvo_pontos", "stop_pontos", "lote",
    "preco_entrada", "preco_saida", "tipo_saida", "is_simulated",
    "resultado_pontos", "saldo_operacao_reais",
    "resumo_operacional", "obs",
    "imagem_arquivo",
    "nivel_sr_referencia", "nivel_sr_tipo", "nivel_sr_forte",
    "snapshot_mercado_json",
    "imagens_adicionais",
    "tags_setup_erro",
    "conta", "data_replay",
    "fonte_registro", "criado_em",
]

# Tabela separada da principal (não colunas extras em COLUNAS_DIARIO) —
# mantém o log de trades append-only/imutável; reconciliação é recomputável
# a qualquer momento via `reconciliar_com_mt5`, não é histórico do trade.
COLUNAS_RECONCILIACAO = [
    "id_trade", "mt5_position_id", "mt5_ticket_entrada", "mt5_ticket_saida",
    "mt5_divergencia_reais", "confianca", "mt5_match_status",
]

# Idem — recomputável via `avaliar_aderencia_sr`, não é histórico do trade.
COLUNAS_ADERENCIA_SR = [
    "id_trade", "data_op", "ativo", "nivel_sr_referencia", "nivel_sr_tipo_informado",
    "lado_geometrico", "nivel_motor_preco", "nivel_motor_rank", "diferenca_atr",
    "forte", "status", "prob_bounce", "rompimento_hoje", "classificacao",
]


def _proximo_id_trade(df: pd.DataFrame | None) -> int:
    if df is None or df.empty:
        return 1
    return int(df["id_trade"].max()) + 1


def novo_trade(**campos) -> dict:
    """
    Monta um dict com todas as colunas do schema (default None/valor neutro
    onde o chamador não informar), pronto pra virar uma linha de
    `diario_trades.parquet`. Não grava nada em disco — ver `registrar_trade`.
    """
    # Base com None em toda coluna do schema, sobrescrita pelos defaults REAIS
    # (não dá pra usar dict.setdefault depois — a linha de baixo já teria
    # deixado a chave "presente" com valor None, e setdefault só age quando a
    # chave está AUSENTE, nunca aplicaria nada) e por fim pelo que o
    # chamador passou de verdade em `campos`.
    trade = {coluna: None for coluna in COLUNAS_DIARIO}
    trade.update({
        "tempo_grafico": "5 min.",
        "lote": 1,
        "is_simulated": False,
        "estrategia_tags": [],
        "tags_setup_erro": [],
        "fonte_registro": FONTE_MANUAL,
        "criado_em": datetime.now(),
    })
    trade.update(campos)
    # `dcc.Input(type='date')` devolve string ISO ('YYYY-MM-DD'), mas as
    # linhas migradas do xlsm têm `datetime.date` de verdade nessa coluna —
    # concatenar os dois tipos misturados quebra a inferência de schema do
    # pyarrow no `to_parquet` (arrow tenta serializar a coluna toda como
    # date32/int32 e trava na primeira string). Normaliza aqui, na origem.
    for campo_data in ("data_op", "data_replay"):
        valor = trade.get(campo_data)
        if isinstance(valor, str):
            trade[campo_data] = date.fromisoformat(valor)
    return trade


def registrar_trade(
    campos: dict, imagem_bytes: bytes | None = None,
    snapshot_mercado: dict | None = None,
) -> int | None:
    """
    Atribui `id_trade` sequencial e grava um trade novo no diário. Retorna o
    `id_trade` atribuído, ou None se a trava estava ocupada (chamador —
    tipicamente um callback de botão "Salvar" na UI — deve mostrar um erro/
    retry visível, ao contrário do update de candle que pula em silêncio).

    Se `imagem_bytes` for fornecido, salva a imagem e preenche
    `imagem_arquivo` ANTES de gravar a linha — tudo dentro da mesma trava,
    pra nunca deixar um trade órfão sem imagem (ou vice-versa) se algo falhar
    no meio do caminho.

    `snapshot_mercado`, se fornecido, é um dict JSON-serializável (montado
    por `metricas.montar_snapshot_diario()` a partir do estado do motor de
    quant no momento do save) — serializado aqui como string JSON e gravado
    em `snapshot_mercado_json`, imutável, junto da linha do trade. Este
    módulo não sabe de onde o dict veio (zero import do motor ao vivo/
    `estado` global) — só aceita um dict pronto ou None e serializa. Escopo
    desta v1: só CAPTURA — comparação/validação contra `nivel_sr_referencia`
    fica para um ciclo futuro.
    """
    with persistencia.lock_diario() as adquirida:
        if not adquirida:
            return None
        df_atual = persistencia.carregar_diario()
        extra = {}
        if snapshot_mercado is not None:
            extra["snapshot_mercado_json"] = json.dumps(snapshot_mercado, ensure_ascii=False)
        trade = novo_trade(**campos, **extra)
        trade["id_trade"] = _proximo_id_trade(df_atual)
        if imagem_bytes is not None:
            trade["imagem_arquivo"] = salvar_imagem(
                imagem_bytes, trade["id_trade"], trade["ativo"], trade["data_op"]
            )
        df_novo = pd.DataFrame([trade])[COLUNAS_DIARIO]
        df_final = (
            pd.concat([df_atual, df_novo], ignore_index=True)
            if df_atual is not None else df_novo
        )
        persistencia.salvar_diario(df_final)
        return trade["id_trade"]


def salvar_imagem(conteudo_bytes: bytes, id_trade: int, ativo: str, data_op: date | datetime) -> str:
    """
    Salva um print (upload ou colado do clipboard) em
    `data/base_processada/diario_imagens/`, redimensionando pra no máximo
    `cfg.DIARIO_IMAGEM_LARGURA_MAX` de largura (evita crescimento sem
    controle do volume a cada print colado em resolução nativa). Retorna só
    o NOME do arquivo (não o path completo) — resolver o path sempre via
    `cfg.PASTA_DIARIO_IMAGENS / nome`.
    """
    cfg.PASTA_DIARIO_IMAGENS.mkdir(parents=True, exist_ok=True)

    imagem = Image.open(io.BytesIO(conteudo_bytes)).convert("RGB")
    if imagem.width > cfg.DIARIO_IMAGEM_LARGURA_MAX:
        nova_altura = int(imagem.height * cfg.DIARIO_IMAGEM_LARGURA_MAX / imagem.width)
        imagem = imagem.resize((cfg.DIARIO_IMAGEM_LARGURA_MAX, nova_altura), Image.LANCZOS)

    data_str = pd.Timestamp(data_op).strftime("%Y%m%d")
    nome_arquivo = f"{id_trade:04d}_{ativo}_{data_str}.png"
    imagem.save(cfg.PASTA_DIARIO_IMAGENS / nome_arquivo, format="PNG")
    return nome_arquivo


def listar_trades(
    ativo: str | None = None,
    janela: str | None = None,
    estrategia_tag: str | None = None,
    data_ini=None,
    data_fim=None,
    is_simulated: bool | None = None,
) -> pd.DataFrame:
    """Carrega o diário e aplica filtros opcionais. DataFrame vazio se nada existir ainda."""
    df = persistencia.carregar_diario()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_DIARIO)

    # `COLUNAS_DIARIO` pode ganhar colunas novas (ex: `snapshot_mercado_json`)
    # depois que o parquet em disco já foi escrito pela última vez — o union
    # de schema do `pd.concat` em `registrar_trade` só preenche a coluna nova
    # quando um trade NOVO é gravado, não retroativamente no arquivo parado.
    # `reindex` garante que toda coluna do schema atual sempre exista aqui
    # (preenchida com NaN/None onde faltar), sem exigir gravar nada em disco.
    df = df.reindex(columns=COLUNAS_DIARIO)

    if ativo is not None:
        df = df[df["ativo"] == ativo]
    if janela is not None:
        df = df[df["janela_pregao"] == janela]
    if estrategia_tag is not None:
        # `estrategia_tags` volta do parquet como numpy array, não list Python
        # (arrow serializa listas assim) — `isinstance(tags, list)` nunca é
        # True pra dado real, o filtro sempre voltava vazio. `in` funciona
        # igual sobre array ou list, só a checagem de tipo precisa mudar.
        df = df[df["estrategia_tags"].apply(
            lambda tags: hasattr(tags, "__iter__") and estrategia_tag in tags
        )]
    if data_ini is not None or data_fim is not None:
        # `data_op` guarda `datetime.date` puro — comparar direto com
        # `pd.Timestamp` levanta TypeError (date vs datetime não compara em
        # Python puro). Normaliza os dois lados pra `date` antes de comparar.
        datas = df["data_op"].apply(lambda d: pd.Timestamp(d).date() if d is not None else None)
        if data_ini is not None:
            df = df[datas >= pd.Timestamp(data_ini).date()]
            datas = datas[df.index]
        if data_fim is not None:
            df = df[datas <= pd.Timestamp(data_fim).date()]
    if is_simulated is not None:
        df = df[df["is_simulated"] == is_simulated]

    return df.sort_values("data_op").reset_index(drop=True)


def _maior_sequencia(sinais: pd.Series, alvo: int) -> int:
    """Maior número de ocorrências consecutivas de `alvo` (1=win, -1=loss) na série, em ordem cronológica."""
    maior = atual = 0
    for s in sinais:
        if s == alvo:
            atual += 1
            maior = max(maior, atual)
        else:
            atual = 0
    return maior


def _agrupar_soma_contagem(df: pd.DataFrame, coluna: str) -> dict:
    """`{valor_da_coluna: {'n': contagem, 'saldo': soma de saldo_operacao_reais}}`, ordenado por saldo desc."""
    agrupado = df.groupby(coluna)["saldo_operacao_reais"].agg(["count", "sum"])
    agrupado = agrupado.sort_values("sum", ascending=False)
    return {
        str(indice): {"n": int(linha["count"]), "saldo": float(linha["sum"])}
        for indice, linha in agrupado.iterrows()
    }


def calcular_estatisticas(df: pd.DataFrame | None = None) -> dict:
    """
    Estatísticas agregadas equivalentes à aba "Dashboard" da planilha
    original — win rate, payoff, expectância matemática, sequências,
    breakdowns por ativo/janela/sala/estratégia, evolução de saldo. Trades
    simulados (`is_simulated=True`) são SEMPRE excluídos, mesmo critério da
    planilha (`COUNTIFS(...,"<>Simulador")`) — estatística real de
    performance não deve incluir prática/replay.

    Se `df` não for passado, carrega o diário inteiro via `listar_trades()`.
    """
    if df is None:
        df = listar_trades()

    reais = df[~df["is_simulated"].astype(bool)].copy()
    n = len(reais)

    vazio = {
        "num_operacoes": 0, "saldo_total": 0.0, "max_gain": 0.0, "max_loss": 0.0,
        "taxa_acerto": 0.0, "payoff": None, "expectativa": 0.0,
        "n_win": 0, "n_loss": 0, "n_breakeven": 0,
        "maior_sequencia_win": 0, "maior_sequencia_loss": 0,
        "por_ativo": {}, "por_janela": {}, "por_sala": {}, "por_estrategia": {},
        "por_tags_setup_erro": {}, "por_dia": {},
        "drawdown_maximo_reais": 0.0,
        "evolucao_data": [], "evolucao_saldo_acumulado": [],
    }
    if n == 0:
        return vazio

    reais = reais.sort_values("data_op")
    saldo = reais["saldo_operacao_reais"].astype(float)

    vencedoras = reais[saldo > 0]
    perdedoras = reais[saldo < 0]

    n_win = len(vencedoras)
    n_loss = len(perdedoras)
    n_be = n - n_win - n_loss

    taxa_acerto = n_win / n
    media_ganho = float(vencedoras["saldo_operacao_reais"].mean()) if n_win else 0.0
    media_perda = float(perdedoras["saldo_operacao_reais"].mean()) if n_loss else 0.0
    payoff = abs(media_ganho / media_perda) if media_perda else None
    expectativa = taxa_acerto * media_ganho + (1 - taxa_acerto) * media_perda

    sinais = saldo.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))

    tags_explodidas = reais.explode("estrategia_tags")
    tags_explodidas = tags_explodidas[tags_explodidas["estrategia_tags"].notna()]

    tags_erro_explodidas = reais.explode("tags_setup_erro")
    tags_erro_explodidas = tags_erro_explodidas[tags_erro_explodidas["tags_setup_erro"].notna()]

    evolucao = reais[["data_op", "saldo_operacao_reais"]].copy()
    evolucao["saldo_acumulado"] = evolucao["saldo_operacao_reais"].astype(float).cumsum()

    # Drawdown máximo pico-vale em R$, não em % (desvio deliberado do plano
    # original, que seguia a definição textual do Zella Score). Testado
    # contra o parquet real: a versão em % explode pra valores sem sentido
    # (ex: -1185%) porque o "pico" nos primeiros dias da série é um saldo
    # acumulado pequeno (dezenas de R$), e o TradeZella só consegue expressar
    # essa métrica em % porque eles têm saldo de CONTA sincronizado do
    # corretor como denominador — este projeto não rastreia capital/conta,
    # só P&L acumulado a partir de zero. R$ é robusto a isso e já é a
    # unidade de todo o resto do dict (saldo_total/max_gain/max_loss).
    pico_corrente = evolucao["saldo_acumulado"].cummax()
    drawdown_maximo_reais = float((evolucao["saldo_acumulado"] - pico_corrente).min())

    return {
        "num_operacoes": n,
        "saldo_total": float(saldo.sum()),
        "max_gain": float(saldo.max()),
        "max_loss": float(saldo.min()),
        "taxa_acerto": taxa_acerto,
        "payoff": payoff,
        "expectativa": expectativa,
        "n_win": n_win, "n_loss": n_loss, "n_breakeven": n_be,
        "maior_sequencia_win": _maior_sequencia(sinais, 1),
        "maior_sequencia_loss": _maior_sequencia(sinais, -1),
        "por_ativo": _agrupar_soma_contagem(reais, "ativo"),
        "por_janela": _agrupar_soma_contagem(reais, "janela_pregao"),
        "por_sala": _agrupar_soma_contagem(reais, "sala_analise"),
        "por_estrategia": _agrupar_soma_contagem(tags_explodidas, "estrategia_tags"),
        "por_tags_setup_erro": _agrupar_soma_contagem(tags_erro_explodidas, "tags_setup_erro"),
        "por_dia": _agrupar_soma_contagem(reais, "data_op"),
        "drawdown_maximo_reais": drawdown_maximo_reais,
        "evolucao_data": evolucao["data_op"].astype(str).tolist(),
        "evolucao_saldo_acumulado": evolucao["saldo_acumulado"].tolist(),
    }


# Pesos/caps de `calcular_score_confiabilidade` — primeiro palpite (pesos
# iguais entre os 4 componentes, caps de payoff/profit_factor em 2.5),
# mesmo espírito das constantes ADERENCIA_SR_ATR_* acima: ajustar depois
# de conferir os setups com amostra real contra o que Jesué já sabe
# empiricamente sobre cada um.
SCORE_MIN_AMOSTRA = 10
SCORE_PESO_TAXA_ACERTO = 0.25
SCORE_PESO_PAYOFF = 0.25
SCORE_PESO_PROFIT_FACTOR = 0.25
SCORE_PESO_CONSISTENCIA = 0.25
SCORE_CAP_PAYOFF = 2.5
SCORE_CAP_PROFIT_FACTOR = 2.5


def calcular_score_confiabilidade(df: pd.DataFrame | None = None, min_amostra: int = SCORE_MIN_AMOSTRA) -> dict:
    """
    Score de Confiabilidade 0-100 por `estrategia_tags` (não por conta
    inteira, como o Zella Score original que inspirou isso — ver
    `analise-tradezella-para-super-quant.md` seção 3). Combina taxa de
    acerto, payoff, profit factor e um proxy de consistência (maior
    sequência de perdas relativa à amostra), em peso igual.

    Setups com `n < min_amostra` voltam com `score=None` e
    `amostra_suficiente=False` — os números brutos aparecem mesmo assim
    (referência), só o score não é calculado. Com o volume atual de
    trades, isso é a maioria dos casos, não uma borda rara.

    NÃO inclui "disciplina/aderência a regras" (6º componente do Zella
    Score original) — não há Playbook/regra formal cadastrada ainda,
    fabricar esse componente sem dado real seria pior que omiti-lo.
    """
    if df is None:
        df = listar_trades()
    reais = df[~df["is_simulated"].astype(bool)].copy()
    reais = reais.sort_values("data_op")
    tags_explodidas = reais.explode("estrategia_tags")
    tags_explodidas = tags_explodidas[tags_explodidas["estrategia_tags"].notna()]

    resultado = {}
    for tag in ESTRATEGIA_TAGS_CANONICAS:
        subset = tags_explodidas[tags_explodidas["estrategia_tags"] == tag]
        n = len(subset)
        if n == 0:
            resultado[tag] = {
                "n": 0, "taxa_acerto": None, "payoff": None, "profit_factor": None,
                "maior_sequencia_loss": 0, "score": None, "amostra_suficiente": False,
            }
            continue

        saldo = subset["saldo_operacao_reais"].astype(float)
        vencedoras = saldo[saldo > 0]
        perdedoras = saldo[saldo < 0]
        n_win = len(vencedoras)
        n_loss = len(perdedoras)

        taxa_acerto = n_win / n
        media_ganho = float(vencedoras.mean()) if n_win else 0.0
        media_perda = float(perdedoras.mean()) if n_loss else 0.0
        payoff = abs(media_ganho / media_perda) if media_perda else None
        soma_ganhos = float(vencedoras.sum())
        soma_perdas = float(perdedoras.sum())
        profit_factor = abs(soma_ganhos / soma_perdas) if soma_perdas else None

        sinais = saldo.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))
        maior_sequencia_loss = _maior_sequencia(sinais, -1)

        amostra_suficiente = n >= min_amostra
        score = None
        if amostra_suficiente:
            comp_taxa_acerto = taxa_acerto * 100
            comp_payoff = min((payoff or SCORE_CAP_PAYOFF) / SCORE_CAP_PAYOFF, 1.0) * 100
            comp_profit_factor = min((profit_factor or SCORE_CAP_PROFIT_FACTOR) / SCORE_CAP_PROFIT_FACTOR, 1.0) * 100
            comp_consistencia = (1 - maior_sequencia_loss / n) * 100
            score = (
                comp_taxa_acerto * SCORE_PESO_TAXA_ACERTO
                + comp_payoff * SCORE_PESO_PAYOFF
                + comp_profit_factor * SCORE_PESO_PROFIT_FACTOR
                + comp_consistencia * SCORE_PESO_CONSISTENCIA
            )

        resultado[tag] = {
            "n": n, "taxa_acerto": taxa_acerto, "payoff": payoff, "profit_factor": profit_factor,
            "maior_sequencia_loss": maior_sequencia_loss, "score": score,
            "amostra_suficiente": amostra_suficiente,
        }
    return resultado


def reconciliar_com_mt5(dias_atras: int = 30) -> pd.DataFrame:
    """
    Casa trades logados (reais, não simulados) dos últimos `dias_atras`
    dias contra execuções reais da conta MT5 (mesma conexão de
    `mt5_loader.py`), por `(ativo == raiz do contrato, mesma data)` —
    não exige horário/contrato exato (a maioria dos trades ainda não tem
    esses campos preenchidos), então a confiança do casamento fica marcada
    como `'aproximado'` sempre nesta v1 (ver riscos do plano — precisão
    maior só quando `contrato`/`datetime_entrada` existirem de verdade).
    Salva o resultado via `persistencia.salvar_reconciliacao` e retorna.
    """
    fim = datetime.now()
    inicio = fim - timedelta(days=dias_atras)

    df_deals = mt5_loader.buscar_execucoes_periodo(inicio, fim)
    if df_deals is None:
        resultado = pd.DataFrame(columns=COLUNAS_RECONCILIACAO)
        persistencia.salvar_reconciliacao(resultado)
        return resultado

    posicoes = mt5_loader.agrupar_posicoes(df_deals)
    posicoes["data"] = posicoes["datetime_entrada"].apply(lambda t: t.date() if t is not None else None)
    posicoes["usado"] = False

    trades = listar_trades(data_ini=inicio.date(), data_fim=fim.date(), is_simulated=False)

    linhas = []
    for _, trade in trades.iterrows():
        candidatas = posicoes[
            (posicoes["raiz_contrato"] == trade["ativo"])
            & (posicoes["data"] == trade["data_op"])
            & (~posicoes["usado"])
        ]
        if candidatas.empty:
            linhas.append({
                "id_trade": trade["id_trade"], "mt5_position_id": None,
                "mt5_ticket_entrada": None, "mt5_ticket_saida": None,
                "mt5_divergencia_reais": None, "confianca": None,
                "mt5_match_status": "sem_correspondencia_mt5",
            })
            continue

        # Sem horário exato pra desempatar entre candidatas do mesmo dia —
        # escolhe a de menor divergência absoluta contra o saldo logado
        # (melhor heurística disponível com os dados de hoje).
        saldo_logado = float(trade["saldo_operacao_reais"]) if pd.notna(trade["saldo_operacao_reais"]) else 0.0
        candidatas = candidatas.copy()
        candidatas["divergencia"] = (candidatas["profit_total"] - saldo_logado).abs()
        escolhida = candidatas.sort_values("divergencia").iloc[0]
        posicoes.loc[posicoes["position_id"] == escolhida["position_id"], "usado"] = True

        divergencia = float(escolhida["profit_total"]) - saldo_logado
        linhas.append({
            "id_trade": trade["id_trade"], "mt5_position_id": escolhida["position_id"],
            "mt5_ticket_entrada": escolhida["tickets_entrada"][0] if escolhida["tickets_entrada"] else None,
            "mt5_ticket_saida": escolhida["tickets_saida"][-1] if escolhida["tickets_saida"] else None,
            "mt5_divergencia_reais": divergencia,
            "confianca": "aproximado",
            "mt5_match_status": "ok" if abs(divergencia) < 1.0 else "divergente",
        })

    # Posições reais da conta sem nenhum trade logado correspondente —
    # execuções que aconteceram mas não foram registradas no diário.
    for _, pos in posicoes[~posicoes["usado"]].iterrows():
        linhas.append({
            "id_trade": None, "mt5_position_id": pos["position_id"],
            "mt5_ticket_entrada": pos["tickets_entrada"][0] if pos["tickets_entrada"] else None,
            "mt5_ticket_saida": pos["tickets_saida"][-1] if pos["tickets_saida"] else None,
            "mt5_divergencia_reais": None, "confianca": None,
            "mt5_match_status": "sem_correspondencia_diario",
        })

    resultado = pd.DataFrame(linhas, columns=COLUNAS_RECONCILIACAO)
    persistencia.salvar_reconciliacao(resultado)
    return resultado


# Thresholds de classificação de `avaliar_aderencia_sr` — primeiro palpite,
# não validado ainda contra casos reais. Ajustar aqui depois de conferir a
# olho 3-5 trades reais (mesmo padrão de validação empírica já usado no
# projeto — nunca confiar de olhos fechados numa métrica nova).
ADERENCIA_SR_ATR_PRECISO = 0.15
ADERENCIA_SR_ATR_PROXIMO = 0.5


def avaliar_aderencia_sr(dias_atras: int | None = None) -> pd.DataFrame:
    """
    Compara, para cada trade com `nivel_sr_referencia` preenchido, o nível
    marcado à mão por Jesué contra o que o motor via no instante do save
    (`snapshot_mercado_json`, capturado por `metricas.montar_snapshot_diario`
    em `registrar_trade`). Responde "o motor via esse nível?" — relatório
    de REVISÃO MANUAL, nunca ajuste automático de peso/lógica (volume de
    dados baixo demais pra isso ser seguro, mesma decisão já tomada pra
    reconciliação MT5).

    Só considera trades com `nivel_sr_referencia` E `snapshot_mercado_json`
    preenchidos — linhas migradas/antigas não têm snapshot e ficam de fora
    automaticamente. `dias_atras`, se informado, restringe por `data_op`
    (mesmo parâmetro de `reconciliar_com_mt5`).
    """
    df = listar_trades()
    if dias_atras is not None:
        limite = (datetime.now() - timedelta(days=dias_atras)).date()
        df = df[df["data_op"].apply(lambda d: d is not None and pd.Timestamp(d).date() >= limite)]

    elegiveis = df[df["nivel_sr_referencia"].notna() & df["snapshot_mercado_json"].notna()]

    linhas = []
    for _, trade in elegiveis.iterrows():
        nivel_ref = float(trade["nivel_sr_referencia"])
        try:
            snapshot = json.loads(trade["snapshot_mercado_json"])
        except (TypeError, ValueError):
            continue

        preco_snapshot = snapshot.get("preco")
        atr = snapshot.get("atr")
        if preco_snapshot is None:
            continue

        lado = "abaixo" if nivel_ref < preco_snapshot else "acima"
        candidatos = snapshot.get(f"niveis_sr_{lado}") or []

        casado = None
        diferenca_atr = None
        if candidatos:
            casado = min(candidatos, key=lambda n: abs((n.get("preco") or 0) - nivel_ref))
            diferenca_pts = abs(casado["preco"] - nivel_ref)
            diferenca_atr = (diferenca_pts / atr) if atr else None

        if diferenca_atr is None:
            classificacao = "nao_visto"
        elif diferenca_atr <= ADERENCIA_SR_ATR_PRECISO:
            classificacao = "visto_preciso"
        elif diferenca_atr <= ADERENCIA_SR_ATR_PROXIMO:
            classificacao = "visto_proximo"
        else:
            classificacao = "nao_visto"

        linhas.append({
            "id_trade": trade["id_trade"], "data_op": trade["data_op"], "ativo": trade["ativo"],
            "nivel_sr_referencia": nivel_ref, "nivel_sr_tipo_informado": trade.get("nivel_sr_tipo"),
            "lado_geometrico": lado,
            "nivel_motor_preco": casado.get("preco") if casado else None,
            "nivel_motor_rank": casado.get("rank") if casado else None,
            "diferenca_atr": diferenca_atr,
            "forte": casado.get("forte") if casado else None,
            "status": casado.get("status") if casado else None,
            "prob_bounce": casado.get("prob_bounce") if casado else None,
            "rompimento_hoje": casado.get("rompimento_hoje") if casado else None,
            "classificacao": classificacao,
        })

    resultado = pd.DataFrame(linhas, columns=COLUNAS_ADERENCIA_SR)
    persistencia.salvar_aderencia_sr(resultado)
    return resultado


# Idem — recomputável via `detectar_padroes_comportamentais`, não é
# histórico do trade.
COLUNAS_PADROES_COMPORTAMENTAIS = [
    "tipo", "id_trade", "id_trade_anterior", "data_op", "ativo", "precisao", "detalhe",
]

# Janelas de `detectar_padroes_comportamentais` — primeiro palpite, não
# calibrado, mesmo espírito de `ADERENCIA_SR_ATR_*`/`SCORE_*`: ajustar
# depois que Jesué conferir os casos reais.
REVENGE_JANELA_MINUTOS = 30
TILT_MIN_TRADES = 3
TILT_JANELA_MINUTOS = 60


def _num_ou_zero(v) -> float:
    return 0.0 if v is None or (isinstance(v, float) and pd.isna(v)) else float(v)


def _avaliar_janela_temporal(anterior: dict, atual: dict, janela_minutos: int) -> tuple[str, str] | None:
    """
    Decide se `atual` está "logo depois" de `anterior` (mesmo ativo, já em
    ordem cronológica) — usa `datetime_entrada` real se ambos tiverem
    (precisão 'minuto', dentro de `janela_minutos`), senão cai pro proxy de
    dia (precisão 'dia', só `data_op` igual — a granularidade disponível
    hoje, já que nenhum trade real tem horário preciso ainda). Retorna
    `(precisao, descrição)` ou `None` se não estiver dentro da janela.
    """
    dt_ant, dt_atual = anterior.get("datetime_entrada"), atual.get("datetime_entrada")
    if pd.notna(dt_ant) and pd.notna(dt_atual):
        delta_min = (pd.Timestamp(dt_atual) - pd.Timestamp(dt_ant)).total_seconds() / 60
        if 0 <= delta_min <= janela_minutos:
            return "minuto", f"{delta_min:.0f} min depois"
        return None
    if anterior.get("data_op") == atual.get("data_op"):
        return "dia", "no mesmo dia"
    return None


def detectar_padroes_comportamentais(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Detecta revenge trading e tilt cascade nos trades reais, por ativo, em
    ordem cronológica (`data_op` + `id_trade` como desempate — assume que a
    ordem de `id_trade` reflete a ordem real dentro do dia, o que vale para
    a migração do xlsm por ter processado as linhas em ordem; não é
    garantia absoluta, só a melhor aproximação disponível sem horário real).

    Relatório de REVISÃO MANUAL, mesmo espírito de `avaliar_aderencia_sr`/
    `reconciliar_com_mt5` — Jesué julga, nada se ajusta sozinho.

    Cada comparação decide sozinha a precisão via `_avaliar_janela_
    temporal`: usa minutos reais quando ambos os trades comparados têm
    `datetime_entrada` (hoje, nenhum tem — só a aba "Posição", sincronizada
    com o MT5, vai gerar isso daqui pra frente), senão cai pro proxy de dia
    (a granularidade real disponível agora). Não são dois sistemas
    separados — o mesmo código já funciona nos dois casos.

    FOMO entries NÃO é detectado aqui (ver plano da Fase 4): exige cruzar
    horário exato de entrada com movimento de preço, sem proxy de dia que
    faça sentido, e hoje não há nenhum trade com horário real pra validar.
    """
    if df is None:
        df = listar_trades()
    reais = df[~df["is_simulated"].astype(bool)].copy()

    linhas = []
    for ativo, grupo in reais.groupby("ativo", sort=False):
        trades = grupo.sort_values(["data_op", "id_trade"]).to_dict("records")

        # --- Revenge trading: trade M vs. o trade IMEDIATAMENTE anterior N
        # do mesmo ativo — reage à perda mais recente, não a uma perda
        # antiga qualquer.
        for i in range(1, len(trades)):
            n_trade, m_trade = trades[i - 1], trades[i]
            saldo_n = n_trade["saldo_operacao_reais"]
            if pd.isna(saldo_n) or saldo_n >= 0:
                continue
            lote_n, lote_m = _num_ou_zero(n_trade.get("lote")), _num_ou_zero(m_trade.get("lote"))
            if not (lote_m > lote_n):
                continue
            janela = _avaliar_janela_temporal(n_trade, m_trade, REVENGE_JANELA_MINUTOS)
            if janela is None:
                continue
            precisao, quando = janela
            linhas.append({
                "tipo": "revenge_trading", "id_trade": m_trade["id_trade"],
                "id_trade_anterior": n_trade["id_trade"], "data_op": m_trade["data_op"],
                "ativo": ativo, "precisao": precisao,
                "detalhe": (f"lote subiu de {lote_n:g} para {lote_m:g} {quando} após perda "
                            f"de R$ {saldo_n:+.2f} (trade #{n_trade['id_trade']})"),
            })

        # --- Tilt cascade: sequências de TILT_MIN_TRADES+ trades
        # consecutivos, começando numa perda, com lote NUNCA decrescendo E
        # crescendo de verdade em algum ponto (`houve_escalada`) — só
        # "não-decrescente" não basta: com lote=1 constante em TODO o
        # histórico real (nenhum trade jamais variou tamanho), essa
        # condição sozinha marcava qualquer dia de 3+ trades começando em
        # perda como "tilt", sem nenhuma escalada de verdade. "Tamanho
        # crescente" é a assinatura que o benchmark descreve — exigir isso
        # de verdade é o que distingue tilt de um dia comum com várias
        # entradas.
        i = 0
        while i < len(trades):
            if pd.isna(trades[i]["saldo_operacao_reais"]) or trades[i]["saldo_operacao_reais"] >= 0:
                i += 1
                continue
            sequencia = [trades[i]]
            precisoes_seq = []
            houve_escalada = False
            j = i + 1
            while j < len(trades):
                anterior, atual = sequencia[-1], trades[j]
                lote_ant = _num_ou_zero(anterior.get("lote"))
                lote_atual = _num_ou_zero(atual.get("lote"))
                if lote_atual < lote_ant:
                    break
                if lote_atual > lote_ant:
                    houve_escalada = True
                janela = _avaliar_janela_temporal(anterior, atual, TILT_JANELA_MINUTOS)
                if janela is None:
                    break
                precisoes_seq.append(janela[0])
                sequencia.append(atual)
                j += 1
            if len(sequencia) >= TILT_MIN_TRADES and houve_escalada:
                precisao = "minuto" if all(p == "minuto" for p in precisoes_seq) else "dia"
                ids_str = ", ".join(f"#{t['id_trade']}" for t in sequencia)
                lotes_str = "→".join(f"{_num_ou_zero(t.get('lote')):g}" for t in sequencia)
                linhas.append({
                    "tipo": "tilt_cascade", "id_trade": sequencia[-1]["id_trade"],
                    "id_trade_anterior": sequencia[0]["id_trade"], "data_op": sequencia[-1]["data_op"],
                    "ativo": ativo, "precisao": precisao,
                    "detalhe": (f"{len(sequencia)} trades em sequência ({ids_str}), lote {lotes_str}, "
                                f"iniciada em perda de R$ {sequencia[0]['saldo_operacao_reais']:+.2f}"),
                })
                i = j
            else:
                i += 1

    resultado = pd.DataFrame(linhas, columns=COLUNAS_PADROES_COMPORTAMENTAIS)
    persistencia.salvar_padroes_comportamentais(resultado)
    return resultado


def resumo_recente_para_ia(ativo: str, n: int = 5) -> str:
    """
    Bloco terso com as últimas `n` operações REAIS (não simuladas) do
    ativo, no mesmo estilo "linha objetiva" já usado por
    `ai_analyst.montar_contexto` — dá ao Mentor IA consciência de como
    Jesué tem operado ultimamente. Sem nenhuma lógica de correlação com o
    motor de S/R (isso é Fase 2/F do plano, deliberadamente fora de
    escopo aqui). Retorna string vazia se não houver trades ainda (diário
    recém-criado, ou nenhuma operação real deste ativo) — o chamador
    (`ai_analyst._montar_prompt`) simplesmente omite o bloco nesse caso.
    """
    df = listar_trades(ativo=ativo, is_simulated=False)
    if df.empty:
        return ""

    recentes = df.sort_values("data_op", ascending=False).head(n)
    linhas = []
    for _, trade in recentes.iterrows():
        tags = trade["estrategia_tags"]
        tags_str = ", ".join(tags) if hasattr(tags, "__iter__") and len(tags) else "—"
        saldo = trade["saldo_operacao_reais"]
        saldo_str = f"R$ {float(saldo):+.2f}" if pd.notna(saldo) else "—"
        linhas.append(
            f"- {trade['data_op']} | {trade.get('tipo_operacao') or '—'} | "
            f"{tags_str} | saída: {trade.get('tipo_saida') or '—'} | {saldo_str}"
        )
    return "ÚLTIMAS OPERAÇÕES REGISTRADAS NO DIÁRIO:\n" + "\n".join(linhas)


# =============================================================================
# POSIÇÕES ABERTAS — rascunho ao vivo do Diário (ver plano em
# C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md)
# =============================================================================
# Tabela SEPARADA de `diario_trades.parquet` (`posicoes_abertas.parquet`,
# ver persistencia.py) — uma linha por posição MT5 atualmente aberta ou
# recém-fechada aguardando confirmação. Ao contrário do diário principal,
# ESTA tabela é deliberadamente mutável: o dashboard principal escreve os
# campos vindos do MT5 a cada ciclo de sincronização, e o próprio usuário
# (via UI do Diário) vai escrevendo notas/tags/imagens enquanto acompanha
# a operação — inclusive depois que ela fecha, até decidir "Finalizar".
# Nada aqui grava em `diario_trades.parquet` diretamente; só
# `finalizar_posicao` faz a ponte, chamando `registrar_trade()` já
# existente, sem alterá-lo.

STATUS_POSICAO_ABERTA = "aberta"
STATUS_POSICAO_FECHADA_PENDENTE = "fechada_pendente"

COLUNAS_POSICOES_ABERTAS = [
    "id_posicao", "ativo", "contrato", "tipo_operacao", "volume",
    "preco_entrada", "datetime_entrada",
    "preco_atual", "pnl_pontos", "pnl_reais", "sl", "tp",
    "status",
    "preco_saida", "datetime_saida", "pnl_pontos_final", "pnl_reais_final",
    "notas_cronologicas", "imagens_em_andamento",
    "estrategia_tags_preliminar", "sala_analise_preliminar",
    "alvo_pontos_preliminar", "stop_pontos_preliminar",
    "nivel_sr_referencia_preliminar",
    "criado_em", "atualizado_em",
]


def _pnl_pontos(tipo_operacao: str, preco_entrada: float, preco_ref: float) -> float:
    """Pontos ganhos/perdidos com o sinal certo conforme a direção — long:
    preço subiu = positivo; short: preço caiu = positivo."""
    bruto = preco_ref - preco_entrada
    return bruto if tipo_operacao == "long" else -bruto


def _salvar_imagem_rascunho(conteudo_bytes: bytes, id_posicao: int, sequencia: int) -> str:
    """
    Salva um print da galeria de uma posição AINDA em rascunho — ao
    contrário de `salvar_imagem` (que assume 1 trade = 1 imagem, nome fixo
    por `id_trade`), aqui pode haver VÁRIOS prints pra mesma posição ao
    longo da operação, então `sequencia` entra no nome pra nunca um
    sobrescrever o outro. Mesmo redimensionamento de `salvar_imagem`,
    função separada de propósito (regra de nomeação diferente, e evita
    qualquer risco de colisão com mudanças em `salvar_imagem` feitas em
    paralelo por outra sessão).
    """
    cfg.PASTA_DIARIO_IMAGENS.mkdir(parents=True, exist_ok=True)
    imagem = Image.open(io.BytesIO(conteudo_bytes)).convert("RGB")
    if imagem.width > cfg.DIARIO_IMAGEM_LARGURA_MAX:
        nova_altura = int(imagem.height * cfg.DIARIO_IMAGEM_LARGURA_MAX / imagem.width)
        imagem = imagem.resize((cfg.DIARIO_IMAGEM_LARGURA_MAX, nova_altura), Image.LANCZOS)
    nome_arquivo = f"rascunho_{id_posicao}_{sequencia:02d}.png"
    imagem.save(cfg.PASTA_DIARIO_IMAGENS / nome_arquivo, format="PNG")
    return nome_arquivo


def _para_lista(valor) -> list:
    """`notas_cronologicas`/`imagens_em_andamento` voltam do parquet como
    numpy array (mesma pegadinha já documentada em `listar_trades` sobre
    `estrategia_tags`), ou `None` se a coluna nunca foi tocada — normaliza
    pra list Python de verdade antes de fazer `.append`."""
    if valor is None or isinstance(valor, str):
        return []
    return list(valor) if hasattr(valor, "__iter__") else []


def sincronizar_posicoes_abertas(df_posicoes_mt5: pd.DataFrame | None) -> pd.DataFrame | None:
    """
    Diff entre as posições REALMENTE abertas agora no MT5
    (`mt5_loader.buscar_posicoes_abertas()`) e a tabela de rascunhos salva:
    ticket novo -> cria linha (`status='aberta'`); ticket que já era
    rastreado e continua aberto -> atualiza só os campos vindos do MT5
    (preço atual, pnl, sl/tp), nunca mexe nas notas/tags/imagens do
    usuário; ticket que sumiu -> resolve o fechamento real via
    `mt5_loader.buscar_execucoes_periodo`/`agrupar_posicoes` (últimas 24h)
    e marca `status='fechada_pendente'`.

    Não bloqueante (`persistencia.lock_posicoes_abertas()`): se outro
    processo está escrevendo, pula este ciclo em silêncio — isso roda num
    timer automático, não por ação do usuário, mesma filosofia do lock de
    atualização de candle. Retorna a tabela atualizada, ou None se pulou o
    ciclo por trava ocupada.
    """
    with persistencia.lock_posicoes_abertas() as adquirida:
        if not adquirida:
            return None

        df_atual = persistencia.carregar_posicoes_abertas()
        if df_atual is None:
            df_atual = pd.DataFrame(columns=COLUNAS_POSICOES_ABERTAS)

        linhas_por_id = {
            int(linha["id_posicao"]): linha.to_dict() for _, linha in df_atual.iterrows()
        }
        tickets_rastreados = set(linhas_por_id.keys())
        tickets_mt5 = set(int(t) for t in df_posicoes_mt5["ticket"]) if df_posicoes_mt5 is not None and not df_posicoes_mt5.empty else set()

        agora = datetime.now()

        if df_posicoes_mt5 is not None:
            for _, pos in df_posicoes_mt5.iterrows():
                ticket = int(pos["ticket"])
                pnl_pts = _pnl_pontos(pos["tipo_operacao"], pos["preco_entrada"], pos["preco_atual"])
                valor_ponto = cfg.VALOR_PONTO.get(pos["ativo"], 0.0)
                pnl_reais = float(pos["profit"]) if pd.notna(pos["profit"]) else pnl_pts * valor_ponto * pos["volume"]

                if ticket in linhas_por_id:
                    linha = linhas_por_id[ticket]
                    linha.update({
                        "preco_atual": pos["preco_atual"], "pnl_pontos": pnl_pts,
                        "pnl_reais": pnl_reais, "sl": pos["sl"], "tp": pos["tp"],
                        "atualizado_em": agora,
                    })
                else:
                    novo = {coluna: None for coluna in COLUNAS_POSICOES_ABERTAS}
                    novo.update({
                        "id_posicao": ticket, "ativo": pos["ativo"], "contrato": pos["contrato"],
                        "tipo_operacao": pos["tipo_operacao"], "volume": pos["volume"],
                        "preco_entrada": pos["preco_entrada"], "datetime_entrada": pos["datetime_entrada"],
                        "preco_atual": pos["preco_atual"], "pnl_pontos": pnl_pts, "pnl_reais": pnl_reais,
                        "sl": pos["sl"], "tp": pos["tp"],
                        "status": STATUS_POSICAO_ABERTA,
                        "notas_cronologicas": [], "imagens_em_andamento": [],
                        "criado_em": agora, "atualizado_em": agora,
                    })
                    linhas_por_id[ticket] = novo

        tickets_fechados_agora = [
            tid for tid in tickets_rastreados
            if tid not in tickets_mt5 and linhas_por_id[tid]["status"] == STATUS_POSICAO_ABERTA
        ]
        if tickets_fechados_agora:
            inicio = agora - timedelta(hours=24)
            df_deals = mt5_loader.buscar_execucoes_periodo(inicio, agora)
            posicoes_fechadas = (
                mt5_loader.agrupar_posicoes(df_deals) if df_deals is not None
                else pd.DataFrame(columns=["position_id"])
            )
            for tid in tickets_fechados_agora:
                linha = linhas_por_id[tid]
                fechamento = posicoes_fechadas[posicoes_fechadas["position_id"] == tid] if not posicoes_fechadas.empty else posicoes_fechadas
                if not fechamento.empty:
                    f = fechamento.iloc[0]
                    pnl_pts_final = _pnl_pontos(linha["tipo_operacao"], linha["preco_entrada"], f["preco_saida"])
                    linha.update({
                        "preco_saida": f["preco_saida"], "datetime_saida": f["datetime_saida"],
                        "pnl_pontos_final": pnl_pts_final, "pnl_reais_final": float(f["profit_total"]),
                    })
                else:
                    # Não achou o fechamento nas últimas 24h (raro) — usa o
                    # último snapshot ao vivo como estimativa; o usuário ainda
                    # revisa tudo antes de finalizar, não é dado "cego".
                    linha.update({
                        "preco_saida": linha["preco_atual"], "datetime_saida": agora,
                        "pnl_pontos_final": linha["pnl_pontos"], "pnl_reais_final": linha["pnl_reais"],
                    })
                linha["status"] = STATUS_POSICAO_FECHADA_PENDENTE
                linha["atualizado_em"] = agora

        df_final = pd.DataFrame(list(linhas_por_id.values()), columns=COLUNAS_POSICOES_ABERTAS)
        persistencia.salvar_posicoes_abertas(df_final)
        return df_final


def listar_posicoes_abertas() -> pd.DataFrame:
    """Carrega a tabela de posições abertas/rascunhos. DataFrame vazio
    (colunas garantidas) se nada foi sincronizado ainda."""
    df = persistencia.carregar_posicoes_abertas()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_POSICOES_ABERTAS)
    return df


def atualizar_rascunho(
    id_posicao: int,
    nota_texto: str | None = None,
    campos: dict | None = None,
    imagem_bytes: bytes | None = None,
) -> bool:
    """
    Único ponto de edição "em andamento" — só mexe em
    `posicoes_abertas.parquet`, nunca em `diario_trades.parquet`. Funciona
    igual com a posição ainda `status='aberta'` ou já `'fechada_pendente'`
    (só `finalizar_posicao` encerra o rascunho de vez) — é assim que a
    análise que o Jesué faz DURANTE e DEPOIS do trade (antes de finalizar)
    fica coberta.

    - `nota_texto`: SEMPRE um `append` em `notas_cronologicas` (nunca
      sobrescreve as notas anteriores) — o "diário de bordo" da própria
      operação.
    - `campos`: atualiza in-place os campos preliminares (chaves aceitas:
      `estrategia_tags`, `sala_analise`, `alvo_pontos`, `stop_pontos`,
      `nivel_sr_referencia` — vira `<chave>_preliminar` automaticamente).
    - `imagem_bytes`: salva via `_salvar_imagem_rascunho` e ANEXA o nome do
      arquivo em `imagens_em_andamento` (galeria, não substitui).

    Retorna False se a trava estava ocupada (chamador — botão de salvar na
    UI do Diário — deve mostrar erro/retry visível) ou se `id_posicao` não
    existe na tabela.
    """
    with persistencia.lock_posicoes_abertas() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty or id_posicao not in set(df["id_posicao"]):
            return False

        idx = df.index[df["id_posicao"] == id_posicao][0]
        agora = datetime.now()

        if nota_texto:
            notas = _para_lista(df.at[idx, "notas_cronologicas"])
            notas.append({"hora": agora.isoformat(), "texto": nota_texto})
            df.at[idx, "notas_cronologicas"] = notas

        if campos:
            for chave, valor in campos.items():
                coluna = chave if chave.endswith("_preliminar") else f"{chave}_preliminar"
                if coluna in COLUNAS_POSICOES_ABERTAS:
                    df.at[idx, coluna] = valor

        if imagem_bytes is not None:
            imagens = _para_lista(df.at[idx, "imagens_em_andamento"])
            nome_arquivo = _salvar_imagem_rascunho(imagem_bytes, id_posicao, len(imagens) + 1)
            imagens.append(nome_arquivo)
            df.at[idx, "imagens_em_andamento"] = imagens

        df.at[idx, "atualizado_em"] = agora
        persistencia.salvar_posicoes_abertas(df)
        return True


def finalizar_posicao(
    id_posicao: int,
    campos_classificacao: dict,
    imagem_principal: str | None = None,
) -> int | None:
    """
    Encerra um rascunho de posição e grava o trade definitivo em
    `diario_trades.parquet` via `registrar_trade()` (reaproveitado, SEM
    nenhuma alteração) — o rascunho nunca é escrito lá diretamente, só
    serve de origem dos dados pra montar `campos`.

    `campos_classificacao` é o mesmo dict que o formulário "Novo Trade" já
    monta hoje (`estrategia_tags`, `sala_analise`, `tipo_saida`,
    `tags_setup_erro`, etc.) — os valores `*_preliminar` do rascunho
    servem só de DEFAULT (pré-preenchimento do lado da UI); esta função
    recebe o que o usuário confirmou, não decide sozinha. Os campos vindos
    do MT5 (ativo/contrato/tipo_operacao/lote/preços/datas/resultado) são
    injetados aqui a partir do rascunho — o chamador não precisa repassá-los.

    `imagem_principal`, se informado, deve ser um dos nomes já presentes em
    `imagens_em_andamento` — vira a `imagem_arquivo` oficial do trade
    (coluna existente, inalterada). As demais imagens da galeria ficam em
    `imagens_adicionais` (coluna nova, aditiva) e o log cronológico de
    notas é concatenado em `obs`.

    Retorna o `id_trade` criado, ou None se a trava do rascunho estiver
    ocupada, se `id_posicao` não existir, ou se `registrar_trade` falhar
    (trava do diário ocupada) — nesses casos o rascunho é preservado
    intacto, nunca perdido.
    """
    with persistencia.lock_posicoes_abertas() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty or id_posicao not in set(df["id_posicao"]):
            return None
        linha = df[df["id_posicao"] == id_posicao].iloc[0].to_dict()

        notas = _para_lista(linha.get("notas_cronologicas"))
        narrativa = "\n".join(f"[{n['hora']}] {n['texto']}" for n in notas) or None

        imagens_galeria = _para_lista(linha.get("imagens_em_andamento"))
        imagens_restantes = [img for img in imagens_galeria if img != imagem_principal]

        # Defaults vindos do rascunho (`*_preliminar`, preenchidos ao longo da
        # operação) — `campos_classificacao` sobrescreve por cima com o que o
        # usuário confirmou/ajustou nesse último passo, mas se ele não
        # mencionar de novo uma chave (ex: só ajustou `tipo_saida`), o valor
        # preliminar já escrito durante a operação não se perde.
        campos = {
            "estrategia_tags": _para_lista(linha.get("estrategia_tags_preliminar")),
            "sala_analise": linha.get("sala_analise_preliminar"),
            "alvo_pontos": linha.get("alvo_pontos_preliminar"),
            "stop_pontos": linha.get("stop_pontos_preliminar"),
            "nivel_sr_referencia": linha.get("nivel_sr_referencia_preliminar"),
            "ativo": linha["ativo"], "contrato": linha["contrato"],
            "tipo_operacao": linha["tipo_operacao"], "lote": linha["volume"],
            "preco_entrada": linha["preco_entrada"], "datetime_entrada": linha["datetime_entrada"],
            "preco_saida": linha["preco_saida"], "datetime_saida": linha["datetime_saida"],
            "resultado_pontos": linha["pnl_pontos_final"], "saldo_operacao_reais": linha["pnl_reais_final"],
            "data_op": pd.Timestamp(linha["datetime_entrada"]).date(),
            "obs": narrativa,
            "imagens_adicionais": imagens_restantes,
        }
        campos.update(campos_classificacao)  # estrategia_tags/sala_analise/tipo_saida/etc. — decisão do usuário

        imagem_bytes = None
        if imagem_principal:
            caminho_img = cfg.PASTA_DIARIO_IMAGENS / imagem_principal
            if caminho_img.exists():
                imagem_bytes = caminho_img.read_bytes()

    # `registrar_trade` abre sua PRÓPRIA trava (`lock_diario`, arquivo
    # diferente de `lock_posicoes_abertas`) — chamado fora do `with` acima
    # de propósito, pra nunca segurar as duas travas ao mesmo tempo (risco
    # de deadlock cruzado se outro processo pedir na ordem inversa).
    id_trade = registrar_trade(campos, imagem_bytes=imagem_bytes)
    if id_trade is None:
        return None  # trava do diário ocupada — rascunho preservado, usuário tenta de novo

    with persistencia.lock_posicoes_abertas() as adquirida:
        if not adquirida:
            return id_trade  # trade já gravado; remoção do rascunho fica pro próximo ciclo/tentativa
        df = persistencia.carregar_posicoes_abertas()
        if df is not None and not df.empty:
            df_restante = df[df["id_posicao"] != id_posicao]
            persistencia.salvar_posicoes_abertas(df_restante)
    return id_trade


# =============================================================================
# ORDENS PENDENTES (ainda não executadas) — mesma seção "Posição" do Diário
# =============================================================================
# Ao contrário de `posicoes_abertas`, aqui não há nada pra o usuário editar
# (nota/tags/imagem) — é puramente informativo até a ordem executar (vira
# posição, capturada por `sincronizar_posicoes_abertas`) ou ser cancelada.
# Por isso o sync é um simples "substitui tudo" a cada ciclo, sem diff
# nem preservar campo nenhum entre chamadas.

COLUNAS_ORDENS_PENDENTES = [
    "ticket", "ativo", "contrato", "tipo_ordem", "volume",
    "preco_pedido", "sl", "tp", "datetime_criacao", "comment",
]


def sincronizar_ordens_pendentes(df_ordens_mt5: pd.DataFrame | None) -> None:
    """Substitui `ordens_pendentes.parquet` pelo snapshot atual de
    `mt5_loader.buscar_ordens_pendentes()`. `None` (falha de MT5) não apaga
    o que já existe — só um DataFrame vazio de verdade (sem ordens agora)
    zera a tabela."""
    if df_ordens_mt5 is None:
        return
    persistencia.salvar_ordens_pendentes(df_ordens_mt5[COLUNAS_ORDENS_PENDENTES] if not df_ordens_mt5.empty
                                          else pd.DataFrame(columns=COLUNAS_ORDENS_PENDENTES))


def listar_ordens_pendentes() -> pd.DataFrame:
    """Carrega a tabela de ordens pendentes. DataFrame vazio (colunas
    garantidas) se nada foi sincronizado ainda."""
    df = persistencia.carregar_ordens_pendentes()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_ORDENS_PENDENTES)
    return df
