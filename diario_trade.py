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


def registrar_trade(campos: dict, imagem_bytes: bytes | None = None) -> int | None:
    """
    Atribui `id_trade` sequencial e grava um trade novo no diário. Retorna o
    `id_trade` atribuído, ou None se a trava estava ocupada (chamador —
    tipicamente um callback de botão "Salvar" na UI — deve mostrar um erro/
    retry visível, ao contrário do update de candle que pula em silêncio).

    Se `imagem_bytes` for fornecido, salva a imagem e preenche
    `imagem_arquivo` ANTES de gravar a linha — tudo dentro da mesma trava,
    pra nunca deixar um trade órfão sem imagem (ou vice-versa) se algo falhar
    no meio do caminho.
    """
    with persistencia.lock_diario() as adquirida:
        if not adquirida:
            return None
        df_atual = persistencia.carregar_diario()
        trade = novo_trade(**campos)
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

    evolucao = reais[["data_op", "saldo_operacao_reais"]].copy()
    evolucao["saldo_acumulado"] = evolucao["saldo_operacao_reais"].astype(float).cumsum()

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
        "evolucao_data": evolucao["data_op"].astype(str).tolist(),
        "evolucao_saldo_acumulado": evolucao["saldo_acumulado"].tolist(),
    }


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
