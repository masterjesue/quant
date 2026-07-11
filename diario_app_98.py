# =============================================================================
# diario_app_98.py — Diário de Trade, processo isolado (visual Super Quant '98)
# =============================================================================
# Roda em processo/porta próprios (8052), embutido via <iframe> dentro da
# janela `win-diario` de dashboard_98.py (que continua dona da moldura:
# titlebar, arrastar, minimizar/fechar — só o CONTEÚDO interno vira iframe).
#
# Por quê um processo separado, e não só um callback dentro de dashboard_98.py:
# o dash-renderer paga um custo de reconciliação a cada tick de 1s
# (interval-live) proporcional ao total de componentes Dash montados na
# página — CONFIRMADO empiricamente que isso vale mesmo pra componentes
# escondidos via `display:none` (não é só sobre visibilidade). Nenhuma
# quantidade de lazy-mount dentro da MESMA página elimina esse custo por
# completo, porque qualquer UI do Diário montada ali soma no total. Um
# <iframe> é a única forma de garantir isolamento real: é sempre um
# Window/Document/árvore de reconciliação SEPARADA — o dashboard principal
# nunca precisa percorrer o que está dentro dele, não importa quão pesado o
# Diário fique. Ver `C:\Users\jesue\.claude\plans\t-tulo-super-quant-eager-knuth.md`.
#
# Zero duplicação de LÓGICA DE DADOS: usa os mesmos `diario_trade.py`/
# `persistencia.py`/`metricas.py`/`config_backtest.py` que os dois
# dashboards principais — só a construção de HTML (estilo win98) é própria
# deste arquivo, migrada de dashboard_98.py sem mudança de comportamento,
# com uma exceção: o snapshot de mercado (`salvar_novo_trade_98`) lia
# `estado['bases']` da MEMÓRIA de dashboard_98.py — este processo não
# compartilha memória com aquele, então lê os candles frescos do disco via
# `persistencia.carregar_candles(ativo)` (diferença de até ~30s da leitura
# em memória, irrelevante pra um snapshot no instante do save de um trade).
#
# Ciclo de vida: lançado automaticamente por dashboard_98.py na subida (só
# se a porta 8052 ainda não estiver ocupada — ver `gerenciador_processos.
# garantir_processo_filho`). Se encerra sozinho quando dashboard_98.py
# (porta 8051) para de responder por ~60s — não depende de um sinal de
# desligamento do processo pai, robusto a taskkill/crash (ver
# `gerenciador_processos.iniciar_monitor_processo_pai`).
# =============================================================================

import base64
import calendar
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs
sys.path.insert(0, str(Path(__file__).parent))

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update
import flask
import pandas as pd
import plotly.graph_objects as go

import config_backtest as cfg
import persistencia
import diario_trade as dt
import gerenciador_processos as gp
from metricas import extrair_metricas, montar_snapshot_diario
from estilo_win98 import C, FONTS, win98_button, win98_field, win98_groupbox, win98_sunken

PORTA_PROPRIA = 8052
PORTA_PAI = 8051  # dashboard_98.py

_ABAS_DIARIO98 = [
    ('novo', 'Novo Trade'),
    ('posicao', 'Posição'),
    ('historico', 'Histórico'),
    ('estatisticas', 'Estatísticas'),
    ('reconciliacao', 'Reconciliação'),
    ('aderencia_sr', 'Aderência S/R'),
    ('padroes', 'Padrões'),
]


def _opcoes_diario(valores: list[str]) -> list[dict]:
    """Converte vocabulário canônico (snake_case) em opções de dropdown legíveis."""
    return [{'label': v.replace('_', ' ').title(), 'value': v} for v in valores]


def _campo_diario98(label: str, componente) -> html.Div:
    return html.Div([
        html.Div(label, style={'fontSize': '10px', 'color': C['text2'],
                                'marginBottom': '2px', 'fontFamily': FONTS['display']}),
        componente,
    ], style={'marginBottom': '8px'})


def _construir_diario98_novo_trade() -> html.Div:
    campo = _campo_diario98
    linha = lambda *filhos: html.Div(list(filhos), style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'})

    return html.Div([
        win98_groupbox('Dados da Operação', html.Div([
            linha(
                campo('Data', dcc.Input(id='diario98-data', type='date', style=win98_field())),
                campo('Ativo', dcc.Dropdown(id='diario98-ativo', options=_opcoes_diario(cfg.ATIVOS),
                                             clearable=False, style={'width': '110px'})),
                campo('Janela', dcc.Dropdown(id='diario98-janela',
                                              options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                                              style={'width': '150px'})),
            ),
            linha(
                campo('Estratégia', dcc.Dropdown(id='diario98-estrategia', multi=True,
                                                  options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
                                                  style={'width': '280px'})),
                campo('Sala/Análise', dcc.Dropdown(id='diario98-sala',
                                                     options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
                                                     style={'width': '170px'})),
            ),
            linha(
                campo('Tipo', dcc.Dropdown(id='diario98-tipo-operacao', options=_opcoes_diario(dt.TIPO_OPERACAO),
                                            clearable=False, style={'width': '100px'})),
                campo('Alvo (pts)', dcc.Input(id='diario98-alvo', type='number', style=win98_field())),
                campo('Stop (pts)', dcc.Input(id='diario98-stop', type='number', style=win98_field())),
                campo('Lote', dcc.Input(id='diario98-lote', type='number', value=1, style=win98_field())),
            ),
            linha(
                campo('Preço Entrada', dcc.Input(id='diario98-preco-entrada', type='number', style=win98_field())),
                campo('Preço Saída', dcc.Input(id='diario98-preco-saida', type='number', style=win98_field())),
                campo('Tipo Saída', dcc.Dropdown(id='diario98-tipo-saida', options=_opcoes_diario(dt.TIPO_SAIDA),
                                                  style={'width': '140px'})),
            ),
            linha(
                campo('Resultado (pts)', dcc.Input(id='diario98-resultado', type='number', style=win98_field())),
                campo('Saldo (R$)', dcc.Input(id='diario98-saldo', type='number', style=win98_field())),
            ),
        ])),

        win98_groupbox('Nível de S/R referenciado (opcional)', html.Div([
            linha(
                campo('Preço', dcc.Input(id='diario98-nivel-sr', type='number', style=win98_field())),
                campo('Tipo', dcc.Input(id='diario98-nivel-sr-tipo', type='text', placeholder='ex: suporte',
                                         style=win98_field())),
            ),
        ])),

        win98_groupbox('Anotações', html.Div([
            campo('Resumo Operacional', dcc.Textarea(id='diario98-resumo', rows=3,
                                                       style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            campo('Obs (autocoaching)', dcc.Textarea(id='diario98-obs', rows=2,
                                                       style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            campo('Tags de Setup/Erro', dcc.Dropdown(id='diario98-tags-setup-erro', multi=True,
                                                       options=_opcoes_diario(dt.TAGS_SETUP_ERRO_INICIAIS))),
        ])),

        win98_groupbox('Print', html.Div([
            html.Div('Cole (Ctrl+V) um print ou envie um arquivo:', style={
                'fontSize': '10px', 'color': C['text2'], 'marginBottom': '6px'}),
            dcc.Upload(
                id='diario98-upload-imagem',
                children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '11px'}),
                style={**win98_sunken(bg=C['window']), 'padding': '10px', 'textAlign': 'center', 'cursor': 'pointer'},
            ),
            dcc.Store(id='store-diario98-imagem-colada'),
            html.Div(id='diario98-preview-imagem', style={'marginTop': '8px'}),
        ])),

        html.Div([
            html.Button('Salvar Operação', id='btn-diario98-salvar', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Button('Limpar', id='btn-diario98-limpar', n_clicks=0,
                        className='win98-btn', style=win98_button(extra={'marginLeft': '8px'})),
        ], style={'marginTop': '10px'}),
        html.Div(id='diario98-status', style={'marginTop': '8px', 'fontSize': '11px', 'fontFamily': FONTS['mono']}),
    ])


def _construir_diario98_placeholder(nome: str) -> html.Div:
    return html.Div(f'{nome} — em construção.', style={
        'padding': '30px', 'textAlign': 'center', 'color': C['text2'],
        'fontFamily': FONTS['display'], 'fontSize': '12px',
    })


# --- Em Andamento ---------------------------------------------------------
# Rascunho ao vivo de posições abertas — ver plano em
# C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md.
# Mesma lógica de dados de diario_app_classico.py (dt.listar_posicoes_
# abertas/atualizar_rascunho/finalizar_posicao) — só a construção de HTML
# (estilo win98) é própria deste arquivo. Primeiro uso de ID por padrão-
# matching neste arquivo (`{'type': 'ea98-...', 'index': id_posicao}`):
# necessário porque o número de posições abertas varia.

def _lista_segura_98(valor) -> list:
    if valor is None or isinstance(valor, str):
        return []
    return list(valor) if hasattr(valor, '__iter__') else []


def _fmt_num_98(valor, sinal=False, casas=0) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return '—'
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return '—'
    formato = f"{{:+.{casas}f}}" if sinal else f"{{:.{casas}f}}"
    return formato.format(v)


def _bloco_finalizar_em_andamento_98(pos: dict, imagens: list) -> html.Div:
    campo = _campo_diario98
    id_pos = pos['id_posicao']
    opcoes_imagem = [{'label': nome, 'value': nome} for nome in imagens]
    return html.Div([
        html.Div('Finalizar operação', style={
            'fontSize': '10px', 'color': C['gold'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'marginTop': '10px', 'marginBottom': '4px',
        }),
        html.Div([
            campo('Tipo de Saída', dcc.Dropdown(id={'type': 'ea98-tipo-saida', 'index': id_pos},
                  options=_opcoes_diario(dt.TIPO_SAIDA), style={'width': '150px'})),
            campo('Tags Setup/Erro', dcc.Dropdown(id={'type': 'ea98-tags-erro', 'index': id_pos}, multi=True,
                  options=_opcoes_diario(dt.TAGS_SETUP_ERRO_INICIAIS), style={'width': '240px'})),
        ] + ([campo('Imagem principal', dcc.Dropdown(id={'type': 'ea98-imagem-principal', 'index': id_pos},
                    options=opcoes_imagem, style={'width': '200px'}))] if opcoes_imagem else []),
            style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
        html.Button('Finalizar e gravar no Diário', id={'type': 'ea98-btn-finalizar', 'index': id_pos},
                    n_clicks=0, className='win98-btn', style=win98_button(extra={'marginTop': '8px'})),
    ])


def _bloco_posicao_em_andamento_98(pos: dict) -> html.Div:
    campo = _campo_diario98
    id_pos = pos['id_posicao']
    cor_direcao = C['green'] if pos.get('tipo_operacao') == 'long' else C['red']
    pendente = pos.get('status') == dt.STATUS_POSICAO_FECHADA_PENDENTE

    notas = _lista_segura_98(pos.get('notas_cronologicas'))
    timeline = html.Div([
        html.Div([
            html.Span(str(n.get('hora', ''))[:16].replace('T', ' '), style={
                'color': C['text2'], 'fontSize': '9px', 'fontFamily': FONTS['mono']}),
            html.Span(' — ' + str(n.get('texto', '')), style={'fontSize': '11px', 'marginLeft': '6px'}),
        ], style={'padding': '2px 0', 'borderBottom': f"1px solid {C['border']}"})
        for n in notas
    ]) if notas else html.Div('Sem notas ainda.', style={'fontSize': '11px', 'color': C['text2']})

    imagens = _lista_segura_98(pos.get('imagens_em_andamento'))
    galeria = html.Div([
        html.Img(src=f'/diario-imagens/{nome}', style={
            'width': '64px', 'height': '42px', 'objectFit': 'cover',
            'border': f"1px solid {C['shadow_dk']}", 'marginRight': '6px', 'marginTop': '6px',
        }) for nome in imagens
    ], style={'display': 'flex', 'flexWrap': 'wrap'}) if imagens else None

    preco_ref = pos.get('preco_saida') if pendente else pos.get('preco_atual')
    label_ref = 'Saída' if pendente else 'Atual'
    pnl_pts = pos.get('pnl_pontos_final') if pendente else pos.get('pnl_pontos')
    pnl_reais = pos.get('pnl_reais_final') if pendente else pos.get('pnl_reais')
    detalhe = html.Div([
        html.Span((pos.get('tipo_operacao') or '—').upper(), style={
            'color': cor_direcao, 'fontSize': '10px', 'fontWeight': '700'}),
        html.Span(' ⚠ FECHADA — AGUARDANDO CONFIRMAÇÃO' if pendente else ' ● ABERTA', style={
            'marginLeft': '6px', 'fontSize': '9px', 'fontWeight': '700',
            'color': C['yellow'] if pendente else C['green']}),
        html.Div(
            f"Entrada {_fmt_num_98(pos.get('preco_entrada'))} · {label_ref} {_fmt_num_98(preco_ref)} · "
            f"Pts {_fmt_num_98(pnl_pts, sinal=True)} · R$ {_fmt_num_98(pnl_reais, sinal=True, casas=2)}",
            style={'fontSize': '10px', 'color': C['text2'], 'fontFamily': FONTS['mono'], 'marginTop': '2px'}
        ),
    ])

    campos_classificacao = html.Div([
        campo('Estratégia', dcc.Dropdown(
            id={'type': 'ea98-estrategia', 'index': id_pos}, multi=True,
            options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
            value=_lista_segura_98(pos.get('estrategia_tags_preliminar')) or None, style={'width': '240px'})),
        campo('Sala/Análise', dcc.Dropdown(
            id={'type': 'ea98-sala', 'index': id_pos}, options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
            value=pos.get('sala_analise_preliminar'), style={'width': '160px'})),
        campo('Alvo (pts)', dcc.Input(id={'type': 'ea98-alvo', 'index': id_pos}, type='number',
              value=pos.get('alvo_pontos_preliminar'), style=win98_field())),
        campo('Stop (pts)', dcc.Input(id={'type': 'ea98-stop', 'index': id_pos}, type='number',
              value=pos.get('stop_pontos_preliminar'), style=win98_field())),
    ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap', 'marginTop': '8px'})

    nota_input = html.Div([
        dcc.Textarea(id={'type': 'ea98-nota', 'index': id_pos}, rows=2,
                     placeholder='Nova anotação (durante ou depois da operação)...',
                     style={**win98_field(), 'height': 'auto', 'resize': 'none'}),
        html.Button('Salvar', id={'type': 'ea98-btn-salvar', 'index': id_pos}, n_clicks=0,
                    className='win98-btn', style=win98_button(extra={'marginTop': '6px'})),
        html.Div(id={'type': 'ea98-status', 'index': id_pos}, style={
            'fontSize': '10px', 'color': C['text2'], 'marginTop': '4px'}),
    ], style={'marginTop': '8px'})

    filhos = [
        detalhe,
        html.Div('Notas:', style={'fontSize': '9px', 'color': C['text2'], 'marginTop': '8px'}),
        timeline,
    ]
    if galeria:
        filhos.append(galeria)
    filhos.append(campos_classificacao)
    filhos.append(nota_input)
    if pendente:
        filhos.append(_bloco_finalizar_em_andamento_98(pos, imagens))

    return win98_groupbox(f"{pos.get('ativo', '—')} · {pos.get('contrato', '—')}", html.Div(filhos))


_TIPO_ORDEM_LABEL_98 = {
    'compra_limitada': 'Compra Limitada', 'venda_limitada': 'Venda Limitada',
    'compra_stop': 'Compra Stop', 'venda_stop': 'Venda Stop',
    'compra_stop_limitada': 'Compra Stop Limitada', 'venda_stop_limitada': 'Venda Stop Limitada',
}


def _secao_ordens_pendentes_98(ordens: pd.DataFrame) -> html.Div | None:
    """Ordens ainda NÃO executadas — puramente informativo, sem nota/tag/
    imagem (mesma razão de diario_app_classico.py::_secao_ordens_pendentes)."""
    if ordens is None or ordens.empty:
        return None
    linhas = []
    for o in ordens.to_dict('records'):
        cor = C['green'] if 'compra' in (o.get('tipo_ordem') or '') else C['red']
        extras = []
        if pd.notna(o.get('sl')):
            extras.append(f"Stop {_fmt_num_98(o['sl'])}")
        if pd.notna(o.get('tp')):
            extras.append(f"Alvo {_fmt_num_98(o['tp'])}")
        linhas.append(html.Div([
            html.Span(f"{o.get('ativo')} · {o.get('contrato')}", style={
                'fontWeight': '700', 'fontFamily': FONTS['display'], 'fontSize': '11px'}),
            html.Span(_TIPO_ORDEM_LABEL_98.get(o.get('tipo_ordem'), o.get('tipo_ordem') or '—'), style={
                'color': cor, 'marginLeft': '6px', 'fontSize': '10px', 'fontWeight': '700'}),
            html.Div(
                f"Preço pedido {_fmt_num_98(o.get('preco_pedido'))} · {o.get('volume')} lote(s)"
                + (' · ' + ' · '.join(extras) if extras else ''),
                style={'fontSize': '10px', 'color': C['text2'], 'fontFamily': FONTS['mono'], 'marginTop': '2px'}
            ),
        ], style={'padding': '6px 0', 'borderBottom': f"1px solid {C['border']}"}))
    return win98_groupbox('Ordens Pendentes (ainda não executadas)', html.Div(linhas))


def _renderizar_em_andamento_98(posicoes: pd.DataFrame, ordens: pd.DataFrame | None = None) -> html.Div:
    secao_ordens = _secao_ordens_pendentes_98(ordens) if ordens is not None else None

    if posicoes is None or posicoes.empty:
        vazio = html.Div('Nenhuma posição aberta ou aguardando confirmação no momento.', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
        return html.Div([vazio, secao_ordens] if secao_ordens else vazio)

    registros = posicoes.to_dict('records')
    blocos = [_bloco_posicao_em_andamento_98(p) for p in registros]

    opcoes_posicao = [
        {'label': f"{p.get('ativo')} · entrada {_fmt_num_98(p.get('preco_entrada'))} · "
                  f"{'aguardando confirmação' if p.get('status') == dt.STATUS_POSICAO_FECHADA_PENDENTE else 'aberta'}",
         'value': p['id_posicao']}
        for p in registros
    ]
    anexar = win98_groupbox('Anexar print (cole com Ctrl+V ou envie um arquivo)', html.Div([
        _campo_diario98('Posição', dcc.Dropdown(id='em-andamento98-select-posicao', options=opcoes_posicao,
                                                  style={'width': '280px'})),
        dcc.Upload(
            id='em-andamento98-upload-imagem',
            children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '11px'}),
            style={**win98_sunken(bg=C['window']), 'padding': '10px', 'textAlign': 'center',
                   'cursor': 'pointer', 'marginTop': '6px'},
        ),
        html.Div(id='em-andamento98-preview-imagem', style={'marginTop': '6px'}),
        html.Button('Anexar à posição selecionada', id='btn-em-andamento98-anexar', n_clicks=0,
                    className='win98-btn', style=win98_button(extra={'marginTop': '8px'})),
        html.Div(id='em-andamento98-anexar-status', style={'marginTop': '6px', 'fontSize': '10px', 'color': C['text2']}),
    ]))

    filhos = blocos + [anexar]
    if secao_ordens:
        filhos.append(secao_ordens)
    return html.Div(filhos)


def _construir_diario98_em_andamento() -> html.Div:
    return html.Div([
        dcc.Interval(id='interval-em-andamento98', interval=6_000, n_intervals=0),
        html.Div(id='em-andamento98-conteudo'),
        # Reaproveita o MESMO id/JS global de colagem (assets_98/diario_paste.js)
        # do Novo Trade — seguro porque as abas nunca ficam montadas ao mesmo
        # tempo (`montar_aba_diario98` troca o conteúdo inteiro).
        dcc.Store(id='store-diario98-imagem-colada'),
    ])


# --- Histórico ---------------------------------------------------------------

def _td_diario98(conteudo, extra=None) -> html.Td:
    estilo = {'padding': '4px 8px', 'fontSize': '11px', 'fontFamily': FONTS['mono'],
              'borderBottom': f"1px solid {C['border']}"}
    if extra:
        estilo.update(extra)
    return html.Td(conteudo, style=estilo)


def _linha_historico_98(trade: pd.Series) -> html.Tr:
    tags = trade['estrategia_tags']
    tags_str = ', '.join(tags) if hasattr(tags, '__iter__') else ''
    saldo = trade['saldo_operacao_reais']
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0
    cor_saldo = C['green'] if saldo_num > 0 else (C['red'] if saldo_num < 0 else C['text2'])
    thumb = (
        html.Img(src=f"/diario-imagens/{trade['imagem_arquivo']}", style={
            'width': '48px', 'height': '32px', 'objectFit': 'cover',
            'border': f"1px solid {C['shadow_dk']}",
        }) if trade.get('imagem_arquivo') else html.Span('—', style={'color': C['text2']})
    )
    return html.Tr([
        _td_diario98(str(trade['data_op'])),
        _td_diario98(trade['ativo']),
        _td_diario98(trade.get('janela_pregao') or '—'),
        _td_diario98(tags_str or '—'),
        _td_diario98(trade.get('tipo_operacao') or '—'),
        _td_diario98(f"{trade['resultado_pontos']:+.0f}" if pd.notna(trade['resultado_pontos']) else '—'),
        _td_diario98(f"R$ {saldo_num:+.2f}" if pd.notna(saldo) else '—',
                     extra={'color': cor_saldo, 'fontWeight': '700'}),
        _td_diario98(thumb),
    ])


def _construir_diario98_historico() -> html.Div:
    return html.Div([
        html.Div([
            dcc.Dropdown(id='diario98-hist-ativo', options=_opcoes_diario(cfg.ATIVOS),
                         placeholder='Ativo', style={'width': '120px'}),
            dcc.Dropdown(id='diario98-hist-janela', options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                         placeholder='Janela', style={'width': '160px'}),
            dcc.Dropdown(id='diario98-hist-estrategia', options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
                         placeholder='Estratégia', style={'width': '190px'}),
        ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '10px', 'flexWrap': 'wrap'}),
        html.Div(id='diario98-historico-tabela'),
    ])


# --- Estatísticas --------------------------------------------------------------

def _card_estatistica_98(titulo: str, valor: str, cor=None) -> html.Div:
    return html.Div([
        html.Div(titulo, style={'fontSize': '9px', 'color': C['text2'], 'fontFamily': FONTS['display'],
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em', 'marginBottom': '4px'}),
        html.Div(valor, style={'fontSize': '15px', 'fontWeight': '700', 'fontFamily': FONTS['mono'],
                                'color': cor or C['text']}),
    ], style={**win98_sunken(bg=C['window'], padding='6px 10px'), 'minWidth': '110px'})


def _bloco_breakdown_98(titulo: str, dados: dict) -> html.Div:
    linhas = [
        html.Div([
            html.Span(chave.replace('_', ' ').title(), style={'flex': '1', 'fontSize': '11px'}),
            html.Span(str(v['n']), style={'width': '30px', 'textAlign': 'right', 'fontSize': '11px',
                                           'fontFamily': FONTS['mono']}),
            html.Span(f"R$ {v['saldo']:+.0f}", style={
                'width': '80px', 'textAlign': 'right', 'fontSize': '11px', 'fontFamily': FONTS['mono'],
                'color': C['green'] if v['saldo'] > 0 else (C['red'] if v['saldo'] < 0 else C['text2']),
            }),
        ], style={'display': 'flex', 'padding': '3px 0', 'borderBottom': f"1px solid {C['border']}"})
        for chave, v in dados.items()
    ] or [html.Div('Sem dados.', style={'fontSize': '11px', 'color': C['text2']})]
    return win98_groupbox(titulo, html.Div(linhas), estilo_extra={'flex': '1', 'minWidth': '220px'})


def _grafico_evolucao_saldo_98(stats: dict) -> go.Figure:
    fig = go.Figure()
    if stats['evolucao_data']:
        fig.add_trace(go.Scatter(
            x=stats['evolucao_data'], y=stats['evolucao_saldo_acumulado'],
            mode='lines', line=dict(color=C['gold'], width=2),
            fill='tozeroy', fillcolor='rgba(0,0,128,0.08)',
        ))
    fig.update_layout(
        paper_bgcolor=C['panel'], plot_bgcolor=C['window'],
        font=dict(family=FONTS['mono'], color=C['text'], size=10),
        margin=dict(l=45, r=10, t=10, b=30), height=180,
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor=C['border'], gridwidth=0.5, title='R$ acumulado'),
    )
    return fig


_NOMES_MES = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
              'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
_DIAS_SEMANA = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']


def _grade_calendario_98(por_dia: dict, ano: int, mes: int) -> html.Div:
    """Grade mensal verde/vermelho/cinza por dia — `html.Div` customizado
    (não `go.Heatmap`: não há precedente de Plotly heatmap no projeto, e um
    grid de células `win98_sunken` casa mais com o resto da UI win98)."""
    cal = calendar.Calendar(firstweekday=6)  # domingo primeiro
    semanas = cal.monthdayscalendar(ano, mes)

    cabecalho = html.Div([
        html.Div(d, style={'flex': '1', 'textAlign': 'center', 'fontSize': '9px',
                            'color': C['text2'], 'fontFamily': FONTS['display']})
        for d in _DIAS_SEMANA
    ], style={'display': 'flex', 'marginBottom': '4px'})

    linhas = [cabecalho]
    for semana in semanas:
        celulas = []
        for dia in semana:
            if dia == 0:
                celulas.append(html.Div(style={'flex': '1', 'minHeight': '36px', 'margin': '2px'}))
                continue
            info = por_dia.get(f"{ano:04d}-{mes:02d}-{dia:02d}")
            if info is None:
                bg, cor_texto = C['window'], C['text2']
            elif info['saldo'] > 0:
                bg, cor_texto = C['green'], C['bg']
            elif info['saldo'] < 0:
                bg, cor_texto = C['red'], C['bg']
            else:
                bg, cor_texto = C['border'], C['text2']
            celulas.append(html.Div([
                html.Div(str(dia), style={'fontSize': '10px'}),
                html.Div(f"R$ {info['saldo']:+.0f}" if info else '',
                          style={'fontSize': '9px', 'fontFamily': FONTS['mono']}),
            ], style={**win98_sunken(bg=bg, padding='4px'), 'flex': '1', 'minHeight': '36px',
                       'margin': '2px', 'color': cor_texto, 'textAlign': 'center'}))
        linhas.append(html.Div(celulas, style={'display': 'flex'}))
    return html.Div(linhas)


_COR_SCORE_CONFIABILIDADE = {'alto': 'green', 'medio': 'yellow', 'baixo': 'red'}


def _faixa_score(score: float) -> str:
    if score >= 70:
        return 'alto'
    if score >= 40:
        return 'medio'
    return 'baixo'


def _tabela_score_confiabilidade_98(scores: dict) -> html.Div:
    """Score de Confiabilidade 0-100 por setup (`estrategia_tags`) — ver
    `diario_trade.calcular_score_confiabilidade`. Mostra os 15 setups
    canônicos, mesmo os sem dado nenhum ainda (referência completa)."""
    linhas_ordenadas = sorted(scores.items(), key=lambda item: item[1]['n'], reverse=True)
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['Setup', 'N', 'Taxa Acerto', 'Payoff', 'Profit Factor', 'Score']
    ])
    linhas = []
    for tag, v in linhas_ordenadas:
        if v['n'] == 0:
            score_cel = _td_diario98('sem dados', extra={'color': C['text2']})
        elif not v['amostra_suficiente']:
            score_cel = _td_diario98(f"amostra insuficiente (n={v['n']})", extra={'color': C['text2']})
        else:
            cor = C[_COR_SCORE_CONFIABILIDADE[_faixa_score(v['score'])]]
            score_cel = _td_diario98(f"{v['score']:.0f}", extra={'color': cor, 'fontWeight': '700'})
        linhas.append(html.Tr([
            _td_diario98(tag.replace('_', ' ').title()),
            _td_diario98(str(v['n'])),
            _td_diario98(f"{v['taxa_acerto'] * 100:.1f}%" if v['taxa_acerto'] is not None else '—'),
            _td_diario98(f"{v['payoff']:.2f}" if v['payoff'] is not None else '—'),
            _td_diario98(f"{v['profit_factor']:.2f}" if v['profit_factor'] is not None else '—'),
            score_cel,
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario98_estatisticas(stats: dict, scores: dict) -> html.Div:
    if stats['num_operacoes'] == 0:
        return _construir_diario98_placeholder('Estatísticas (nenhum trade real lançado ainda)')

    payoff_str = f"{stats['payoff']:.2f}" if stats['payoff'] is not None else '—'
    hoje = date.today()
    mes_inicial = f"{hoje.year:04d}-{hoje.month:02d}"
    return html.Div([
        dcc.Store(id='diario98-por-dia-store', data=stats['por_dia']),
        dcc.Store(id='diario98-calendario-mes', data=mes_inicial),

        html.Div([
            _card_estatistica_98('Operações', str(stats['num_operacoes'])),
            _card_estatistica_98('Saldo Total', f"R$ {stats['saldo_total']:+.2f}",
                                  cor=C['green'] if stats['saldo_total'] > 0 else C['red']),
            _card_estatistica_98('Taxa de Acerto', f"{stats['taxa_acerto'] * 100:.1f}%"),
            _card_estatistica_98('Payoff', payoff_str),
            _card_estatistica_98('Expectativa', f"R$ {stats['expectativa']:+.2f}"),
            _card_estatistica_98('Max Gain', f"R$ {stats['max_gain']:+.2f}", cor=C['green']),
            _card_estatistica_98('Max Loss', f"R$ {stats['max_loss']:+.2f}", cor=C['red']),
            _card_estatistica_98('Drawdown Máx.', f"R$ {stats['drawdown_maximo_reais']:+.2f}",
                                  cor=C['red'] if stats['drawdown_maximo_reais'] < 0 else None),
            _card_estatistica_98('Seq. Wins', str(stats['maior_sequencia_win'])),
            _card_estatistica_98('Seq. Losses', str(stats['maior_sequencia_loss'])),
        ], style={'display': 'flex', 'gap': '8px', 'flexWrap': 'wrap', 'marginBottom': '14px'}),

        win98_groupbox('Evolução de Saldo', dcc.Graph(
            figure=_grafico_evolucao_saldo_98(stats), config={'displayModeBar': False},
        )),

        win98_groupbox('Calendário', html.Div([
            html.Div([
                html.Button('◀', id='btn-diario98-cal-prev', n_clicks=0,
                            className='win98-btn', style={**win98_button(), 'padding': '2px 10px'}),
                html.Span(id='diario98-calendario-label', style={
                    'margin': '0 10px', 'fontSize': '11px', 'fontFamily': FONTS['display'],
                }),
                html.Button('▶', id='btn-diario98-cal-next', n_clicks=0,
                            className='win98-btn', style={**win98_button(), 'padding': '2px 10px'}),
            ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '8px'}),
            html.Div(id='diario98-calendario-grid'),
        ])),

        html.Div([
            _bloco_breakdown_98('Por Ativo', stats['por_ativo']),
            _bloco_breakdown_98('Por Janela', stats['por_janela']),
        ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
        html.Div([
            _bloco_breakdown_98('Por Sala/Análise', stats['por_sala']),
            _bloco_breakdown_98('Por Estratégia', stats['por_estrategia']),
        ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
        html.Div([
            _bloco_breakdown_98('Por Tags Setup/Erro', stats['por_tags_setup_erro']),
        ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),

        win98_groupbox('Score de Confiabilidade', _tabela_score_confiabilidade_98(scores)),
    ])


# --- Reconciliação -------------------------------------------------------------

_COR_STATUS_RECONCILIACAO = {
    'ok': 'green', 'divergente': 'red',
    'sem_correspondencia_mt5': 'yellow', 'sem_correspondencia_diario': 'text2',
}


def _tabela_reconciliacao_98(df: pd.DataFrame | None) -> html.Div:
    if df is None or df.empty:
        return html.Div('Nenhum resultado ainda — clique em "Reconciliar agora".', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['ID Trade', 'Position MT5', 'Divergência', 'Confiança', 'Status']
    ])
    linhas = []
    for _, linha in df.iterrows():
        cor = C[_COR_STATUS_RECONCILIACAO.get(linha['mt5_match_status'], 'text2')]
        divergencia = linha['mt5_divergencia_reais']
        linhas.append(html.Tr([
            _td_diario98(str(int(linha['id_trade'])) if pd.notna(linha['id_trade']) else '—'),
            _td_diario98(str(int(linha['mt5_position_id'])) if pd.notna(linha['mt5_position_id']) else '—'),
            _td_diario98(f"R$ {divergencia:+.2f}" if pd.notna(divergencia) else '—'),
            _td_diario98(linha['confianca'] or '—'),
            _td_diario98((linha['mt5_match_status'] or '').replace('_', ' '),
                         extra={'color': cor, 'fontWeight': '700'}),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario98_reconciliacao() -> html.Div:
    return html.Div([
        html.Div([
            html.Button('Reconciliar agora', id='btn-diario98-reconciliar', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Span(id='diario98-reconciliar-status', style={
                'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2'],
            }),
        ], style={'marginBottom': '10px'}),
        html.Div(_tabela_reconciliacao_98(persistencia.carregar_reconciliacao()),
                 id='diario98-reconciliacao-tabela'),
    ])


# --- Aderência S/R -------------------------------------------------------------
# Responde "o motor via o nível que Jesué marcou à mão?" cruzando
# `nivel_sr_referencia` contra o `snapshot_mercado_json` capturado no save
# (ver `diario_trade.avaliar_aderencia_sr`). Relatório de revisão manual —
# Jesué julga, o sistema não ajusta nada sozinho.

_COR_STATUS_ADERENCIA_SR = {
    'visto_preciso': 'green', 'visto_proximo': 'yellow', 'nao_visto': 'red',
}


def _tabela_aderencia_sr_98(df: pd.DataFrame | None) -> html.Div:
    if df is None or df.empty:
        return html.Div('Nenhum resultado ainda — clique em "Avaliar aderência S/R".', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['ID Trade', 'Data', 'Ativo', 'Nível Ref.', 'Nível Motor', 'Dif. ATR', 'Forte', 'Classificação']
    ])
    linhas = []
    for _, linha in df.iterrows():
        cor = C[_COR_STATUS_ADERENCIA_SR.get(linha['classificacao'], 'text2')]
        dif_atr = linha['diferenca_atr']
        nivel_motor = linha['nivel_motor_preco']
        linhas.append(html.Tr([
            _td_diario98(str(int(linha['id_trade'])) if pd.notna(linha['id_trade']) else '—'),
            _td_diario98(str(linha['data_op'])),
            _td_diario98(linha['ativo'] or '—'),
            _td_diario98(f"{linha['nivel_sr_referencia']:.0f}" if pd.notna(linha['nivel_sr_referencia']) else '—'),
            _td_diario98(f"{nivel_motor:.0f}" if pd.notna(nivel_motor) else '—'),
            _td_diario98(f"{dif_atr:.2f}" if pd.notna(dif_atr) else '—'),
            _td_diario98('sim' if linha['forte'] else ('não' if pd.notna(linha['forte']) else '—')),
            _td_diario98((linha['classificacao'] or '').replace('_', ' '),
                         extra={'color': cor, 'fontWeight': '700'}),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario98_aderencia_sr() -> html.Div:
    return html.Div([
        html.Div([
            html.Button('Avaliar aderência S/R', id='btn-diario98-aderencia-sr', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Span(id='diario98-aderencia-sr-status', style={
                'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2'],
            }),
        ], style={'marginBottom': '10px'}),
        html.Div(_tabela_aderencia_sr_98(persistencia.carregar_aderencia_sr()),
                 id='diario98-aderencia-sr-tabela'),
    ])


# --- Padrões comportamentais ----------------------------------------------------
# Revenge trading / tilt cascade — ver `diario_trade.detectar_padroes_
# comportamentais`. Relatório de revisão manual, mesmo espírito da
# Aderência S/R: Jesué julga, o sistema não ajusta nada sozinho.

_COR_TIPO_PADRAO = {'revenge_trading': 'red', 'tilt_cascade': 'yellow'}


def _tabela_padroes_98(df: pd.DataFrame | None) -> html.Div:
    if df is None or df.empty:
        return html.Div('Nenhum resultado ainda — clique em "Detectar padrões".', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['Tipo', 'Data', 'Ativo', 'Trade(s)', 'Precisão', 'Detalhe']
    ])
    linhas = []
    for _, linha in df.iterrows():
        cor = C[_COR_TIPO_PADRAO.get(linha['tipo'], 'text2')]
        # 'dia' é proxy (sem horário real) — itálico/opaco, mesmo tratamento
        # visual que a reconciliação já dá pra confiança 'aproximado'.
        estilo_precisao = {'color': C['text2']}
        if linha['precisao'] == 'dia':
            estilo_precisao['fontStyle'] = 'italic'
        linhas.append(html.Tr([
            _td_diario98(linha['tipo'].replace('_', ' ').title(), extra={'color': cor, 'fontWeight': '700'}),
            _td_diario98(str(linha['data_op'])),
            _td_diario98(linha['ativo'] or '—'),
            _td_diario98(f"#{linha['id_trade_anterior']}→#{linha['id_trade']}"),
            _td_diario98(linha['precisao'], extra=estilo_precisao),
            _td_diario98(linha['detalhe'], extra={'fontSize': '10px'}),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario98_padroes() -> html.Div:
    return html.Div([
        html.Div([
            html.Button('Detectar padrões', id='btn-diario98-padroes', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Span(id='diario98-padroes-status', style={
                'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2'],
            }),
        ], style={'marginBottom': '10px'}),
        html.Div(_tabela_padroes_98(persistencia.carregar_padroes_comportamentais()),
                 id='diario98-padroes-tabela'),
    ])


# =============================================================================
# APP
# =============================================================================

app = dash.Dash(__name__, assets_folder='assets_98', suppress_callback_exceptions=True)

_PADRAO_NOME_IMAGEM_DIARIO = re.compile(r'^(\d{4}_[A-Z]+_\d{8}|rascunho_\d+_\d{2})\.png$')


@app.server.route('/diario-imagens/<path:nome_arquivo>')
def _servir_imagem_diario(nome_arquivo):
    if not _PADRAO_NOME_IMAGEM_DIARIO.match(nome_arquivo):
        flask.abort(404)
    return flask.send_from_directory(cfg.PASTA_DIARIO_IMAGENS, nome_arquivo)


app.layout = html.Div([
    # Lido uma vez na carga pra decidir a aba inicial (ver
    # selecionar_aba_por_url) — permite que dashboard_98.py abra este mesmo
    # app numa aba específica via querystring (?aba=posicao), sem duplicar
    # processo/porta — ver janela 'win-posicao'.
    dcc.Location(id='url-diario98', refresh=False),
    dcc.Store(id='store-diario98-aba', data='novo'),
    html.Div([
        html.Button(nome, id=f'btn-diario98-aba-{chave}', n_clicks=0,
                    className='win98-btn',
                    style=win98_button(pressed=(chave == 'novo')))
        for chave, nome in _ABAS_DIARIO98
    ], style={'display': 'flex', 'gap': '6px', 'marginBottom': '10px'}),
    html.Div(id='diario98-aba-conteudo'),
], style={
    'padding': '8px', 'fontFamily': FONTS['body'], 'background': C['window'],
    'minHeight': '100vh', 'boxSizing': 'border-box',
})


@app.callback(
    Output('store-diario98-aba', 'data'),
    [Input(f'btn-diario98-aba-{chave}', 'n_clicks') for chave, _ in _ABAS_DIARIO98],
    prevent_initial_call=True,
)
def selecionar_aba_diario98(*_n_clicks):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    botao_id = ctx.triggered[0]['prop_id'].split('.')[0]
    chave = botao_id.replace('btn-diario98-aba-', '')
    return chave


@app.callback(
    Output('store-diario98-aba', 'data', allow_duplicate=True),
    Input('url-diario98', 'search'),
    prevent_initial_call='initial_duplicate',
)
def selecionar_aba_por_url(search):
    """Roda uma vez na carga da página — permite que a janela 'win-posicao'
    de dashboard_98.py abra este mesmo app (mesmo processo/porta 8052) já na
    aba 'Posição', via `?aba=posicao`, sem duplicar processo. Sem
    querystring (abertura normal pela janela 'win-diario'), não mexe no
    default 'novo' do Store."""
    if not search:
        return no_update
    chaves_validas = {chave for chave, _ in _ABAS_DIARIO98}
    valores = parse_qs(search.lstrip('?')).get('aba', [])
    if valores and valores[0] in chaves_validas:
        return valores[0]
    return no_update


@app.callback(
    [Output(f'btn-diario98-aba-{chave}', 'style') for chave, _ in _ABAS_DIARIO98],
    Input('store-diario98-aba', 'data'),
)
def atualizar_estilo_aba_diario98(aba_ativa):
    return [win98_button(pressed=(chave == aba_ativa)) for chave, _ in _ABAS_DIARIO98]


_DIARIO98_ABA_CONSTRUTORES = {
    'novo': _construir_diario98_novo_trade,
    'posicao': _construir_diario98_em_andamento,
    'historico': _construir_diario98_historico,
    'estatisticas': lambda: html.Div(id='diario98-estatisticas-conteudo'),
    'reconciliacao': _construir_diario98_reconciliacao,
    'aderencia_sr': _construir_diario98_aderencia_sr,
    'padroes': _construir_diario98_padroes,
}


@app.callback(
    Output('diario98-aba-conteudo', 'children'),
    Input('store-diario98-aba', 'data'),
)
def montar_aba_diario98(aba_ativa):
    """Monta só a aba ativa por vez — mesmo dentro deste processo isolado,
    manter as 4 sempre montadas custaria reconciliação desnecessária a cada
    interação. Herdado de dashboard_98.py, continua valendo aqui."""
    construtor = _DIARIO98_ABA_CONSTRUTORES.get(aba_ativa, _construir_diario98_novo_trade)
    return construtor()


@app.callback(
    Output('diario98-preview-imagem', 'children'),
    Input('diario98-upload-imagem', 'contents'),
    Input('store-diario98-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def preview_imagem_diario98(upload_contents, imagem_colada):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    data_url = upload_contents if origem == 'diario98-upload-imagem' else imagem_colada
    if not data_url:
        return no_update
    return html.Img(src=data_url, style={'maxWidth': '240px', 'border': f"1px solid {C['shadow_dk']}"})


@app.callback(
    Output('diario98-status', 'children'),
    Input('btn-diario98-salvar', 'n_clicks'),
    State('diario98-data', 'value'),
    State('diario98-ativo', 'value'),
    State('diario98-janela', 'value'),
    State('diario98-estrategia', 'value'),
    State('diario98-sala', 'value'),
    State('diario98-tipo-operacao', 'value'),
    State('diario98-alvo', 'value'),
    State('diario98-stop', 'value'),
    State('diario98-lote', 'value'),
    State('diario98-preco-entrada', 'value'),
    State('diario98-preco-saida', 'value'),
    State('diario98-tipo-saida', 'value'),
    State('diario98-resultado', 'value'),
    State('diario98-saldo', 'value'),
    State('diario98-resumo', 'value'),
    State('diario98-obs', 'value'),
    State('diario98-nivel-sr', 'value'),
    State('diario98-nivel-sr-tipo', 'value'),
    State('diario98-tags-setup-erro', 'value'),
    State('diario98-upload-imagem', 'contents'),
    State('store-diario98-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def salvar_novo_trade_98(n_clicks, data_op, ativo, janela, estrategia_tags, sala, tipo_op, alvo, stop, lote,
                          preco_entrada, preco_saida, tipo_saida, resultado, saldo, resumo, obs,
                          nivel_sr, nivel_sr_tipo, tags_setup_erro, upload_contents, imagem_colada):
    if not data_op or not ativo:
        return '⚠ Preencha ao menos Data e Ativo.'

    campos = dict(
        data_op=data_op, ativo=ativo, janela_pregao=janela,
        estrategia_tags=estrategia_tags or [], sala_analise=sala,
        tipo_operacao=tipo_op, alvo_pontos=alvo, stop_pontos=stop, lote=lote or 1,
        preco_entrada=preco_entrada, preco_saida=preco_saida, tipo_saida=tipo_saida,
        resultado_pontos=resultado, saldo_operacao_reais=saldo,
        resumo_operacional=resumo, obs=obs,
        nivel_sr_referencia=nivel_sr, nivel_sr_tipo=nivel_sr_tipo,
        tags_setup_erro=tags_setup_erro or [],
        conta='real',
    )

    imagem_bytes = None
    data_url = upload_contents or imagem_colada
    if data_url:
        try:
            imagem_bytes = base64.b64decode(data_url.split(',', 1)[1])
        except Exception:
            imagem_bytes = None

    # Snapshot do motor de quant no instante do save. Este processo NÃO
    # compartilha memória com dashboard_98.py — lê os candles frescos do
    # disco em vez do `estado['bases']` em memória do dashboard principal
    # (diferença de até ~30s, irrelevante pra este propósito). Escopo: só
    # CAPTURA (ver metricas.montar_snapshot_diario) — comparação contra
    # nivel_sr_referencia fica pra um ciclo futuro.
    df_ativo = persistencia.carregar_candles(ativo)
    m = extrair_metricas(df_ativo) if df_ativo is not None else {}
    snapshot_mercado = montar_snapshot_diario(m) if m else None

    id_trade = dt.registrar_trade(campos, imagem_bytes=imagem_bytes, snapshot_mercado=snapshot_mercado)
    if id_trade is None:
        return '⚠ Diário ocupado por outra escrita — tente novamente em instantes.'
    return f'✔ Operação #{id_trade} salva.'


@app.callback(
    Output('diario98-data', 'value'),
    Output('diario98-ativo', 'value'),
    Output('diario98-janela', 'value'),
    Output('diario98-estrategia', 'value'),
    Output('diario98-sala', 'value'),
    Output('diario98-tipo-operacao', 'value'),
    Output('diario98-alvo', 'value'),
    Output('diario98-stop', 'value'),
    Output('diario98-lote', 'value'),
    Output('diario98-preco-entrada', 'value'),
    Output('diario98-preco-saida', 'value'),
    Output('diario98-tipo-saida', 'value'),
    Output('diario98-resultado', 'value'),
    Output('diario98-saldo', 'value'),
    Output('diario98-resumo', 'value'),
    Output('diario98-obs', 'value'),
    Output('diario98-nivel-sr', 'value'),
    Output('diario98-nivel-sr-tipo', 'value'),
    Output('diario98-tags-setup-erro', 'value'),
    Output('diario98-preview-imagem', 'children', allow_duplicate=True),
    Output('store-diario98-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-diario98-limpar', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_form_diario98(n_clicks):
    return (None, None, None, None, None, None, None, None, 1,
            None, None, None, None, None, None, None, None, None, None,
            None, None)


@app.callback(
    Output('diario98-historico-tabela', 'children'),
    Input('diario98-hist-ativo', 'value'),
    Input('diario98-hist-janela', 'value'),
    Input('diario98-hist-estrategia', 'value'),
)
def atualizar_historico_98(ativo, janela, estrategia):
    df = dt.listar_trades(ativo=ativo, janela=janela, estrategia_tag=estrategia, is_simulated=False)
    df = df.sort_values('data_op', ascending=False).head(50)
    if df.empty:
        return html.Div('Nenhuma operação encontrada.', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['Data', 'Ativo', 'Janela', 'Estratégia', 'Tipo', 'Pts', 'Saldo', 'Print']
    ])
    linhas = [_linha_historico_98(row) for _, row in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


@app.callback(
    Output('diario98-estatisticas-conteudo', 'children'),
    Input('store-diario98-aba', 'data'),
)
def atualizar_estatisticas_98(aba_ativa):
    if aba_ativa != 'estatisticas':
        return no_update
    return _construir_diario98_estatisticas(dt.calcular_estatisticas(), dt.calcular_score_confiabilidade())


@app.callback(
    Output('diario98-calendario-mes', 'data'),
    Input('btn-diario98-cal-prev', 'n_clicks'),
    Input('btn-diario98-cal-next', 'n_clicks'),
    State('diario98-calendario-mes', 'data'),
    prevent_initial_call=True,
)
def navegar_calendario_98(n_prev, n_next, mes_atual):
    ctx = callback_context
    if not ctx.triggered or not mes_atual:
        return no_update
    ano, mes = (int(x) for x in mes_atual.split('-'))
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    mes += -1 if origem == 'btn-diario98-cal-prev' else 1
    if mes == 0:
        mes, ano = 12, ano - 1
    elif mes == 13:
        mes, ano = 1, ano + 1
    return f"{ano:04d}-{mes:02d}"


@app.callback(
    Output('diario98-calendario-grid', 'children'),
    Output('diario98-calendario-label', 'children'),
    Input('diario98-calendario-mes', 'data'),
    Input('diario98-por-dia-store', 'data'),
)
def atualizar_grade_calendario_98(mes_atual, por_dia):
    """Redesenha a grade a partir do `por_dia` já carregado no Store — não
    chama `calcular_estatisticas()` de novo a cada troca de mês (só o
    `interval-slow`/save-novo-trade recarrega o Store, ver
    `atualizar_estatisticas_98`)."""
    if not mes_atual:
        return no_update, no_update
    ano, mes = (int(x) for x in mes_atual.split('-'))
    return _grade_calendario_98(por_dia or {}, ano, mes), f"{_NOMES_MES[mes]} {ano}"


@app.callback(
    Output('diario98-reconciliacao-tabela', 'children'),
    Output('diario98-reconciliar-status', 'children'),
    Input('btn-diario98-reconciliar', 'n_clicks'),
    prevent_initial_call=True,
)
def reconciliar_98(n_clicks):
    try:
        resultado = dt.reconciliar_com_mt5(dias_atras=30)
    except Exception as e:
        return no_update, f'⚠ Erro ao reconciliar: {str(e)[:100]}'
    return _tabela_reconciliacao_98(resultado), f'Concluído — {len(resultado)} linhas.'


@app.callback(
    Output('diario98-aderencia-sr-tabela', 'children'),
    Output('diario98-aderencia-sr-status', 'children'),
    Input('btn-diario98-aderencia-sr', 'n_clicks'),
    prevent_initial_call=True,
)
def avaliar_aderencia_sr_98(n_clicks):
    try:
        resultado = dt.avaliar_aderencia_sr()
        todos = dt.listar_trades()
        com_nivel = todos[todos['nivel_sr_referencia'].notna()]
        sem_snapshot = len(com_nivel) - len(resultado)
    except Exception as e:
        return no_update, f'⚠ Erro ao avaliar: {str(e)[:100]}'
    status = f'Concluído — {len(resultado)} avaliados'
    if sem_snapshot > 0:
        status += f', {sem_snapshot} com nível marcado mas sem snapshot (trades antigos).'
    else:
        status += '.'
    return _tabela_aderencia_sr_98(resultado), status


@app.callback(
    Output('diario98-padroes-tabela', 'children'),
    Output('diario98-padroes-status', 'children'),
    Input('btn-diario98-padroes', 'n_clicks'),
    prevent_initial_call=True,
)
def detectar_padroes_98(n_clicks):
    try:
        resultado = dt.detectar_padroes_comportamentais()
    except Exception as e:
        return no_update, f'⚠ Erro ao detectar: {str(e)[:100]}'
    return _tabela_padroes_98(resultado), f'Concluído — {len(resultado)} padrões detectados.'


@app.callback(
    Output('em-andamento98-conteudo', 'children'),
    Input('interval-em-andamento98', 'n_intervals'),
    Input('store-diario98-aba', 'data'),
)
def atualizar_em_andamento_98(n, aba_ativa):
    if aba_ativa != 'posicao':
        return no_update
    return _renderizar_em_andamento_98(dt.listar_posicoes_abertas(), dt.listar_ordens_pendentes())


@app.callback(
    Output({'type': 'ea98-status', 'index': dash.MATCH}, 'children'),
    Input({'type': 'ea98-btn-salvar', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'ea98-nota', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-estrategia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-sala', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-alvo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-stop', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-btn-salvar', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def salvar_em_andamento_98(n_clicks, nota, estrategia, sala, alvo, stop, id_componente):
    campos = {}
    if estrategia is not None:
        campos['estrategia_tags'] = estrategia
    if sala is not None:
        campos['sala_analise'] = sala
    if alvo is not None:
        campos['alvo_pontos'] = alvo
    if stop is not None:
        campos['stop_pontos'] = stop
    ok = dt.atualizar_rascunho(id_componente['index'], nota_texto=nota or None, campos=campos or None)
    return '✔ Salvo.' if ok else '⚠ Ocupado — tente de novo.'


@app.callback(
    Output('em-andamento98-preview-imagem', 'children'),
    Input('em-andamento98-upload-imagem', 'contents'),
    Input('store-diario98-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def preview_imagem_em_andamento_98(upload_contents, imagem_colada):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    data_url = upload_contents if origem == 'em-andamento98-upload-imagem' else imagem_colada
    if not data_url:
        return no_update
    return html.Img(src=data_url, style={'maxWidth': '180px', 'border': f"1px solid {C['shadow_dk']}"})


@app.callback(
    Output('em-andamento98-anexar-status', 'children'),
    Input('btn-em-andamento98-anexar', 'n_clicks'),
    State('em-andamento98-select-posicao', 'value'),
    State('em-andamento98-upload-imagem', 'contents'),
    State('store-diario98-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def anexar_imagem_em_andamento_98(n_clicks, id_posicao, upload_contents, imagem_colada):
    if not id_posicao:
        return '⚠ Selecione a posição antes de anexar.'
    data_url = upload_contents or imagem_colada
    if not data_url:
        return '⚠ Cole (Ctrl+V) ou envie uma imagem antes.'
    try:
        imagem_bytes = base64.b64decode(data_url.split(',', 1)[1])
    except Exception:
        return '⚠ Imagem inválida.'
    ok = dt.atualizar_rascunho(id_posicao, imagem_bytes=imagem_bytes)
    return '✔ Imagem anexada à galeria.' if ok else '⚠ Ocupado — tente de novo.'


@app.callback(
    Output({'type': 'ea98-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Input({'type': 'ea98-btn-finalizar', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'ea98-estrategia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-sala', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-tipo-saida', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-tags-erro', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-imagem-principal', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-btn-finalizar', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def finalizar_em_andamento_98(n_clicks, estrategia, sala, tipo_saida, tags_erro, imagem_principal, id_componente):
    # Só entra no dict quem tem valor de verdade — mesma razão de
    # diario_app_classico.py::finalizar_em_andamento (não sobrescrever o
    # default `*_preliminar` com vazio se o usuário não mexeu no campo).
    campos_classificacao = {'conta': 'real'}
    if estrategia is not None:
        campos_classificacao['estrategia_tags'] = estrategia
    if sala is not None:
        campos_classificacao['sala_analise'] = sala
    if tipo_saida is not None:
        campos_classificacao['tipo_saida'] = tipo_saida
    if tags_erro is not None:
        campos_classificacao['tags_setup_erro'] = tags_erro
    id_trade = dt.finalizar_posicao(id_componente['index'], campos_classificacao, imagem_principal=imagem_principal)
    if id_trade is None:
        return '⚠ Não foi possível finalizar agora — tente novamente.'
    return f'✔ Finalizado como operação #{id_trade} no Diário.'


if __name__ == '__main__':
    gp.iniciar_monitor_processo_pai(PORTA_PAI)
    print("=" * 60)
    print("  Diário de Trade — processo SQ'98 (porta 8052)")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=PORTA_PROPRIA, threaded=True)
