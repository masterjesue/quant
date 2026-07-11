# =============================================================================
# diario_app_classico.py — Diário de Trade, processo isolado (visual clássico)
# =============================================================================
# Roda em processo/porta próprios (8053), embutido via <iframe> dentro do
# painel "DIÁRIO DE TRADE" de dashboard.py (que continua dono do cabeçalho
# `secao()` e da troca de aba do topo — só o CONTEÚDO interno vira iframe).
#
# Mesmo motivo/arquitetura de `diario_app_98.py` (ler o cabeçalho daquele
# arquivo pro raciocínio completo): o dash-renderer paga um custo de
# reconciliação a cada tick de 1s proporcional ao total de componentes Dash
# montados na página, mesmo escondidos via `display:none` — confirmado
# empiricamente. Um <iframe> isola o Diário numa árvore de reconciliação
# separada, garantia da própria plataforma web, não um ajuste de performance.
#
# Zero duplicação de LÓGICA DE DADOS — só a construção de HTML (estilo dark
# cockpit do clássico) foi migrada de dashboard.py sem mudança de
# comportamento, com a mesma exceção do snapshot de mercado: lido fresco do
# disco (`persistencia.carregar_candles`) em vez de `estado['bases']` em
# memória, já que este processo não compartilha memória com dashboard.py.
#
# Ciclo de vida: lançado automaticamente por dashboard.py na subida (só se a
# porta 8053 ainda não estiver ocupada). Se encerra sozinho quando
# dashboard.py (porta 8050) para de responder por ~60s — ver
# `gerenciador_processos.py`.
# =============================================================================

import base64
import calendar
import re
import sys
from datetime import date
from pathlib import Path
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

PORTA_PROPRIA = 8053
PORTA_PAI = 8050  # dashboard.py

# =============================================================================
# PALETA E TOKENS (mesmos tokens de dashboard.py — duplicados aqui pela mesma
# razão que assets/diario_paste.js duplica assets_98/diario_paste.js: os dois
# front-ends nunca compartilham camada visual, só lógica de dados)
# =============================================================================

C = {
    'bg':        '#0A0A0F',
    'panel':     '#12121A',
    'border':    '#1C1C28',
    'gold':      '#C8A96E',
    'green':     '#2ECC8A',
    'red':       '#E05C5C',
    'text':      '#E8E8F0',
    'text2':     '#8888AA',
    'blue':      '#4A90D9',
    'yellow':    '#F0C040',
}

FONTS = {
    'display': 'Space Grotesk, system-ui, sans-serif',
    'mono':    'JetBrains Mono, monospace',
    'body':    'Inter, system-ui, sans-serif',
}

_ABAS_DIARIO_SUB = [
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


def _estilo_subaba_diario(chave: str, aba_ativa: str) -> dict:
    ativa = (chave == aba_ativa)
    return {
        'background': C['green'] if ativa else C['panel'],
        'color': C['bg'] if ativa else C['text2'],
        'border': f'1px solid {C["border"]}',
        'padding': '6px 14px', 'borderRadius': '6px', 'cursor': 'pointer',
        'fontFamily': FONTS['display'], 'fontSize': '12px', 'fontWeight': '600',
    }


def _campo_diario(label, componente) -> html.Div:
    return html.Div([
        html.Div(label, style={'fontSize': '10px', 'color': C['text2'],
                                'marginBottom': '4px', 'fontFamily': FONTS['display']}),
        componente,
    ], style={'marginBottom': '10px'})


def _bloco_diario(titulo, children) -> html.Div:
    return html.Div([
        html.Div(titulo, style={
            'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
            'fontWeight': '600', 'textTransform': 'uppercase', 'letterSpacing': '0.08em',
            'marginBottom': '10px',
        }),
        children,
    ], style={
        'background': C['panel'], 'border': f'1px solid {C["border"]}',
        'borderRadius': '10px', 'padding': '14px 16px', 'marginBottom': '16px',
    })


def _estilo_input_diario(extra=None) -> dict:
    estilo = {
        'background': C['bg'], 'border': f'1px solid {C["border"]}',
        'borderRadius': '6px', 'padding': '6px 10px', 'color': C['text'],
        'fontFamily': FONTS['body'], 'fontSize': '13px', 'width': '100%',
        'boxSizing': 'border-box',
    }
    if extra:
        estilo.update(extra)
    return estilo


def _placeholder_aba(nome: str) -> html.Div:
    return html.Div(f'{nome} — em construção.', style={
        'padding': '40px', 'textAlign': 'center', 'color': C['text2'],
        'fontFamily': FONTS['display'], 'fontSize': '13px',
    })


def _construir_diario_novo_trade() -> html.Div:
    campo = _campo_diario
    estilo_input = _estilo_input_diario
    linha = lambda *filhos: html.Div(list(filhos), style={'display': 'flex', 'gap': '16px', 'flexWrap': 'wrap'})

    return html.Div([
        _bloco_diario('Dados da Operação', html.Div([
            linha(
                campo('Data', dcc.Input(id='diario-data', type='date', style=estilo_input({'width': '160px'}))),
                campo('Ativo', dcc.Dropdown(id='diario-ativo', options=_opcoes_diario(cfg.ATIVOS),
                                             clearable=False, style={'width': '120px'})),
                campo('Janela', dcc.Dropdown(id='diario-janela',
                                              options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                                              style={'width': '170px'})),
            ),
            linha(
                campo('Estratégia', dcc.Dropdown(id='diario-estrategia', multi=True,
                                                  options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
                                                  style={'width': '320px'})),
                campo('Sala/Análise', dcc.Dropdown(id='diario-sala',
                                                     options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
                                                     style={'width': '190px'})),
            ),
            linha(
                campo('Tipo', dcc.Dropdown(id='diario-tipo-operacao', options=_opcoes_diario(dt.TIPO_OPERACAO),
                                            clearable=False, style={'width': '110px'})),
                campo('Alvo (pts)', dcc.Input(id='diario-alvo', type='number', style=estilo_input({'width': '110px'}))),
                campo('Stop (pts)', dcc.Input(id='diario-stop', type='number', style=estilo_input({'width': '110px'}))),
                campo('Lote', dcc.Input(id='diario-lote', type='number', value=1, style=estilo_input({'width': '90px'}))),
            ),
            linha(
                campo('Preço Entrada', dcc.Input(id='diario-preco-entrada', type='number', style=estilo_input({'width': '130px'}))),
                campo('Preço Saída', dcc.Input(id='diario-preco-saida', type='number', style=estilo_input({'width': '130px'}))),
                campo('Tipo Saída', dcc.Dropdown(id='diario-tipo-saida', options=_opcoes_diario(dt.TIPO_SAIDA),
                                                  style={'width': '150px'})),
            ),
            linha(
                campo('Resultado (pts)', dcc.Input(id='diario-resultado', type='number', style=estilo_input({'width': '130px'}))),
                campo('Saldo (R$)', dcc.Input(id='diario-saldo', type='number', style=estilo_input({'width': '130px'}))),
            ),
        ])),

        _bloco_diario('Nível de S/R referenciado (opcional)', html.Div([
            linha(
                campo('Preço', dcc.Input(id='diario-nivel-sr', type='number', style=estilo_input({'width': '130px'}))),
                campo('Tipo', dcc.Input(id='diario-nivel-sr-tipo', type='text', placeholder='ex: suporte',
                                         style=estilo_input({'width': '170px'}))),
            ),
        ])),

        _bloco_diario('Anotações', html.Div([
            campo('Resumo Operacional', dcc.Textarea(id='diario-resumo', rows=3, style=estilo_input({'resize': 'none'}))),
            campo('Obs (autocoaching)', dcc.Textarea(id='diario-obs', rows=2, style=estilo_input({'resize': 'none'}))),
            campo('Tags de Setup/Erro', dcc.Dropdown(id='diario-tags-setup-erro', multi=True,
                                                       options=_opcoes_diario(dt.TAGS_SETUP_ERRO_INICIAIS))),
        ])),

        _bloco_diario('Print', html.Div([
            html.Div('Cole (Ctrl+V) um print ou envie um arquivo:', style={
                'fontSize': '11px', 'color': C['text2'], 'marginBottom': '8px'}),
            dcc.Upload(
                id='diario-upload-imagem',
                children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '12px'}),
                style={
                    'background': C['bg'], 'border': f'1px dashed {C["border"]}', 'borderRadius': '8px',
                    'padding': '16px', 'textAlign': 'center', 'cursor': 'pointer', 'color': C['text2'],
                },
            ),
            dcc.Store(id='store-diario-imagem-colada'),
            html.Div(id='diario-preview-imagem', style={'marginTop': '10px'}),
        ])),

        html.Div([
            html.Button('Salvar Operação', id='btn-diario-salvar', n_clicks=0, style={
                'background': C['green'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
                'padding': '8px 20px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
                'fontWeight': '700', 'fontSize': '12px',
            }),
            html.Button('Limpar', id='btn-diario-limpar', n_clicks=0, style={
                'background': 'transparent', 'color': C['text2'], 'border': f'1px solid {C["border"]}',
                'borderRadius': '6px', 'padding': '8px 20px', 'cursor': 'pointer', 'marginLeft': '10px',
                'fontFamily': FONTS['display'], 'fontSize': '12px',
            }),
        ], style={'marginTop': '6px'}),
        html.Div(id='diario-status', style={
            'marginTop': '10px', 'fontSize': '12px', 'fontFamily': FONTS['mono'], 'color': C['text2'],
        }),
    ])


# --- Em Andamento ---------------------------------------------------------
# Rascunho ao vivo de posições abertas — ver plano em
# C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md.
# Dados vêm de `diario_trade.listar_posicoes_abertas()` (tabela SEPARADA de
# `diario_trades.parquet`, escrita pelo dashboard principal a cada poucos
# segundos com os campos vindos do MT5). Este processo só lê/edita os
# campos do usuário (notas, tags preliminares, imagens) — nunca escreve os
# campos de MT5. Primeiro uso de callback com ID por padrão-matching
# (`{'type': ..., 'index': id_posicao}`) neste arquivo: necessário porque o
# número de posições abertas varia (0, 1 ou várias simultâneas).

def _lista_segura(valor) -> list:
    """Mesma pegadinha de `estrategia_tags` (parquet devolve numpy array,
    não list) — normaliza antes de usar como `value=` de um Dropdown/lista."""
    if valor is None or isinstance(valor, str):
        return []
    return list(valor) if hasattr(valor, '__iter__') else []


def _bloco_finalizar_em_andamento(pos: dict, imagens: list) -> html.Div:
    campo = _campo_diario
    id_pos = pos['id_posicao']
    opcoes_imagem = [{'label': nome, 'value': nome} for nome in imagens]
    return html.Div([
        html.Div('Finalizar operação', style={
            'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'marginTop': '14px', 'marginBottom': '6px',
        }),
        html.Div([
            campo('Tipo de Saída', dcc.Dropdown(id={'type': 'ea-tipo-saida', 'index': id_pos},
                  options=_opcoes_diario(dt.TIPO_SAIDA), style={'width': '160px'})),
            campo('Tags Setup/Erro', dcc.Dropdown(id={'type': 'ea-tags-erro', 'index': id_pos}, multi=True,
                  options=_opcoes_diario(dt.TAGS_SETUP_ERRO_INICIAIS), style={'width': '260px'})),
        ] + ([campo('Imagem principal', dcc.Dropdown(id={'type': 'ea-imagem-principal', 'index': id_pos},
                    options=opcoes_imagem, style={'width': '220px'}))] if opcoes_imagem else []),
            style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap'}),
        html.Button('Finalizar e gravar no Diário', id={'type': 'ea-btn-finalizar', 'index': id_pos}, n_clicks=0, style={
            'background': C['gold'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
            'padding': '7px 18px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'fontSize': '12px', 'marginTop': '8px',
        }),
    ])


def _bloco_posicao_em_andamento(pos: dict) -> html.Div:
    campo = _campo_diario
    estilo_input = _estilo_input_diario
    id_pos = pos['id_posicao']
    cor_direcao = C['green'] if pos.get('tipo_operacao') == 'long' else C['red']
    pendente = pos.get('status') == dt.STATUS_POSICAO_FECHADA_PENDENTE

    notas = _lista_segura(pos.get('notas_cronologicas'))
    timeline = html.Div([
        html.Div([
            html.Span(str(n.get('hora', ''))[:16].replace('T', ' '), style={
                'color': C['text2'], 'fontSize': '10px', 'fontFamily': FONTS['mono']}),
            html.Span(' — ' + str(n.get('texto', '')), style={'fontSize': '12px', 'marginLeft': '6px'}),
        ], style={'padding': '3px 0', 'borderBottom': f'1px solid {C["border"]}'})
        for n in notas
    ]) if notas else html.Div('Sem notas ainda.', style={'fontSize': '12px', 'color': C['text2']})

    imagens = _lista_segura(pos.get('imagens_em_andamento'))
    galeria = html.Div([
        html.Img(src=f'/diario-imagens/{nome}', style={
            'width': '70px', 'height': '46px', 'objectFit': 'cover', 'borderRadius': '4px',
            'border': f'1px solid {C["border"]}', 'marginRight': '6px', 'marginTop': '6px',
        }) for nome in imagens
    ], style={'display': 'flex', 'flexWrap': 'wrap'}) if imagens else None

    preco_ref = pos.get('preco_saida') if pendente else pos.get('preco_atual')
    label_ref = 'Saída' if pendente else 'Atual'
    pnl_pts = pos.get('pnl_pontos_final') if pendente else pos.get('pnl_pontos')
    pnl_reais = pos.get('pnl_reais_final') if pendente else pos.get('pnl_reais')
    detalhe = html.Div([
        html.Span((pos.get('tipo_operacao') or '—').upper(), style={
            'color': cor_direcao, 'fontSize': '11px', 'fontWeight': '700'}),
        html.Span(' ⚠ FECHADA — AGUARDANDO CONFIRMAÇÃO' if pendente else ' ● ABERTA', style={
            'marginLeft': '8px', 'fontSize': '10px', 'fontWeight': '700',
            'color': C['yellow'] if pendente else C['green']}),
        html.Div(
            f"Entrada {_fmt_num(pos.get('preco_entrada'))} · {label_ref} {_fmt_num(preco_ref)} · "
            f"Pts {_fmt_num(pnl_pts, sinal=True)} · R$ {_fmt_num(pnl_reais, sinal=True, casas=2)}",
            style={'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['mono'], 'marginTop': '2px'}
        ),
    ])

    campos_classificacao = html.Div([
        campo('Estratégia', dcc.Dropdown(
            id={'type': 'ea-estrategia', 'index': id_pos}, multi=True,
            options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
            value=_lista_segura(pos.get('estrategia_tags_preliminar')) or None, style={'width': '260px'})),
        campo('Sala/Análise', dcc.Dropdown(
            id={'type': 'ea-sala', 'index': id_pos}, options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
            value=pos.get('sala_analise_preliminar'), style={'width': '170px'})),
        campo('Alvo (pts)', dcc.Input(id={'type': 'ea-alvo', 'index': id_pos}, type='number',
              value=pos.get('alvo_pontos_preliminar'), style=estilo_input({'width': '100px'}))),
        campo('Stop (pts)', dcc.Input(id={'type': 'ea-stop', 'index': id_pos}, type='number',
              value=pos.get('stop_pontos_preliminar'), style=estilo_input({'width': '100px'}))),
    ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '10px'})

    nota_input = html.Div([
        dcc.Textarea(id={'type': 'ea-nota', 'index': id_pos}, rows=2,
                     placeholder='Escreva uma nova anotação (análise durante ou depois da operação)...',
                     style=estilo_input({'resize': 'none'})),
        html.Button('Salvar', id={'type': 'ea-btn-salvar', 'index': id_pos}, n_clicks=0, style={
            'background': C['green'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
            'padding': '6px 16px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'fontSize': '11px', 'marginTop': '6px',
        }),
        html.Div(id={'type': 'ea-status', 'index': id_pos}, style={
            'fontSize': '11px', 'color': C['text2'], 'marginTop': '4px'}),
    ], style={'marginTop': '10px'})

    filhos = [
        detalhe,
        html.Div('Notas:', style={'fontSize': '10px', 'color': C['text2'], 'marginTop': '10px'}),
        timeline,
    ]
    if galeria:
        filhos.append(galeria)
    filhos.append(campos_classificacao)
    filhos.append(nota_input)
    if pendente:
        filhos.append(_bloco_finalizar_em_andamento(pos, imagens))

    return _bloco_diario(f"{pos.get('ativo', '—')} · {pos.get('contrato', '—')}", html.Div(filhos))


def _fmt_num(valor, sinal=False, casas=0) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return '—'
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return '—'
    formato = f"{{:+.{casas}f}}" if sinal else f"{{:.{casas}f}}"
    return formato.format(v)


_TIPO_ORDEM_LABEL = {
    'compra_limitada': 'Compra Limitada', 'venda_limitada': 'Venda Limitada',
    'compra_stop': 'Compra Stop', 'venda_stop': 'Venda Stop',
    'compra_stop_limitada': 'Compra Stop Limitada', 'venda_stop_limitada': 'Venda Stop Limitada',
}


def _secao_ordens_pendentes(ordens: pd.DataFrame) -> html.Div | None:
    """Ordens ainda NÃO executadas (buy/sell limit/stop) — puramente
    informativo, sem nota/tag/imagem (não é um trade ainda, não há o que
    jornalar até executar ou ser cancelada)."""
    if ordens is None or ordens.empty:
        return None
    linhas = []
    for o in ordens.to_dict('records'):
        cor = C['green'] if 'compra' in (o.get('tipo_ordem') or '') else C['red']
        extras = []
        if pd.notna(o.get('sl')):
            extras.append(f"Stop {_fmt_num(o['sl'])}")
        if pd.notna(o.get('tp')):
            extras.append(f"Alvo {_fmt_num(o['tp'])}")
        linhas.append(html.Div([
            html.Span(f"{o.get('ativo')} · {o.get('contrato')}", style={
                'fontWeight': '700', 'fontFamily': FONTS['display'], 'fontSize': '12px'}),
            html.Span(_TIPO_ORDEM_LABEL.get(o.get('tipo_ordem'), o.get('tipo_ordem') or '—'), style={
                'color': cor, 'marginLeft': '8px', 'fontSize': '11px', 'fontWeight': '700'}),
            html.Div(
                f"Preço pedido {_fmt_num(o.get('preco_pedido'))} · {o.get('volume')} lote(s)"
                + (' · ' + ' · '.join(extras) if extras else ''),
                style={'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['mono'], 'marginTop': '2px'}
            ),
        ], style={
            'padding': '8px 0', 'borderBottom': f'1px solid {C["border"]}',
        }))
    return _bloco_diario('Ordens Pendentes (ainda não executadas)', html.Div(linhas))


def _renderizar_em_andamento(posicoes: pd.DataFrame, ordens: pd.DataFrame | None = None) -> html.Div:
    secao_ordens = _secao_ordens_pendentes(ordens) if ordens is not None else None

    if posicoes is None or posicoes.empty:
        vazio = html.Div('Nenhuma posição aberta ou aguardando confirmação no momento.', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
        return html.Div([vazio, secao_ordens] if secao_ordens else vazio)

    registros = posicoes.to_dict('records')
    blocos = [_bloco_posicao_em_andamento(p) for p in registros]

    opcoes_posicao = [
        {'label': f"{p.get('ativo')} · entrada {_fmt_num(p.get('preco_entrada'))} · "
                  f"{'aguardando confirmação' if p.get('status') == dt.STATUS_POSICAO_FECHADA_PENDENTE else 'aberta'}",
         'value': p['id_posicao']}
        for p in registros
    ]
    anexar = _bloco_diario('Anexar print (cole com Ctrl+V ou envie um arquivo)', html.Div([
        _campo_diario('Posição', dcc.Dropdown(id='em-andamento-select-posicao', options=opcoes_posicao,
                                                style={'width': '320px'})),
        dcc.Upload(
            id='em-andamento-upload-imagem',
            children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '12px'}),
            style={
                'background': C['bg'], 'border': f'1px dashed {C["border"]}', 'borderRadius': '8px',
                'padding': '12px', 'textAlign': 'center', 'cursor': 'pointer', 'color': C['text2'],
                'marginTop': '8px',
            },
        ),
        html.Div(id='em-andamento-preview-imagem', style={'marginTop': '8px'}),
        html.Button('Anexar à posição selecionada', id='btn-em-andamento-anexar', n_clicks=0, style={
            'background': C['green'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
            'padding': '7px 18px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'fontSize': '12px', 'marginTop': '8px',
        }),
        html.Div(id='em-andamento-anexar-status', style={'marginTop': '6px', 'fontSize': '11px', 'color': C['text2']}),
    ]))

    filhos = blocos + [anexar]
    if secao_ordens:
        filhos.append(secao_ordens)
    return html.Div(filhos)


def _construir_diario_em_andamento() -> html.Div:
    return html.Div([
        dcc.Interval(id='interval-em-andamento', interval=6_000, n_intervals=0),
        html.Div(id='em-andamento-conteudo'),
        # Reaproveita o MESMO id/JS global de colagem (assets/diario_paste.js)
        # do Novo Trade — seguro porque as sub-abas nunca ficam montadas ao
        # mesmo tempo (`montar_sub_aba_diario` troca o conteúdo inteiro).
        dcc.Store(id='store-diario-imagem-colada'),
    ])


def _td_diario(conteudo, extra=None) -> html.Td:
    estilo = {'padding': '6px 10px', 'fontSize': '12px', 'fontFamily': FONTS['mono'],
              'borderBottom': f'1px solid {C["border"]}'}
    if extra:
        estilo.update(extra)
    return html.Td(conteudo, style=estilo)


def _linha_historico(trade: pd.Series) -> html.Tr:
    tags = trade['estrategia_tags']
    tags_str = ', '.join(tags) if hasattr(tags, '__iter__') else ''
    saldo = trade['saldo_operacao_reais']
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0
    cor_saldo = C['green'] if saldo_num > 0 else (C['red'] if saldo_num < 0 else C['text2'])
    thumb = (
        html.Img(src=f"/diario-imagens/{trade['imagem_arquivo']}", style={
            'width': '56px', 'height': '36px', 'objectFit': 'cover', 'borderRadius': '4px',
            'border': f'1px solid {C["border"]}',
        }) if trade.get('imagem_arquivo') else html.Span('—', style={'color': C['text2']})
    )
    return html.Tr([
        _td_diario(str(trade['data_op'])),
        _td_diario(trade['ativo']),
        _td_diario(trade.get('janela_pregao') or '—'),
        _td_diario(tags_str or '—'),
        _td_diario(trade.get('tipo_operacao') or '—'),
        _td_diario(f"{trade['resultado_pontos']:+.0f}" if pd.notna(trade['resultado_pontos']) else '—'),
        _td_diario(f"R$ {saldo_num:+.2f}" if pd.notna(saldo) else '—',
                   extra={'color': cor_saldo, 'fontWeight': '700'}),
        _td_diario(thumb),
    ])


def _construir_diario_historico() -> html.Div:
    return html.Div([
        html.Div([
            dcc.Dropdown(id='diario-hist-ativo', options=_opcoes_diario(cfg.ATIVOS),
                         placeholder='Ativo', style={'width': '140px'}),
            dcc.Dropdown(id='diario-hist-janela', options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                         placeholder='Janela', style={'width': '170px'}),
            dcc.Dropdown(id='diario-hist-estrategia', options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
                         placeholder='Estratégia', style={'width': '200px'}),
        ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '14px', 'flexWrap': 'wrap'}),
        html.Div(id='diario-historico-tabela'),
    ])


def _card_estatistica(titulo: str, valor: str, cor=None) -> html.Div:
    return html.Div([
        html.Div(titulo, style={'fontSize': '10px', 'color': C['text2'], 'fontFamily': FONTS['display'],
                                 'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '5px'}),
        html.Div(valor, style={'fontSize': '17px', 'fontWeight': '700', 'fontFamily': FONTS['mono'],
                                'color': cor or C['text']}),
    ], style={'background': C['bg'], 'border': f'1px solid {C["border"]}', 'borderRadius': '8px',
              'padding': '10px 14px', 'minWidth': '120px'})


def _bloco_breakdown_diario(titulo: str, dados: dict) -> html.Div:
    linhas = [
        html.Div([
            html.Span(chave.replace('_', ' ').title(), style={'flex': '1', 'fontSize': '12px'}),
            html.Span(str(v['n']), style={'width': '34px', 'textAlign': 'right', 'fontSize': '12px',
                                           'fontFamily': FONTS['mono']}),
            html.Span(f"R$ {v['saldo']:+.0f}", style={
                'width': '90px', 'textAlign': 'right', 'fontSize': '12px', 'fontFamily': FONTS['mono'],
                'color': C['green'] if v['saldo'] > 0 else (C['red'] if v['saldo'] < 0 else C['text2']),
            }),
        ], style={'display': 'flex', 'padding': '4px 0', 'borderBottom': f'1px solid {C["border"]}'})
        for chave, v in dados.items()
    ] or [html.Div('Sem dados.', style={'fontSize': '12px', 'color': C['text2']})]
    return _bloco_diario(titulo, html.Div(linhas))


def _grafico_evolucao_saldo(stats: dict) -> go.Figure:
    fig = go.Figure()
    if stats['evolucao_data']:
        fig.add_trace(go.Scatter(
            x=stats['evolucao_data'], y=stats['evolucao_saldo_acumulado'],
            mode='lines', line=dict(color=C['gold'], width=2),
            fill='tozeroy', fillcolor='rgba(200,169,110,0.08)',
        ))
    fig.update_layout(
        paper_bgcolor=C['panel'], plot_bgcolor=C['bg'],
        font=dict(family=FONTS['mono'], color=C['text2'], size=10),
        margin=dict(l=45, r=10, t=10, b=30), height=200,
        xaxis=dict(showgrid=False, gridcolor=C['border']),
        yaxis=dict(gridcolor=C['border'], gridwidth=0.5, title='R$ acumulado'),
    )
    return fig


_NOMES_MES = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
              'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
_DIAS_SEMANA = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']


def _grade_calendario(por_dia: dict, ano: int, mes: int) -> html.Div:
    """Grade mensal verde/vermelho/cinza por dia — `html.Div` customizado
    (mesma razão de `diario_app_98.py::_grade_calendario_98`: sem
    precedente de Plotly heatmap no projeto, grid casa melhor com o
    resto da UI dark-cockpit)."""
    cal = calendar.Calendar(firstweekday=6)  # domingo primeiro
    semanas = cal.monthdayscalendar(ano, mes)

    cabecalho = html.Div([
        html.Div(d, style={'flex': '1', 'textAlign': 'center', 'fontSize': '10px',
                            'color': C['text2'], 'fontFamily': FONTS['display']})
        for d in _DIAS_SEMANA
    ], style={'display': 'flex', 'marginBottom': '4px'})

    linhas = [cabecalho]
    for semana in semanas:
        celulas = []
        for dia in semana:
            if dia == 0:
                celulas.append(html.Div(style={'flex': '1', 'minHeight': '40px', 'margin': '2px'}))
                continue
            info = por_dia.get(f"{ano:04d}-{mes:02d}-{dia:02d}")
            if info is None:
                bg, cor_texto = C['bg'], C['text2']
            elif info['saldo'] > 0:
                bg, cor_texto = C['green'], C['bg']
            elif info['saldo'] < 0:
                bg, cor_texto = C['red'], C['bg']
            else:
                bg, cor_texto = C['border'], C['text2']
            celulas.append(html.Div([
                html.Div(str(dia), style={'fontSize': '11px'}),
                html.Div(f"R$ {info['saldo']:+.0f}" if info else '',
                          style={'fontSize': '10px', 'fontFamily': FONTS['mono']}),
            ], style={'background': bg, 'border': f'1px solid {C["border"]}', 'borderRadius': '4px',
                       'flex': '1', 'minHeight': '40px', 'margin': '2px', 'padding': '4px',
                       'color': cor_texto, 'textAlign': 'center'}))
        linhas.append(html.Div(celulas, style={'display': 'flex'}))
    return html.Div(linhas)


_COR_SCORE_CONFIABILIDADE = {'alto': 'green', 'medio': 'yellow', 'baixo': 'red'}


def _faixa_score(score: float) -> str:
    if score >= 70:
        return 'alto'
    if score >= 40:
        return 'medio'
    return 'baixo'


def _tabela_score_confiabilidade(scores: dict) -> html.Div:
    """Score de Confiabilidade 0-100 por setup — ver
    `diario_trade.calcular_score_confiabilidade`. Mostra os 15 setups
    canônicos, mesmo os sem dado nenhum ainda (referência completa)."""
    linhas_ordenadas = sorted(scores.items(), key=lambda item: item[1]['n'], reverse=True)
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
        for t in ['Setup', 'N', 'Taxa Acerto', 'Payoff', 'Profit Factor', 'Score']
    ])
    linhas = []
    for tag, v in linhas_ordenadas:
        if v['n'] == 0:
            score_cel = _td_diario('sem dados', extra={'color': C['text2']})
        elif not v['amostra_suficiente']:
            score_cel = _td_diario(f"amostra insuficiente (n={v['n']})", extra={'color': C['text2']})
        else:
            chave_cor = _COR_SCORE_CONFIABILIDADE[_faixa_score(v['score'])]
            score_cel = _td_diario(f"{v['score']:.0f}", extra={'color': C.get(chave_cor, C['text2']), 'fontWeight': '700'})
        linhas.append(html.Tr([
            _td_diario(tag.replace('_', ' ').title()),
            _td_diario(str(v['n'])),
            _td_diario(f"{v['taxa_acerto'] * 100:.1f}%" if v['taxa_acerto'] is not None else '—'),
            _td_diario(f"{v['payoff']:.2f}" if v['payoff'] is not None else '—'),
            _td_diario(f"{v['profit_factor']:.2f}" if v['profit_factor'] is not None else '—'),
            score_cel,
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario_estatisticas(stats: dict, scores: dict) -> html.Div:
    if stats['num_operacoes'] == 0:
        return _placeholder_aba('Estatísticas (nenhum trade real lançado ainda)')

    payoff_str = f"{stats['payoff']:.2f}" if stats['payoff'] is not None else '—'
    hoje = date.today()
    mes_inicial = f"{hoje.year:04d}-{hoje.month:02d}"
    return html.Div([
        dcc.Store(id='diario-por-dia-store', data=stats['por_dia']),
        dcc.Store(id='diario-calendario-mes', data=mes_inicial),

        html.Div([
            _card_estatistica('Operações', str(stats['num_operacoes'])),
            _card_estatistica('Saldo Total', f"R$ {stats['saldo_total']:+.2f}",
                               cor=C['green'] if stats['saldo_total'] > 0 else C['red']),
            _card_estatistica('Taxa de Acerto', f"{stats['taxa_acerto'] * 100:.1f}%"),
            _card_estatistica('Payoff', payoff_str),
            _card_estatistica('Expectativa', f"R$ {stats['expectativa']:+.2f}"),
            _card_estatistica('Max Gain', f"R$ {stats['max_gain']:+.2f}", cor=C['green']),
            _card_estatistica('Max Loss', f"R$ {stats['max_loss']:+.2f}", cor=C['red']),
            _card_estatistica('Drawdown Máx.', f"R$ {stats['drawdown_maximo_reais']:+.2f}",
                               cor=C['red'] if stats['drawdown_maximo_reais'] < 0 else None),
            _card_estatistica('Seq. Wins', str(stats['maior_sequencia_win'])),
            _card_estatistica('Seq. Losses', str(stats['maior_sequencia_loss'])),
        ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap', 'marginBottom': '16px'}),

        _bloco_diario('Evolução de Saldo', dcc.Graph(
            figure=_grafico_evolucao_saldo(stats), config={'displayModeBar': False},
        )),

        _bloco_diario('Calendário', html.Div([
            html.Div([
                html.Button('◀', id='btn-diario-cal-prev', n_clicks=0, style={
                    'background': C['panel'], 'color': C['text'], 'border': f'1px solid {C["border"]}',
                    'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer',
                }),
                html.Span(id='diario-calendario-label', style={
                    'margin': '0 10px', 'fontSize': '12px', 'fontFamily': FONTS['display'],
                }),
                html.Button('▶', id='btn-diario-cal-next', n_clicks=0, style={
                    'background': C['panel'], 'color': C['text'], 'border': f'1px solid {C["border"]}',
                    'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer',
                }),
            ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '8px'}),
            html.Div(id='diario-calendario-grid'),
        ])),

        html.Div([
            _bloco_breakdown_diario('Por Ativo', stats['por_ativo']),
            _bloco_breakdown_diario('Por Janela', stats['por_janela']),
        ], style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap'}),
        html.Div([
            _bloco_breakdown_diario('Por Sala/Análise', stats['por_sala']),
            _bloco_breakdown_diario('Por Estratégia', stats['por_estrategia']),
        ], style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap'}),
        html.Div([
            _bloco_breakdown_diario('Por Tags Setup/Erro', stats['por_tags_setup_erro']),
        ], style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap'}),

        _bloco_diario('Score de Confiabilidade', _tabela_score_confiabilidade(scores)),
    ])


_COR_STATUS_RECONCILIACAO = {
    'ok': 'green', 'divergente': 'red',
    'sem_correspondencia_mt5': 'yellow', 'sem_correspondencia_diario': 'text2',
}


def _tabela_reconciliacao(df: pd.DataFrame | None) -> html.Div:
    if df is None or df.empty:
        return html.Div('Nenhum resultado ainda — clique em "Reconciliar agora".', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
        for t in ['ID Trade', 'Position MT5', 'Divergência', 'Confiança', 'Status']
    ])
    linhas = []
    for _, linha in df.iterrows():
        chave_cor = _COR_STATUS_RECONCILIACAO.get(linha['mt5_match_status'], 'text2')
        cor = C.get(chave_cor, C['text2'])
        divergencia = linha['mt5_divergencia_reais']
        linhas.append(html.Tr([
            _td_diario(str(int(linha['id_trade'])) if pd.notna(linha['id_trade']) else '—'),
            _td_diario(str(int(linha['mt5_position_id'])) if pd.notna(linha['mt5_position_id']) else '—'),
            _td_diario(f"R$ {divergencia:+.2f}" if pd.notna(divergencia) else '—'),
            _td_diario(linha['confianca'] or '—'),
            _td_diario((linha['mt5_match_status'] or '').replace('_', ' '),
                       extra={'color': cor, 'fontWeight': '700'}),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario_reconciliacao() -> html.Div:
    return html.Div([
        html.Div([
            html.Button('Reconciliar agora', id='btn-diario-reconciliar', n_clicks=0, style={
                'background': C['green'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
                'padding': '8px 20px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
                'fontWeight': '700', 'fontSize': '12px',
            }),
            html.Span(id='diario-reconciliar-status', style={
                'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2'],
            }),
        ], style={'marginBottom': '14px'}),
        html.Div(_tabela_reconciliacao(persistencia.carregar_reconciliacao()),
                 id='diario-reconciliacao-tabela'),
    ])


# --- Aderência S/R -------------------------------------------------------------
# Responde "o motor via o nível que Jesué marcou à mão?" cruzando
# `nivel_sr_referencia` contra o `snapshot_mercado_json` capturado no save
# (ver `diario_trade.avaliar_aderencia_sr`). Relatório de revisão manual —
# Jesué julga, o sistema não ajusta nada sozinho.

_COR_STATUS_ADERENCIA_SR = {
    'visto_preciso': 'green', 'visto_proximo': 'yellow', 'nao_visto': 'red',
}


def _tabela_aderencia_sr(df: pd.DataFrame | None) -> html.Div:
    if df is None or df.empty:
        return html.Div('Nenhum resultado ainda — clique em "Avaliar aderência S/R".', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
        for t in ['ID Trade', 'Data', 'Ativo', 'Nível Ref.', 'Nível Motor', 'Dif. ATR', 'Forte', 'Classificação']
    ])
    linhas = []
    for _, linha in df.iterrows():
        chave_cor = _COR_STATUS_ADERENCIA_SR.get(linha['classificacao'], 'text2')
        cor = C.get(chave_cor, C['text2'])
        dif_atr = linha['diferenca_atr']
        nivel_motor = linha['nivel_motor_preco']
        linhas.append(html.Tr([
            _td_diario(str(int(linha['id_trade'])) if pd.notna(linha['id_trade']) else '—'),
            _td_diario(str(linha['data_op'])),
            _td_diario(linha['ativo'] or '—'),
            _td_diario(f"{linha['nivel_sr_referencia']:.0f}" if pd.notna(linha['nivel_sr_referencia']) else '—'),
            _td_diario(f"{nivel_motor:.0f}" if pd.notna(nivel_motor) else '—'),
            _td_diario(f"{dif_atr:.2f}" if pd.notna(dif_atr) else '—'),
            _td_diario('sim' if linha['forte'] else ('não' if pd.notna(linha['forte']) else '—')),
            _td_diario((linha['classificacao'] or '').replace('_', ' '),
                       extra={'color': cor, 'fontWeight': '700'}),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario_aderencia_sr() -> html.Div:
    return html.Div([
        html.Div([
            html.Button('Avaliar aderência S/R', id='btn-diario-aderencia-sr', n_clicks=0, style={
                'background': C['green'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
                'padding': '8px 20px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
                'fontWeight': '700', 'fontSize': '12px',
            }),
            html.Span(id='diario-aderencia-sr-status', style={
                'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2'],
            }),
        ], style={'marginBottom': '14px'}),
        html.Div(_tabela_aderencia_sr(persistencia.carregar_aderencia_sr()),
                 id='diario-aderencia-sr-tabela'),
    ])


# --- Padrões comportamentais ----------------------------------------------------
# Revenge trading / tilt cascade — ver `diario_trade.detectar_padroes_
# comportamentais`. Relatório de revisão manual, mesmo espírito da
# Aderência S/R: Jesué julga, o sistema não ajusta nada sozinho.

_COR_TIPO_PADRAO = {'revenge_trading': 'red', 'tilt_cascade': 'yellow'}


def _tabela_padroes(df: pd.DataFrame | None) -> html.Div:
    if df is None or df.empty:
        return html.Div('Nenhum resultado ainda — clique em "Detectar padrões".', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
        for t in ['Tipo', 'Data', 'Ativo', 'Trade(s)', 'Precisão', 'Detalhe']
    ])
    linhas = []
    for _, linha in df.iterrows():
        chave_cor = _COR_TIPO_PADRAO.get(linha['tipo'], 'text2')
        cor = C.get(chave_cor, C['text2'])
        estilo_precisao = {'color': C['text2']}
        if linha['precisao'] == 'dia':
            estilo_precisao['fontStyle'] = 'italic'
        linhas.append(html.Tr([
            _td_diario(linha['tipo'].replace('_', ' ').title(), extra={'color': cor, 'fontWeight': '700'}),
            _td_diario(str(linha['data_op'])),
            _td_diario(linha['ativo'] or '—'),
            _td_diario(f"#{linha['id_trade_anterior']}→#{linha['id_trade']}"),
            _td_diario(linha['precisao'], extra=estilo_precisao),
            _td_diario(linha['detalhe'], extra={'fontSize': '11px'}),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario_padroes() -> html.Div:
    return html.Div([
        html.Div([
            html.Button('Detectar padrões', id='btn-diario-padroes', n_clicks=0, style={
                'background': C['green'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
                'padding': '8px 20px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
                'fontWeight': '700', 'fontSize': '12px',
            }),
            html.Span(id='diario-padroes-status', style={
                'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2'],
            }),
        ], style={'marginBottom': '14px'}),
        html.Div(_tabela_padroes(persistencia.carregar_padroes_comportamentais()),
                 id='diario-padroes-tabela'),
    ])


# =============================================================================
# APP
# =============================================================================

app = dash.Dash(__name__, suppress_callback_exceptions=True)

_PADRAO_NOME_IMAGEM_DIARIO = re.compile(r'^(\d{4}_[A-Z]+_\d{8}|rascunho_\d+_\d{2})\.png$')


@app.server.route('/diario-imagens/<path:nome_arquivo>')
def _servir_imagem_diario(nome_arquivo):
    if not _PADRAO_NOME_IMAGEM_DIARIO.match(nome_arquivo):
        flask.abort(404)
    return flask.send_from_directory(cfg.PASTA_DIARIO_IMAGENS, nome_arquivo)


app.layout = html.Div([
    html.Link(rel='stylesheet',
              href='https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500&display=swap'),

    dcc.Store(id='store-diario-sub-aba', data='novo'),
    html.Div([
        html.Button(nome, id=f'btn-diario-sub-{chave}', n_clicks=0,
                    style=_estilo_subaba_diario(chave, 'novo'))
        for chave, nome in _ABAS_DIARIO_SUB
    ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '16px'}),

    html.Div(id='diario-sub-aba-conteudo'),
], style={
    'padding': '8px', 'fontFamily': FONTS['body'], 'background': C['bg'],
    'minHeight': '100vh', 'boxSizing': 'border-box',
})


@app.callback(
    Output('store-diario-sub-aba', 'data'),
    [Input(f'btn-diario-sub-{chave}', 'n_clicks') for chave, _ in _ABAS_DIARIO_SUB],
    prevent_initial_call=True,
)
def selecionar_sub_aba_diario(*_n_clicks):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    botao_id = ctx.triggered[0]['prop_id'].split('.')[0]
    return botao_id.replace('btn-diario-sub-', '')


@app.callback(
    [Output(f'btn-diario-sub-{chave}', 'style') for chave, _ in _ABAS_DIARIO_SUB],
    Input('store-diario-sub-aba', 'data'),
)
def atualizar_estilo_sub_aba_diario(sub_aba_ativa):
    return [_estilo_subaba_diario(chave, sub_aba_ativa) for chave, _ in _ABAS_DIARIO_SUB]


_DIARIO_SUBABA_CONSTRUTORES = {
    'novo': _construir_diario_novo_trade,
    'posicao': _construir_diario_em_andamento,
    'historico': _construir_diario_historico,
    'estatisticas': lambda: html.Div(id='diario-estatisticas-conteudo'),
    'reconciliacao': _construir_diario_reconciliacao,
    'aderencia_sr': _construir_diario_aderencia_sr,
    'padroes': _construir_diario_padroes,
}


@app.callback(
    Output('diario-sub-aba-conteudo', 'children'),
    Input('store-diario-sub-aba', 'data'),
)
def montar_sub_aba_diario(sub_aba_ativa):
    """Monta só a sub-aba ativa por vez — mesma razão de
    `diario_app_98.py::montar_aba_diario98`."""
    construtor = _DIARIO_SUBABA_CONSTRUTORES.get(sub_aba_ativa, _construir_diario_novo_trade)
    return construtor()


@app.callback(
    Output('diario-preview-imagem', 'children'),
    Input('diario-upload-imagem', 'contents'),
    Input('store-diario-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def preview_imagem_diario(upload_contents, imagem_colada):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    data_url = upload_contents if origem == 'diario-upload-imagem' else imagem_colada
    if not data_url:
        return no_update
    return html.Img(src=data_url, style={'maxWidth': '240px', 'borderRadius': '6px',
                                          'border': f'1px solid {C["border"]}'})


@app.callback(
    Output('diario-status', 'children'),
    Input('btn-diario-salvar', 'n_clicks'),
    State('diario-data', 'value'),
    State('diario-ativo', 'value'),
    State('diario-janela', 'value'),
    State('diario-estrategia', 'value'),
    State('diario-sala', 'value'),
    State('diario-tipo-operacao', 'value'),
    State('diario-alvo', 'value'),
    State('diario-stop', 'value'),
    State('diario-lote', 'value'),
    State('diario-preco-entrada', 'value'),
    State('diario-preco-saida', 'value'),
    State('diario-tipo-saida', 'value'),
    State('diario-resultado', 'value'),
    State('diario-saldo', 'value'),
    State('diario-resumo', 'value'),
    State('diario-obs', 'value'),
    State('diario-nivel-sr', 'value'),
    State('diario-nivel-sr-tipo', 'value'),
    State('diario-tags-setup-erro', 'value'),
    State('diario-upload-imagem', 'contents'),
    State('store-diario-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def salvar_novo_trade(n_clicks, data_op, ativo, janela, estrategia_tags, sala, tipo_op, alvo, stop, lote,
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
    # compartilha memória com dashboard.py — lê os candles frescos do disco
    # em vez do `estado['bases']` em memória (diferença de até ~30s,
    # irrelevante pra este propósito). Escopo: só CAPTURA — comparação
    # contra nivel_sr_referencia fica pra um ciclo futuro.
    df_ativo = persistencia.carregar_candles(ativo)
    m = extrair_metricas(df_ativo) if df_ativo is not None else {}
    snapshot_mercado = montar_snapshot_diario(m) if m else None

    id_trade = dt.registrar_trade(campos, imagem_bytes=imagem_bytes, snapshot_mercado=snapshot_mercado)
    if id_trade is None:
        return '⚠ Diário ocupado por outra escrita — tente novamente em instantes.'
    return f'✔ Operação #{id_trade} salva.'


@app.callback(
    Output('diario-data', 'value'),
    Output('diario-ativo', 'value'),
    Output('diario-janela', 'value'),
    Output('diario-estrategia', 'value'),
    Output('diario-sala', 'value'),
    Output('diario-tipo-operacao', 'value'),
    Output('diario-alvo', 'value'),
    Output('diario-stop', 'value'),
    Output('diario-lote', 'value'),
    Output('diario-preco-entrada', 'value'),
    Output('diario-preco-saida', 'value'),
    Output('diario-tipo-saida', 'value'),
    Output('diario-resultado', 'value'),
    Output('diario-saldo', 'value'),
    Output('diario-resumo', 'value'),
    Output('diario-obs', 'value'),
    Output('diario-nivel-sr', 'value'),
    Output('diario-nivel-sr-tipo', 'value'),
    Output('diario-tags-setup-erro', 'value'),
    Output('diario-preview-imagem', 'children', allow_duplicate=True),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-diario-limpar', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_form_diario(n_clicks):
    return (None, None, None, None, None, None, None, None, 1,
            None, None, None, None, None, None, None, None, None, None,
            None, None)


@app.callback(
    Output('diario-historico-tabela', 'children'),
    Input('diario-hist-ativo', 'value'),
    Input('diario-hist-janela', 'value'),
    Input('diario-hist-estrategia', 'value'),
)
def atualizar_historico(ativo, janela, estrategia):
    df = dt.listar_trades(ativo=ativo, janela=janela, estrategia_tag=estrategia, is_simulated=False)
    df = df.sort_values('data_op', ascending=False).head(50)
    if df.empty:
        return html.Div('Nenhuma operação encontrada.', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
        for t in ['Data', 'Ativo', 'Janela', 'Estratégia', 'Tipo', 'Pts', 'Saldo', 'Print']
    ])
    linhas = [_linha_historico(row) for _, row in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


@app.callback(
    Output('diario-estatisticas-conteudo', 'children'),
    Input('store-diario-sub-aba', 'data'),
)
def atualizar_estatisticas(sub_aba_ativa):
    if sub_aba_ativa != 'estatisticas':
        return no_update
    return _construir_diario_estatisticas(dt.calcular_estatisticas(), dt.calcular_score_confiabilidade())


@app.callback(
    Output('diario-calendario-mes', 'data'),
    Input('btn-diario-cal-prev', 'n_clicks'),
    Input('btn-diario-cal-next', 'n_clicks'),
    State('diario-calendario-mes', 'data'),
    prevent_initial_call=True,
)
def navegar_calendario_classico(n_prev, n_next, mes_atual):
    ctx = callback_context
    if not ctx.triggered or not mes_atual:
        return no_update
    ano, mes = (int(x) for x in mes_atual.split('-'))
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    mes += -1 if origem == 'btn-diario-cal-prev' else 1
    if mes == 0:
        mes, ano = 12, ano - 1
    elif mes == 13:
        mes, ano = 1, ano + 1
    return f"{ano:04d}-{mes:02d}"


@app.callback(
    Output('diario-calendario-grid', 'children'),
    Output('diario-calendario-label', 'children'),
    Input('diario-calendario-mes', 'data'),
    Input('diario-por-dia-store', 'data'),
)
def atualizar_grade_calendario_classico(mes_atual, por_dia):
    """Redesenha a grade a partir do `por_dia` já carregado no Store — mesma
    razão de `diario_app_98.py::atualizar_grade_calendario_98`."""
    if not mes_atual:
        return no_update, no_update
    ano, mes = (int(x) for x in mes_atual.split('-'))
    return _grade_calendario(por_dia or {}, ano, mes), f"{_NOMES_MES[mes]} {ano}"


@app.callback(
    Output('diario-reconciliacao-tabela', 'children'),
    Output('diario-reconciliar-status', 'children'),
    Input('btn-diario-reconciliar', 'n_clicks'),
    prevent_initial_call=True,
)
def reconciliar_classico(n_clicks):
    try:
        resultado = dt.reconciliar_com_mt5(dias_atras=30)
    except Exception as e:
        return no_update, f'⚠ Erro ao reconciliar: {str(e)[:100]}'
    return _tabela_reconciliacao(resultado), f'Concluído — {len(resultado)} linhas.'


@app.callback(
    Output('diario-aderencia-sr-tabela', 'children'),
    Output('diario-aderencia-sr-status', 'children'),
    Input('btn-diario-aderencia-sr', 'n_clicks'),
    prevent_initial_call=True,
)
def avaliar_aderencia_sr_classico(n_clicks):
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
    return _tabela_aderencia_sr(resultado), status


@app.callback(
    Output('diario-padroes-tabela', 'children'),
    Output('diario-padroes-status', 'children'),
    Input('btn-diario-padroes', 'n_clicks'),
    prevent_initial_call=True,
)
def detectar_padroes_classico(n_clicks):
    try:
        resultado = dt.detectar_padroes_comportamentais()
    except Exception as e:
        return no_update, f'⚠ Erro ao detectar: {str(e)[:100]}'
    return _tabela_padroes(resultado), f'Concluído — {len(resultado)} padrões detectados.'


@app.callback(
    Output('em-andamento-conteudo', 'children'),
    Input('interval-em-andamento', 'n_intervals'),
    Input('store-diario-sub-aba', 'data'),
)
def atualizar_em_andamento(n, sub_aba_ativa):
    if sub_aba_ativa != 'posicao':
        return no_update
    return _renderizar_em_andamento(dt.listar_posicoes_abertas(), dt.listar_ordens_pendentes())


@app.callback(
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children'),
    Input({'type': 'ea-btn-salvar', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'ea-nota', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-estrategia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-sala', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-alvo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-stop', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-btn-salvar', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def salvar_em_andamento(n_clicks, nota, estrategia, sala, alvo, stop, id_componente):
    """Salva nota nova (sempre um append no log cronológico) + campos de
    classificação preliminar — funciona com a posição aberta ou já fechada/
    pendente, exatamente pra cobrir a análise que o Jesué faz durante E
    depois do trade (ver `diario_trade.atualizar_rascunho`)."""
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
    Output('em-andamento-preview-imagem', 'children'),
    Input('em-andamento-upload-imagem', 'contents'),
    Input('store-diario-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def preview_imagem_em_andamento(upload_contents, imagem_colada):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    data_url = upload_contents if origem == 'em-andamento-upload-imagem' else imagem_colada
    if not data_url:
        return no_update
    return html.Img(src=data_url, style={'maxWidth': '200px', 'borderRadius': '6px',
                                          'border': f'1px solid {C["border"]}'})


@app.callback(
    Output('em-andamento-anexar-status', 'children'),
    Input('btn-em-andamento-anexar', 'n_clicks'),
    State('em-andamento-select-posicao', 'value'),
    State('em-andamento-upload-imagem', 'contents'),
    State('store-diario-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def anexar_imagem_em_andamento(n_clicks, id_posicao, upload_contents, imagem_colada):
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
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Input({'type': 'ea-btn-finalizar', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'ea-estrategia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-sala', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-tipo-saida', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-tags-erro', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-imagem-principal', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-btn-finalizar', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def finalizar_em_andamento(n_clicks, estrategia, sala, tipo_saida, tags_erro, imagem_principal, id_componente):
    """Encerra o rascunho e grava o trade definitivo — chama
    `diario_trade.finalizar_posicao`, que por sua vez chama o
    `registrar_trade` já existente sem alterá-lo. A lista some da tela no
    próximo refresh do `interval-em-andamento` (até 6s) ou ao trocar de aba."""
    # Só entra no dict quem tem valor de verdade — se o usuário não mexeu
    # num campo (None), o default `*_preliminar` já escrito durante a
    # operação (aplicado dentro de `finalizar_posicao`) não é sobrescrito
    # por engano com uma lista/valor vazio.
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
    print("  Diário de Trade — processo clássico (porta 8053)")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=PORTA_PROPRIA, threaded=True)
