# =============================================================================
# correlacao_ativos.py — DI1FUT × WINFUT × WDOFUT: leitura de direcionamento
# =============================================================================
# 2026-07-15: primeira versão — substitui o botão "WIN × WDO" do hero (puro
# placeholder, nunca calculou nada) por um velocímetro de correlação de
# Pearson WIN×WDO + um painel manual de ativos correlacionados (2 vencimentos
# de DI + DOLFUT + INDFUT).
#
# 2026-07-16 (Jesué, a partir de uso real): simplificado pra só 3 ativos —
# DI1FUT, WINFUT, WDOFUT (ver `config_backtest.SIMBOLO_DI1FUT`) — e a leitura
# de correlação deixou de ser só o Pearson bruto WIN×WDO isolado pra virar a
# verificação real da tese de mercado do Jesué: "o DI segue o WDO e é
# inverso ao WIN; quanto mais próximo dessa configuração, mais
# correlacionados os ativos estão e mais claro é o direcionamento pra
# operar". Ver `calcular_correlacao_di` abaixo — o velocímetro do hero
# mostra esse alinhamento composto agora, não mais o Pearson WIN×WDO
# isolado (esse número sozinho não capturava a tese, que é sobre os 3
# ativos juntos).
# =============================================================================

import pandas as pd

import config_backtest as cfg
import mt5_loader


def _fechamentos_diarios(df: pd.DataFrame) -> pd.Series:
    """Resample de candles M5 pra fechamento diário — mesmo cálculo de
    `indice_medo_ganancia._fechamentos_diarios`, duplicado aqui (1 linha)
    pra não acoplar aos internos privados daquele módulo."""
    return df.set_index('datetime')['close'].resample('D').last().dropna()


def _classificar_alinhamento(valor: float) -> tuple[str, str]:
    """Faixas de leitura do alinhamento DI×WDO×WIN. Diferente de uma
    correlação pura (onde não existe polo "certo" — força e direção, só
    isso), aqui EXISTE uma configuração esperada pela tese do Jesué: DI
    acompanha WDO (correlação positiva) e é inverso ao WIN (correlação
    negativa). Por isso perto de +1 é deliberadamente "melhor" (leitura de
    mercado mais confiável pra operar), não é só uma questão de força —
    perto de -1 significa que os 3 ativos estão se comportando ao
    contrário do esperado (sinal de que o contexto não está claro hoje).
    `cor` é uma chave de `estilo_win98.C`/paleta do dashboard clássico."""
    if valor >= 0.6:
        return 'Direcionamento Claro', 'green'
    if valor >= 0.2:
        return 'Direcionamento Parcial', 'yellow'
    if valor > -0.2:
        return 'Configuração Neutra', 'text2'
    return 'Configuração Invertida', 'red'


def calcular_correlacao_di(df_win: pd.DataFrame | None, df_wdo: pd.DataFrame | None,
                            fechamentos_di: pd.Series | None, janela_dias: int = None) -> dict | None:
    """
    Verifica a tese de mercado do Jesué: DI1FUT deveria correlacionar
    POSITIVO com WDO (juro futuro sobe junto com o dólar, típico de fuga
    pra segurança) e NEGATIVO com WIN (juro alto pressiona bolsa pra
    baixo). `alinhamento = (corr_di_wdo - corr_di_win) / 2` vai de -1
    (configuração invertida: DI cai com WDO e sobe com WIN) a +1
    (configuração clássica perfeita) — é essa métrica composta que o
    velocímetro do hero mostra. Os dois números isolados (`corr_di_wdo`/
    `corr_di_win`) também são retornados, pra nunca virar black box.

    Retorna `None` se não houver histórico suficiente em qualquer uma das
    3 séries — nunca inventa um valor sem base estatística (mesma doutrina
    de `indice_medo_ganancia._zscore_para_0_100`).
    """
    janela_dias = janela_dias or cfg.JANELA_CORRELACAO_DIAS
    if df_win is None or df_win.empty or df_wdo is None or df_wdo.empty:
        return None
    if fechamentos_di is None or len(fechamentos_di) < 2:
        return None

    ret_win = _fechamentos_diarios(df_win).pct_change().dropna()
    ret_wdo = _fechamentos_diarios(df_wdo).pct_change().dropna()
    ret_di = fechamentos_di.pct_change().dropna()

    # Alinha as 3 séries pelas datas em comum — bases independentes (candle
    # M5 local pra WIN/WDO, D1 direto do MT5 pra DI) podem ter dias
    # faltando diferentes entre si.
    conjunto = pd.concat([
        ret_win.rename('win'), ret_wdo.rename('wdo'), ret_di.rename('di'),
    ], axis=1).dropna().tail(janela_dias)
    if len(conjunto) < 5:
        return None

    corr_di_wdo = conjunto['di'].corr(conjunto['wdo'])
    corr_di_win = conjunto['di'].corr(conjunto['win'])
    if pd.isna(corr_di_wdo) or pd.isna(corr_di_win):
        return None

    alinhamento = (corr_di_wdo - corr_di_win) / 2
    nome, cor = _classificar_alinhamento(alinhamento)
    return {
        'alinhamento': round(float(alinhamento), 3),
        'corr_di_wdo': round(float(corr_di_wdo), 3),
        'corr_di_win': round(float(corr_di_win), 3),
        'nivel_nome': nome, 'cor': cor, 'janela_dias': len(conjunto),
    }


def obter_fechamentos_di(n_dias: int = None) -> pd.Series | None:
    """Histórico de fechamentos diários do DI1FUT (proxy real:
    `cfg.SIMBOLO_DI1FUT`, ver comentário lá) — chamado só no ciclo de 30s
    (`carregar_bases`, nunca no tick de 1s), cacheado em
    `estado['fechamentos_di']` e reaproveitado a cada tick por
    `calcular_correlacao_di` (cálculo puro sobre a série já cacheada, sem
    nova chamada MT5). Pede uma folga de dias extra sobre
    `cfg.JANELA_CORRELACAO_DIAS` porque o alinhamento por data em
    `calcular_correlacao_di` (3 calendários independentes) sempre perde
    algumas linhas."""
    n_dias = n_dias or (cfg.JANELA_CORRELACAO_DIAS + 15)
    return mt5_loader.buscar_fechamentos_diarios(cfg.SIMBOLO_DI1FUT, n_dias)


def obter_cotacoes_correlacionadas(df_win: pd.DataFrame | None = None,
                                    df_wdo: pd.DataFrame | None = None) -> list[dict]:
    """
    Painel 'CORRELACIONADOS' do hero: variação % do dia de DI1FUT, WINFUT e
    WDOFUT lado a lado — chamado só no ciclo de 30s (`carregar_bases`, nunca
    no tick de 1s). WINFUT/WDOFUT vêm das bases já carregadas em memória
    (sem chamada MT5 extra — o dado já existe); só DI1FUT precisa de uma
    chamada MT5 real. Item que falhar é simplesmente omitido — o painel
    degrada mostrando só o que respondeu, nunca quebra.
    """
    resultado = []

    variacao_di = mt5_loader.buscar_variacao_dia(cfg.SIMBOLO_DI1FUT)
    if variacao_di is not None:
        resultado.append({'label': 'DI1FUT', 'ticker': cfg.SIMBOLO_DI1FUT, 'variacao_pct': round(variacao_di, 2)})

    for label, df in (('WINFUT', df_win), ('WDOFUT', df_wdo)):
        if df is None or df.empty:
            continue
        fech = _fechamentos_diarios(df)
        if len(fech) < 2:
            continue
        anterior, atual = float(fech.iloc[-2]), float(fech.iloc[-1])
        if anterior == 0:
            continue
        resultado.append({'label': label, 'ticker': label, 'variacao_pct': round((atual - anterior) / anterior * 100, 2)})

    return resultado
