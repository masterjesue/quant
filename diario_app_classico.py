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
from datetime import date, datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import json
import logging

# dash-mantine-components 2.x exige React 18 fixado ANTES de `dash.Dash(...)`
# ser instanciado (item F, redesenho TradeZella — "tipo de biblioteca", não só
# cor/fonte) — ver `spec_redesenho_tradezella.md` Fase 2.6.
from dash import _dash_renderer
_dash_renderer._set_react_version('18.2.0')

import dash
from dash import ALL, dcc, html, Input, Output, State, callback_context, no_update
import dash_mantine_components as dmc
import dash_ag_grid as dag
import flask
import numpy as np
import pandas as pd
import plotly.graph_objects as go

import config_backtest as cfg
import persistencia
import diario_trade as dt
import tracados
import gerenciador_processos as gp
import ai_analyst
from metricas import extrair_metricas, montar_snapshot_diario, analisar_tracados

PORTA_PROPRIA = 8053
PORTA_PAI = 8050  # dashboard.py

# Logger próprio (não `logging.basicConfig`, que captura junto o log de request
# do Werkzeug e afoga o que interessa) pra ações do Diário que falham sem ter
# onde reclamar na tela — ex: uma finalização recusada pela trava de
# idempotência. `print()` não serve: acento/emoji em stdout no console do
# Windows levanta UnicodeEncodeError e mata a thread em silêncio (ver CLAUDE.md).
_log_diario = logging.getLogger('diario_classico')
if not _log_diario.handlers:
    _handler_diario = logging.FileHandler(
        Path(__file__).parent / 'log_diario.log', encoding='utf-8')
    _handler_diario.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    _log_diario.addHandler(_handler_diario)
    _log_diario.setLevel(logging.INFO)
    _log_diario.propagate = False

# =============================================================================
# PALETA E TOKENS (mesmos tokens de dashboard.py — duplicados aqui pela mesma
# razão que assets/diario_paste.js duplica assets_98/diario_paste.js: os dois
# front-ends nunca compartilham camada visual, só lógica de dados)
# =============================================================================

# 2026-07-17: valores viraram `var(--x)` (assets/style.css, compartilhado
# com dashboard.py — mesmo modo claro/escuro do SQ Padrão, propagado pro
# Diário via postMessage, ver assets/tema.js). PALETA_GAUGE abaixo é a
# exceção pra Plotly (_grafico_evolucao_saldo/_radar_roda_vida), que não
# entende `var(...)`.
C = {
    'bg':        'var(--bg)',
    'panel':     'var(--panel)',
    'border':    'var(--border)',
    'gold':      'var(--gold)',
    'green':     'var(--green)',
    'red':       'var(--red)',
    'text':      'var(--text)',
    'text2':     'var(--text2)',
    'blue':      'var(--blue)',
    'yellow':    'var(--yellow)',
}

PALETA_GAUGE = {
    'dark': {
        'bg': '#0A0A0F', 'panel': '#12121A', 'border': '#1C1C28',
        'gold': '#C8A96E', 'green': '#2ECC8A', 'red': '#E05C5C',
        'text': '#E8E8F0', 'text2': '#8888AA', 'blue': '#4A90D9', 'yellow': '#F0C040',
    },
    'light': {
        'bg': '#E4E7F0', 'panel': '#FFFFFF', 'border': '#C7CCDC',
        'gold': '#A87C2E', 'green': '#128A55', 'red': '#B33A3A',
        'text': '#14141C', 'text2': '#5B5B78', 'blue': '#3573B5', 'yellow': '#8A6508',
    },
}

FONTS = {
    'display': 'Space Grotesk, system-ui, sans-serif',
    'mono':    'JetBrains Mono, monospace',
    'body':    'Inter, system-ui, sans-serif',
}

# Tokens SÓ da aba Estatísticas (redesenho TradeZella, item F,
# `spec_redesenho_tradezella.md`) — etapa 1 de um rollout progressivo
# (Estatísticas -> Diário inteiro -> app inteiro, decisão do Jesué
# 2026-07-30). NÃO mexe em C/PALETA_GAUGE (usados pelo resto do app, ainda
# no visual "cockpit" antigo, intacto de propósito até a próxima etapa).
#
# Achado da análise das 3 referências reais do TradeZella (`upgrade
# diario.docx`): tema claro usa célula de calendário PASTEL + texto escuro;
# tema escuro usa célula SATURADA + texto claro (já bem parecido com o que
# o Diário tinha) — por isso `TZ_PASTEL_CALENDARIO` só tem entrada 'light'.
TZ_RAIO_CARD = '14px'
TZ_RAIO_CELULA = '8px'
TZ_SOMBRA_CARD = '0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04)'
TZ_PADDING_CARD = '18px 20px'
TZ_GAP_GRID = '16px'

TZ_PASTEL_CALENDARIO = {
    'light': {'green_bg': '#DCFCE7', 'red_bg': '#FEE2E2', 'neutro_bg': '#F1F1F6', 'texto': '#14141C'},
}


def _tz_estilo_card(extra: dict | None = None) -> dict:
    """Estilo de card do redesenho TradeZella — sombra suave em vez de
    borda grossa, cantos bem arredondados, padding generoso. Reaproveita
    `C['panel']` (já existente) como fundo, só troca a moldura."""
    base = {
        'background': C['panel'], 'borderRadius': TZ_RAIO_CARD,
        'boxShadow': TZ_SOMBRA_CARD, 'padding': TZ_PADDING_CARD,
    }
    if extra:
        base.update(extra)
    return base


def _tz_badge_icone(emoji: str, cor_fundo: str, tamanho: int = 32) -> html.Span:
    """Emoji dentro de um badge circular pequeno, em vez de solto no meio
    do texto — mesmo espírito dos ícones de linha fina do TradeZella (ⓘ,
    engrenagem): reduz o peso visual do emoji sem abrir mão da
    expressividade."""
    return html.Span(emoji, style={
        'display': 'inline-flex', 'alignItems': 'center', 'justifyContent': 'center',
        'width': f'{tamanho}px', 'height': f'{tamanho}px', 'borderRadius': '50%',
        'background': cor_fundo, 'fontSize': f'{tamanho * 0.5:.0f}px', 'flexShrink': '0',
    })

_ABAS_DIARIO_SUB = [
    ('estatisticas', 'Estatísticas'),
    ('posicao', 'Posição'),
    ('historico', 'Histórico'),
    ('estrategias', 'Estratégias'),
    ('novo', 'Novo Trade'),
    ('padroes', 'Padrões'),
    ('bem_estar', 'Bem-Estar'),
]
_ABA_DIARIO_INICIAL = 'estatisticas'


def _opcoes_diario(valores: list[str]) -> list[dict]:
    """Converte vocabulário canônico (snake_case) em opções de dropdown legíveis."""
    return [{'label': v.replace('_', ' ').title(), 'value': v} for v in valores]


def _opcoes_estrategias() -> list[dict]:
    """Opções de estratégia lidas do cadastro editável (aba Estratégias),
    não mais da lista fixa — refletem criação/desativação na hora."""
    return [{'label': linha['nome'], 'value': linha['slug']} for _, linha in dt.listar_estrategias().iterrows()]


def _opcoes_com_valores_atuais(opcoes: list[dict], valores_atuais: list) -> list[dict]:
    """Acrescenta às `opcoes` qualquer valor JÁ GRAVADO no trade que não
    esteja mais no cadastro, marcado como "(fora do cadastro)".

    Sem isto o campo vira uma armadilha destrutiva: um `dcc.Dropdown` cujo
    `value` não consta das `options` não renderiza aquele chip, então o
    usuário abre o pop-up, vê o campo aparentemente vazio, salva — e o
    `State` devolvido pelo Dash apaga a classificação que existia. Não é
    hipótese: 140 dos 225 trades do Diário carregam slugs herdados da
    importação do XLSM (`suporte_resistencia`, `inversao_polaridade`,
    `pullback_mme21`…) que nunca existiram no cadastro de estratégias, mais
    a tag emocional `ganância`. Enquanto a reclassificação não termina, o
    valor antigo precisa continuar visível E preservado."""
    conhecidos = {o['value'] for o in opcoes}
    extras = [{'label': f"{str(v).replace('_', ' ').title()} (fora do cadastro)", 'value': v}
              for v in dict.fromkeys(valores_atuais) if v not in conhecidos]
    return opcoes + extras


def _opcoes_tags_setup_erro() -> list[dict]:
    return [{'label': linha['nome'], 'value': linha['slug']} for _, linha in dt.listar_tags_setup_erro().iterrows()]


def _opcoes_tags_emocionais() -> list[dict]:
    return [{'label': linha['nome'], 'value': linha['slug']} for _, linha in dt.listar_tags_emocionais().iterrows()]


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


_MARCAS_SENTIMENTO_MERCADO = {0: 'Medo Extremo', 25: 'Medo', 50: 'Neutro', 75: 'Ganância', 100: 'Ganância Extrema'}


def _num_estrela_ou_padrao(valor, padrao: int = 3) -> int:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return padrao
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def _estrela_rating(id_componente, valor: int = 3) -> html.Div:
    """Nota 1-5 clicável (5 estrelas de verdade) — ver diario_app_98.py::
    _estrela_rating_98 pro mesmo raciocínio. Substitui o dcc.Slider com
    marks de ★ (feedback 2026-07-11: "muito feio, coloque apenas as
    estrelas... como tem normalmente nos apps"). Mantém a MESMA interface
    externa da função antiga — um componente com id=id_componente expondo
    `.value` — via um dcc.Input(type='hidden') por baixo, então nenhum
    callback que já lê/escreve nesse id (Novo Trade, Bem-Estar, edição
    rápida da Posição) precisa mudar. `id_componente` aceita string (campo
    único) ou dict pattern-matched {'type','index'} (ver ea-emo-* na
    Posição) — o clique de cada um é ligado em
    `_registrar_clique_estrela`/`_registrar_clique_estrela_match`."""
    valor = valor or 0
    if isinstance(id_componente, str):
        ids_estrelas = [f'{id_componente}-estrela-{i}' for i in range(1, 6)]
    else:
        ids_estrelas = [{**id_componente, 'type': f"{id_componente['type']}-estrela", 'n': i}
                         for i in range(1, 6)]
    estrelas = html.Div([
        html.Span('', id=ids_estrelas[i - 1], n_clicks=0,
                  className='diario-estrela' + (' ativa' if i <= valor else ''))
        for i in range(1, 6)
    ], style={'display': 'flex', 'gap': '2px'})
    return html.Div([estrelas, dcc.Input(id=id_componente, type='hidden', value=valor)])


def _registrar_clique_estrela(app, id_campo: str):
    """Registra o par de clientside_callbacks de um campo de estrela
    SINGLETON (id string única — Novo Trade/Bem-Estar). Ver
    `_registrar_clique_estrela_match` pro equivalente pattern-matched
    (Posição, N posições abertas dinamicamente)."""
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
            return [1, 2, 3, 4, 5].map(i => 'diario-estrela' + (i <= v ? ' ativa' : ''));
        }
        """,
        [Output(f'{id_campo}-estrela-{i}', 'className') for i in range(1, 6)],
        Input(id_campo, 'value'),
    )


def _registrar_clique_estrela_match(app, tipo_campo: str):
    """Mesma lógica de `_registrar_clique_estrela`, mas via MATCH+ALL pra
    cobrir qualquer posição aberta na aba Posição (ea-emo-confianca etc —
    o número/id das posições muda em tempo real, não dá pra pré-registrar
    um callback por instância)."""
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
            return [1, 2, 3, 4, 5].map(i => 'diario-estrela' + (i <= v ? ' ativa' : ''));
        }
        """,
        [Output({'type': f'{tipo_campo}-estrela', 'index': dash.MATCH, 'n': i}, 'className') for i in range(1, 6)],
        Input({'type': tipo_campo, 'index': dash.MATCH}, 'value'),
    )


def _campo_tag_widget(id_store: dict, opcoes: list[dict], valores_iniciais) -> html.Div:
    """Campo de tag "de verdade" (feedback 2026-07-20 — print de
    referência: caixa com chips grandes/arredondados, X pra remover,
    Enter OU vírgula/ponto-e-vírgula confirmam, rodapé com contador +
    "Remover todas"). `dcc.Dropdown` não faz Enter-pra-confirmar nem esse
    layout nativamente — mesmo raciocínio de `_estrela_rating` (widget
    HTML/CSS/JS de verdade, ver `assets/tag_input.js`, + um `dcc.Store`
    escondido carregando a lista atual pros callbacks Python lerem via
    `.data`, já que não existe prop nativo do Dash pra isso).

    Só usado nos campos que já são vocabulário aberto/crescente (Print
    Emocional e Tags Setup/Erro, dentro dos cards de Posição) — Estratégia
    e o Novo Trade continuam com o `dcc.Dropdown` de sempre (escopo
    2026-07-20, decisão explícita: não vale a pena reescrever CADA
    dropdown do Diário pra este widget agora).

    `id_store` é sempre um id pattern-matching (dict, {'type':...,
    'index': id_pos}) — usado tanto no `dcc.Store` quanto como
    `data-armar-index` (o card volta a ficar "armado" pro próximo Ctrl+V
    ao clicar/focar dentro do widget, mesmo esses elementos não tendo um
    id Dash próprio — ver `assets/diario_paste.js`)."""
    rotulo_por_valor = {o['value']: o['label'] for o in opcoes}
    selecionados = [
        {'value': v, 'label': rotulo_por_valor.get(v, str(v))}
        for v in (valores_iniciais or [])
    ]
    chips = [
        html.Span([
            html.Span(item['label']),
            html.Button('×', type='button', className='tag-chip-remover', **{'aria-label': 'Remover tag'}),
        ], className='tag-chip', **{'data-valor': item['value']})
        for item in selecionados
    ]
    return html.Div([
        html.Div('Digite e pressione Enter ou vírgula/ponto-e-vírgula após cada tag',
                 className='tag-widget-dica'),
        html.Div([
            html.Div(chips, className='tag-widget-chips'),
            # BUG REAL 2026-07-27 (digitar a tag e apertar Enter não fazia
            # nada): no Dash 4.x o `dcc.Input` NÃO renderiza mais um `<input>`
            # nu — ele envolve o campo num wrapper e a `className` daqui vai
            # parar na DIV de fora:
            #   <div class="dash-input-container ... tag-widget-input">
            #     <input class="dash-input-element" ...>
            #   </div>
            # O `assets/tag_input.js` subia até `.tag-widget-input` e lia
            # `.value` — que numa div é `undefined`. O texto digitado virava
            # string vazia e nenhuma tag era criada.
            #
            # Não dá pra trocar por um `<input>` puro: `html.Input` não existe
            # mais no Dash 4.3. A correção mora no JS (`campoDeTexto`), que
            # agora resolve o `<input>` real e aceita as duas formas — ver o
            # comentário lá. O CSS também estiliza os dois níveis
            # (`.tag-widget-input` e `.tag-widget-input .dash-input-element`).
            dcc.Input(type='text', className='tag-widget-input', autoComplete='off',
                      placeholder='Nova tag...'),
        ], className='tag-widget-caixa'),
        html.Div(className='tag-widget-sugestoes'),
        html.Div([
            html.Span(f"{len(selecionados)} tag(s)", className='tag-widget-contador'),
            html.Button('Remover todas', type='button', className='tag-widget-limpar'),
        ], className='tag-widget-rodape'),
        dcc.Store(id=id_store, data=[item['value'] for item in selecionados]),
    ], className='tag-widget', **{
        'data-opcoes': json.dumps(opcoes, ensure_ascii=False),
        'data-store-id': json.dumps(id_store, ensure_ascii=False),
        'data-armar-index': str(id_store['index']),
    })


def _bloco_emocional(id_confianca, id_humor, id_energia, id_atencao, id_presenca,
                      id_tags, id_sentimento, id_label_sentimento, valores: dict | None = None,
                      widget_tags_customizado: bool = False) -> html.Div:
    """Autoavaliação emocional — ver diario_app_98.py::_bloco_emocional_98
    pro mesmo raciocínio completo (fundação do módulo emocional/
    comportamental, feedback diário.txt 2026-07-11).

    `widget_tags_customizado`: quando True (só os cards de Posição, ver
    `_bloco_posicao_form`), o campo "Print Emocional" usa `_campo_tag_widget`
    em vez de `dcc.Dropdown` (feedback 2026-07-20). O Novo Trade continua
    com o dropdown de sempre (`widget_tags_customizado=False`, default)."""
    campo = _campo_diario
    valores = valores or {}
    sentimento_valor = valores.get('sentimento')
    sentimento_valor = int(sentimento_valor) if sentimento_valor is not None and not (
        isinstance(sentimento_valor, float) and pd.isna(sentimento_valor)) else 50
    if widget_tags_customizado:
        campo_tags = campo('Print Emocional (tags)',
                            _campo_tag_widget(id_tags, _opcoes_tags_emocionais(), _lista_segura(valores.get('tags'))))
    else:
        campo_tags = campo('Print Emocional (tags)', dcc.Dropdown(id=id_tags, multi=True,
                                                        options=_opcoes_tags_emocionais(),
                                                        value=_lista_segura(valores.get('tags')) or None,
                                                        className='classico-dropdown classico-dropdown-tags'))
    return _bloco_diario('Autoavaliação Emocional', html.Div([
        html.Div([
            campo('Confiança', _estrela_rating(id_confianca, _num_estrela_ou_padrao(valores.get('confianca')))),
            campo('Humor', _estrela_rating(id_humor, _num_estrela_ou_padrao(valores.get('humor')))),
            campo('Energia', _estrela_rating(id_energia, _num_estrela_ou_padrao(valores.get('energia')))),
            campo('Atenção', _estrela_rating(id_atencao, _num_estrela_ou_padrao(valores.get('atencao')))),
            campo('Presença', _estrela_rating(id_presenca, _num_estrela_ou_padrao(valores.get('presenca')))),
        ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(160px, 1fr))', 'gap': '6px 16px'}),
        campo_tags,
        html.Div([
            html.Div('Sentimento de Mercado', style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '4px'}),
            dcc.Slider(id=id_sentimento, min=0, max=100, step=1, value=sentimento_valor, marks=_MARCAS_SENTIMENTO_MERCADO),
            html.Div(id=id_label_sentimento, style={'fontSize': '12px', 'marginTop': '4px'}),
        ], style={'marginTop': '10px'}),
    ]))


def _estrelas_texto(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return '—'
    try:
        n = max(0, min(5, int(valor)))
    except (TypeError, ValueError):
        return '—'
    return '★' * n + '☆' * (5 - n)


def _texto_sentimento(valor) -> str:
    resultado = dt.classificar_sentimento_mercado(valor)
    if resultado is None:
        return '—'
    nome, _ = resultado
    return f"{nome} ({int(valor)})"


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
        'boxShadow': 'var(--shadow-card)',
        'borderRadius': '10px', 'padding': '14px 16px', 'marginBottom': '16px',
    })


def _bloco_tradezella(titulo, children) -> dmc.Card:
    """Equivalente a `_bloco_diario`, só que `dmc.Card` real (item F, Fase
    2.6 — não mais `html.Div` com sombra pintada à mão) usado em TODA a
    aba Estatísticas (rollout progressivo: só aqui por enquanto, decisão
    do Jesué). NÃO substitui `_bloco_diario` (as outras abas do Diário
    continuam com ele, sem mudança, até a próxima etapa do rollout).

    `w='100%'` (2026-07-31, feedback do Jesué: "não gosto desses espaços
    vazios") — `dmc.Card` não estica sozinho pra preencher o container
    (diferente de um `html.Div` de bloco comum), deixando espaço morto à
    direita quando usado sozinho numa linha. Quando usado EM PAR (ex:
    "Melhor por Horário"/"Melhor por Direção"), o par inteiro precisa estar
    dentro de um `dmc.SimpleGrid` (não `display:flex` cru) pra cada card
    ocupar sua célula por igual — ver `_bloco_analise_estrategia`."""
    return dmc.Card([
        dmc.Text(titulo, size='sm', fw=600, mb='md'),
        children,
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _estilo_input_diario(extra=None) -> dict:
    estilo = {
        'background': C['bg'], 'border': f'1px solid {C["border"]}',
        'borderRadius': '6px', 'padding': '6px 10px', 'color': C['text'],
        'fontFamily': FONTS['body'], 'fontSize': '13px', 'width': '100%',
        'boxSizing': 'border-box', 'height': '32px',
    }
    if extra:
        estilo.update(extra)
    return estilo


def _num(valor):
    """Converte o `value` de um <input type=text inputMode=decimal> (usado
    no lugar de type=number pra eliminar as setinhas +/- nativas — ver
    CHANGELOG 2026-07-11) pra float; aceita vírgula decimal; None se vazio/
    inválido. Idempotente pra int/float (Outputs programáticos continuam
    passando o número direto, sem passar por aqui de novo)."""
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


def _placeholder_aba(nome: str) -> html.Div:
    return html.Div(f'{nome} — em construção.', style={
        'padding': '40px', 'textAlign': 'center', 'color': C['text2'],
        'fontFamily': FONTS['display'], 'fontSize': '13px',
    })


def _construir_diario_novo_trade() -> html.Div:
    campo = _campo_diario
    estilo_input = _estilo_input_diario
    # Grid ao invés de flex+largura fixa por campo (era o motivo do espaço
    # vazio à direita em telas largas, ver feedback diário.txt) — cada campo
    # ocupa uma coluna de no mínimo 150px e estica pra preencher a largura
    # disponível. `_estilo_input_diario` já usa width:100%, então os campos
    # de texto/número acompanham a coluna do grid sem precisar de largura
    # fixa por campo.
    grade = lambda *filhos: html.Div(list(filhos), style={
        'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(150px, 1fr))',
        'gap': '14px 16px', 'alignItems': 'end',
    })

    return html.Div([
        _bloco_diario('Dados da Operação', html.Div([
            grade(
                campo('Data', dcc.Input(id='diario-data', type='date', style=estilo_input())),
                campo('Ativo', dcc.Dropdown(id='diario-ativo', options=_opcoes_diario(cfg.ATIVOS_DIARIO),
                                             clearable=False, className='classico-dropdown')),
                campo('Janela', dcc.Dropdown(id='diario-janela',
                                              options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                                              className='classico-dropdown')),
                campo('Estratégia', dcc.Dropdown(id='diario-estrategia', multi=True,
                                                  options=_opcoes_estrategias(),
                                                  className='classico-dropdown classico-dropdown-tags')),
                campo('Sala/Análise', dcc.Dropdown(id='diario-sala',
                                                     options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
                                                     className='classico-dropdown')),
                campo('Tipo', dcc.Dropdown(id='diario-tipo-operacao', options=_opcoes_diario(dt.TIPO_OPERACAO),
                                            clearable=False, className='classico-dropdown')),
                campo('R:R', dcc.Dropdown(id='diario-rr-preset',
                                           options=[{'label': l['nome'], 'value': l['id']}
                                                    for _, l in dt.listar_rr_presets().iterrows()],
                                           placeholder='nenhuma', className='classico-dropdown')),
                campo('Alvo (pts)', dcc.Input(id='diario-alvo', type='text', inputMode='decimal',
                                               style=estilo_input())),
                campo('Stop (pts)', dcc.Input(id='diario-stop', type='text', inputMode='decimal',
                                               style=estilo_input())),
                campo('Lote', dcc.Input(id='diario-lote', type='number', value=1, style=estilo_input())),
                campo('Preço Entrada', dcc.Input(id='diario-preco-entrada', type='text', inputMode='decimal',
                                                  style=estilo_input())),
                campo('Preço Saída', dcc.Input(id='diario-preco-saida', type='text', inputMode='decimal',
                                                style=estilo_input())),
                campo('Tipo Saída', dcc.Dropdown(id='diario-tipo-saida', options=_opcoes_diario(dt.TIPO_SAIDA),
                                                  className='classico-dropdown')),
                campo('Resultado (pts)', dcc.Input(id='diario-resultado', type='text', inputMode='decimal',
                                                    style=estilo_input())),
                campo('Saldo (R$)', dcc.Input(id='diario-saldo', type='text', inputMode='decimal',
                                               style=estilo_input())),
            ),
        ])),

        _bloco_diario('Nível de S/R referenciado (opcional)', html.Div([
            grade(
                campo('Preço', dcc.Input(id='diario-nivel-sr', type='text', inputMode='decimal',
                                          style=estilo_input())),
                campo('Tipo', dcc.Input(id='diario-nivel-sr-tipo', type='text', placeholder='ex: suporte',
                                         style=estilo_input())),
            ),
        ])),

        _bloco_diario('Anotações', html.Div([
            campo('Resumo Operacional', dcc.Textarea(id='diario-resumo', rows=3, style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            campo('Obs (autocoaching)', dcc.Textarea(id='diario-obs', rows=2, style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            campo('Tags de Setup/Erro', dcc.Dropdown(id='diario-tags-setup-erro', multi=True,
                                                       options=_opcoes_tags_setup_erro(),
                                                       className='classico-dropdown classico-dropdown-tags')),
        ])),

        _bloco_emocional(
            id_confianca='diario-emo-confianca', id_humor='diario-emo-humor',
            id_energia='diario-emo-energia', id_atencao='diario-emo-atencao',
            id_presenca='diario-emo-presenca', id_tags='diario-tags-emocionais',
            id_sentimento='diario-sentimento-mercado', id_label_sentimento='diario-sentimento-label',
        ),

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


def _parse_index_par(index):
    """Se `index` (de um `dash.MATCH`/`id_componente['index']`) for um par
    de hedge — string `"ticket_a-ticket_b"`, ver
    `dt.montar_posicao_sintetica_par` — devolve `(ticket_a, ticket_b)`;
    senão `None` (é um ticket avulso normal, int ou string numérica)."""
    texto = str(index)
    partes = texto.split('-')
    if len(partes) != 2:
        return None
    try:
        return int(partes[0]), int(partes[1])
    except ValueError:
        return None


def _ticket_alvo(index):
    """Ticket MT5 que deve receber ações de rascunho (nota/imagem) pra este
    `index` — pra um par de hedge, escreve sempre na perna de ENTRADA
    (`ticket_a`), mesma decisão de `finalizar_par_hedge`/
    `montar_posicao_sintetica_par`; pra um ticket avulso, é o próprio."""
    par = _parse_index_par(index)
    return par[0] if par else index


def _estilo_thumb_imagem_principal(selecionada: bool) -> dict:
    return {
        'width': '120px', 'height': '80px', 'objectFit': 'cover', 'borderRadius': '6px',
        'cursor': 'pointer', 'display': 'block',
        'border': f"3px solid {C['gold'] if selecionada else C['border']}",
        'opacity': '1' if selecionada else '0.7',
    }


def _thumbs_galeria_live(id_pos, imagens: list) -> list:
    """Miniaturas da galeria do bloco só-leitura do card (as pequenas, do
    topo). Lista vazia quando não há print — o container em volta existe
    sempre, ver `_bloco_posicao_live`."""
    return [
        _thumb_com_remover(id_pos, nome, html.Img(
            src=f'/diario-imagens/{nome}', style={
                'width': '70px', 'height': '46px', 'objectFit': 'cover', 'borderRadius': '4px',
                'border': f'1px solid {C["border"]}', 'display': 'block',
            }))
        for nome in imagens
    ]


def _thumb_com_remover(id_pos, nome: str, miniatura) -> html.Div:
    """Envolve uma miniatura de print num container com um "×" que só aparece
    ao passar o mouse (pedido 2026-07-27: "a thumb do print deve ter um X que
    aparece ao passar o mouse, caso eu queira remover um print do trade").

    O botão é irmão da imagem, não filho — assim clicar na miniatura continua
    fazendo o que já fazia (ampliar, ou escolher a capa) e só o × remove. O
    hover é 100% CSS (`.diario-thumb-wrap:hover .diario-thumb-x`, ver
    assets/style.css) pra não depender de callback nenhuma pra aparecer.
    """
    return html.Div([
        miniatura,
        html.Button('×', id={'type': 'ea-imagem-remover', 'index': id_pos, 'nome': nome},
                    n_clicks=0, className='diario-thumb-x', title='Remover este print do trade'),
    ], className='diario-thumb-wrap')


def _campo_imagem_principal(id_pos, imagens: list) -> html.Div:
    """Escolhe qual imagem da galeria vira a capa oficial do trade
    (`imagem_arquivo`) — feedback 2026-07-20: "deve trazer a última como
    padrão e deve mostrar a miniatura pra selecionar (um pouco maior do
    que o normal, já que é difícil ver uma tela de gráfico em miniatura)".
    Substitui o dropdown de texto (só nome de arquivo, sem preview nenhum)
    por miniaturas clicáveis MAIORES que a galeria comum (`_bloco_posicao_
    live`, 70×46px) — mesmo padrão de clique pattern-matched já usado em
    `ampliar_imagem`/`_registrar_clique_estrela_match`, sem precisar de JS
    novo. Default = última imagem anexada (mais recente), sempre — ver
    `escolher_imagem_principal`/`destacar_imagem_principal`.

    Bug real 2026-07-22 ("Finalizar" sem NENHUM efeito pra qualquer posição
    sem print anexado — achado direto no console): o `dcc.Store(id={'type':
    'ea-imagem-principal', ...})` SÓ era renderizado quando `imagens` não
    era vazio (gate no chamador, `_bloco_posicao_form`) — mas
    `finalizar_em_andamento` lê esse Store como `State` incondicionalmente.
    Sem NENHUM print anexado (o caso mais comum), o Store não existe no
    DOM, e o Dash rejeita a callback INTEIRA com `ReferenceError` (mesma
    classe de bug do `em-andamento-conteudo` corrigido antes — qualquer
    Input/State/Output que aponte pra um id condicional quebra a callback
    pra QUALQUER clique, não só quando a condição é falsa). Corrigido:
    o Store agora é SEMPRE renderizado (`data=None` sem imagens); só a
    parte VISUAL (miniaturas clicáveis) continua condicional."""
    # A div externa tem id fixo (pattern-matching) e o CONTEÚDO é reconstruído
    # por `remover_imagem_do_rascunho` quando um print é removido — por isso o
    # miolo mora numa função à parte, chamada dos dois lugares.
    return html.Div(_conteudo_imagem_principal(id_pos, imagens),
                    id={'type': 'ea-campo-imagem-principal', 'index': id_pos})


def _conteudo_imagem_principal(id_pos, imagens: list) -> list:
    """Miolo de `_campo_imagem_principal` — o `dcc.Store` entra SEMPRE (ver o
    bug de 2026-07-22 no docstring de lá); só as miniaturas são condicionais."""
    if not imagens:
        return [dcc.Store(id={'type': 'ea-imagem-principal', 'index': id_pos}, data=None)]
    selecionada = imagens[-1]
    thumbs = [
        _thumb_com_remover(id_pos, nome, html.Img(
            src=f'/diario-imagens/{nome}',
            id={'type': 'ea-imagem-thumb-escolher', 'index': id_pos, 'nome': nome},
            n_clicks=0,
            style=_estilo_thumb_imagem_principal(nome == selecionada),
        ))
        for nome in imagens
    ]
    return [
        html.Div('Imagem principal (clique pra escolher a capa do trade)', style={
            'fontSize': '11px', 'color': C['text2'], 'marginBottom': '4px', 'fontFamily': FONTS['display']}),
        html.Div(thumbs, style={'display': 'flex', 'gap': '8px', 'flexWrap': 'wrap'}),
        dcc.Store(id={'type': 'ea-imagem-principal', 'index': id_pos}, data=selecionada),
    ]


def _bloco_finalizar_em_andamento(pos: dict, imagens: list) -> html.Div:
    campo = _campo_diario
    id_pos = pos['id_posicao']
    saida_estimada = bool(pos.get('saida_estimada'))
    # Tipo de saída não é mais escolhido à mão (feedback diário.txt,
    # 2026-07-11) — só um preview do que `dt.derivar_tipo_saida` vai gravar.
    tipo_saida_previsto = dt.derivar_tipo_saida(
        pos.get('tipo_operacao'), pos.get('preco_entrada'), pos.get('preco_saida'),
        pos.get('sl'), pos.get('tp'),
    )
    # Campos de correção de saída SEMPRE renderizados (ver
    # diario_app_98.py::_bloco_finalizar_em_andamento_98 pro mesmo
    # raciocínio de não depender de um componente condicional que quebraria
    # o agrupamento de dash.MATCH). O aviso só aparece quando a corretora
    # não confirmou o fechamento real (bug confirmado em 2026-07-12).
    aviso_saida = html.Div(
        '⚠ Saída NÃO confirmada pela corretora — o valor abaixo é uma estimativa. '
        'Confira/corrija antes de finalizar.',
        style={'fontSize': '11px', 'color': C['yellow'], 'fontWeight': '700', 'marginBottom': '8px'},
    ) if saida_estimada else None
    campos_correcao_saida = html.Div([
        campo('Preço de Saída (confira/corrija)', dcc.Input(
            id={'type': 'ea-preco-saida-correcao', 'index': id_pos}, type='text', inputMode='decimal',
            value=pos.get('preco_saida'), style=_estilo_input_diario({'width': '120px'}))),
        campo('Data/Hora de Saída (confira/corrija, AAAA-MM-DD HH:MM)', dcc.Input(
            id={'type': 'ea-datahora-saida-correcao', 'index': id_pos}, type='text',
            value=str(pos.get('datetime_saida'))[:16] if pos.get('datetime_saida') else '',
            style=_estilo_input_diario({'width': '180px'}))),
    ], style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap', 'marginBottom': '8px'})
    aviso_e_correcao = ([aviso_saida] if aviso_saida is not None else []) + [campos_correcao_saida]
    return html.Div([
        html.Div('Finalizar operação', style={
            'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'marginTop': '14px', 'marginBottom': '6px',
        }),
        html.Div(f'Tipo de saída (calculado a partir do MT5): {tipo_saida_previsto}',
                 style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '8px'}),
    ] + aviso_e_correcao + [
        campo('Tags Setup/Erro', dcc.Dropdown(id={'type': 'ea-tags-erro', 'index': id_pos}, multi=True,
              options=_opcoes_tags_setup_erro(),
              className='classico-dropdown classico-dropdown-tags', style={'width': '260px'})),
    ] + [_campo_imagem_principal(id_pos, imagens)] + [
        html.Button('Finalizar e gravar no Diário', id={'type': 'ea-btn-finalizar', 'index': id_pos}, n_clicks=0, style={
            'background': C['gold'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
            'padding': '7px 18px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'fontSize': '12px', 'marginTop': '8px',
        }),
        # Resposta do clique AO LADO do botão (2026-07-27). Antes o único
        # destino era `ea-status`, que mora lá em cima junto do campo de Nota —
        # quando "Finalizar" era recusado (disputa de trava), o aviso aparecia
        # fora da área que o usuário estava olhando e o clique parecia "não
        # fazer nada". Erro que o usuário não vê é indistinguível de bug.
        html.Div(id={'type': 'ea-status-finalizar', 'index': id_pos}, style={
            'fontSize': '11px', 'marginTop': '8px', 'minHeight': '15px', 'color': C['text2'],
        }),
    ])


def _bloco_posicao_live(pos: dict) -> html.Div:
    """Parte SÓ-LEITURA do card de posição (preço/P&L/notas já salvas) —
    reconstruída a cada 6s pelo `interval-em-andamento` (ver
    `atualizar_em_andamento`). Deliberadamente SEM nenhum campo editável:
    ver `_bloco_posicao_form` pro raciocínio de por que a classificação
    (estratégia/estrelas/sentimento/resumo/nota) foi separada num Output
    PRÓPRIO, nunca tocado pelo interval — um `dcc.Input`/`dcc.Slider`
    reconstruído com `value=` fresco do disco a cada 6s sobrescreve
    qualquer edição não salva do usuário, isso é o que causava o
    "Neutro piscando" (o valor persistido reaparecendo por cima do que o
    usuário acabou de arrastar/clicar) reportado em 2026-07-11."""
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
    # Id fixo (mesmo sem imagens) — `remover_imagem_do_rascunho` reescreve o
    # conteúdo daqui, e um Output pra id condicional derrubaria a callback.
    galeria = html.Div(_thumbs_galeria_live(pos['id_posicao'], imagens),
                       id={'type': 'ea-galeria-live', 'index': pos['id_posicao']},
                       style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '6px', 'marginTop': '6px'})

    par_hedge = bool(pos.get('par_hedge'))
    preco_ref = pos.get('preco_saida') if pendente else pos.get('preco_atual')
    if pendente:
        label_ref = 'Saída (⚠ estimada)' if pos.get('saida_estimada') else 'Saída'
    else:
        label_ref = 'Atual'
    pnl_pts = pos.get('pnl_pontos_final') if pendente else pos.get('pnl_pontos')
    pnl_reais = pos.get('pnl_reais_final') if pendente else pos.get('pnl_reais')
    # Achado real 2026-07-15: "AGUARDANDO CONFIRMAÇÃO" faz sentido pro caso
    # antigo (saída estimada, ver comentário em sincronizar_posicoes_abertas),
    # mas é confuso/errado pra um par de hedge cuja saída É confirmada
    # (preço real da posição oposta que fechou o par, não uma estimativa).
    # Bug real 2026-07-22: um par de hedge TAMBÉM pode ter saída estimada
    # agora (quando a perna de saída fechou sozinha sem confirmação real
    # do MT5 ainda — ver `detectar_pares_hedge`/`montar_posicao_sintetica_
    # par`) — nesse caso não faz sentido mostrar "✓" confiante.
    # Pedido real 2026-07-22: "deixe a informação 'ABERTA' piscando" — só a
    # posição genuinamente aberta pisca (`diario-status-piscante`, ver
    # assets/style.css), pra chamar atenção de que ela ainda está em
    # risco real de mercado; os estados fechados/pendentes ficam estáticos
    # de propósito (já têm badge de cor própria, piscar ali só distrairia).
    status_classe = None
    if pendente and par_hedge and not pos.get('saida_estimada'):
        status_txt, status_cor = ' ✓ FECHADA (par hedge — pronta pra finalizar)', C['green']
    elif pendente and par_hedge:
        status_txt, status_cor = ' ⚠ FECHADA (par hedge) — AGUARDANDO CONFIRMAÇÃO', C['yellow']
    elif pendente:
        status_txt, status_cor = ' ⚠ FECHADA — AGUARDANDO CONFIRMAÇÃO', C['yellow']
    else:
        status_txt, status_cor = ' ● ABERTA', C['green']
        status_classe = 'diario-status-piscante'
    detalhe = html.Div([
        html.Span((pos.get('tipo_operacao') or '—').upper(), style={
            'color': cor_direcao, 'fontSize': '11px', 'fontWeight': '700'}),
        html.Span(status_txt, className=status_classe, style={
            'marginLeft': '8px', 'fontSize': '10px', 'fontWeight': '700', 'color': status_cor}),
        html.Div(
            f"Entrada {_fmt_num(pos.get('preco_entrada'))} · {label_ref} {_fmt_num(preco_ref)} · "
            f"Pts {_fmt_num(pnl_pts, sinal=True)} · R$ {_fmt_num(pnl_reais, sinal=True, casas=2)}",
            style={'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['mono'], 'marginTop': '2px'}
        ),
    ])

    filhos = [
        detalhe,
        html.Div('Notas:', style={'fontSize': '10px', 'color': C['text2'], 'marginTop': '10px'}),
        timeline,
    ]
    if galeria:
        filhos.append(galeria)
    return _bloco_diario(f"{pos.get('ativo', '—')} · {pos.get('contrato', '—')}", html.Div(filhos))


def _bloco_posicao_form(pos: dict, imagens: list) -> html.Div:
    """Parte EDITÁVEL do card de posição (classificação/estrelas/
    sentimento/resumo/nota nova) — ver `_bloco_posicao_live` pro raciocínio
    completo de por que isso vive num Output separado, atualizado só ao
    entrar na aba Posição (ou pelo botão "Atualizar formulários"), nunca
    pelo `interval-em-andamento` de 6s."""
    campo = _campo_diario
    estilo_input = _estilo_input_diario
    id_pos = pos['id_posicao']
    pendente = pos.get('status') == dt.STATUS_POSICAO_FECHADA_PENDENTE

    campos_classificacao = html.Div([
        campo('Estratégia', dcc.Dropdown(
            id={'type': 'ea-estrategia', 'index': id_pos}, multi=True,
            options=_opcoes_estrategias(),
            value=_lista_segura(pos.get('estrategia_tags_preliminar')) or None,
            className='classico-dropdown classico-dropdown-tags', style={'width': '260px'})),
        campo('Sala/Análise', dcc.Dropdown(
            id={'type': 'ea-sala', 'index': id_pos}, options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
            value=pos.get('sala_analise_preliminar'), className='classico-dropdown', style={'width': '170px'})),
        campo('R:R', dcc.Dropdown(
            id={'type': 'ea-rr-preset', 'index': id_pos},
            options=[{'label': l['nome'], 'value': l['id']} for _, l in dt.listar_rr_presets().iterrows()],
            placeholder='nenhuma', className='classico-dropdown', style={'width': '140px'})),
        campo('Alvo (pts)', dcc.Input(id={'type': 'ea-alvo', 'index': id_pos}, type='text', inputMode='decimal',
              debounce=True, value=pos.get('alvo_pontos_preliminar'), style=estilo_input({'width': '130px'}))),
        campo('Stop (pts)', dcc.Input(id={'type': 'ea-stop', 'index': id_pos}, type='text', inputMode='decimal',
              debounce=True, value=pos.get('stop_pontos_preliminar'), style=estilo_input({'width': '130px'}))),
        # R:R calculado automaticamente a partir de Alvo/Stop (feedback
        # 2026-07-20) — só leitura, não confundir com o preset acima (que
        # PREENCHE Alvo/Stop a partir de um valor salvo; este aqui é o
        # inverso, deriva o número a partir do que já está nos campos).
        campo('R:R calculado', html.Div(
            id={'type': 'ea-rr-calculado', 'index': id_pos},
            style={'fontSize': '13px', 'fontFamily': FONTS['mono'], 'color': C['text2'], 'paddingTop': '7px'})),
        campo('Nível S/R referenciado', dcc.Input(id={'type': 'ea-nivel-sr', 'index': id_pos}, type='text', inputMode='decimal',
              debounce=True, value=pos.get('nivel_sr_referencia_preliminar'), style=estilo_input({'width': '140px'}))),
    ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '10px'})

    resumo_input = campo('Resumo Operacional', dcc.Textarea(
        id={'type': 'ea-resumo', 'index': id_pos}, rows=4,
        value=pos.get('resumo_operacional_preliminar'), style=estilo_input({'resize': 'none', 'height': 'auto'})))

    bloco_emocional = _bloco_emocional(
        id_confianca={'type': 'ea-emo-confianca', 'index': id_pos},
        id_humor={'type': 'ea-emo-humor', 'index': id_pos},
        id_energia={'type': 'ea-emo-energia', 'index': id_pos},
        id_atencao={'type': 'ea-emo-atencao', 'index': id_pos},
        id_presenca={'type': 'ea-emo-presenca', 'index': id_pos},
        id_tags={'type': 'ea-tags-emocionais', 'index': id_pos},
        id_sentimento={'type': 'ea-sentimento-mercado', 'index': id_pos},
        id_label_sentimento={'type': 'ea-sentimento-label', 'index': id_pos},
        valores={
            'confianca': pos.get('emo_confianca_preliminar'), 'humor': pos.get('emo_humor_preliminar'),
            'energia': pos.get('emo_energia_preliminar'), 'atencao': pos.get('emo_atencao_preliminar'),
            'presenca': pos.get('emo_presenca_preliminar'), 'tags': pos.get('tags_emocionais_preliminar'),
            'sentimento': pos.get('sentimento_mercado_preliminar'),
        },
        widget_tags_customizado=True,
    )

    # Feedback 2026-07-20 ("o sistema de salvamento é muito primitivo...
    # qualquer sistema consegue salvar tudo imediatamente"): NENHUM campo
    # deste formulário depende mais de um botão "Salvar" — a nota grava
    # sozinha ao perder foco (`n_blur`, ver `salvar_nota_em_andamento`) e
    # os demais campos (estratégia/sala/alvo/stop/nível S/R/resumo/
    # estrelas/tags/sentimento) auto-salvam a cada mudança (ver
    # `auto_salvar_campos_em_andamento`) — achado real: um trade fechado
    # (par hedge) apareceu "Pronta pra Finalizar" com tudo em branco porque
    # o usuário preencheu a classificação enquanto a posição ainda estava
    # `aberta` e nunca clicou em "Salvar" antes dela fechar — sem botão,
    # não tem como esquecer.
    nota_input = html.Div([
        dcc.Textarea(id={'type': 'ea-nota', 'index': id_pos}, rows=2,
                     placeholder='Escreva uma nova anotação (análise durante ou depois da operação)... '
                                  '(salva sozinha ao sair do campo)',
                     style=estilo_input({'resize': 'none', 'height': 'auto'})),
        html.Div(id={'type': 'ea-status', 'index': id_pos}, style={
            'fontSize': '11px', 'color': C['text2'], 'marginTop': '4px'}),
    ], style={'marginTop': '10px'})

    # Campo de print embutido NESTE card (não mais um seletor+upload
    # compartilhado no rodapé da aba — feedback de 2026-07-12: "eu quero
    # que apareça o campo trade a trade, e não no final com seletor").
    # Feedback 2026-07-20: "não faz sentido eu adicionar a imagem, e depois
    # clicar no botão" (auto-anexa, ver `preview_e_anexar_imagem_em_
    # andamento`) E "eu não quero ter que apertar em um botão para depois
    # dar Ctrl+V, o sistema tem que capturar automaticamente" — o botão
    # "clique aqui e cole" que "armava" este card pro próximo Ctrl+V foi
    # removido; agora `assets/diario_paste.js` arma sozinho o card mais
    # próximo de QUALQUER clique/foco (em qualquer campo do card — Resumo,
    # Nota, Alvo, etc.), então colar já funciona assim que o usuário estiver
    # interagindo com o card, sem esse passo extra.
    imagem_bloco = html.Div([
        html.Div('Anexar print desta posição (cole com Ctrl+V ou envie um arquivo):', style={
            'fontSize': '11px', 'color': C['text2'], 'marginTop': '10px', 'marginBottom': '4px'}),
        dcc.Upload(
            id={'type': 'ea-upload-imagem', 'index': id_pos},
            children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '12px'}),
            style={
                'background': C['bg'], 'border': f'1px dashed {C["border"]}', 'borderRadius': '8px',
                'padding': '10px', 'textAlign': 'center', 'cursor': 'pointer', 'color': C['text2'],
            },
        ),
        html.Div(id={'type': 'ea-preview-imagem', 'index': id_pos}, style={'marginTop': '8px'}),
    ], style={'marginTop': '10px'})

    filhos = [campos_classificacao, resumo_input, bloco_emocional, nota_input, imagem_bloco]
    if pendente:
        filhos.append(_bloco_finalizar_em_andamento(pos, imagens))

    return _bloco_diario(f"{pos.get('ativo', '—')} · {pos.get('contrato', '—')} — Classificação", html.Div(filhos))


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


def _preparar_registros_posicao(posicoes: pd.DataFrame, excluir_id=None) -> tuple[list, dict, dict]:
    """
    Mescla pares de hedge (ver `dt.detectar_pares_hedge`) com posições
    avulsas (não pareadas) e agrupa por `ativo` — fonte única de verdade
    pras funções de render abaixo, pra nunca divergir entre a parte
    ao-vivo, a parte editável e a seção de pendentes no Histórico. Achado
    real 2026-07-15: cada ORDEM/ticket virava seu próprio card+formulário
    (Jesué opera com OCO, então SL/TP disparando cria posições opostas
    novas no MT5 em vez de fechar a original) — o usuário quer 1 card por
    TRADE, não por ticket.

    Feedback 2026-07-16 ("a visualização deve ser dinâmica, apenas as
    posições abertas... trade fechado vai pro histórico"): separa o
    resultado em DOIS grupos por ativo em vez de um só —
    `por_ativo_abertas` (só avulsas genuinamente `status='aberta'`, sem
    par — o que continua aparecendo na aba Posição) e `por_ativo_prontas`
    (pares de hedge mesclados — SEMPRE prontos, ver
    `montar_posicao_sintetica_par` — + avulsas já `fechada_pendente` —
    migraram pra uma seção "Pendente de Classificação" dentro do
    Histórico; mesmo formulário de sempre, só mudou onde mora).

    `excluir_id` (2026-07-21): remove esse `id_posicao` ANTES de qualquer
    processamento — usado quando o mesmo ticket está aberto no pop-up de
    edição de pendência do Histórico (ver `abrir_pendencia_editar`), pra
    nunca montar o MESMO componente pattern-matching (`{'type':'ea-...',
    'index':id_pos}`) duas vezes ao mesmo tempo no DOM (a aba Posição
    nunca desmonta, só esconde via display:none — ver convenção do
    projeto). Sem isso, reabrir a aba Posição com o pop-up ainda aberto
    duplicaria o card, quebrando o dispatch de callback MATCH/ALL.

    Retorna `(pares_crus, por_ativo_abertas, por_ativo_prontas)` — os
    dicts dentro de cada lista já estão no formato de
    `montar_posicao_sintetica_par` (pares) ou da linha original do
    parquet (avulsas), prontos pra `_bloco_posicao_live`/`_bloco_posicao_form`
    sem distinção nenhuma.
    """
    if posicoes is None or posicoes.empty:
        return [], {}, {}
    if excluir_id is not None:
        # `excluir_id` pode ser um ticket avulso OU um par de hedge
        # composto "ticket_a-ticket_b" (ver `dt.listar_pendencias_
        # historico`) — nesse caso `id_posicao` nunca bate com a string
        # composta direto (a coluna só tem tickets individuais), então
        # precisa excluir AS DUAS pernas, senão o par continua se
        # formando aqui e duplica o card mostrado no pop-up.
        par = _parse_index_par(excluir_id)
        ids_excluir = set(par) if par else {excluir_id}
        posicoes = posicoes[~posicoes['id_posicao'].isin(ids_excluir)]
        if posicoes.empty:
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


def _secao_status_posicoes(posicoes: pd.DataFrame, pares: list) -> html.Div:
    """Resumo rápido da aba — quantas posições existem agora (por ativo),
    quantos pares de hedge já foram mesclados num trade só, e se sobra
    exposição líquida real por contrato (ver `dt.calcular_exposicao_residual`
    — Requisito 4 da especificação de pareamento hedge, 2026-07-16), pra
    deixar explícito o que a seção "Status" está mostrando antes de descer
    pros cards de cada ativo."""
    total = 0 if posicoes is None or posicoes.empty else len(posicoes)
    n_win = 0 if not total else int((posicoes['ativo'] == 'WIN').sum())
    n_wdo = 0 if not total else int((posicoes['ativo'] == 'WDO').sum())
    linhas = [
        (f"{total} posição(ões)/ordem(ns) rastreada(s) no MT5 agora — {n_win} WIN, {n_wdo} WDO.", C['text2']),
        (f"{len(pares)} par(es) de hedge detectado(s) (SL/TP oposto virou posição nova) — "
         f"mesclado(s) automaticamente em 1 trade líquido cada." if pares
         else "Nenhum par de hedge detectado agora.", C['text2']),
    ]
    # Achado real 2026-07-21: uma falha silenciosa na sincronização em
    # background podia congelar este parquet num snapshot velho (posição
    # nova sumia da tela, exposição residual calculada sobre dado
    # desatualizado) sem nenhum aviso — nunca mais afirmar "zerado"/"X
    # posições" sem dizer de quando é o dado. `atualizado_em` é escrito em
    # TODA linha a cada ciclo bem-sucedido (`sincronizar_posicoes_abertas`).
    if total:
        atualizado_em = pd.to_datetime(posicoes['atualizado_em']).max()
        atraso_s = (datetime.now() - atualizado_em.to_pydatetime()).total_seconds()
        if atraso_s > 15:
            linhas.append((
                f"⚠ Sincronização com MT5 pode estar atrasada — dado de "
                f"{atualizado_em.strftime('%H:%M:%S')} ({atraso_s:.0f}s atrás). "
                f"Não confie nos números acima sem confirmar no MT5.",
                C['yellow'],
            ))
        else:
            linhas.append((f"Sincronizado às {atualizado_em.strftime('%H:%M:%S')}.", C['text2']))
    residual = dt.calcular_exposicao_residual(posicoes)
    sobras = {contrato: liquido for contrato, liquido in residual.items() if liquido != 0}
    if sobras:
        texto_sobras = ', '.join(f"{contrato}: {liquido:+.0f}" for contrato, liquido in sobras.items())
        linhas.append((f"⚠ Exposição líquida real (sem par no momento) — {texto_sobras}.", C['yellow']))
    else:
        linhas.append(("Exposição líquida zerada em todos os contratos — nenhum risco de mercado residual.", C['text2']))
    return _bloco_diario('Status das Posições', html.Div([
        html.Div(t, style={'fontSize': '12px', 'color': cor, 'marginBottom': '4px'}) for t, cor in linhas
    ]))


def _registros_por_ativo(mapa: dict, ativo_selecionado: str) -> list:
    """Junta os registros do ativo selecionado com qualquer "outro" ativo
    fora de WIN/WDO (não deveria existir na prática, mas não pode
    desaparecer silenciosamente se existir) — mesmo filtro usado tanto
    pelas posições abertas quanto pelas prontas pra finalizar."""
    registros = list(mapa.get(ativo_selecionado, []))
    for ativo_nome, lista in mapa.items():
        if ativo_nome not in ('WIN', 'WDO'):
            registros.extend(lista)
    return registros


def _bloco_posicao_completo(p: dict, imagens: list) -> html.Div:
    """Info ao vivo (só-leitura) + formulário editável EMPILHADOS num único
    bloco — feedback 2026-07-20: "quando um trade está aberto aparecem as
    informações do trade desconectados do formulário... o formulário deve
    ficar logo abaixo do bloco do trade aberto". Reaproveita
    `_bloco_posicao_live`/`_bloco_posicao_form` sem alteração nenhuma, só
    muda ONDE ficam (adjacentes, um container Dash só — antes viviam em
    dois Outputs separados, `em-andamento-conteudo`/`em-andamento-
    formularios`, um bem abaixo do outro na página)."""
    return html.Div([_bloco_posicao_live(p), _bloco_posicao_form(p, imagens)],
                     style={'marginBottom': '16px'})


def _ids_rastreados(registros_prontas: list, registros_abertas: list) -> list:
    """"Assinatura" do conjunto atual de posições (prontas + abertas) pro
    ativo selecionado — usada só pra decidir SE alguma coisa realmente
    mudou (posição nova, fechou, virou par) entre um tick do interval e o
    anterior, ver `atualizar_em_andamento`."""
    return sorted(str(r['id_posicao']) for r in registros_prontas + registros_abertas)


def _renderizar_posicao_completa(posicoes: pd.DataFrame, ordens: pd.DataFrame | None = None,
                                  ativo_selecionado: str = 'WIN', excluir_id=None) -> html.Div:
    """Substitui `_renderizar_em_andamento` + `_renderizar_formularios_em_
    andamento` (unificados em 2026-07-20 — ver `_bloco_posicao_completo`
    pro raciocínio do porquê). Mostra só o ativo selecionado na sub-aba
    WIN/WDO da Posição (feedback 2026-07-16).

    Feedback real 2026-07-22: "trade fechado vai para o histórico, não
    deve ficar em posição" — a seção "Pronta pra Finalizar" (pares hedge/
    avulsas já `fechada_pendente`) SAIU da aba Posição, que agora mostra
    SÓ posições genuinamente `aberta` no MT5 (ver `registros_abertas`
    abaixo). Tudo que já fechou (pronto pra classificar/finalizar) mora
    exclusivamente no Histórico (lista "PENDENTE", botão "Editar" abre o
    mesmo formulário `_bloco_posicao_completo` num pop-up — ver
    `abrir_pendencia_editar`/`renderizar_overlay_pendencia`). `pares`/
    `por_ativo_prontas` continuam computados aqui (não removidos) porque
    `_ids_rastreados` ainda precisa da assinatura completa (abertas +
    prontas) pra decidir se o tick do interval precisa reconstruir algo —
    só a RENDERIZAÇÃO da seção "prontas" foi retirada."""
    pares, por_ativo_abertas, por_ativo_prontas = _preparar_registros_posicao(posicoes, excluir_id=excluir_id)
    status = _secao_status_posicoes(posicoes, pares)

    registros_prontas = _registros_por_ativo(por_ativo_prontas, ativo_selecionado)
    registros_abertas = _registros_por_ativo(por_ativo_abertas, ativo_selecionado)

    ordens_ativo = ordens
    if ordens is not None and not ordens.empty:
        ordens_ativo = ordens[ordens['ativo'] == ativo_selecionado]
    secao_ordens = _secao_ordens_pendentes(ordens_ativo) if ordens_ativo is not None else None

    filhos = [status]
    if secao_ordens:
        filhos.append(secao_ordens)
    if not registros_abertas:
        vazio = html.Div(f'Nenhuma posição {ativo_selecionado} aberta no momento.', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
        filhos.append(vazio)
    else:
        blocos = [_bloco_posicao_completo(p, _lista_segura(p.get('imagens_em_andamento')))
                  for p in registros_abertas]
        filhos.append(_bloco_diario(f'Posições {ativo_selecionado}', html.Div(blocos)))
    return html.Div(filhos)


_CORES_ESTADO_DIA = {'livre': 'green', 'atencao': 'yellow', 'critico': 'red'}
_ROTULO_ESTADO_DIA = {
    'livre': 'Dentro do plano',
    'atencao': 'Limite do dia atingido',
    'critico': 'Fora do plano',
}


def _bloco_guarda_corpo_dia() -> html.Div:
    """Contador de operações do dia + checklist pré-trade (2026-07-30).

    O maior achado do estudo (`PLANO_DE_TRADE_AGOSTO_2026.md`) foi
    comportamental: dias com 1-2 operações somaram +R$ 328, e dias com 5 ou
    mais destruíram R$ 627 em apenas 12 dias — mais que a perda do ano.
    Esse número só existia no relatório do dia seguinte, quando já não muda
    nada. Aqui ele fica na tela ANTES da próxima entrada.

    É AVISO, nunca bloqueio — nada aqui impede o Jesué de operar. Mesmo
    princípio do Modo Aprendizagem: o sistema mede e mostra, quem decide é
    ele. A conta mora em `dt.status_do_dia`, compartilhada com o skin '98.

    Devolve SÓ os contadores. O botão do checklist e o conteúdo dele ficam
    fora, estáticos no layout — este bloco é reconstruído a cada 6s pelo
    interval, e recriar o botão zeraria `n_clicks` no browser (a armadilha
    recorrente deste código: nunca recriar o componente que dispara a
    própria callback)."""
    s = dt.status_do_dia()
    cor = C[_CORES_ESTADO_DIA[s['estado']]]
    cor_saldo = C['green'] if s['saldo'] > 0 else (C['red'] if s['saldo'] < 0 else C['text2'])

    def contador(rotulo, valor, cor_valor=None):
        return html.Div([
            html.Div(rotulo, style={'fontSize': '10px', 'color': C['text2']}),
            html.Div(valor, style={'fontSize': '20px', 'fontWeight': '700',
                                   'fontFamily': FONTS['mono'], 'color': cor_valor or C['text'],
                                   'lineHeight': '1.1'}),
        ], style={'minWidth': '80px'})

    return html.Div([
        contador('Operações hoje', f"{s['n_trades']}/{s['limite']}", cor),
        contador('Stops', str(s['n_stops']), C['red'] if s['n_stops'] else None),
        contador('Saldo do dia', f"R$ {s['saldo']:+.2f}", cor_saldo),
        html.Div([
            html.Div(_ROTULO_ESTADO_DIA[s['estado']], style={
                'fontSize': '12px', 'fontWeight': '700', 'color': cor,
                'fontFamily': FONTS['display'],
            }),
            html.Div([html.Div(m, style={'marginTop': '2px'}) for m in s['motivos']],
                     style={'fontSize': '11px', 'color': C['text2'], 'maxWidth': '460px'}),
        ], style={'flex': '1', 'minWidth': '220px'}),
    ], style={'display': 'flex', 'gap': '18px', 'alignItems': 'flex-start', 'flexWrap': 'wrap'})


def _borda_guarda_corpo() -> str:
    return C[_CORES_ESTADO_DIA[dt.status_do_dia()['estado']]]


def _conteudo_checklist_pre_trade() -> html.Div:
    """As 8 linhas de filtro do plano. `dcc.Checklist` de verdade (não texto
    decorativo) — o feedback de 2026-07-20 sobre as estrelas vale igual aqui:
    se é pra marcar, tem que ser marcável. Nada é persistido de propósito: é
    um ritual de 20 segundos antes da entrada, não mais um formulário."""
    return html.Div([
        dcc.Checklist(
            id='diario-checklist-itens',
            options=[{'label': item, 'value': str(i)}
                     for i, item in enumerate(cfg.DIARIO_CHECKLIST_PRE_TRADE)],
            value=[], labelStyle={'display': 'block', 'marginBottom': '5px', 'fontSize': '12px'},
            inputStyle={'marginRight': '8px'},
        ),
        html.Div(id='diario-checklist-status', style={'fontSize': '11px', 'marginTop': '8px'}),
    ], style={'marginTop': '12px', 'paddingTop': '12px', 'borderTop': f'1px solid {C["border"]}'})


def _construir_diario_em_andamento() -> html.Div:
    return html.Div([
        # Guarda-corpo do dia (2026-07-30) — fica ACIMA de tudo na Posição
        # de propósito: é a informação que precisa ser vista antes de abrir a
        # próxima operação, não depois. Só os contadores são atualizados pelo
        # interval; o botão e o checklist são estáticos (ver
        # `_bloco_guarda_corpo_dia`).
        html.Div([
            html.Div([
                html.Div(_bloco_guarda_corpo_dia(), id='diario-guarda-corpo-dia',
                         style={'flex': '1'}),
                html.Button('Checklist pré-trade', id='btn-diario-checklist', n_clicks=0, style={
                    'background': 'transparent', 'color': C['text2'],
                    'border': f'1px solid {C["border"]}', 'borderRadius': '6px',
                    'padding': '5px 12px', 'cursor': 'pointer', 'fontSize': '11px',
                    'alignSelf': 'center', 'flexShrink': '0', 'marginLeft': '12px',
                }),
            ], style={'display': 'flex', 'alignItems': 'flex-start'}),
            html.Div(id='diario-checklist-conteudo'),
        ], style={
            'padding': '12px 14px', 'marginBottom': '14px', 'background': C['panel'],
            'border': f'1px solid {C["border"]}', 'borderRadius': '8px',
        }),
        # Sub-abas WIN/WDO (feedback 2026-07-16: "eu quero WDO e WIN
        # separados em sub-abas da aba Posições") — mesmo padrão de
        # Store+botões+callback de estilo já usado pras abas principais do
        # Diário (`store-diario-sub-aba`), só que aninhado aqui dentro.
        dcc.Store(id='store-posicao-ativo-subaba', data='WIN'),
        html.Div([
            html.Button(nome, id=f'btn-posicao-ativo-{nome}', n_clicks=0,
                        style=_estilo_subaba_diario(nome, 'WIN'))
            for nome in ('WIN', 'WDO')
        ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '12px'}),
        dcc.Interval(id='interval-em-andamento', interval=6_000, n_intervals=0),
        # Guarda a "assinatura" (ids) do último render pra decidir se um
        # tick do interval precisa reconstruir alguma coisa de verdade —
        # ver `atualizar_em_andamento`.
        dcc.Store(id='store-em-andamento-ids-conhecidos', data=[]),
        html.Div(id='em-andamento-conteudo'),
    ])


def _td_diario(conteudo, extra=None) -> html.Td:
    estilo = {'padding': '6px 10px', 'fontSize': '12px', 'fontFamily': FONTS['mono'],
              'borderBottom': f'1px solid {C["border"]}'}
    if extra:
        estilo.update(extra)
    return html.Td(conteudo, style=estilo)


# Aderência aos Traçados deixou de ser aba própria (feedback diário.txt, 2026-07-11)
# — vira coluna extra aqui (join por id_trade), ver diario_app_98.py pro
# mesmo raciocínio.
_COR_STATUS_ADERENCIA_TRACADOS = {
    'visto_preciso': 'green', 'visto_proximo': 'yellow', 'nao_visto': 'red',
}


def _formatar_janela(chave: str | None) -> str:
    """"Abertura (9:00-9:30)" — ver diario_app_98.py::_formatar_janela_98
    pro mesmo raciocínio."""
    if not chave or chave not in cfg.JANELAS_PREGAO:
        return chave or '—'
    inicio, fim = cfg.JANELAS_PREGAO[chave]
    return f"{chave.replace('_', ' ').title()} ({inicio}-{fim})"


def _opcoes_janelas() -> list[dict]:
    return [{'label': _formatar_janela(k), 'value': k} for k in cfg.JANELAS_PREGAO.keys()]


def _mapa_nomes_estrategias() -> dict:
    return {l['slug']: l['nome'] for _, l in dt.listar_estrategias(somente_ativas=False).iterrows()}


def _mapa_categorias_estrategias() -> dict:
    """slug -> rótulo da categoria (Tendência/Reversão/Lateralidade), ou '—'
    quando não classificada. Usado pela coluna Categoria de "Confiabilidade
    por Estratégia" (2026-07-30, feedback: "o bloco de estratégias não
    mudou nada" — a categoria só aparecia na aba Estratégias, não onde o
    Jesué de fato olha o desempenho)."""
    df = dt.listar_estrategias(somente_ativas=False)
    return {
        l['slug']: dt.ROTULO_CATEGORIA_ESTRATEGIA.get(l.get('categoria'), '—')
        for _, l in df.iterrows()
    }


def _formatar_tags_estrategia(tags, mapa_nomes: dict) -> str:
    lista = list(tags) if hasattr(tags, '__iter__') and not isinstance(tags, str) else []
    return ', '.join(mapa_nomes.get(t, t.replace('_', ' ').title()) for t in lista) or '—'


def _linha_historico(trade: pd.Series, aderencia_por_trade: dict, mapa_nomes_estrategias: dict) -> list[html.Tr]:
    """Retorna 2 `Tr` (resumo + detalhe) em vez de 1 — feedback 2026-07-16:
    "ao invés de colocar os detalhes do trade no final da página, coloque
    na linha do histórico de forma que quando clicamos em Detalhes a linha
    se expande... quando clicamos novamente esses detalhes ocultam,
    voltando a se recolher". Substitui o popup/modal (`_overlay`) por uma
    2ª linha de detalhe, inicialmente vazia (recolhida), alternada por
    `alternar_detalhe_historico` (usa o próprio texto da seta como estado:
    "▶" fechado, "▼" aberto — sem precisar de um `dcc.Store` por linha)."""
    id_trade = int(trade['id_trade']) if pd.notna(trade.get('id_trade')) else None
    tags_str = _formatar_tags_estrategia(trade['estrategia_tags'], mapa_nomes_estrategias)
    saldo = trade['saldo_operacao_reais']
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0
    cor_saldo = C['green'] if saldo_num > 0 else (C['red'] if saldo_num < 0 else C['text2'])
    thumb = (
        html.Img(src=f"/diario-imagens/{trade['imagem_arquivo']}",
                 id={'type': 'diario-img-thumb', 'index': id_trade}, n_clicks=0,
                 style={
                     'width': '56px', 'height': '36px', 'objectFit': 'cover', 'borderRadius': '4px',
                     'border': f'1px solid {C["border"]}', 'cursor': 'pointer',
                 }) if trade.get('imagem_arquivo') and id_trade is not None else html.Span('—', style={'color': C['text2']})
    )
    classificacao = aderencia_por_trade.get(id_trade) if id_trade is not None else None
    celula_aderencia = (
        _td_diario(classificacao.replace('_', ' '),
                   extra={'color': C[_COR_STATUS_ADERENCIA_TRACADOS.get(classificacao, 'text2')], 'fontSize': '10px'})
        if classificacao else _td_diario('—')
    )
    botao_expandir = (
        html.Button('▶', id={'type': 'diario-btn-expandir', 'index': id_trade}, n_clicks=0, style={
            'background': 'transparent', 'color': C['text2'], 'border': f'1px solid {C["border"]}',
            'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer', 'fontSize': '11px',
        }) if id_trade is not None else ''
    )
    # Pedido do Jesué em 2026-07-30, junto da classificação retroativa: "para
    # que eu possa posteriormente avaliar os trades fora das estratégias e
    # reclassificá-las". Abre `renderizar_overlay_trade` — que só edita
    # LEITURA do trade (estratégia, texto, contexto emocional); preço/saldo/
    # data continuam vindo do MT5 e não são editáveis (ver
    # `dt.CAMPOS_EDITAVEIS_TRADE`).
    botao_editar_trade = (
        html.Button('✎', id={'type': 'diario-btn-editar-trade', 'index': id_trade}, n_clicks=0,
                    title='Editar classificação e anotações desta operação', style={
                        'background': 'transparent', 'color': C['gold'], 'border': f'1px solid {C["gold"]}',
                        'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer',
                        'fontSize': '11px', 'marginRight': '6px',
                    }) if id_trade is not None else ''
    )
    acoes = html.Div([botao_editar_trade, botao_expandir], style={'display': 'flex'}) if id_trade is not None else ''
    linha_resumo = html.Tr([
        _td_diario(str(trade['data_op'])),
        _td_diario(trade['ativo']),
        _td_diario(_formatar_janela(trade.get('janela_pregao'))),
        _td_diario(tags_str),
        _td_diario(trade.get('tipo_operacao') or '—'),
        _td_diario(f"{trade['resultado_pontos']:+.0f}" if pd.notna(trade['resultado_pontos']) else '—'),
        _td_diario(f"R$ {saldo_num:+.2f}" if pd.notna(saldo) else '—',
                   extra={'color': cor_saldo, 'fontWeight': '700'}),
        _td_diario(thumb),
        celula_aderencia,
        _td_diario(acoes),
    ])
    linha_detalhe = html.Tr(
        html.Td(html.Div(id={'type': 'diario-detalhe-linha', 'index': id_trade}),
                colSpan=10, style={'padding': '0', 'border': 'none'}),
        id={'type': 'diario-tr-detalhe', 'index': id_trade},
    ) if id_trade is not None else None
    return [linha_resumo, linha_detalhe] if linha_detalhe is not None else [linha_resumo]


def _linha_pendencia(p: pd.Series) -> html.Tr:
    """1 linha de execução REAL na conta sem nenhum trade correspondente
    logado no Diário — ver `dt.listar_pendencias_historico` (2026-07-21,
    une fechamentos ao vivo da aba Posição + descobertas históricas da
    reconciliação de 30 dias, pedido do Jesué: "Posição fechada deve ir
    para o histórico automaticamente, com edição pendente"). `id_editar`
    já vem no formato certo pro botão (composto pra par de hedge, ticket
    avulso senão — ver `dt.materializar_pendencia_como_rascunho`)."""
    saldo_est = p.get('saldo_estimado')
    saldo_txt = f"R$ {saldo_est:+.2f} (estimado)" if pd.notna(saldo_est) else '—'
    botao_editar = html.Button('Editar', id={'type': 'diario-btn-editar-pendencia', 'index': p['id_editar']},
                                n_clicks=0, style={
                                    'background': C['gold'], 'color': C['bg'], 'border': 'none',
                                    'borderRadius': '4px', 'padding': '2px 12px', 'cursor': 'pointer',
                                    'fontSize': '11px', 'fontWeight': '700',
                                })
    # Feedback 2026-07-27: "um botão com um check pra gravar a operação no
    # histórico sem precisar editar, pegando apenas as informações padrão
    # do MT5" — atalho de 1 clique que pula o pop-up inteiro (ver
    # `finalizar_rapido_pendencia`), pra quando o usuário não tem nenhuma
    # classificação/nota pra adicionar e só quer tirar a pendência da
    # lista com os dados que o MT5 já forneceu.
    botao_finalizar_rapido = html.Button('✓', id={'type': 'diario-btn-finalizar-rapido', 'index': p['id_editar']},
                                          n_clicks=0, title='Gravar no Diário com os dados padrão do MT5, sem editar',
                                          style={
                                              'background': C['green'], 'color': C['bg'], 'border': 'none',
                                              'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer',
                                              'fontSize': '11px', 'fontWeight': '700', 'marginLeft': '6px',
                                          })
    return html.Tr([
        _td_diario(str(p.get('data') or '—')),
        _td_diario(str(p.get('ativo') or '—')),
        _td_diario('—'), _td_diario('—'), _td_diario('—'), _td_diario('—'),
        _td_diario(saldo_txt, extra={'color': C['text2'], 'fontStyle': 'italic'}),
        _td_diario('—'),
        _td_diario('PENDENTE', extra={
            'color': C['bg'], 'background': C['yellow'], 'fontWeight': '700',
            'fontSize': '10px', 'textAlign': 'center', 'borderRadius': '4px',
        }),
        _td_diario(html.Div([botao_editar, botao_finalizar_rapido], style={'display': 'flex'})),
    ], style={'background': 'rgba(200,169,110,0.08)'})


def _construir_diario_historico() -> html.Div:
    # Feedback 2026-07-17: a seção "Pendente de Classificação" que morava
    # aqui (desde 2026-07-16) voltou pra aba Posição — trade fechado ainda
    # precisa de 1 clique consciente em "Finalizar" antes de virar registro
    # permanente, mas isso agora acontece sem trocar de aba (ver
    # `_renderizar_posicao_completa`).
    return html.Div([
        # Os 2 overlays (imagem ampliada e pop-up de pendência) NÃO moram mais
        # aqui — subiram pro `app.layout` global, logo ACIMA de
        # `diario-sub-aba-conteudo`. Ver o comentário lá pro motivo (eram
        # Output de callbacks disparadas por Store globais, uma combinação que
        # quebra fora do Histórico). A posição no topo do conteúdo, que é o
        # que o feedback de 2026-07-20 pedia ("a imagem deve abrir em pop-up e
        # não no final da lista"), foi preservada.
        html.Div([
            dcc.Dropdown(id='diario-hist-ativo', options=_opcoes_diario(cfg.ATIVOS_DIARIO),
                         placeholder='Ativo', className='classico-dropdown', style={'width': '140px'}),
            dcc.Dropdown(id='diario-hist-janela', options=_opcoes_janelas(),
                         placeholder='Janela', className='classico-dropdown', style={'width': '190px'}),
            dcc.Dropdown(id='diario-hist-estrategia', options=_opcoes_estrategias(),
                         placeholder='Estratégia', className='classico-dropdown', style={'width': '200px'}),
            dcc.Input(id='diario-hist-data-ini', type='date', style=_estilo_input_diario({'width': '150px'})),
            html.Span('até', style={'fontSize': '12px', 'color': C['text2'], 'alignSelf': 'center'}),
            dcc.Input(id='diario-hist-data-fim', type='date', style=_estilo_input_diario({'width': '150px'})),
        ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '8px', 'flexWrap': 'wrap', 'alignItems': 'center'}),
        html.Div(id='diario-hist-banner-filtro-cal', style={'marginBottom': '10px'}),
        html.Div(id='diario-historico-tabela'),
    ])


def _overlay(conteudo, id_backdrop: str) -> html.Div:
    """Pop-up centralizado no viewport REAL da janela.

    Histórico desta função, porque ela já errou nas duas direções:

    1. `position: fixed` (até 2026-07-16) — dentro do iframe do Diário,
       `fixed` se ancora à altura TOTAL do iframe, que se auto-ajusta pra
       caber todo o conteúdo (`assets/diario_iframe_resize.js`). O pop-up
       "centralizado" caía milhares de px abaixo da dobra e parecia que o
       clique não fazia nada.
    2. Fluxo normal + `scrollIntoView` (2026-07-16 a 2026-07-30) — resolvia
       o "invisível", mas produzia o sintoma que o Jesué reportou em
       2026-07-30: *"abre no meio da página, preciso rolar para chegar lá"*.
       Pior: com `height: 88vh` e `vh` valendo a altura do iframe crescido,
       o painel virava uma caixa de milhares de px com a imagem perdida no
       meio de uma área cinza (foi o que o screenshot mostrou).

    3. **Agora:** o PAI mede onde o iframe cruza o viewport de verdade e
       manda a fatia visível por postMessage; o CSS posiciona o overlay
       exatamente sobre ela (`.diario-overlay-viewport` em style.css, medida
       em `assets/diario_overlay_viewport.js`). Standalone em :8053, o
       fallback do CSS é `fixed; inset: 0` — que sempre foi o certo lá.

    Não depende mais de `scrollIntoView` — ver `_registrar_scroll_ate_overlay`,
    hoje inofensiva (o overlay já nasce na tela)."""
    return html.Div([
        html.Div(id=id_backdrop, n_clicks=0, className='diario-overlay-backdrop'),
        html.Div(conteudo, className='diario-overlay-painel'),
    ], id=f'{id_backdrop}-wrap', className='diario-overlay-viewport')


def _registrar_scroll_ate_overlay(app, id_store: str, id_container_estavel: str, id_wrap: str):
    """Rola a página até o painel de detalhe quando ele abre (`id_store`
    vira não-None) — ver `_overlay` pro motivo de não dar mais pra confiar
    em `position:fixed` pra centralizar.

    `Output` mira `id_container_estavel` — o `html.Div` placeholder
    declarado 1x no `app.layout` (`diario-overlay-detalhe`/`-imagem`,
    SEMPRE presente, nunca removido), não o painel dinâmico `id_wrap`
    (que só passa a existir depois que a OUTRA callback reagindo ao MESMO
    Input já rodou) — evita depender de ordem entre as duas callbacks. O
    `setTimeout` dá tempo do React montar o painel antes do
    `getElementById` ir procurar por ele."""
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


def _campo_detalhe(label: str, valor) -> html.Div:
    # Ver diario_app_98.py::_campo_detalhe_98 pro mesmo raciocínio
    # (comparar com pd.isna, não com a string 'nan').
    vazio = valor is None or valor == '' or (isinstance(valor, float) and pd.isna(valor))
    return html.Div([
        html.Span(f"{label}: ", style={'color': C['text2'], 'fontSize': '11px'}),
        html.Span('—' if vazio else str(valor), style={'fontSize': '12px'}),
    ], style={'marginBottom': '5px'})


def _conteudo_detalhe_trade(id_trade: int) -> html.Div | None:
    """Conteúdo do detalhe de uma operação — antes um popup/modal
    (`position:fixed`, ver `_overlay`), agora renderizado DENTRO da própria
    linha do Histórico (feedback 2026-07-16: "quando clicarmos em Detalhes
    a linha se expande... clicarmos novamente esses detalhes ocultam") —
    ver `_linha_historico`/`alternar_detalhe_historico`. Sem botão de
    fechar próprio: a seta da linha já alterna aberto/fechado."""
    df = dt.listar_trades()
    linha_df = df[df['id_trade'] == id_trade]
    if linha_df.empty:
        return None
    trade = linha_df.iloc[0]

    mapa_nomes = _mapa_nomes_estrategias()
    tags_str = _formatar_tags_estrategia(trade['estrategia_tags'], mapa_nomes)
    tags_erro = trade.get('tags_setup_erro')
    tags_erro_str = ', '.join(list(tags_erro)) if hasattr(tags_erro, '__iter__') and not isinstance(tags_erro, str) else '—'

    tags_emocionais = trade.get('tags_emocionais')
    tags_emocionais_str = (', '.join(list(tags_emocionais))
                            if hasattr(tags_emocionais, '__iter__') and not isinstance(tags_emocionais, str) else '—')
    tem_dado_emocional = any(pd.notna(trade.get(c)) for c in
                             ('emo_confianca', 'emo_humor', 'emo_energia', 'emo_atencao', 'emo_presenca', 'sentimento_mercado'))

    aderencia_df = dt.avaliar_aderencia_tracados()
    linha_aderencia = aderencia_df[aderencia_df['id_trade'] == id_trade] if not aderencia_df.empty else aderencia_df
    bloco_aderencia = None
    if not linha_aderencia.empty:
        classificacao = linha_aderencia.iloc[0]['classificacao']
        bloco_aderencia = html.Div(
            f"Aderência aos Traçados: {classificacao.replace('_', ' ')}",
            style={'color': C[_COR_STATUS_ADERENCIA_TRACADOS.get(classificacao, 'text2')],
                   'fontWeight': '700', 'fontSize': '12px', 'marginBottom': '6px'})

    padroes_df = persistencia.carregar_padroes_comportamentais()
    flags_padroes = []
    if padroes_df is not None and not padroes_df.empty:
        relacionados = padroes_df[(padroes_df['id_trade'] == id_trade) | (padroes_df['id_trade_anterior'] == id_trade)]
        for _, p in relacionados.iterrows():
            flags_padroes.append(html.Div(
                f"⚠ {p['tipo'].replace('_', ' ').title()}: {p['detalhe']}",
                style={'color': C[_COR_TIPO_PADRAO.get(p['tipo'], 'text2')], 'fontSize': '12px', 'marginBottom': '4px'}))

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
                           id={'type': 'diario-img-thumb-detalhe', 'index': id_trade}, n_clicks=0, style={
            'maxWidth': '360px', 'maxHeight': '260px', 'objectFit': 'contain', 'borderRadius': '6px',
            'border': f"1px solid {C['border']}", 'marginTop': '8px', 'display': 'block', 'cursor': 'pointer',
        })

    saldo = trade['saldo_operacao_reais']
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0

    return _bloco_diario(f"Detalhe da Operação #{id_trade}", html.Div([
        html.Div([
            _campo_detalhe('Data', trade['data_op']),
            _campo_detalhe('Ativo', trade['ativo']),
            _campo_detalhe('Janela', _formatar_janela(trade.get('janela_pregao'))),
            _campo_detalhe('Tipo', trade.get('tipo_operacao')),
            _campo_detalhe('Estratégia', tags_str),
            _campo_detalhe('Sala/Análise', trade.get('sala_analise')),
            _campo_detalhe('Alvo (pts)', trade.get('alvo_pontos')),
            _campo_detalhe('Stop (pts)', trade.get('stop_pontos')),
            _campo_detalhe('Lote', trade.get('lote')),
            _campo_detalhe('Preço Entrada', trade.get('preco_entrada')),
            _campo_detalhe('Preço Saída', trade.get('preco_saida')),
            _campo_detalhe('Tipo Saída', trade.get('tipo_saida')),
            _campo_detalhe('Resultado (pts)', trade.get('resultado_pontos')),
            _campo_detalhe('Saldo (R$)', f"R$ {saldo_num:+.2f}"),
            _campo_detalhe('Nível S/R referenciado', trade.get('nivel_sr_referencia')),
            _campo_detalhe('Tags Setup/Erro', tags_erro_str),
        ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(190px, 1fr))', 'gap': '2px 14px'}),
        html.Div([
            html.Div('Estado Emocional', style={
                'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
                'fontWeight': '700', 'marginTop': '10px', 'marginBottom': '4px',
            }),
            html.Div([
                _campo_detalhe('Confiança', _estrelas_texto(trade.get('emo_confianca'))),
                _campo_detalhe('Humor', _estrelas_texto(trade.get('emo_humor'))),
                _campo_detalhe('Energia', _estrelas_texto(trade.get('emo_energia'))),
                _campo_detalhe('Atenção', _estrelas_texto(trade.get('emo_atencao'))),
                _campo_detalhe('Presença', _estrelas_texto(trade.get('emo_presenca'))),
                _campo_detalhe('Sentimento de Mercado', _texto_sentimento(trade.get('sentimento_mercado'))),
                _campo_detalhe('Print Emocional', tags_emocionais_str),
            ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(190px, 1fr))', 'gap': '2px 14px'}),
        ]) if tem_dado_emocional else None,
        html.Div([
            html.Div('Resumo Operacional:', style={'fontSize': '11px', 'color': C['text2'], 'marginTop': '8px'}),
            html.Div(trade.get('resumo_operacional') or '—', style={'fontSize': '12px', 'whiteSpace': 'pre-wrap'}),
            html.Div('Obs (autocoaching):', style={'fontSize': '11px', 'color': C['text2'], 'marginTop': '8px'}),
            html.Div(trade.get('obs') or '—', style={'fontSize': '12px', 'whiteSpace': 'pre-wrap'}),
        ]),
        bloco_aderencia,
        html.Div(flags_padroes) if flags_padroes else None,
        html.Div(f"Snapshot do motor no save: {snapshot_resumo}" if snapshot_resumo else 'Snapshot do motor: não capturado (trade antigo).',
                 style={'fontSize': '11px', 'color': C['text2'], 'marginTop': '8px'}),
        imagem,
    ]))


# ---------------------------------------------------------------------------
# Header de stat cards estilo TradeZella (item F, fase 2,
# `spec_redesenho_tradezella.md`) — Net P&L / Trade Win% / Avg Win-Loss /
# Profit Factor / Current Streak, substituindo os grupos Quantidade/Taxas/
# Valores antigos (que continuam existindo, só demovidos pra um bloco
# "Detalhes" logo abaixo — nenhum dado é descartado, só reorganizado).
# ---------------------------------------------------------------------------

def _card_header_tradezella(titulo: str, valor_str, cor_valor=None, componente=None,
                              animar_numero: bool = False) -> dmc.Card:
    """Card estilo TradeZella (item F, Fase 2.6 — `dmc.Card` real, não
    `html.Div` com sombra pintada à mão): elevação nativa (`shadow`/
    `withBorder`), cantos arredondados via `radius`, hover embutido do
    componente. `componente`, se passado, é um objeto DMC animável de
    verdade (`dmc.RingProgress`/`dmc.ProgressRoot`), nunca mais um
    `dcc.Graph` Plotly estático pra esse papel.

    `animar_numero=True` marca o valor com a classe `tz-numero-animado`
    (`assets/tradezella_ux.js` conta de 0 até o valor real ao montar) — só
    seguro pra `valor_str` de STRING simples (nunca quando `valor_str` é
    um componente composto, tipo o badge+texto do Current Streak: o JS
    reescreve `el.textContent`, o que apagaria filhos não-texto)."""
    # `c=None` quebra o parser de cor do Mantine no navegador (Python
    # `None` vira JSON `null`, e `typeof null === 'object'` em JS — o
    # parser só aceita string ou `undefined`) — só passa `c` quando há
    # cor de verdade, mesmo cuidado em `_card_estatistica` logo abaixo.
    kwargs_valor = {'c': cor_valor} if cor_valor else {}
    if animar_numero:
        kwargs_valor['className'] = 'tz-numero-animado'
    texto = html.Div([
        dmc.Text(titulo, size='sm', c='dimmed'),
        dmc.Text(valor_str, size='xl', fw=700, **kwargs_valor),
    ])
    # Gráfico ao LADO do número, não embaixo (2026-07-31, feedback do
    # Jesué: "não gosto desses espaços vazios") — um `dmc.RingProgress` de
    # 70px embaixo de um texto de ~30px deixava um vão vazio à direita
    # dele; lado a lado (`dmc.Group justify='space-between'`) usa a
    # largura do card por inteiro, sem sobra. Cards sem gráfico (Net P&L/
    # Current Streak) ficam só com `texto`, sem vão nenhum — `align=
    # 'center'` centraliza verticalmente contra a altura do gráfico
    # quando ele existe.
    conteudo = dmc.Group([texto, componente], justify='space-between', align='center', wrap='nowrap') \
        if componente is not None else texto
    # `SimpleGrid` estica todo card da linha pra altura do mais alto (o
    # que tem RingProgress/barra) — sem centralizar verticalmente, um
    # card só-texto (Saldo Líquido/Sequência Atual) sobrava um vão em
    # branco embaixo do conteúdo (2026-07-31, 2ª rodada do mesmo
    # feedback "espaço vazio"). `display:flex` + `justifyContent:center`
    # no Card centraliza o conteúdo na altura real da linha, qualquer
    # que seja ela.
    return dmc.Card(conteudo, withBorder=True, shadow='sm', radius='lg', p='lg',
                     className='tz-card',
                     style={'flex': '1', 'minWidth': '190px', 'height': '100%',
                            'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center'})


def _cards_header_tradezella(stats: dict, tema: str) -> dmc.SimpleGrid:
    """5 cards ao estilo TradeZella (item F): Net P&L / Trade Win% / Avg
    Win-Loss / Profit Factor / Current Streak — `dmc.SimpleGrid` real (não
    `html.Div` com `display:flex` manual) pra responsividade nativa.
    Current Streak é deliberadamente ALL-TIME (`dt.sequencia_atual()` sem
    filtro): um streak "atual" cortado no limite do mês selecionado não
    faz sentido conceitual (um streak real de 3 trades pode atravessar a
    virada do mês)."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])

    saldo_total = stats['saldo_total']
    card_net_pnl = _card_header_tradezella(
        'Saldo Líquido', f"R$ {saldo_total:+.2f}",
        cor_valor=('green' if saldo_total > 0 else ('red' if saldo_total < 0 else None)),
        animar_numero=True,
    )

    # Anel de 3 seções (Win/Loss/Breakeven), não só um arco verde de % —
    # bate com o gauge real do TradeZella (`image1.png`: "Trade win %"
    # mostra um anel segmentado vermelho/azul/verde com as 3 contagens
    # embaixo, não um arco único de porcentagem).
    win_pct = stats['taxa_acerto'] * 100
    card_win_pct = _card_header_tradezella(
        'Taxa de Acerto',
        html.Div([
            dmc.Text(f"{win_pct:.1f}%", size='xl', fw=700, className='tz-numero-animado'),
            dmc.Group([
                dmc.Text(str(stats['n_loss']), size='xs', c='red', fw=700),
                dmc.Text(str(stats['n_breakeven']), size='xs', c='dimmed', fw=700),
                dmc.Text(str(stats['n_win']), size='xs', c='green', fw=700),
            ], gap=6, mt=2),
        ]),
        componente=dmc.RingProgress(
            size=70, thickness=7, roundCaps=True,
            sections=[
                {'value': stats['n_loss'] / stats['num_operacoes'] * 100, 'color': 'red'},
                {'value': stats['n_breakeven'] / stats['num_operacoes'] * 100, 'color': 'gray'},
                {'value': stats['n_win'] / stats['num_operacoes'] * 100, 'color': 'green'},
            ] if stats['num_operacoes'] else [],
        ),
    )

    payoff = stats['payoff']
    media_ganho, media_perda_abs = stats['media_ganho_reais'], abs(stats['media_perda_reais'])
    total_win_loss = media_ganho + media_perda_abs
    pct_ganho = (media_ganho / total_win_loss * 100) if total_win_loss else 0
    # Valores em R$ embaixo da barra (verde à esquerda, vermelho à
    # direita) — bate com o card real do TradeZella, que mostra "$951" e
    # "-$352" sob os dois lados da barra, não só o número de payoff.
    barra_avg_win_loss = html.Div([
        dmc.ProgressRoot([
            dmc.ProgressSection(value=pct_ganho, color='green'),
            dmc.ProgressSection(value=100 - pct_ganho, color='red'),
        ], size='lg', radius='xl'),
        dmc.Group([
            dmc.Text(f"R$ +{media_ganho:.0f}", size='xs', c='green', fw=600),
            dmc.Text(f"R$ -{media_perda_abs:.0f}", size='xs', c='red', fw=600),
        ], justify='space-between', mt=4),
    ], style={'width': '120px'}) if total_win_loss else None
    card_avg_win_loss = _card_header_tradezella(
        'Ganho/Perda Médio', f"{payoff:.2f}" if payoff is not None else '—',
        componente=barra_avg_win_loss,
    )

    profit_factor = stats['profit_factor']
    pf_normalizado = min(profit_factor or 0, 3.0) / 3.0 * 100
    card_profit_factor = _card_header_tradezella(
        'Fator de Lucro', f"{profit_factor:.2f}" if profit_factor is not None else '—',
        componente=dmc.RingProgress(
            size=70, thickness=7, roundCaps=True,
            sections=[{'value': pf_normalizado, 'color': 'green' if (profit_factor or 0) >= 1 else 'red'}],
        ),
    )

    streak = dt.sequencia_atual()
    if streak['tipo'] is None:
        streak_str, cor_streak = '—', None
        badge_streak = None
    else:
        cor_streak = 'green' if streak['tipo'] == 'win' else 'red'
        rotulo_trades = 'trade' if streak['trades'] == 1 else 'trades'
        rotulo_dias = 'dia' if streak['dias'] == 1 else 'dias'
        streak_str = f"{streak['trades']} {rotulo_trades} ({streak['dias']} {rotulo_dias})"
        badge_streak = dmc.ThemeIcon(streak['tipo'] == 'win' and '🔥' or '❄',
                                       color=cor_streak, variant='light', radius='xl', size='lg')
    card_streak = _card_header_tradezella(
        'Sequência Atual',
        html.Div([badge_streak, html.Span(streak_str, style={'marginLeft': '8px'})],
                  style={'display': 'flex', 'alignItems': 'center'}) if badge_streak else streak_str,
        cor_valor=cor_streak,
    )

    return dmc.SimpleGrid(
        [card_net_pnl, card_win_pct, card_avg_win_loss, card_profit_factor, card_streak],
        cols={'base': 1, 'sm': 2, 'lg': 5}, spacing='md', mb='md',
    )


def _card_estatistica(titulo: str, valor: str, cor=None) -> dmc.Paper:
    """Card de detalhe (grupos Quantidade/Taxas/Valores) — `dmc.Paper`
    real (item F, Fase 2.6) em vez de `html.Div` com fundo pintado."""
    return dmc.Paper([
        dmc.Text(titulo, size='xs', c='dimmed'),
        dmc.Text(valor, size='md', fw=700, **({'c': cor} if cor else {})),
    ], radius='md', p='sm', withBorder=True, style={'minWidth': '120px'})


def _bloco_breakdown_diario(titulo: str, dados: dict) -> html.Div:
    linhas = [
        html.Div([
            html.Span(chave.replace('_', ' ').title(), style={'flex': '1', 'fontSize': '12px'}),
            html.Span(str(v['n']), style={'width': '34px', 'textAlign': 'right', 'fontSize': '12px',
                                           'fontFamily': FONTS['body']}),
            html.Span(f"R$ {v['saldo']:+.0f}", style={
                'width': '90px', 'textAlign': 'right', 'fontSize': '12px', 'fontFamily': FONTS['body'],
                'color': C['green'] if v['saldo'] > 0 else (C['red'] if v['saldo'] < 0 else C['text2']),
            }),
        ], style={'display': 'flex', 'padding': '4px 0', 'borderBottom': f'1px solid {C["border"]}'})
        for chave, v in dados.items()
    ] or [html.Div('Sem dados.', style={'fontSize': '12px', 'color': C['text2']})]
    return _bloco_tradezella(titulo, html.Div(linhas))


# ---------------------------------------------------------------------------
# Análise de Estratégia (2026-07-30, "upgrade diario.docx") — ver
# dt.analise_estrategia pro dado. Bloco ALL-TIME (mesmo recorte de
# "Confiabilidade por Estratégia", não reage ao seletor de mês/ano do
# calendário) — junta Por Ativo/Janela/Sala/Tags (que antes viviam soltos
# em `_resto_periodo_estatisticas`, reagindo ao período) com "melhor
# estratégia por X" e o destaque de Vencedora/Perdedora.
# ---------------------------------------------------------------------------

_ROTULO_DIRECAO_OPERACAO = {'long': 'Compra', 'short': 'Venda'}


def _tabela_melhor_por(titulo: str, dados: dict, rotulos_grupo: dict | None = None) -> html.Div:
    """'Melhor estratégia por X' (janela/direção/ativo/categoria) — ver
    `dt.analise_estrategia`. Cada grupo só aparece se alguma estratégia
    atingiu `dt.ANALISE_ESTRATEGIA_MIN_AMOSTRA` NAQUELE recorte; texto
    explícito quando não há nenhum grupo qualificado, em vez de bloco vazio
    sem explicação."""
    nomes = _mapa_nomes_estrategias()
    rotulos_grupo = rotulos_grupo or {}
    if not dados:
        linhas = [html.Div(f'Amostra insuficiente (mínimo {dt.ANALISE_ESTRATEGIA_MIN_AMOSTRA} trades por grupo).',
                           style={'fontSize': '12px', 'color': C['text2']})]
    else:
        linhas = []
        for chave, info in sorted(dados.items(), key=lambda kv: -kv[1]['saldo']):
            rotulo_chave = rotulos_grupo.get(chave, str(chave).replace('_', ' ').title())
            nome_estrategia = nomes.get(info['estrategia'], str(info['estrategia']).replace('_', ' ').title())
            saldo = info['saldo']
            cor = C['green'] if saldo > 0 else (C['red'] if saldo < 0 else C['text2'])
            linhas.append(html.Div([
                html.Span(rotulo_chave, style={'flex': '1', 'fontSize': '12px', 'color': C['text2']}),
                html.Span(nome_estrategia, style={'flex': '1.3', 'fontSize': '12px', 'fontWeight': '600'}),
                html.Span(f"R$ {saldo:+.0f} ({info['n']})", style={
                    'width': '110px', 'textAlign': 'right', 'fontSize': '12px',
                    'fontFamily': FONTS['body'], 'color': cor,
                }),
            ], style={'display': 'flex', 'padding': '4px 0', 'borderBottom': f'1px solid {C["border"]}'}))
    return _bloco_tradezella(titulo, html.Div(linhas))


def _card_estrategia_destaque(titulo: str, emoji: str, info: dict | None, cor_dmc: str) -> dmc.Card:
    """Estratégia Vencedora/Perdedora, destacada de forma ilustrada (pedido
    explícito do docx: "coloque em destaque de forma ilustrativa — por
    exemplo, um troféu ou medalha"). `dmc.Card` + `dmc.ThemeIcon` real
    (item F, Fase 2.6) — `cor_dmc` é um nome de cor Mantine ('green'/
    'red'), não mais a string CSS var `C[...]` usada antes."""
    nomes = _mapa_nomes_estrategias()
    if info is None:
        conteudo = dmc.Text(f'Amostra insuficiente ainda (mínimo {dt.ANALISE_ESTRATEGIA_MIN_AMOSTRA} trades).',
                             size='sm', c='dimmed')
    else:
        nome = nomes.get(info['estrategia'], str(info['estrategia']).replace('_', ' ').title())
        saldo = info['saldo']
        cor_saldo = 'green' if saldo > 0 else ('red' if saldo < 0 else 'dimmed')
        conteudo = html.Div([
            dmc.Text(nome, size='lg', fw=700),
            dmc.Text(f"R$ {saldo:+.2f} em {info['n']} operações", size='sm', c=cor_saldo, mt=4),
        ])
    return dmc.Card([
        dmc.Group([
            dmc.ThemeIcon(emoji, color=cor_dmc, variant='light', radius='xl', size='lg'),
            dmc.Text(titulo, size='sm', fw=600, c='dimmed'),
        ], mb='sm'),
        conteudo,
    ], withBorder=True, shadow='sm', radius='lg', p='lg', className='tz-card',
       style={'flex': '1', 'minWidth': '220px'})


def _conteudo_mentor_ia(cache: dict | None) -> html.Div:
    """Corpo do bloco Mentor de IA — texto cacheado (se houver) ou
    `dmc.Skeleton` (placeholder cinza pulsante — item F, Fase 2.6, real
    componente de loading em vez de só texto estático) quando ainda não
    foi gerado. Usado tanto no render inicial quanto como retorno da
    callback, pra manter os dois caminhos idênticos."""
    if not cache or not cache.get('texto'):
        return html.Div([
            dmc.Skeleton(height=12, width='90%', mb='xs'),
            dmc.Skeleton(height=12, width='75%', mb='xs'),
            dmc.Skeleton(height=12, width='60%'),
            dmc.Text('Ainda não gerada. Clique em "Gerar análise de hoje".', size='xs', c='dimmed', mt='sm'),
        ])
    gerado_em = cache.get('gerado_em', '')
    try:
        horario = datetime.fromisoformat(gerado_em).strftime('%d/%m %H:%M')
    except ValueError:
        horario = gerado_em
    return html.Div([
        dcc.Markdown(cache['texto'], style={'fontSize': '13px', 'lineHeight': '1.6'}),
        dmc.Text(f"Gerado em {horario} · {cache.get('modelo_usado', '')} · "
                 f"{cache.get('tokens_usados', 0)} tokens", size='xs', c='dimmed', mt='sm'),
    ])


def _bloco_mentor_ia() -> dmc.Card:
    """Mentor de IA — análise retrospectiva diária (item D, "upgrade
    diario.docx", 2026-07-30): curva de aprendizagem, evolução operacional/
    estratégica, próximos passos, tom DIRETO e objetivo. Reusa o MESMO hub
    Gemini/OpenRouter do chat (`ai_analyst._despachar_para_modelo`), nunca
    uma chamada própria. `dmc.Card`/`dmc.Button` reais (item F, Fase 2.6).

    Cadência de 1x/dia é só uma SUGESTÃO da UI (rótulo do botão muda pra
    "Gerar nova análise" depois da 1ª geração do dia) — nunca um bloqueio,
    porque o Jesué pode querer regenerar manualmente. O botão em si nunca é
    recriado por sua própria callback (evitaria resetar n_clicks) — só o
    `children` de `diario-mentor-ia-conteudo` e o rótulo do botão mudam."""
    cache = ai_analyst.carregar_analise_diaria()
    hoje = date.today().isoformat()
    ja_gerou_hoje = bool(cache) and cache.get('data') == hoje
    return dmc.Card([
        dmc.Group([
            dmc.ThemeIcon('🧭', color='yellow', variant='light', radius='xl', size='lg'),
            dmc.Text('Mentor de IA — Evolução Operacional', size='sm', fw=600),
            dmc.Button(
                'Gerar nova análise' if ja_gerou_hoje else 'Gerar análise de hoje',
                id='btn-gerar-mentor-ia', n_clicks=0, variant='light', color='gray',
                size='xs', radius='xl', ml='auto',
            ),
        ], mb='md'),
        dcc.Loading(
            id='loading-mentor-ia', type='dot', color=C['gold'],
            children=html.Div(_conteudo_mentor_ia(cache), id='diario-mentor-ia-conteudo'),
        ),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mt='md', className='tz-card')


# ---------------------------------------------------------------------------
# Anticovardia Score (item F, fase 3, `spec_redesenho_tradezella.md`) —
# renomeado do "Zella Score" do TradeZella. Radar hexagonal + barra
# horizontal de posição, mesmo padrão visual de `_radar_roda_vida`
# (Bem-Estar), reaproveitado aqui. Dado vem de
# `dt.calcular_anticovardia_score()` (Fase 1, já testado).
# ---------------------------------------------------------------------------

_ROTULO_EIXO_ANTICOVARDIA = {
    'win_rate': 'Taxa de Acerto',
    'profit_factor': 'Fator de Lucro',
    'avg_win_loss': 'Ganho/Perda Médio',
    'recovery_factor': 'Fator de Recuperação',
    'max_drawdown': 'Drawdown Máximo',
    'consistencia': 'Consistência',
}
# Ordem de exibição no radar — bate com a referência visual do TradeZella
# (`image1.png` do docx), não com a ordem de inserção do dict Python.
_ORDEM_EIXOS_ANTICOVARDIA = ['win_rate', 'profit_factor', 'avg_win_loss',
                             'recovery_factor', 'max_drawdown', 'consistencia']


def _grafico_radar_anticovardia(eixos: dict, tema: str) -> go.Figure:
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    categorias = [_ROTULO_EIXO_ANTICOVARDIA[e] for e in _ORDEM_EIXOS_ANTICOVARDIA]
    valores = [eixos.get(e, 0.0) for e in _ORDEM_EIXOS_ANTICOVARDIA]
    r_fechado = valores + [valores[0]]
    theta_fechado = categorias + [categorias[0]]

    # A 1ª tentativa de "gradiente" (N cópias do mesmo polígono encolhidas
    # em direção ao centro) foi um erro: como os 6 eixos quase nunca têm o
    # MESMO valor, cada cópia é uma versão IRREGULAR do polígono, e cópias
    # concêntricas de um polígono irregular não formam faixas suaves — as
    # arestas de cada camada cruzam as das outras, criando o efeito
    # "estrela"/crivo de linhas visto no comparativo direto com o
    # TradeZella real (2026-07-31: "não é fluido e clean... cheio de
    # pontos"). O TradeZella usa preenchimento SÓLIDO de tom único, sem
    # gradiente de verdade — Scatterpolar também não suporta `fillgradient`
    # nativo (só o Scatter cartesiano, ver `_grafico_evolucao_saldo`), então
    # replicar o "clean" real é exatamente isto: uma única camada.
    fig = go.Figure(go.Scatterpolar(
        r=r_fechado, theta=theta_fechado, fill='toself',
        fillcolor=_hex_para_rgba(p['gold'], 0.35), line=dict(color=p['gold'], width=2),
        showlegend=False,
    ))
    fig.update_layout(
        # Mesmo cuidado de `_radar_roda_vida`: `autorange=False` + `tickvals`
        # explícitos, senão o Plotly quebra a escala com variância baixa.
        # `gridshape='linear'` (feedback: "deixe o gráfico quadrado/
        # hexagonal ao invés de redondo") troca o grid circular padrão por
        # um polígono reto nos mesmos 6 eixos. `showticklabels=False`
        # (2026-07-31, "não tem números no meio" no real) esconde os
        # números 0/20/40/.../100 que poluíam o centro do gráfico — o grid
        # em si continua visível, só o rótulo numérico some.
        polar=dict(bgcolor='rgba(0,0,0,0)', gridshape='linear',
                   radialaxis=dict(visible=True, range=[0, 100], autorange=False,
                                    tickvals=[0, 20, 40, 60, 80, 100], showticklabels=False,
                                    gridcolor=p['border']),
                   angularaxis=dict(tickfont=dict(size=9, color=p['text']), gridcolor=p['border'])),
        paper_bgcolor='rgba(0,0,0,0)', showlegend=False, margin=dict(l=30, r=30, t=20, b=20),
        height=280, autosize=True,
    )
    return fig


def _barra_anticovardia_score(score: float, tema: str = 'light') -> html.Div:
    """Barra de gradiente 0-100 (vermelho→amarelo→verde) com marcador na
    posição do score — o TradeZella mostra o Zella Score como BARRA
    horizontal degradê, não anel (2026-07-31, feedback: "O Anticovardia
    Score é em barra, e não em círculo"). Sem componente DMC pronto pra
    isso (`RingProgress` é círculo; `ProgressRoot` não faz gradiente
    contínuo nem marcador de posição livre) — construído com CSS puro
    (`linear-gradient` + marcador posicionado via `left: calc(pct% - r)`),
    mesmo padrão de outros widgets sem equivalente pronto no projeto."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    pct = max(0.0, min(score, 100.0))
    cor_marcador = p['red'] if score < 33 else (p['yellow'] if score < 66 else p['green'])
    return html.Div([
        html.Div(html.Div(style={
            'position': 'absolute', 'left': f'calc({pct}% - 8px)', 'top': '-4px',
            'width': '16px', 'height': '16px', 'borderRadius': '50%',
            'background': cor_marcador, 'border': f"2px solid {p['panel']}",
            'boxShadow': '0 1px 3px rgba(0,0,0,0.35)',
        }), style={
            'position': 'relative', 'height': '8px', 'borderRadius': '4px',
            'background': f"linear-gradient(to right, {p['red']}, {p['yellow']}, {p['green']})",
        }),
        html.Div([
            html.Span(str(v), style={'fontSize': '10px', 'color': p['text2'], 'fontFamily': FONTS['body']})
            for v in (0, 20, 40, 60, 80, 100)
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'marginTop': '6px'}),
    ], style={'width': '100%', 'padding': '8px 4px 0 4px'})


def _bloco_anticovardia_score(tema: str = 'light') -> dmc.Card:
    """Anticovardia Score — `dmc.Card` real com o radar Plotly (sem
    equivalente DMC pronto, mantido) + a barra degradê
    `_barra_anticovardia_score` pro score final (item F — TradeZella usa
    barra, não anel/círculo, ver docstring da barra)."""
    resultado = dt.calcular_anticovardia_score()
    if resultado['score'] is None:
        return dmc.Card('Sem trades reais suficientes pra calcular o Anticovardia Score ainda.',
                         withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', w='100%',
                         style={'fontSize': '12px', 'color': C['text2']})
    score = resultado['score']
    return dmc.Card([
        dmc.Text('Anticovardia Score', size='sm', fw=600, mb='sm'),
        dcc.Graph(figure=_grafico_radar_anticovardia(resultado['eixos'], tema),
                  config={'displayModeBar': False, 'responsive': True}, style={'width': '100%'}),
        _barra_anticovardia_score(score, tema),
        dmc.Group([
            dmc.Text(f"Seu Anticovardia Score: {score:.1f}", size='sm', fw=700,
                      className='tz-numero-animado'),
        ], justify='flex-start', mt='sm'),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _bloco_trades_recentes(tema: str = 'light') -> dmc.Card:
    """Trades Recentes (item F — bloco condensado que faltava, 2026-07-31:
    "você não está mostrando o bloco de trades recentes no dashboard,
    como é no TradeZella"). Lista os últimos `dt.trades_recentes` trades
    reais num formato compacto (data/ativo/direção/estratégia/R-múltiplo/
    resultado) — prévia rápida, não substitui o Histórico (que tem
    filtro completo + edição)."""
    linhas = dt.trades_recentes(n=8)
    if not linhas:
        return dmc.Card('Nenhum trade real lançado ainda.', withBorder=True, shadow='sm',
                         radius='lg', p='lg', mb='md', w='100%', className='tz-card',
                         style={'fontSize': '12px', 'color': C['text2']})
    nomes_estrategias = _mapa_nomes_estrategias()

    def _celula(conteudo, largura, alinhar='left'):
        return html.Div(conteudo, style={'width': largura, 'textAlign': alinhar, 'flexShrink': '0'})

    cabecalho = dmc.Group([
        _celula(dmc.Text('Data', size='xs', c='dimmed'), '70px'),
        _celula(dmc.Text('Ativo', size='xs', c='dimmed'), '55px'),
        _celula(dmc.Text('Direção', size='xs', c='dimmed'), '65px'),
        dmc.Text('Estratégia', size='xs', c='dimmed', style={'flex': '1'}),
        _celula(dmc.Text('R-Múltiplo', size='xs', c='dimmed'), '80px', 'right'),
        _celula(dmc.Text('Resultado', size='xs', c='dimmed'), '90px', 'right'),
    ], wrap='nowrap', gap='xs', style={'padding': '0 4px 6px 4px'})

    linhas_ui = [cabecalho]
    for t in linhas:
        saldo = t['saldo_operacao_reais']
        cor_saldo = 'green' if saldo > 0 else ('red' if saldo < 0 else 'dimmed')
        r_mult = t['r_multiple']
        direcao_label = _ROTULO_DIRECAO_OPERACAO.get(t['tipo_operacao'], t['tipo_operacao'])
        cor_direcao = 'blue' if t['tipo_operacao'] == 'long' else 'grape'
        nome_estrategia = nomes_estrategias.get(t['estrategia'], t['estrategia']) if t['estrategia'] else '—'
        linhas_ui.append(dmc.Group([
            _celula(dmc.Text(t['data_op'], size='xs'), '70px'),
            _celula(dmc.Text(t['ativo'], size='xs', fw=600), '55px'),
            _celula(dmc.Badge(direcao_label, color=cor_direcao, variant='light', size='sm'), '65px'),
            dmc.Text(nome_estrategia, size='xs', c='dimmed', truncate=True, style={'flex': '1'}),
            _celula(dmc.Text(f"{r_mult:+.2f}R" if r_mult is not None else '—', size='xs'), '80px', 'right'),
            _celula(dmc.Text(f"R$ {saldo:+.2f}", size='xs', fw=700, c=cor_saldo), '90px', 'right'),
        ], wrap='nowrap', gap='xs', style={'padding': '6px 4px', 'borderTop': f"1px solid {C['border']}"}))

    return dmc.Card([
        dmc.Text('Trades Recentes', size='sm', fw=600, mb='sm'),
        html.Div(linhas_ui),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _grafico_duracao_performance(tema: str = 'light') -> go.Figure:
    """Scatter Duração × Resultado (item F, fase 5 — "Trade Duration
    Performance" do TradeZella). Um ponto por trade real com os dois
    timestamps preenchidos (`dt.dados_duracao_performance` — boa parte da
    base não tem, amostra parcial por natureza), cor por sinal do saldo
    (verde ganho/vermelho perda), eixo X em minutos, eixo Y em R$."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    linhas = dt.dados_duracao_performance()
    fig = go.Figure()
    if linhas:
        duracoes = [t['duracao_minutos'] for t in linhas]
        saldos = [t['saldo_operacao_reais'] for t in linhas]
        cores = [p['green'] if s > 0 else (p['red'] if s < 0 else p['text2']) for s in saldos]
        textos = [f"{t['ativo']} · {_ROTULO_DIRECAO_OPERACAO.get(t['tipo_operacao'], t['tipo_operacao'])} · {t['data_op']}"
                  for t in linhas]
        fig.add_trace(go.Scatter(
            x=duracoes, y=saldos, mode='markers',
            marker=dict(color=cores, size=8, opacity=0.75, line=dict(width=1, color=p['panel'])),
            text=textos, hovertemplate='%{text}<br>Duração: %{x:.0f} min<br>Resultado: R$ %{y:.2f}<extra></extra>',
        ))
        fig.add_hline(y=0, line_width=1, line_color=p['border'])
    fig.update_layout(
        paper_bgcolor=p['panel'], plot_bgcolor=p['panel'],
        font=dict(family=FONTS['body'], color=p['text2'], size=10),
        margin=dict(l=45, r=10, t=10, b=35), height=260,
        xaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5, title='Duração (minutos)'),
        yaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5, title='R$', zeroline=False),
    )
    return fig


def _bloco_o_que_fazer(descricao: str, resultado: dict) -> html.Div:
    """"O que fazer com isso" (2026-07-31, pedido: "coloque um feedback
    dinâmico e uma descrição... o nosso famoso 'o que fazer com isso'") —
    mesmo padrão já estabelecido em `dashboard.py::_balao_big_players` pros
    velocímetros do hero: descrição FIXA do que o gráfico mostra +
    veredito DINÂMICO calculado do dado real (`resultado`, de
    `dt.feedback_progress_tracker`/`dt.feedback_duracao_performance`), não
    um texto genérico igual sempre."""
    if not resultado.get('tem_dado'):
        corpo = html.Div(resultado.get('mensagem', 'Sem dado suficiente ainda.'),
                          style={'fontSize': '11px', 'color': C['text2'], 'lineHeight': '1.5'})
    else:
        corpo = html.Div([
            html.Span('O que fazer: ', style={'fontWeight': '700', 'color': C['text']}),
            html.Span(resultado['acao_texto'], style={'color': C.get(resultado.get('cor_acao'), C['text2'])}),
        ], style={'fontSize': '11px', 'lineHeight': '1.5'})
    return html.Div([
        html.Div(descricao, style={'fontSize': '11px', 'color': C['text2'], 'lineHeight': '1.5', 'marginBottom': '6px'}),
        corpo,
    ], style={'marginTop': '10px', 'paddingTop': '10px', 'borderTop': f"1px solid {C['border']}",
              'fontFamily': FONTS['body']})


def _bloco_duracao_performance(tema: str = 'light') -> dmc.Card:
    """Card "Desempenho por Duração da Operação" (item F, fase 5) — só
    aparece com conteúdo real; card informativo quando não há amostra
    (a maioria dos trades antigos não tem os 2 timestamps)."""
    linhas = dt.dados_duracao_performance()
    if not linhas:
        return dmc.Card(
            'Sem trades reais com horário de entrada/saída registrado ainda '
            '(precisa dos dois timestamps preenchidos).',
            withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', w='100%',
            className='tz-card', style={'fontSize': '12px', 'color': C['text2']})
    return dmc.Card([
        dmc.Text('Desempenho por Duração da Operação', size='sm', fw=600, mb='sm'),
        dcc.Graph(figure=_grafico_duracao_performance(tema),
                  config={'displayModeBar': False, 'responsive': True}, style={'width': '100%'}),
        _bloco_o_que_fazer(
            'Cada ponto é um trade real (só os com horário de entrada e saída '
            'registrados): duração em minutos no eixo X, resultado em R$ no eixo Y.',
            dt.feedback_duracao_performance(linhas)),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _bloco_analise_estrategia(stats_geral: dict, tema: str = 'light') -> html.Div:
    """Painel "Análise de Estratégia" — pedido do docx: "junte os blocos Por
    Ativo, Por Janela, Por Sala/Análise e Por Tags Setup/Erro num painel de
    dashboard estatístico, adicionando melhor estratégia por horário/
    direção/ativo/categoria; destaque a Estratégia Vencedora (troféu/
    medalha) e a Perdedora". O Mentor de IA (item D) também vive aqui dentro
    — pedido explícito do docx: "ainda dentro da Análise de Estratégias
    crie um bloco [do Mentor]". O Anticovardia Score (item F, fase 3,
    redesenho TradeZella) NÃO mora mais aqui dentro — migrou pra coluna
    esquerda do grid assimétrico em `_construir_diario_estatisticas` (Fase
    2.6: composição real de página, não pilha vertical única — ver
    `spec_redesenho_tradezella.md`)."""
    analise = dt.analise_estrategia()
    rotulos_janela = {k: _formatar_janela(k) for k in cfg.JANELAS_PREGAO}
    # `dmc.SimpleGrid` em vez de `html.Div(display:flex)` cru (2026-07-31,
    # feedback do Jesué: "não gosto desses espaços vazios, organize melhor
    # os elementos") — SimpleGrid divide a linha em células IGUAIS de
    # verdade; um `dmc.Card` dentro de flexbox não estica sozinho e deixava
    # metade da linha em branco quando o card vizinho era mais estreito.
    #
    # Detalhes (Melhor por X + Por Ativo/Janela/Sala/Tags) recolhidos por
    # padrão dentro de `dmc.Collapse` (2026-07-31, feedback: "deixe
    # Vencedora/Perdedora e Mentor de IA visíveis, o resto contraído,
    # clicando numa seta") — só Vencedora/Perdedora e Mentor de IA ficam
    # sempre visíveis. Botão estático (nunca recriado pela própria
    # callback, mesmo cuidado de `btn-diario-cal-config`): o próprio
    # `children` do botão guarda o estado (seta ▼/▲), não precisa de
    # `dcc.Store` extra.
    return html.Div([
        dmc.SimpleGrid([
            _card_estrategia_destaque('Estratégia Vencedora', '🏆', analise['vencedora'], 'green'),
            _card_estrategia_destaque('Estratégia Perdedora', '📉', analise['perdedora'], 'red'),
        ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
        dmc.Button('▼ Ver detalhes', id='btn-analise-estrategia-detalhes', n_clicks=0,
                   variant='subtle', color='gray', size='xs', mb='sm'),
        dmc.Collapse([
            dmc.SimpleGrid([
                _tabela_melhor_por('Melhor por Horário', analise['melhor_por_janela'], rotulos_janela),
                _tabela_melhor_por('Melhor por Direção', analise['melhor_por_direcao'], _ROTULO_DIRECAO_OPERACAO),
            ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
            dmc.SimpleGrid([
                _tabela_melhor_por('Melhor por Ativo', analise['melhor_por_ativo']),
                _tabela_melhor_por('Melhor por Categoria', analise['melhor_por_categoria'],
                                   dt.ROTULO_CATEGORIA_ESTRATEGIA),
            ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
            dmc.SimpleGrid([
                _bloco_breakdown_diario('Por Ativo', stats_geral['por_ativo']),
                _bloco_breakdown_diario('Por Janela', stats_geral['por_janela']),
            ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
            dmc.SimpleGrid([
                _bloco_breakdown_diario('Por Sala/Análise', stats_geral['por_sala']),
                _bloco_breakdown_diario('Por Tags Setup/Erro', stats_geral['por_tags_setup_erro']),
            ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
        ], id='colapso-analise-estrategia-detalhes', opened=False),
        _bloco_mentor_ia(),
    ])


def _hex_para_rgba(hex_cor: str, alpha: float) -> str:
    """Converte '#RRGGBB' pra 'rgba(r,g,b,a)' — Plotly só aceita opacidade
    via rgba(), não hex separado. Usado pelo fill translúcido dos gráficos
    abaixo, que precisam de PALETA_GAUGE (hex literal) por tema."""
    h = hex_cor.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'


def _segmentos_cor_por_sinal(xs: list, ys: list) -> list:
    """Quebra a série em segmentos verde (>=0)/vermelho (<0), inserindo um
    ponto interpolado exatamente no cruzamento de zero — é o que dá o
    efeito "linha muda de cor exatamente onde cruza o eixo" do equity
    curve real do TradeZella (2026-07-31, feedback "gráficos não
    mudaram": o gráfico de Evolução de Saldo era uma linha/preenchimento
    de UMA cor só, sem essa dualidade)."""
    xs_num = pd.to_datetime(xs).astype('int64').astype(float)
    segmentos = []
    atual_x, atual_y = [xs[0]], [ys[0]]
    sinal_atual = 'pos' if ys[0] >= 0 else 'neg'
    for i in range(1, len(ys)):
        sinal = 'pos' if ys[i] >= 0 else 'neg'
        if sinal != sinal_atual:
            y_prev, y_cur = ys[i - 1], ys[i]
            frac = y_prev / (y_prev - y_cur) if (y_prev - y_cur) != 0 else 0.0
            x_cruz_num = xs_num[i - 1] + frac * (xs_num[i] - xs_num[i - 1])
            x_cruz = pd.Timestamp(int(x_cruz_num))
            atual_x.append(x_cruz); atual_y.append(0.0)
            segmentos.append((sinal_atual, atual_x, atual_y))
            atual_x, atual_y = [x_cruz], [0.0]
            sinal_atual = sinal
        atual_x.append(xs[i]); atual_y.append(ys[i])
    segmentos.append((sinal_atual, atual_x, atual_y))
    return segmentos


def _grafico_evolucao_saldo(stats: dict, tema: str = 'light') -> go.Figure:
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    fig = go.Figure()
    if stats['evolucao_data']:
        cor_por_sinal = {'pos': p['green'], 'neg': p['red']}
        for sinal, xs_seg, ys_seg in _segmentos_cor_por_sinal(
                stats['evolucao_data'], stats['evolucao_saldo_acumulado']):
            cor = cor_por_sinal[sinal]
            extremo = max(ys_seg) if sinal == 'pos' else min(ys_seg)
            fig.add_trace(go.Scatter(
                x=xs_seg, y=ys_seg, mode='lines', line=dict(color=cor, width=2, shape='spline', smoothing=0.3),
                # Gradiente vertical de verdade (`fillgradient`, Plotly
                # nativo — 2026-07-31, feedback "aplique o gradiente em
                # todos os gráficos do dashboard"): transparente na linha
                # de zero, cor sólida no pico/vale do segmento — igual ao
                # "mountain chart" real do TradeZella (verde sólido no
                # topo esmaecendo pra branco perto do eixo).
                fill='tozeroy', fillcolor=_hex_para_rgba(cor, 0.15),
                fillgradient=dict(type='vertical', start=0, stop=extremo,
                                   colorscale=[[0, _hex_para_rgba(cor, 0.0)], [1, _hex_para_rgba(cor, 0.45)]]),
                showlegend=False, hoverinfo='x+y',
            ))
        fig.add_hline(y=0, line_width=1, line_color=p['border'])
    fig.update_layout(
        paper_bgcolor=p['panel'], plot_bgcolor=p['panel'],
        font=dict(family=FONTS['body'], color=p['text2'], size=10),
        margin=dict(l=45, r=10, t=10, b=30), height=200,
        xaxis=dict(showgrid=False, gridcolor=p['border']),
        yaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5, title='R$ acumulado', zeroline=False),
        hovermode='x unified',
    )
    return fig


_NOMES_MES = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
              'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
_DIAS_SEMANA = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']


# Stats configuráveis por dia no calendário (item F, fase 4,
# `spec_redesenho_tradezella.md`) — "Ticks" do TradeZella vira "Pontos"
# aqui (não operamos Forex). Conferido em detalhe contra `image2.png` do
# docx (anotada, mostra a lista real "Display stats" do produto): Daily
# P/L É UM TOGGLE de verdade lá (não é fixo como eu tinha assumido antes),
# então entra na lista igual aos outros — só por padrão vem marcado,
# porque esconder o R$ da célula sem querer seria uma surpresa ruim.
# Defaults batem com a referência: R Multiple/Daily P/L/Number of trades/
# Day Winrate ligados, Ticks (Pontos) desligado.
_OPCOES_STATS_CALENDARIO = [
    ('r_multiple', 'R-Multiple'),
    ('daily_pl', 'Daily P/L'),
    ('pontos', 'Pontos'),
    ('n_trades', 'Nº de Trades'),
    ('winrate', 'Day Winrate'),
]
_STATS_CALENDARIO_PADRAO = ['r_multiple', 'daily_pl', 'n_trades', 'winrate']


def _grade_calendario(por_dia: dict, detalhe_dias: dict, ano: int, mes: int,
                       stats_visiveis: list | None = None, tema: str = 'light') -> html.Div:
    """Grade mensal por dia — `html.Div` customizado (mesma razão de
    `diario_app_98.py::_grade_calendario_98`: sem precedente de Plotly
    heatmap no projeto). `detalhe_dias` (`dt.detalhe_dias_calendario`)
    fornece R-multiple médio/Nº trades/Day Winrate/Pontos por dia — ver
    `stats_visiveis` (lista de chaves de `_OPCOES_STATS_CALENDARIO`)
    controlando quais dessas linhas extras aparecem em cada célula. Uma
    coluna extra por linha de semana soma o saldo + conta os dias operados
    daquela semana (item F, fase 4: "total semanal na lateral").

    Cor de célula depende do TEMA (item F, redesenho visual TradeZella,
    conferido nas 3 referências reais do docx): no tema claro
    (`image1.png`) o TradeZella usa fundo PASTEL + texto escuro; no tema
    escuro (`image3.png`) usa fundo saturado + texto claro (já bem
    parecido com o que este calendário sempre teve). Por isso só o tema
    claro troca de paleta aqui — `TZ_PASTEL_CALENDARIO`."""
    stats_visiveis = stats_visiveis if stats_visiveis is not None else _STATS_CALENDARIO_PADRAO
    pastel = TZ_PASTEL_CALENDARIO.get(tema)
    cal = calendar.Calendar(firstweekday=6)  # domingo primeiro
    semanas = cal.monthdayscalendar(ano, mes)

    cabecalho = html.Div([
        html.Div(d, style={'flex': '1', 'textAlign': 'center', 'fontSize': '10px',
                            'color': C['text2'], 'fontFamily': FONTS['body']})
        for d in _DIAS_SEMANA
    ] + [html.Div('Semana', style={'width': '90px', 'textAlign': 'center', 'fontSize': '10px',
                                     'color': C['text2'], 'fontFamily': FONTS['body']})],
        style={'display': 'flex', 'marginBottom': '4px'})

    linhas = [cabecalho]
    for semana in semanas:
        celulas = []
        saldo_semana = 0.0
        dias_operados_semana = 0
        for dia in semana:
            if dia == 0:
                celulas.append(html.Div(style={'flex': '1', 'minHeight': '40px', 'margin': '2px'}))
                continue
            data_iso = f"{ano:04d}-{mes:02d}-{dia:02d}"
            info = por_dia.get(data_iso)
            detalhe = detalhe_dias.get(data_iso)
            if info is None:
                bg, cor_texto = C['bg'], C['text2']
            elif info['saldo'] > 0:
                bg = pastel['green_bg'] if pastel else C['green']
                cor_texto = pastel['texto'] if pastel else C['bg']
                saldo_semana += info['saldo']
                dias_operados_semana += 1
            elif info['saldo'] < 0:
                bg = pastel['red_bg'] if pastel else C['red']
                cor_texto = pastel['texto'] if pastel else C['bg']
                saldo_semana += info['saldo']
                dias_operados_semana += 1
            else:
                bg = pastel['neutro_bg'] if pastel else C['border']
                cor_texto = pastel['texto'] if pastel else C['text2']
                dias_operados_semana += 1
            estilo_celula = {'background': bg, 'borderRadius': TZ_RAIO_CELULA,
                              'flex': '1', 'minHeight': '40px', 'margin': '2px', 'padding': '4px',
                              'color': cor_texto, 'textAlign': 'center'}
            conteudo = [html.Div(str(dia), style={'fontSize': '11px'})]
            if info is not None and 'daily_pl' in stats_visiveis:
                conteudo.append(html.Div(f"R$ {info['saldo']:+.0f}",
                                          style={'fontSize': '10px', 'fontFamily': FONTS['body'], 'fontWeight': '700'}))
            if info is not None and detalhe is not None:
                estilo_extra = {'fontSize': '9px', 'fontFamily': FONTS['body'], 'opacity': '0.85'}
                if 'n_trades' in stats_visiveis:
                    conteudo.append(html.Div(f"{detalhe['n']} trade(s)", style=estilo_extra))
                # R-Multiple e Day Winrate ficam NA MESMA LINHA (conferido em
                # `image2.png`: "-1.14R, 33.33%" é um texto só, não 2 linhas).
                partes_linha = []
                if 'r_multiple' in stats_visiveis:
                    rm = detalhe['r_multiple_medio']
                    partes_linha.append(f"{rm:+.2f}R" if rm is not None else '—R')
                if 'winrate' in stats_visiveis:
                    partes_linha.append(f"{detalhe['taxa_acerto'] * 100:.1f}%")
                if partes_linha:
                    conteudo.append(html.Div(', '.join(partes_linha), style=estilo_extra))
                if 'pontos' in stats_visiveis:
                    conteudo.append(html.Div(f"{detalhe['pontos_total']:+.0f}pts", style=estilo_extra))
            if info is not None:
                # Clicável só quando há operação no dia — navega pro
                # Histórico já filtrado (ver `clicar_dia_calendario`).
                celulas.append(html.Div(conteudo, id={'type': 'diario-cal-dia', 'index': data_iso},
                                         n_clicks=0, style={**estilo_celula, 'cursor': 'pointer'}))
            else:
                celulas.append(html.Div(conteudo, style=estilo_celula))
        cor_semana = (C['green'] if saldo_semana > 0 else (C['red'] if saldo_semana < 0 else C['text2']))
        celulas.append(html.Div([
            html.Div(f"R$ {saldo_semana:+.0f}", style={'fontSize': '11px', 'fontWeight': '700',
                                                         'fontFamily': FONTS['body'], 'color': cor_semana}),
            html.Div(f"{dias_operados_semana} dia(s)", style={'fontSize': '9px', 'color': C['text2']}),
        ], style={'width': '90px', 'minHeight': '40px', 'margin': '2px', 'padding': '4px',
                  'textAlign': 'center', 'background': C['panel'], 'borderRadius': TZ_RAIO_CELULA,
                  'boxShadow': TZ_SOMBRA_CARD}))
        linhas.append(html.Div(celulas, style={'display': 'flex'}))
    return html.Div(linhas)


def _conteudo_config_calendario(stats_visiveis: list) -> html.Div:
    """Painel "Display stats" (item F, fase 4) — mesmo padrão de
    `_conteudo_checklist_pre_trade` (Checklist de verdade, marcável), só
    que este PERSISTE a seleção (grava em `store-calendario-stats-
    visiveis` via `atualizar_stats_visiveis_calendario`), ao contrário do
    checklist pré-trade que é ritual efêmero de propósito."""
    return html.Div([
        html.Div('Stats visíveis por dia', style={'fontSize': '12px', 'fontWeight': '600',
                                                    'fontFamily': FONTS['body'],
                                                    'color': C['text'], 'marginBottom': '8px'}),
        dcc.Checklist(
            id='diario-cal-config-itens',
            options=[{'label': rotulo, 'value': chave} for chave, rotulo in _OPCOES_STATS_CALENDARIO],
            value=stats_visiveis, labelStyle={'display': 'inline-block', 'marginRight': '16px', 'fontSize': '12px',
                                               'fontFamily': FONTS['body']},
            inputStyle={'marginRight': '6px'},
        ),
    ], style=_tz_estilo_card({'marginBottom': '8px', 'padding': '14px 16px'}))


def _grade_calendario_anual(por_mes: dict, ano: int, tema: str = 'light') -> html.Div:
    """Grade 4x3 (um trimestre por linha) com o saldo agregado de cada mês
    do ano — usada quando o toggle "Ano Corrente" está ligado. Mesma
    paleta pastel-no-claro/saturado-no-escuro de `_grade_calendario`."""
    pastel = TZ_PASTEL_CALENDARIO.get(tema)
    linhas = []
    for trimestre in range(4):
        celulas = []
        for i in range(3):
            mes_num = trimestre * 3 + i + 1
            info = por_mes.get(f"{ano:04d}-{mes_num:02d}")
            if info is None:
                bg, cor_texto = C['bg'], C['text2']
            elif info['saldo'] > 0:
                bg = pastel['green_bg'] if pastel else C['green']
                cor_texto = pastel['texto'] if pastel else C['bg']
            elif info['saldo'] < 0:
                bg = pastel['red_bg'] if pastel else C['red']
                cor_texto = pastel['texto'] if pastel else C['bg']
            else:
                bg = pastel['neutro_bg'] if pastel else C['border']
                cor_texto = pastel['texto'] if pastel else C['text2']
            celulas.append(html.Div([
                html.Div(_NOMES_MES[mes_num][:3], style={'fontSize': '11px', 'fontWeight': '700'}),
                html.Div(f"R$ {info['saldo']:+.0f}" if info else '—',
                          style={'fontSize': '10px', 'fontFamily': FONTS['body']}),
                html.Div(f"{info['n']} op." if info else '', style={'fontSize': '9px'}),
            ], style={'background': bg, 'borderRadius': TZ_RAIO_CELULA,
                       'flex': '1', 'minHeight': '52px', 'margin': '2px', 'padding': '6px',
                       'color': cor_texto, 'textAlign': 'center'}))
        linhas.append(html.Div(celulas, style={'display': 'flex'}))
    return html.Div(linhas)


def _linhas_grid_confiabilidade(scores: dict, saldos_por_estrategia: dict) -> list:
    """`rowData` do AG Grid "Confiabilidade por Estratégia" (item F, Fase
    2.6 — `dag.AgGrid` real substitui a `html.Table` montada linha a
    linha à mão: sort/filter/resize NATIVOS do componente, nada de
    callback própria pra ordenar coluna — `ordenar_confiabilidade_
    estrategia`/`store-ordenacao-estrategia` foram removidos, o grid faz
    isso sozinho no cliente).

    `tag` fica no dict (coluna oculta, `hide: True` no columnDef) pra
    identificar a linha clicada e buscar `_detalhe_estrategia_expandido`."""
    nomes = _mapa_nomes_estrategias()
    categorias = _mapa_categorias_estrategias()
    linhas = []
    for tag, v in scores.items():
        saldo = saldos_por_estrategia.get(tag, {}).get('saldo')
        if v['n'] == 0:
            score_label = 'sem dados'
        elif not v['amostra_suficiente']:
            score_label = f"amostra insuficiente (n={v['n']})"
        else:
            score_label = f"{v['score']:.0f}"
        linhas.append({
            'tag': tag,
            'nome': nomes.get(tag, tag.replace('_', ' ').title()),
            'categoria': categorias.get(tag, '—'),
            'n': v['n'],
            'saldo': saldo if saldo is not None else 0.0,
            'saldo_label': f"R$ {saldo:+.0f}" if saldo is not None else '—',
            'taxa_acerto': f"{v['taxa_acerto'] * 100:.1f}%" if v['taxa_acerto'] is not None else '—',
            'payoff': f"{v['payoff']:.2f}" if v['payoff'] is not None else '—',
            'profit_factor': f"{v['profit_factor']:.2f}" if v['profit_factor'] is not None else '—',
            'score': v['score'] if v['score'] is not None else -1,
            'score_label': score_label,
        })
    return linhas


# `cellStyle` como função JS (dash-ag-grid aceita `{"function": "<corpo JS>"}`
# em vez de um dict fixo — precisa ser JS porque a cor depende do VALOR da
# célula, calculado no navegador a cada render/sort/filter, não em Python).
_CELL_STYLE_SALDO = {'function': "params.value > 0 ? {'color': 'var(--green)', 'fontWeight': 700} "
                                  ": params.value < 0 ? {'color': 'var(--red)', 'fontWeight': 700} : null"}
_CELL_STYLE_SCORE = {'function': "params.value >= 70 ? {'color': 'var(--green)', 'fontWeight': 700} "
                                  ": params.value >= 40 ? {'color': 'var(--yellow)', 'fontWeight': 700} "
                                  ": params.value >= 0 ? {'color': 'var(--red)', 'fontWeight': 700} : null"}


def _tabela_score_confiabilidade(scores: dict, saldos_por_estrategia: dict | None = None) -> html.Div:
    """Bloco único "Confiabilidade por Estratégia" — fusão do antigo "Score
    de Confiabilidade" com o breakdown "Por Estratégia" (pedido do Jesué em
    2026-07-30: "junte o score de confiabilidade e a seção 'Por Estratégia'
    no mesmo bloco"), agora como `dag.AgGrid` real (item F, Fase 2.6):
    sort/filter/resize de coluna nativos do componente (clique no
    cabeçalho ordena, sem callback Python nenhuma pra isso), clique numa
    linha abre o detalhe abaixo do grid (`atualizar_detalhe_estrategia_grid`).

    Eram dois painéis lendo VOCABULÁRIOS diferentes — o score varria a lista
    de bootstrap congelada no código e o breakdown varria o cadastro
    editável — então mostravam nomes diferentes para os mesmos trades. Ver
    `dt._tags_para_pontuar` para a correção da causa raiz."""
    saldos_por_estrategia = saldos_por_estrategia or {}
    linhas = _linhas_grid_confiabilidade(scores, saldos_por_estrategia)

    grid = dag.AgGrid(
        id='grid-confiabilidade-estrategia',
        rowData=linhas,
        columnDefs=[
            {'field': 'tag', 'hide': True},
            {'field': 'nome', 'headerName': 'Estratégia', 'sortable': True, 'filter': True, 'flex': 2},
            {'field': 'categoria', 'headerName': 'Categoria', 'sortable': True, 'filter': True},
            {'field': 'n', 'headerName': 'N', 'sortable': True, 'filter': True, 'maxWidth': 90},
            {'field': 'saldo', 'headerName': 'Saldo', 'sortable': True, 'filter': True,
             'valueFormatter': {'function': "params.data.saldo_label"}, 'cellStyle': _CELL_STYLE_SALDO},
            {'field': 'taxa_acerto', 'headerName': 'Taxa Acerto', 'sortable': True, 'filter': True},
            {'field': 'payoff', 'headerName': 'Payoff', 'sortable': True, 'filter': True},
            {'field': 'profit_factor', 'headerName': 'Fator de Lucro', 'sortable': True, 'filter': True},
            {'field': 'score', 'headerName': 'Score', 'sortable': True, 'filter': True,
             'valueFormatter': {'function': "params.data.score_label"}, 'cellStyle': _CELL_STYLE_SCORE},
        ],
        defaultColDef={'resizable': True, 'sortable': True, 'flex': 1},
        dashGridOptions={'domLayout': 'autoHeight', 'animateRows': True},
        columnSize='responsiveSizeToFit',
        className='ag-theme-alpine',
        style={'width': '100%'},
    )
    return html.Div([
        dmc.Text('Só trades reais (o simulador não entra). Clique numa coluna pra ordenar/filtrar; '
                 'clique numa linha pra ver o detalhe do setup.', size='xs', c='dimmed', mb='sm'),
        grid,
        html.Div(id='diario-detalhe-estrategia-selecionada', style={'marginTop': '12px'}),
    ])


def _mini_recorte(titulo: str, dados: dict) -> html.Div:
    """Coluninha 'chave → n / saldo' usada dentro do detalhe expandido de
    uma estratégia (por direção, por janela, por ativo)."""
    if not dados:
        return html.Div()
    linhas = [
        html.Div([
            html.Span(str(k).replace('_', ' ').title(), style={'flex': '1', 'fontSize': '11px'}),
            html.Span(str(v['n']), style={'width': '28px', 'textAlign': 'right',
                                          'fontSize': '11px', 'fontFamily': FONTS['body']}),
            html.Span(f"R$ {v['saldo']:+.0f}", style={
                'width': '78px', 'textAlign': 'right', 'fontSize': '11px', 'fontFamily': FONTS['body'],
                'color': C['green'] if v['saldo'] > 0 else (C['red'] if v['saldo'] < 0 else C['text2']),
            }),
        ], style={'display': 'flex', 'padding': '3px 0'})
        for k, v in dados.items()
    ]
    return html.Div([
        html.Div(titulo, style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '3px',
                                'fontWeight': '600'}),
        html.Div(linhas),
    ], style={'flex': '1', 'minWidth': '190px'})


def _detalhe_estrategia_expandido(tag: str) -> html.Div:
    """Conteúdo da linha expandida — ver `dt.detalhe_estrategia` (a conta
    mora lá, compartilhada entre os 2 skins).

    Visual em "mini dashboard" (2026-07-30, feedback: "no detalhamento da
    estratégia ao clicar na seta, melhore a visualização, faça em formato
    de blocos como se fosse um mini dashboard") — antes era uma fileira só
    de 13 métricas soltas, sem agrupamento. Reaproveita a MESMA linguagem
    visual dos grupos rotulados criados pro topo de Estatísticas
    (`_grupo_cards_estatistica`) em vez de inventar um estilo novo."""
    d = dt.detalhe_estrategia(tag)
    if not d['n']:
        return html.Div('Sem trades reais neste setup.',
                        style={'padding': '10px', 'fontSize': '11px', 'color': C['text2']})

    def metrica(rotulo, valor, cor=None):
        return _card_estatistica(rotulo, valor, cor=cor)

    payoff = f"{d['payoff']:.2f}" if d['payoff'] is not None else '—'
    grupo_resultado = _grupo_cards_estatistica('Resultado', [
        metrica('Operações', str(d['n'])),
        metrica('Saldo', f"R$ {d['saldo_total']:+.2f}", C['green'] if d['saldo_total'] > 0 else C['red']),
        metrica('Wins', str(d['n_win']), C['green']),
        metrica('Losses', str(d['n_loss']), C['red']),
        metrica('Zero a zero', str(d['zero_a_zero'])),
        metrica('Expectativa', f"R$ {d['expectativa']:+.2f}", C['green'] if d['expectativa'] > 0 else C['red']),
    ])
    grupo_qualidade = _grupo_cards_estatistica('Qualidade', [
        metrica('Taxa de Acerto', f"{d['taxa_acerto'] * 100:.1f}%"),
        metrica('Payoff', payoff),
        metrica('Max Gain', f"R$ {d['max_gain']:+.2f}", C['green']),
        metrica('Max Loss', f"R$ {d['max_loss']:+.2f}", C['red']),
        metrica('Drawdown Máx.', f"R$ {d['drawdown_maximo_reais']:+.2f}", C['red']),
        metrica('Seq. Wins', str(d['maior_sequencia_win'])),
        metrica('Seq. Losses', str(d['maior_sequencia_loss'])),
    ])
    return html.Div([
        html.Div([grupo_resultado, grupo_qualidade],
                 style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginBottom': '12px'}),
        html.Div([
            _mini_recorte('Por direção', d['por_direcao']),
            _mini_recorte('Por janela', d['por_janela']),
            _mini_recorte('Por ativo', d['por_ativo']),
        ], style={'display': 'flex', 'gap': '18px', 'flexWrap': 'wrap', 'marginBottom': '10px'}),
        html.Div([
            html.Span(f"Período: {d['primeiro']} a {d['ultimo']}", style={'marginRight': '16px'}),
            html.Span(f"Melhor: #{d['melhor_trade']['id_trade']} "
                      f"({d['melhor_trade']['ativo']}, R$ {d['melhor_trade']['saldo']:+.2f})",
                      style={'marginRight': '16px', 'color': C['green']}),
            html.Span(f"Pior: #{d['pior_trade']['id_trade']} "
                      f"({d['pior_trade']['ativo']}, R$ {d['pior_trade']['saldo']:+.2f})",
                      style={'color': C['red']}),
        ], style={'fontSize': '11px', 'color': C['text2']}),
    ], style={'padding': '12px 14px', 'background': C['bg'],
              'borderLeft': f'2px solid {C["gold"]}', 'marginBottom': '6px'})


def _grupo_cards_estatistica(titulo: str, cards: list) -> html.Div:
    """Um grupo rotulado de cards (2026-07-30, feedback: "está meio bagunçado,
    aleatório. Agrupe as quantidades de operações, taxas e valores em blocos
    diferentes") — antes os 15 cards eram uma única fileira sem nenhuma
    ordem lógica visível (contagem, R$ e % intercalados). Agora cada grupo
    tem seu próprio rótulo e uma borda mais sutil que a dos cards
    individuais, só pra indicar "isto pertence junto"."""
    return html.Div([
        html.Div(titulo, style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['body'],
                                 'fontWeight': '600', 'marginBottom': '10px'}),
        html.Div(cards, style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
    ], style={'padding': '12px', 'borderRadius': TZ_RAIO_CARD, 'background': 'rgba(128,128,128,0.05)'})


def _cards_detalhe_estatisticas(stats: dict) -> html.Div:
    """Grupos Quantidade/Taxas/Valores (nome antigo: `_cards_estatisticas`) —
    demovidos pra bloco secundário "Detalhes" quando o header estilo
    TradeZella (`_cards_header_tradezella`) entrou (item F, fase 2,
    2026-07-30). Nenhum dado foi descartado: Wins/Losses/Breakeven/
    Sequências/Max Gain-Loss/Drawdown/Ganho-Perda Total continuam aqui,
    só não fazem mais parte do header principal."""
    payoff_str = f"{stats['payoff']:.2f}" if stats['payoff'] is not None else '—'
    grupo_quantidade = _grupo_cards_estatistica('Quantidade', [
        _card_estatistica('Operações', str(stats['num_operacoes'])),
        _card_estatistica('Wins', str(stats['n_win']), cor=C['green']),
        _card_estatistica('Losses', str(stats['n_loss']), cor=C['red']),
        _card_estatistica('Breakeven', str(stats['n_breakeven'])),
        _card_estatistica('Seq. Wins', str(stats['maior_sequencia_win'])),
        _card_estatistica('Seq. Losses', str(stats['maior_sequencia_loss'])),
    ])
    # Taxa de Acerto já aparece no card do header (`_cards_header_tradezella`)
    # — removida daqui pra não duplicar (2026-07-31, feedback do Jesué).
    # Fator de Recuperação (saldo total / |drawdown máximo|) — já existia
    # como eixo normalizado 0-100 do radar Anticovardia Score, mas nunca
    # aparecia no dashboard em valor cru (2026-07-31, feedback: "parece
    # ser um indicador novo... adicione-o também no dashboard").
    fator_recuperacao = stats['fator_recuperacao']
    fator_recuperacao_str = f"{fator_recuperacao:.2f}" if fator_recuperacao is not None else '—'
    grupo_taxas = _grupo_cards_estatistica('Taxas', [
        _card_estatistica('Payoff', payoff_str),
        _card_estatistica('Fator de Recuperação', fator_recuperacao_str,
                           cor=(C['green'] if (fator_recuperacao or 0) >= 1
                                else (C['red'] if fator_recuperacao is not None else None))),
    ])
    grupo_valores = _grupo_cards_estatistica('Valores (R$)', [
        _card_estatistica('Saldo Total', f"R$ {stats['saldo_total']:+.2f}",
                           cor=C['green'] if stats['saldo_total'] > 0 else C['red']),
        _card_estatistica('Expectativa', f"R$ {stats['expectativa']:+.2f}"),
        # Ganho Total/Perda Total (2026-07-30, feedback: "está faltando") —
        # soma de todos os vencedores/perdedores, ao lado de Max Gain/Max Loss
        # (melhor/pior trade ÚNICO) pra não confundir os dois.
        _card_estatistica('Ganho Total', f"R$ {stats['ganho_total']:+.2f}", cor=C['green']),
        _card_estatistica('Perda Total', f"R$ {stats['perda_total']:+.2f}", cor=C['red']),
        _card_estatistica('Max Gain', f"R$ {stats['max_gain']:+.2f}", cor=C['green']),
        _card_estatistica('Max Loss', f"R$ {stats['max_loss']:+.2f}", cor=C['red']),
        _card_estatistica('Drawdown Máx.', f"R$ {stats['drawdown_maximo_reais']:+.2f}",
                           cor=C['red'] if stats['drawdown_maximo_reais'] < 0 else None),
    ])
    return html.Div([grupo_quantidade, grupo_taxas, grupo_valores],
                     style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginBottom': '16px'})


_TOGGLE_LARGURA = 42
_TOGGLE_ALTURA = 22
_TOGGLE_BOLINHA = 18
_TOGGLE_MARGEM = 2


def _estilo_toggle_trilho(ativo: bool) -> dict:
    """Trilho do toggle switch 'Ano Inteiro' (feedback 2026-07-12: pediu o
    visual padrão de toggle switch de app — trilho arredondado + bolinha
    deslizante — no lugar do botão retangular antigo. Só no clássico: no
    SQ'98 esse visual destoaria do bisel Win98, mantido como botão."""
    return {
        'position': 'relative', 'width': f'{_TOGGLE_LARGURA}px', 'height': f'{_TOGGLE_ALTURA}px',
        'borderRadius': f'{_TOGGLE_ALTURA // 2}px', 'border': 'none', 'padding': '0',
        'cursor': 'pointer', 'flexShrink': '0',
        # C['border'] (#1C1C28) é quase invisível contra o fundo escuro pra
        # servir de trilho "desligado" — tom intermediário só pra esse uso.
        'background': C['green'] if ativo else '#3A3A48',
        'transition': 'background-color 0.2s ease',
    }


def _estilo_toggle_bolinha(ativo: bool) -> dict:
    deslocamento = _TOGGLE_LARGURA - _TOGGLE_BOLINHA - _TOGGLE_MARGEM if ativo else _TOGGLE_MARGEM
    return {
        'position': 'absolute', 'top': f'{_TOGGLE_MARGEM}px', 'left': f'{deslocamento}px',
        'width': f'{_TOGGLE_BOLINHA}px', 'height': f'{_TOGGLE_BOLINHA}px', 'borderRadius': '50%',
        'background': '#FFFFFF', 'boxShadow': '0 1px 3px rgba(0,0,0,0.4)',
        'transition': 'left 0.2s ease',
    }


def _cards_ou_vazio_estatisticas(stats: dict, tema: str = 'light') -> html.Div:
    if stats['num_operacoes']:
        return html.Div([
            _cards_header_tradezella(stats, tema),
            _bloco_tradezella('Detalhes', _cards_detalhe_estatisticas(stats)),
        ])
    return html.Div('Nenhum trade real neste período.', style={
        'padding': '16px', 'textAlign': 'center', 'color': C['text2'], 'fontSize': '12px',
    })


def _resto_periodo_estatisticas(stats: dict, tema: str = 'light') -> html.Div:
    """Evolução de saldo do período — a parte do painel de Estatísticas que
    pode ser livremente recriada a cada troca de mês/ano (nenhum desses
    componentes guarda estado de clique). O calendário em si (grade +
    botões) NÃO mora aqui — ver o comentário em `_construir_diario_
    estatisticas` pro porquê.

    Por Ativo/Janela/Sala/Tags Setup-Erro NÃO moram mais aqui (2026-07-30,
    "upgrade diario.docx": "junte os blocos... num painel de dashboard
    estatístico") — migraram pra dentro de "Análise de Estratégia"
    (`_bloco_analise_estrategia`), junto de "Confiabilidade por Estratégia".
    Antes eram sensíveis ao período (mês/ano selecionado); agora são
    ALL-TIME, consistentes com o resto daquele painel — só a Evolução de
    Saldo continua reagindo ao seletor de período."""
    return html.Div([
        _bloco_tradezella('Evolução de Saldo', dcc.Graph(
            figure=_grafico_evolucao_saldo(stats, tema), config={'displayModeBar': False, 'responsive': True},
            style={'width': '100%'},
        )),
    ])


def _bloco_status_badges() -> html.Div:
    """Reconciliação MT5 (agora em background) e resumo de Aderência aos Traçados
    (agora enriquece o Histórico) — ver diario_app_98.py::
    _bloco_status_badges_98 pro mesmo raciocínio."""
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
                style={'color': C['gold']})

    resumo_ader = dt.avaliar_aderencia_tracados()
    if resumo_ader.empty:
        badge_ader = html.Span(
            'Aderência aos Traçados: sem trades avaliáveis ainda (precisa de nível S/R referenciado + snapshot de mercado).',
            style={'color': C['text2']})
    else:
        total = len(resumo_ader)
        precisos = int((resumo_ader['classificacao'] == 'visto_preciso').sum())
        badge_ader = html.Span(f"Aderência aos Traçados: {precisos}/{total} trade(s) vistos com precisão pelo motor "
                                f"(ver coluna no Histórico).", style={'color': C['text']})

    return _bloco_tradezella('Status', html.Div([
        html.Div(badge_rec, style={'fontSize': '12px', 'marginBottom': '4px'}),
        html.Div(badge_ader, style={'fontSize': '12px'}),
    ]))


def _opacidade_progress_tracker(n: int) -> float:
    """Bucket de intensidade do Progress Tracker — 0 trades = célula vazia,
    escala até 1.0 (calibrado contra o limiar de overtrading já documentado
    no estudo de estratégias vencedoras: 5+ trades/dia é o ponto onde os
    dias começam a virar prejuízo)."""
    if n <= 0:
        return 0.0
    if n <= 2:
        return 0.25
    if n <= 4:
        return 0.5
    if n <= 6:
        return 0.75
    return 1.0


def _bloco_progress_tracker(por_dia: dict, tema: str = 'light') -> dmc.Card:
    """Progress Tracker (item F, fase 7, `spec_redesenho_tradezella.md`) —
    heatmap estilo GitHub contribution graph, últimas 52 semanas terminando
    hoje. Intensidade = Nº de trades operados naquele dia (decisão do
    Jesué, 2026-07-30: "analise o padrão do TradeZella e implemente da
    mesma forma" — usa `por_dia['n']`, que já existe em
    `calcular_estatisticas`, em vez de inventar uma leitura de disciplina/
    aderência sem regra formal cadastrada — mesma cautela já documentada em
    `calcular_score_confiabilidade`)."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    hoje = date.today()
    # Python: segunda=0..domingo=6 -> aqui queremos domingo=0..sábado=6
    # (primeiro dia da semana = domingo, mesma convenção do resto do Diário).
    indice_domingo = (hoje.weekday() + 1) % 7
    domingo_atual = hoje - timedelta(days=indice_domingo)
    n_semanas = 52
    domingo_inicio = domingo_atual - timedelta(weeks=n_semanas - 1)

    def _cor_celula(n: int) -> str:
        op = _opacidade_progress_tracker(n)
        return _hex_para_rgba(p['green'], op) if op > 0 else 'rgba(128,128,128,0.08)'

    colunas_semana, rotulos_mes = [], []
    mes_ja_rotulado = None
    for semana in range(n_semanas):
        domingo_semana = domingo_inicio + timedelta(weeks=semana)
        celulas = []
        rotulo_mes = ''
        for offset in range(7):
            dia = domingo_semana + timedelta(days=offset)
            if dia > hoje:
                celulas.append(html.Div(style={'width': '11px', 'height': '11px'}))
                continue
            n = por_dia.get(dia.isoformat(), {}).get('n', 0)
            celulas.append(html.Div(title=f"{dia.strftime('%d/%m/%Y')}: {n} trade(s)", style={
                'width': '11px', 'height': '11px', 'borderRadius': '2px', 'background': _cor_celula(n),
            }))
            if dia.day <= 7 and dia.month != mes_ja_rotulado:
                rotulo_mes = _NOMES_MES[dia.month][:3]
                mes_ja_rotulado = dia.month
        rotulos_mes.append(rotulo_mes)
        colunas_semana.append(html.Div(celulas, style={'display': 'flex', 'flexDirection': 'column', 'gap': '2px'}))

    linha_meses = html.Div([
        html.Div(rotulo, style={'width': '11px', 'fontSize': '9px', 'color': p['text2'], 'flexShrink': '0'})
        for rotulo in rotulos_mes
    ], style={'display': 'flex', 'gap': '2px', 'marginBottom': '4px'})

    legenda = html.Div([
        html.Span('Menos', style={'fontSize': '10px', 'color': p['text2'], 'marginRight': '6px'}),
        *[html.Div(style={'width': '11px', 'height': '11px', 'borderRadius': '2px', 'marginRight': '3px',
                           'background': (_hex_para_rgba(p['green'], op) if op > 0 else 'rgba(128,128,128,0.08)')})
          for op in (0.0, 0.25, 0.5, 0.75, 1.0)],
        html.Span('Mais', style={'fontSize': '10px', 'color': p['text2'], 'marginLeft': '3px'}),
    ], style={'display': 'flex', 'alignItems': 'center', 'marginTop': '8px'})

    return dmc.Card([
        dmc.Text('Progress Tracker', size='sm', fw=600, mb='sm'),
        html.Div([linha_meses, html.Div(colunas_semana, style={'display': 'flex', 'gap': '2px'})],
                  style={'overflowX': 'auto', 'paddingBottom': '4px'}),
        legenda,
        _bloco_o_que_fazer(
            'Cada célula é um dia — quanto mais escura, mais trades reais você operou nele. '
            'Últimas 52 semanas até hoje.',
            dt.feedback_progress_tracker(por_dia)),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _construir_diario_estatisticas(stats_geral: dict, scores: dict, tema: str = 'light') -> html.Div:
    """Shell montado 1x por ativação da aba — ver
    `diario_app_98.py::_construir_diario98_estatisticas` pro raciocínio
    completo (mesma estrutura, só a casca visual muda).

    Os botões de navegação do calendário (◀/▶/"Ver Ano Inteiro") moram AQUI,
    criados uma única vez, e NÃO dentro de `diario-periodo-conteudo`/
    `diario-resto-periodo-conteudo` (que `atualizar_periodo_estatisticas`
    reconstrói a cada troca de mês/ano/toggle). Causa raiz do bug relatado
    em 2026-07-12 (calendário pulando pra mês/ano aparentemente aleatório e
    o toggle "Ano Corrente" às vezes não fazendo nada): um `html.Button`
    recriado do zero nasce com `n_clicks=0`, e como esse prop é Input do
    callback de navegação, o Dash detectava essa queda pra 0 como "mais um
    clique" e disparava a navegação sozinha — cada clique real gerava 0-2
    cliques fantasmas extras, em direção imprevisível pro usuário. Com os
    botões fixos aqui e só `children`/`style` atualizados por callback
    (nunca o componente inteiro), `n_clicks` nunca mais reseta."""
    if stats_geral['num_operacoes'] == 0:
        return _placeholder_aba('Estatísticas (nenhum trade real lançado ainda)')

    hoje = date.today()
    mes_inicial = f"{hoje.year:04d}-{hoje.month:02d}"
    estilo_botao_nav = {
        'background': C['panel'], 'color': C['text'], 'border': 'none',
        'boxShadow': TZ_SOMBRA_CARD, 'borderRadius': '20px', 'padding': '4px 12px',
        'cursor': 'pointer', 'fontSize': '11px', 'fontFamily': FONTS['body'],
    }
    return html.Div([
        dcc.Store(id='diario-calendario-mes', data=mes_inicial),
        dcc.Store(id='diario-modo-ano', data=False),
        # Stats configuráveis por dia no calendário (item F, fase 4) —
        # persiste entre sessões (mesmo padrão de `store-tema`), já que é
        # preferência de exibição, não estado de navegação.
        dcc.Store(id='store-calendario-stats-visiveis', data=_STATS_CALENDARIO_PADRAO, storage_type='local'),

        html.Div(id='diario-cards-conteudo'),

        # Composição assimétrica real (item F, Fase 2.6 — `dmc.Grid`, não
        # mais uma pilha vertical de blocos full-width): coluna estreita à
        # esquerda (Evolução de Saldo + Anticovardia Score empilhados),
        # coluna larga à direita (Calendário) — mesma composição do
        # Dashboard real do TradeZella (`image1.png` do docx de
        # referência). O resto do conteúdo exclusivo do Super Quant
        # (Vencedora/Perdedora, Melhor por X, breakdowns, Confiabilidade,
        # Mentor de IA) continua existindo, só que ABAIXO deste grid —
        # decisão do Jesué: "mantenha o que já temos, adicione o que o
        # TradeZella agrega".
        dmc.Grid([
            dmc.GridCol([
                html.Div(id='diario-resto-periodo-conteudo'),
                _bloco_anticovardia_score(tema),
            ], span={'base': 12, 'md': 5}),
            dmc.GridCol(
                _bloco_tradezella('Calendário', html.Div([
                    html.Div([
                        html.Div([
                            html.Button('◀', id='btn-diario-cal-mes-prev', n_clicks=0, style=estilo_botao_nav),
                            html.Span(_NOMES_MES[hoje.month], id='diario-calendario-label-mes', style={
                                'margin': '0 8px', 'fontSize': '12px', 'fontFamily': FONTS['display'],
                                'fontWeight': '700',
                            }),
                            html.Button('▶', id='btn-diario-cal-mes-next', n_clicks=0, style=estilo_botao_nav),
                        ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '16px'}),
                        html.Div([
                            html.Button('◀', id='btn-diario-cal-ano-prev', n_clicks=0, style=estilo_botao_nav),
                            html.Span(str(hoje.year), id='diario-calendario-label-ano', style={
                                'margin': '0 8px', 'fontSize': '12px', 'fontFamily': FONTS['display'],
                                'fontWeight': '700',
                            }),
                            html.Button('▶', id='btn-diario-cal-ano-next', n_clicks=0, style=estilo_botao_nav),
                        ], style={'display': 'flex', 'alignItems': 'center'}),
                        html.Span('Ano Inteiro', style={
                            'marginLeft': 'auto', 'marginRight': '8px', 'fontSize': '11px', 'color': C['text2'],
                            'fontFamily': FONTS['display'],
                        }),
                        html.Button(html.Div(id='diario-modo-ano-bolinha', style=_estilo_toggle_bolinha(False)),
                                    id='btn-diario-modo-ano', n_clicks=0, style=_estilo_toggle_trilho(False)),
                        # Config de stats por dia (item F, fase 4) — botão
                        # estático (mesmo raciocínio de `btn-diario-
                        # checklist`: nunca recriado pela própria callback,
                        # só o `children` do painel abaixo muda) que
                        # abre/fecha um `dcc.Checklist`.
                        html.Button('⚙ Stats', id='btn-diario-cal-config', n_clicks=0, style={
                            **estilo_botao_nav, 'marginLeft': '8px',
                        }),
                    ], style={
                        # `flexWrap`: 4 mês + ano viraram grupos separados
                        # (feedback 2026-07-12) — a fileira ficou mais larga
                        # que o botão único de antes e, sem wrap, estourava a
                        # largura do iframe do Diário (dashboard.py embute em
                        # 100% de largura), forçando scroll horizontal que
                        # por sua vez empurrava a página inteira a precisar
                        # de scroll vertical também. Com wrap, em telas
                        # estreitas o toggle só desce pra 2ª linha.
                        'display': 'flex', 'flexWrap': 'wrap', 'alignItems': 'center',
                        'rowGap': '8px', 'marginBottom': '8px',
                    }),
                    html.Div(id='diario-cal-config-conteudo'),
                    html.Div(id='diario-calendario-grid'),
                ])),
                span={'base': 12, 'md': 7},
            ),
        ], gutter='md', mb='md'),

        # Progress Tracker (item F, fase 7) à esquerda + Trades Recentes
        # (item F) à direita, no mesmo split 5/7 do grid acima (2026-07-31,
        # feedback: "suba o Progress Tracker, coloque do lado esquerdo dos
        # Trades Recentes, tire esse espaço vazio abaixo do calendário") —
        # continua a coluna esquerda visualmente, em vez de deixar um vão
        # abaixo de Evolução de Saldo/Anticovardia Score enquanto o
        # Calendário (coluna direita, mais alto) ainda não terminou. Ambos
        # sempre ALL-TIME (não filtram por período do calendário acima),
        # mesma convenção de `stats_geral`/`scores` logo abaixo.
        dmc.Grid([
            dmc.GridCol(_bloco_progress_tracker(stats_geral['por_dia'], tema), span={'base': 12, 'md': 5}),
            dmc.GridCol(_bloco_trades_recentes(tema), span={'base': 12, 'md': 7}),
        ], gutter='md', mb='md'),

        # Trade Duration Performance (item F, fase 5, `spec_redesenho_
        # tradezella.md`) — independente do resto, entra a qualquer momento.
        _bloco_duracao_performance(tema),

        # `stats_geral` é o SEM filtro de período (ver a chamada em
        # `montar_sub_aba_diario`), igual ao `scores` — os dois lados da
        # tabela fundida precisam falar do mesmo recorte.
        #
        # "Análise de Estratégia" (2026-07-30) fica ACIMA de "Confiabilidade
        # por Estratégia", pedido explícito do docx — os dois são ALL-TIME,
        # mesmo recorte (stats_geral/scores computados 1x na entrada da aba).
        _bloco_tradezella('Análise de Estratégia', _bloco_analise_estrategia(stats_geral, tema)),
        _bloco_tradezella('Confiabilidade por Estratégia',
                          _tabela_score_confiabilidade(scores, stats_geral['por_estrategia'])),

        _bloco_status_badges(),
    ])


# --- Padrões comportamentais ----------------------------------------------------
# Revenge trading / tilt cascade — ver `diario_trade.detectar_padroes_
# comportamentais`. Relatório de revisão manual, mesmo espírito da
# Aderência aos Traçados: Jesué julga, o sistema não ajusta nada sozinho.

_COR_TIPO_PADRAO = {'revenge_trading': 'red', 'tilt_cascade': 'yellow'}


def _tabela_padroes(df: pd.DataFrame | None, ja_rodou: bool = False) -> html.Div:
    """`ja_rodou` (2026-07-21, fix de bug real): distingue "ainda não
    cliquei em Detectar padrões" de "cliquei e a detecção rodou, achou 0" —
    antes as duas situações mostravam o EXATO mesmo texto, parecendo que o
    clique não tinha feito nada mesmo quando o status ao lado do botão já
    dizia "Concluído — 0 padrões detectados"."""
    if df is None or df.empty:
        texto = ('Nenhum padrão comportamental encontrado no histórico atual.' if ja_rodou
                 else 'Nenhum resultado ainda — clique em "Detectar padrões".')
        return html.Div(texto, style={
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


# --- Estratégias (CRUD) ---------------------------------------------------------
# Ver diario_app_98.py pro mesmo raciocínio completo (mesma estrutura, só a
# casca visual muda) — substitui as listas fixas por cadastro editável.

def _estilo_btn_primario() -> dict:
    return {
        'background': C['green'], 'color': C['bg'], 'border': 'none', 'borderRadius': '6px',
        'padding': '7px 18px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
        'fontWeight': '700', 'fontSize': '12px',
    }


def _estilo_btn_pequeno() -> dict:
    return {
        'background': 'transparent', 'color': C['text2'], 'border': f'1px solid {C["border"]}',
        'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer', 'fontSize': '10px',
    }


def _botao_desativar(tipo_pattern: str, id_item: int) -> html.Button:
    return html.Button('Desativar', id={'type': tipo_pattern, 'index': int(id_item)}, n_clicks=0,
                        style=_estilo_btn_pequeno())


def _botao_editar(tipo_pattern: str, id_item: int) -> html.Button:
    return html.Button('Editar', id={'type': tipo_pattern, 'index': int(id_item)}, n_clicks=0,
                        style={**_estilo_btn_pequeno(), 'marginRight': '6px', 'color': C['gold'], 'borderColor': C['gold']})


def _tabela_estrategias(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma estratégia cadastrada.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', 'Categoria', 'Descrição', 'Checklist', '']])
    linhas = [html.Tr([
        _td_diario(linha['nome']),
        _td_diario(dt.ROTULO_CATEGORIA_ESTRATEGIA.get(linha.get('categoria'), '—')),
        _td_diario(linha['descricao'] or '—'),
        _td_diario(f"{len(_lista_segura(linha['checklist']))} item(ns)"),
        _td_diario(html.Div([
            _botao_editar('diario-estrategia-editar', linha['id']),
            _botao_desativar('diario-estrategia-desativar', linha['id']),
        ], style={'display': 'flex'})),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_rr_presets(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum preset R:R cadastrado.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', 'Alvo (pts)', 'Stop (pts)', 'Breakeven (pts)', '']])
    linhas = [html.Tr([
        _td_diario(linha['nome']),
        _td_diario(f"{linha['alvo_pontos']:.0f}"),
        _td_diario(f"{linha['stop_pontos']:.0f}"),
        _td_diario(f"{linha['breakeven_pontos']:.0f}" if pd.notna(linha['breakeven_pontos']) else '—'),
        _td_diario(_botao_desativar('diario-rr-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_tags(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma tag cadastrada.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', 'Tipo', '']])
    linhas = [html.Tr([
        _td_diario(linha['nome']),
        _td_diario(linha['tipo']),
        _td_diario(_botao_desativar('diario-tag-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_tags_emocionais(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma tag emocional cadastrada.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', '']])
    linhas = [html.Tr([
        _td_diario(linha['nome']),
        _td_diario(_botao_desativar('diario-tag-emocional-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario_estrategias() -> html.Div:
    campo = _campo_diario
    estilo_input = _estilo_input_diario
    return html.Div([
        _bloco_diario('Nova Estratégia', html.Div([
            html.Div(id='diario-estrategia-editando-aviso'),
            campo('Nome', dcc.Input(id='diario-nova-estrategia-nome', type='text', style=estilo_input())),
            campo('Descrição', dcc.Textarea(id='diario-nova-estrategia-desc', rows=2, style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            campo('Checklist operacional (1 item por linha, opcional)',
                  dcc.Textarea(id='diario-nova-estrategia-checklist', rows=3, style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            # Categoria (2026-07-30, "upgrade diario.docx"): característica
            # do SETUP em si, não do resultado — ver dt.CATEGORIA_ESTRATEGIA_
            # OPCOES pra definição de cada uma.
            campo('Categoria', dcc.Dropdown(
                id='diario-nova-estrategia-categoria',
                options=[{'label': dt.ROTULO_CATEGORIA_ESTRATEGIA[c], 'value': c}
                         for c in dt.CATEGORIA_ESTRATEGIA_OPCOES],
                placeholder='(opcional)', className='classico-dropdown', style={'width': '220px'})),
            html.Button('Adicionar Estratégia', id='btn-diario-add-estrategia', n_clicks=0, style=_estilo_btn_primario()),
            html.Button('Cancelar Edição', id='btn-diario-cancelar-edicao-estrategia', n_clicks=0,
                        style={**_estilo_btn_pequeno(), 'marginLeft': '8px', 'display': 'none'}),
            html.Span(id='diario-estrategia-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
            dcc.Store(id='store-diario-estrategia-editando', data=None),
        ])),
        _bloco_diario('Estratégias Cadastradas', html.Div(_tabela_estrategias(dt.listar_estrategias()),
                                                             id='diario-tabela-estrategias')),

        _bloco_diario('Novo Preset R:R', html.Div([
            html.Div([
                campo('Nome', dcc.Input(id='diario-novo-rr-nome', type='text', style=estilo_input({'width': '180px'}))),
                campo('Alvo (pts)', dcc.Input(id='diario-novo-rr-alvo', type='text', inputMode='decimal',
                                               style=estilo_input({'width': '110px'}))),
                campo('Stop (pts)', dcc.Input(id='diario-novo-rr-stop', type='text', inputMode='decimal',
                                               style=estilo_input({'width': '110px'}))),
                campo('Breakeven (pts, opcional)', dcc.Input(id='diario-novo-rr-be', type='text', inputMode='decimal',
                                                              style=estilo_input({'width': '150px'}))),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
            html.Button('Adicionar Preset', id='btn-diario-add-rr', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '8px'}),
            html.Span(id='diario-rr-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),
        _bloco_diario('Presets R:R Cadastrados', html.Div(_tabela_rr_presets(dt.listar_rr_presets()),
                                                             id='diario-tabela-rr')),

        _bloco_diario('Nova Tag Setup/Erro', html.Div([
            html.Div([
                campo('Nome', dcc.Input(id='diario-nova-tag-nome', type='text', style=estilo_input({'width': '220px'}))),
                campo('Tipo', dcc.Dropdown(id='diario-nova-tag-tipo',
                                            options=[{'label': v.title(), 'value': v} for v in ('setup', 'erro', 'ambos')],
                                            value='ambos', clearable=False, className='classico-dropdown', style={'width': '140px'})),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
            html.Button('Adicionar Tag', id='btn-diario-add-tag', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '8px'}),
            html.Span(id='diario-tag-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),
        _bloco_diario('Tags Cadastradas', html.Div(_tabela_tags(dt.listar_tags_setup_erro()),
                                                      id='diario-tabela-tags')),

        _bloco_diario('Nova Tag Emocional (Print Emocional)', html.Div([
            campo('Nome', dcc.Input(id='diario-nova-tag-emocional-nome', type='text', style=estilo_input({'width': '220px'}))),
            html.Button('Adicionar Tag', id='btn-diario-add-tag-emocional', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '8px'}),
            html.Span(id='diario-tag-emocional-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),
        _bloco_diario('Tags Emocionais Cadastradas', html.Div(_tabela_tags_emocionais(dt.listar_tags_emocionais()),
                                                                 id='diario-tabela-tags-emocionais')),
    ])


# =============================================================================
# BEM-ESTAR: check-in pré-operacional, Escala Hawkins, Roda da Vida (aba
# nova, feedback diário.txt 2026-07-11, Fase E) — ver diario_app_98.py pro
# mesmo raciocínio completo (primeira tela do Diário organizada por DATA em
# vez de por trade/cadastro editável).
# =============================================================================

# Funil vertical (2026-07-15, pedido do Jesué — substitui o velocímetro/
# gauge circular anterior por um formato mais fiel ao pôster original da
# Escala Hawkins: funil estreitando da consciência expandida, no topo, à
# contraída, na base, com os 17 níveis listados ao lado). CSS puro (sem
# Plotly) — `clip-path` recorta um div em forma de funil, o preenchimento é
# um `linear-gradient` cujos stops usam a MESMA escala logarítmica de
# `dt.posicao_normalizada_hawkins` que posiciona o marcador do nível atual,
# então cor de fundo e rótulos ficam sempre alinhados entre si.
_ALTURA_FUNIL_HAWKINS = 440
_LARGURA_FUNIL_HAWKINS = 170


def _funil_hawkins(valor: float, normalizado: float, nivel_nome: str, cor_nivel: str) -> html.Div:
    """Ver diario_app_98.py::_funil_hawkins_98 pro mesmo raciocínio
    (duplicado aqui porque este processo isolado nunca importa de
    dashboard.py). `normalizado` já vem 0-1 de `dt.calcular_nivel_hawkins`
    (mesma escala usada pra plotar `dt.NIVEIS_HAWKINS` ao lado)."""
    stops = ', '.join(
        f'{cor} {dt.posicao_normalizada_hawkins(v) * 100:.1f}%'
        for v, _, cor in dt.NIVEIS_HAWKINS
    )
    pos_marcador = min(max((1 - normalizado) * 100, 2), 98)  # 0%=topo/expandido, 100%=base/contraído, com folga nas bordas

    # Espaçamento UNIFORME (17 linhas iguais), não proporcional à escala log
    # real do gradiente: valores altos como 500/540/600/700 ficam a poucos
    # % de distância na escala log de verdade — texto legível não cabe nesse
    # espaço. Mesmo o pôster original da Escala Hawkins espaça a legenda de
    # forma uniforme; só o degradê de cor do funil segue a escala log real
    # (via `dt.posicao_normalizada_hawkins` nos `stops`, acima).
    rotulos = []
    for v, nome, cor in reversed(dt.NIVEIS_HAWKINS):
        eh_coragem = (v == dt.LIMIAR_HAWKINS_CORAGEM)
        rotulos.append(html.Div([
            html.Span(nome, style={
                'fontFamily': FONTS['display'], 'color': C['gold'] if eh_coragem else C['text2'],
                'fontWeight': '700' if eh_coragem else '400',
            }),
            html.Span(style={'display': 'inline-block', 'width': '10px', 'height': '0',
                              'borderBottom': f'1px dotted {C["border"]}', 'margin': '0 5px', 'verticalAlign': 'middle'}),
            html.Span(str(v), style={'fontFamily': FONTS['mono'], 'color': cor}),
        ], style={
            'flex': '1', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'flex-end',
            'whiteSpace': 'nowrap', 'fontSize': '10px', 'lineHeight': '1',
        }))

    marcador = html.Div([
        html.Div(style={
            'position': 'absolute', 'left': f'-{_LARGURA_FUNIL_HAWKINS + 40}px', 'right': '0',
            'top': '0', 'height': '2px', 'background': cor_nivel,
            'boxShadow': f'0 0 8px {cor_nivel}',
        }),
        html.Span('◀ você está aqui', style={
            'position': 'absolute', 'left': '100%', 'top': '-8px', 'marginLeft': '8px',
            'fontSize': '10px', 'fontFamily': FONTS['display'], 'color': cor_nivel, 'whiteSpace': 'nowrap',
        }),
    ], style={'position': 'absolute', 'top': f'{pos_marcador:.2f}%', 'left': '0', 'right': '0', 'zIndex': '2'})

    funil = html.Div(style={
        'position': 'absolute', 'left': '0', 'top': '0',
        'width': f'{_LARGURA_FUNIL_HAWKINS}px', 'height': f'{_ALTURA_FUNIL_HAWKINS}px',
        'background': f'linear-gradient(to top, {stops})',
        'clipPath': 'polygon(0% 4%, 6% 0%, 94% 0%, 100% 4%, 100% 72%, 60% 100%, 40% 100%, 0% 72%)',
        'opacity': '0.92',
    })

    cabecalho = html.Div([
        html.Span(f'{nivel_nome}', style={'fontFamily': FONTS['mono'], 'fontSize': '15px',
                                           'fontWeight': '700', 'color': cor_nivel}),
        html.Span(f' · {valor:.0f}', style={'fontFamily': FONTS['mono'], 'fontSize': '12px', 'color': C['text2']}),
    ], style={'marginBottom': '10px'})

    return html.Div([
        cabecalho,
        html.Div([
            html.Div([funil, marcador], style={
                'position': 'relative', 'width': f'{_LARGURA_FUNIL_HAWKINS}px', 'height': f'{_ALTURA_FUNIL_HAWKINS}px',
                'marginLeft': f'{_LARGURA_FUNIL_HAWKINS + 40}px',
            }),
            html.Div(rotulos, style={
                'position': 'absolute', 'left': '0', 'top': '0',
                'width': f'{_LARGURA_FUNIL_HAWKINS + 20}px', 'height': f'{_ALTURA_FUNIL_HAWKINS}px',
                'display': 'flex', 'flexDirection': 'column',
            }),
        ], style={'position': 'relative', 'height': f'{_ALTURA_FUNIL_HAWKINS}px'}),
        html.Div('Escala Hawkins (proxy, não calibrado) — abaixo de Coragem (200) a teoria original considera que a pessoa "perde poder"',
                  style={'fontSize': '10px', 'color': C['text2'], 'marginTop': '10px', 'maxWidth': '420px'}),
    ])


def _bloco_gauge_hawkins(data_checkin: date):
    """Nunca inventa valor: sem check-in do dia (ou sem nenhuma nota
    preenchida), mostra um aviso em vez do funil."""
    checkin = dt.obter_checkin_dia(data_checkin)
    if not checkin:
        return html.Div('Sem check-in salvo pra esse dia ainda — preencha e salve pra ver o nível.',
                         style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    medias = dt.calcular_medias_emocionais_dia(data_checkin)
    resultado = dt.calcular_nivel_hawkins(checkin, medias)
    if not resultado:
        return html.Div('Check-in salvo, mas sem nenhuma nota preenchida ainda.',
                         style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    return _funil_hawkins(resultado['valor'], resultado['normalizado'], resultado['nivel_nome'], resultado['cor'])


def _radar_roda_vida(registro: dict | None, tema: str = 'light') -> go.Figure:
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    categorias = [dt.NOMES_AREAS_RODA_VIDA[a] for a in dt.AREAS_RODA_VIDA]
    valores = [float(registro.get(a) or 0) for a in dt.AREAS_RODA_VIDA] if registro else [0] * len(dt.AREAS_RODA_VIDA)
    fig = go.Figure(go.Scatterpolar(r=valores + [valores[0]], theta=categorias + [categorias[0]],
                                     fill='toself', line=dict(color=p['gold']), fillcolor=p['gold'], opacity=0.5))
    fig.update_layout(
        # `range` sozinho não é suficiente: com todas as áreas em 0 (antes do
        # 1º registro salvo), o auto-cálculo de ticks do Plotly quebra (variância
        # zero nos dados) e desenha uma escala minúscula tipo "0.7378" em vez de
        # 0-10 — `autorange=False` + `tickvals` explícitos travam a escala real.
        polar=dict(bgcolor='rgba(0,0,0,0)',
                   radialaxis=dict(visible=True, range=[0, 10], autorange=False, tickvals=[0, 2, 4, 6, 8, 10],
                                    tickfont=dict(size=8, color=p['text2']), gridcolor=p['border']),
                   angularaxis=dict(tickfont=dict(size=9, color=p['text']), gridcolor=p['border'])),
        paper_bgcolor='rgba(0,0,0,0)', showlegend=False, margin=dict(l=30, r=30, t=20, b=20),
        height=320, autosize=True,
    )
    return fig


def _tabela_checkins(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum check-in registrado ainda.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Data', 'Sono', 'Café', 'Duração Sono', 'Nível Hawkins']])
    linhas = []
    for _, linha in df.head(14).iterrows():
        checkin = linha.to_dict()
        resultado = dt.calcular_nivel_hawkins(checkin, dt.calcular_medias_emocionais_dia(checkin['data']))
        duracao = checkin.get('sono_duracao_horas')
        linhas.append(html.Tr([
            _td_diario(str(checkin['data'])),
            _td_diario(_estrelas_texto(checkin.get('sono_qualidade'))),
            _td_diario(_estrelas_texto(checkin.get('cafe_qualidade'))),
            _td_diario(f"{duracao:.1f}h" if duracao is not None and not (isinstance(duracao, float) and pd.isna(duracao)) else '—'),
            _td_diario(resultado['nivel_nome'] if resultado else '—'),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_roda_vida(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum registro de Roda da Vida ainda.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Data', 'Média Geral']])
    linhas = []
    for _, linha in df.head(10).iterrows():
        valores = [linha[a] for a in dt.AREAS_RODA_VIDA if pd.notna(linha[a])]
        media = sum(valores) / len(valores) if valores else None
        linhas.append(html.Tr([
            _td_diario(str(linha['data'])),
            _td_diario(f"{media:.1f}" if media is not None else '—'),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario_bem_estar() -> html.Div:
    campo = _campo_diario
    estilo_input = _estilo_input_diario
    hoje = date.today().isoformat()
    checkin_hoje = dt.obter_checkin_dia(hoje) or {}
    ultimo_roda = dt.obter_ultimo_registro_roda_vida()
    # Ver diario_app_98.py::_construir_diario98_bem_estar pro mesmo
    # raciocínio (radar inicial sincronizado com o valor-padrão dos
    # sliders, não em zero).
    valores_iniciais_roda = ultimo_roda or {area: 5 for area in dt.AREAS_RODA_VIDA}

    def _valor(chave, padrao=None):
        v = checkin_hoje.get(chave)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return padrao
        return v

    return html.Div([
        _bloco_diario('Check-in do Dia', html.Div([
            campo('Data', dcc.Input(id='diario-checkin-data', type='date', value=hoje, style=estilo_input())),
            html.Div([
                campo('Qualidade do Sono', _estrela_rating('diario-checkin-sono', _num_estrela_ou_padrao(_valor('sono_qualidade')))),
                campo('Qualidade do Café', _estrela_rating('diario-checkin-cafe', _num_estrela_ou_padrao(_valor('cafe_qualidade')))),
            ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(160px, 1fr))', 'gap': '6px 16px'}),
            html.Div([
                campo('Dormiu (HH:MM)', dcc.Input(id='diario-checkin-hora-dormir', type='text', placeholder='23:00',
                                                    value=_valor('sono_hora_dormir', ''), style=estilo_input({'width': '100px'}))),
                campo('Acordou (HH:MM)', dcc.Input(id='diario-checkin-hora-acordar', type='text', placeholder='07:00',
                                                     value=_valor('sono_hora_acordar', ''), style=estilo_input({'width': '100px'}))),
                campo('Autocuidado (min)', dcc.Input(id='diario-checkin-autocuidado', type='number', className='no-spinner',
                                                       value=_valor('autocuidado_minutos'), style=estilo_input({'width': '100px'}))),
                campo('Meditação (min)', dcc.Input(id='diario-checkin-meditacao', type='number', className='no-spinner',
                                                     value=_valor('meditacao_minutos'), style=estilo_input({'width': '100px'}))),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
            campo('5 Motivos Para Agradecer + Obs.:',
                  dcc.Textarea(id='diario-checkin-obs', value=_valor('observacoes', ''), rows=6,
                               style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            html.Button('Salvar Check-in do Dia', id='btn-diario-salvar-checkin', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '8px'}),
            html.Span(id='diario-checkin-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),

        _bloco_diario('Escala Hawkins (proxy, não calibrado)',
                       html.Div(_bloco_gauge_hawkins(date.fromisoformat(hoje)), id='diario-gauge-hawkins')),

        _bloco_diario('Roda da Vida', html.Div([
            html.Div([
                campo(dt.NOMES_AREAS_RODA_VIDA[area],
                      dcc.Slider(id={'type': 'diario-roda-area', 'index': area}, min=0, max=10, step=1,
                                 value=int(valores_iniciais_roda.get(area) or 5),
                                 marks={0: '0', 5: '5', 10: '10'}))
                for area in dt.AREAS_RODA_VIDA
            ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(210px, 1fr))', 'gap': '10px 20px'}),
            html.Button('Salvar Novo Registro', id='btn-diario-salvar-roda', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '10px'}),
            html.Span(id='diario-roda-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
            # `staticPlot=True`: só leitura, ver diario_app_98.py pro mesmo
            # raciocínio (tirar a interatividade evita repetir o bug do
            # eixo distorcido por clique/arraste direto no gráfico).
            dcc.Graph(id='diario-radar-roda', figure=_radar_roda_vida(valores_iniciais_roda), responsive=True,
                      config={'displayModeBar': False, 'staticPlot': True, 'responsive': True},
                      style={'marginTop': '10px', 'width': '100%'}),
        ])),

        _bloco_diario('Histórico', html.Div([
            html.Div('Check-ins recentes', style={'fontSize': '12px', 'fontWeight': '700', 'marginBottom': '4px'}),
            html.Div(_tabela_checkins(dt.listar_checkins()), id='diario-tabela-checkins'),
            html.Div('Roda da Vida — registros', style={'fontSize': '12px', 'fontWeight': '700', 'margin': '10px 0 4px'}),
            html.Div(_tabela_roda_vida(dt.listar_registros_roda_vida()), id='diario-tabela-roda-vida'),
        ])),
    ])


# =============================================================================
# APP
# =============================================================================

app = dash.Dash(__name__, suppress_callback_exceptions=True)

# Registro dos cliques da avaliação em estrelas (ver _estrela_rating) — um
# campo singleton por chamada (Novo Trade + Bem-Estar), um TIPO
# pattern-matched por chamada (Posição, cobre qualquer nº de posições
# abertas).
for _campo_estrela in (
    'diario-emo-confianca', 'diario-emo-humor', 'diario-emo-energia',
    'diario-emo-atencao', 'diario-emo-presenca',
    'diario-checkin-sono', 'diario-checkin-cafe',
):
    _registrar_clique_estrela(app, _campo_estrela)
for _tipo_estrela in (
    'ea-emo-confianca', 'ea-emo-humor', 'ea-emo-energia', 'ea-emo-atencao', 'ea-emo-presenca',
):
    _registrar_clique_estrela_match(app, _tipo_estrela)

# O auto-scroll até o overlay foi DESLIGADO em 2026-07-30. Ele existia porque
# o pop-up era renderizado em fluxo normal, no meio da página — e era
# exatamente isso que o Jesué reportou: "abre no meio da página, preciso rolar
# para chegar lá". Agora o overlay é posicionado sobre a fatia visível da
# janela (ver `_overlay` e assets/diario_overlay_viewport.js), então ele já
# nasce na tela; rolar a página por baixo dele só desorienta.
# `_registrar_scroll_ate_overlay` continua no arquivo de propósito, com o
# histórico documentado — não apagar sem ler o comentário dela.

_PADRAO_NOME_IMAGEM_DIARIO = re.compile(r'^(\d{4}_[A-Z]+_\d{8}|rascunho_\d+_\d{2})\.png$')


@app.server.route('/diario-imagens/<path:nome_arquivo>')
def _servir_imagem_diario(nome_arquivo):
    if not _PADRAO_NOME_IMAGEM_DIARIO.match(nome_arquivo):
        flask.abort(404)
    return flask.send_from_directory(cfg.PASTA_DIARIO_IMAGENS, nome_arquivo)


# dmc.MantineProvider precisa envolver a árvore inteira do app pra CSS
# vars/contexto do React funcionarem (item F, Fase 2.6 — componentes DMC
# usados dentro da aba Estatísticas, ver mais abaixo). `defaultColorScheme`
# fica em 'light' por padrão — a troca real de tema já é 100% feita via CSS
# vars (`C`/`assets/tema.js`); o DMC só precisa não brigar com isso (ver
# `_bridge_tema_mantine` callback client-side logo abaixo do layout).
app.layout = dmc.MantineProvider(id='mantine-provider-diario', defaultColorScheme='light', children=html.Div([
    html.Link(rel='stylesheet',
              href='https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500&display=swap'),

    dcc.Store(id='store-diario-sub-aba', data=_ABA_DIARIO_INICIAL),

    # Tema claro/escuro (2026-07-17) — este processo é o FILHO (embutido
    # via iframe em dashboard.py); recebe o tema do pai por postMessage
    # (assets/tema.js) e grava aqui, ou usa o último valor persistido no
    # próprio localStorage se acessado direto em :8053. Ver dashboard.py
    # pro raciocínio completo (mesmos stores lá, só que o pai também tem
    # o switch e repassa por postMessage pro iframe).
    dcc.Store(id='store-tema', data='light', storage_type='local'),
    dcc.Store(id='store-tema-dummy'),

    dcc.Store(id='diario-hist-filtro-data-calendario', data=None),
    dcc.Store(id='diario-imagem-ampliada', data=None),
    # `id_posicao` da pendência aberta no pop-up de edição do Histórico
    # (ver `abrir_pendencia_editar`/`renderizar_overlay_pendencia`), None
    # quando fechado — mesmo padrão de `diario-imagem-ampliada`.
    dcc.Store(id='store-pendencia-editar', data=None),
    # `id_trade` do trade JÁ FINALIZADO aberto no pop-up de edição do
    # Histórico (2026-07-30, botão ✎ — ver `renderizar_overlay_trade`), None
    # quando fechado. Store separada da de pendência de propósito: são dois
    # objetos diferentes (rascunho em `posicoes_abertas` × registro
    # permanente em `diario_trades`), com formulários e gravações distintas.
    dcc.Store(id='store-trade-editar', data=None),
    # Bug real 2026-07-22: esta Store vivia duplicada dentro de "Novo Trade"
    # e de "Posição" (`_construir_diario_em_andamento`) — nenhuma das duas
    # existe quando a sub-aba atual é Histórico, então clicar "Finalizar"
    # no pop-up de pendência (aberto a partir do Histórico) referenciava um
    # State inexistente e o Dash rejeitava a callback INTEIRA em silêncio.
    # Movida pra cá (topo do layout, sempre montada) resolve pras 3 sub-abas
    # de uma vez — assets/diario_paste.js já escreve nela só pelo id-string,
    # não importa onde ela esteja montada na árvore.
    dcc.Store(id='store-diario-imagem-colada'),
    # Mesmo bug, mesma causa raiz — qual card está "armado" pra receber o
    # próximo Ctrl+V (ver `assets/diario_paste.js`), referenciado por
    # `finalizar_em_andamento` que também roda a partir do pop-up do
    # Histórico, onde a sub-aba Posição não está montada.
    dcc.Store(id='store-diario-posicao-ativa-print'),
    # Contador de invalidação do Histórico (2026-07-27). Qualquer ação que
    # mude o conjunto de trades/pendências (finalizar, descartar, o "✓")
    # incrementa ISTO em vez de escrever direto em `diario-historico-tabela`.
    #
    # Por que: `diario-historico-tabela` e os filtros `diario-hist-*` só
    # existem enquanto a sub-aba Histórico está montada (`montar_sub_aba_diario`
    # troca a árvore inteira, não usa display:none). Uma callback que os
    # referencia direto passa a ser inválida em qualquer outra aba — e o Dash
    # rejeita a callback INTEIRA em silêncio (`ReferenceError: nonexistent
    # object`), matando junto o Output que de fato importava. Foi assim que
    # "Editar" e "Finalizar" ficaram sem NENHUM efeito (2026-07-22, via
    # `em-andamento-conteudo` que só existe na Posição) e é a mesma armadilha
    # que a correção de 2026-07-27 reintroduziu apontando pro outro lado.
    #
    # Com este store GLOBAL no meio, quem age só toca em ids globais, e a
    # tabela é remontada por quem é dona dela (`atualizar_historico`), que já
    # tem os filtros atuais como Input. Elimina a classe de bug, não a
    # ocorrência.
    dcc.Store(id='store-diario-refresh', data=0),
    html.Div([
        html.Button(nome, id=f'btn-diario-sub-{chave}', n_clicks=0,
                    style=_estilo_subaba_diario(chave, _ABA_DIARIO_INICIAL))
        for chave, nome in _ABAS_DIARIO_SUB
    ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '16px', 'flexWrap': 'wrap'}),

    # Overlays (imagem ampliada + pop-up de pendência) — GLOBAIS de propósito,
    # e posicionados aqui, ACIMA do conteúdo da aba.
    #
    # Eles moraram dentro de `_construir_diario_historico` entre 2026-07-20 e
    # 2026-07-27. Isso resolvia o feedback "a imagem deve abrir em pop-up e não
    # no final da lista" (antes ficavam no FIM do layout, então
    # `_registrar_scroll_ate_overlay` rolava pra baixo da tabela inteira), mas
    # criava um acoplamento perigoso: `renderizar_overlay_pendencia` e
    # `ampliar_imagem` são disparadas por Store GLOBAIS
    # (`store-pendencia-editar`/`diario-imagem-ampliada`) e escreviam num id
    # que só existe no Histórico — se qualquer escrita nessas stores
    # acontecesse com outra aba montada, o Dash derrubaria a callback inteira
    # em silêncio (mesma falha de "Editar"/"Finalizar" de 2026-07-22).
    #
    # Aqui em cima resolve os dois: sempre montados (nenhum acoplamento) E o
    # scroll cai no topo do conteúdo, não depois da tabela.
    html.Div(id='diario-overlay-imagem'),
    html.Div(id='diario-overlay-pendencia'),
    html.Div(id='diario-overlay-trade'),

    html.Div(id='diario-sub-aba-conteudo'),
], style={
    'padding': '8px', 'fontFamily': FONTS['body'], 'background': C['bg'], 'color': C['text'],
    'minHeight': '100vh', 'boxSizing': 'border-box',
}))


# Tema claro/escuro (2026-07-17) — aplica o tema recebido do pai (ou o
# persistido no próprio localStorage, se acessado direto em :8053). Sem
# repasse por postMessage aqui: este processo é o FILHO, não tem mais
# ninguém embaixo pra avisar (ver dashboard.py pro lado que manda/escuta os
# dois papéis, e assets/tema.js pro branch client-side).
app.clientside_callback(
    """
    function(tema) {
        window.SQ_TEMA = window.SQ_TEMA || {};
        window.SQ_TEMA.atual = tema || 'light';
        if (window.SQ_TEMA.aplicarLocal) window.SQ_TEMA.aplicarLocal(window.SQ_TEMA.atual);
        return '';
    }
    """,
    Output('store-tema-dummy', 'data'),
    Input('store-tema', 'data'),
)

# Ponte DMC (item F, Fase 2.6) — `dmc.MantineProvider` tem sistema de tema
# PRÓPRIO (`forceColorScheme`), independente do `C`/CSS vars já existente.
# Sem isso, componentes DMC (Card/RingProgress/Tooltip/AgGrid) ficariam
# sempre no esquema default enquanto o resto da página troca por CSS var —
# os dois sistemas precisam ouvir o MESMO `store-tema`.
app.clientside_callback(
    "function(tema) { return tema || 'light'; }",
    Output('mantine-provider-diario', 'forceColorScheme'),
    Input('store-tema', 'data'),
)


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
    'estrategias': lambda: html.Div(id='diario-estrategias-conteudo'),
    'padroes': _construir_diario_padroes,
    'bem_estar': lambda: html.Div(id='diario-bemestar-conteudo'),
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
    Output('diario-estrategias-conteudo', 'children'),
    Input('store-diario-sub-aba', 'data'),
)
def atualizar_estrategias(sub_aba_ativa):
    if sub_aba_ativa != 'estrategias':
        return no_update
    return _construir_diario_estrategias()


@app.callback(
    Output('diario-tabela-estrategias', 'children'),
    Output('diario-estrategia-status', 'children'),
    Output('diario-nova-estrategia-nome', 'value'),
    Output('diario-nova-estrategia-desc', 'value'),
    Output('diario-nova-estrategia-checklist', 'value'),
    Output('diario-nova-estrategia-categoria', 'value'),
    Output('store-diario-estrategia-editando', 'data'),
    Output('btn-diario-add-estrategia', 'children'),
    Output('btn-diario-cancelar-edicao-estrategia', 'style'),
    Output('diario-estrategia-editando-aviso', 'children'),
    Input('btn-diario-add-estrategia', 'n_clicks'),
    Input({'type': 'diario-estrategia-desativar', 'index': ALL}, 'n_clicks'),
    Input({'type': 'diario-estrategia-editar', 'index': ALL}, 'n_clicks'),
    Input('btn-diario-cancelar-edicao-estrategia', 'n_clicks'),
    State('diario-nova-estrategia-nome', 'value'),
    State('diario-nova-estrategia-desc', 'value'),
    State('diario-nova-estrategia-checklist', 'value'),
    State('diario-nova-estrategia-categoria', 'value'),
    State('store-diario-estrategia-editando', 'data'),
    prevent_initial_call=True,
)
def gerenciar_estrategias(n_add, n_desativar, n_editar, n_cancelar, nome, descricao, checklist_texto,
                          categoria, editando_id):
    """Um único form ("Nova Estratégia") serve os 2 modos — criar e editar
    (feedback 2026-07-20: "preciso de um botão pra editar a estratégia").
    `store-diario-estrategia-editando` guarda o id em edição (None = modo
    criar); clicar "Editar" popula o form + muda o botão pra "Salvar
    Alterações"; "Cancelar Edição" e o próprio submit (quando `editando_id`
    está setado) voltam ao modo criar."""
    ctx = callback_context
    if not ctx.triggered:
        return (no_update,) * 10
    origem = ctx.triggered[0]['prop_id']
    prop_curto = origem.rsplit('.', 1)[0]
    estilo_cancelar_oculto = {**_estilo_btn_pequeno(), 'marginLeft': '8px', 'display': 'none'}
    estilo_cancelar_visivel = {**_estilo_btn_pequeno(), 'marginLeft': '8px', 'display': 'inline-block',
                                'color': C['gold'], 'borderColor': C['gold']}

    if prop_curto == 'btn-diario-cancelar-edicao-estrategia':
        if not n_cancelar:
            return (no_update,) * 10
        return (no_update, 'Edição cancelada.', '', '', '', None, None,
                'Adicionar Estratégia', estilo_cancelar_oculto, None)

    if prop_curto == 'btn-diario-add-estrategia':
        if not nome or not nome.strip():
            return (no_update, '⚠ Informe um nome.') + (no_update,) * 8
        checklist = [linha.strip() for linha in (checklist_texto or '').split('\n') if linha.strip()]
        if editando_id:
            dt.editar_estrategia(editando_id, nome=nome.strip(), descricao=descricao or '',
                                  checklist=checklist, categoria=categoria)
            return (_tabela_estrategias(dt.listar_estrategias()), f'✔ "{nome.strip()}" atualizada.',
                    '', '', '', None, None, 'Adicionar Estratégia', estilo_cancelar_oculto, None)
        dt.criar_estrategia(nome, descricao or '', checklist, categoria=categoria)
        return (_tabela_estrategias(dt.listar_estrategias()), f'✔ "{nome.strip()}" cadastrada.',
                '', '', '', None, no_update, no_update, no_update, no_update)

    if prop_curto.startswith('{'):
        id_componente = json.loads(prop_curto)
        if id_componente['type'] == 'diario-estrategia-editar':
            if not any(n_editar or []):
                return (no_update,) * 10
            df = dt.listar_estrategias(somente_ativas=False)
            linha = df[df['id'] == id_componente['index']]
            if linha.empty:
                return (no_update,) * 10
            linha0 = linha.iloc[0]
            checklist_texto_novo = '\n'.join(_lista_segura(linha0['checklist']))
            categoria_atual = linha0.get('categoria')
            categoria_atual = None if pd.isna(categoria_atual) else str(categoria_atual)
            aviso = html.Div(f'✏ Editando "{linha0["nome"]}" (ID {linha0["id"]}).',
                              style={'fontSize': '11px', 'color': C['gold'], 'marginBottom': '8px'})
            return (no_update, '', linha0['nome'], linha0['descricao'] or '', checklist_texto_novo,
                    categoria_atual, int(linha0['id']), 'Salvar Alterações', estilo_cancelar_visivel, aviso)
        if id_componente['type'] == 'diario-estrategia-desativar':
            if not any(n_desativar or []):
                return (no_update,) * 10
            dt.desativar_estrategia(id_componente['index'])
            return (_tabela_estrategias(dt.listar_estrategias()), '✔ Estratégia desativada.',
                    no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update)
    return (no_update,) * 10


@app.callback(
    Output('diario-tabela-rr', 'children'),
    Output('diario-rr-status', 'children'),
    Output('diario-novo-rr-nome', 'value'),
    Output('diario-novo-rr-alvo', 'value'),
    Output('diario-novo-rr-stop', 'value'),
    Output('diario-novo-rr-be', 'value'),
    Input('btn-diario-add-rr', 'n_clicks'),
    Input({'type': 'diario-rr-desativar', 'index': ALL}, 'n_clicks'),
    State('diario-novo-rr-nome', 'value'),
    State('diario-novo-rr-alvo', 'value'),
    State('diario-novo-rr-stop', 'value'),
    State('diario-novo-rr-be', 'value'),
    prevent_initial_call=True,
)
def gerenciar_rr_presets(n_add, n_desativar, nome, alvo, stop, be):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario-add-rr'):
        alvo, stop, be = _num(alvo), _num(stop), _num(be)
        if not nome or not nome.strip() or alvo is None or stop is None:
            return no_update, '⚠ Informe nome, alvo e stop.', no_update, no_update, no_update, no_update
        dt.criar_rr_preset(nome, alvo, stop, be)
        return _tabela_rr_presets(dt.listar_rr_presets()), f'✔ "{nome.strip()}" cadastrado.', '', None, None, None
    if not any(n_desativar or []):
        return no_update, no_update, no_update, no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_rr_preset(id_componente['index'])
    return _tabela_rr_presets(dt.listar_rr_presets()), '✔ Preset desativado.', no_update, no_update, no_update, no_update


@app.callback(
    Output('diario-tabela-tags', 'children'),
    Output('diario-tag-status', 'children'),
    Output('diario-nova-tag-nome', 'value'),
    Input('btn-diario-add-tag', 'n_clicks'),
    Input({'type': 'diario-tag-desativar', 'index': ALL}, 'n_clicks'),
    State('diario-nova-tag-nome', 'value'),
    State('diario-nova-tag-tipo', 'value'),
    prevent_initial_call=True,
)
def gerenciar_tags(n_add, n_desativar, nome, tipo):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario-add-tag.'):
        if not nome or not nome.strip():
            return no_update, '⚠ Informe um nome.', no_update
        dt.criar_tag_setup_erro(nome, tipo or 'ambos')
        return _tabela_tags(dt.listar_tags_setup_erro()), f'✔ "{nome.strip()}" cadastrada.', ''
    if not any(n_desativar or []):
        return no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_tag_setup_erro(id_componente['index'])
    return _tabela_tags(dt.listar_tags_setup_erro()), '✔ Tag desativada.', no_update


@app.callback(
    Output('diario-tabela-tags-emocionais', 'children'),
    Output('diario-tag-emocional-status', 'children'),
    Output('diario-nova-tag-emocional-nome', 'value'),
    Input('btn-diario-add-tag-emocional', 'n_clicks'),
    Input({'type': 'diario-tag-emocional-desativar', 'index': ALL}, 'n_clicks'),
    State('diario-nova-tag-emocional-nome', 'value'),
    prevent_initial_call=True,
)
def gerenciar_tags_emocionais(n_add, n_desativar, nome):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario-add-tag-emocional'):
        if not nome or not nome.strip():
            return no_update, '⚠ Informe um nome.', no_update
        dt.criar_tag_emocional(nome)
        return _tabela_tags_emocionais(dt.listar_tags_emocionais()), f'✔ "{nome.strip()}" cadastrada.', ''
    if not any(n_desativar or []):
        return no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_tag_emocional(id_componente['index'])
    return _tabela_tags_emocionais(dt.listar_tags_emocionais()), '✔ Tag desativada.', no_update


@app.callback(
    Output('diario-alvo', 'value'),
    Output('diario-stop', 'value'),
    Input('diario-rr-preset', 'value'),
    prevent_initial_call=True,
)
def aplicar_rr_preset(rr_valor):
    preset = dt.obter_rr_preset(rr_valor)
    if preset is None:
        return no_update, no_update
    return preset['alvo_pontos'], preset['stop_pontos']


@app.callback(
    Output('diario-rr-preset', 'value', allow_duplicate=True),
    Input('diario-alvo', 'value'),
    Input('diario-stop', 'value'),
    State('diario-rr-preset', 'value'),
    prevent_initial_call=True,
)
def resetar_rr_se_editado(alvo, stop, rr_atual):
    """Ver diario_app_98.py::resetar_rr_se_editado_98 pro raciocínio
    completo (compara contra o preset em vez de usar triggered_id, pra
    evitar loop de re-disparo)."""
    if not rr_atual:
        return no_update
    preset = dt.obter_rr_preset(rr_atual)
    if preset is None:
        return no_update
    if _num(alvo) == preset['alvo_pontos'] and _num(stop) == preset['stop_pontos']:
        return no_update
    return None


@app.callback(
    Output('diario-sentimento-label', 'children'),
    Output('diario-sentimento-label', 'style'),
    Input('diario-sentimento-mercado', 'value'),
)
def atualizar_label_sentimento(valor):
    resultado = dt.classificar_sentimento_mercado(valor)
    if resultado is None:
        return '—', {'fontSize': '12px', 'color': C['text2']}
    nome, cor_chave = resultado
    return nome, {'fontSize': '12px', 'fontWeight': '700', 'color': C.get(cor_chave, C['text'])}


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
    State('diario-emo-confianca', 'value'),
    State('diario-emo-humor', 'value'),
    State('diario-emo-energia', 'value'),
    State('diario-emo-atencao', 'value'),
    State('diario-emo-presenca', 'value'),
    State('diario-tags-emocionais', 'value'),
    State('diario-sentimento-mercado', 'value'),
    State('diario-upload-imagem', 'contents'),
    State('store-diario-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def salvar_novo_trade(n_clicks, data_op, ativo, janela, estrategia_tags, sala, tipo_op, alvo, stop, lote,
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
    # compartilha memória com dashboard.py — lê os candles frescos do disco
    # em vez do `estado['bases']` em memória (diferença de até ~30s,
    # irrelevante pra este propósito). Escopo: só CAPTURA — comparação
    # contra nivel_sr_referencia é `dt.avaliar_aderencia_tracados`, sob
    # demanda. Traçados analisados aqui (Fase 3.2, 2026-07-21) — mesmo
    # motor que a aba Price Action usa, sem I/O extra além do que já
    # precisaríamos pra montar o snapshot de qualquer forma.
    df_ativo = persistencia.carregar_candles(ativo)
    m = extrair_metricas(df_ativo) if df_ativo is not None else {}
    tracados_analisados = []
    if df_ativo is not None:
        try:
            tracados_df = tracados.listar_tracados(ativo)
            tracados_analisados = analisar_tracados(df_ativo, tracados_df)
        except Exception:
            tracados_analisados = []
    snapshot_mercado = montar_snapshot_diario(m, tracados_analisados) if m else None

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
    Output('diario-emo-confianca', 'value'),
    Output('diario-emo-humor', 'value'),
    Output('diario-emo-energia', 'value'),
    Output('diario-emo-atencao', 'value'),
    Output('diario-emo-presenca', 'value'),
    Output('diario-tags-emocionais', 'value'),
    Output('diario-sentimento-mercado', 'value'),
    Output('diario-preview-imagem', 'children', allow_duplicate=True),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-diario-limpar', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_form_diario(n_clicks):
    return (None, None, None, None, None, None, None, None, 1,
            None, None, None, None, None, None, None, None, None, None,
            3, 3, 3, 3, 3, None, 50,
            None, None)


# Tetos do Histórico (2026-07-30) — nunca renderizar sem limite algum: o
# custo de reconciliação do Dash escala com a contagem TOTAL de componentes
# montados (ver MAPA_ESTRUTURA.md), e o Jesué travou a página de verdade
# selecionando um período amplo com a base de 210 trades reais (420 `<tr>`,
# cada trade vira resumo+detalhe) — na página embutida real (dashboard.py,
# com o gráfico candlestick ao vivo e o sync em background disputando o
# mesmo thread de render, não só o processo isolado do Diário). Não dava
# pra reproduzir esse ambiente exato daqui pra medir o limiar certo — por
# isso o teto do "com filtro" fica BEM abaixo do que já travou (210), não
# só "abaixo": 100 é menos da metade do caso confirmado ruim, com margem de
# sobra. Navegação livre continua conservadora (50, nunca mudou).
_LIMITE_HISTORICO_SEM_FILTRO = 50
_LIMITE_HISTORICO_COM_FILTRO = 100


def _montar_tabela_historico(ativo, janela, estrategia, data_ini_efetivo, data_fim_efetivo, filtro_cal):
    """Monta a tabela do Histórico (trades reais + pendências) pros filtros
    já resolvidos — extraído de `atualizar_historico`, que só resolve os
    filtros e delega aqui.

    Chamador único, de propósito: `atualizar_historico` é a única dona de
    `diario-historico-tabela`. Ações que mudam a lista (finalizar, descartar,
    "✓") NÃO chamam esta função — elas incrementam `store-diario-refresh`,
    que é Input de lá. Ver o comentário desse store no layout pro motivo (uma
    ação que escreve direto nesta tabela vira inválida fora da aba Histórico
    e o Dash derruba a callback inteira em silêncio)."""
    df = dt.listar_trades(ativo=ativo, janela=janela, estrategia_tag=estrategia,
                           data_ini=data_ini_efetivo, data_fim=data_fim_efetivo, is_simulated=False)
    df = df.sort_values('data_op', ascending=False)
    # BUG REAL 2026-07-30 (reportado pelo Jesué: "mesmo marcando o período no
    # seletor de datas ele exibe apenas o mês atual"): o corte de linhas era
    # incondicional (top 50 por recência) a menos que um clique no calendário
    # estivesse ativo. Um filtro de DATA explícito no seletor não bastava pra
    # levantar o corte — então escolher um período amplo sem também mexer em
    # ativo/janela/estratégia continuava mostrando só as 50 operações mais
    # recentes, que quase sempre são todas do mês corrente. Com outro filtro
    # (ativo, janela, estratégia) o subconjunto ficava menor e o corte passava
    # despercebido — foi exatamente essa diferença que ele reportou.
    #
    # BUG REAL nº2, achado pelo Jesué na hora ("agora está travando ao tentar
    # selecionar o período"): a correção acima removeu o corte por completo
    # pra qualquer filtro — mas SEM corte nenhum, escolher um período amplo
    # (ex: desde 2016) devolve os 210 trades reais de uma vez, 420 `<tr>`
    # (cada trade vira 2 linhas: resumo + detalhe). Renderizar isso inteiro
    # trava o navegador — a mesma classe de problema que já tinha justificado
    # botar o Diário inteiro num iframe (ver MAPA_ESTRUTURA.md: custo de
    # reconciliação do Dash escala com a contagem TOTAL de componentes
    # montados). NUNCA renderizar sem limite algum, então — só limites
    # diferentes: navegação livre (sem filtro nenhum) continua limitada a 50;
    # qualquer filtro explícito ganha uma margem bem mais folgada (300, cobre
    # a base inteira de hoje com sobra), mas segue BOUNDED, com aviso visível
    # quando o corte realmente acontecer (diferença chave: o corte antigo era
    # SILENCIOSO, esse avisa).
    total_encontrado = len(df)
    sem_filtro_nenhum = not (ativo or janela or estrategia or data_ini_efetivo or data_fim_efetivo or filtro_cal)
    limite = _LIMITE_HISTORICO_SEM_FILTRO if sem_filtro_nenhum else _LIMITE_HISTORICO_COM_FILTRO
    truncado = total_encontrado > limite
    df = df.head(limite)

    # Pendências (fechamentos ao vivo + descobertas históricas, ver
    # dt.listar_pendencias_historico) — mesmos filtros de ativo/data do
    # Histórico, JANELA/ESTRATÉGIA não se aplicam (não existem pra uma
    # execução ainda não preenchida).
    pendencias = dt.listar_pendencias_historico()
    if not pendencias.empty:
        if ativo:
            pendencias = pendencias[pendencias['ativo'] == ativo]
        if data_ini_efetivo:
            pendencias = pendencias[pendencias['data'].astype(str) >= str(data_ini_efetivo)]
        if data_fim_efetivo:
            pendencias = pendencias[pendencias['data'].astype(str) <= str(data_fim_efetivo)]

    partes_banner = []
    if filtro_cal:
        partes_banner.append(html.Span(f'Filtrando pelo dia {filtro_cal} (clique no calendário). ',
                                        style={'fontSize': '12px', 'color': C['text2']}))
        partes_banner.append(html.Button('Limpar', id='btn-diario-hist-limpar-filtro-cal', n_clicks=0, style={
            'background': 'transparent', 'color': C['text2'], 'border': f'1px solid {C["border"]}',
            'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer', 'fontSize': '11px',
        }))
    if truncado:
        partes_banner.append(html.Span(
            f'Mostrando as {limite} operações mais recentes de {total_encontrado} encontradas — '
            f'refine o ativo, janela, estratégia ou período pra ver o restante.',
            style={'fontSize': '12px', 'color': C['yellow']}))
    banner = html.Div(partes_banner, style={'display': 'flex', 'alignItems': 'center', 'gap': '8px',
                                            'flexWrap': 'wrap'}) if partes_banner else ''

    if df.empty and pendencias.empty:
        return html.Div('Nenhuma operação encontrada.', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        }), banner
    aderencia_df = dt.avaliar_aderencia_tracados()
    aderencia_por_trade = (
        dict(zip(aderencia_df['id_trade'].astype(int), aderencia_df['classificacao']))
        if not aderencia_df.empty else {}
    )
    mapa_nomes_estrategias = _mapa_nomes_estrategias()
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
        for t in ['Data', 'Ativo', 'Janela', 'Estratégia', 'Tipo', 'Pts', 'Saldo', 'Print', 'Aderência aos Traçados', '']
    ])
    linhas = [_linha_pendencia(p) for _, p in pendencias.iterrows()]
    for _, row in df.iterrows():
        linhas.extend(_linha_historico(row, aderencia_por_trade, mapa_nomes_estrategias))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'}), banner


@app.callback(
    Output('diario-historico-tabela', 'children'),
    Output('diario-hist-banner-filtro-cal', 'children'),
    Input('diario-hist-ativo', 'value'),
    Input('diario-hist-janela', 'value'),
    Input('diario-hist-estrategia', 'value'),
    Input('diario-hist-data-ini', 'value'),
    Input('diario-hist-data-fim', 'value'),
    Input('diario-hist-filtro-data-calendario', 'data'),
    Input('store-diario-refresh', 'data'),
)
def atualizar_historico(ativo, janela, estrategia, data_ini, data_fim, filtro_cal, _refresh):
    """Única dona de `diario-historico-tabela`. `_refresh` não é usado no
    corpo de propósito — é só o gatilho de invalidação (ver
    `store-diario-refresh` no layout): finalizar/descartar/"✓" incrementam
    aquele contador e a tabela se remonta aqui, com os filtros que já estão
    na tela, sem que nenhuma daquelas callbacks precise conhecer os ids
    desta aba."""
    # O clique num dia do calendário (Estatísticas) força um filtro de dia
    # único aqui, sobrepondo o filtro manual de intervalo.
    data_ini_efetivo = filtro_cal or data_ini
    data_fim_efetivo = filtro_cal or data_fim
    return _montar_tabela_historico(ativo, janela, estrategia, data_ini_efetivo, data_fim_efetivo, filtro_cal)


@app.callback(
    Output('store-diario-refresh', 'data', allow_duplicate=True),
    Input({'type': 'diario-btn-finalizar-rapido', 'index': ALL}, 'n_clicks'),
    State('store-diario-refresh', 'data'),
    prevent_initial_call=True,
)
def finalizar_rapido_pendencia(n_clicks_lista, refresh_atual):
    """Botão "✓" da lista PENDENTE do Histórico (feedback 2026-07-27:
    "gravar a operação no histórico sem precisar editar, pegando apenas as
    informações padrão do MT5") — pula o pop-up inteiro e finaliza direto
    com `{'conta': 'real'}` como única classificação (mesmo mínimo que
    `finalizar_em_andamento` usa quando o usuário não preenche nada).

    Reaproveita `materializar_pendencia_como_rascunho` pro caso 3 (só
    reconciliação histórica, ainda não é um rascunho de verdade) — pro
    par de hedge e pro ticket avulso já materializado, vai direto pra
    `finalizar_par_hedge`/`finalizar_posicao`.

    Só escreve em `store-diario-refresh` (global) — a tabela é remontada por
    `atualizar_historico`. Ver o comentário desse store no layout pro porquê.

    A guarda de `n_clicks` abaixo é obrigatória: o refresh remonta a tabela,
    que recria estes próprios botões com `n_clicks=0`, e a lista `ALL` mudando
    redispara esta callback (regra do CLAUDE.md sobre nunca recriar o próprio
    gatilho). Com todos em 0, `any(...)` é False e ela para aqui."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    componente_id = json.loads(prop_id)
    id_editar = componente_id['index']

    par = _parse_index_par(id_editar)
    if par:
        ticket_a, ticket_b = par
        id_trade = dt.finalizar_par_hedge(ticket_a, ticket_b, {'conta': 'real'})
    else:
        mt5_position_id = dt.materializar_pendencia_como_rascunho(id_editar)
        id_trade = (dt.finalizar_posicao(mt5_position_id, {'conta': 'real'})
                    if mt5_position_id is not None else None)

    if id_trade is None:
        # Nada gravado — ticket já finalizado antes (a trava de idempotência
        # de `diario_trade` recusou) ou trava de arquivo ocupada. Pede o
        # refresh do mesmo jeito: se era duplicidade, a linha vai sumir da
        # lista agora que a lápide é consultada; se era trava, o próximo
        # ciclo mostra o estado certo. Nunca fingir sucesso em silêncio.
        _log_diario.warning('Finalização rápida não gravou nada para id_editar=%s '
                            '(já finalizado antes ou trava ocupada).', id_editar)
    return (refresh_atual or 0) + 1


@app.callback(
    Output('diario-hist-filtro-data-calendario', 'data', allow_duplicate=True),
    Input('btn-diario-hist-limpar-filtro-cal', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_filtro_calendario(n_clicks):
    return None


@app.callback(
    Output({'type': 'diario-detalhe-linha', 'index': dash.MATCH}, 'children'),
    Output({'type': 'diario-btn-expandir', 'index': dash.MATCH}, 'children'),
    Input({'type': 'diario-btn-expandir', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'diario-btn-expandir', 'index': dash.MATCH}, 'children'),
    State({'type': 'diario-btn-expandir', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def alternar_detalhe_historico(n_clicks, seta_atual, id_componente):
    """Expande/recolhe a linha de detalhe do Histórico — substitui o
    antigo popup (`_overlay`/`diario-trade-detalhe-id`), removido de vez
    (feedback 2026-07-16). Usa o próprio texto da seta como estado (▶
    fechado/▼ aberto) em vez de um `dcc.Store` por linha — mais simples,
    já que a seta É a única fonte de verdade de "está aberta ou não"."""
    if seta_atual == '▶':
        conteudo = _conteudo_detalhe_trade(id_componente['index'])
        return conteudo, '▼'
    return None, '▶'


@app.callback(
    Output('diario-guarda-corpo-dia', 'children'),
    Input('interval-em-andamento', 'n_intervals'),
)
def atualizar_guarda_corpo_dia(_n):
    """Contadores do dia, a cada 6s.

    O Input é o interval da PRÓPRIA aba Posição, não `store-diario-refresh`
    (global). Parece pior — o número só atualiza depois de até 6s em vez de
    na hora — mas evita a armadilha recorrente deste arquivo: um Output pra
    id que só existe na Posição, disparado por um store global, seria
    rejeitado INTEIRO pelo Dash sempre que o Jesué finalizasse um trade
    estando no Histórico. Ver o comentário de `store-diario-refresh` no
    layout. 6s de atraso num contador não muda decisão nenhuma."""
    return _bloco_guarda_corpo_dia()


@app.callback(
    Output('diario-checklist-conteudo', 'children'),
    Output('btn-diario-checklist', 'children'),
    Input('btn-diario-checklist', 'n_clicks'),
    State('btn-diario-checklist', 'children'),
    prevent_initial_call=True,
)
def alternar_checklist_pre_trade(n_clicks, rotulo_atual):
    """Abre/fecha o checklist. Mesma mecânica das outras setas: o próprio
    rótulo do botão é o estado, e o botão nunca é recriado por esta
    callback (só o `children` dele muda)."""
    if not n_clicks:
        return no_update, no_update
    if rotulo_atual == 'Checklist pré-trade':
        return _conteudo_checklist_pre_trade(), 'Fechar checklist'
    return None, 'Checklist pré-trade'


@app.callback(
    Output('diario-checklist-status', 'children'),
    Output('diario-checklist-status', 'style'),
    Input('diario-checklist-itens', 'value'),
)
def resumir_checklist(marcados):
    total = len(cfg.DIARIO_CHECKLIST_PRE_TRADE)
    n = len(marcados or [])
    base = {'fontSize': '11px', 'marginTop': '8px', 'fontWeight': '700'}
    if n == total:
        return f'✔ {n}/{total} — dentro do plano.', {**base, 'color': C['green']}
    return (f'{n}/{total} conferidos — faltam {total - n}.',
            {**base, 'color': C['text2'] if n else C['text2']})


@app.callback(
    Output('diario-mentor-ia-conteudo', 'children'),
    Output('btn-gerar-mentor-ia', 'children'),
    Input('btn-gerar-mentor-ia', 'n_clicks'),
    prevent_initial_call=True,
)
def gerar_mentor_ia(n_clicks):
    """Gera (ou regenera) a análise do Mentor de IA. Bloqueante — mesma
    convenção do chat de IA (`enviar_mensagem_chat` em dashboard.py): não
    há um segundo round-trip, o callback só retorna quando a resposta real
    (ou erro) chega. `dcc.Loading` cobre a espera."""
    if not n_clicks:
        return no_update, no_update
    resumo = dt.montar_resumo_para_mentor_ia()
    resultado = ai_analyst.gerar_analise_evolucao_operacional(resumo)
    if resultado.get('erro'):
        conteudo = html.Div(f"⚠ {resultado['erro']}", style={'fontSize': '12px', 'color': C['red']})
        return conteudo, no_update
    cache = ai_analyst.carregar_analise_diaria()
    return _conteudo_mentor_ia(cache), 'Gerar nova análise'


@app.callback(
    Output('diario-detalhe-estrategia-selecionada', 'children'),
    Input('grid-confiabilidade-estrategia', 'cellClicked'),
    prevent_initial_call=True,
)
def atualizar_detalhe_estrategia_grid(cell_clicked):
    """Clique numa linha do AG Grid "Confiabilidade por Estratégia" (item
    F, Fase 2.6) — substitui a seta ▶/▼ por linha (`alternar_detalhe_
    estrategia`, agora removida): `cellClicked` já traz a linha inteira em
    `cell_clicked['data']`, sem precisar de pattern-matching id por tag.
    Sempre re-renderiza o MESMO painel fixo (`diario-detalhe-estrategia-
    selecionada`), nunca um id por linha — mais simples que o padrão
    anterior porque o grid já resolve "qual linha" sozinho."""
    if not cell_clicked or not cell_clicked.get('data'):
        return no_update
    tag = cell_clicked['data'].get('tag')
    if not tag or not cell_clicked['data'].get('n'):
        return None
    return _detalhe_estrategia_expandido(tag)


@app.callback(
    Output({'type': 'ea-imagem-principal', 'index': dash.MATCH}, 'data'),
    Input({'type': 'ea-imagem-thumb-escolher', 'index': dash.MATCH, 'nome': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def escolher_imagem_principal(n_clicks_lista):
    """Clique na miniatura vira a escolha de capa na hora — ver
    `_campo_imagem_principal`. Mesma técnica de `ampliar_imagem` (achar QUAL
    id disparou via `ctx.triggered`, sem precisar de JS novo)."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    id_componente = json.loads(ctx.triggered[0]['prop_id'].rsplit('.', 1)[0])
    return id_componente['nome']


@app.callback(
    Output({'type': 'ea-imagem-thumb-escolher', 'index': dash.MATCH, 'nome': ALL}, 'style'),
    Input({'type': 'ea-imagem-principal', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea-imagem-thumb-escolher', 'index': dash.MATCH, 'nome': ALL}, 'id'),
)
def destacar_imagem_principal(selecionada, ids_lista):
    return [_estilo_thumb_imagem_principal(item_id['nome'] == selecionada) for item_id in ids_lista]


@app.callback(
    Output('diario-imagem-ampliada', 'data'),
    Input({'type': 'diario-img-thumb', 'index': ALL}, 'n_clicks'),
    Input({'type': 'diario-img-thumb-detalhe', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def ampliar_imagem(n_clicks_lista, n_clicks_lista_detalhe):
    """Amplia tanto a miniatura da linha do Histórico (`diario-img-thumb`)
    quanto a imagem maior dentro do detalhe expandido (`diario-img-thumb-
    detalhe`, feedback 2026-07-16) — 2 `Input`s pattern-matched em vez de 1
    porque as duas podem coexistir na página ao mesmo tempo (linha
    recolhida + detalhe expandido), então não podem compartilhar o mesmo
    `type` de id (duplicaria o id na mesma renderização).

    Feedback 2026-07-20: "deve ter botões de navegação entre outros prints
    do mesmo trade quando tiver" — a imagem principal (`imagem_arquivo`)
    sempre entra primeiro, seguida das adicionais (`imagens_adicionais`,
    coluna existente desde a Fase C, nunca exposta na UI até agora).
    `indice` sempre começa em 0 (a principal), navegação em
    `navegar_imagem_ampliada`."""
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
    adicionais = [nome for nome in _lista_segura(linha0.get('imagens_adicionais')) if nome != principal]
    imagens = [principal] + adicionais
    return {'imagens': [f"/diario-imagens/{nome}" for nome in imagens], 'indice': 0}


@app.callback(
    Output('diario-imagem-ampliada', 'data', allow_duplicate=True),
    Input('btn-diario-fechar-imagem', 'n_clicks'),
    Input('diario-overlay-imagem-backdrop', 'n_clicks'),
    prevent_initial_call=True,
)
def fechar_imagem_ampliada(n1, n2):
    # Mesma guarda de `fechar_pendencia_editar` (bug real 2026-07-27): os
    # gatilhos são criados junto com o overlay, e a inserção dispara a
    # callback com n_clicks=0 — fechando a imagem no mesmo instante em que
    # ela abre.
    ctx = callback_context
    if not ctx.triggered or not ctx.triggered[0].get('value'):
        return no_update
    return None


@app.callback(
    Output('diario-imagem-ampliada', 'data', allow_duplicate=True),
    Input('btn-diario-imagem-anterior', 'n_clicks'),
    Input('btn-diario-imagem-proxima', 'n_clicks'),
    State('diario-imagem-ampliada', 'data'),
    prevent_initial_call=True,
)
def navegar_imagem_ampliada(_n_anterior, _n_proxima, atual):
    if not atual or not atual.get('imagens'):
        return no_update
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    total = len(atual['imagens'])
    indice = atual.get('indice', 0)
    if origem == 'btn-diario-imagem-anterior':
        indice = (indice - 1) % total
    elif origem == 'btn-diario-imagem-proxima':
        indice = (indice + 1) % total
    else:
        return no_update
    return {**atual, 'indice': indice}


@app.callback(
    Output('diario-overlay-imagem', 'children'),
    Input('diario-imagem-ampliada', 'data'),
)
def renderizar_overlay_imagem(dados):
    if not dados or not dados.get('imagens'):
        return None
    imagens = dados['imagens']
    indice = dados.get('indice', 0)
    estilo_botao = {
        'background': 'transparent', 'color': C['text'], 'border': f'1px solid {C["border"]}',
        'borderRadius': '6px', 'padding': '4px 12px', 'cursor': 'pointer',
    }
    # Controles de zoom (feedback 2026-07-30: "com a opção de expandir e dar
    # zoom"). A roda do mouse já dava zoom desde 2026-07-20, mas nada na tela
    # dizia isso. Estes botões não têm callback Python — o comportamento é
    # 100% client-side via `data-zoom-acao` (ver assets/diario_zoom.js);
    # colocá-los no Dash só pra emitir um n_clicks que voltaria pro browser
    # seria um round-trip inútil num controle que precisa ser instantâneo.
    controles_zoom = html.Div([
        html.Button('−', **{'data-zoom-acao': 'menos'}, title='Diminuir (tecla −)',
                    style={**estilo_botao, 'padding': '4px 14px', 'fontWeight': '700'}),
        html.Button('+', **{'data-zoom-acao': 'mais'}, title='Ampliar (tecla +)',
                    style={**estilo_botao, 'padding': '4px 14px', 'fontWeight': '700'}),
        html.Button('⛶ Ajustar', **{'data-zoom-acao': 'reset'},
                    title='Voltar ao tamanho da tela (tecla 0)', style=estilo_botao),
        html.Span('roda do mouse amplia · arraste para mover · Esc fecha',
                  style={'fontSize': '11px', 'color': C['text2'], 'marginLeft': '4px'}),
    ], style={'display': 'flex', 'gap': '8px', 'alignItems': 'center', 'flexWrap': 'wrap'})

    topo = [
        html.Div([
            html.Button('✕ Fechar', id='btn-diario-fechar-imagem', n_clicks=0, style=estilo_botao),
            controles_zoom,
        ], style={'display': 'flex', 'gap': '12px', 'alignItems': 'center',
                  'flexWrap': 'wrap', 'justifyContent': 'space-between'}),
    ]
    if len(imagens) > 1:
        topo.append(html.Div([
            html.Button('◀ Anterior', id='btn-diario-imagem-anterior', n_clicks=0, style=estilo_botao),
            html.Span(f'{indice + 1} / {len(imagens)}', style={'fontSize': '12px', 'color': C['text2']}),
            html.Button('Próxima ▶', id='btn-diario-imagem-proxima', n_clicks=0, style=estilo_botao),
        ], style={'display': 'flex', 'gap': '10px', 'alignItems': 'center'}))
    # Feedback 2026-07-20: "preencher o bloco inteiro com zoom pela rolagem
    # do scroll, e clicar e arrastar" — comportamento em assets/diario_zoom.js
    # (delegação de evento em document, wheel = zoom centrado no cursor,
    # pointer capture = arrastar). `key=imagens[indice]` força o React a
    # remontar um <img>/container NOVO por imagem (não só trocar o src de um
    # nó reciclado) — sem isso o zoom/pan da imagem anterior (guardado direto
    # no elemento DOM pelo JS) vazaria pra próxima ao navegar.
    filhos = [
        html.Div(topo, style={'display': 'flex', 'flexDirection': 'column', 'gap': '8px', 'flexShrink': '0'}),
        html.Div(
            html.Img(src=imagens[indice], className='diario-zoom-img', draggable='false', key=imagens[indice]),
            className='diario-zoom-container', key=imagens[indice],
            style={'flex': '1', 'minHeight': '0', 'marginTop': '8px', 'border': f"2px solid {C['border']}"},
        ),
    ]
    return _overlay(html.Div(filhos, style={'display': 'flex', 'flexDirection': 'column', 'height': '100%'}),
                     'diario-overlay-imagem-backdrop')


@app.callback(
    Output('diario-estatisticas-conteudo', 'children'),
    Input('store-diario-sub-aba', 'data'),
    State('store-tema', 'data'),
)
def atualizar_estatisticas(sub_aba_ativa, tema_atual):
    if sub_aba_ativa != 'estatisticas':
        return no_update
    return _construir_diario_estatisticas(dt.calcular_estatisticas(), dt.calcular_score_confiabilidade(),
                                           tema_atual or 'light')


@app.callback(
    Output('diario-bemestar-conteudo', 'children'),
    Input('store-diario-sub-aba', 'data'),
)
def atualizar_bem_estar(sub_aba_ativa):
    if sub_aba_ativa != 'bem_estar':
        return no_update
    return _construir_diario_bem_estar()


@app.callback(
    Output('diario-checkin-status', 'children'),
    Output('diario-gauge-hawkins', 'children'),
    Output('diario-tabela-checkins', 'children'),
    Input('btn-diario-salvar-checkin', 'n_clicks'),
    State('diario-checkin-data', 'value'),
    State('diario-checkin-sono', 'value'),
    State('diario-checkin-cafe', 'value'),
    State('diario-checkin-hora-dormir', 'value'),
    State('diario-checkin-hora-acordar', 'value'),
    State('diario-checkin-autocuidado', 'value'),
    State('diario-checkin-meditacao', 'value'),
    State('diario-checkin-obs', 'value'),
    prevent_initial_call=True,
)
def salvar_checkin(n_clicks, data_str, sono, cafe, hora_dormir, hora_acordar,
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
    return ('✔ Check-in salvo.', _bloco_gauge_hawkins(date.fromisoformat(data_str)),
            _tabela_checkins(dt.listar_checkins()))


@app.callback(
    Output('diario-radar-roda', 'figure'),
    Output('diario-roda-status', 'children'),
    Output('diario-tabela-roda-vida', 'children'),
    Input('btn-diario-salvar-roda', 'n_clicks'),
    State({'type': 'diario-roda-area', 'index': ALL}, 'value'),
    State({'type': 'diario-roda-area', 'index': ALL}, 'id'),
    State('store-tema', 'data'),
    prevent_initial_call=True,
)
def salvar_roda_vida(n_clicks, valores, ids, tema_atual):
    if not n_clicks:
        return no_update, no_update, no_update
    campos = {id_['index']: valor for id_, valor in zip(ids, valores)}
    dt.criar_registro_roda_vida(date.today(), campos)
    ultimo = dt.obter_ultimo_registro_roda_vida()
    return (_radar_roda_vida(ultimo, tema_atual or 'light'), '✔ Registro salvo.',
            _tabela_roda_vida(dt.listar_registros_roda_vida()))


@app.callback(
    Output('diario-radar-roda', 'figure', allow_duplicate=True),
    Input({'type': 'diario-roda-area', 'index': ALL}, 'value'),
    State({'type': 'diario-roda-area', 'index': ALL}, 'id'),
    State('store-tema', 'data'),
    prevent_initial_call=True,
)
def atualizar_radar_ao_vivo(valores, ids, tema_atual):
    """Ver diario_app_98.py::atualizar_radar_ao_vivo_98 pro mesmo raciocínio
    (preview ao vivo do radar a cada slider movido, sem gravar nada)."""
    campos = {id_['index']: valor for id_, valor in zip(ids, valores)}
    return _radar_roda_vida(campos, tema_atual or 'light')


@app.callback(
    Output('diario-calendario-mes', 'data'),
    Input('btn-diario-cal-mes-prev', 'n_clicks'),
    Input('btn-diario-cal-mes-next', 'n_clicks'),
    Input('btn-diario-cal-ano-prev', 'n_clicks'),
    Input('btn-diario-cal-ano-next', 'n_clicks'),
    State('diario-calendario-mes', 'data'),
    prevent_initial_call=True,
)
def navegar_calendario_classico(n_mes_prev, n_mes_next, n_ano_prev, n_ano_next, mes_atual):
    """Setas de mês e de ano são controles TOTALMENTE separados (feedback
    2026-07-12: um único par de setas reaproveitado pros dois modos —
    trocando de significado conforme `diario-modo-ano` — fazia o usuário
    clicar esperando andar um mês e o calendário pular um ano inteiro,
    3 vezes reportado mesmo depois de rotular os botões dinamicamente. A
    seta de mês SEMPRE anda mês a mês (com rollover de ano) e a de ano
    SEMPRE anda ano a ano, não importa qual grade (`diario-modo-ano`) está
    sendo exibida — `modo_ano` não é mais lido aqui, só decide o que
    `atualizar_periodo_estatisticas` desenha embaixo."""
    ctx = callback_context
    if not ctx.triggered or not mes_atual:
        return no_update
    trigger = ctx.triggered[0]
    if not trigger['value']:
        # Um clique de verdade SEMPRE incrementa `n_clicks` (nunca zera
        # sozinho) — um trigger com valor 0 só pode vir do próprio botão
        # tendo sido recriado (Dash reseta `n_clicks` de um componente
        # novo pra 0), nunca de uma ação do usuário. Guarda defensiva:
        # com os botões agora fixos (nunca recriados, ver
        # `_construir_diario_estatisticas`) isso não deveria mais
        # acontecer, mas custa nada blindar contra o mesmo bug voltando
        # se algum código futuro recriar os botões de novo por engano.
        return no_update
    ano, mes = (int(x) for x in mes_atual.split('-'))
    origem = trigger['prop_id'].split('.')[0]
    if origem in ('btn-diario-cal-mes-prev', 'btn-diario-cal-mes-next'):
        passo = -1 if origem == 'btn-diario-cal-mes-prev' else 1
        mes += passo
        if mes == 0:
            mes, ano = 12, ano - 1
        elif mes == 13:
            mes, ano = 1, ano + 1
    else:
        passo = -1 if origem == 'btn-diario-cal-ano-prev' else 1
        ano += passo
    return f"{ano:04d}-{mes:02d}"


@app.callback(
    Output('diario-modo-ano', 'data'),
    Input('btn-diario-modo-ano', 'n_clicks'),
    State('diario-modo-ano', 'data'),
    prevent_initial_call=True,
)
def alternar_modo_ano(n_clicks, modo_atual):
    if not n_clicks:  # ver comentário em navegar_calendario_classico
        return no_update
    return not bool(modo_atual)


@app.callback(
    Output('diario-cal-config-conteudo', 'children'),
    Output('btn-diario-cal-config', 'children'),
    Input('btn-diario-cal-config', 'n_clicks'),
    State('btn-diario-cal-config', 'children'),
    State('store-calendario-stats-visiveis', 'data'),
    prevent_initial_call=True,
)
def alternar_config_calendario(n_clicks, rotulo_atual, stats_visiveis):
    """Abre/fecha o painel "Display stats" do calendário (item F, fase 4)
    — mesma mecânica do checklist pré-trade: o próprio rótulo do botão é
    o estado, nunca recriado por esta callback."""
    if not n_clicks:
        return no_update, no_update
    if rotulo_atual == '⚙ Stats':
        return _conteudo_config_calendario(stats_visiveis or _STATS_CALENDARIO_PADRAO), '✕ Fechar'
    return None, '⚙ Stats'


@app.callback(
    Output('colapso-analise-estrategia-detalhes', 'opened'),
    Output('btn-analise-estrategia-detalhes', 'children'),
    Input('btn-analise-estrategia-detalhes', 'n_clicks'),
    State('btn-analise-estrategia-detalhes', 'children'),
    prevent_initial_call=True,
)
def alternar_detalhes_analise_estrategia(n_clicks, rotulo_atual):
    """Abre/fecha os detalhes de "Análise de Estratégia" (2026-07-31,
    feedback: "deixe Vencedora/Perdedora e Mentor de IA visíveis, o resto
    contraído") — mesma mecânica de `alternar_config_calendario`: o
    próprio rótulo do botão (seta ▼/▲) é o estado."""
    if not n_clicks:
        return no_update, no_update
    if rotulo_atual == '▼ Ver detalhes':
        return True, '▲ Ocultar detalhes'
    return False, '▼ Ver detalhes'


@app.callback(
    Output('store-calendario-stats-visiveis', 'data'),
    Input('diario-cal-config-itens', 'value'),
    prevent_initial_call=True,
)
def atualizar_stats_visiveis_calendario(valores):
    return valores or []


@app.callback(
    Output('diario-cards-conteudo', 'children'),
    Output('diario-calendario-grid', 'children'),
    Output('diario-calendario-label-mes', 'children'),
    Output('diario-calendario-label-ano', 'children'),
    Output('btn-diario-modo-ano', 'style'),
    Output('diario-modo-ano-bolinha', 'style'),
    Output('diario-resto-periodo-conteudo', 'children'),
    Input('diario-calendario-mes', 'data'),
    Input('diario-modo-ano', 'data'),
    Input('store-calendario-stats-visiveis', 'data'),
    State('store-tema', 'data'),
)
def atualizar_periodo_estatisticas(mes_atual, modo_ano, stats_visiveis, tema_atual):
    """Recalcula estatísticas + calendário a cada troca de mês/ano/toggle —
    ver `diario_app_98.py::atualizar_periodo_estatisticas_98` pro raciocínio
    completo. Só atualiza `children`/`style` de componentes que já existem
    (criados 1x em `_construir_diario_estatisticas`) — NUNCA substitui os
    botões de navegação inteiros, pra não repetir o bug de `n_clicks`
    resetando (ver comentário lá). Os labels de mês/ano são atualizados
    SEMPRE, independente de `modo_ano` — as setas de mês e ano ficam
    visíveis e funcionais nos dois modos de grade (ver
    `navegar_calendario_classico`), só a grade abaixo (dias vs meses) muda.

    `store-calendario-stats-visiveis` (item F, fase 4) também é Input —
    marcar/desmarcar uma stat no calendário precisa remontar a grade na
    hora, sem esperar trocar de mês."""
    if not mes_atual:
        return (no_update,) * 7
    ano, mes = (int(x) for x in mes_atual.split('-'))
    modo_ano = bool(modo_ano)
    if modo_ano:
        stats = dt.calcular_estatisticas(ano=ano)
        grade = _grade_calendario_anual(stats['por_mes'], ano, tema_atual or 'light')
    else:
        stats = dt.calcular_estatisticas(ano=ano, mes=mes)
        detalhe_dias = dt.detalhe_dias_calendario(ano=ano, mes=mes)
        grade = _grade_calendario(stats['por_dia'], detalhe_dias, ano, mes, stats_visiveis, tema_atual or 'light')
    return (
        _cards_ou_vazio_estatisticas(stats, tema_atual or 'light'),
        grade,
        _NOMES_MES[mes],
        str(ano),
        _estilo_toggle_trilho(modo_ano),
        _estilo_toggle_bolinha(modo_ano),
        _resto_periodo_estatisticas(stats, tema_atual or 'light'),
    )


@app.callback(
    Output('store-diario-sub-aba', 'data', allow_duplicate=True),
    Output('diario-hist-filtro-data-calendario', 'data'),
    Input({'type': 'diario-cal-dia', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def clicar_dia_calendario(n_clicks_lista):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update, no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    componente_id = json.loads(prop_id)
    return 'historico', componente_id['index']


@app.callback(
    Output('store-pendencia-editar', 'data'),
    Input({'type': 'diario-btn-editar-pendencia', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def abrir_pendencia_editar(n_clicks_lista):
    """Botão 'Editar' de uma linha PENDENTE no Histórico.

    2026-07-21 (revisão): antes só trocava pra aba Novo Trade sem carregar
    nada — Jesué reportou "vai para a tela de novo trade sem carregar as
    informações já capturadas do trade" e pediu um pop-up com os MESMOS
    campos da aba Posição, pré-carregado com o que o MT5 já capturou.

    Em vez de duplicar formulário/callbacks, materializa a pendência como
    um rascunho de posição (`dt.materializar_pendencia_como_rascunho` —
    idempotente, reaproveita se já existir) e abre o pop-up reaproveitando
    `_bloco_posicao_completo` (mesmo componente que a aba Posição usa pra
    "Pronta pra Finalizar") via `renderizar_overlay_pendencia`.

    Bug real 2026-07-22 ("Editar"/"Finalizar" sem NENHUM efeito, nem visual
    — achado direto no console do navegador): esta callback (e as 3 outras
    que compartilhavam o mesmo padrão — `fechar_pendencia_editar`,
    `finalizar_em_andamento`, `descartar_pendencia_editar`) tentava
    ATUALIZAR `em-andamento-conteudo`/`store-em-andamento-ids-conhecidos`
    incondicionalmente — mas esses ids só existem no DOM quando a sub-aba
    Posição está montada (`montar_sub_aba_diario` troca o conteúdo INTEIRO
    ao trocar de sub-aba, não usa display:none). Como o botão "Editar" só
    existe no Histórico (nunca dá pra estar em Posição E Histórico ao
    mesmo tempo), o Output pra um id inexistente faz o Dash rejeitar a
    callback INTEIRA com `ReferenceError: nonexistent object` — inclusive
    o Output crítico (`store-pendencia-editar`, que abre o pop-up) nunca
    era aplicado. Removido: a Posição já se re-renderiza sozinha, correta,
    na próxima vez que for exibida (`atualizar_em_andamento` já reage a
    `store-diario-sub-aba` mudando de valor + já lê `store-pendencia-
    editar` como State pra excluir o ticket certo) — nunca precisou desse
    empurrão manual daqui."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    componente_id = json.loads(prop_id)
    mt5_position_id = componente_id['index']
    id_pos = dt.materializar_pendencia_como_rascunho(mt5_position_id)
    return no_update if id_pos is None else id_pos


@app.callback(
    Output('store-pendencia-editar', 'data', allow_duplicate=True),
    Input('btn-diario-fechar-pendencia', 'n_clicks'),
    Input('diario-overlay-pendencia-backdrop', 'n_clicks'),
    prevent_initial_call=True,
)
def fechar_pendencia_editar(_n1, _n2):
    """Fecha o pop-up (botão ✕ ou clique no fundo escurecido — mesmo padrão
    de `fechar_imagem_ampliada`). Ver `abrir_pendencia_editar` pro raciocínio
    de por que não atualiza mais `em-andamento-conteudo` diretamente daqui
    (bug real 2026-07-22 — Output pra um id que só existe na sub-aba
    Posição, inexistente enquanto o pop-up do Histórico está aberto,
    derrubava a callback inteira).

    BUG REAL 2026-07-27: os 2 gatilhos (o ✕ e o fundo escurecido) são
    CRIADOS por `renderizar_overlay_pendencia` junto com o pop-up, e essa
    inserção dispara esta callback com `n_clicks=0`. Sem a guarda abaixo,
    `store-pendencia-editar` voltava pra None no mesmo instante em que o
    pop-up abria. Consequência silenciosa: quando o usuário clicava
    "Finalizar e gravar", o `State` dessa store já era None, então
    `finalizar_em_andamento` calculava `fechar_popup = no_update` e o
    pop-up NUNCA fechava — mesmo com o trade gravado certo. Era esse o
    "o pop-up não fecha" reportado. Ver a mesma guarda em
    `descartar_pendencia_editar`/`finalizar_em_andamento`."""
    ctx = callback_context
    if not ctx.triggered or not ctx.triggered[0].get('value'):
        return no_update
    return None


@app.callback(
    Output('store-pendencia-editar', 'data', allow_duplicate=True),
    Output('diario-descartar-pendencia-status', 'children'),
    Output('store-diario-refresh', 'data', allow_duplicate=True),
    Input('btn-diario-descartar-pendencia', 'n_clicks'),
    State('store-pendencia-editar', 'data'),
    State('store-diario-refresh', 'data'),
    prevent_initial_call=True,
)
def descartar_pendencia_editar(n_clicks, id_pos, refresh_atual):
    """Botão "Descartar (não é uma operação real)" do pop-up de edição de
    pendência (ver `renderizar_overlay_pendencia`, só aparece pra rascunhos
    avulsos `fechada_pendente`) — remove o registro órfão via
    `dt.descartar_rascunho_pendencia`, que já recusa sozinha remover uma
    posição `aberta` de verdade ou uma que já tem alguma classificação
    preenchida. Bug real 2026-07-21 que motivou isso: sem essa saída, um
    rascunho aberto no pop-up e nunca finalizado ficava órfão pra sempre
    (ticket 306272514, achado na varredura). Se a função recusar, mantém o
    pop-up aberto e explica o motivo em vez de fingir que descartou.

    Ver `abrir_pendencia_editar` pro raciocínio de por que não atualiza mais
    `em-andamento-conteudo` diretamente daqui (bug real 2026-07-22). O reflexo
    imediato no Histórico (bug real 2026-07-27) é pedido via
    `store-diario-refresh` — id global, nunca `diario-historico-tabela`
    direto; ver o comentário desse store no layout.

    BUG REAL 2026-07-27 (a causa de "Finalizar não faz nada / o pop-up não
    fecha", reportado várias vezes): faltava a guarda de `n_clicks` abaixo.
    Este botão é CRIADO com `n_clicks=0` toda vez que `renderizar_overlay_
    pendencia` monta o pop-up — e essa inserção dispara a callback. Sem a
    guarda, ABRIR o pop-up já chamava `descartar_rascunho_pendencia` e
    APAGAVA o rascunho que o "Editar" tinha acabado de materializar. O
    usuário via o formulário na tela, clicava "Finalizar e gravar", e
    `atualizar_rascunho` não encontrava mais a linha — devolvendo `False`,
    que a UI traduzia como "⚠ Ocupado". Nunca era disputa de trava: o
    registro tinha sido apagado pelo próprio ato de abrir a janela.

    Regra geral que isto vira: callback que APAGA dado nunca pode agir com
    `n_clicks` falsy. `prevent_initial_call` só cobre a carga inicial do
    app, não a inserção dinâmica de um componente novo no layout."""
    if not n_clicks:
        return no_update, no_update, no_update
    if not id_pos:
        return no_update, no_update, no_update
    sucesso = dt.descartar_rascunho_pendencia(id_pos) if not _parse_index_par(id_pos) else True
    if not sucesso:
        return no_update, (
            'Não foi possível descartar — o rascunho já tem alguma classificação '
            'preenchida (nota, tag, resumo etc.) ou não é mais um rascunho pendente. '
            'Finalize normalmente em vez de descartar.'
        ), no_update
    # Além de remover o rascunho, marca o(s) ticket(s) como "não entra no
    # Diário" em definitivo (2026-07-27). Sem isto o descarte durava só até o
    # próximo ciclo de reconciliação: o MT5 continua tendo a operação, então
    # ela voltava pra lista PENDENTE e o botão parecia não ter feito nada.
    dt.ignorar_pendencia(id_pos, motivo='descartado pelo usuário no pop-up de pendência')
    return None, '', (refresh_atual or 0) + 1


@app.callback(
    Output('diario-overlay-pendencia', 'children'),
    Input('store-pendencia-editar', 'data'),
)
def renderizar_overlay_pendencia(id_pos):
    """Constrói o conteúdo do pop-up a partir do `id_pos` guardado em
    `store-pendencia-editar` — pode ser um ticket avulso OU um par de
    hedge composto "ticket_a-ticket_b" (ver `dt.listar_pendencias_
    historico`/`materializar_pendencia_como_rascunho`), cada um com uma
    forma diferente de virar o dict que `_bloco_posicao_completo` espera."""
    if not id_pos:
        return None
    posicoes = dt.listar_posicoes_abertas()
    if posicoes is None or posicoes.empty:
        return None

    par = _parse_index_par(id_pos)
    if par:
        pares = dt.detectar_pares_hedge(posicoes)
        par_match = next((p for p in pares if p['ticket_a'] == par[0] and p['ticket_b'] == par[1]), None)
        if par_match is None:
            return None
        pos = dt.montar_posicao_sintetica_par(par_match, posicoes)
    else:
        linha = posicoes[posicoes['id_posicao'] == id_pos]
        if linha.empty:
            return None
        pos = linha.iloc[0].to_dict()

    imagens = _lista_segura(pos.get('imagens_em_andamento'))
    # "Descartar" = "esta operação não entra no Diário". Vale pra qualquer
    # pendência JÁ FECHADA, inclusive par de hedge (2026-07-27): além de
    # remover o rascunho, `ignorar_pendencia` grava o(s) ticket(s) em
    # `tickets_ignorados.parquet` pra não voltarem na próxima reconciliação.
    # Antes só aparecia pra ticket avulso, então uma operação que o usuário
    # não queria registrar não tinha como ser recusada — só finalizando.
    #
    # Continua escondido pra posição AINDA ABERTA: ali não há o que descartar,
    # a operação está viva no mercado.
    mostrar_descartar = pos.get('status') == dt.STATUS_POSICAO_FECHADA_PENDENTE
    botoes = [
        html.Button('✕ Fechar', id='btn-diario-fechar-pendencia', n_clicks=0, style={
            'background': 'transparent', 'color': C['text'], 'border': f'1px solid {C["border"]}',
            'borderRadius': '6px', 'padding': '4px 12px', 'cursor': 'pointer', 'flexShrink': '0',
        }),
    ]
    if mostrar_descartar:
        botoes.append(html.Button('Descartar (não é uma operação real)', id='btn-diario-descartar-pendencia', n_clicks=0, style={
            'background': 'transparent', 'color': C['red'], 'border': f'1px solid {C["red"]}',
            'borderRadius': '6px', 'padding': '4px 12px', 'cursor': 'pointer', 'flexShrink': '0', 'marginLeft': '8px',
        }))
    conteudo = html.Div([
        html.Div(botoes, style={'display': 'flex', 'alignItems': 'center'}),
        html.Div(id='diario-descartar-pendencia-status', style={'fontSize': '12px', 'color': C['red'], 'marginTop': '4px'}),
        html.Div(_bloco_posicao_completo(pos, imagens), style={'overflowY': 'auto', 'marginTop': '10px'}),
    ], style={'display': 'flex', 'flexDirection': 'column', 'height': '100%'})
    return _overlay(conteudo, 'diario-overlay-pendencia-backdrop')


# ---------------------------------------------------------------------------
# Editar operação JÁ GRAVADA (botão ✎ do Histórico) — 2026-07-30
# ---------------------------------------------------------------------------
# Fluxo inteiro passa por 3 ids GLOBAIS (`store-trade-editar`,
# `diario-overlay-trade`, `store-diario-refresh`), nunca por
# `diario-historico-tabela`/`diario-hist-*` — ver o comentário de
# `store-diario-refresh` no layout pro motivo (callback que referencia id
# desmontado é rejeitada INTEIRA, em silêncio).

@app.callback(
    Output('store-trade-editar', 'data'),
    Input({'type': 'diario-btn-editar-trade', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def abrir_trade_editar(n_clicks_lista):
    """Botão ✎ de uma linha JÁ FINALIZADA do Histórico. Só guarda o
    `id_trade` na store; quem monta o formulário é
    `renderizar_overlay_trade`."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    return json.loads(prop_id)['index']


@app.callback(
    Output('store-trade-editar', 'data', allow_duplicate=True),
    Input('btn-diario-fechar-trade', 'n_clicks'),
    Input('diario-overlay-trade-backdrop', 'n_clicks'),
    prevent_initial_call=True,
)
def fechar_trade_editar(_n1, _n2):
    """Fecha o pop-up (✕ ou clique no fundo). A guarda de `value` é
    obrigatória: os dois gatilhos são CRIADOS com `n_clicks=0` junto com o
    pop-up, e essa inserção dispara a callback — sem ela, a store voltava
    pra None no mesmo instante em que abria (bug real de 2026-07-27 em
    `fechar_pendencia_editar`, mesma armadilha)."""
    ctx = callback_context
    if not ctx.triggered or not ctx.triggered[0].get('value'):
        return no_update
    return None


def _bloco_editar_trade(trade: pd.Series) -> html.Div:
    """Formulário do pop-up de edição. Mostra os fatos da execução como
    texto SÓ-LEITURA no cabeçalho (preço, resultado, saldo, data) e deixa
    editável apenas o que é leitura/opinião do trade — a mesma fronteira
    que `dt.CAMPOS_EDITAVEIS_TRADE` impõe do lado do motor."""
    saldo = trade.get('saldo_operacao_reais')
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0
    cor_saldo = C['green'] if saldo_num > 0 else (C['red'] if saldo_num < 0 else C['text2'])
    pontos = trade.get('resultado_pontos')
    cabecalho = (f"{trade.get('data_op')} · {trade.get('ativo')} · "
                 f"{trade.get('tipo_operacao') or '—'}")
    if pd.notna(pontos):
        cabecalho += f" · {float(pontos):+.0f} pts"

    def campo(rotulo, componente):
        return html.Div([
            html.Div(rotulo, style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '4px'}),
            componente,
        ], style={'marginBottom': '12px'})

    est_atuais = _lista_segura(trade.get('estrategia_tags'))
    erro_atuais = _lista_segura(trade.get('tags_setup_erro'))
    emo_atuais = _lista_segura(trade.get('tags_emocionais'))

    return html.Div([
        html.Div([
            html.Span(f"#{int(trade['id_trade'])}", style={
                'fontFamily': FONTS['mono'], 'fontSize': '16px', 'fontWeight': '700', 'marginRight': '10px'}),
            html.Span(cabecalho, style={'fontSize': '12px', 'color': C['text2']}),
            html.Span(f"  R$ {saldo_num:+.2f}", style={
                'fontFamily': FONTS['mono'], 'fontSize': '14px', 'fontWeight': '700', 'color': cor_saldo}),
        ], style={'marginBottom': '4px'}),
        html.Div('Execução (preço, resultado, saldo, data) vem do MT5 e não é editável aqui.',
                 style={'fontSize': '10px', 'color': C['text2'], 'fontStyle': 'italic', 'marginBottom': '14px'}),

        campo('Estratégia', dcc.Dropdown(id='diario-edit-estrategia', multi=True,
                                          options=_opcoes_com_valores_atuais(_opcoes_estrategias(), est_atuais),
                                          value=est_atuais,
                                          className='classico-dropdown classico-dropdown-tags')),
        html.Div([
            html.Div(campo('Janela do pregão', dcc.Dropdown(
                id='diario-edit-janela', options=_opcoes_janelas(),
                value=trade.get('janela_pregao') if pd.notna(trade.get('janela_pregao')) else None,
                className='classico-dropdown')), style={'flex': '1'}),
            html.Div(campo('Sala/Análise', dcc.Dropdown(
                id='diario-edit-sala', options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
                value=trade.get('sala_analise') if pd.notna(trade.get('sala_analise')) else None,
                className='classico-dropdown')), style={'flex': '1'}),
        ], style={'display': 'flex', 'gap': '10px'}),
        html.Div([
            html.Div(campo('Nível S/R (preço)', dcc.Input(
                id='diario-edit-nivel-sr', type='text', inputMode='decimal',
                value=trade.get('nivel_sr_referencia') if pd.notna(trade.get('nivel_sr_referencia')) else None,
                style=_estilo_input_diario())), style={'flex': '1'}),
            html.Div(campo('Nível S/R (tipo)', dcc.Input(
                id='diario-edit-nivel-sr-tipo', type='text', placeholder='ex: suporte',
                value=trade.get('nivel_sr_tipo') if pd.notna(trade.get('nivel_sr_tipo')) else None,
                style=_estilo_input_diario())), style={'flex': '1'}),
        ], style={'display': 'flex', 'gap': '10px'}),

        campo('Resumo Operacional', dcc.Textarea(
            id='diario-edit-resumo', rows=4,
            value=trade.get('resumo_operacional') if pd.notna(trade.get('resumo_operacional')) else '',
            style=_estilo_input_diario({'resize': 'vertical', 'height': 'auto'}))),
        campo('Obs (autocoaching)', dcc.Textarea(
            id='diario-edit-obs', rows=2,
            value=trade.get('obs') if pd.notna(trade.get('obs')) else '',
            style=_estilo_input_diario({'resize': 'vertical', 'height': 'auto'}))),
        campo('Tags de Setup/Erro', dcc.Dropdown(
            id='diario-edit-tags-erro', multi=True,
            options=_opcoes_com_valores_atuais(_opcoes_tags_setup_erro(), erro_atuais),
            value=erro_atuais,
            className='classico-dropdown classico-dropdown-tags')),
        campo('Print Emocional', dcc.Dropdown(
            id='diario-edit-tags-emocionais', multi=True,
            options=_opcoes_com_valores_atuais(_opcoes_tags_emocionais(), emo_atuais),
            value=emo_atuais,
            className='classico-dropdown classico-dropdown-tags')),
    ])


@app.callback(
    Output('diario-overlay-trade', 'children'),
    Input('store-trade-editar', 'data'),
)
def renderizar_overlay_trade(id_trade):
    if not id_trade:
        return None
    df = dt.listar_trades()
    linha = df[df['id_trade'] == int(id_trade)]
    if linha.empty:
        return None
    conteudo = html.Div([
        html.Div([
            html.Button('✕ Fechar', id='btn-diario-fechar-trade', n_clicks=0, style={
                'background': 'transparent', 'color': C['text'], 'border': f'1px solid {C["border"]}',
                'borderRadius': '6px', 'padding': '4px 12px', 'cursor': 'pointer', 'flexShrink': '0',
            }),
            html.Button('Salvar alterações', id='btn-diario-salvar-trade', n_clicks=0, style={
                'background': C['green'], 'color': C['bg'], 'border': 'none', 'fontWeight': '700',
                'borderRadius': '6px', 'padding': '4px 14px', 'cursor': 'pointer',
                'flexShrink': '0', 'marginLeft': '8px',
            }),
            html.Span(id='diario-editar-trade-status',
                      style={'fontSize': '12px', 'color': C['red'], 'marginLeft': '10px'}),
        ], style={'display': 'flex', 'alignItems': 'center'}),
        html.Div(_bloco_editar_trade(linha.iloc[0]),
                 style={'overflowY': 'auto', 'marginTop': '10px', 'padding': '14px',
                        'background': C['panel'], 'borderRadius': '8px',
                        'border': f'1px solid {C["border"]}'}),
    ], style={'display': 'flex', 'flexDirection': 'column', 'height': '100%'})
    return _overlay(conteudo, 'diario-overlay-trade-backdrop')


@app.callback(
    Output('store-trade-editar', 'data', allow_duplicate=True),
    Output('diario-editar-trade-status', 'children'),
    Output('store-diario-refresh', 'data', allow_duplicate=True),
    Input('btn-diario-salvar-trade', 'n_clicks'),
    State('store-trade-editar', 'data'),
    State('diario-edit-estrategia', 'value'),
    State('diario-edit-janela', 'value'),
    State('diario-edit-sala', 'value'),
    State('diario-edit-nivel-sr', 'value'),
    State('diario-edit-nivel-sr-tipo', 'value'),
    State('diario-edit-resumo', 'value'),
    State('diario-edit-obs', 'value'),
    State('diario-edit-tags-erro', 'value'),
    State('diario-edit-tags-emocionais', 'value'),
    State('store-diario-refresh', 'data'),
    prevent_initial_call=True,
)
def salvar_trade_editado(n_clicks, id_trade, estrategias, janela, sala, nivel_sr, nivel_sr_tipo,
                         resumo, obs, tags_erro, tags_emocionais, refresh_atual):
    """Grava a edição, fecha o pop-up e pede o refresh da tabela.

    Guarda de `n_clicks` obrigatória (regra fixada em 2026-07-27): o botão é
    criado com `n_clicks=0` toda vez que o pop-up é montado, e essa inserção
    dispara a callback — sem a guarda, ABRIR o formulário já gravaria por
    cima do trade com o que estivesse na tela. Aqui seria pior que no caso
    original: os `State` de um pop-up recém-montado ainda podem estar None,
    então o "save fantasma" APAGARIA a classificação existente."""
    if not n_clicks or not id_trade:
        return no_update, no_update, no_update
    campos = {
        'estrategia_tags': estrategias or [],
        'janela_pregao': janela,
        'sala_analise': sala,
        'nivel_sr_referencia': _num(nivel_sr),
        'nivel_sr_tipo': (nivel_sr_tipo or '').strip() or None,
        'resumo_operacional': (resumo or '').strip() or None,
        'obs': (obs or '').strip() or None,
        'tags_setup_erro': tags_erro or [],
        'tags_emocionais': tags_emocionais or [],
    }
    if not dt.atualizar_trade(int(id_trade), campos):
        return no_update, '⚠ Ocupado — não foi possível gravar. Tente de novo.', no_update
    _log_diario.info('trade #%s editado no Histórico — estratégias: %s',
                     id_trade, campos['estrategia_tags'])
    return None, '', (refresh_atual or 0) + 1


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
    return _tabela_padroes(resultado, ja_rodou=True), f'Concluído — {len(resultado)} padrões detectados.'


@app.callback(
    Output('em-andamento-conteudo', 'children'),
    Output('store-em-andamento-ids-conhecidos', 'data'),
    Input('interval-em-andamento', 'n_intervals'),
    Input('store-diario-sub-aba', 'data'),
    Input('store-posicao-ativo-subaba', 'data'),
    State('store-em-andamento-ids-conhecidos', 'data'),
    State('store-pendencia-editar', 'data'),
)
def atualizar_em_andamento(n, sub_aba_ativa, ativo_selecionado, ids_conhecidos, pendencia_aberta):
    """Feedback 2026-07-20: "após o trade ser finalizado na plataforma, a
    seção não apareceu automaticamente, tive que mudar de aba e entrar de
    novo" — E "o formulário deve ficar logo abaixo do bloco do trade
    aberto" (ver `_bloco_posicao_completo`). Isso juntou os dois Outputs
    antigos (`em-andamento-conteudo` só-leitura + `em-andamento-
    formularios` editável, nunca tocado pelo interval de propósito — ver
    histórico de `_renderizar_formularios_em_andamento`) num só, agora
    guiado pelo MESMO interval de 6s.

    Isso reintroduziria o bug do "Neutro piscando" (2026-07-11: um campo
    reconstruído com `value=` fresco do disco sobrescreve o que o usuário
    está digitando) se reconstruísse TUDO a cada tick — mas com o
    auto-save contínuo (2026-07-20, cada campo salva sozinho ao mudar) já
    não existe mais "edição não salva" pra perder na maioria do tempo. A
    salvaguarda que resta: só reconstrói de verdade quando o CONJUNTO de
    posições rastreadas muda de verdade (posição nova, fechou, virou par)
    — comparado via `_ids_rastreados` contra o último render. Enquanto
    nada muda, um tick do interval não toca a árvore de componentes."""
    ctx = callback_context
    disparado_por_interval = bool(ctx.triggered) and ctx.triggered[0]['prop_id'] == 'interval-em-andamento.n_intervals'
    if sub_aba_ativa != 'posicao':
        return no_update, no_update

    posicoes = dt.listar_posicoes_abertas()
    # `excluir_id` (2026-07-21): se uma pendência estiver aberta no pop-up
    # do Histórico (ver `abrir_pendencia_editar`), nunca deixa ela também
    # renderizar aqui — evita 2 componentes com o MESMO id pattern-matching
    # montados ao mesmo tempo (esta aba nunca desmonta, só display:none).
    _, por_ativo_abertas, por_ativo_prontas = _preparar_registros_posicao(posicoes, excluir_id=pendencia_aberta)
    ativo = ativo_selecionado or 'WIN'
    ids_agora = _ids_rastreados(_registros_por_ativo(por_ativo_prontas, ativo),
                                 _registros_por_ativo(por_ativo_abertas, ativo))
    if disparado_por_interval and ids_agora == (ids_conhecidos or []):
        return no_update, no_update
    return _renderizar_posicao_completa(posicoes, dt.listar_ordens_pendentes(), ativo, excluir_id=pendencia_aberta), ids_agora


@app.callback(
    Output('store-posicao-ativo-subaba', 'data'),
    Input('btn-posicao-ativo-WIN', 'n_clicks'),
    Input('btn-posicao-ativo-WDO', 'n_clicks'),
    prevent_initial_call=True,
)
def selecionar_subaba_posicao(n_win, n_wdo):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    id_disparado = ctx.triggered[0]['prop_id'].split('.')[0]
    return 'WIN' if id_disparado == 'btn-posicao-ativo-WIN' else 'WDO'


@app.callback(
    Output('btn-posicao-ativo-WIN', 'style'),
    Output('btn-posicao-ativo-WDO', 'style'),
    Input('store-posicao-ativo-subaba', 'data'),
)
def atualizar_estilo_subaba_posicao(ativo_ativo):
    return _estilo_subaba_diario('WIN', ativo_ativo), _estilo_subaba_diario('WDO', ativo_ativo)



@app.callback(
    Output({'type': 'ea-nota', 'index': dash.MATCH}, 'value'),
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Input({'type': 'ea-nota', 'index': dash.MATCH}, 'n_blur'),
    State({'type': 'ea-nota', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-nota', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def salvar_nota_em_andamento(_n_blur, nota, id_componente):
    """Salva a nota (append no log cronológico) sozinha ao sair do campo
    (`n_blur`) e limpa a caixa depois — sem botão "Salvar" (feedback
    2026-07-20). Limpar após salvar evita reenviar a MESMA nota de novo se
    o usuário só clicar pra fora e voltar sem editar (o campo é sempre
    "escreva uma NOVA anotação", nunca um texto persistente editável)."""
    if not nota or not nota.strip():
        return no_update, no_update
    ok = dt.atualizar_rascunho(_ticket_alvo(id_componente['index']), nota_texto=nota)
    if not ok:
        return no_update, '⚠ Ocupado — tente salvar a nota de novo.'
    return '', '✔ Nota salva.'


@app.callback(
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children'),
    Input({'type': 'ea-estrategia', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-sala', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-alvo', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-stop', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-nivel-sr', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-resumo', 'index': dash.MATCH}, 'n_blur'),
    Input({'type': 'ea-emo-confianca', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-emo-humor', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-emo-energia', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-emo-atencao', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-emo-presenca', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-tags-emocionais', 'index': dash.MATCH}, 'data'),
    Input({'type': 'ea-sentimento-mercado', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-resumo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-estrategia', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def auto_salvar_campos_em_andamento(estrategia, sala, alvo, stop, nivel_sr, _resumo_blur,
                                     emo_confianca, emo_humor, emo_energia, emo_atencao, emo_presenca,
                                     tags_emocionais, sentimento_mercado,
                                     resumo, id_componente):
    """Substitui o antigo botão "Salvar" (feedback 2026-07-20: "qualquer
    sistema consegue salvar tudo imediatamente ao inserir as informações")
    — cada campo de classificação grava sozinho assim que muda (Alvo/Stop/
    Nível S/R têm `debounce=True`, disparam só ao sair do campo/Enter;
    Resumo — `dcc.Textarea` não tem `debounce` — usa `n_blur`; dropdowns/
    estrelas/slider são seleções discretas, já disparam só 1x por escolha).

    Achado real (2026-07-20): um trade fechado (par hedge) virou "Pronta
    pra Finalizar" com Estratégia/Resumo/Estrelas todos em branco — o
    usuário tinha preenchido tudo isso enquanto a posição ainda estava
    `aberta`, mas sem nenhum auto-save o dado nunca chegou a ser gravado;
    quando a posição fechou e o card foi reconstruído do disco, não havia
    nada lá pra mostrar. Não é bug de "amarração" entre ordens (ticket_a/
    ticket_b de `detectar_pares_hedge`/`montar_posicao_sintetica_par`
    concordam de forma independente sobre qual perna é a entrada, e
    `sincronizar_posicoes_abertas` só faz `.update()` nos campos vindos do
    MT5 — nunca toca `*_preliminar`/notas/imagens) — era ausência total de
    auto-save."""
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

    # Tag digitada na hora (ver `assets/tag_input.js`) só existe como chip
    # temporário no widget — vira sugestão permanente de verdade (cadastro
    # em `tags_emocionais.parquet`) só agora, quando a posição é
    # efetivamente salva, não a cada tecla digitada. Tag nova (vocabulário
    # aberto, não bate com nenhuma já cadastrada) usa o próprio texto
    # digitado como nome — o widget não distingue mais value/label
    # separados pra tags livres (ver `_campo_tag_widget`).
    if tags_emocionais:
        slugs_cadastrados = set(dt.listar_tags_emocionais()['slug'])
        for slug in tags_emocionais:
            if slug not in slugs_cadastrados:
                dt.criar_tag_emocional(slug)

    ok = dt.atualizar_rascunho(_ticket_alvo(id_componente['index']), campos=campos)
    return '✔ Salvo automaticamente.' if ok else '⚠ Ocupado — tente de novo.'


@app.callback(
    Output({'type': 'ea-sentimento-label', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea-sentimento-label', 'index': dash.MATCH}, 'style'),
    Input({'type': 'ea-sentimento-mercado', 'index': dash.MATCH}, 'value'),
)
def atualizar_label_sentimento_posicao(valor):
    resultado = dt.classificar_sentimento_mercado(valor)
    if resultado is None:
        return '—', {'fontSize': '12px', 'color': C['text2']}
    nome, cor_chave = resultado
    return nome, {'fontSize': '12px', 'fontWeight': '700', 'color': C.get(cor_chave, C['text'])}


@app.callback(
    Output({'type': 'ea-alvo', 'index': dash.MATCH}, 'value'),
    Output({'type': 'ea-stop', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-rr-preset', 'index': dash.MATCH}, 'value'),
    prevent_initial_call=True,
)
def aplicar_rr_preset_posicao(rr_valor):
    preset = dt.obter_rr_preset(rr_valor)
    if preset is None:
        return no_update, no_update
    return preset['alvo_pontos'], preset['stop_pontos']


@app.callback(
    Output({'type': 'ea-rr-preset', 'index': dash.MATCH}, 'value', allow_duplicate=True),
    Input({'type': 'ea-alvo', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-stop', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-rr-preset', 'index': dash.MATCH}, 'value'),
    prevent_initial_call=True,
)
def resetar_rr_posicao_se_editado(alvo, stop, rr_atual):
    if not rr_atual:
        return no_update
    preset = dt.obter_rr_preset(rr_atual)
    if preset is None:
        return no_update
    if _num(alvo) == preset['alvo_pontos'] and _num(stop) == preset['stop_pontos']:
        return no_update
    return None


@app.callback(
    Output({'type': 'ea-rr-calculado', 'index': dash.MATCH}, 'children'),
    Input({'type': 'ea-alvo', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-stop', 'index': dash.MATCH}, 'value'),
)
def calcular_rr_em_andamento(alvo, stop):
    """R:R calculado sozinho a partir de Alvo/Stop (feedback 2026-07-20) —
    complementa o dropdown de presets acima (que faz o inverso: escolhe um
    R:R salvo e PREENCHE Alvo/Stop)."""
    alvo_num, stop_num = _num(alvo), _num(stop)
    if not alvo_num or not stop_num:
        return '—'
    return f'{alvo_num / stop_num:.2f} : 1'


@app.callback(
    Output({'type': 'ea-preview-imagem', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Input({'type': 'ea-upload-imagem', 'index': dash.MATCH}, 'contents'),
    Input('store-diario-imagem-colada', 'data'),
    State('store-diario-posicao-ativa-print', 'data'),
    State({'type': 'ea-upload-imagem', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def preview_e_anexar_imagem_em_andamento(upload_contents, imagem_colada, posicao_ativa, id_componente):
    """Mostra a preview E anexa à galeria na hora, sem clique em "Anexar"
    nenhum (feedback 2026-07-20: "não faz sentido eu adicionar a imagem, e
    depois clicar no botão para adicionar no registro")."""
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    if origem == 'store-diario-imagem-colada':
        if posicao_ativa != id_componente['index']:
            return no_update, no_update  # Ctrl+V é global — só reage no card armado
        data_url = imagem_colada
    else:
        data_url = upload_contents
    if not data_url:
        return no_update, no_update

    preview = html.Img(src=data_url, style={'maxWidth': '200px', 'borderRadius': '6px',
                                             'border': f'1px solid {C["border"]}'})
    try:
        imagem_bytes = base64.b64decode(data_url.split(',', 1)[1])
    except Exception:
        return preview, '⚠ Imagem inválida — não foi possível anexar.'
    # Par de hedge (ver _ticket_alvo): a galeria/imagem vive na perna de
    # ENTRADA, mesma decisão de onde as notas/tags preliminares ficam.
    ok = dt.atualizar_rascunho(_ticket_alvo(id_componente['index']), imagem_bytes=imagem_bytes)
    return preview, ('✔ Print anexado automaticamente.' if ok else '⚠ Ocupado — tente colar/enviar de novo.')


@app.callback(
    Output({'type': 'ea-galeria-live', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea-campo-imagem-principal', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Input({'type': 'ea-imagem-remover', 'index': dash.MATCH, 'nome': ALL}, 'n_clicks'),
    State({'type': 'ea-imagem-remover', 'index': dash.MATCH, 'nome': ALL}, 'id'),
    prevent_initial_call=True,
)
def remover_imagem_do_rascunho(n_clicks_lista, ids):
    """"×" de uma miniatura de print (pedido 2026-07-27) — tira aquele print
    do rascunho via `dt.remover_imagem_rascunho` e reconstrói as DUAS galerias
    do card (a pequena do bloco só-leitura e a de escolha de capa).

    Escreve só em ids `MATCH` do próprio card, nunca num id global ou de outra
    aba: este botão é renderizado tanto na sub-aba Posição quanto no pop-up do
    Histórico, e um Output fora do card quebraria num dos dois contextos (a
    armadilha recorrente deste arquivo — ver `store-diario-refresh` no layout).

    A guarda de `n_clicks` é obrigatória: reconstruir as galerias recria estes
    próprios botões com `n_clicks=0`, e essa inserção redispara a callback —
    com todos zerados, `any(...)` é False e ela para aqui (mesma regra de
    `descartar_pendencia_editar`, que já apagou dado por não ter isso)."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update, no_update, no_update

    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    componente_id = json.loads(prop_id)
    id_pos, nome = componente_id['index'], componente_id['nome']

    if not dt.remover_imagem_rascunho(_ticket_alvo(id_pos), nome):
        return no_update, no_update, '⚠ Não foi possível remover o print — tente de novo.'

    df = dt.listar_posicoes_abertas()
    linha = df[df['id_posicao'] == _ticket_alvo(id_pos)] if df is not None and not df.empty else None
    imagens = _lista_segura(linha.iloc[0].get('imagens_em_andamento')) if linha is not None and not linha.empty else []
    return (_thumbs_galeria_live(id_pos, imagens),
            _conteudo_imagem_principal(id_pos, imagens),
            '✔ Print removido.')


@app.callback(
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Output({'type': 'ea-status-finalizar', 'index': dash.MATCH}, 'children'),
    Output('store-pendencia-editar', 'data', allow_duplicate=True),
    Output('store-diario-refresh', 'data', allow_duplicate=True),
    Input({'type': 'ea-btn-finalizar', 'index': dash.MATCH}, 'n_clicks'),
    State({'type': 'ea-nota', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-estrategia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-sala', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-alvo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-stop', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-nivel-sr', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-resumo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-emo-confianca', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-emo-humor', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-emo-energia', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-emo-atencao', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-emo-presenca', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-tags-emocionais', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea-sentimento-mercado', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-tags-erro', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-imagem-principal', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea-upload-imagem', 'index': dash.MATCH}, 'contents'),
    State('store-diario-imagem-colada', 'data'),
    State('store-diario-posicao-ativa-print', 'data'),
    State({'type': 'ea-preco-saida-correcao', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-datahora-saida-correcao', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-btn-finalizar', 'index': dash.MATCH}, 'id'),
    State('store-pendencia-editar', 'data'),
    State('store-diario-refresh', 'data'),
    prevent_initial_call=True,
)
def finalizar_em_andamento(n_clicks, nota, estrategia, sala, alvo, stop, nivel_sr, resumo,
                            emo_confianca, emo_humor, emo_energia, emo_atencao, emo_presenca,
                            tags_emocionais, sentimento_mercado, tags_erro, imagem_principal,
                            upload_contents, imagem_colada, posicao_ativa,
                            preco_saida_correcao, datahora_saida_correcao, id_componente,
                            pendencia_aberta, refresh_atual):
    """Encerra o rascunho e grava o trade definitivo. Pra um ticket avulso,
    chama `diario_trade.finalizar_posicao`; pra um PAR de hedge (ver
    `_parse_index_par` — `index` composto "ticket_a-ticket_b", achado real
    2026-07-15: SL/TP via OCO gera posição oposta nova em vez de fechar a
    original), chama `diario_trade.finalizar_par_hedge`, que grava 1 único
    trade líquido em vez de 2 linhas quebradas. `tipo_saida` NÃO entra mais
    aqui — derivado sozinho a partir do MT5.

    Bug real 2026-07-22 ("Finalizar" sem NENHUM efeito, nem visual — achado
    direto no console do navegador): esta callback tentava atualizar
    `em-andamento-conteudo`/`store-em-andamento-ids-conhecidos`
    incondicionalmente, mas esse id só existe no DOM quando a sub-aba
    Posição está montada — quando "Finalizar" é clicado de dentro do pop-up
    de pendência do Histórico (`renderizar_overlay_pendencia`), esse id
    NÃO existe, e o Dash rejeita a callback INTEIRA com `ReferenceError:
    nonexistent object` (nada acontece, nem o Output crítico que fecharia
    o pop-up). Removidos os 2 Outputs pra Posição — ela já se re-renderiza
    sozinha, correta, na próxima vez que for exibida
    (`atualizar_em_andamento` já reage a `store-diario-sub-aba` mudando +
    já lê `store-pendencia-editar` como State). Ver `abrir_pendencia_editar`
    pro mesmo raciocínio completo.

    Feedback 2026-07-17 (mais cedo): "não há necessidade de tantos
    botões... o salvamento tem que ser automático, apenas um botão". Antes,
    resumo/estrelas/tags/nota/print só entravam no trade se o usuário
    clicasse "Salvar" e "Anexar à galeria" ANTES de "Finalizar" — esse
    clique já tinha sumido da tela (ver `_bloco_posicao_form`), mas o antigo
    `finalizar_em_andamento` só lia o que já estava gravado em disco, então
    qualquer edição feita neste último passo sem ter clicado nos botões
    extras era silenciosamente perdida. Agora este único clique chama
    `atualizar_rascunho` primeiro (mesma função que os botões antigos
    chamavam, auto-salvando nota/classificação/print) e só then finaliza —
    nada se perde.

    Bug real 2026-07-27 ("Finalizar" grava certo, mas some do UX até sair
    e voltar da aba): esta callback não tinha NENHUMA ligação com
    `diario-historico-tabela`, então a linha PENDENTE continuava na tela até
    o usuário trocar de aba e voltar. A primeira tentativa de correção
    adicionou Outputs/States apontando direto pra `diario-historico-tabela` e
    pros filtros `diario-hist-*` — o que reintroduziu exatamente a armadilha
    descrita no parágrafo acima, só que espelhada: esses ids só existem na
    aba Histórico, e este botão também é renderizado a partir da Posição.
    Agora o pedido de atualização passa por `store-diario-refresh` (id
    GLOBAL, sempre montado) e quem remonta a tabela é a dona dela,
    `atualizar_historico`. Esta callback voltou a depender só de ids globais
    e dos `ea-*` do próprio card — válida em qualquer aba, hoje e depois."""
    # Guarda obrigatória (bug real 2026-07-27, ver
    # `descartar_pendencia_editar`): este botão é criado com `n_clicks=0` toda
    # vez que o pop-up é montado, e a inserção dispara a callback.
    # `prevent_initial_call` só cobre a carga inicial do app, não a inserção
    # dinâmica. Sem isto, ABRIR o pop-up já podia gravar o trade sozinho.
    if not n_clicks:
        return no_update, no_update, no_update, no_update

    id_pos = id_componente['index']
    par = _parse_index_par(id_pos)
    ticket_alvo = _ticket_alvo(id_pos)

    def responder(msg, fechar=no_update, refresh=no_update):
        """A MESMA mensagem vai pros 2 destinos: `ea-status` (junto da Nota,
        onde o resto do formulário já reporta) e `ea-status-finalizar` (colado
        no botão, onde o usuário está olhando quando clica)."""
        return msg, msg, fechar, refresh

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
            return responder('⚠ Arquivo ocupado — clique de novo em instantes '
                             '(nada foi perdido).')

    # Sem seleção manual de "Imagem principal", a última imagem da galeria
    # vira a capa automaticamente — cobre tanto o print colado NESTE clique
    # quanto um já anexado antes, enquanto a posição ainda estava aberta
    # (achado real 2026-07-20: um print anexado durante o trade caía em
    # `imagens_adicionais` em vez de virar a capa, por só considerar o
    # anexado NA HORA do finalizar).
    if imagem_principal is None:
        df_atual = dt.listar_posicoes_abertas()
        linha_atual = df_atual[df_atual['id_posicao'] == ticket_alvo]
        if not linha_atual.empty:
            imagens_atuais = _lista_segura(linha_atual.iloc[0].get('imagens_em_andamento'))
            if imagens_atuais:
                imagem_principal = imagens_atuais[-1]

    # Só entra no dict quem tem valor de verdade — se o usuário não mexeu
    # num campo (None), o default `*_preliminar` já escrito acima (ou
    # durante a operação) não é sobrescrito por engano com uma lista/valor
    # vazio.
    campos_classificacao = {'conta': 'real'}
    if estrategia is not None:
        campos_classificacao['estrategia_tags'] = estrategia
    if sala is not None:
        campos_classificacao['sala_analise'] = sala
    if tags_erro is not None:
        campos_classificacao['tags_setup_erro'] = tags_erro

    if par:
        # Par de hedge: a saída já é o preço REAL da perna oposta (não uma
        # estimativa) — não há o que corrigir, `corrigir_saida_rascunho`
        # nem se aplica (não existe uma linha só com esse índice composto).
        ticket_a, ticket_b = par
        id_trade = dt.finalizar_par_hedge(ticket_a, ticket_b, campos_classificacao, imagem_principal=imagem_principal)
    else:
        # Grava a correção de saída (se houver) ANTES de finalizar — ver
        # diario_app_98.py::finalizar_em_andamento_98 pro mesmo raciocínio
        # (nunca deixar `finalizar_posicao` gravar o valor fabricado do
        # fallback de `sincronizar_posicoes_abertas` sem essa confirmação).
        preco_corrigido = _num(preco_saida_correcao)
        if preco_corrigido is not None:
            datahora_corrigida = pd.to_datetime(datahora_saida_correcao, errors='coerce') if datahora_saida_correcao else None
            if datahora_corrigida is None or pd.isna(datahora_corrigida):
                return responder('⚠ Data/Hora de Saída inválida — use o formato AAAA-MM-DD HH:MM.')
            if not dt.corrigir_saida_rascunho(id_pos, preco_corrigido, datahora_corrigida):
                return responder('⚠ Arquivo ocupado — clique de novo em instantes.')
        id_trade = dt.finalizar_posicao(id_pos, campos_classificacao, imagem_principal=imagem_principal)

    if id_trade is None:
        # Pode ser trava ocupada OU a trava de idempotência de `diario_trade`
        # recusando um ticket já finalizado antes (bug real 2026-07-27). Nos
        # dois casos fecha o pop-up e pede refresh: se já virou trade, insistir
        # nesta tela não leva a lugar nenhum — a lista embaixo vai mostrar a
        # verdade.
        _log_diario.warning('Finalizar não gravou nada para id_pos=%s '
                            '(já finalizado antes ou trava ocupada).', id_pos)
        fechar = None if pendencia_aberta is not None else no_update
        return responder('⚠ Não foi possível finalizar — talvez já tenha sido gravada antes. '
                         'Confira a lista do Histórico.', fechar, (refresh_atual or 0) + 1)

    # Fecha o pop-up de pendência do Histórico sozinho, se algum estiver
    # aberto (ver `abrir_pendencia_editar`) — o rascunho que ele mostrava
    # acabou de virar um trade de verdade, não faz sentido continuar
    # aberto. Fechamento incondicional (não compara IDs) de propósito:
    # `pendencia_aberta` e `id_pos` podem chegar com tipos numéricos
    # diferentes (int puro vs. numpy/pandas int64, str vs. int) depois de
    # ida-e-volta por JSON/parquet, e na prática só existe 1 popup
    # relevante aberto por vez — mais simples e robusto que tentar casar
    # os dois valores exatamente.
    fechar_popup = None if pendencia_aberta is not None else no_update
    return responder(f'✔ Finalizado como operação #{id_trade} no Diário.',
                     fechar_popup, (refresh_atual or 0) + 1)


if __name__ == '__main__':
    gp.iniciar_monitor_processo_pai(PORTA_PAI)
    print("=" * 60)
    print("  Diário de Trade — processo clássico (porta 8053)")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=PORTA_PROPRIA, threaded=True)
