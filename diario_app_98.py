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

import json

import dash
from dash import ALL, dcc, html, Input, Output, State, callback_context, no_update
import flask
import numpy as np
import pandas as pd
import plotly.graph_objects as go

import config_backtest as cfg
import persistencia
import diario_trade as dt
import gerenciador_processos as gp
from metricas import extrair_metricas, montar_snapshot_diario
from estilo_win98 import C, FONTS, win98_button, win98_field, win98_groupbox, win98_sunken, texto_sobre

PORTA_PROPRIA = 8052
PORTA_PAI = 8051  # dashboard_98.py

_ABAS_DIARIO98 = [
    ('estatisticas', 'Estatísticas'),
    ('posicao', 'Posição'),
    ('historico', 'Histórico'),
    ('estrategias', 'Estratégias'),
    ('novo', 'Novo Trade'),
    ('padroes', 'Padrões'),
    ('bem_estar', 'Bem-Estar'),
]
_ABA_DIARIO98_INICIAL = 'estatisticas'


def _opcoes_diario(valores: list[str]) -> list[dict]:
    """Converte vocabulário canônico (snake_case) em opções de dropdown legíveis."""
    return [{'label': v.replace('_', ' ').title(), 'value': v} for v in valores]


def _opcoes_estrategias() -> list[dict]:
    """Opções de estratégia lidas do cadastro editável (aba Estratégias),
    não mais da lista fixa — refletem criação/desativação na hora."""
    return [{'label': linha['nome'], 'value': linha['slug']} for _, linha in dt.listar_estrategias().iterrows()]


def _opcoes_tags_setup_erro() -> list[dict]:
    return [{'label': linha['nome'], 'value': linha['slug']} for _, linha in dt.listar_tags_setup_erro().iterrows()]


def _opcoes_tags_emocionais_98() -> list[dict]:
    return [{'label': linha['nome'], 'value': linha['slug']} for _, linha in dt.listar_tags_emocionais().iterrows()]


def _campo_diario98(label: str, componente) -> html.Div:
    return html.Div([
        html.Div(label, style={'fontSize': '10px', 'color': C['text2'],
                                'marginBottom': '2px', 'fontFamily': FONTS['display']}),
        componente,
    ], style={'marginBottom': '8px'})


_MARCAS_SENTIMENTO_MERCADO = {0: 'Medo Extremo', 25: 'Medo', 50: 'Neutro', 75: 'Ganância', 100: 'Ganância Extrema'}


def _num_estrela_ou_padrao_98(valor, padrao: int = 3) -> int:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return padrao
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def _estrela_rating_98(id_componente, valor: int = 3) -> html.Div:
    """Nota 1-5 clicável (5 estrelas de verdade) — ver
    diario_app_classico.py::_estrela_rating pro mesmo raciocínio completo
    (feedback 2026-07-11: "muito feio, coloque apenas as estrelas... como
    tem normalmente nos apps", substitui o dcc.Slider com marks de ★).
    Mantém a MESMA interface externa da função antiga — um componente com
    id=id_componente expondo `.value` via dcc.Input(type='hidden') — então
    nenhum callback existente precisa mudar. `id_componente` aceita string
    (campo único) ou dict pattern-matched (ea98-emo-* na Posição)."""
    valor = valor or 0
    if isinstance(id_componente, str):
        ids_estrelas = [f'{id_componente}-estrela-{i}' for i in range(1, 6)]
    else:
        ids_estrelas = [{**id_componente, 'type': f"{id_componente['type']}-estrela", 'n': i}
                         for i in range(1, 6)]
    estrelas = html.Div([
        html.Span('', id=ids_estrelas[i - 1], n_clicks=0,
                  className='diario98-estrela' + (' ativa' if i <= valor else ''))
        for i in range(1, 6)
    ], style={'display': 'flex', 'gap': '2px'})
    return html.Div([estrelas, dcc.Input(id=id_componente, type='hidden', value=valor)])


def _registrar_clique_estrela_98(app, id_campo: str):
    """Ver diario_app_classico.py::_registrar_clique_estrela pro mesmo
    raciocínio (par de clientside_callbacks: clique → valor, valor →
    visual, pra um campo SINGLETON — Novo Trade/Bem-Estar)."""
    app.clientside_callback(
        """
        function(...cliques) {
            const ctx = dash_clientside.callback_context;
            if (!ctx.triggered.length) { return dash_clientside.no_update; }
            const m = ctx.triggered[0].prop_id.match(/-estrela-(\\d+)\\.n_clicks$/);
            return m ? parseInt(m[1], 10) : dash_clientside.no_update;
        }
        """,
        Output(id_campo, 'value'),
        [Input(f'{id_campo}-estrela-{i}', 'n_clicks') for i in range(1, 6)],
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function(valor) {
            const v = valor || 0;
            return [1, 2, 3, 4, 5].map(i => 'diario98-estrela' + (i <= v ? ' ativa' : ''));
        }
        """,
        [Output(f'{id_campo}-estrela-{i}', 'className') for i in range(1, 6)],
        Input(id_campo, 'value'),
    )


def _registrar_clique_estrela_match_98(app, tipo_campo: str):
    """Ver diario_app_classico.py::_registrar_clique_estrela_match pro
    mesmo raciocínio (MATCH+ALL, cobre qualquer posição aberta
    dinamicamente na aba Posição — ea98-emo-confianca etc)."""
    app.clientside_callback(
        """
        function(cliques_lista) {
            const triggered = dash_clientside.callback_context.triggered_id;
            return triggered ? triggered.n : dash_clientside.no_update;
        }
        """,
        Output({'type': tipo_campo, 'index': dash.MATCH}, 'value'),
        Input({'type': f'{tipo_campo}-estrela', 'index': dash.MATCH, 'n': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function(valor) {
            const v = valor || 0;
            return [1, 2, 3, 4, 5].map(i => 'diario98-estrela' + (i <= v ? ' ativa' : ''));
        }
        """,
        [Output({'type': f'{tipo_campo}-estrela', 'index': dash.MATCH, 'n': i}, 'className') for i in range(1, 6)],
        Input({'type': tipo_campo, 'index': dash.MATCH}, 'value'),
    )


def _bloco_emocional_98(id_confianca, id_humor, id_energia, id_atencao, id_presenca,
                         id_tags, id_sentimento, id_label_sentimento, valores: dict | None = None) -> html.Div:
    """Autoavaliação emocional (Confiança/Humor/Energia/Atenção/Presença 1-5,
    tags de Print Emocional, Sentimento de Mercado 0-100) — fundação do
    módulo emocional/comportamental pedida no feedback diário.txt
    (2026-07-11). Reaproveitado em Novo Trade (ids fixos, sem `valores` —
    sempre começa neutro) e Posição (ids pattern-matched por posição,
    `valores` vem do rascunho já salvo) — por isso ids e valores iniciais
    vêm por parâmetro."""
    campo = _campo_diario98
    valores = valores or {}
    sentimento_valor = valores.get('sentimento')
    sentimento_valor = int(sentimento_valor) if sentimento_valor is not None and not (
        isinstance(sentimento_valor, float) and pd.isna(sentimento_valor)) else 50
    return win98_groupbox('Autoavaliação Emocional', html.Div([
        html.Div([
            campo('Confiança', _estrela_rating_98(id_confianca, _num_estrela_ou_padrao_98(valores.get('confianca')))),
            campo('Humor', _estrela_rating_98(id_humor, _num_estrela_ou_padrao_98(valores.get('humor')))),
            campo('Energia', _estrela_rating_98(id_energia, _num_estrela_ou_padrao_98(valores.get('energia')))),
            campo('Atenção', _estrela_rating_98(id_atencao, _num_estrela_ou_padrao_98(valores.get('atencao')))),
            campo('Presença', _estrela_rating_98(id_presenca, _num_estrela_ou_padrao_98(valores.get('presenca')))),
        ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(150px, 1fr))', 'gap': '6px 16px'}),
        campo('Print Emocional (tags)', dcc.Dropdown(id=id_tags, multi=True,
                                                        options=_opcoes_tags_emocionais_98(),
                                                        value=_lista_segura_98(valores.get('tags')) or None,
                                                        className='win98-dropdown win98-dropdown-tags')),
        html.Div([
            html.Div('Sentimento de Mercado', style={'fontSize': '10px', 'color': C['text2'], 'marginBottom': '4px'}),
            dcc.Slider(id=id_sentimento, min=0, max=100, step=1, value=sentimento_valor, marks=_MARCAS_SENTIMENTO_MERCADO),
            html.Div(id=id_label_sentimento, style={'fontSize': '11px', 'marginTop': '4px'}),
        ], style={'marginTop': '10px'}),
    ]))


def _construir_diario98_novo_trade() -> html.Div:
    campo = _campo_diario98
    # Grid ao invés de flex+largura fixa por campo (era o motivo do espaço
    # vazio à direita em telas largas, ver feedback diário.txt) — cada campo
    # ocupa uma coluna de no mínimo 130px e estica pra preencher a largura
    # disponível.
    grade = lambda *filhos: html.Div(list(filhos), style={
        'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(130px, 1fr))',
        'gap': '10px 14px', 'alignItems': 'end',
    })
    largura_total = {'width': '100%'}

    return html.Div([
        win98_groupbox('Dados da Operação', html.Div([
            grade(
                campo('Data', dcc.Input(id='diario98-data', type='date', style={**win98_field(), **largura_total})),
                campo('Ativo', dcc.Dropdown(id='diario98-ativo', options=_opcoes_diario(cfg.ATIVOS),
                                             clearable=False, className='win98-dropdown', style=largura_total)),
                campo('Janela', dcc.Dropdown(id='diario98-janela',
                                              options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                                              className='win98-dropdown', style=largura_total)),
                campo('Estratégia', dcc.Dropdown(id='diario98-estrategia', multi=True,
                                                  options=_opcoes_estrategias(),
                                                  className='win98-dropdown win98-dropdown-tags', style=largura_total)),
                campo('Sala/Análise', dcc.Dropdown(id='diario98-sala',
                                                     options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
                                                     className='win98-dropdown', style=largura_total)),
                campo('Tipo', dcc.Dropdown(id='diario98-tipo-operacao', options=_opcoes_diario(dt.TIPO_OPERACAO),
                                            clearable=False, className='win98-dropdown', style=largura_total)),
                campo('R:R', dcc.Dropdown(id='diario98-rr-preset',
                                           options=[{'label': l['nome'], 'value': l['id']}
                                                    for _, l in dt.listar_rr_presets().iterrows()],
                                           placeholder='nenhuma', className='win98-dropdown', style=largura_total)),
                campo('Alvo (pts)', dcc.Input(id='diario98-alvo', type='text', inputMode='decimal',
                                               style={**win98_field(), **largura_total})),
                campo('Stop (pts)', dcc.Input(id='diario98-stop', type='text', inputMode='decimal',
                                               style={**win98_field(), **largura_total})),
                campo('Lote', dcc.Input(id='diario98-lote', type='number', value=1,
                                         style={**win98_field(), **largura_total})),
                campo('Preço Entrada', dcc.Input(id='diario98-preco-entrada', type='text', inputMode='decimal',
                                                  style={**win98_field(), **largura_total})),
                campo('Preço Saída', dcc.Input(id='diario98-preco-saida', type='text', inputMode='decimal',
                                                style={**win98_field(), **largura_total})),
                campo('Tipo Saída', dcc.Dropdown(id='diario98-tipo-saida', options=_opcoes_diario(dt.TIPO_SAIDA),
                                                  className='win98-dropdown', style=largura_total)),
                campo('Resultado (pts)', dcc.Input(id='diario98-resultado', type='text', inputMode='decimal',
                                                    style={**win98_field(), **largura_total})),
                campo('Saldo (R$)', dcc.Input(id='diario98-saldo', type='text', inputMode='decimal',
                                               style={**win98_field(), **largura_total})),
            ),
        ])),

        win98_groupbox('Nível de S/R referenciado (opcional)', html.Div([
            grade(
                campo('Preço', dcc.Input(id='diario98-nivel-sr', type='text', inputMode='decimal',
                                          style={**win98_field(), **largura_total})),
                campo('Tipo', dcc.Input(id='diario98-nivel-sr-tipo', type='text', placeholder='ex: suporte',
                                         style={**win98_field(), **largura_total})),
            ),
        ])),

        win98_groupbox('Anotações', html.Div([
            campo('Resumo Operacional', dcc.Textarea(id='diario98-resumo', rows=3,
                                                       style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            campo('Obs (autocoaching)', dcc.Textarea(id='diario98-obs', rows=2,
                                                       style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            campo('Tags de Setup/Erro', dcc.Dropdown(id='diario98-tags-setup-erro', multi=True,
                                                       options=_opcoes_tags_setup_erro(),
                                                       className='win98-dropdown win98-dropdown-tags')),
        ])),

        _bloco_emocional_98(
            id_confianca='diario98-emo-confianca', id_humor='diario98-emo-humor',
            id_energia='diario98-emo-energia', id_atencao='diario98-emo-atencao',
            id_presenca='diario98-emo-presenca', id_tags='diario98-tags-emocionais',
            id_sentimento='diario98-sentimento-mercado', id_label_sentimento='diario98-sentimento-label',
        ),

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


def _num(valor):
    """Ver diario_app_classico.py::_num pro mesmo raciocínio (converte o
    `value` de um <input type=text inputMode=decimal> pra float; aceita
    vírgula decimal; None se vazio/inválido)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace(',', '.')
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        return None


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


def _slugificar_tag_98(nome: str) -> str:
    """Ver diario_app_classico.py::_slugificar_tag pro mesmo raciocínio
    (mesmo algoritmo de `diario_trade._slugificar`, duplicado só pra
    decidir o `value` de um chip ainda não persistido)."""
    import re
    import unicodedata
    sem_acento = unicodedata.normalize('NFKD', nome.strip().lower())
    sem_acento = ''.join(c for c in sem_acento if not unicodedata.combining(c))
    s = re.sub(r'[\s\-]+', '_', sem_acento)
    s = re.sub(r'[^a-z0-9_]', '', s)
    return s or 'sem_nome'


def _parse_index_par_98(index):
    """Ver diario_app_classico.py::_parse_index_par — mesma lógica,
    duplicada (não compartilhada) porque os dois apps são processos
    independentes."""
    texto = str(index)
    partes = texto.split('-')
    if len(partes) != 2:
        return None
    try:
        return int(partes[0]), int(partes[1])
    except ValueError:
        return None


def _ticket_alvo_98(index):
    """Ver diario_app_classico.py::_ticket_alvo."""
    par = _parse_index_par_98(index)
    return par[0] if par else index


def _fmt_num_98(valor, sinal=False, casas=0) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return '—'
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return '—'
    formato = f"{{:+.{casas}f}}" if sinal else f"{{:.{casas}f}}"
    return formato.format(v)


def _estilo_thumb_imagem_principal_98(selecionada: bool) -> dict:
    return {
        'width': '120px', 'height': '80px', 'objectFit': 'cover',
        'cursor': 'pointer', 'display': 'block',
        'border': f"3px solid {C['gold'] if selecionada else C['shadow_dk']}",
        'opacity': '1' if selecionada else '0.7',
    }


def _campo_imagem_principal_98(id_pos, imagens: list) -> html.Div | None:
    """Ver diario_app_classico.py::_campo_imagem_principal pro mesmo
    raciocínio (miniaturas clicáveis maiores, default = última imagem)."""
    if not imagens:
        return None
    selecionada = imagens[-1]
    thumbs = [
        html.Img(
            src=f'/diario-imagens/{nome}',
            id={'type': 'ea98-imagem-thumb-escolher', 'index': id_pos, 'nome': nome},
            n_clicks=0,
            style=_estilo_thumb_imagem_principal_98(nome == selecionada),
        )
        for nome in imagens
    ]
    return html.Div([
        html.Div('Imagem principal (clique pra escolher a capa do trade)', style={
            'fontSize': '10px', 'color': C['text2'], 'marginBottom': '4px', 'fontFamily': FONTS['display']}),
        html.Div(thumbs, style={'display': 'flex', 'gap': '8px', 'flexWrap': 'wrap'}),
        dcc.Store(id={'type': 'ea98-imagem-principal', 'index': id_pos}, data=selecionada),
    ])


def _bloco_finalizar_em_andamento_98(pos: dict, imagens: list) -> html.Div:
    campo = _campo_diario98
    id_pos = pos['id_posicao']
    saida_estimada = bool(pos.get('saida_estimada'))
    # Tipo de saída não é mais escolhido à mão (feedback diário.txt,
    # 2026-07-11: "isso será feito na plataforma de operação") — só um
    # preview do que `dt.derivar_tipo_saida` vai gravar, comparando o
    # preço de saída do MT5 contra sl/tp/entrada.
    tipo_saida_previsto = dt.derivar_tipo_saida(
        pos.get('tipo_operacao'), pos.get('preco_entrada'), pos.get('preco_saida'),
        pos.get('sl'), pos.get('tp'),
    )
    # Campos de correção de saída SEMPRE renderizados (não só quando
    # `saida_estimada` — evita depender de um componente pattern-matched
    # que só existe condicionalmente pra ALGUNS índices, o que quebraria o
    # agrupamento de dash.MATCH pros outros cards). O aviso, esse sim, só
    # aparece quando a corretora não confirmou o fechamento real
    # (`sincronizar_posicoes_abertas` caiu no fallback — ver
    # diario_trade.py; bug confirmado em 2026-07-12, 8 posições de 10/07
    # com esse sintoma). "Finalizar" sempre chama `corrigir_saida_rascunho`
    # com o que estiver nesses campos antes de gravar no Diário — nunca
    # deixa passar o valor fabricado sem o usuário ao menos conferir.
    aviso_saida = html.Div(
        '⚠ Saída NÃO confirmada pela corretora — o valor abaixo é uma estimativa. '
        'Confira/corrija antes de finalizar.',
        style={'fontSize': '10px', 'color': C['amber'], 'fontWeight': '700', 'marginBottom': '6px'},
    ) if saida_estimada else None
    campos_correcao_saida = html.Div([
        campo('Preço de Saída (confira/corrija)', dcc.Input(
            id={'type': 'ea98-preco-saida-correcao', 'index': id_pos}, type='text', inputMode='decimal',
            value=pos.get('preco_saida'), style=win98_field())),
        campo('Data/Hora de Saída (confira/corrija, AAAA-MM-DD HH:MM)', dcc.Input(
            id={'type': 'ea98-datahora-saida-correcao', 'index': id_pos}, type='text',
            value=str(pos.get('datetime_saida'))[:16] if pos.get('datetime_saida') else '',
            style=win98_field())),
    ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap', 'marginBottom': '6px'})
    aviso_e_correcao = ([aviso_saida] if aviso_saida is not None else []) + [campos_correcao_saida]
    return html.Div([
        html.Div('Finalizar operação', style={
            'fontSize': '10px', 'color': C['gold'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'marginTop': '10px', 'marginBottom': '4px',
        }),
        html.Div(f'Tipo de saída (calculado a partir do MT5): {tipo_saida_previsto}', style={
            'fontSize': '10px', 'color': C['text2'], 'marginBottom': '6px',
        }),
    ] + aviso_e_correcao + [
        campo('Tags Setup/Erro', dcc.Dropdown(id={'type': 'ea98-tags-erro', 'index': id_pos}, multi=True,
              options=_opcoes_tags_setup_erro(),
              className='win98-dropdown win98-dropdown-tags', style={'width': '240px'})),
    ] + ([_campo_imagem_principal_98(id_pos, imagens)] if imagens else []) + [
        html.Button('Finalizar e gravar no Diário', id={'type': 'ea98-btn-finalizar', 'index': id_pos},
                    n_clicks=0, className='win98-btn', style=win98_button(extra={'marginTop': '8px'})),
    ])


def _bloco_posicao_live_98(pos: dict) -> html.Div:
    """Parte SÓ-LEITURA do card (preço/P&L/notas já salvas) — ver
    diario_app_classico.py::_bloco_posicao_live pro mesmo raciocínio
    completo (reconstruída a cada 6s pelo interval, deliberadamente sem
    nenhum campo editável — é o que corrige o "Neutro piscando" de
    2026-07-11: um dcc.Slider/Input reconstruído com `value=` fresco do
    disco a cada 6s sobrescrevia qualquer edição não salva)."""
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

    par_hedge = bool(pos.get('par_hedge'))
    preco_ref = pos.get('preco_saida') if pendente else pos.get('preco_atual')
    if pendente:
        label_ref = 'Saída (⚠ estimada)' if pos.get('saida_estimada') else 'Saída'
    else:
        label_ref = 'Atual'
    pnl_pts = pos.get('pnl_pontos_final') if pendente else pos.get('pnl_pontos')
    pnl_reais = pos.get('pnl_reais_final') if pendente else pos.get('pnl_reais')
    # Achado real 2026-07-15 (ver diario_app_classico.py::_bloco_posicao_live
    # pro mesmo raciocínio completo): "AGUARDANDO CONFIRMAÇÃO" é errado pra
    # um par de hedge — ali a saída É confirmada (preço real da posição
    # oposta que fechou o par), só não veio de uma perna OUT no mesmo ticket.
    if pendente and par_hedge:
        status_txt, status_cor = ' ✓ FECHADA (par hedge — pronta pra finalizar)', C['green']
    elif pendente:
        status_txt, status_cor = ' ⚠ FECHADA — AGUARDANDO CONFIRMAÇÃO', C['amber']
    else:
        status_txt, status_cor = ' ● ABERTA', C['green']
    detalhe = html.Div([
        html.Span((pos.get('tipo_operacao') or '—').upper(), style={
            'color': cor_direcao, 'fontSize': '10px', 'fontWeight': '700'}),
        html.Span(status_txt, style={
            'marginLeft': '6px', 'fontSize': '9px', 'fontWeight': '700', 'color': status_cor}),
        html.Div(
            f"Entrada {_fmt_num_98(pos.get('preco_entrada'))} · {label_ref} {_fmt_num_98(preco_ref)} · "
            f"Pts {_fmt_num_98(pnl_pts, sinal=True)} · R$ {_fmt_num_98(pnl_reais, sinal=True, casas=2)}",
            style={'fontSize': '10px', 'color': C['text2'], 'fontFamily': FONTS['mono'], 'marginTop': '2px'}
        ),
    ])

    filhos = [
        detalhe,
        html.Div('Notas:', style={'fontSize': '9px', 'color': C['text2'], 'marginTop': '8px'}),
        timeline,
    ]
    if galeria:
        filhos.append(galeria)
    return win98_groupbox(f"{pos.get('ativo', '—')} · {pos.get('contrato', '—')}", html.Div(filhos))


def _bloco_posicao_form_98(pos: dict, imagens: list) -> html.Div:
    """Parte EDITÁVEL (classificação/estrelas/sentimento/resumo/nota nova)
    — ver diario_app_classico.py::_bloco_posicao_form pro mesmo raciocínio
    (Output separado, nunca tocado pelo interval de 6s)."""
    campo = _campo_diario98
    id_pos = pos['id_posicao']
    pendente = pos.get('status') == dt.STATUS_POSICAO_FECHADA_PENDENTE

    campos_classificacao = html.Div([
        campo('Estratégia', dcc.Dropdown(
            id={'type': 'ea98-estrategia', 'index': id_pos}, multi=True,
            options=_opcoes_estrategias(),
            value=_lista_segura_98(pos.get('estrategia_tags_preliminar')) or None,
            className='win98-dropdown win98-dropdown-tags', style={'width': '240px'})),
        campo('Sala/Análise', dcc.Dropdown(
            id={'type': 'ea98-sala', 'index': id_pos}, options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
            value=pos.get('sala_analise_preliminar'), className='win98-dropdown', style={'width': '160px'})),
        campo('R:R', dcc.Dropdown(
            id={'type': 'ea98-rr-preset', 'index': id_pos},
            options=[{'label': l['nome'], 'value': l['id']} for _, l in dt.listar_rr_presets().iterrows()],
            placeholder='nenhuma', className='win98-dropdown', style={'width': '140px'})),
        # Feedback 2026-07-20: "não cabe nem 3 dígitos" — causa raiz real:
        # `win98_field()` não define `width` nenhum (diferente da versão
        # clássica, que já tinha 100px), então o container do dcc.Input
        # (`.dash-input-container { width:100% }`, Dash 4.x) colapsava pro
        # "shrink-to-fit" mínimo dentro do `display:flex` da linha — nunca
        # tinha largura de verdade. Agora explícito, igual ao clássico.
        campo('Alvo (pts)', dcc.Input(id={'type': 'ea98-alvo', 'index': id_pos}, type='text', inputMode='decimal',
              debounce=True, value=pos.get('alvo_pontos_preliminar'), style=win98_field(extra={'width': '90px'}))),
        campo('Stop (pts)', dcc.Input(id={'type': 'ea98-stop', 'index': id_pos}, type='text', inputMode='decimal',
              debounce=True, value=pos.get('stop_pontos_preliminar'), style=win98_field(extra={'width': '90px'}))),
        campo('R:R calculado', html.Div(
            id={'type': 'ea98-rr-calculado', 'index': id_pos},
            style={'fontSize': '12px', 'fontFamily': FONTS['mono'], 'color': C['text2'], 'paddingTop': '6px'})),
        campo('Nível S/R referenciado', dcc.Input(id={'type': 'ea98-nivel-sr', 'index': id_pos}, type='text', inputMode='decimal',
              debounce=True, value=pos.get('nivel_sr_referencia_preliminar'), style=win98_field(extra={'width': '140px'}))),
    ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap', 'marginTop': '8px'})

    resumo_input = campo('Resumo Operacional', dcc.Textarea(
        id={'type': 'ea98-resumo', 'index': id_pos}, rows=4,
        value=pos.get('resumo_operacional_preliminar'),
        style={**win98_field(), 'height': 'auto', 'resize': 'none'}))

    bloco_emocional = _bloco_emocional_98(
        id_confianca={'type': 'ea98-emo-confianca', 'index': id_pos},
        id_humor={'type': 'ea98-emo-humor', 'index': id_pos},
        id_energia={'type': 'ea98-emo-energia', 'index': id_pos},
        id_atencao={'type': 'ea98-emo-atencao', 'index': id_pos},
        id_presenca={'type': 'ea98-emo-presenca', 'index': id_pos},
        id_tags={'type': 'ea98-tags-emocionais', 'index': id_pos},
        id_sentimento={'type': 'ea98-sentimento-mercado', 'index': id_pos},
        id_label_sentimento={'type': 'ea98-sentimento-label', 'index': id_pos},
        valores={
            'confianca': pos.get('emo_confianca_preliminar'), 'humor': pos.get('emo_humor_preliminar'),
            'energia': pos.get('emo_energia_preliminar'), 'atencao': pos.get('emo_atencao_preliminar'),
            'presenca': pos.get('emo_presenca_preliminar'), 'tags': pos.get('tags_emocionais_preliminar'),
            'sentimento': pos.get('sentimento_mercado_preliminar'),
        },
    )

    # Feedback 2026-07-20 ("o sistema de salvamento é muito primitivo...
    # qualquer sistema consegue salvar tudo imediatamente"): NENHUM campo
    # deste formulário depende mais de um botão "Salvar" — a nota grava
    # sozinha ao perder foco (`n_blur`, ver `salvar_nota_em_andamento_98`)
    # e os demais campos auto-salvam a cada mudança (ver
    # `auto_salvar_campos_em_andamento_98`) — achado real: um trade fechado
    # (par hedge) apareceu "Pronta pra Finalizar" com tudo em branco porque
    # o usuário preencheu a classificação enquanto a posição ainda estava
    # `aberta` e nunca clicou em "Salvar" antes dela fechar — sem botão,
    # não tem como esquecer.
    nota_input = html.Div([
        dcc.Textarea(id={'type': 'ea98-nota', 'index': id_pos}, rows=2,
                     placeholder='Nova anotação (durante ou depois da operação)... (salva sozinha ao sair do campo)',
                     style={**win98_field(), 'height': 'auto', 'resize': 'none'}),
        html.Div(id={'type': 'ea98-status', 'index': id_pos}, style={
            'fontSize': '10px', 'color': C['text2'], 'marginTop': '4px'}),
    ], style={'marginTop': '8px'})

    # Campo de print embutido NESTE card (não mais um seletor+upload
    # compartilhado no rodapé da aba — feedback de 2026-07-12: "eu quero
    # que apareça o campo trade a trade, e não no final com seletor").
    # Feedback 2026-07-20: "não faz sentido eu adicionar a imagem, e depois
    # clicar no botão" (auto-anexa, ver `preview_e_anexar_imagem_em_
    # andamento_98`) E "eu não quero ter que apertar em um botão para
    # depois dar Ctrl+V" — o botão que "armava" o card pro próximo Ctrl+V
    # foi removido; `assets_98/diario_paste.js` arma sozinho o card mais
    # próximo de QUALQUER clique/foco num campo do card.
    imagem_bloco = html.Div([
        html.Div('Anexar print desta posição (cole com Ctrl+V ou envie um arquivo):', style={
            'fontSize': '10px', 'color': C['text2'], 'marginTop': '10px', 'marginBottom': '4px'}),
        dcc.Upload(
            id={'type': 'ea98-upload-imagem', 'index': id_pos},
            children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '10px'}),
            style={**win98_sunken(bg=C['window']), 'padding': '8px', 'textAlign': 'center', 'cursor': 'pointer'},
        ),
        html.Div(id={'type': 'ea98-preview-imagem', 'index': id_pos}, style={'marginTop': '6px'}),
    ], style={'marginTop': '8px'})

    filhos = [campos_classificacao, resumo_input, bloco_emocional, nota_input, imagem_bloco]
    if pendente:
        filhos.append(_bloco_finalizar_em_andamento_98(pos, imagens))

    return win98_groupbox(f"{pos.get('ativo', '—')} · {pos.get('contrato', '—')} — Classificação", html.Div(filhos))


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


def _preparar_registros_posicao_98(posicoes: pd.DataFrame) -> tuple[list, dict, dict]:
    """Ver diario_app_classico.py::_preparar_registros_posicao — mesma
    lógica, duplicada (processos independentes). Retorna
    (pares, por_ativo_abertas, por_ativo_prontas)."""
    if posicoes is None or posicoes.empty:
        return [], {}, {}
    pares = dt.detectar_pares_hedge(posicoes)
    tickets_pareados = {p['ticket_a'] for p in pares} | {p['ticket_b'] for p in pares}
    sinteticas = [dt.montar_posicao_sintetica_par(par, posicoes) for par in pares]
    avulsas = [p for p in posicoes.to_dict('records') if p['id_posicao'] not in tickets_pareados]
    avulsas_abertas = [p for p in avulsas if p.get('status') == dt.STATUS_POSICAO_ABERTA]
    avulsas_prontas = [p for p in avulsas if p.get('status') != dt.STATUS_POSICAO_ABERTA]

    por_ativo_abertas: dict[str, list] = {}
    for r in avulsas_abertas:
        por_ativo_abertas.setdefault(r.get('ativo') or '—', []).append(r)

    por_ativo_prontas: dict[str, list] = {}
    for r in sinteticas + avulsas_prontas:
        por_ativo_prontas.setdefault(r.get('ativo') or '—', []).append(r)

    return pares, por_ativo_abertas, por_ativo_prontas


def _secao_status_posicoes_98(posicoes: pd.DataFrame, pares: list) -> html.Div:
    """Ver diario_app_classico.py::_secao_status_posicoes."""
    total = 0 if posicoes is None or posicoes.empty else len(posicoes)
    n_win = 0 if not total else int((posicoes['ativo'] == 'WIN').sum())
    n_wdo = 0 if not total else int((posicoes['ativo'] == 'WDO').sum())
    linhas = [
        (f"{total} posição(ões)/ordem(ns) rastreada(s) no MT5 agora — {n_win} WIN, {n_wdo} WDO.", C['text2']),
        (f"{len(pares)} par(es) de hedge detectado(s) (SL/TP oposto virou posição nova) — "
         f"mesclado(s) automaticamente em 1 trade líquido cada." if pares
         else "Nenhum par de hedge detectado agora.", C['text2']),
    ]
    residual = dt.calcular_exposicao_residual(posicoes)
    sobras = {contrato: liquido for contrato, liquido in residual.items() if liquido != 0}
    if sobras:
        texto_sobras = ', '.join(f"{contrato}: {liquido:+.0f}" for contrato, liquido in sobras.items())
        linhas.append((f"⚠ Exposição líquida real (sem par no momento) — {texto_sobras}.", C['amber']))
    else:
        linhas.append(("Exposição líquida zerada em todos os contratos — nenhum risco de mercado residual.", C['text2']))
    return win98_groupbox('Status das Posições', html.Div([
        html.Div(t, style={'fontSize': '11px', 'color': cor, 'marginBottom': '4px'}) for t, cor in linhas
    ]))


def _registros_por_ativo_98(mapa: dict, ativo_selecionado: str) -> list:
    """Ver diario_app_classico.py::_registros_por_ativo."""
    registros = list(mapa.get(ativo_selecionado, []))
    for ativo_nome, lista in mapa.items():
        if ativo_nome not in ('WIN', 'WDO'):
            registros.extend(lista)
    return registros


def _bloco_posicao_completo_98(p: dict, imagens: list) -> html.Div:
    """Ver diario_app_classico.py::_bloco_posicao_completo pro mesmo
    raciocínio (info ao vivo + formulário empilhados, feedback 2026-07-20:
    "o formulário deve ficar logo abaixo do bloco do trade aberto")."""
    return html.Div([_bloco_posicao_live_98(p), _bloco_posicao_form_98(p, imagens)],
                     style={'marginBottom': '14px'})


def _ids_rastreados_98(registros_prontas: list, registros_abertas: list) -> list:
    """Ver diario_app_classico.py::_ids_rastreados."""
    return sorted(str(r['id_posicao']) for r in registros_prontas + registros_abertas)


def _renderizar_posicao_completa_98(posicoes: pd.DataFrame, ordens: pd.DataFrame | None = None,
                                     ativo_selecionado: str = 'WIN') -> html.Div:
    """Substitui `_renderizar_em_andamento_98` + `_renderizar_formularios_
    em_andamento_98` — ver diario_app_classico.py::
    _renderizar_posicao_completa pro raciocínio completo (feedback
    2026-07-20: info do trade "desconectada" do formulário + seção não
    aparecia sozinha quando um trade novo fechava)."""
    pares, por_ativo_abertas, por_ativo_prontas = _preparar_registros_posicao_98(posicoes)
    status = _secao_status_posicoes_98(posicoes, pares)

    registros_prontas = _registros_por_ativo_98(por_ativo_prontas, ativo_selecionado)
    registros_abertas = _registros_por_ativo_98(por_ativo_abertas, ativo_selecionado)

    ordens_ativo = ordens
    if ordens is not None and not ordens.empty:
        ordens_ativo = ordens[ordens['ativo'] == ativo_selecionado]
    secao_ordens = _secao_ordens_pendentes_98(ordens_ativo) if ordens_ativo is not None else None

    filhos = [status]
    if secao_ordens:
        filhos.append(secao_ordens)
    if registros_prontas:
        blocos_prontas = [_bloco_posicao_completo_98(p, _lista_segura_98(p.get('imagens_em_andamento')))
                          for p in registros_prontas]
        filhos.append(win98_groupbox(f'Pronta pra Finalizar — {ativo_selecionado} ({len(registros_prontas)})',
                                      html.Div(blocos_prontas)))
    if not registros_abertas:
        vazio = html.Div(f'Nenhuma posição {ativo_selecionado} aberta no momento.', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
        filhos.append(vazio)
    else:
        blocos = [_bloco_posicao_completo_98(p, _lista_segura_98(p.get('imagens_em_andamento')))
                  for p in registros_abertas]
        filhos.append(win98_groupbox(f'Posições {ativo_selecionado}', html.Div(blocos)))
    return html.Div(filhos)


def _construir_diario98_em_andamento() -> html.Div:
    return html.Div([
        # Sub-abas WIN/WDO (feedback 2026-07-16) — mesmo padrão de
        # Store+botões+callback de estilo das abas principais do Diário.
        dcc.Store(id='store-posicao98-ativo-subaba', data='WIN'),
        html.Div([
            html.Button(nome, id=f'btn-posicao98-ativo-{nome}', n_clicks=0,
                        className='win98-btn', style=win98_button(pressed=(nome == 'WIN')))
            for nome in ('WIN', 'WDO')
        ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '12px'}),
        dcc.Interval(id='interval-em-andamento98', interval=6_000, n_intervals=0),
        dcc.Store(id='store-em-andamento98-ids-conhecidos', data=[]),
        html.Div(id='em-andamento98-conteudo'),
        # Reaproveita o MESMO id/JS global de colagem (assets_98/diario_paste.js)
        # do Novo Trade — seguro porque as abas nunca ficam montadas ao mesmo
        # tempo (`montar_aba_diario98` troca o conteúdo inteiro).
        dcc.Store(id='store-diario98-imagem-colada'),
        # Qual card está "armado" pra receber o próximo Ctrl+V — ver
        # `assets_98/diario_paste.js` (clicar/focar em qualquer campo do
        # card já arma sozinho, sem botão).
        dcc.Store(id='store-diario98-posicao-ativa-print'),
    ])


# --- Histórico ---------------------------------------------------------------

def _td_diario98(conteudo, extra=None) -> html.Td:
    estilo = {'padding': '4px 8px', 'fontSize': '11px', 'fontFamily': FONTS['mono'],
              'borderBottom': f"1px solid {C['border']}"}
    if extra:
        estilo.update(extra)
    return html.Td(conteudo, style=estilo)


# Aderência S/R deixou de ser aba própria (feedback diário.txt, 2026-07-11)
# — como `avaliar_aderencia_sr()` já é 1 linha por `id_trade`, vira uma
# coluna extra aqui no Histórico (join direto por id_trade), e um resumo
# agregado na aba Estatísticas (ver `_bloco_resumo_aderencia_98`).
_COR_STATUS_ADERENCIA_SR = {
    'visto_preciso': 'green', 'visto_proximo': 'yellow', 'nao_visto': 'red',
}


def _formatar_janela_98(chave: str | None) -> str:
    """"Abertura (9:00-9:30)" ao invés de só "abertura" — `JANELAS_PREGAO`
    já é um dict {nome: (inicio, fim)}, só faltava formatar assim na
    exibição (feedback diário.txt, 2026-07-11)."""
    if not chave or chave not in cfg.JANELAS_PREGAO:
        return chave or '—'
    inicio, fim = cfg.JANELAS_PREGAO[chave]
    return f"{chave.replace('_', ' ').title()} ({inicio}-{fim})"


def _opcoes_janelas_98() -> list[dict]:
    return [{'label': _formatar_janela_98(k), 'value': k} for k in cfg.JANELAS_PREGAO.keys()]


def _mapa_nomes_estrategias_98() -> dict:
    """slug -> nome cadastrado (aba Estratégias), incluindo desativadas —
    trade antigo referenciando uma estratégia desativada ainda mostra um
    nome, não fica em branco."""
    return {l['slug']: l['nome'] for _, l in dt.listar_estrategias(somente_ativas=False).iterrows()}


def _formatar_tags_estrategia_98(tags, mapa_nomes: dict) -> str:
    lista = list(tags) if hasattr(tags, '__iter__') and not isinstance(tags, str) else []
    return ', '.join(mapa_nomes.get(t, t.replace('_', ' ').title()) for t in lista) or '—'


def _linha_historico_98(trade: pd.Series, aderencia_por_trade: dict, mapa_nomes_estrategias: dict) -> list[html.Tr]:
    """Ver diario_app_classico.py::_linha_historico pro raciocínio
    completo — retorna 2 `Tr` (resumo + detalhe expansível), substitui o
    popup antigo."""
    id_trade = int(trade['id_trade']) if pd.notna(trade.get('id_trade')) else None
    tags_str = _formatar_tags_estrategia_98(trade['estrategia_tags'], mapa_nomes_estrategias)
    saldo = trade['saldo_operacao_reais']
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0
    cor_saldo = C['green'] if saldo_num > 0 else (C['red'] if saldo_num < 0 else C['text2'])
    thumb = (
        html.Img(src=f"/diario-imagens/{trade['imagem_arquivo']}",
                 id={'type': 'diario98-img-thumb', 'index': id_trade}, n_clicks=0,
                 style={
                     'width': '48px', 'height': '32px', 'objectFit': 'cover',
                     'border': f"1px solid {C['shadow_dk']}", 'cursor': 'pointer',
                 }) if trade.get('imagem_arquivo') and id_trade is not None else html.Span('—', style={'color': C['text2']})
    )
    classificacao = aderencia_por_trade.get(id_trade) if id_trade is not None else None
    celula_aderencia = (
        _td_diario98(classificacao.replace('_', ' '),
                     extra={'color': C[_COR_STATUS_ADERENCIA_SR.get(classificacao, 'text2')], 'fontSize': '9px'})
        if classificacao else _td_diario98('—')
    )
    botao_expandir = (
        html.Button('▶', id={'type': 'diario98-btn-expandir', 'index': id_trade}, n_clicks=0,
                    className='win98-btn', style={**win98_button(), 'minHeight': '16px', 'padding': '0 8px', 'fontSize': '10px'})
        if id_trade is not None else ''
    )
    linha_resumo = html.Tr([
        _td_diario98(str(trade['data_op'])),
        _td_diario98(trade['ativo']),
        _td_diario98(_formatar_janela_98(trade.get('janela_pregao'))),
        _td_diario98(tags_str),
        _td_diario98(trade.get('tipo_operacao') or '—'),
        _td_diario98(f"{trade['resultado_pontos']:+.0f}" if pd.notna(trade['resultado_pontos']) else '—'),
        _td_diario98(f"R$ {saldo_num:+.2f}" if pd.notna(saldo) else '—',
                     extra={'color': cor_saldo, 'fontWeight': '700'}),
        _td_diario98(thumb),
        celula_aderencia,
        _td_diario98(botao_expandir),
    ])
    linha_detalhe = html.Tr(
        html.Td(html.Div(id={'type': 'diario98-detalhe-linha', 'index': id_trade}),
                colSpan=10, style={'padding': '0', 'border': 'none'}),
        id={'type': 'diario98-tr-detalhe', 'index': id_trade},
    ) if id_trade is not None else None
    return [linha_resumo, linha_detalhe] if linha_detalhe is not None else [linha_resumo]


def _construir_diario98_historico() -> html.Div:
    # Feedback 2026-07-17: a seção "Pendente de Classificação" que morava
    # aqui voltou pra aba Posição — ver diario_app_classico.py::
    # _construir_diario_historico pro mesmo raciocínio completo.
    return html.Div([
        # Feedback 2026-07-20: ver diario_app_classico.py::
        # _construir_diario_historico pro raciocínio completo (overlay
        # morava no nível mais alto do layout, "rolar até ele" caía
        # depois da tabela inteira — agora mora no TOPO do próprio
        # conteúdo do Histórico).
        html.Div(id='diario98-overlay-imagem'),
        html.Div([
            dcc.Dropdown(id='diario98-hist-ativo', options=_opcoes_diario(cfg.ATIVOS),
                         placeholder='Ativo', className='win98-dropdown', style={'width': '120px'}),
            dcc.Dropdown(id='diario98-hist-janela', options=_opcoes_janelas_98(),
                         placeholder='Janela', className='win98-dropdown', style={'width': '190px'}),
            dcc.Dropdown(id='diario98-hist-estrategia', options=_opcoes_estrategias(),
                         placeholder='Estratégia', className='win98-dropdown', style={'width': '190px'}),
            dcc.Input(id='diario98-hist-data-ini', type='date', style=win98_field()),
            html.Span('até', style={'fontSize': '11px', 'alignSelf': 'center'}),
            dcc.Input(id='diario98-hist-data-fim', type='date', style=win98_field()),
        ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '6px', 'flexWrap': 'wrap', 'alignItems': 'center'}),
        html.Div(id='diario98-hist-banner-filtro-cal', style={'marginBottom': '8px'}),
        html.Div(id='diario98-historico-tabela'),
    ])


def _overlay_98(conteudo, id_backdrop: str) -> html.Div:
    """Painel de detalhe — ver diario_app_classico.py::_overlay pro
    raciocínio completo (bug real confirmado em 2026-07-16: `position:fixed`
    dentro do iframe do Diário fica preso à altura TOTAL dele, não ao
    viewport real da janela). Mesmo fix aqui por consistência entre os 2
    skins, mesmo o SQ'98 não tendo mostrado o bug até agora (a janela
    win98 já tem scroll interno próprio, diferente do iframe que cresce
    sem limite do clássico).

    Único uso hoje é o popup de imagem (feedback 2026-07-20: "preencher o
    bloco inteiro") — ver diario_app_classico.py::_overlay pro mesmo
    raciocínio do flex column de altura fixa (88vh)."""
    return html.Div([
        html.Div(id=id_backdrop, n_clicks=0, style={
            'position': 'absolute', 'top': 0, 'left': 0, 'right': 0, 'bottom': 0,
            'background': 'rgba(0,0,0,0.6)', 'borderRadius': '4px',
        }),
        html.Div(conteudo, style={
            'position': 'relative', 'maxWidth': '95vw', 'margin': '0 auto',
            'height': '88vh', 'display': 'flex', 'flexDirection': 'column',
        }),
    ], id=f'{id_backdrop}-wrap', style={'position': 'relative', 'padding': '24px 16px'})


def _registrar_scroll_ate_overlay_98(app, id_store: str, id_container_estavel: str, id_wrap: str):
    """Ver diario_app_classico.py::_registrar_scroll_ate_overlay pro
    raciocínio completo."""
    app.clientside_callback(
        f"""
        function(valor) {{
            if (valor) {{
                setTimeout(function() {{
                    var el = document.getElementById('{id_wrap}');
                    if (el) {{ el.scrollIntoView({{behavior: 'smooth', block: 'start'}}); }}
                }}, 60);
            }}
            return window.dash_clientside.no_update;
        }}
        """,
        Output(id_container_estavel, 'title'),
        Input(id_store, 'data'),
        prevent_initial_call=True,
    )


def _campo_detalhe_98(label: str, valor) -> html.Div:
    # `pd.isna` (não comparar com a string 'nan') — muitos desses campos
    # vêm como float NaN de verdade (ex. Preço Entrada em trades migrados
    # sem esse dado), e NaN nunca é igual a nada, nem a si mesmo.
    vazio = valor is None or valor == '' or (isinstance(valor, float) and pd.isna(valor))
    return html.Div([
        html.Span(f"{label}: ", style={'color': C['text2'], 'fontSize': '10px'}),
        html.Span('—' if vazio else str(valor), style={'fontSize': '11px'}),
    ], style={'marginBottom': '5px'})


def _estrelas_texto_98(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return '—'
    try:
        n = max(0, min(5, int(valor)))
    except (TypeError, ValueError):
        return '—'
    return '★' * n + '☆' * (5 - n)


def _texto_sentimento_98(valor) -> str:
    resultado = dt.classificar_sentimento_mercado(valor)
    if resultado is None:
        return '—'
    nome, _ = resultado
    return f"{nome} ({int(valor)})"


def _conteudo_detalhe_trade_98(id_trade: int) -> html.Div | None:
    """Ver diario_app_classico.py::_conteudo_detalhe_trade pro raciocínio
    completo — conteúdo do detalhe renderizado DENTRO da linha do
    Histórico (não mais popup)."""
    df = dt.listar_trades()
    linha_df = df[df['id_trade'] == id_trade]
    if linha_df.empty:
        return None
    trade = linha_df.iloc[0]

    mapa_nomes = _mapa_nomes_estrategias_98()
    tags_str = _formatar_tags_estrategia_98(trade['estrategia_tags'], mapa_nomes)
    tags_erro = trade.get('tags_setup_erro')
    tags_erro_str = ', '.join(list(tags_erro)) if hasattr(tags_erro, '__iter__') and not isinstance(tags_erro, str) else '—'

    tags_emocionais = trade.get('tags_emocionais')
    tags_emocionais_str = (', '.join(list(tags_emocionais))
                            if hasattr(tags_emocionais, '__iter__') and not isinstance(tags_emocionais, str) else '—')
    tem_dado_emocional = any(pd.notna(trade.get(c)) for c in
                             ('emo_confianca', 'emo_humor', 'emo_energia', 'emo_atencao', 'emo_presenca', 'sentimento_mercado'))

    aderencia_df = dt.avaliar_aderencia_sr()
    linha_aderencia = aderencia_df[aderencia_df['id_trade'] == id_trade] if not aderencia_df.empty else aderencia_df
    bloco_aderencia = None
    if not linha_aderencia.empty:
        classificacao = linha_aderencia.iloc[0]['classificacao']
        bloco_aderencia = html.Div(
            f"Aderência S/R: {classificacao.replace('_', ' ')}",
            style={'color': C[_COR_STATUS_ADERENCIA_SR.get(classificacao, 'text2')],
                   'fontWeight': '700', 'fontSize': '11px', 'marginBottom': '6px'})

    padroes_df = persistencia.carregar_padroes_comportamentais()
    flags_padroes = []
    if padroes_df is not None and not padroes_df.empty:
        relacionados = padroes_df[(padroes_df['id_trade'] == id_trade) | (padroes_df['id_trade_anterior'] == id_trade)]
        for _, p in relacionados.iterrows():
            flags_padroes.append(html.Div(
                f"⚠ {p['tipo'].replace('_', ' ').title()}: {p['detalhe']}",
                style={'color': C[_COR_TIPO_PADRAO.get(p['tipo'], 'text2')], 'fontSize': '11px', 'marginBottom': '4px'}))

    snapshot_resumo = None
    snap_json = trade.get('snapshot_mercado_json')
    if snap_json is not None and pd.notna(snap_json):
        try:
            s = json.loads(snap_json)
            snapshot_resumo = (f"Preço {s.get('preco')} · ATR {s.get('atr')} · "
                                f"Tendência {s.get('tendencia')} · Fase {s.get('fase')} · Saúde {s.get('saude')}")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot_resumo = None

    imagem = None
    if trade.get('imagem_arquivo'):
        imagem = html.Img(src=f"/diario-imagens/{trade['imagem_arquivo']}",
                           id={'type': 'diario98-img-thumb-detalhe', 'index': id_trade}, n_clicks=0, style={
            'maxWidth': '360px', 'maxHeight': '260px', 'objectFit': 'contain',
            'border': f"1px solid {C['shadow_dk']}", 'marginTop': '8px', 'display': 'block', 'cursor': 'pointer',
        })

    saldo = trade['saldo_operacao_reais']
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0

    return win98_groupbox(f"Detalhe da Operação #{id_trade}", html.Div([
        html.Div([
            _campo_detalhe_98('Data', trade['data_op']),
            _campo_detalhe_98('Ativo', trade['ativo']),
            _campo_detalhe_98('Janela', _formatar_janela_98(trade.get('janela_pregao'))),
            _campo_detalhe_98('Tipo', trade.get('tipo_operacao')),
            _campo_detalhe_98('Estratégia', tags_str),
            _campo_detalhe_98('Sala/Análise', trade.get('sala_analise')),
            _campo_detalhe_98('Alvo (pts)', trade.get('alvo_pontos')),
            _campo_detalhe_98('Stop (pts)', trade.get('stop_pontos')),
            _campo_detalhe_98('Lote', trade.get('lote')),
            _campo_detalhe_98('Preço Entrada', trade.get('preco_entrada')),
            _campo_detalhe_98('Preço Saída', trade.get('preco_saida')),
            _campo_detalhe_98('Tipo Saída', trade.get('tipo_saida')),
            _campo_detalhe_98('Resultado (pts)', trade.get('resultado_pontos')),
            _campo_detalhe_98('Saldo (R$)', f"R$ {saldo_num:+.2f}"),
            _campo_detalhe_98('Nível S/R referenciado', trade.get('nivel_sr_referencia')),
            _campo_detalhe_98('Tags Setup/Erro', tags_erro_str),
        ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(180px, 1fr))', 'gap': '2px 14px'}),
        html.Div([
            html.Div('Estado Emocional', style={
                'fontSize': '10px', 'color': C['gold'], 'fontFamily': FONTS['display'],
                'fontWeight': '700', 'marginTop': '10px', 'marginBottom': '4px',
            }),
            html.Div([
                _campo_detalhe_98('Confiança', _estrelas_texto_98(trade.get('emo_confianca'))),
                _campo_detalhe_98('Humor', _estrelas_texto_98(trade.get('emo_humor'))),
                _campo_detalhe_98('Energia', _estrelas_texto_98(trade.get('emo_energia'))),
                _campo_detalhe_98('Atenção', _estrelas_texto_98(trade.get('emo_atencao'))),
                _campo_detalhe_98('Presença', _estrelas_texto_98(trade.get('emo_presenca'))),
                _campo_detalhe_98('Sentimento de Mercado', _texto_sentimento_98(trade.get('sentimento_mercado'))),
                _campo_detalhe_98('Print Emocional', tags_emocionais_str),
            ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(180px, 1fr))', 'gap': '2px 14px'}),
        ]) if tem_dado_emocional else None,
        html.Div([
            html.Div('Resumo Operacional:', style={'fontSize': '10px', 'color': C['text2'], 'marginTop': '8px'}),
            html.Div(trade.get('resumo_operacional') or '—', style={'fontSize': '11px', 'whiteSpace': 'pre-wrap'}),
            html.Div('Obs (autocoaching):', style={'fontSize': '10px', 'color': C['text2'], 'marginTop': '8px'}),
            html.Div(trade.get('obs') or '—', style={'fontSize': '11px', 'whiteSpace': 'pre-wrap'}),
        ]),
        bloco_aderencia,
        html.Div(flags_padroes) if flags_padroes else None,
        html.Div(f"Snapshot do motor no save: {snapshot_resumo}" if snapshot_resumo else 'Snapshot do motor: não capturado (trade antigo).',
                 style={'fontSize': '10px', 'color': C['text2'], 'marginTop': '8px'}),
        imagem,
    ]), estilo_extra={'background': C['panel']})


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
            data_iso = f"{ano:04d}-{mes:02d}-{dia:02d}"
            info = por_dia.get(data_iso)
            if info is None:
                bg = C['window']
            elif info['saldo'] > 0:
                bg = C['green']
            elif info['saldo'] < 0:
                bg = C['red']
            else:
                bg = C['border']
            # `texto_sobre()` escolhe preto/branco por luminância real do fundo
            # — antes usava `C['bg']` (teal) fixo como "cor de contraste", que
            # em cima de C['green']/C['red'] ficava quase ilegível (mesmo
            # canal de luminância); ver CHANGELOG_SQ98.md pro mesmo bug já
            # corrigido noutros badges.
            cor_texto = texto_sobre(bg)
            estilo_celula = {**win98_sunken(bg=bg, padding='4px'), 'flex': '1', 'minHeight': '36px',
                              'margin': '2px', 'color': cor_texto, 'textAlign': 'center'}
            conteudo = [
                html.Div(str(dia), style={'fontSize': '10px'}),
                html.Div(f"R$ {info['saldo']:+.0f}" if info else '',
                          style={'fontSize': '9px', 'fontFamily': FONTS['mono']}),
            ]
            if info is not None:
                # Clicável só quando há operação no dia — navega pro Histórico
                # já filtrado (ver `clicar_dia_calendario_98`).
                celulas.append(html.Div(conteudo, id={'type': 'diario98-cal-dia', 'index': data_iso},
                                         n_clicks=0, style={**estilo_celula, 'cursor': 'pointer'}))
            else:
                celulas.append(html.Div(conteudo, style=estilo_celula))
        linhas.append(html.Div(celulas, style={'display': 'flex'}))
    return html.Div(linhas)


def _grade_calendario_anual_98(por_mes: dict, ano: int) -> html.Div:
    """Grade 4x3 (um trimestre por linha) com o saldo agregado de cada mês do
    ano — usada quando o toggle "Ano Corrente" está ligado, no lugar da grade
    dia-a-dia."""
    linhas = []
    for trimestre in range(4):
        celulas = []
        for i in range(3):
            mes_num = trimestre * 3 + i + 1
            info = por_mes.get(f"{ano:04d}-{mes_num:02d}")
            if info is None:
                bg = C['window']
            elif info['saldo'] > 0:
                bg = C['green']
            elif info['saldo'] < 0:
                bg = C['red']
            else:
                bg = C['border']
            cor_texto = texto_sobre(bg)
            celulas.append(html.Div([
                html.Div(_NOMES_MES[mes_num][:3], style={'fontSize': '10px', 'fontWeight': '700'}),
                html.Div(f"R$ {info['saldo']:+.0f}" if info else '—',
                          style={'fontSize': '9px', 'fontFamily': FONTS['mono']}),
                html.Div(f"{info['n']} op." if info else '', style={'fontSize': '8px'}),
            ], style={**win98_sunken(bg=bg, padding='6px'), 'flex': '1', 'minHeight': '48px',
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


def _cards_estatisticas_98(stats: dict) -> html.Div:
    payoff_str = f"{stats['payoff']:.2f}" if stats['payoff'] is not None else '—'
    return html.Div([
        _card_estatistica_98('Operações', str(stats['num_operacoes'])),
        _card_estatistica_98('Saldo Total', f"R$ {stats['saldo_total']:+.2f}",
                              cor=C['green'] if stats['saldo_total'] > 0 else C['red']),
        _card_estatistica_98('Wins', str(stats['n_win']), cor=C['green']),
        _card_estatistica_98('Losses', str(stats['n_loss']), cor=C['red']),
        _card_estatistica_98('Breakeven', str(stats['n_breakeven'])),
        _card_estatistica_98('Taxa de Acerto', f"{stats['taxa_acerto'] * 100:.1f}%"),
        _card_estatistica_98('Payoff', payoff_str),
        _card_estatistica_98('Expectativa', f"R$ {stats['expectativa']:+.2f}"),
        _card_estatistica_98('Max Gain', f"R$ {stats['max_gain']:+.2f}", cor=C['green']),
        _card_estatistica_98('Max Loss', f"R$ {stats['max_loss']:+.2f}", cor=C['red']),
        _card_estatistica_98('Drawdown Máx.', f"R$ {stats['drawdown_maximo_reais']:+.2f}",
                              cor=C['red'] if stats['drawdown_maximo_reais'] < 0 else None),
        _card_estatistica_98('Seq. Wins', str(stats['maior_sequencia_win'])),
        _card_estatistica_98('Seq. Losses', str(stats['maior_sequencia_loss'])),
    ], style={'display': 'flex', 'gap': '8px', 'flexWrap': 'wrap', 'marginBottom': '14px'})


def _cards_ou_vazio_estatisticas_98(stats: dict) -> html.Div:
    if stats['num_operacoes']:
        return _cards_estatisticas_98(stats)
    return html.Div('Nenhum trade real neste período.', style={
        'padding': '14px', 'textAlign': 'center', 'color': C['text2'], 'fontSize': '12px',
    })


def _resto_periodo_estatisticas_98(stats: dict) -> html.Div:
    """Evolução de saldo + breakdowns do período — ver
    `diario_app_classico.py::_resto_periodo_estatisticas` pro mesmo
    raciocínio (o calendário em si, grade + botões, NÃO mora aqui)."""
    return html.Div([
        win98_groupbox('Evolução de Saldo', dcc.Graph(
            figure=_grafico_evolucao_saldo_98(stats), config={'displayModeBar': False},
        )),

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
    ])


def _bloco_status_badges_98() -> html.Div:
    """Reconciliação MT5 (agora em background, sem aba própria) e resumo de
    Aderência S/R (agora enriquece o Histórico, não é mais aba própria) —
    badge discreto de 1-2 linhas, não uma tabela navegável (feedback
    diário.txt, 2026-07-11)."""
    resumo_rec = dt.resumo_reconciliacao()
    if resumo_rec is None:
        badge_rec = html.Span('Reconciliação: aguardando o 1º ciclo em background (a cada 5min).',
                               style={'color': C['text2']})
    else:
        problemas = (resumo_rec['divergente'] + resumo_rec['sem_correspondencia_mt5']
                     + resumo_rec['sem_correspondencia_diario'])
        if problemas == 0:
            badge_rec = html.Span(f"Reconciliação MT5: ✔ {resumo_rec['ok']} trade(s) OK (últimos 30 dias).",
                                   style={'color': C['green']})
        else:
            badge_rec = html.Span(
                f"Reconciliação MT5: ⚠ {problemas} pendência(s) nos últimos 30 dias "
                f"({resumo_rec['divergente']} divergente(s), {resumo_rec['sem_correspondencia_mt5']} sem execução MT5, "
                f"{resumo_rec['sem_correspondencia_diario']} sem registro no diário).",
                style={'color': C['amber']})

    resumo_ader = dt.avaliar_aderencia_sr()
    if resumo_ader.empty:
        badge_ader = html.Span(
            'Aderência S/R: sem trades avaliáveis ainda (precisa de nível S/R referenciado + snapshot de mercado).',
            style={'color': C['text2']})
    else:
        total = len(resumo_ader)
        precisos = int((resumo_ader['classificacao'] == 'visto_preciso').sum())
        badge_ader = html.Span(f"Aderência S/R: {precisos}/{total} trade(s) vistos com precisão pelo motor "
                                f"(ver coluna no Histórico).", style={'color': C['text']})

    return win98_groupbox('Status', html.Div([
        html.Div(badge_rec, style={'fontSize': '11px', 'marginBottom': '4px'}),
        html.Div(badge_ader, style={'fontSize': '11px'}),
    ]))


def _construir_diario98_estatisticas(stats_geral: dict, scores: dict) -> html.Div:
    """Shell montado 1x por ativação da aba: só decide se não há dado NENHUMA
    vez (placeholder) e monta o Score de Confiabilidade (all-time). O
    conteúdo escopado por mês/ano vive em `diario98-periodo-conteudo`/
    `diario98-cards-conteudo`/`diario98-resto-periodo-conteudo`, preenchido
    por `atualizar_periodo_estatisticas_98` a partir dos Stores de mês/modo
    abaixo.

    Os botões de navegação do calendário (◀/▶/"Ver Ano Inteiro") moram AQUI,
    criados uma única vez — ver `diario_app_classico.py::
    _construir_diario_estatisticas` pro raciocínio completo (causa raiz do
    bug relatado em 2026-07-12: recriar esses botões a cada troca de mês
    reseta `n_clicks` pra 0, e como esse prop é Input do callback de
    navegação, o Dash disparava a navegação sozinha, fantasma, em direção
    imprevisível). Botões fixos aqui, só `children`/`style` mudam depois."""
    if stats_geral['num_operacoes'] == 0:
        return _construir_diario98_placeholder('Estatísticas (nenhum trade real lançado ainda)')

    hoje = date.today()
    mes_inicial = f"{hoje.year:04d}-{hoje.month:02d}"
    return html.Div([
        dcc.Store(id='diario98-calendario-mes', data=mes_inicial),
        dcc.Store(id='diario98-modo-ano', data=False),

        html.Div(id='diario98-cards-conteudo'),

        win98_groupbox('Calendário', html.Div([
            html.Div([
                html.Div([
                    html.Button('◀', id='btn-diario98-cal-mes-prev', n_clicks=0,
                                className='win98-btn', style={**win98_button(), 'padding': '2px 10px'}),
                    html.Span(_NOMES_MES[hoje.month], id='diario98-calendario-label-mes', style={
                        'margin': '0 8px', 'fontSize': '11px', 'fontFamily': FONTS['display'], 'fontWeight': '700',
                    }),
                    html.Button('▶', id='btn-diario98-cal-mes-next', n_clicks=0,
                                className='win98-btn', style={**win98_button(), 'padding': '2px 10px'}),
                ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '16px'}),
                html.Div([
                    html.Button('◀', id='btn-diario98-cal-ano-prev', n_clicks=0,
                                className='win98-btn', style={**win98_button(), 'padding': '2px 10px'}),
                    html.Span(str(hoje.year), id='diario98-calendario-label-ano', style={
                        'margin': '0 8px', 'fontSize': '11px', 'fontFamily': FONTS['display'], 'fontWeight': '700',
                    }),
                    html.Button('▶', id='btn-diario98-cal-ano-next', n_clicks=0,
                                className='win98-btn', style={**win98_button(), 'padding': '2px 10px'}),
                ], style={'display': 'flex', 'alignItems': 'center'}),
                html.Button('Ver Ano Inteiro', id='btn-diario98-modo-ano', n_clicks=0,
                            className='win98-btn', style={**win98_button(pressed=False), 'marginLeft': 'auto'}),
            ], style={
                # Ver diario_app_classico.py pro mesmo raciocínio: setas de
                # mês/ano viraram grupos separados (feedback 2026-07-12) e a
                # fileira ficou mais larga — `flexWrap` evita estourar a
                # largura da janela Win98 (que pode ser bem estreita).
                'display': 'flex', 'flexWrap': 'wrap', 'alignItems': 'center',
                'rowGap': '8px', 'marginBottom': '8px',
            }),
            html.Div(id='diario98-calendario-grid'),
        ])),

        html.Div(id='diario98-resto-periodo-conteudo'),

        win98_groupbox('Score de Confiabilidade', _tabela_score_confiabilidade_98(scores)),

        _bloco_status_badges_98(),
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


# --- Estratégias (CRUD) ---------------------------------------------------------
# Substitui as listas fixas ESTRATEGIA_TAGS_CANONICAS/TAGS_SETUP_ERRO_INICIAIS
# por cadastro editável (feedback diário.txt, 2026-07-11) — prep pro
# Playbook. Nunca desativa de verdade (soft delete via `ativo=False`):
# trades antigos podem referenciar o slug.

def _botao_desativar_98(tipo_pattern: str, id_item: int) -> html.Button:
    return html.Button('Desativar', id={'type': tipo_pattern, 'index': int(id_item)}, n_clicks=0,
                        className='win98-btn',
                        style={**win98_button(), 'minHeight': '18px', 'padding': '0 8px', 'fontSize': '9px'})


def _botao_editar_98(tipo_pattern: str, id_item: int) -> html.Button:
    return html.Button('Editar', id={'type': tipo_pattern, 'index': int(id_item)}, n_clicks=0,
                        className='win98-btn',
                        style={**win98_button(), 'minHeight': '18px', 'padding': '0 8px', 'fontSize': '9px',
                               'marginRight': '4px', 'color': C['gold']})


def _tabela_estrategias_98(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma estratégia cadastrada.', style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
                          for t in ['Nome', 'Descrição', 'Checklist', '']])
    linhas = [html.Tr([
        _td_diario98(linha['nome']),
        _td_diario98(linha['descricao'] or '—'),
        _td_diario98(f"{len(_lista_segura_98(linha['checklist']))} item(ns)"),
        _td_diario98(html.Div([
            _botao_editar_98('diario98-estrategia-editar', linha['id']),
            _botao_desativar_98('diario98-estrategia-desativar', linha['id']),
        ], style={'display': 'flex'})),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_rr_presets_98(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum preset R:R cadastrado.', style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
                          for t in ['Nome', 'Alvo (pts)', 'Stop (pts)', 'Breakeven (pts)', '']])
    linhas = [html.Tr([
        _td_diario98(linha['nome']),
        _td_diario98(f"{linha['alvo_pontos']:.0f}"),
        _td_diario98(f"{linha['stop_pontos']:.0f}"),
        _td_diario98(f"{linha['breakeven_pontos']:.0f}" if pd.notna(linha['breakeven_pontos']) else '—'),
        _td_diario98(_botao_desativar_98('diario98-rr-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_tags_98(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma tag cadastrada.', style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
                          for t in ['Nome', 'Tipo', '']])
    linhas = [html.Tr([
        _td_diario98(linha['nome']),
        _td_diario98(linha['tipo']),
        _td_diario98(_botao_desativar_98('diario98-tag-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_tags_emocionais_98(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma tag emocional cadastrada.', style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
                          for t in ['Nome', '']])
    linhas = [html.Tr([
        _td_diario98(linha['nome']),
        _td_diario98(_botao_desativar_98('diario98-tag-emocional-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario98_estrategias() -> html.Div:
    campo = _campo_diario98
    return html.Div([
        win98_groupbox('Nova Estratégia', html.Div([
            html.Div(id='diario98-estrategia-editando-aviso'),
            campo('Nome', dcc.Input(id='diario98-nova-estrategia-nome', type='text', style=win98_field())),
            campo('Descrição', dcc.Textarea(id='diario98-nova-estrategia-desc', rows=2,
                                             style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            campo('Checklist operacional (1 item por linha, opcional)',
                  dcc.Textarea(id='diario98-nova-estrategia-checklist', rows=3,
                               style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            html.Button('Adicionar Estratégia', id='btn-diario98-add-estrategia', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Button('Cancelar Edição', id='btn-diario98-cancelar-edicao-estrategia', n_clicks=0,
                        className='win98-btn', style={**win98_button(), 'marginLeft': '6px', 'display': 'none'}),
            html.Span(id='diario98-estrategia-status', style={'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2']}),
            dcc.Store(id='store-diario98-estrategia-editando', data=None),
        ])),
        win98_groupbox('Estratégias Cadastradas', html.Div(_tabela_estrategias_98(dt.listar_estrategias()),
                                                              id='diario98-tabela-estrategias')),

        win98_groupbox('Novo Preset R:R', html.Div([
            html.Div([
                campo('Nome', dcc.Input(id='diario98-novo-rr-nome', type='text', style={**win98_field(), 'width': '160px'})),
                campo('Alvo (pts)', dcc.Input(id='diario98-novo-rr-alvo', type='text', inputMode='decimal',
                                               style={**win98_field(), 'width': '100px'})),
                campo('Stop (pts)', dcc.Input(id='diario98-novo-rr-stop', type='text', inputMode='decimal',
                                               style={**win98_field(), 'width': '100px'})),
                campo('Breakeven (pts, opcional)', dcc.Input(id='diario98-novo-rr-be', type='text', inputMode='decimal',
                                                              style={**win98_field(), 'width': '140px'})),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
            html.Button('Adicionar Preset', id='btn-diario98-add-rr', n_clicks=0,
                        className='win98-btn', style=win98_button(extra={'marginTop': '6px'})),
            html.Span(id='diario98-rr-status', style={'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2']}),
        ])),
        win98_groupbox('Presets R:R Cadastrados', html.Div(_tabela_rr_presets_98(dt.listar_rr_presets()),
                                                              id='diario98-tabela-rr')),

        win98_groupbox('Nova Tag Setup/Erro', html.Div([
            html.Div([
                campo('Nome', dcc.Input(id='diario98-nova-tag-nome', type='text', style={**win98_field(), 'width': '200px'})),
                campo('Tipo', dcc.Dropdown(id='diario98-nova-tag-tipo',
                                            options=[{'label': v.title(), 'value': v} for v in ('setup', 'erro', 'ambos')],
                                            value='ambos', clearable=False, className='win98-dropdown', style={'width': '120px'})),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
            html.Button('Adicionar Tag', id='btn-diario98-add-tag', n_clicks=0,
                        className='win98-btn', style=win98_button(extra={'marginTop': '6px'})),
            html.Span(id='diario98-tag-status', style={'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2']}),
        ])),
        win98_groupbox('Tags Cadastradas', html.Div(_tabela_tags_98(dt.listar_tags_setup_erro()),
                                                       id='diario98-tabela-tags')),

        win98_groupbox('Nova Tag Emocional (Print Emocional)', html.Div([
            campo('Nome', dcc.Input(id='diario98-nova-tag-emocional-nome', type='text', style={**win98_field(), 'width': '200px'})),
            html.Button('Adicionar Tag', id='btn-diario98-add-tag-emocional', n_clicks=0,
                        className='win98-btn', style=win98_button(extra={'marginTop': '6px'})),
            html.Span(id='diario98-tag-emocional-status', style={'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2']}),
        ])),
        win98_groupbox('Tags Emocionais Cadastradas', html.Div(_tabela_tags_emocionais_98(dt.listar_tags_emocionais()),
                                                                  id='diario98-tabela-tags-emocionais')),
    ])


# =============================================================================
# BEM-ESTAR: check-in pré-operacional, Escala Hawkins, Roda da Vida (aba
# nova, feedback diário.txt 2026-07-11, Fase E) — primeira tela do Diário
# organizada por DATA em vez de por trade/cadastro editável.
# =============================================================================

# Funil vertical (2026-07-15, pedido do Jesué — substitui o velocímetro/
# gauge circular anterior por um formato mais fiel ao pôster original da
# Escala Hawkins: funil estreitando da consciência expandida, no topo, à
# contraída, na base, com os 17 níveis listados ao lado). Mesmo raciocínio
# de `diario_app_classico.py::_funil_hawkins` (CSS puro, sem Plotly, stops
# do gradiente na mesma escala log de `dt.posicao_normalizada_hawkins`),
# só a moldura muda pro bevel sunken do skin '98.
_ALTURA_FUNIL_HAWKINS_98 = 400
_LARGURA_FUNIL_HAWKINS_98 = 150


def _funil_hawkins_98(valor: float, normalizado: float, nivel_nome: str, cor_nivel: str) -> html.Div:
    """Ver diario_app_classico.py::_funil_hawkins pro mesmo raciocínio
    (duplicado aqui porque este processo isolado nunca importa de
    dashboard_98.py). Moldura sunken (`win98_sunken`) sobre fundo `C['window']`
    branco — as cores de `dt.NIVEIS_HAWKINS` já são saturadas o bastante pra
    contrastar sozinhas, sem precisar do 'gold' navy do skin pra texto."""
    stops = ', '.join(
        f'{cor} {dt.posicao_normalizada_hawkins(v) * 100:.1f}%'
        for v, _, cor in dt.NIVEIS_HAWKINS
    )
    pos_marcador = min(max((1 - normalizado) * 100, 2), 98)  # 0%=topo/expandido, 100%=base/contraído, com folga nas bordas

    # Espaçamento UNIFORME (ver diario_app_classico.py::_funil_hawkins pro
    # mesmo raciocínio) — só o degradê de cor do funil segue a escala log
    # real, a legenda ao lado é uniforme pra não sobrepor texto nos valores
    # altos (500/540/600/700), muito próximos entre si na escala log.
    rotulos = []
    for v, nome, cor in reversed(dt.NIVEIS_HAWKINS):
        eh_coragem = (v == dt.LIMIAR_HAWKINS_CORAGEM)
        rotulos.append(html.Div([
            html.Span(nome, style={
                'fontFamily': FONTS['display'], 'color': C['gold'] if eh_coragem else C['text2'],
                'fontWeight': 'bold' if eh_coragem else 'normal',
            }),
            html.Span(style={'display': 'inline-block', 'width': '10px', 'height': '0',
                              'borderBottom': f'1px dotted {C["shadow"]}', 'margin': '0 5px', 'verticalAlign': 'middle'}),
            html.Span(str(v), style={'fontFamily': FONTS['mono'], 'color': cor}),
        ], style={
            'flex': '1', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'flex-end',
            'whiteSpace': 'nowrap', 'fontSize': '10px', 'lineHeight': '1',
        }))

    marcador = html.Div([
        html.Div(style={
            'position': 'absolute', 'left': f'-{_LARGURA_FUNIL_HAWKINS_98 + 36}px', 'right': '0',
            'top': '0', 'height': '2px', 'background': cor_nivel,
        }),
        html.Span('◀ você está aqui', style={
            'position': 'absolute', 'left': '100%', 'top': '-8px', 'marginLeft': '8px',
            'fontSize': '10px', 'fontFamily': FONTS['display'], 'color': cor_nivel, 'whiteSpace': 'nowrap',
        }),
    ], style={'position': 'absolute', 'top': f'{pos_marcador:.2f}%', 'left': '0', 'right': '0', 'zIndex': '2'})

    funil = html.Div(style={
        'position': 'absolute', 'left': '0', 'top': '0',
        'width': f'{_LARGURA_FUNIL_HAWKINS_98}px', 'height': f'{_ALTURA_FUNIL_HAWKINS_98}px',
        'background': f'linear-gradient(to top, {stops})',
        'clipPath': 'polygon(0% 4%, 6% 0%, 94% 0%, 100% 4%, 100% 72%, 60% 100%, 40% 100%, 0% 72%)',
    })

    cabecalho = html.Div([
        html.Span(f'{nivel_nome}', style={'fontFamily': FONTS['mono'], 'fontSize': '14px',
                                           'fontWeight': 'bold', 'color': C['text']}),
        html.Span(f' · {valor:.0f}', style={'fontFamily': FONTS['mono'], 'fontSize': '11px', 'color': C['text2']}),
    ], style={'marginBottom': '10px'})

    return html.Div([
        cabecalho,
        html.Div([
            html.Div([funil, marcador], style={
                'position': 'relative', 'width': f'{_LARGURA_FUNIL_HAWKINS_98}px', 'height': f'{_ALTURA_FUNIL_HAWKINS_98}px',
                'marginLeft': f'{_LARGURA_FUNIL_HAWKINS_98 + 40}px',
            }),
            html.Div(rotulos, style={
                'position': 'absolute', 'left': '0', 'top': '0',
                'width': f'{_LARGURA_FUNIL_HAWKINS_98 + 20}px', 'height': f'{_ALTURA_FUNIL_HAWKINS_98}px',
                'display': 'flex', 'flexDirection': 'column',
            }),
        ], style=win98_sunken(bg=C['window'], padding='14px', extra={
            'position': 'relative', 'height': f'{_ALTURA_FUNIL_HAWKINS_98 + 28}px',
        })),
        html.Div('Escala Hawkins (proxy, não calibrado) — abaixo de Coragem (200) a teoria original considera que a pessoa "perde poder"',
                  style={'fontSize': '10px', 'color': C['text2'], 'marginTop': '10px', 'maxWidth': '420px'}),
    ])


def _bloco_gauge_hawkins_98(data_checkin: date):
    """Nunca inventa valor: sem check-in do dia (ou check-in sem nenhuma
    nota preenchida), mostra um aviso em vez do funil."""
    checkin = dt.obter_checkin_dia(data_checkin)
    if not checkin:
        return html.Div('Sem check-in salvo pra esse dia ainda — preencha e salve pra ver o nível.',
                         style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    medias = dt.calcular_medias_emocionais_dia(data_checkin)
    resultado = dt.calcular_nivel_hawkins(checkin, medias)
    if not resultado:
        return html.Div('Check-in salvo, mas sem nenhuma nota preenchida ainda.',
                         style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    return _funil_hawkins_98(resultado['valor'], resultado['normalizado'], resultado['nivel_nome'], resultado['cor'])


def _radar_roda_vida_98(registro: dict | None) -> go.Figure:
    categorias = [dt.NOMES_AREAS_RODA_VIDA[a] for a in dt.AREAS_RODA_VIDA]
    valores = [float(registro.get(a) or 0) for a in dt.AREAS_RODA_VIDA] if registro else [0] * len(dt.AREAS_RODA_VIDA)
    fig = go.Figure(go.Scatterpolar(r=valores + [valores[0]], theta=categorias + [categorias[0]],
                                     fill='toself', line=dict(color=C['gold']), fillcolor=C['gold'], opacity=0.5))
    fig.update_layout(
        # `range` sozinho não é suficiente: com todas as áreas em 0 (antes do
        # 1º registro salvo), o auto-cálculo de ticks do Plotly quebra (variância
        # zero nos dados) e desenha uma escala minúscula tipo "0.7378" em vez de
        # 0-10 — `autorange=False` + `tickvals` explícitos travam a escala real.
        polar=dict(radialaxis=dict(visible=True, range=[0, 10], autorange=False,
                                    tickvals=[0, 2, 4, 6, 8, 10], tickfont=dict(size=8, color=C['text2'])),
                   angularaxis=dict(tickfont=dict(size=9, color=C['text']))),
        paper_bgcolor='rgba(0,0,0,0)', showlegend=False, margin=dict(l=30, r=30, t=20, b=20),
        height=320, autosize=True,
    )
    return fig


def _tabela_checkins_98(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum check-in registrado ainda.', style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
                          for t in ['Data', 'Sono', 'Café', 'Duração Sono', 'Nível Hawkins']])
    linhas = []
    for _, linha in df.head(14).iterrows():
        checkin = linha.to_dict()
        resultado = dt.calcular_nivel_hawkins(checkin, dt.calcular_medias_emocionais_dia(checkin['data']))
        duracao = checkin.get('sono_duracao_horas')
        linhas.append(html.Tr([
            _td_diario98(str(checkin['data'])),
            _td_diario98(_estrelas_texto_98(checkin.get('sono_qualidade'))),
            _td_diario98(_estrelas_texto_98(checkin.get('cafe_qualidade'))),
            _td_diario98(f"{duracao:.1f}h" if duracao is not None and not (isinstance(duracao, float) and pd.isna(duracao)) else '—'),
            _td_diario98(resultado['nivel_nome'] if resultado else '—'),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_roda_vida_98(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum registro de Roda da Vida ainda.', style={'color': C['text2'], 'fontSize': '11px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
                          for t in ['Data', 'Média Geral']])
    linhas = []
    for _, linha in df.head(10).iterrows():
        valores = [linha[a] for a in dt.AREAS_RODA_VIDA if pd.notna(linha[a])]
        media = sum(valores) / len(valores) if valores else None
        linhas.append(html.Tr([
            _td_diario98(str(linha['data'])),
            _td_diario98(f"{media:.1f}" if media is not None else '—'),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario98_bem_estar() -> html.Div:
    campo = _campo_diario98
    hoje = date.today().isoformat()
    checkin_hoje = dt.obter_checkin_dia(hoje) or {}
    ultimo_roda = dt.obter_ultimo_registro_roda_vida()
    # Sem nenhum registro salvo ainda, os sliders começam em 5 (meio da
    # escala) — o radar inicial usa os MESMOS valores, senão ele nasce
    # todo em zero (quase um ponto só) enquanto os sliders já mostram 5,
    # os dois ficando fora de sincronia até o usuário mexer em algum.
    valores_iniciais_roda = ultimo_roda or {area: 5 for area in dt.AREAS_RODA_VIDA}

    def _valor(chave, padrao=None):
        v = checkin_hoje.get(chave)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return padrao
        return v

    sem_spinner = 'no-spinner'
    return html.Div([
        win98_groupbox('Check-in do Dia', html.Div([
            campo('Data', dcc.Input(id='diario98-checkin-data', type='date', value=hoje, style=win98_field())),
            html.Div([
                campo('Qualidade do Sono', _estrela_rating_98('diario98-checkin-sono', _num_estrela_ou_padrao_98(_valor('sono_qualidade')))),
                campo('Qualidade do Café', _estrela_rating_98('diario98-checkin-cafe', _num_estrela_ou_padrao_98(_valor('cafe_qualidade')))),
            ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(150px, 1fr))', 'gap': '6px 16px'}),
            html.Div([
                campo('Dormiu (HH:MM)', dcc.Input(id='diario98-checkin-hora-dormir', type='text', placeholder='23:00',
                                                    value=_valor('sono_hora_dormir', ''), style={**win98_field(), 'width': '90px'})),
                campo('Acordou (HH:MM)', dcc.Input(id='diario98-checkin-hora-acordar', type='text', placeholder='07:00',
                                                     value=_valor('sono_hora_acordar', ''), style={**win98_field(), 'width': '90px'})),
                campo('Autocuidado (min)', dcc.Input(id='diario98-checkin-autocuidado', type='number', className=sem_spinner,
                                                       value=_valor('autocuidado_minutos'), style={**win98_field(), 'width': '90px'})),
                campo('Meditação (min)', dcc.Input(id='diario98-checkin-meditacao', type='number', className=sem_spinner,
                                                     value=_valor('meditacao_minutos'), style={**win98_field(), 'width': '90px'})),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
            campo('5 Motivos Para Agradecer + Obs.:',
                  dcc.Textarea(id='diario98-checkin-obs', value=_valor('observacoes', ''), rows=6,
                               style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            html.Button('Salvar Check-in do Dia', id='btn-diario98-salvar-checkin', n_clicks=0,
                        className='win98-btn', style=win98_button(extra={'marginTop': '6px'})),
            html.Span(id='diario98-checkin-status', style={'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2']}),
        ])),

        win98_groupbox('Escala Hawkins (proxy, não calibrado)',
                        html.Div(_bloco_gauge_hawkins_98(date.fromisoformat(hoje)), id='diario98-gauge-hawkins')),

        win98_groupbox('Roda da Vida', html.Div([
            html.Div([
                campo(dt.NOMES_AREAS_RODA_VIDA[area],
                      dcc.Slider(id={'type': 'diario98-roda-area', 'index': area}, min=0, max=10, step=1,
                                 value=int(valores_iniciais_roda.get(area) or 5),
                                 marks={0: '0', 5: '5', 10: '10'}))
                for area in dt.AREAS_RODA_VIDA
            ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(200px, 1fr))', 'gap': '10px 20px'}),
            html.Button('Salvar Novo Registro', id='btn-diario98-salvar-roda', n_clicks=0,
                        className='win98-btn', style=win98_button(extra={'marginTop': '10px'})),
            html.Span(id='diario98-roda-status', style={'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2']}),
            # `staticPlot=True`: gráfico é só leitura (o preenchimento vem
            # dos sliders, não de arrastar no próprio gráfico) — clicar/
            # arrastar direto nele já causou o bug do eixo distorcido, tirar
            # a interatividade evita qualquer repetição desse tipo de problema.
            dcc.Graph(id='diario98-radar-roda', figure=_radar_roda_vida_98(valores_iniciais_roda), responsive=True,
                      config={'displayModeBar': False, 'staticPlot': True, 'responsive': True},
                      style={'marginTop': '10px', 'width': '100%'}),
        ])),

        win98_groupbox('Histórico', html.Div([
            html.Div('Check-ins recentes', style={'fontSize': '11px', 'fontWeight': 'bold', 'marginBottom': '4px'}),
            html.Div(_tabela_checkins_98(dt.listar_checkins()), id='diario98-tabela-checkins'),
            html.Div('Roda da Vida — registros', style={'fontSize': '11px', 'fontWeight': 'bold', 'margin': '10px 0 4px'}),
            html.Div(_tabela_roda_vida_98(dt.listar_registros_roda_vida()), id='diario98-tabela-roda-vida'),
        ])),
    ])


# =============================================================================
# APP
# =============================================================================

app = dash.Dash(__name__, assets_folder='assets_98', suppress_callback_exceptions=True)

# Registro dos cliques da avaliação em estrelas (ver _estrela_rating_98) —
# um campo singleton por chamada (Novo Trade + Bem-Estar), um TIPO
# pattern-matched por chamada (Posição, cobre qualquer nº de posições
# abertas).
for _campo_estrela in (
    'diario98-emo-confianca', 'diario98-emo-humor', 'diario98-emo-energia',
    'diario98-emo-atencao', 'diario98-emo-presenca',
    'diario98-checkin-sono', 'diario98-checkin-cafe',
):
    _registrar_clique_estrela_98(app, _campo_estrela)
for _tipo_estrela in (
    'ea98-emo-confianca', 'ea98-emo-humor', 'ea98-emo-energia', 'ea98-emo-atencao', 'ea98-emo-presenca',
):
    _registrar_clique_estrela_match_98(app, _tipo_estrela)

_registrar_scroll_ate_overlay_98(app, 'diario98-imagem-ampliada', 'diario98-overlay-imagem',
                                  'diario98-overlay-imagem-backdrop-wrap')

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
    dcc.Store(id='store-diario98-aba', data=_ABA_DIARIO98_INICIAL),
    dcc.Store(id='diario98-hist-filtro-data-calendario', data=None),
    dcc.Store(id='diario98-imagem-ampliada', data=None),
    html.Div([
        html.Button(nome, id=f'btn-diario98-aba-{chave}', n_clicks=0,
                    className='win98-btn',
                    style=win98_button(pressed=(chave == _ABA_DIARIO98_INICIAL)))
        for chave, nome in _ABAS_DIARIO98
    ], style={'display': 'flex', 'gap': '6px', 'marginBottom': '10px', 'flexWrap': 'wrap'}),
    html.Div(id='diario98-aba-conteudo'),
], style={
    # `height: 100vh` + `overflowY: auto` (não mais `minHeight: 100vh` sem
    # scroll) — feedback 2026-07-12: "a barra de rolagem não funciona e não
    # consigo ver as informações completas, mesmo redimensionando a
    # janela". Causa: `html, body, #react-entry-point` têm `overflow:
    # hidden` em assets_98/style.css (pensado pro DESKTOP do SQ'98 nunca
    # rolar como página — cada win98_window rola por conta própria via
    # `.win98-window-body`). Esse CSS é compartilhado (mesma pasta
    # assets_98/) e também se aplica a ESTE app — só que este layout não
    # tinha nenhuma região própria de scroll, então conteúdo além da altura
    # do iframe ficava simplesmente cortado, invisível, sem jeito de
    # alcançar (nem rolando, nem redimensionando a janela do Diário — como
    # os ancestrais têm overflow:hidden, esticar a janela só mudava o
    # tamanho da "janela de corte", não revelava o resto). Com `height`
    # travado no viewport do próprio iframe e `overflowY: auto` aqui, essa
    # div vira a região de scroll de verdade, e cresce/encolhe junto com o
    # redimensionamento da janela Win98 (a cadeia inteira usa % / vh, sem
    # nenhum valor fixo em px que travasse no tamanho antigo).
    'padding': '8px', 'fontFamily': FONTS['body'], 'background': C['window'],
    'height': '100vh', 'overflowY': 'auto', 'boxSizing': 'border-box',
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
    'estrategias': lambda: html.Div(id='diario98-estrategias-conteudo'),
    'padroes': _construir_diario98_padroes,
    'bem_estar': lambda: html.Div(id='diario98-bemestar-conteudo'),
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
    Output('diario98-estrategias-conteudo', 'children'),
    Input('store-diario98-aba', 'data'),
)
def atualizar_estrategias_98(aba_ativa):
    if aba_ativa != 'estrategias':
        return no_update
    return _construir_diario98_estrategias()


@app.callback(
    Output('diario98-tabela-estrategias', 'children'),
    Output('diario98-estrategia-status', 'children'),
    Output('diario98-nova-estrategia-nome', 'value'),
    Output('diario98-nova-estrategia-desc', 'value'),
    Output('diario98-nova-estrategia-checklist', 'value'),
    Output('store-diario98-estrategia-editando', 'data'),
    Output('btn-diario98-add-estrategia', 'children'),
    Output('btn-diario98-cancelar-edicao-estrategia', 'style'),
    Output('diario98-estrategia-editando-aviso', 'children'),
    Input('btn-diario98-add-estrategia', 'n_clicks'),
    Input({'type': 'diario98-estrategia-desativar', 'index': ALL}, 'n_clicks'),
    Input({'type': 'diario98-estrategia-editar', 'index': ALL}, 'n_clicks'),
    Input('btn-diario98-cancelar-edicao-estrategia', 'n_clicks'),
    State('diario98-nova-estrategia-nome', 'value'),
    State('diario98-nova-estrategia-desc', 'value'),
    State('diario98-nova-estrategia-checklist', 'value'),
    State('store-diario98-estrategia-editando', 'data'),
    prevent_initial_call=True,
)
def gerenciar_estrategias_98(n_add, n_desativar, n_editar, n_cancelar, nome, descricao, checklist_texto, editando_id):
    """Ver diario_app_classico.py::gerenciar_estrategias pro mesmo
    raciocínio completo (form único servindo criar/editar, feedback
    2026-07-20)."""
    ctx = callback_context
    if not ctx.triggered:
        return (no_update,) * 9
    origem = ctx.triggered[0]['prop_id']
    prop_curto = origem.rsplit('.', 1)[0]
    estilo_cancelar_oculto = {**win98_button(), 'marginLeft': '6px', 'display': 'none'}
    estilo_cancelar_visivel = {**win98_button(), 'marginLeft': '6px', 'display': 'inline-block', 'color': C['gold']}

    if prop_curto == 'btn-diario98-cancelar-edicao-estrategia':
        if not n_cancelar:
            return (no_update,) * 9
        return (no_update, 'Edição cancelada.', '', '', '', None,
                'Adicionar Estratégia', estilo_cancelar_oculto, None)

    if prop_curto == 'btn-diario98-add-estrategia':
        if not nome or not nome.strip():
            return (no_update, '⚠ Informe um nome.') + (no_update,) * 7
        checklist = [linha.strip() for linha in (checklist_texto or '').split('\n') if linha.strip()]
        if editando_id:
            dt.editar_estrategia(editando_id, nome=nome.strip(), descricao=descricao or '', checklist=checklist)
            return (_tabela_estrategias_98(dt.listar_estrategias()), f'✔ "{nome.strip()}" atualizada.',
                    '', '', '', None, 'Adicionar Estratégia', estilo_cancelar_oculto, None)
        dt.criar_estrategia(nome, descricao or '', checklist)
        return (_tabela_estrategias_98(dt.listar_estrategias()), f'✔ "{nome.strip()}" cadastrada.',
                '', '', '', no_update, no_update, no_update, no_update)

    if prop_curto.startswith('{'):
        id_componente = json.loads(prop_curto)
        if id_componente['type'] == 'diario98-estrategia-editar':
            if not any(n_editar or []):
                return (no_update,) * 9
            df = dt.listar_estrategias(somente_ativas=False)
            linha = df[df['id'] == id_componente['index']]
            if linha.empty:
                return (no_update,) * 9
            linha0 = linha.iloc[0]
            checklist_texto_novo = '\n'.join(_lista_segura_98(linha0['checklist']))
            aviso = html.Div(f'✏ Editando "{linha0["nome"]}" (ID {linha0["id"]}).',
                              style={'fontSize': '10px', 'color': C['gold'], 'marginBottom': '6px'})
            return (no_update, '', linha0['nome'], linha0['descricao'] or '', checklist_texto_novo,
                    int(linha0['id']), 'Salvar Alterações', estilo_cancelar_visivel, aviso)
        if id_componente['type'] == 'diario98-estrategia-desativar':
            if not any(n_desativar or []):
                return (no_update,) * 9
            dt.desativar_estrategia(id_componente['index'])
            return (_tabela_estrategias_98(dt.listar_estrategias()), '✔ Estratégia desativada.',
                    no_update, no_update, no_update, no_update, no_update, no_update, no_update)
    return (no_update,) * 9


@app.callback(
    Output('diario98-tabela-rr', 'children'),
    Output('diario98-rr-status', 'children'),
    Output('diario98-novo-rr-nome', 'value'),
    Output('diario98-novo-rr-alvo', 'value'),
    Output('diario98-novo-rr-stop', 'value'),
    Output('diario98-novo-rr-be', 'value'),
    Input('btn-diario98-add-rr', 'n_clicks'),
    Input({'type': 'diario98-rr-desativar', 'index': ALL}, 'n_clicks'),
    State('diario98-novo-rr-nome', 'value'),
    State('diario98-novo-rr-alvo', 'value'),
    State('diario98-novo-rr-stop', 'value'),
    State('diario98-novo-rr-be', 'value'),
    prevent_initial_call=True,
)
def gerenciar_rr_presets_98(n_add, n_desativar, nome, alvo, stop, be):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario98-add-rr'):
        alvo, stop, be = _num(alvo), _num(stop), _num(be)
        if not nome or not nome.strip() or alvo is None or stop is None:
            return no_update, '⚠ Informe nome, alvo e stop.', no_update, no_update, no_update, no_update
        dt.criar_rr_preset(nome, alvo, stop, be)
        return _tabela_rr_presets_98(dt.listar_rr_presets()), f'✔ "{nome.strip()}" cadastrado.', '', None, None, None
    if not any(n_desativar or []):
        return no_update, no_update, no_update, no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_rr_preset(id_componente['index'])
    return _tabela_rr_presets_98(dt.listar_rr_presets()), '✔ Preset desativado.', no_update, no_update, no_update, no_update


@app.callback(
    Output('diario98-tabela-tags', 'children'),
    Output('diario98-tag-status', 'children'),
    Output('diario98-nova-tag-nome', 'value'),
    Input('btn-diario98-add-tag', 'n_clicks'),
    Input({'type': 'diario98-tag-desativar', 'index': ALL}, 'n_clicks'),
    State('diario98-nova-tag-nome', 'value'),
    State('diario98-nova-tag-tipo', 'value'),
    prevent_initial_call=True,
)
def gerenciar_tags_98(n_add, n_desativar, nome, tipo):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario98-add-tag.'):
        if not nome or not nome.strip():
            return no_update, '⚠ Informe um nome.', no_update
        dt.criar_tag_setup_erro(nome, tipo or 'ambos')
        return _tabela_tags_98(dt.listar_tags_setup_erro()), f'✔ "{nome.strip()}" cadastrada.', ''
    if not any(n_desativar or []):
        return no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_tag_setup_erro(id_componente['index'])
    return _tabela_tags_98(dt.listar_tags_setup_erro()), '✔ Tag desativada.', no_update


@app.callback(
    Output('diario98-tabela-tags-emocionais', 'children'),
    Output('diario98-tag-emocional-status', 'children'),
    Output('diario98-nova-tag-emocional-nome', 'value'),
    Input('btn-diario98-add-tag-emocional', 'n_clicks'),
    Input({'type': 'diario98-tag-emocional-desativar', 'index': ALL}, 'n_clicks'),
    State('diario98-nova-tag-emocional-nome', 'value'),
    prevent_initial_call=True,
)
def gerenciar_tags_emocionais_98(n_add, n_desativar, nome):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario98-add-tag-emocional'):
        if not nome or not nome.strip():
            return no_update, '⚠ Informe um nome.', no_update
        dt.criar_tag_emocional(nome)
        return _tabela_tags_emocionais_98(dt.listar_tags_emocionais()), f'✔ "{nome.strip()}" cadastrada.', ''
    if not any(n_desativar or []):
        return no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_tag_emocional(id_componente['index'])
    return _tabela_tags_emocionais_98(dt.listar_tags_emocionais()), '✔ Tag desativada.', no_update


@app.callback(
    Output('diario98-alvo', 'value'),
    Output('diario98-stop', 'value'),
    Input('diario98-rr-preset', 'value'),
    prevent_initial_call=True,
)
def aplicar_rr_preset_98(rr_valor):
    preset = dt.obter_rr_preset(rr_valor)
    if preset is None:
        return no_update, no_update
    return preset['alvo_pontos'], preset['stop_pontos']


@app.callback(
    Output('diario98-rr-preset', 'value', allow_duplicate=True),
    Input('diario98-alvo', 'value'),
    Input('diario98-stop', 'value'),
    State('diario98-rr-preset', 'value'),
    prevent_initial_call=True,
)
def resetar_rr_se_editado_98(alvo, stop, rr_atual):
    """Se o Alvo/Stop mudar pra algo diferente do que o preset selecionado
    prescreve, volta o seletor pra 'nenhuma' — mas NÃO reseta se o valor
    ainda bate com o preset (isso é só o próprio auto-preenchimento do
    callback acima refletindo de volta aqui, não uma edição manual de
    verdade). Comparar contra o preset (não usar `triggered_id`) é o que
    evita um loop: Alvo/Stop também são Input deste callback porque
    precisam disparar o reset quando editados à mão."""
    if not rr_atual:
        return no_update
    preset = dt.obter_rr_preset(rr_atual)
    if preset is None:
        return no_update
    if _num(alvo) == preset['alvo_pontos'] and _num(stop) == preset['stop_pontos']:
        return no_update
    return None


@app.callback(
    Output('diario98-sentimento-label', 'children'),
    Output('diario98-sentimento-label', 'style'),
    Input('diario98-sentimento-mercado', 'value'),
)
def atualizar_label_sentimento_98(valor):
    resultado = dt.classificar_sentimento_mercado(valor)
    if resultado is None:
        return '—', {'fontSize': '11px', 'color': C['text2']}
    nome, cor_chave = resultado
    return nome, {'fontSize': '11px', 'fontWeight': '700', 'color': C[cor_chave]}


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
    State('diario98-emo-confianca', 'value'),
    State('diario98-emo-humor', 'value'),
    State('diario98-emo-energia', 'value'),
    State('diario98-emo-atencao', 'value'),
    State('diario98-emo-presenca', 'value'),
    State('diario98-tags-emocionais', 'value'),
    State('diario98-sentimento-mercado', 'value'),
    State('diario98-upload-imagem', 'contents'),
    State('store-diario98-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def salvar_novo_trade_98(n_clicks, data_op, ativo, janela, estrategia_tags, sala, tipo_op, alvo, stop, lote,
                          preco_entrada, preco_saida, tipo_saida, resultado, saldo, resumo, obs,
                          nivel_sr, nivel_sr_tipo, tags_setup_erro,
                          emo_confianca, emo_humor, emo_energia, emo_atencao, emo_presenca,
                          tags_emocionais, sentimento_mercado, upload_contents, imagem_colada):
    if not data_op or not ativo:
        return '⚠ Preencha ao menos Data e Ativo.'

    alvo, stop, preco_entrada, preco_saida, resultado, saldo, nivel_sr = (
        _num(alvo), _num(stop), _num(preco_entrada), _num(preco_saida),
        _num(resultado), _num(saldo), _num(nivel_sr),
    )
    campos = dict(
        data_op=data_op, ativo=ativo, janela_pregao=janela,
        estrategia_tags=estrategia_tags or [], sala_analise=sala,
        tipo_operacao=tipo_op, alvo_pontos=alvo, stop_pontos=stop, lote=lote or 1,
        preco_entrada=preco_entrada, preco_saida=preco_saida, tipo_saida=tipo_saida,
        resultado_pontos=resultado, saldo_operacao_reais=saldo,
        resumo_operacional=resumo, obs=obs,
        nivel_sr_referencia=nivel_sr, nivel_sr_tipo=nivel_sr_tipo,
        tags_setup_erro=tags_setup_erro or [],
        emo_confianca=emo_confianca, emo_humor=emo_humor, emo_energia=emo_energia,
        emo_atencao=emo_atencao, emo_presenca=emo_presenca,
        tags_emocionais=tags_emocionais or [], sentimento_mercado=sentimento_mercado,
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
    Output('diario98-emo-confianca', 'value'),
    Output('diario98-emo-humor', 'value'),
    Output('diario98-emo-energia', 'value'),
    Output('diario98-emo-atencao', 'value'),
    Output('diario98-emo-presenca', 'value'),
    Output('diario98-tags-emocionais', 'value'),
    Output('diario98-sentimento-mercado', 'value'),
    Output('diario98-preview-imagem', 'children', allow_duplicate=True),
    Output('store-diario98-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-diario98-limpar', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_form_diario98(n_clicks):
    return (None, None, None, None, None, None, None, None, 1,
            None, None, None, None, None, None, None, None, None, None,
            3, 3, 3, 3, 3, None, 50,
            None, None)


@app.callback(
    Output('diario98-historico-tabela', 'children'),
    Output('diario98-hist-banner-filtro-cal', 'children'),
    Input('diario98-hist-ativo', 'value'),
    Input('diario98-hist-janela', 'value'),
    Input('diario98-hist-estrategia', 'value'),
    Input('diario98-hist-data-ini', 'value'),
    Input('diario98-hist-data-fim', 'value'),
    Input('diario98-hist-filtro-data-calendario', 'data'),
)
def atualizar_historico_98(ativo, janela, estrategia, data_ini, data_fim, filtro_cal):
    # O clique num dia do calendário (Estatísticas) força um filtro de dia
    # único aqui, sobrepondo o filtro manual de intervalo — `filtro_cal` só
    # é setado por `clicar_dia_calendario_98` e limpo pelo botão do banner.
    data_ini_efetivo = filtro_cal or data_ini
    data_fim_efetivo = filtro_cal or data_fim
    df = dt.listar_trades(ativo=ativo, janela=janela, estrategia_tag=estrategia,
                           data_ini=data_ini_efetivo, data_fim=data_fim_efetivo, is_simulated=False)
    df = df.sort_values('data_op', ascending=False).head(200 if filtro_cal else 50)

    banner = html.Div([
        html.Span(f'Filtrando pelo dia {filtro_cal} (clique no calendário). ',
                  style={'fontSize': '11px', 'color': C['text2']}),
        html.Button('Limpar', id='btn-diario98-hist-limpar-filtro-cal', n_clicks=0,
                    className='win98-btn', style={**win98_button(), 'minHeight': '18px', 'padding': '0 8px'}),
    ], style={'display': 'flex', 'alignItems': 'center', 'gap': '6px'}) if filtro_cal else ''

    if df.empty:
        return html.Div('Nenhuma operação encontrada.', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        }), banner
    aderencia_df = dt.avaliar_aderencia_sr()
    aderencia_por_trade = (
        dict(zip(aderencia_df['id_trade'].astype(int), aderencia_df['classificacao']))
        if not aderencia_df.empty else {}
    )
    mapa_nomes_estrategias = _mapa_nomes_estrategias_98()
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['Data', 'Ativo', 'Janela', 'Estratégia', 'Tipo', 'Pts', 'Saldo', 'Print', 'Aderência S/R', '']
    ])
    linhas = []
    for _, row in df.iterrows():
        linhas.extend(_linha_historico_98(row, aderencia_por_trade, mapa_nomes_estrategias))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'}), banner


@app.callback(
    Output('diario98-hist-filtro-data-calendario', 'data', allow_duplicate=True),
    Input('btn-diario98-hist-limpar-filtro-cal', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_filtro_calendario_98(n_clicks):
    return None


@app.callback(
    Output({'type': 'diario98-detalhe-linha', 'index': dash.MATCH}, 'children'),
    Output({'type': 'diario98-btn-expandir', 'index': dash.MATCH}, 'children'),
    Input({'type': 'diario98-btn-expandir', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'diario98-btn-expandir', 'index': dash.MATCH}, 'children'),
    State({'type': 'diario98-btn-expandir', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def alternar_detalhe_historico_98(n_clicks, seta_atual, id_componente):
    """Ver diario_app_classico.py::alternar_detalhe_historico pro
    raciocínio completo — substitui o popup antigo."""
    if seta_atual == '▶':
        conteudo = _conteudo_detalhe_trade_98(id_componente['index'])
        return conteudo, '▼'
    return None, '▶'


@app.callback(
    Output({'type': 'ea98-imagem-principal', 'index': dash.MATCH}, 'data'),
    Input({'type': 'ea98-imagem-thumb-escolher', 'index': dash.MATCH, 'nome': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def escolher_imagem_principal_98(n_clicks_lista):
    """Ver diario_app_classico.py::escolher_imagem_principal."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    id_componente = json.loads(ctx.triggered[0]['prop_id'].rsplit('.', 1)[0])
    return id_componente['nome']


@app.callback(
    Output({'type': 'ea98-imagem-thumb-escolher', 'index': dash.MATCH, 'nome': ALL}, 'style'),
    Input({'type': 'ea98-imagem-principal', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea98-imagem-thumb-escolher', 'index': dash.MATCH, 'nome': ALL}, 'id'),
)
def destacar_imagem_principal_98(selecionada, ids_lista):
    return [_estilo_thumb_imagem_principal_98(item_id['nome'] == selecionada) for item_id in ids_lista]


@app.callback(
    Output('diario98-imagem-ampliada', 'data'),
    Input({'type': 'diario98-img-thumb', 'index': ALL}, 'n_clicks'),
    Input({'type': 'diario98-img-thumb-detalhe', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def ampliar_imagem_98(n_clicks_lista, n_clicks_lista_detalhe):
    """Ver diario_app_classico.py::ampliar_imagem pro mesmo raciocínio (2
    Inputs pattern-matched porque a miniatura da linha e a imagem do
    detalhe expandido podem coexistir na página ao mesmo tempo, e a lista
    de imagens/navegação, feedback 2026-07-20)."""
    ctx = callback_context
    if not ctx.triggered or not any((n_clicks_lista or []) + (n_clicks_lista_detalhe or [])):
        return no_update
    id_componente = json.loads(ctx.triggered[0]['prop_id'].rsplit('.', 1)[0])
    df = dt.listar_trades()
    linha = df[df['id_trade'] == id_componente['index']]
    if linha.empty or not linha.iloc[0].get('imagem_arquivo'):
        return no_update
    linha0 = linha.iloc[0]
    principal = linha0['imagem_arquivo']
    adicionais = [nome for nome in _lista_segura_98(linha0.get('imagens_adicionais')) if nome != principal]
    imagens = [principal] + adicionais
    return {'imagens': [f"/diario-imagens/{nome}" for nome in imagens], 'indice': 0}


@app.callback(
    Output('diario98-imagem-ampliada', 'data', allow_duplicate=True),
    Input('btn-diario98-fechar-imagem', 'n_clicks'),
    Input('diario98-overlay-imagem-backdrop', 'n_clicks'),
    prevent_initial_call=True,
)
def fechar_imagem_ampliada_98(n1, n2):
    return None


@app.callback(
    Output('diario98-imagem-ampliada', 'data', allow_duplicate=True),
    Input('btn-diario98-imagem-anterior', 'n_clicks'),
    Input('btn-diario98-imagem-proxima', 'n_clicks'),
    State('diario98-imagem-ampliada', 'data'),
    prevent_initial_call=True,
)
def navegar_imagem_ampliada_98(_n_anterior, _n_proxima, atual):
    """Ver diario_app_classico.py::navegar_imagem_ampliada."""
    if not atual or not atual.get('imagens'):
        return no_update
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    total = len(atual['imagens'])
    indice = atual.get('indice', 0)
    if origem == 'btn-diario98-imagem-anterior':
        indice = (indice - 1) % total
    elif origem == 'btn-diario98-imagem-proxima':
        indice = (indice + 1) % total
    else:
        return no_update
    return {**atual, 'indice': indice}


@app.callback(
    Output('diario98-overlay-imagem', 'children'),
    Input('diario98-imagem-ampliada', 'data'),
)
def renderizar_overlay_imagem_98(dados):
    if not dados or not dados.get('imagens'):
        return None
    imagens = dados['imagens']
    indice = dados.get('indice', 0)
    topo = [
        html.Button('✕ Fechar', id='btn-diario98-fechar-imagem', n_clicks=0,
                    className='win98-btn', style=win98_button()),
    ]
    if len(imagens) > 1:
        topo.append(html.Div([
            html.Button('◀ Anterior', id='btn-diario98-imagem-anterior', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Span(f'{indice + 1} / {len(imagens)}', style={'fontSize': '11px', 'color': C['text2']}),
            html.Button('Próxima ▶', id='btn-diario98-imagem-proxima', n_clicks=0,
                        className='win98-btn', style=win98_button()),
        ], style={'display': 'flex', 'gap': '10px', 'alignItems': 'center'}))
    # Ver diario_app_classico.py::renderizar_overlay_imagem pro raciocínio
    # completo (zoom/pan em assets_98/diario_zoom.js, key força remontar
    # <img> novo por imagem pra não vazar zoom/pan entre navegações).
    filhos = [
        html.Div(topo, style={'display': 'flex', 'flexDirection': 'column', 'gap': '6px', 'flexShrink': '0'}),
        html.Div(
            html.Img(src=imagens[indice], className='diario-zoom-img', draggable='false', key=imagens[indice]),
            className='diario-zoom-container', key=imagens[indice],
            style={'flex': '1', 'minHeight': '0', 'marginTop': '6px', 'border': f"2px solid {C['shadow_dk']}"},
        ),
    ]
    return _overlay_98(html.Div(filhos, style={'display': 'flex', 'flexDirection': 'column', 'height': '100%'}),
                        'diario98-overlay-imagem-backdrop')


@app.callback(
    Output('diario98-estatisticas-conteudo', 'children'),
    Input('store-diario98-aba', 'data'),
)
def atualizar_estatisticas_98(aba_ativa):
    if aba_ativa != 'estatisticas':
        return no_update
    return _construir_diario98_estatisticas(dt.calcular_estatisticas(), dt.calcular_score_confiabilidade())


@app.callback(
    Output('diario98-bemestar-conteudo', 'children'),
    Input('store-diario98-aba', 'data'),
)
def atualizar_bem_estar_98(aba_ativa):
    if aba_ativa != 'bem_estar':
        return no_update
    return _construir_diario98_bem_estar()


@app.callback(
    Output('diario98-checkin-status', 'children'),
    Output('diario98-gauge-hawkins', 'children'),
    Output('diario98-tabela-checkins', 'children'),
    Input('btn-diario98-salvar-checkin', 'n_clicks'),
    State('diario98-checkin-data', 'value'),
    State('diario98-checkin-sono', 'value'),
    State('diario98-checkin-cafe', 'value'),
    State('diario98-checkin-hora-dormir', 'value'),
    State('diario98-checkin-hora-acordar', 'value'),
    State('diario98-checkin-autocuidado', 'value'),
    State('diario98-checkin-meditacao', 'value'),
    State('diario98-checkin-obs', 'value'),
    prevent_initial_call=True,
)
def salvar_checkin_98(n_clicks, data_str, sono, cafe, hora_dormir, hora_acordar,
                       autocuidado, meditacao, obs):
    if not n_clicks or not data_str:
        return no_update, no_update, no_update
    campos = {
        'sono_qualidade': sono, 'cafe_qualidade': cafe,
        'sono_hora_dormir': hora_dormir, 'sono_hora_acordar': hora_acordar,
        'autocuidado_minutos': autocuidado, 'meditacao_minutos': meditacao,
        'observacoes': obs,
    }
    ok = dt.salvar_checkin_dia(data_str, campos)
    if not ok:
        return '⚠ Não foi possível salvar (tente novamente).', no_update, no_update
    return ('✔ Check-in salvo.', _bloco_gauge_hawkins_98(date.fromisoformat(data_str)),
            _tabela_checkins_98(dt.listar_checkins()))


@app.callback(
    Output('diario98-radar-roda', 'figure'),
    Output('diario98-roda-status', 'children'),
    Output('diario98-tabela-roda-vida', 'children'),
    Input('btn-diario98-salvar-roda', 'n_clicks'),
    State({'type': 'diario98-roda-area', 'index': ALL}, 'value'),
    State({'type': 'diario98-roda-area', 'index': ALL}, 'id'),
    prevent_initial_call=True,
)
def salvar_roda_vida_98(n_clicks, valores, ids):
    if not n_clicks:
        return no_update, no_update, no_update
    campos = {id_['index']: valor for id_, valor in zip(ids, valores)}
    dt.criar_registro_roda_vida(date.today(), campos)
    ultimo = dt.obter_ultimo_registro_roda_vida()
    return (_radar_roda_vida_98(ultimo), '✔ Registro salvo.',
            _tabela_roda_vida_98(dt.listar_registros_roda_vida()))


@app.callback(
    Output('diario98-radar-roda', 'figure', allow_duplicate=True),
    Input({'type': 'diario98-roda-area', 'index': ALL}, 'value'),
    State({'type': 'diario98-roda-area', 'index': ALL}, 'id'),
    prevent_initial_call=True,
)
def atualizar_radar_ao_vivo_98(valores, ids):
    """Redesenha o radar a cada slider movido — só preview, não grava nada
    (o registro só é criado de fato no clique de "Salvar Novo Registro",
    ver `salvar_roda_vida_98`). Sem isso, mexer nos sliders não muda nada
    na tela até salvar, o que lia como "não funciona"."""
    campos = {id_['index']: valor for id_, valor in zip(ids, valores)}
    return _radar_roda_vida_98(campos)


@app.callback(
    Output('diario98-calendario-mes', 'data'),
    Input('btn-diario98-cal-mes-prev', 'n_clicks'),
    Input('btn-diario98-cal-mes-next', 'n_clicks'),
    Input('btn-diario98-cal-ano-prev', 'n_clicks'),
    Input('btn-diario98-cal-ano-next', 'n_clicks'),
    State('diario98-calendario-mes', 'data'),
    prevent_initial_call=True,
)
def navegar_calendario_98(n_mes_prev, n_mes_next, n_ano_prev, n_ano_next, mes_atual):
    """Ver diario_app_classico.py::navegar_calendario_classico pro mesmo
    raciocínio completo (feedback 2026-07-12: setas de mês e ano viraram
    controles totalmente separados, cada uma sempre com o mesmo
    significado — não dependem mais de `diario98-modo-ano`)."""
    ctx = callback_context
    if not ctx.triggered or not mes_atual:
        return no_update
    trigger = ctx.triggered[0]
    if not trigger['value']:
        # Um clique de verdade SEMPRE incrementa `n_clicks` (nunca zera
        # sozinho) — um trigger com valor 0 só pode vir do botão tendo sido
        # recriado (Dash reseta `n_clicks` de componente novo pra 0), nunca
        # de ação do usuário. Ver `_construir_diario98_estatisticas` — com
        # os botões agora fixos isso não deveria mais acontecer, guarda
        # defensiva contra o mesmo bug voltando.
        return no_update
    ano, mes = (int(x) for x in mes_atual.split('-'))
    origem = trigger['prop_id'].split('.')[0]
    if origem in ('btn-diario98-cal-mes-prev', 'btn-diario98-cal-mes-next'):
        passo = -1 if origem == 'btn-diario98-cal-mes-prev' else 1
        mes += passo
        if mes == 0:
            mes, ano = 12, ano - 1
        elif mes == 13:
            mes, ano = 1, ano + 1
    else:
        passo = -1 if origem == 'btn-diario98-cal-ano-prev' else 1
        ano += passo
    return f"{ano:04d}-{mes:02d}"


@app.callback(
    Output('diario98-modo-ano', 'data'),
    Input('btn-diario98-modo-ano', 'n_clicks'),
    State('diario98-modo-ano', 'data'),
    prevent_initial_call=True,
)
def alternar_modo_ano_98(n_clicks, modo_atual):
    if not n_clicks:  # ver comentário em navegar_calendario_98
        return no_update
    return not bool(modo_atual)


@app.callback(
    Output('diario98-cards-conteudo', 'children'),
    Output('diario98-calendario-grid', 'children'),
    Output('diario98-calendario-label-mes', 'children'),
    Output('diario98-calendario-label-ano', 'children'),
    Output('btn-diario98-modo-ano', 'children'),
    Output('btn-diario98-modo-ano', 'style'),
    Output('diario98-resto-periodo-conteudo', 'children'),
    Input('diario98-calendario-mes', 'data'),
    Input('diario98-modo-ano', 'data'),
)
def atualizar_periodo_estatisticas_98(mes_atual, modo_ano):
    """Recalcula estatísticas + calendário toda vez que o mês/ano navegado ou
    o toggle Ano Corrente mudam — os números da aba Estatísticas agora
    acompanham o seletor do calendário (pedido do Jesué), não são mais
    all-time fixo. Só atualiza `children`/`style` de componentes que já
    existem (criados 1x em `_construir_diario98_estatisticas`) — NUNCA
    substitui os botões de navegação inteiros, pra não repetir o bug de
    `n_clicks` resetando (ver comentário lá). Labels de mês/ano são
    atualizados SEMPRE, independente de `modo_ano` — ver
    `navegar_calendario_98`."""
    if not mes_atual:
        return (no_update,) * 7
    ano, mes = (int(x) for x in mes_atual.split('-'))
    modo_ano = bool(modo_ano)
    if modo_ano:
        stats = dt.calcular_estatisticas(ano=ano)
        grade = _grade_calendario_anual_98(stats['por_mes'], ano)
    else:
        stats = dt.calcular_estatisticas(ano=ano, mes=mes)
        grade = _grade_calendario_98(stats['por_dia'], ano, mes)
    return (
        _cards_ou_vazio_estatisticas_98(stats),
        grade,
        _NOMES_MES[mes],
        str(ano),
        'Ver por Mês' if modo_ano else 'Ver Ano Inteiro',
        {**win98_button(pressed=modo_ano), 'marginLeft': 'auto'},
        _resto_periodo_estatisticas_98(stats),
    )


@app.callback(
    Output('store-diario98-aba', 'data', allow_duplicate=True),
    Output('diario98-hist-filtro-data-calendario', 'data'),
    Input({'type': 'diario98-cal-dia', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def clicar_dia_calendario_98(n_clicks_lista):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update, no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    componente_id = json.loads(prop_id)
    return 'historico', componente_id['index']


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
    Output('store-em-andamento98-ids-conhecidos', 'data'),
    Input('interval-em-andamento98', 'n_intervals'),
    Input('store-diario98-aba', 'data'),
    Input('store-posicao98-ativo-subaba', 'data'),
    State('store-em-andamento98-ids-conhecidos', 'data'),
)
def atualizar_em_andamento_98(n, aba_ativa, ativo_selecionado, ids_conhecidos):
    """Ver diario_app_classico.py::atualizar_em_andamento pro raciocínio
    completo (junta os dois Outputs antigos num só, guiado pelo interval de
    6s — só reconstrói de verdade quando o CONJUNTO de posições muda,
    preservando qualquer campo em edição no meio tempo)."""
    ctx = callback_context
    disparado_por_interval = bool(ctx.triggered) and ctx.triggered[0]['prop_id'] == 'interval-em-andamento98.n_intervals'
    if aba_ativa != 'posicao':
        return no_update, no_update

    posicoes = dt.listar_posicoes_abertas()
    _, por_ativo_abertas, por_ativo_prontas = _preparar_registros_posicao_98(posicoes)
    ativo = ativo_selecionado or 'WIN'
    ids_agora = _ids_rastreados_98(_registros_por_ativo_98(por_ativo_prontas, ativo),
                                    _registros_por_ativo_98(por_ativo_abertas, ativo))
    if disparado_por_interval and ids_agora == (ids_conhecidos or []):
        return no_update, no_update
    return _renderizar_posicao_completa_98(posicoes, dt.listar_ordens_pendentes(), ativo), ids_agora


@app.callback(
    Output('store-posicao98-ativo-subaba', 'data'),
    Input('btn-posicao98-ativo-WIN', 'n_clicks'),
    Input('btn-posicao98-ativo-WDO', 'n_clicks'),
    prevent_initial_call=True,
)
def selecionar_subaba_posicao_98(n_win, n_wdo):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    id_disparado = ctx.triggered[0]['prop_id'].split('.')[0]
    return 'WIN' if id_disparado == 'btn-posicao98-ativo-WIN' else 'WDO'


@app.callback(
    Output('btn-posicao98-ativo-WIN', 'style'),
    Output('btn-posicao98-ativo-WDO', 'style'),
    Input('store-posicao98-ativo-subaba', 'data'),
)
def atualizar_estilo_subaba_posicao_98(ativo_ativo):
    return win98_button(pressed=(ativo_ativo == 'WIN')), win98_button(pressed=(ativo_ativo == 'WDO'))



@app.callback(
    Output({'type': 'ea98-nota', 'index': dash.MATCH}, 'value'),
    Output({'type': 'ea98-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Input({'type': 'ea98-nota', 'index': dash.MATCH}, 'n_blur'),
    State({'type': 'ea98-nota', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-nota', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def salvar_nota_em_andamento_98(_n_blur, nota, id_componente):
    """Ver diario_app_classico.py::salvar_nota_em_andamento pro mesmo
    raciocínio (salva ao sair do campo, limpa a caixa depois)."""
    if not nota or not nota.strip():
        return no_update, no_update
    ok = dt.atualizar_rascunho(_ticket_alvo_98(id_componente['index']), nota_texto=nota)
    if not ok:
        return no_update, '⚠ Ocupado — tente salvar a nota de novo.'
    return '', '✔ Nota salva.'


@app.callback(
    Output({'type': 'ea98-status', 'index': dash.MATCH}, 'children'),
    Input({'type': 'ea98-estrategia', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-sala', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-alvo', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-stop', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-nivel-sr', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-resumo', 'index': dash.MATCH}, 'n_blur'),
    Input({'type': 'ea98-emo-confianca', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-emo-humor', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-emo-energia', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-emo-atencao', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-emo-presenca', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-sentimento-mercado', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-resumo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'options'),
    State({'type': 'ea98-estrategia', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def auto_salvar_campos_em_andamento_98(estrategia, sala, alvo, stop, nivel_sr, _resumo_blur,
                                        emo_confianca, emo_humor, emo_energia, emo_atencao, emo_presenca,
                                        tags_emocionais, sentimento_mercado,
                                        resumo, opcoes_tags_emocionais, id_componente):
    """Ver diario_app_classico.py::auto_salvar_campos_em_andamento pro
    raciocínio completo (substitui o botão "Salvar" — achado real
    2026-07-20: trade fechado virou "Pronta pra Finalizar" em branco por
    falta de auto-save, não por bug de amarração de ordens)."""
    alvo, stop, nivel_sr = _num(alvo), _num(stop), _num(nivel_sr)
    campos = {}
    if estrategia is not None:
        campos['estrategia_tags'] = estrategia
    if sala is not None:
        campos['sala_analise'] = sala
    if alvo is not None:
        campos['alvo_pontos'] = alvo
    if stop is not None:
        campos['stop_pontos'] = stop
    if nivel_sr is not None:
        campos['nivel_sr_referencia'] = nivel_sr
    if resumo is not None:
        campos['resumo_operacional'] = resumo
    if emo_confianca is not None:
        campos['emo_confianca'] = emo_confianca
    if emo_humor is not None:
        campos['emo_humor'] = emo_humor
    if emo_energia is not None:
        campos['emo_energia'] = emo_energia
    if emo_atencao is not None:
        campos['emo_atencao'] = emo_atencao
    if emo_presenca is not None:
        campos['emo_presenca'] = emo_presenca
    if tags_emocionais is not None:
        campos['tags_emocionais'] = tags_emocionais
    if sentimento_mercado is not None:
        campos['sentimento_mercado'] = sentimento_mercado
    if not campos:
        return no_update

    if tags_emocionais:
        slugs_cadastrados = set(dt.listar_tags_emocionais()['slug'])
        rotulos = {o['value']: o['label'] for o in (opcoes_tags_emocionais or [])}
        for slug in tags_emocionais:
            if slug not in slugs_cadastrados:
                dt.criar_tag_emocional(rotulos.get(slug, slug))

    ok = dt.atualizar_rascunho(_ticket_alvo_98(id_componente['index']), campos=campos)
    return '✔ Salvo automaticamente.' if ok else '⚠ Ocupado — tente de novo.'


@app.callback(
    Output({'type': 'ea98-sentimento-label', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea98-sentimento-label', 'index': dash.MATCH}, 'style'),
    Input({'type': 'ea98-sentimento-mercado', 'index': dash.MATCH}, 'value'),
)
def atualizar_label_sentimento_posicao_98(valor):
    resultado = dt.classificar_sentimento_mercado(valor)
    if resultado is None:
        return '—', {'fontSize': '11px', 'color': C['text2']}
    nome, cor_chave = resultado
    return nome, {'fontSize': '11px', 'fontWeight': '700', 'color': C[cor_chave]}


@app.callback(
    Output({'type': 'ea98-alvo', 'index': dash.MATCH}, 'value'),
    Output({'type': 'ea98-stop', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-rr-preset', 'index': dash.MATCH}, 'value'),
    prevent_initial_call=True,
)
def aplicar_rr_preset_posicao_98(rr_valor):
    preset = dt.obter_rr_preset(rr_valor)
    if preset is None:
        return no_update, no_update
    return preset['alvo_pontos'], preset['stop_pontos']


@app.callback(
    Output({'type': 'ea98-rr-preset', 'index': dash.MATCH}, 'value', allow_duplicate=True),
    Input({'type': 'ea98-alvo', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-stop', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-rr-preset', 'index': dash.MATCH}, 'value'),
    prevent_initial_call=True,
)
def resetar_rr_posicao_se_editado_98(alvo, stop, rr_atual):
    """Mesma lógica de `resetar_rr_se_editado_98` (Novo Trade) — ver esse
    pro raciocínio completo de por que compara contra o preset em vez de
    usar `triggered_id` (evita loop de re-disparo)."""
    if not rr_atual:
        return no_update
    preset = dt.obter_rr_preset(rr_atual)
    if preset is None:
        return no_update
    if _num(alvo) == preset['alvo_pontos'] and _num(stop) == preset['stop_pontos']:
        return no_update
    return None


@app.callback(
    Output({'type': 'ea98-rr-calculado', 'index': dash.MATCH}, 'children'),
    Input({'type': 'ea98-alvo', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea98-stop', 'index': dash.MATCH}, 'value'),
)
def calcular_rr_em_andamento_98(alvo, stop):
    """Ver diario_app_classico.py::calcular_rr_em_andamento pro mesmo
    raciocínio (R:R derivado sozinho de Alvo/Stop, complementa o preset)."""
    alvo_num, stop_num = _num(alvo), _num(stop)
    if not alvo_num or not stop_num:
        return '—'
    return f'{alvo_num / stop_num:.2f} : 1'


@app.callback(
    Output({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'value'),
    Output({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'options'),
    Output({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'search_value'),
    Input({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'search_value'),
    State({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'options'),
    prevent_initial_call=True,
)
def criar_tag_emocional_digitada_match_98(search_value, valor_atual, opcoes_atuais):
    """Ver diario_app_classico.py::criar_tag_emocional_digitada_match pro
    raciocínio completo (vírgula/ponto-e-vírgula vira chip na hora)."""
    if not search_value or search_value[-1] not in (',', ';'):
        return no_update, no_update, no_update
    nome = search_value[:-1].strip()
    if not nome:
        return no_update, no_update, ''
    slug = _slugificar_tag_98(nome)
    valor_atual = list(valor_atual or [])
    opcoes_atuais = list(opcoes_atuais or [])
    if slug not in valor_atual:
        valor_atual.append(slug)
    if not any(o['value'] == slug for o in opcoes_atuais):
        opcoes_atuais.append({'label': nome, 'value': slug})
    return valor_atual, opcoes_atuais, ''


@app.callback(
    Output({'type': 'ea98-preview-imagem', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea98-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Input({'type': 'ea98-upload-imagem', 'index': dash.MATCH}, 'contents'),
    Input('store-diario98-imagem-colada', 'data'),
    State('store-diario98-posicao-ativa-print', 'data'),
    State({'type': 'ea98-upload-imagem', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def preview_e_anexar_imagem_em_andamento_98(upload_contents, imagem_colada, posicao_ativa, id_componente):
    """Ver diario_app_classico.py::preview_e_anexar_imagem_em_andamento pro
    raciocínio completo (mostra a preview E anexa à galeria na hora, sem
    clique em "Anexar" nenhum)."""
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    if origem == 'store-diario98-imagem-colada':
        if posicao_ativa != id_componente['index']:
            return no_update, no_update  # Ctrl+V é global — só reage no card armado
        data_url = imagem_colada
    else:
        data_url = upload_contents
    if not data_url:
        return no_update, no_update

    preview = html.Img(src=data_url, style={'maxWidth': '180px', 'border': f"1px solid {C['shadow_dk']}"})
    try:
        imagem_bytes = base64.b64decode(data_url.split(',', 1)[1])
    except Exception:
        return preview, '⚠ Imagem inválida — não foi possível anexar.'
    ok = dt.atualizar_rascunho(_ticket_alvo_98(id_componente['index']), imagem_bytes=imagem_bytes)
    return preview, ('✔ Print anexado automaticamente.' if ok else '⚠ Ocupado — tente colar/enviar de novo.')


@app.callback(
    Output({'type': 'ea98-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Output('em-andamento98-conteudo', 'children', allow_duplicate=True),
    Output('store-em-andamento98-ids-conhecidos', 'data', allow_duplicate=True),
    Input({'type': 'ea98-btn-finalizar', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'ea98-nota', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-estrategia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-sala', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-alvo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-stop', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-nivel-sr', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-resumo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-emo-confianca', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-emo-humor', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-emo-energia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-emo-atencao', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-emo-presenca', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-tags-emocionais', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-sentimento-mercado', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-tags-erro', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-imagem-principal', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea98-upload-imagem', 'index': dash.MATCH}, 'contents'),
    State('store-diario98-imagem-colada', 'data'),
    State('store-diario98-posicao-ativa-print', 'data'),
    State({'type': 'ea98-preco-saida-correcao', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea98-datahora-saida-correcao', 'index': dash.MATCH}, 'value'),
    State('store-posicao98-ativo-subaba', 'data'),
    State({'type': 'ea98-btn-finalizar', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def finalizar_em_andamento_98(n_clicks, nota, estrategia, sala, alvo, stop, nivel_sr, resumo,
                               emo_confianca, emo_humor, emo_energia, emo_atencao, emo_presenca,
                               tags_emocionais, sentimento_mercado, tags_erro, imagem_principal,
                               upload_contents, imagem_colada, posicao_ativa,
                               preco_saida_correcao, datahora_saida_correcao, ativo_selecionado, id_componente):
    # Feedback 2026-07-17: "não há necessidade de tantos botões... apenas
    # um botão" — ver diario_app_classico.py::finalizar_em_andamento pro
    # raciocínio completo. Este único clique auto-salva nota/classificação/
    # print (mesmo `atualizar_rascunho` que "Salvar"/"Anexar à galeria"
    # chamavam) ANTES de finalizar, então nada se perde por não ter
    # clicado nesses botões (que agora nem existem mais nesta seção).
    #
    # Feedback 2026-07-17 (2ª rodada): "se eu já preenchi... não faz
    # sentido aparecer pendência/confirmação no histórico" — o card
    # "pronto pra finalizar" voltou pra aba Posição, então este callback
    # re-renderiza `em-andamento98-conteudo` na hora quando finaliza com
    # sucesso (2º Output, allow_duplicate=True — também atualiza o 3º
    # Output, `store-em-andamento98-ids-conhecidos`, pro próximo tick do
    # interval não achar que "nada mudou"), sem esperar o interval de 6s.
    id_pos = id_componente['index']
    par = _parse_index_par_98(id_pos)
    ticket_alvo = _ticket_alvo_98(id_pos)

    alvo, stop, nivel_sr = _num(alvo), _num(stop), _num(nivel_sr)
    campos_rascunho = {}
    if estrategia is not None:
        campos_rascunho['estrategia_tags'] = estrategia
    if sala is not None:
        campos_rascunho['sala_analise'] = sala
    if alvo is not None:
        campos_rascunho['alvo_pontos'] = alvo
    if stop is not None:
        campos_rascunho['stop_pontos'] = stop
    if nivel_sr is not None:
        campos_rascunho['nivel_sr_referencia'] = nivel_sr
    if resumo is not None:
        campos_rascunho['resumo_operacional'] = resumo
    if emo_confianca is not None:
        campos_rascunho['emo_confianca'] = emo_confianca
    if emo_humor is not None:
        campos_rascunho['emo_humor'] = emo_humor
    if emo_energia is not None:
        campos_rascunho['emo_energia'] = emo_energia
    if emo_atencao is not None:
        campos_rascunho['emo_atencao'] = emo_atencao
    if emo_presenca is not None:
        campos_rascunho['emo_presenca'] = emo_presenca
    if tags_emocionais is not None:
        campos_rascunho['tags_emocionais'] = tags_emocionais
    if sentimento_mercado is not None:
        campos_rascunho['sentimento_mercado'] = sentimento_mercado

    data_url = upload_contents or (imagem_colada if posicao_ativa == id_pos else None)
    imagem_bytes = None
    if data_url:
        try:
            imagem_bytes = base64.b64decode(data_url.split(',', 1)[1])
        except Exception:
            imagem_bytes = None

    if nota or campos_rascunho or imagem_bytes is not None:
        if not dt.atualizar_rascunho(ticket_alvo, nota_texto=nota or None,
                                      campos=campos_rascunho or None, imagem_bytes=imagem_bytes):
            return '⚠ Ocupado — tente de novo (nada foi perdido).', no_update, no_update

    # Sem seleção manual de "Imagem principal", a última imagem da galeria
    # vira a capa automaticamente — cobre tanto o print colado NESTE clique
    # quanto um já anexado antes, enquanto a posição ainda estava aberta
    # (achado real 2026-07-20, ver diario_app_classico.py::
    # finalizar_em_andamento pro raciocínio completo).
    if imagem_principal is None:
        df_atual = dt.listar_posicoes_abertas()
        linha_atual = df_atual[df_atual['id_posicao'] == ticket_alvo]
        if not linha_atual.empty:
            imagens_atuais = _lista_segura_98(linha_atual.iloc[0].get('imagens_em_andamento'))
            if imagens_atuais:
                imagem_principal = imagens_atuais[-1]

    # Só entra no dict quem tem valor de verdade — mesma razão de
    # diario_app_classico.py::finalizar_em_andamento (não sobrescrever o
    # default `*_preliminar` com vazio se o usuário não mexeu no campo).
    # `tipo_saida` NÃO entra mais aqui — `finalizar_posicao` já deriva
    # sozinho a partir do MT5 (feedback diário.txt, 2026-07-11).
    campos_classificacao = {'conta': 'real'}
    if estrategia is not None:
        campos_classificacao['estrategia_tags'] = estrategia
    if sala is not None:
        campos_classificacao['sala_analise'] = sala
    if tags_erro is not None:
        campos_classificacao['tags_setup_erro'] = tags_erro

    if par:
        # Par de hedge (ver diario_app_classico.py::finalizar_em_andamento
        # pro raciocínio completo) — saída já é o preço REAL da perna
        # oposta, não há o que corrigir manualmente aqui.
        ticket_a, ticket_b = par
        id_trade = dt.finalizar_par_hedge(ticket_a, ticket_b, campos_classificacao, imagem_principal=imagem_principal)
    else:
        # Grava a correção (se houver) ANTES de finalizar — `corrigir_saida_rascunho`
        # recalcula pnl_pontos_final/pnl_reais_final e marca saida_estimada=False,
        # pra `finalizar_posicao` (que lê preco_saida direto do rascunho) nunca
        # gravar no Diário o valor fabricado do fallback (ver diario_trade.py).
        preco_corrigido = _num(preco_saida_correcao)
        if preco_corrigido is not None:
            datahora_corrigida = pd.to_datetime(datahora_saida_correcao, errors='coerce') if datahora_saida_correcao else None
            if datahora_corrigida is None or pd.isna(datahora_corrigida):
                return '⚠ Data/Hora de Saída inválida — use o formato AAAA-MM-DD HH:MM.', no_update, no_update
            if not dt.corrigir_saida_rascunho(id_pos, preco_corrigido, datahora_corrigida):
                return '⚠ Ocupado — tente de novo.', no_update, no_update
        id_trade = dt.finalizar_posicao(id_pos, campos_classificacao, imagem_principal=imagem_principal)

    if id_trade is None:
        return '⚠ Não foi possível finalizar agora — tente novamente.', no_update, no_update

    posicoes_atuais = dt.listar_posicoes_abertas()
    ativo_atual = ativo_selecionado or 'WIN'
    _, por_ativo_abertas, por_ativo_prontas = _preparar_registros_posicao_98(posicoes_atuais)
    ids_agora = _ids_rastreados_98(_registros_por_ativo_98(por_ativo_prontas, ativo_atual),
                                    _registros_por_ativo_98(por_ativo_abertas, ativo_atual))
    return (f'✔ Finalizado como operação #{id_trade} no Diário.',
            _renderizar_posicao_completa_98(posicoes_atuais, dt.listar_ordens_pendentes(), ativo_atual),
            ids_agora)


if __name__ == '__main__':
    gp.iniciar_monitor_processo_pai(PORTA_PAI)
    print("=" * 60)
    print("  Diário de Trade — processo SQ'98 (porta 8052)")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=PORTA_PROPRIA, threaded=True)
