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
from dash_iconify import DashIconify
import flask
import numpy as np
import pandas as pd
import plotly.graph_objects as go

import config_backtest as cfg
import persistencia
import diario_trade as dt
import ctrader_importar_extrato
import profit_importar_extrato
import tracados
import gerenciador_processos as gp
import ai_analyst
from metricas import extrair_metricas, montar_snapshot_diario, analisar_tracados, segmentos_cor_por_sinal

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

# Mesmo mapeamento de `dashboard.py::_ROTULO_DIRECAO_OPERACAO` — duplicado
# aqui de propósito (processos separados, sem import cruzado entre os dois
# arquivos de dashboard).
_ROTULO_DIRECAO = {'long': 'Compra', 'short': 'Venda'}

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

# Estatísticas removida (rollout TradeZella, 2026-07-31: "tire a aba
# Estatísticas de dentro do Diário, torne-o a página inicial do
# Dashboard") — relocada pra `dashboard.py` (ver
# `_construir_diario_estatisticas` lá). A navegação entre estas 6 seções
# não vem mais de botões PRÓPRIOS deste processo — vem do menu lateral do
# pai, via postMessage (`assets/secao_diario.js`), que escreve direto em
# `store-diario-sub-aba`.
# "Novo Trade" saiu daqui (rollout TradeZella, 2026-07-31, pedido do
# Jesué: "Novo Trade vira um botão no diário que abre em pop-up") — não é
# mais uma sub-aba própria, nunca é valor de `store-diario-sub-aba`. Vira
# pop-up disparado por `btn-abrir-novo-trade` (dentro de
# `_construir_diario_historico`), ver `drawer-novo-trade` mais
# abaixo. `_construir_diario_novo_trade` (o formulário em si) continua
# existindo — só mudou COMO ele aparece na tela.
_ABAS_DIARIO_SUB = [
    ('posicao', 'Posição'),
    ('historico', 'Histórico'),
    ('estrategias', 'Estratégias'),
    ('padroes', 'Padrões'),
    ('bem_estar', 'Bem-Estar'),
]
_ABA_DIARIO_INICIAL = 'historico'


def _opcoes_diario(valores: list[str]) -> list[dict]:
    """Converte vocabulário canônico (snake_case) em opções de dropdown legíveis."""
    return [{'label': v.replace('_', ' ').title(), 'value': v} for v in valores]


def _unidade_pontuacao(ativo: str | None) -> str:
    """"pts" (WIN/WDO/BIT) ou "pips" (XAUUSD/EURUSD, cTrader) — 2026-08-05,
    pedido do Jesué: "o XAUUSD geralmente traz resultados, alvos e stops em
    pips e não em pontos. Ajuste isso." Só rótulo — `cfg.UNIDADE_PONTUACAO_
    ATIVO` não converte o número, o dado já vem na unidade certa da
    origem."""
    return cfg.UNIDADE_PONTUACAO_ATIVO.get(ativo, 'pts')


def _opcoes_estrategias() -> list[dict]:
    """Opções de estratégia lidas do cadastro editável (aba Estratégias),
    não mais da lista fixa — refletem criação/desativação na hora."""
    return [{'label': linha['nome'], 'value': linha['slug']} for _, linha in dt.listar_estrategias().iterrows()]


def _opcoes_estrategias_agrupadas() -> list[dict]:
    """Mesmas opções de `_opcoes_estrategias`, mas agrupadas por categoria
    (Tendência/Reversão/Lateralidade/Sem categoria) — formato `{group,
    items}` que `dmc.Select`/`dmc.MultiSelect` entendem nativamente
    (2026-08-05, auditoria de UX: "~28 opções numa lista só, sem agrupar
    por categoria... vale agrupar"). Fica separada de `_opcoes_estrategias`
    porque `dcc.Dropdown` (usado em Posição/Em Andamento) NÃO suporta esse
    formato de agrupamento — só trocar onde o componente é Mantine."""
    df = dt.listar_estrategias()
    ordem_categorias = list(dt.CATEGORIA_ESTRATEGIA_OPCOES)
    grupos: dict = {cat: [] for cat in ordem_categorias}
    grupos[None] = []
    for _, linha in df.iterrows():
        cat = linha.get('categoria')
        grupos.setdefault(cat, []).append({'label': linha['nome'], 'value': linha['slug']})
    resultado = []
    for cat in ordem_categorias:
        if grupos.get(cat):
            resultado.append({'group': dt.ROTULO_CATEGORIA_ESTRATEGIA[cat], 'items': grupos[cat]})
    if grupos.get(None):
        resultado.append({'group': 'Sem categoria', 'items': grupos[None]})
    return resultado


def _intervalo_ultima_semana() -> tuple:
    """Semana civil (dom-sáb) do trade mais recente — mesmo critério de
    `_dias_da_semana` no Day View — usada como padrão inicial dos filtros
    de data do Histórico (2026-08-05, pedido do Jesué: "deixe o calendário
    selecionado com a última semana como padrão... e os filtros de data em
    cima preenchidos para mostrar onde estamos"). `(None, None)` se não
    houver nenhum trade real ainda."""
    df = dt.listar_trades(is_simulated=False)
    if df.empty:
        return None, None
    ultimo_dia = pd.to_datetime(df['data_op']).max().date()
    inicio = ultimo_dia - timedelta(days=(ultimo_dia.weekday() + 1) % 7)  # domingo
    fim = inicio + timedelta(days=6)
    return inicio.isoformat(), fim.isoformat()


def _opcoes_gerenciamento_risco(ativo_mercado: str | None = None) -> list[dict]:
    """Opções do cadastro de Gerenciamento de Risco (Playbook, 2026-08-02;
    definido por ATIVO desde 2026-08-03, não mais por estratégia) — `value`
    é o id (int), `dcc.Dropdown` aceita number sem problema (ao contrário
    do `dmc.Select`). `ativo_mercado` filtra só as opções daquele
    instrumento (usado no seletor do trade); sem filtro, mostra todas com
    o ativo no rótulo (usado no cadastro)."""
    df = dt.listar_gerenciamentos_risco(ativo_mercado=ativo_mercado)
    if ativo_mercado is not None:
        return [{'label': linha['nome'], 'value': int(linha['id'])} for _, linha in df.iterrows()]
    return [{'label': f"{linha['nome']} ({linha['ativo_mercado']})", 'value': int(linha['id'])}
            for _, linha in df.iterrows()]


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



def _icone(nome_tabler: str, tamanho: int = 13, cor: str | None = None, **kwargs) -> DashIconify:
    """Ícone padrão (Tabler, via `dash_iconify`) — substitui os glifos
    Unicode/emoji soltos usados antes como ícone de status/botão (2026-08-03,
    pedido do Jesué: "REMOVA TODOS OS EMOJIS... identidade visual mais
    refinada com ícones estilizados"). Mesmo helper de `dashboard.py`
    (arquivos/processos separados, sem import cruzado de UI)."""
    if cor:
        kwargs['color'] = cor
    return DashIconify(icon=nome_tabler, width=tamanho, **kwargs)


def _texto_com_icone(nome_tabler: str, texto: str, cor: str | None = None, tamanho: int = 13):
    """Ícone + texto lado a lado — substitui o padrão antigo de prefixar uma
    string crua com um glifo emoji (`f'⚠ {texto}'`, `f'✔ {texto}'`, etc.).
    Retorna uma LISTA, usável direto como `children`."""
    return [_icone(nome_tabler, tamanho=tamanho, cor=cor), f' {texto}']


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


_TABLER_ICONE_RATING = {
    'agua': 'tabler:glass-full', 'exercicio': 'tabler:barbell', 'alimentacao': 'tabler:apple',
    # 2026-08-03 (feedback do Jesué: "ficou destoando do estilo das
    # estrelas" — Sono/Café continuavam com ★ enquanto Água/Exercício/
    # Alimentação já eram ícone): Sono e Café entram no mesmo mecanismo.
    'sono': 'tabler:cloud', 'cafe': 'tabler:coffee',
}


def _icone_rating(id_componente: str, tipo_icone: str, valor: int = 3) -> html.Div:
    """Nota 1-5 clicável com ÍCONE customizado em vez de estrela — Água/
    Exercício Físico/Alimentação no Check-in do Dia, pedido do Jesué
    2026-08-03. Mesma estrutura/mecânica de `_estrela_rating`
    (`dcc.Input(type='hidden')` por baixo, clique ligado em
    `_registrar_clique_icone`), só troca o ícone visual (Tabler via
    `DashIconify`, `_TABLER_ICONE_RATING` acima — 2026-08-03: era emoji via
    CSS `content`, corrigido pra bater com a regra "sem emojis" que o
    próprio widget tinha motivado; o estado "não marcado" usa dessaturação/
    opacidade em `.diario-icone-rating`, ver assets/style.css). Só usado em
    campos singleton (Check-in do Dia não é por-posição), então não precisa
    do equivalente pattern-matched de `_registrar_clique_estrela_match`."""
    valor = valor or 0
    ids_icones = [f'{id_componente}-{tipo_icone}-{i}' for i in range(1, 6)]
    icones = html.Div([
        html.Span(DashIconify(icon=_TABLER_ICONE_RATING[tipo_icone], width=20), id=ids_icones[i - 1], n_clicks=0,
                  className=f'diario-icone-rating diario-icone-{tipo_icone}' + (' ativa' if i <= valor else ''))
        for i in range(1, 6)
    ], style={'display': 'flex', 'gap': '2px'})
    return html.Div([icones, dcc.Input(id=id_componente, type='hidden', value=valor)])


def _registrar_clique_icone(app, id_campo: str, tipo_icone: str):
    """Ver `_registrar_clique_estrela` pro mesmo raciocínio — só troca o
    sufixo dos spans clicáveis (`-{tipo_icone}-N` em vez de `-estrela-N`,
    pra não colidir se algum dia um mesmo campo precisar dos dois) e a
    classe CSS restaurada em cada tick."""
    app.clientside_callback(
        f"""
        function(...cliques) {{
            const ctx = dash_clientside.callback_context;
            if (!ctx.triggered.length) {{ return dash_clientside.no_update; }}
            const m = ctx.triggered[0].prop_id.match(/-{tipo_icone}-(\\d+)\\.n_clicks$/);
            return m ? parseInt(m[1], 10) : dash_clientside.no_update;
        }}
        """,
        Output(id_campo, 'value'),
        [Input(f'{id_campo}-{tipo_icone}-{i}', 'n_clicks') for i in range(1, 6)],
        prevent_initial_call=True,
    )
    app.clientside_callback(
        f"""
        function(valor) {{
            const v = valor || 0;
            return [1, 2, 3, 4, 5].map(i => 'diario-icone-rating diario-icone-{tipo_icone}' + (i <= v ? ' ativa' : ''));
        }}
        """,
        [Output(f'{id_campo}-{tipo_icone}-{i}', 'className') for i in range(1, 6)],
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

    Usado em TODO campo de tag do Diário (pedido do Jesué 2026-08-03: "todos
    os campos de tags devem ter o mesmo mecanismo") — Print Emocional e Tags
    Setup/Erro, tanto no Novo Trade quanto em Posição quanto no pop-up Editar
    Trade. Estratégia/Sala/Ativo continuam com `dcc.Dropdown`/`dmc.Select` de
    sempre — não são vocabulário aberto, são registros fechados com lógica
    própria (checklist, risco por ativo), não "tags".

    `id_store` normalmente é um id pattern-matching (dict, {'type':...,
    'index': id_pos}, usado nos cards de Posição) mas também aceita um id
    singleton simples (string, usado no Novo Trade e no Editar Trade) — em
    ambos os casos ele vira o id do `dcc.Store` escondido que os callbacks
    Python leem via `.data`. `data-armar-index` (o card volta a ficar
    "armado" pro próximo Ctrl+V ao clicar/focar dentro do widget, ver
    `assets/diario_paste.js`) só é escrito quando `id_store` é um dict com
    'index' — nos ids singleton essa marcação não se aplica."""
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
        # `data-armar-index` só existe pros ids pattern-matching de Posição
        # (cada card tem o seu) — os campos singleton do Novo Trade e do
        # pop-up Editar Trade não têm 'index' nenhum pra armar.
        **({'data-armar-index': str(id_store['index'])} if isinstance(id_store, dict) and 'index' in id_store else {}),
    })


def _wrap_id_tag_widget(id_tag):
    """Id do wrapper endereçável em volta de um `_campo_tag_widget` — só
    precisa existir pros campos SINGLETON do Novo Trade (ids string), que
    são resetados por `limpar_form_diario` sem reconstruir o formulário
    inteiro (ao contrário de Posição/Editar Trade, que reconstroem o card
    todo a cada mudança real e por isso nunca precisam de reset em
    isolamento). Resetar só o `dcc.Store` de dentro do widget não limpa os
    chips já renderizados no DOM (`assets/tag_input.js` nunca lê o Store de
    volta pra desenhar chips) — por isso `limpar_form_diario` troca os
    `children` DESTE wrapper por um `_campo_tag_widget` novo em vez de só
    zerar o valor do Store."""
    return f'{id_tag}-wrap' if isinstance(id_tag, str) else {**id_tag, 'type': f"{id_tag['type']}-wrap"}


def _bloco_emocional(id_confianca, id_humor, id_energia, id_presenca, id_equilibrio, id_disciplina,
                      id_tags, id_sentimento, id_label_sentimento, valores: dict | None = None) -> html.Div:
    """Autoavaliação — ver diario_app_98.py::_bloco_emocional_98 pro mesmo
    raciocínio completo (fundação do módulo emocional/comportamental,
    feedback diário.txt 2026-07-11). 2026-08-03 (pedido do Jesué): título
    encurtado ("Autoavaliação Emocional" -> "Autoavaliação"); "Atenção"
    saiu do formulário (a coluna `emo_atencao` continua no schema só pra
    trades antigos que já a têm preenchida — nunca mais escrita por um
    trade novo); "Equilíbrio" e "Disciplina" entraram no lugar. 2026-08-03
    (2ª rodada, "todos os campos de tags devem ter o mesmo mecanismo"):
    Print Emocional usa `_campo_tag_widget` sempre — não existe mais
    variante `dcc.Dropdown`, nem no Novo Trade."""
    campo = _campo_diario
    valores = valores or {}
    sentimento_valor = valores.get('sentimento')
    sentimento_valor = int(sentimento_valor) if sentimento_valor is not None and not (
        isinstance(sentimento_valor, float) and pd.isna(sentimento_valor)) else 50
    campo_tags = campo('Print Emocional (tags)', html.Div(
        _campo_tag_widget(id_tags, _opcoes_tags_emocionais(), _lista_segura(valores.get('tags'))),
        id=_wrap_id_tag_widget(id_tags)))
    return _bloco_diario('Autoavaliação', html.Div([
        html.Div([
            campo('Confiança', _estrela_rating(id_confianca, _num_estrela_ou_padrao(valores.get('confianca')))),
            campo('Humor', _estrela_rating(id_humor, _num_estrela_ou_padrao(valores.get('humor')))),
            campo('Energia', _estrela_rating(id_energia, _num_estrela_ou_padrao(valores.get('energia')))),
            campo('Presença', _estrela_rating(id_presenca, _num_estrela_ou_padrao(valores.get('presenca')))),
            campo('Equilíbrio', _estrela_rating(id_equilibrio, _num_estrela_ou_padrao(valores.get('equilibrio')))),
            campo('Disciplina', _estrela_rating(id_disciplina, _num_estrela_ou_padrao(valores.get('disciplina')))),
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


def _bloco_diario(titulo, children) -> dmc.Card:
    """Casca de seção usada em QUASE toda a superfície ainda não redesenhada
    do Diário (Novo Trade, Posição, Estratégias, Bem-Estar — só Histórico já
    tinha seu próprio `dmc.Card`/`tz-card`, ver `_construir_diario_
    historico`). 2026-08-02: convertida de `html.Div` cru pro MESMO padrão
    real (`dmc.Card` + `className='tz-card'` + título em `dmc.Text`
    dourado/uppercase) — item 2 da fila pós-"Ver" ("rollout TradeZella pra
    Posição e Novo Trade, ainda no formato anterior"). Trocar aqui em vez de
    duplicar a casca em cada seção propaga o upgrade pra tudo que já usa
    `_bloco_diario`, de graça."""
    return dmc.Card([
        dmc.Text(titulo, size='sm', fw=600, tt='uppercase',
                  style={'letterSpacing': '0.05em', 'color': C['gold'], 'marginBottom': '10px'}),
        children,
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card')




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
    # 2026-08-02, item 2 da fila pós-"Ver" ("rollout TradeZella pra Posição
    # e Novo Trade"): campos convertidos de `dcc.Dropdown`/`dcc.Input` cru
    # (setas nativas de <select>, spinner nativo de <input type=number>,
    # `classico-dropdown`) pros componentes REAIS do Mantine já usados nos
    # filtros do Histórico (`dmc.Select`/`dmc.MultiSelect`/`dmc.NumberInput`/
    # `dmc.DateInput`/`dmc.Textarea`) — mesmo `id`/prop `value` de sempre
    # (confirmado compatível com `salvar_novo_trade`/`limpar_form_diario`,
    # que já só leem/escrevem `.value`), então NENHUMA outra callback
    # precisou mudar. Label passa a vir do próprio `label=`
    # do componente Mantine — `_campo_diario`/`_estilo_input_diario` não são
    # mais usados aqui (continuam servindo `_bloco_emocional`/outras abas).
    # `hideControls=True` nos campos de preço/pontos porque um spinner
    # +/- não faz sentido pra esses valores (mesma razão que already levou
    # a usar `type=text inputMode=decimal` antes); Lote mantém os controles
    # (é uma contagem pequena, o spinner do Mantine é limpo, não a seta
    # feia nativa que motivou a troca).
    grade = lambda *filhos: html.Div(list(filhos), style={
        'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(150px, 1fr))',
        'gap': '14px 16px', 'alignItems': 'end',
    })

    return html.Div([
        # Playbook (2026-08-02) — Coaching Quantitativo: check-in emocional e
        # checklist pré-mercado (rituais DIÁRIOS, não por-trade) vêm primeiro
        # visualmente, antes dos dados objetivos da operação — mesma ordem
        # lógica pedida pro fluxo inteiro (check-in > checklist pré-mercado >
        # checklist da estratégia + risco > autoavaliação/tags > números).
        html.Div(_conteudo_alerta_coaching(), id='diario-novo-trade-alerta-coaching'),
        # Modo Rápido/Completo (2026-08-05, auditoria de UX: "pra registrar
        # um trade rápido no meio do pregão, 5 seções todas expandidas é
        # muito scroll/carga cognitiva"). Rápido (padrão) mostra só o
        # essencial pra lançar o número — Dados da Operação + Gerenciamento
        # de Risco (auto-preenche Alvo/Stop, não é "extra") — e esconde
        # Checklist/Nível de S/R/Anotações/Emocional/Print em
        # `diario-novo-trade-extras` (`display:none`, nunca desmontado —
        # mesma disciplina de sempre, então nada se perde ao trocar de
        # modo). Completo revela tudo, pra quem quer registrar com riqueza
        # total logo de cara.
        dmc.SegmentedControl(
            id='diario-novo-trade-modo', value='rapido',
            data=[{'label': 'Rápido', 'value': 'rapido'}, {'label': 'Completo', 'value': 'completo'}],
            size='xs', mb='md',
        ),
        _bloco_diario('Dados da Operação', html.Div([
            grade(
                dmc.DateInput(id='diario-data', label='Data', valueFormat='DD/MM/YYYY'),
                dmc.Select(id='diario-ativo', label='Ativo', data=_opcoes_diario(cfg.ATIVOS_DIARIO),
                           clearable=False),
                dmc.Select(id='diario-janela', label='Janela', clearable=True,
                           data=_opcoes_janelas()),
                dmc.MultiSelect(id='diario-estrategia', label='Estratégia', data=_opcoes_estrategias_agrupadas()),
                dmc.Select(id='diario-sala', label='Sala/Análise', clearable=True,
                           data=_opcoes_diario(dt.SALA_ANALISE_CANONICA)),
                dmc.Select(id='diario-tipo-operacao', label='Tipo', clearable=False,
                           data=_opcoes_diario(dt.TIPO_OPERACAO)),
                dmc.NumberInput(id='diario-alvo', label='Alvo (pts)', hideControls=True, decimalScale=2),
                dmc.NumberInput(id='diario-stop', label='Stop (pts)', hideControls=True, decimalScale=2),
                dmc.NumberInput(id='diario-lote', label='Lote', value=1, min=0),
                dmc.NumberInput(id='diario-preco-entrada', label='Preço Entrada', hideControls=True, decimalScale=2),
                dmc.NumberInput(id='diario-preco-saida', label='Preço Saída', hideControls=True, decimalScale=2),
                dmc.Select(id='diario-tipo-saida', label='Tipo Saída', clearable=True,
                           data=_opcoes_diario(dt.TIPO_SAIDA)),
                dmc.NumberInput(id='diario-resultado', label='Resultado (pts)', hideControls=True,
                                decimalScale=2, allowNegative=True),
                dmc.NumberInput(id='diario-saldo', label='Saldo (R$)', hideControls=True,
                                decimalScale=2, allowNegative=True),
            ),
        ])),

        # Gerenciamento de Risco (corrigido 2026-08-03 — definido por
        # ATIVO, não por estratégia: WIN/WDO têm parâmetros de pontuação/
        # volatilidade diferentes). Selecionar o Ativo acima carrega o
        # gerenciamento `padrao` daquele ativo aqui; o seletor permite
        # trocar por qualquer outro cadastrado (ver `aplicar_risco_ativo`).
        # Fica FORA de `diario-novo-trade-extras` de propósito — auto-
        # preenche Alvo/Stop, é parte do essencial mesmo no Modo Rápido.
        _bloco_diario('Gerenciamento de Risco', html.Div([
            dcc.Dropdown(id='diario-gerenciamento-risco', options=[], placeholder='selecione o ativo acima',
                         className='classico-dropdown', style={'marginBottom': '8px'}),
            html.Div(id='diario-risco-info', style={'fontSize': '12px', 'color': C['text2']}),
        ])),

        html.Div(id='diario-novo-trade-extras', style={'display': 'none'}, children=[
            # Playbook (2026-08-02) — Checklist da Estratégia, populado só
            # quando exatamente 1 estratégia está selecionada acima (ver
            # `resolver_estrategia_unica`).
            _bloco_diario('Checklist da Estratégia', html.Div(id='diario-checklist-estrategia-conteudo')),

            _bloco_diario('Nível de S/R referenciado (opcional)', html.Div([
                grade(
                    dmc.NumberInput(id='diario-nivel-sr', label='Preço', hideControls=True, decimalScale=2),
                    dmc.TextInput(id='diario-nivel-sr-tipo', label='Tipo', placeholder='ex: suporte'),
                ),
            ])),

            _bloco_diario('Anotações', html.Div([
                dmc.Textarea(id='diario-resumo', label='Resumo Operacional', autosize=True, minRows=3, mb='10px'),
                dmc.Textarea(id='diario-obs', label='Obs (autocoaching)', autosize=True, minRows=2, mb='10px'),
                _campo_diario('Tags de Setup/Erro', html.Div(
                    _campo_tag_widget('diario-tags-setup-erro', _opcoes_tags_setup_erro(), []),
                    id=_wrap_id_tag_widget('diario-tags-setup-erro'))),
            ])),

            _bloco_emocional(
                id_confianca='diario-emo-confianca', id_humor='diario-emo-humor',
                id_energia='diario-emo-energia', id_presenca='diario-emo-presenca',
                id_equilibrio='diario-emo-equilibrio', id_disciplina='diario-emo-disciplina',
                id_tags='diario-tags-emocionais',
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
        ]),

        html.Div([
            dmc.Button('Salvar Operação', id='btn-diario-salvar', n_clicks=0, color='green'),
            dmc.Button('Limpar', id='btn-diario-limpar', n_clicks=0, variant='outline', color='gray', ml='10px'),
        ], style={'marginTop': '6px'}),
        html.Div(id='diario-status', style={
            'marginTop': '10px', 'fontSize': '12px', 'fontFamily': FONTS['mono'], 'color': C['text2'],
        }),
    ])


# --- Importar Trades (cTrader + ProfitPro) --------------------------------
# 2026-08-05: Jesué já começou a operar XAUUSD (conta cTrader, IC Markets)
# antes da Fase 1 do plano de integração (histórico automático via Open API)
# destravar — precisa de um jeito de alimentar o Diário JÁ, a partir do
# extrato que a própria plataforma exporta (Conta > Extrato de Conta). Ver
# `ctrader_importar_extrato.py` pro parsing do arquivo. Estendido no mesmo
# dia pra também aceitar o export "Operações" do ProfitPro (`profit_
# importar_extrato.py`) — preparado defensivamente ("caso seja necessário");
# WIN/WDO já chega ao Diário sozinho via MT5, então esse caminho é pra
# backfill/reconciliação manual, não o fluxo do dia a dia.
#
# Fluxo (idêntico pras duas fontes, dispatch por extensão do arquivo em
# `processar_upload_extrato`): upload -> parse -> preview com 1 checkbox por
# linha (desmarcado se `dt.trade_ja_importado` já achar o mesmo trade
# gravado antes, POR QUALQUER MOTIVO — inclusive sincronização MT5 automática,
# não só uma importação anterior) -> "Importar Selecionados" grava tudo de
# uma vez via `dt.importar_trades_lote`. A classificação qualitativa
# (estratégia/tags emocionais/notas) NÃO acontece aqui — os trades
# importados aparecem no Histórico como qualquer outro, e o "editar um por
# um" pedido pelo Jesué é o botão Editar que já existe lá
# (`CAMPOS_EDITAVEIS_TRADE`), sem UI nova pra isso.
#
# Cada linha do preview (`store-importar-extrato-linhas`) é um dict num
# formato comum às duas fontes — ver `processar_upload_extrato` — com
# `moeda` ('USD' pro cTrader, 'BRL' pro Profit) dizendo como `saldo_nativo`
# deve ser interpretado/convertido no commit.

def _linha_preview_importacao(i: int, linha: dict) -> html.Tr:
    duplicado = bool(linha.get('duplicado'))
    saldo = linha['saldo_nativo']
    simbolo_moeda = 'US$' if linha['moeda'] == 'USD' else 'R$'
    abertura = linha.get('datetime_entrada')
    return html.Tr([
        html.Td(dmc.Checkbox(id={'type': 'importar-linha-incluir', 'index': i},
                              checked=not duplicado, size='xs')),
        html.Td(linha['ativo'], style={'fontWeight': 600}),
        html.Td('Compra' if linha['tipo_operacao'] == 'long' else 'Venda'),
        html.Td(pd.Timestamp(abertura).strftime('%d/%m %H:%M:%S') if abertura else '—'),
        html.Td(pd.Timestamp(linha['datetime_saida']).strftime('%d/%m/%Y %H:%M:%S')),
        html.Td(f"{linha['preco_entrada']:.2f}"),
        html.Td(f"{linha['preco_saida']:.2f}"),
        html.Td(f"{linha['lote']:.2f}"),
        html.Td(f"{simbolo_moeda} {saldo:+.2f}", style={'color': C['green'] if saldo >= 0 else C['red']}),
        html.Td(linha.get('janela') or '—', style={'fontSize': '11px', 'color': C['text2']}),
        html.Td('já importado' if duplicado else '', style={'fontSize': '11px', 'color': C['red']}),
    ], style={'background': 'rgba(224,92,92,0.06)' if duplicado else 'transparent'})


def _tabela_preview_importacao(linhas: list[dict]) -> html.Div:
    if not linhas:
        return html.Div('Nenhum trade encontrado no arquivo.',
                         style={'fontSize': '12px', 'color': C['text2']})
    cabecalho = html.Tr([html.Th(c, style={'textAlign': 'left', 'padding': '4px 8px'}) for c in [
        '', 'Ativo', 'Direção', 'Abertura', 'Fechamento', 'Entrada', 'Saída', 'Lote', 'Resultado', 'Janela', '',
    ]])
    corpo = [_linha_preview_importacao(i, l) for i, l in enumerate(linhas)]
    return html.Div(
        html.Table([html.Thead(cabecalho), html.Tbody(corpo)], style={
            'width': '100%', 'borderCollapse': 'collapse', 'fontSize': '12px',
        }),
        style={'maxHeight': '420px', 'overflowY': 'auto', 'border': f'1px solid {C["border"]}',
               'borderRadius': '8px', 'padding': '4px'},
    )


def _construir_diario_importar_extrato() -> html.Div:
    return html.Div([
        _bloco_diario('Relatório da Corretora', html.Div([
            html.Div([
                html.Div(
                    'cTrader (XAUUSD/EURUSD): extrato de conta (Conta > Extrato de '
                    'Conta > Exportar, .htm ou .xlsx) — cada linha da tabela '
                    '"Histórico" já é um round-trip fechado (conta netting).',
                    style={'fontSize': '12px', 'color': C['text2'], 'marginBottom': '4px'},
                ),
                html.Div(
                    'ProfitPro (WIN/WDO): export "Operações" (.csv) — só necessário '
                    'pra backfill/reconciliação manual, já que WIN/WDO chega ao '
                    'Diário sozinho pela sincronização MT5 ao vivo.',
                    style={'fontSize': '12px', 'color': C['text2'], 'marginBottom': '10px'},
                ),
            ]),
            dcc.Upload(
                id='importar-extrato-upload',
                children=html.Div('Arraste ou clique pra selecionar o arquivo (.htm/.xlsx/.csv)',
                                   style={'fontSize': '12px'}),
                style={
                    'background': C['bg'], 'border': f'1px dashed {C["border"]}', 'borderRadius': '8px',
                    'padding': '16px', 'textAlign': 'center', 'cursor': 'pointer', 'color': C['text2'],
                },
            ),
            html.Div(id='importar-extrato-status', style={
                'marginTop': '10px', 'fontSize': '12px', 'fontFamily': FONTS['mono'], 'color': C['text2'],
            }),
        ])),
        _bloco_diario('Revisão antes de importar', html.Div([
            html.Div(
                'Trades marcados "já importado" (desmarcados por padrão) já existem '
                'no Diário — inclusive os que chegaram sozinhos pela sincronização '
                'MT5. Confira antes de confirmar.',
                style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '8px', 'fontStyle': 'italic'},
            ),
            html.Div(id='importar-extrato-preview'),
        ])),
        html.Div([
            dmc.Button('Importar Selecionados', id='btn-importar-extrato-confirmar', n_clicks=0,
                       color='green', disabled=True),
        ], style={'marginTop': '6px'}),
        html.Div(id='importar-extrato-resultado', style={
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
        _texto_com_icone('tabler:alert-triangle',
            'Saída NÃO confirmada pela corretora — o valor abaixo é uma estimativa. '
            'Confira/corrija antes de finalizar.', tamanho=13),
        style={'fontSize': '11px', 'color': C['yellow'], 'fontWeight': '700', 'marginBottom': '8px',
               'display': 'flex', 'alignItems': 'flex-start', 'gap': '4px'},
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
        campo('Tags Setup/Erro',
              _campo_tag_widget({'type': 'ea-tags-erro', 'index': id_pos}, _opcoes_tags_setup_erro(), [])),
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
        label_ref = 'Saída (estimada)' if pos.get('saida_estimada') else 'Saída'
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
        status_txt, status_cor = _texto_com_icone('tabler:check', 'FECHADA (par hedge — pronta pra finalizar)', tamanho=11), C['green']
    elif pendente and par_hedge:
        status_txt, status_cor = _texto_com_icone('tabler:alert-triangle', 'FECHADA (par hedge) — AGUARDANDO CONFIRMAÇÃO', tamanho=11), C['yellow']
    elif pendente:
        status_txt, status_cor = _texto_com_icone('tabler:alert-triangle', 'FECHADA — AGUARDANDO CONFIRMAÇÃO', tamanho=11), C['yellow']
    else:
        status_txt, status_cor = ' ● ABERTA', C['green']
        status_classe = 'diario-status-piscante'
    detalhe = html.Div([
        html.Span((pos.get('tipo_operacao') or '—').upper(), style={
            'color': cor_direcao, 'fontSize': '11px', 'fontWeight': '700'}),
        html.Span(status_txt, className=status_classe, style={
            'marginLeft': '8px', 'fontSize': '10px', 'fontWeight': '700', 'color': status_cor,
            'display': 'inline-flex', 'alignItems': 'center'}),
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
        campo(f'Alvo ({_unidade_pontuacao(pos.get("ativo"))})', dcc.Input(
              id={'type': 'ea-alvo', 'index': id_pos}, type='text', inputMode='decimal',
              debounce=True, value=pos.get('alvo_pontos_preliminar'), style=estilo_input({'width': '130px'}))),
        campo(f'Stop ({_unidade_pontuacao(pos.get("ativo"))})', dcc.Input(
              id={'type': 'ea-stop', 'index': id_pos}, type='text', inputMode='decimal',
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
        id_presenca={'type': 'ea-emo-presenca', 'index': id_pos},
        id_equilibrio={'type': 'ea-emo-equilibrio', 'index': id_pos},
        id_disciplina={'type': 'ea-emo-disciplina', 'index': id_pos},
        id_tags={'type': 'ea-tags-emocionais', 'index': id_pos},
        id_sentimento={'type': 'ea-sentimento-mercado', 'index': id_pos},
        id_label_sentimento={'type': 'ea-sentimento-label', 'index': id_pos},
        valores={
            'confianca': pos.get('emo_confianca_preliminar'), 'humor': pos.get('emo_humor_preliminar'),
            'energia': pos.get('emo_energia_preliminar'), 'presenca': pos.get('emo_presenca_preliminar'),
            'equilibrio': pos.get('emo_equilibrio_preliminar'), 'disciplina': pos.get('emo_disciplina_preliminar'),
            'tags': pos.get('tags_emocionais_preliminar'),
            'sentimento': pos.get('sentimento_mercado_preliminar'),
        },
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
        # Guarda o último texto realmente salvo (ver `salvar_nota_em_andamento`)
        # pra não duplicar a MESMA nota no log a cada blur repetido (ex:
        # Alt+Tab pra fora e de volta várias vezes sem editar nada).
        dcc.Store(id={'type': 'ea-nota-ultimo-salvo', 'index': id_pos}, data=''),
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

    # Playbook (2026-08-02) — Checklist da Estratégia, populado só quando
    # exatamente 1 estratégia estiver selecionada acima.
    bloco_checklist_estrategia = html.Div([
        html.Div(id={'type': 'ea-checklist-estrategia-conteudo', 'index': id_pos},
                 children=_conteudo_checklist_estrategia(
                     _lista_segura(pos.get('estrategia_tags_preliminar')),
                     {'type': 'ea-checklist-estrategia-itens', 'index': id_pos})),
        # Bridge sempre-montado — mesmo raciocínio do
        # `store-diario-checklist-estrategia-marcados` em Novo Trade
        # (`ea-checklist-estrategia-itens` só existe quando 1 estratégia
        # está resolvida; ler `.value` direto como State no Finalizar
        # arriscaria rejeitar a callback inteira quando nenhuma estiver).
        dcc.Store(id={'type': 'ea-checklist-estrategia-marcados', 'index': id_pos}, data=[]),
    ], style={'marginTop': '10px'})

    # Gerenciamento de Risco (corrigido 2026-08-03 — definido por ATIVO, o
    # da própria posição, não pela estratégia). O ativo da posição já é
    # conhecido (vem do MT5, não é escolhido aqui), então o padrão daquele
    # ativo já entra pré-carregado no render — o seletor permite trocar.
    _gerenciamento_padrao_pos = dt.obter_gerenciamento_risco_padrao(pos.get('ativo'))
    bloco_risco = html.Div([
        dcc.Dropdown(
            id={'type': 'ea-gerenciamento-risco', 'index': id_pos},
            options=_opcoes_gerenciamento_risco(pos.get('ativo')),
            value=int(_gerenciamento_padrao_pos['id']) if _gerenciamento_padrao_pos else None,
            placeholder='nenhum gerenciamento cadastrado', className='classico-dropdown',
            style={'marginBottom': '8px'},
        ),
        html.Div(id={'type': 'ea-risco-info', 'index': id_pos},
                 children=_conteudo_risco_info(_gerenciamento_padrao_pos)[0],
                 style={'fontSize': '12px', 'color': C['text2']}),
    ], style={'marginTop': '10px'})

    filhos = [campos_classificacao, bloco_checklist_estrategia, bloco_risco, resumo_input, bloco_emocional,
              nota_input, imagem_bloco]
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
                _texto_com_icone('tabler:alert-triangle',
                    f"Sincronização com MT5 pode estar atrasada — dado de "
                    f"{atualizado_em.strftime('%H:%M:%S')} ({atraso_s:.0f}s atrás). "
                    f"Não confie nos números acima sem confirmar no MT5.", tamanho=12),
                C['yellow'],
            ))
        else:
            linhas.append((f"Sincronizado às {atualizado_em.strftime('%H:%M:%S')}.", C['text2']))
    residual = dt.calcular_exposicao_residual(posicoes)
    sobras = {contrato: liquido for contrato, liquido in residual.items() if liquido != 0}
    if sobras:
        texto_sobras = ', '.join(f"{contrato}: {liquido:+.0f}" for contrato, liquido in sobras.items())
        linhas.append((_texto_com_icone('tabler:alert-triangle',
            f"Exposição líquida real (sem par no momento) — {texto_sobras}.", tamanho=12), C['yellow']))
    else:
        linhas.append(("Exposição líquida zerada em todos os contratos — nenhum risco de mercado residual.", C['text2']))
    return _bloco_diario('Status das Posições', html.Div([
        html.Div(t, style={'fontSize': '12px', 'color': cor, 'marginBottom': '4px',
                            'display': 'flex', 'alignItems': 'center', 'gap': '4px'}) for t, cor in linhas
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


# Conexões suportadas hoje só MT5 — cTrader/NinjaTrader anotados como
# planos futuros (ver [[project_futuras_integracoes_corretoras]] na
# memória), aparecem no seletor já desabilitados pra deixar claro que
# "existem no roadmap" sem fingir que funcionam.
_OPCOES_CONEXAO_CORRETORA = [
    {'label': 'MetaTrader 5', 'value': 'mt5'},
    {'label': 'cTrader (em breve)', 'value': 'ctrader', 'disabled': True},
    {'label': 'NinjaTrader (em breve)', 'value': 'ninjatrader', 'disabled': True},
]


def _conteudo_status_conexao() -> html.Div:
    """Cabeçalho de conexão em Posição (Playbook, 2026-08-03, pedido do
    Jesué) — status de conexão + resumo da conta da corretora + seletor de
    conexão. Quem fala com o MT5 de verdade é só a thread de fundo do
    dashboard.py (processo PAI); este processo (filho) só LÊ o cache que
    ela escreve (`persistencia.carregar_status_conexao`), mesmo padrão já
    usado por `posicoes_abertas.parquet` — nunca conecta no MT5 direto
    daqui."""
    status = persistencia.carregar_status_conexao()

    def _pill(texto, cor):
        return html.Div(texto, style={
            'fontSize': '11px', 'fontWeight': '700', 'color': cor,
            'fontFamily': FONTS['display'], 'display': 'flex', 'alignItems': 'center', 'gap': '4px',
        })

    if status is None:
        conteudo_status = _pill(_texto_com_icone('tabler:alert-triangle',
            'Sem dado de conexão ainda (aguardando o Dashboard sincronizar)', tamanho=12), C['text2'])
        conteudo_conta = html.Div('—', style={'fontSize': '12px', 'color': C['text2']})
    else:
        conectado = bool(status.get('conectado'))
        atualizado_em = status.get('atualizado_em')
        atraso_s = None
        if pd.notna(atualizado_em):
            atraso_s = (datetime.now() - pd.Timestamp(atualizado_em).to_pydatetime()).total_seconds()
        # Mesmo cuidado já documentado em `_secao_status_posicoes`: nunca
        # afirmar "conectado" sobre um dado velho sem dizer de quando é.
        se_atrasado = f" (há {int(atraso_s)}s)" if atraso_s is not None and atraso_s > 40 else ""
        if conectado:
            latencia = status.get('latencia_ms')
            texto_latencia = f" · {latencia:.0f}ms" if pd.notna(latencia) else ""
            conteudo_status = _pill(f'● Conectado{texto_latencia}{se_atrasado}', C['green'])
        else:
            conteudo_status = _pill(f'● Sem conexão com a corretora{se_atrasado}', C['red'])

        login, servidor = status.get('login'), status.get('servidor')
        saldo, patrimonio, margem_livre = status.get('saldo'), status.get('patrimonio'), status.get('margem_livre')
        moeda = status.get('moeda') or 'R$'
        if pd.notna(login):
            partes = [f"Conta {int(login)}" + (f" · {servidor}" if servidor else "")]
            if pd.notna(saldo):
                partes.append(f"Saldo {moeda} {saldo:,.2f}")
            if pd.notna(patrimonio):
                partes.append(f"Patrimônio {moeda} {patrimonio:,.2f}")
            if pd.notna(margem_livre):
                partes.append(f"Margem livre {moeda} {margem_livre:,.2f}")
            conteudo_conta = html.Div(' · '.join(partes), style={'fontSize': '12px', 'color': C['text2']})
        else:
            conteudo_conta = html.Div('Sem dados de conta (corretora desconectada).',
                                       style={'fontSize': '12px', 'color': C['text2']})

    conteudo_conta.style = {**conteudo_conta.style, 'whiteSpace': 'nowrap', 'overflow': 'hidden', 'textOverflow': 'ellipsis'}
    return html.Div([
        html.Div(conteudo_status, style={'flexShrink': '0'}),
        conteudo_conta,
        dcc.Dropdown(
            id='diario-conexao-corretora', options=_OPCOES_CONEXAO_CORRETORA, value='mt5',
            clearable=False, searchable=False, className='classico-dropdown',
            style={'width': '190px', 'flexShrink': '0'},
        ),
    ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center', 'gap': '12px'})


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
        html.Div([
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
        ], style={'display': 'flex', 'gap': '18px', 'alignItems': 'flex-start', 'flexWrap': 'wrap'}),
        _conteudo_metas_dia(s),
        _conteudo_alerta_coaching(),
    ])


def _conteudo_metas_dia(s: dict) -> html.Div:
    """Metas em R$ do plano (2026-08-06, pedido do Jesué — ver
    [[project_reorg_cadastro_checklist_pretrade]]): prejuízo é alerta (já
    reflete em `estado`/`motivos` de `status_do_dia`, aqui só reforça com a
    régua de progresso); ganho é CONQUISTA — tratamento visual positivo
    (verde, ícone de troféu), nunca o vermelho/amarelo do guarda-corpo,
    mesma filosofia de aviso não-bloqueante de `_conteudo_alerta_coaching`."""
    linhas = []
    if s.get('meta_prejuizo_dia') is not None:
        meta = s['meta_prejuizo_dia']
        cor = C['red'] if s.get('prejuizo_estourado') else C['text2']
        linhas.append(_texto_com_icone(
            'tabler:shield-exclamation' if s.get('prejuizo_estourado') else 'tabler:shield',
            f"Meta de prejuízo: R$ {s['saldo']:+.0f} / limite R$ -{meta:.0f}", tamanho=12, cor=cor))
    if s.get('meta_ganho_dia') is not None:
        meta = s['meta_ganho_dia']
        if s.get('ganho_batido'):
            linhas.append(_texto_com_icone('tabler:trophy', f"Meta de ganho batida! R$ {s['saldo']:+.0f} / R$ {meta:.0f}",
                                            tamanho=12, cor=C['green']))
        else:
            linhas.append(_texto_com_icone('tabler:target', f"Meta de ganho: R$ {s['saldo']:+.0f} / R$ {meta:.0f}",
                                            tamanho=12, cor=C['text2']))
    if not linhas:
        return html.Div()
    return html.Div([html.Div(l, style={'marginTop': '4px', 'display': 'flex', 'alignItems': 'center', 'gap': '4px'})
                      for l in linhas], style={'marginTop': '8px'})


def _conteudo_alerta_coaching() -> html.Div:
    """Playbook (2026-08-02) — avisos NÃO-BLOQUEANTES de Check-in Emocional
    e Checklist Pré-Mercado ainda não feitos hoje. Mesma filosofia do
    guarda-corpo: só mostra, nunca impede operar. Ambos alimentam
    `coaching_checkin_feito`/`checklist_pretrade_*_dia` no momento do save
    de qualquer trade do dia (ver `salvar_novo_trade`/Finalizar)."""
    hoje = date.today()
    avisos = []
    if not dt.obter_checkin_dia(hoje):
        avisos.append(_texto_com_icone('tabler:alert-triangle', 'Check-in emocional de hoje ainda não foi feito (aba Coaching).', tamanho=12))
    checklist = dt.obter_checklist_pretrade()
    registro = dt.obter_checklist_pretrade_dia(hoje)
    total = len(checklist['itens']) if checklist else 0
    marcados = len(registro['marcados']) if registro else 0
    if total and marcados < total:
        avisos.append(_texto_com_icone('tabler:alert-triangle', f'Checklist Pré-Mercado incompleto hoje ({marcados}/{total}).', tamanho=12))
    if not avisos:
        return html.Div()
    return html.Div([html.Div(a, style={'fontSize': '11px', 'color': C['gold'], 'marginTop': '2px',
                                         'display': 'flex', 'alignItems': 'center', 'gap': '4px'}) for a in avisos],
                     style={'marginTop': '8px', 'paddingTop': '8px', 'borderTop': f'1px solid {C["border"]}'})


def _borda_guarda_corpo() -> str:
    return C[_CORES_ESTADO_DIA[dt.status_do_dia()['estado']]]


def _conteudo_checklist_pre_trade() -> html.Div:
    """Checklist Pré-Mercado (Playbook, 2026-08-02) — itens vêm do cadastro
    editável (`dt.obter_checklist_pretrade`, aba Estratégias), não mais da
    lista fixa `cfg.DIARIO_CHECKLIST_PRE_TRADE`. É um RITUAL DIÁRIO, igual
    ao check-in emocional (não por-trade): marcar/desmarcar PERSISTE em
    `dt.salvar_checklist_pretrade_dia` (ver `resumir_checklist`), e reabrir
    mais tarde no mesmo dia mostra o que já foi marcado — não zera mais a
    cada toggle do botão."""
    checklist = dt.obter_checklist_pretrade()
    itens = checklist['itens'] if checklist else []
    hoje = date.today()
    registro_hoje = dt.obter_checklist_pretrade_dia(hoje)
    marcados_hoje = [str(i) for i in registro_hoje['marcados']] if registro_hoje else []
    return html.Div([
        dcc.Checklist(
            id='diario-checklist-itens',
            options=[{'label': item, 'value': str(i)} for i, item in enumerate(itens)],
            value=marcados_hoje, labelStyle={'display': 'block', 'marginBottom': '5px', 'fontSize': '12px'},
            inputStyle={'marginRight': '8px'},
        ),
        html.Div(id='diario-checklist-status', style={'fontSize': '11px', 'marginTop': '8px'}),
    ], style={'marginTop': '12px', 'paddingTop': '12px', 'borderTop': f'1px solid {C["border"]}'})


def _construir_diario_em_andamento() -> html.Div:
    return html.Div([
        # Cabeçalho de conexão (Playbook, 2026-08-03) — status/conta/
        # seletor de corretora, ACIMA até do guarda-corpo (é a primeira
        # coisa que precisa ficar visível antes de operar).
        dmc.Card(html.Div(_conteudo_status_conexao(), id='diario-status-conexao'),
                 withBorder=True, shadow='sm', radius='lg', p='xs', mb='md', className='tz-card'),
        # Guarda-corpo do dia (2026-07-30) — fica ACIMA de tudo na Posição
        # de propósito: é a informação que precisa ser vista antes de abrir a
        # próxima operação, não depois. Só os contadores são atualizados pelo
        # interval; o botão e o checklist são estáticos (ver
        # `_bloco_guarda_corpo_dia`).
        dmc.Card([
            html.Div([
                html.Div(_bloco_guarda_corpo_dia(), id='diario-guarda-corpo-dia',
                         style={'flex': '1'}),
                dmc.Button('Checklist pré-trade', id='btn-diario-checklist', n_clicks=0,
                           variant='outline', color='gray', size='xs', ml='12px',
                           style={'alignSelf': 'center', 'flexShrink': '0'}),
            ], style={'display': 'flex', 'alignItems': 'flex-start'}),
            html.Div(id='diario-checklist-conteudo'),
        ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card'),
        # Sub-abas WIN/WDO (feedback 2026-07-16: "eu quero WDO e WIN
        # separados em sub-abas da aba Posições"). 2026-08-02: convertido
        # de par de `html.Button` + callback de estilo manual pra
        # `dmc.SegmentedControl` real (mesmo padrão do toggle Dia/Semana
        # do Day View) — `selecionar_subaba_posicao`/`atualizar_estilo_
        # subaba_posicao` viraram um único passthrough trivial, ver logo
        # abaixo.
        dcc.Store(id='store-posicao-ativo-subaba', data='WIN'),
        dmc.SegmentedControl(
            id='segmented-posicao-ativo', value='WIN',
            data=[{'label': 'WIN', 'value': 'WIN'}, {'label': 'WDO', 'value': 'WDO'}],
            mb='md',
        ),
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
    """"Abertura (9:00-9:30)" — nome/horário vêm do cadastro editável
    (`diario_trade.listar_janelas`, substitui `cfg.JANELAS_PREGAO` fixo,
    2026-08-04) — ver diario_app_98.py::_formatar_janela_98 pro mesmo
    raciocínio. Slug não encontrado no cadastro (trade antigo com janela
    desativada) mostra a chave crua em vez de sumir."""
    if not chave:
        return '—'
    linhas = dt.listar_janelas(somente_ativas=False)
    linha = linhas[linhas['slug'] == chave]
    if linha.empty:
        return chave
    r = linha.iloc[0]
    return f"{r['nome']} ({r['hora_inicio']}-{r['hora_fim']})"


def _opcoes_janelas(ativo: str | None = None) -> list[dict]:
    """Sem `ativo`, lista o cadastro inteiro (uso em filtros que cruzam
    ativos, ex: Histórico sem filtro de Ativo selecionado). Com `ativo`,
    filtra só as janelas cadastradas pra ele — 2026-08-06, correção do
    Jesué: o mercado internacional (XAUUSD/EURUSD) tem sessões diferentes
    de WIN/WDO/BIT (B3), mostrar Abertura/Prime/Almoço/Tarde/Final (janelas
    de pregão B3) pra editar um trade de XAUUSD é simplesmente errado."""
    linhas = dt.listar_janelas(ativo=ativo)
    return [{'label': f"{r['nome']} ({r['hora_inicio']}-{r['hora_fim']})", 'value': r['slug']}
            for _, r in linhas.iterrows()]


def _mapa_nomes_estrategias() -> dict:
    return {l['slug']: l['nome'] for _, l in dt.listar_estrategias(somente_ativas=False).iterrows()}


def _mapa_categorias_estrategias() -> dict:
    """slug -> rótulo da categoria (Tendência/Reversão/Lateralidade), ou
    '—' quando não classificada — migrado de `dashboard.py` (2026-08-05,
    item 18 do upgrade: "traga o bloco Confiabilidade por Estratégia pro
    módulo Estratégias")."""
    df = dt.listar_estrategias(somente_ativas=False)
    return {
        l['slug']: dt.ROTULO_CATEGORIA_ESTRATEGIA.get(l.get('categoria'), '—')
        for _, l in df.iterrows()
    }


def _agregado_por_categoria(scores: dict, saldos_por_estrategia: dict) -> dict:
    """N/saldo/taxa de acerto agregados por Categoria (Tendência/Reversão/
    Lateralidade/—) — insumo do mini-dashboard de Estratégias (2026-08-06,
    pedido "mini dashboard dentro de Estratégias"). Só entram estratégias
    com trades reais (n > 0); taxa de acerto é ponderada por n de cada
    estratégia, não média simples entre elas."""
    categorias = _mapa_categorias_estrategias()
    agregado: dict = {}
    for tag, v in scores.items():
        n = v['n']
        if not n:
            continue
        saldo = saldos_por_estrategia.get(tag, {}).get('saldo') or 0.0
        cat = categorias.get(tag, '—')
        acc = agregado.setdefault(cat, {'n': 0, 'saldo': 0.0, 'n_win_estimado': 0.0})
        acc['n'] += n
        acc['saldo'] += saldo
        if v['taxa_acerto'] is not None:
            acc['n_win_estimado'] += v['taxa_acerto'] * n
    for acc in agregado.values():
        acc['taxa_acerto'] = (acc['n_win_estimado'] / acc['n']) if acc['n'] else None
        del acc['n_win_estimado']
    return agregado


def _grafico_saldo_por_categoria(agregado: dict, tema: str) -> go.Figure:
    """Barras de saldo por Categoria — único corte que a grade linha-a-linha
    de Confiabilidade não mostra de relance."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    ordem = ['Tendência', 'Reversão', 'Lateralidade']
    categorias = [c for c in ordem if c in agregado] + sorted(c for c in agregado if c not in ordem)
    saldos = [agregado[c]['saldo'] for c in categorias]
    cores = [p['green'] if s >= 0 else p['red'] for s in saldos]
    textos_hover = [
        f"{agregado[c]['n']} trades · {agregado[c]['taxa_acerto'] * 100:.0f}% acerto"
        if agregado[c]['taxa_acerto'] is not None else f"{agregado[c]['n']} trades"
        for c in categorias
    ]
    fig = go.Figure(go.Bar(
        x=categorias, y=saldos, marker_color=cores,
        text=[f"R$ {s:+.0f}" for s in saldos], textposition='outside',
        hovertext=textos_hover, hoverinfo='text',
    ))
    fig.add_hline(y=0, line_width=1, line_color=p['border'])
    fig.update_layout(
        paper_bgcolor=p['panel'], plot_bgcolor=p['panel'],
        font=dict(family=FONTS['body'], color=p['text2'], size=10),
        margin=dict(l=45, r=10, t=20, b=30), height=200,
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5, title='R$', zeroline=False),
        showlegend=False,
    )
    return fig


def _mini_dashboard_estrategias(scores: dict, saldos_por_estrategia: dict, tema: str) -> dmc.Card:
    """Resumo agregado no topo do módulo Estratégias — KPIs + gráfico de
    saldo por Categoria (2026-08-06, pedido do Jesué "mini dashboard dentro
    de Estratégias"): a grade de Confiabilidade mostra tudo linha a linha,
    mas não dá uma leitura de conjunto sem rolar/comparar manualmente."""
    nomes = _mapa_nomes_estrategias()
    elegiveis = [(tag, v) for tag, v in scores.items() if v['amostra_suficiente'] and v['score'] is not None]
    saldo_total = sum((saldos_por_estrategia.get(tag, {}).get('saldo') or 0.0) for tag in scores)
    melhor = max(elegiveis, key=lambda item: item[1]['score']) if elegiveis else None
    pior = min(elegiveis, key=lambda item: item[1]['score']) if elegiveis else None

    kpis = _grupo_mini_estatistica('Visão Geral do Playbook', [
        _card_mini_estatistica('Estratégias Cadastradas', str(len(scores))),
        _card_mini_estatistica('Com Amostra Suficiente', str(len(elegiveis))),
        _card_mini_estatistica('Saldo Total', f"R$ {saldo_total:+.0f}", C['green'] if saldo_total > 0 else C['red']),
        _card_mini_estatistica('Melhor Score',
                                f"{nomes.get(melhor[0], melhor[0])} ({melhor[1]['score']:.0f})" if melhor else '—',
                                C['green'] if melhor else None),
        _card_mini_estatistica('Pior Score',
                                f"{nomes.get(pior[0], pior[0])} ({pior[1]['score']:.0f})" if pior else '—',
                                C['red'] if pior else None),
    ])
    agregado_categoria = _agregado_por_categoria(scores, saldos_por_estrategia)
    return dmc.Card([
        dmc.Text('Visão Geral', size='sm', fw=600, mb='sm'),
        kpis,
        html.Div(style={'height': '14px'}),
        dmc.Text('Saldo por Categoria', size='xs', c='dimmed', mb='xs'),
        dcc.Graph(figure=_grafico_saldo_por_categoria(agregado_categoria, tema),
                  config={'displayModeBar': False, 'responsive': True}, style={'width': '100%'})
        if agregado_categoria else
        dmc.Text('Sem trades reais em nenhuma estratégia ainda.', size='xs', c='dimmed'),
    ], withBorder=True, shadow='sm', radius='lg', p='md', mb='md', className='tz-card', w='100%')


def _linhas_grid_confiabilidade(scores: dict, saldos_por_estrategia: dict) -> list:
    """`rowData` do AG Grid "Confiabilidade por Estratégia" — migrado de
    `dashboard.py::_linhas_grid_confiabilidade` junto com o bloco inteiro
    (2026-08-05, item 18 do upgrade). `tag` fica oculta na coluna (`hide:
    True`) pra identificar a linha clicada (ver `atualizar_detalhe_
    estrategia_grid`)."""
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


def _card_mini_estatistica(rotulo: str, valor: str, cor: str | None = None) -> dmc.Paper:
    """Card pequeno rótulo+valor — versão local de `dashboard.py::_card_
    estatistica`, usada só no detalhe expandido de uma estratégia (ver
    `_detalhe_estrategia_expandido`). Duplicado em vez de importado —
    mesmo precedente já usado pra `PALETA_GAUGE`/`FONTS`/`_hex_para_rgba`
    entre os 2 processos."""
    return dmc.Paper([
        dmc.Text(rotulo, size='10px', c='dimmed', tt='uppercase', style={'letterSpacing': '0.04em'}),
        dmc.Text(valor, size='sm', fw=700, style=({'color': cor} if cor else {})),
    ], p='xs', withBorder=True, radius='md', style={'flex': '1', 'minWidth': '100px'})


def _grupo_mini_estatistica(titulo: str, cards: list) -> html.Div:
    return html.Div([
        html.Div(titulo, style={'fontSize': '12px', 'color': C['text2'], 'fontWeight': '600', 'marginBottom': '8px'}),
        html.Div(cards, style={'display': 'flex', 'gap': '8px', 'flexWrap': 'wrap'}),
    ], style={'padding': '10px', 'borderRadius': '10px', 'background': 'rgba(128,128,128,0.05)', 'flex': '1'})


def _mini_recorte_estrategia(titulo: str, dados: dict) -> html.Div:
    """Coluninha 'chave -> n / saldo' do detalhe expandido (por direção,
    janela, ativo) — migrado de `dashboard.py::_mini_recorte`."""
    if not dados:
        return html.Div()
    linhas = [
        html.Div([
            html.Span(str(k).replace('_', ' ').title(), style={'flex': '1', 'fontSize': '11px'}),
            html.Span(str(v['n']), style={'width': '28px', 'textAlign': 'right', 'fontSize': '11px'}),
            html.Span(f"R$ {v['saldo']:+.0f}", style={
                'width': '78px', 'textAlign': 'right', 'fontSize': '11px',
                'color': C['green'] if v['saldo'] > 0 else (C['red'] if v['saldo'] < 0 else C['text2']),
            }),
        ], style={'display': 'flex', 'padding': '3px 0'})
        for k, v in dados.items()
    ]
    return html.Div([
        html.Div(titulo, style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '3px', 'fontWeight': '600'}),
        html.Div(linhas),
    ], style={'flex': '1', 'minWidth': '190px'})


def _detalhe_estrategia_expandido(tag: str) -> html.Div:
    """Conteúdo da linha expandida — ver `dt.detalhe_estrategia` (a conta
    mora lá, compartilhada entre os 2 skins). Migrado de `dashboard.py`
    (2026-08-05, item 18 do upgrade)."""
    d = dt.detalhe_estrategia(tag)
    if not d['n']:
        return html.Div('Sem trades reais neste setup.',
                        style={'padding': '10px', 'fontSize': '11px', 'color': C['text2']})

    payoff = f"{d['payoff']:.2f}" if d['payoff'] is not None else '—'
    grupo_resultado = _grupo_mini_estatistica('Resultado', [
        _card_mini_estatistica('Operações', str(d['n'])),
        _card_mini_estatistica('Saldo', f"R$ {d['saldo_total']:+.2f}", C['green'] if d['saldo_total'] > 0 else C['red']),
        _card_mini_estatistica('Wins', str(d['n_win']), C['green']),
        _card_mini_estatistica('Losses', str(d['n_loss']), C['red']),
        _card_mini_estatistica('Zero a zero', str(d['zero_a_zero'])),
        _card_mini_estatistica('Expectativa', f"R$ {d['expectativa']:+.2f}", C['green'] if d['expectativa'] > 0 else C['red']),
    ])
    grupo_qualidade = _grupo_mini_estatistica('Qualidade', [
        _card_mini_estatistica('Taxa de Acerto', f"{d['taxa_acerto'] * 100:.1f}%"),
        _card_mini_estatistica('Payoff', payoff),
        _card_mini_estatistica('Max Gain', f"R$ {d['max_gain']:+.2f}", C['green']),
        _card_mini_estatistica('Max Loss', f"R$ {d['max_loss']:+.2f}", C['red']),
        _card_mini_estatistica('Drawdown Máx.', f"R$ {d['drawdown_maximo_reais']:+.2f}", C['red']),
        _card_mini_estatistica('Seq. Wins', str(d['maior_sequencia_win'])),
        _card_mini_estatistica('Seq. Losses', str(d['maior_sequencia_loss'])),
    ])
    return html.Div([
        html.Div([grupo_resultado, grupo_qualidade],
                 style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginBottom': '12px'}),
        html.Div([
            _mini_recorte_estrategia('Por direção', d['por_direcao']),
            _mini_recorte_estrategia('Por janela', d['por_janela']),
            _mini_recorte_estrategia('Por ativo', d['por_ativo']),
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
    ], style={'padding': '12px 14px', 'background': C['bg'], 'borderLeft': f'2px solid {C["gold"]}', 'marginBottom': '6px'})


def _tabela_score_confiabilidade(scores: dict, saldos_por_estrategia: dict | None = None) -> html.Div:
    """Bloco "Confiabilidade por Estratégia" — migrado de `dashboard.py`
    (2026-08-05, item 18 do upgrade: "traga o bloco... para o módulo
    Estratégias"). `dag.AgGrid` real: sort/filter/resize nativos do
    componente, clique numa linha abre o detalhe abaixo (`atualizar_
    detalhe_estrategia_grid`) — `getRowId` NÃO usado de propósito (mesma
    ressalva já documentada no bug irmão do Histórico: colapsava a grade
    inteira pra 1 linha; clique resolvido via `rowIndex` + `State` do
    `rowData`)."""
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
             'valueFormatter': {'function': "params.data.saldo_label"}, 'cellStyle': _CELL_STYLE_RESULTADO_HISTORICO},
            {'field': 'taxa_acerto', 'headerName': 'Taxa Acerto', 'sortable': True, 'filter': True},
            {'field': 'payoff', 'headerName': 'Payoff', 'sortable': True, 'filter': True},
            {'field': 'profit_factor', 'headerName': 'Fator de Lucro', 'sortable': True, 'filter': True},
            {'field': 'score', 'headerName': 'Score', 'sortable': True, 'filter': True,
             'valueFormatter': {'function': "params.data.score_label"}, 'cellStyle': _CELL_STYLE_SCORE_HISTORICO},
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


def _formatar_tags_estrategia(tags, mapa_nomes: dict) -> str:
    lista = list(tags) if hasattr(tags, '__iter__') and not isinstance(tags, str) else []
    return ', '.join(mapa_nomes.get(t, t.replace('_', ' ').title()) for t in lista) or '—'


def _construir_diario_historico() -> html.Div:
    # Feedback 2026-07-17: a seção "Pendente de Classificação" que morava
    # aqui (desde 2026-07-16) voltou pra aba Posição — trade fechado ainda
    # precisa de 1 clique consciente em "Finalizar" antes de virar registro
    # permanente, mas isso agora acontece sem trocar de aba (ver
    # `_renderizar_posicao_completa`).
    #
    # Item F, Fase "rollout" (2026-07-31, Task #37): a `html.Table` montada
    # linha a linha à mão (capada em 50/300 linhas — Task #29, "Histórico
    # lento") vira um `dag.AgGrid` real (sort/filter/virtualização nativos,
    # SEM corte nenhum — `_linhas_grid_historico` já devolve a base inteira).
    # Pendências (execuções sem trade classificado ainda) continuam FORA do
    # grid, numa lista pequena acima dele — são poucas, precisam de ações
    # próprias (Editar/✓ finalizar rápido) que um `dag.AgGrid` sem
    # cellRenderer customizado não hospeda bem, e misturá-las no mesmo grid
    # obrigaria a inventar 2 esquemas de coluna diferentes na mesma tabela.
    #
    # Filtro de data com valor inicial (2026-08-05, pedido do Jesué: "os
    # filtros de data em cima preenchidos para mostrar onde estamos") — a
    # última semana civil com trade real, mesmo período que o Day View
    # mostra por padrão (`_intervalo_ultima_semana`). Antes os filtros
    # nasciam vazios (mostrando TODA a base, "213 operações" sem contexto
    # nenhum de "onde eu estou agora").
    _data_ini_padrao, _data_fim_padrao = _intervalo_ultima_semana()
    return html.Div([
        # Os 2 overlays (imagem ampliada e pop-up de pendência) NÃO moram mais
        # aqui — subiram pro `app.layout` global, logo ACIMA de
        # `diario-sub-aba-conteudo`. Ver o comentário lá pro motivo (eram
        # Output de callbacks disparadas por Store globais, uma combinação que
        # quebra fora do Histórico). A posição no topo do conteúdo, que é o
        # que o feedback de 2026-07-20 pedia ("a imagem deve abrir em pop-up e
        # não no final da lista"), foi preservada.
        #
        # Cabeçalho/filtros reescritos com `dmc.Card`/`dmc.Select`/
        # `dmc.DateInput`/`dmc.Button` reais (2026-07-31, correção do Jesué:
        # "esse cabeçalho parece não ter entrado no novo padrão tradezella"
        # — até aqui só o GRID tinha virado biblioteca real, a moldura ao
        # redor (`_bloco_diario`/`classico-dropdown`/`<input type=date>`
        # nativo) continuava 100% no sistema visual antigo). Mesmo padrão
        # de card (`withBorder`/`shadow='sm'`/`radius='lg'`/`className=
        # 'tz-card'`) já usado em `_bloco_tradezella` (dashboard.py) —
        # duplicado aqui em vez de importado porque os dois processos Dash
        # já compartilham esse tipo de duplicação por precedente (`C`/
        # `PALETA_GAUGE`/`FONTS`, `_formatar_janela`/`_mapa_nomes_
        # estrategias`). `dmc.Select`/`dmc.DateInput` expõem `value` no
        # mesmo formato de antes (string simples / ISO `AAAA-MM-DD`,
        # confirmado em teste isolado) — nenhuma callback de filtro
        # precisou mudar.
        dmc.Card([
            dmc.Group([
                # "Histórico" (2026-08-05, correção após auditoria de UX —
                # o rótulo antigo repetia "Diário de Trade", que já aparece
                # no cabeçalho FORA do iframe (`dashboard.py::secao`),
                # olhando redundante/inacabado. Cada outra sub-aba do
                # Diário (Estratégias, Posição etc.) já nomeia seu próprio
                # card com o nome específico da seção, nunca repetindo o
                # nome do módulo inteiro — este card só seguiu o padrão.
                dmc.Text('Histórico', size='sm', fw=600, tt='uppercase',
                         style={'letterSpacing': '0.05em', 'color': C['gold']}),
                # Indicador do recorte filtrado (2026-08-01, pedido do
                # Jesué: "melhore esse cabeçalho... veja se faz sentido
                # adicionar algum indicador" — referência TradeZella, que
                # mostra a contagem de trades ao lado do título do Trade
                # View). Preenchido por `atualizar_historico`, junto do
                # `rowData` do grid — reflete SEMPRE o conjunto já
                # filtrado, nunca a base inteira.
                html.Div(id='diario-hist-indicador'),
            ], justify='space-between', mb='md'),
            dmc.Group([
                dmc.Button('Novo Trade', id='btn-abrir-novo-trade', n_clicks=0, color='yellow',
                           leftSection=DashIconify(icon='tabler:plus', width=16)),
                # Importar Extrato (2026-08-05) — trades cTrader (XAUUSD/
                # EURUSD) enquanto o histórico automático (Fase 1, Open API)
                # segue bloqueado pela aprovação da Spotware.
                dmc.Button('Importar Trades', id='btn-abrir-importar-extrato', n_clicks=0,
                           variant='outline', color='gray',
                           leftSection=DashIconify(icon='tabler:file-upload', width=16)),
                dmc.Group([
                    dmc.Select(id='diario-hist-ativo', data=_opcoes_diario(cfg.ATIVOS_DIARIO),
                               placeholder='Ativo', clearable=True, w=140),
                    dmc.Select(id='diario-hist-janela', data=_opcoes_janelas(),
                               placeholder='Janela', clearable=True, w=190),
                    dmc.Select(id='diario-hist-estrategia', data=_opcoes_estrategias_agrupadas(),
                               placeholder='Estratégia', clearable=True, w=200),
                ], gap='8px'),
                dmc.Group([
                    dmc.DateInput(id='diario-hist-data-ini', placeholder='Data início',
                                  value=_data_ini_padrao,
                                  valueFormat='DD/MM/YYYY', clearable=True, w=150),
                    html.Span('até', style={'fontSize': '12px', 'color': C['text2'], 'alignSelf': 'center'}),
                    dmc.DateInput(id='diario-hist-data-fim', placeholder='Data fim',
                                  value=_data_fim_padrao,
                                  valueFormat='DD/MM/YYYY', clearable=True, w=150),
                ], gap='8px'),
            ], gap='20px', justify='flex-start', wrap='wrap'),
        ], withBorder=True, shadow='sm', radius='lg', p='md', mb='md', className='tz-card', w='100%'),
        # Calendário de range SEMPRE visível (item 2D do upgrade de
        # 2026-08-04 — pedido explícito: "deixe um calendário sempre à
        # mostra... clicando direto no calendário, como os sites de
        # reserva de hotel"). Mantém os 2 `dmc.DateInput` de texto acima
        # pra quem prefere digitar — sincronizados nos 2 sentidos por
        # `sincronizar_filtro_data_range`, nunca 2 fontes de verdade
        # divergentes.
        #
        # Emparelhado com a Evolução de Saldo NA MESMA linha (2026-08-05,
        # correção pós-auditoria de UX — o calendário sozinho tem largura
        # `fit-content` e sobrava ~800px de área morta à direita dele,
        # achado real com o Jesué olhando um screenshot real). O gráfico
        # ocupa o espaço restante (`flex: 1`) em vez de ficar empilhado
        # embaixo em largura cheia — mesma informação, sem espaço vazio e
        # com menos rolagem de página.
        dmc.Group([
            dmc.Card(
                dmc.DatePicker(id='diario-hist-calendario-range', type='range',
                               value=[_data_ini_padrao, _data_fim_padrao],
                               numberOfColumns=2, allowSingleDateInRange=True, size='xs'),
                withBorder=True, shadow='sm', radius='lg', p='sm', className='tz-card',
                w='fit-content',
            ),
            html.Div(id='diario-hist-evolucao-periodo', style={'flex': 1, 'minWidth': '320px'}),
        ], align='flex-start', gap='16px', wrap='wrap', mb='md'),
        html.Div(id='diario-hist-banner-filtro-cal', style={'marginBottom': '10px'}),
        html.Div(id='diario-hist-pendencias'),
        # Store único, atualizado por `atualizar_historico` (junto do
        # `rowData` do grid) — os 3 modos de visualização (Grade/Galeria/
        # Dia, Tasks #29/#30/#31) leem do MESMO conjunto já filtrado, sem
        # repetir a query/filtro cada um por conta própria.
        dcc.Store(id='store-diario-linhas-filtradas', data=[]),
        dcc.Store(id='store-diario-galeria-pagina', data=1),
        dcc.Store(id='store-diario-dia-atual', data=None),
        # Galeria como padrão (2026-08-01, pedido do Jesué) — `value`/
        # `style` iniciais dos 3 containers já nascem corretos (Galeria
        # visível, Grade/Dia ocultos), `alternar_view_modo_historico`
        # confirma a mesma coisa no primeiro disparo (Input sem
        # `prevent_initial_call`), então não há flash do modo errado.
        # Linha única (2026-08-05, pedido do Jesué com screenshot: "coloque
        # esses botões na mesma linha e melhore o design") — o seletor de
        # view (Dia/Galeria/Grade) e as setas de navegação por semana
        # (só relevantes no modo Dia) viviam em 2 linhas empilhadas com um
        # rótulo de data solto no meio ("essa data feia no meio não
        # precisa" — removido, ver `renderizar_dia_historico`). Setas
        # viraram `dmc.ActionIcon` compactos (só ícone, sem texto) em vez
        # de botões largos com label — mais limpo, e o contexto de qual
        # semana já aparece nos filtros de data acima (preenchidos com a
        # última semana por padrão).
        dmc.Group([
            dmc.SegmentedControl(
                id='diario-hist-view-modo',
                # Dia como padrão (item 2C do upgrade de 2026-08-04, pedido
                # explícito do Jesué) — ordem e valor inicial trocados de
                # Grade/Galeria/Dia (Galeria padrão) para Dia/Galeria/Grade.
                value='dia',
                data=[
                    {'label': 'Dia', 'value': 'dia'},
                    {'label': 'Galeria', 'value': 'galeria'},
                    {'label': 'Grade', 'value': 'grid'},
                ],
            ),
            dmc.Group([
                dmc.ActionIcon(DashIconify(icon='tabler:chevron-left', width=18), id='btn-dia-anterior',
                               n_clicks=0, disabled=True, variant='subtle', color='gray', size='lg',
                               **{'aria-label': 'Semana anterior'}),
                dmc.ActionIcon(DashIconify(icon='tabler:chevron-right', width=18), id='btn-dia-proximo',
                               n_clicks=0, disabled=True, variant='subtle', color='gray', size='lg',
                               **{'aria-label': 'Próxima semana'}),
            ], id='diario-dia-nav-botoes', gap='4px'),
            # Paginação da Galeria (2026-08-05, pedido do Jesué com
            # screenshot: "coloque os botões do modo galeria também na
            # mesma linha") — mesmo tratamento das setas do Dia: `dmc.
            # ActionIcon` ícone-only, na mesma linha do seletor de view,
            # visível só no modo Galeria. `diario-galeria-label` mantido
            # (ao contrário do rótulo de data do Dia, "Página X de Y (N
            # operações)" é informação nova, não redundante com o resto
            # da tela).
            dmc.Group([
                dmc.ActionIcon(DashIconify(icon='tabler:chevron-left', width=18), id='btn-galeria-pag-anterior',
                               n_clicks=0, disabled=True, variant='subtle', color='gray', size='lg',
                               **{'aria-label': 'Página anterior'}),
                html.Span(id='diario-galeria-label', style={'fontSize': '12px', 'color': C['text2']}),
                dmc.ActionIcon(DashIconify(icon='tabler:chevron-right', width=18), id='btn-galeria-pag-proxima',
                               n_clicks=0, disabled=True, variant='subtle', color='gray', size='lg',
                               **{'aria-label': 'Próxima página'}),
            ], id='diario-galeria-nav-botoes', gap='8px', style={'display': 'none'}),
        ], justify='space-between', align='center', mb='md'),
        html.Div(id='diario-view-grid', style={'display': 'none'}, children=[
            _grid_historico(_linhas_grid_historico(dt.listar_trades(is_simulated=False))),
        ]),
        # Galeria (Task #30) — botões de página ESTÁTICOS (nunca recriados,
        # regra de sempre): só `diario-galeria-grade`/`-label`/`disabled`
        # dos botões mudam via `renderizar_galeria_historico`. Botões de
        # navegação subiram pra linha do seletor de view (ver acima).
        html.Div(id='diario-view-galeria', style={'display': 'none'}, children=[
            html.Div(id='diario-galeria-grade'),
        ]),
        # Day View (Task #31) — mesma disciplina: setas de navegação
        # ESTÁTICAS. Item 2B do upgrade de 2026-08-04 (pedido explícito:
        # "a visualização semanal deve ser o único modo... não faz sentido
        # ter o outro"): toggle Dia/Semana removido, Semana é o único modo
        # — empilha os dias da semana civil que contém `dia_atual`, cada um
        # recolhível (`_cartao_dia_semana`/`store-diario-semana-
        # expandido`). As setas agora navegam por SEMANA (não mais dia a
        # dia), consequência direta de Semana virar o único modo — ver
        # `navegar_dia_historico`.
        html.Div(id='diario-view-dia', children=[
            dcc.Store(id='store-diario-semana-expandido', data={}),
            html.Div(id='diario-dia-conteudo'),
        ]),
        # Detalhe da operação — barra lateral (2026-07-31, correção do
        # Jesué: "não tente enfiar os detalhes do trade no bloco abaixo...
        # abra um pop-up ou uma barra lateral", depois refinado: "deve
        # surgir do canto da tela, com animação suave, sombras e estilo" —
        # painel FLUTUANTE (`offset`+`radius`+`shadow`, não mais um drawer
        # colado na borda) com overlay bem leve (`overlayProps`, feedback:
        # "o box da galeria não deve ficar escuro" — o overlay padrão do
        # Mantine escurecia demais o conteúdo atrás).
        #
        # `transitionProps` teve que ser calibrado com cuidado: a
        # ferramenta de teste isolado usada nesta sessão (browser
        # automatizado) NUNCA renderiza o Drawer quando `opened` muda via
        # callback E a transição tem duração > 0 — só funciona com
        # `duration: 0` ali. Confirmado que essa limitação é da FERRAMENTA
        # de teste, não do app real: o mecanismo de animação do Mantine
        # (CSS transition padrão, usado em produção por milhares de apps)
        # não depende de nada específico do Dash pra funcionar num
        # navegador de verdade. Duração restaurada pra 220ms com
        # transição 'slide-left' (entra da direita, combina com
        # `position='right'`) — testado, aparência (offset/radius/shadow/
        # overlay claro) confirmada, MAS a suavidade da animação em si só
        # pode ser conferida numa tela real, não nesta ferramenta.
        # `size='1160px'` (2026-07-31, pedido do Jesué: "aumente a largura
        # da janela em 2x para que mostre o máximo de informações sem
        # precisar de barra de rolagem") — o dobro do `lg` padrão do
        # Mantine (~580px). A grade de campos dentro (`_secao_detalhe_
        # trade`, `repeat(auto-fit, minmax(160px, 1fr))`) já é responsiva:
        # com mais largura disponível ela sozinha passa a mostrar mais
        # colunas por linha, sem precisar de nenhum ajuste adicional aqui.
        dmc.Drawer(
            id='drawer-detalhe-trade',
            opened=False,
            position='right',
            size='1160px',
            offset=16,
            radius='md',
            shadow='xl',
            title='Detalhe da Operação',
            transitionProps={'duration': 220, 'transition': 'slide-left'},
            overlayProps={'opacity': 0.15, 'blur': 2},
            children=html.Div(id='drawer-detalhe-trade-conteudo'),
        ),
    ])


_CELL_STYLE_RESULTADO_HISTORICO = {
    'function': "params.value > 0 ? {'color': 'var(--green)', 'fontWeight': 700} "
                ": params.value < 0 ? {'color': 'var(--red)', 'fontWeight': 700} : null"
}

_CELL_STYLE_STATUS_HISTORICO = {
    'function': "params.value === 'Win' ? {'color': 'var(--green)', 'fontWeight': 700} "
                ": params.value === 'Loss' ? {'color': 'var(--red)', 'fontWeight': 700} "
                ": {'color': 'var(--text2)', 'fontWeight': 700}"
}

# Mesma escala de 3 faixas já usada em `_CELL_STYLE_SCORE` (dashboard.py,
# Confiabilidade por Estratégia) — >=70 verde, >=40 amarelo, resto
# vermelho. `params.value == null` (trade sem traçado casado) cai no
# `: null` final, sem cor (`score_setup_label` já mostra "—" nesse caso).
_CELL_STYLE_SCORE_HISTORICO = {
    'function': "params.value >= 70 ? {'color': 'var(--green)', 'fontWeight': 700} "
                ": params.value >= 40 ? {'color': 'var(--yellow)', 'fontWeight': 700} "
                ": params.value >= 0 ? {'color': 'var(--red)', 'fontWeight': 700} : null"
}


def _resultado_trade(trade) -> tuple[float | None, str, str, str | None]:
    """Resultado formatado, moeda-aware (2026-08-06, correção do Jesué:
    "os trades internacionais devem mostrar o valor em dólar, e como
    informação extra (fonte menor) o valor convertido em reais" — a versão
    anterior só mostrava R$ pra QUALQUER ativo, escondendo o valor nativo
    de XAUUSD/EURUSD). Retorna `(saldo_reais_num, rotulo_compacto,
    rotulo_principal, rotulo_secundario)`:
    - `saldo_reais_num` é sempre o número em BRL — usado pra cor (win/loss)
      e status; a consolidação/soma continua SEMPRE em BRL, sem exceção
      (só a exibição muda).
    - `rotulo_compacto`: "R$ +X,XX" (ativo BRL) ou "US$ +X,XX (R$ +Y,YY)"
      (ativo USD) — pra contextos de uma linha só (grid/galeria/dia).
    - `rotulo_principal`/`rotulo_secundario`: pro Drawer de detalhe, que
      tem espaço pra mostrar o USD grande e o R$ pequeno embaixo dele.
      `rotulo_secundario` é None pra ativo BRL (nada extra a mostrar)."""
    saldo = trade.get('saldo_operacao_reais')
    saldo_num = float(saldo) if pd.notna(saldo) else None
    moeda = cfg.MOEDA_ATIVO.get(trade.get('ativo'), 'BRL')
    saldo_usd = trade.get('saldo_operacao_usd')
    saldo_usd_num = float(saldo_usd) if pd.notna(saldo_usd) else None
    if moeda == 'USD' and saldo_usd_num is not None:
        principal = f"US$ {saldo_usd_num:+.2f}"
        secundario = f"R$ {saldo_num:+.2f}" if saldo_num is not None else None
        compacto = f"{principal} ({secundario})" if secundario else principal
        return saldo_num, compacto, principal, secundario
    rotulo = f"R$ {saldo_num:+.2f}" if saldo_num is not None else '—'
    return saldo_num, rotulo, rotulo, None


def _linhas_grid_historico(df: pd.DataFrame) -> list:
    """`rowData` do AG Grid do Histórico — mesmo padrão de
    `_linhas_grid_confiabilidade` (dashboard.py): um dict por linha, campo
    oculto (`id_trade`) pra identificar a linha clicada e buscar
    `_conteudo_detalhe_trade`. SEM corte de linhas — `dag.AgGrid` virtualiza
    nativamente (renderiza só as linhas visíveis no scroll), diferente da
    `html.Table` antiga (todo `<tr>` vira nó real no DOM).

    Coluna "Score" (Playbook, 2026-08-02) — REDESENHADA: antes vinha de
    `_score_qualidade_setup` (aderência a traçado de S/R via `dt.
    avaliar_aderencia_tracados`); agora vem de `dt.calcular_trade_score`
    (Anticovardia Score + Aderência ao Playbook). Aderência a traçado
    CONTINUA existindo/rodando — só deixou de contar pra qualidade do
    trade, virou dado de bastidores pro Quant Lab/backtesting/treino do
    motor de price action (por isso NÃO é mais lida aqui)."""
    if df.empty:
        return []
    anticovardia_atual = dt.calcular_anticovardia_score()['score']
    mapa_nomes = _mapa_nomes_estrategias()
    linhas = []
    for _, trade in df.sort_values('data_op', ascending=False).iterrows():
        id_trade = int(trade['id_trade']) if pd.notna(trade.get('id_trade')) else None
        saldo_num, resultado_label, resultado_principal, resultado_secundario = _resultado_trade(trade)
        status = 'Win' if (saldo_num or 0) > 0 else ('Loss' if (saldo_num or 0) < 0 else 'BE')
        r_mult = dt.r_multiple_trade(trade)
        score_setup = dt.calcular_trade_score(trade.to_dict(), anticovardia_score_atual=anticovardia_atual)
        score_setup = round(score_setup) if score_setup is not None else None
        tem_nota = bool(trade.get('resumo_operacional') or trade.get('obs'))
        tem_imagem = bool(trade.get('imagem_arquivo'))
        # Célula de dag.AgGrid — valor precisa ser texto puro (JSON pro grid
        # JS, não passa pelo pipeline normal de componentes Dash), por isso
        # texto simples em vez de ícone aqui (2026-08-03: emoji removido).
        notas_label = ('Print+Nota' if tem_imagem and tem_nota else 'Print' if tem_imagem else 'Nota' if tem_nota else '—')
        datetime_entrada = trade.get('datetime_entrada')
        hora = (pd.Timestamp(datetime_entrada).strftime('%H:%M')
                if pd.notna(datetime_entrada) else None)
        linhas.append({
            'id_trade': id_trade,
            'data': str(trade['data_op']),
            'hora': hora,
            'ativo': trade.get('ativo'),
            'status': status,
            'entrada': float(trade['preco_entrada']) if pd.notna(trade.get('preco_entrada')) else None,
            'saida': float(trade['preco_saida']) if pd.notna(trade.get('preco_saida')) else None,
            'resultado': saldo_num if saldo_num is not None else 0.0,
            # `resultado_label` (texto puro, compacto) é só pro grid
            # (dag.AgGrid — célula é texto sem estilo, não dá pra empilhar
            # 2 linhas). Galeria/Dia usam `resultado_principal`/`_secundario`
            # (2026-08-06, pedido do Jesué: R$ convertido embaixo do US$,
            # fonte menor — não entre parênteses na mesma linha).
            'resultado_label': resultado_label,
            'resultado_principal': resultado_principal,
            'resultado_secundario': resultado_secundario,
            'r_multiplo': round(r_mult, 2) if r_mult is not None else None,
            'estrategia': _formatar_tags_estrategia(trade.get('estrategia_tags'), mapa_nomes) or '—',
            'score_setup': score_setup,
            'score_setup_label': str(score_setup) if score_setup is not None else '—',
            'notas': notas_label,
            'acao_label': 'Ver',
            # Campos abaixo NÃO entram em `columnDefs` do grid (Task #37) —
            # servem só pra Galeria/Day View (Task #30/#31, mesmo Store
            # `store-diario-linhas-filtradas` alimenta os 3 modos).
            'imagem_arquivo': trade.get('imagem_arquivo') or None,
            # `direcao_label`/`lote` (2026-08-02, rebuild do Day View fiel
            # ao TradeZella — "Side"/"Volume" da referência): só pro modo
            # Dia/Semana, mesmo raciocínio de não entrar no grid.
            'direcao_label': _ROTULO_DIRECAO.get(trade.get('tipo_operacao'), trade.get('tipo_operacao') or '—'),
            'lote': float(trade['lote']) if pd.notna(trade.get('lote')) else None,
        })
    return linhas


def _grid_historico(linhas: list) -> dag.AgGrid:
    return dag.AgGrid(
        id='grid-historico',
        rowData=linhas,
        # 2026-08-02: `getRowId` foi tentado aqui e REVERTIDO — colapsou
        # a grade inteira pra 1 linha só (o Jesué pegou isso ao vivo:
        # "aparece apenas um trade na grade"). `params.data.id_trade`
        # dentro do `getRowId` avaliava sempre pra `undefined` por uma
        # razão ainda não identificada (mesmo com `dangerously_allow_
        # code=True` e a sintaxe certa pra prop STRING-com-código de
        # nível superior do `dag.AgGrid`), então toda linha recebia o
        # mesmo id "undefined" e o AG-Grid deduplicava pra 1 linha. Causa
        # raiz do "Ver" agora resolvida de outro jeito, sem getRowId — ver
        # `atualizar_detalhe_historico_grid` (usa `rowIndex` + `State` do
        # `rowData`, não a identidade de linha do grid).
        # 2026-08-02, 2ª correção do Jesué: "o botão Ver ainda não funciona"
        # mesmo depois de `pinned:'right'` (tentativa anterior). Causa raiz
        # revista: 11 colunas somavam 1330px de largura mínima contra
        # ~1249px de viewport — só 81px de estouro, mas o suficiente pra
        # "Ação" (a ÚLTIMA coluna) sair da faixa renderizada. Em vez de
        # pinar (não dá pra confirmar com confiança que colunas pinadas
        # encaminham `cellClicked` pro Dash de forma tão confiável quanto
        # as colunas centrais nesta versão do dash-ag-grid — sem um clique
        # real de navegador pra testar, prefiro não apostar nisso), a
        # correção agora é ELIMINAR o estouro: larguras fixas cortadas o
        # suficiente pra sobrar espaço de sobra pras 2 colunas flex
        # (Estratégia/Resultado), sem precisar de nenhum comportamento
        # especial de coluna — a MESMA renderização de coluna central já
        # validada desde que este grid foi construído (Task #37).
        columnDefs=[
            {'field': 'id_trade', 'hide': True},
            {'field': 'data', 'headerName': 'Data', 'sortable': True, 'filter': True, 'maxWidth': 100},
            {'field': 'ativo', 'headerName': 'Ativo', 'sortable': True, 'filter': True, 'maxWidth': 80},
            {'field': 'status', 'headerName': 'Status', 'sortable': True, 'filter': True,
             'maxWidth': 85, 'cellStyle': _CELL_STYLE_STATUS_HISTORICO},
            {'field': 'entrada', 'headerName': 'Entrada', 'sortable': True, 'filter': True, 'maxWidth': 90},
            {'field': 'saida', 'headerName': 'Saída', 'sortable': True, 'filter': True, 'maxWidth': 90},
            {'field': 'resultado', 'headerName': 'Resultado', 'sortable': True, 'filter': True,
             'valueFormatter': {'function': "params.data.resultado_label"},
             'cellStyle': _CELL_STYLE_RESULTADO_HISTORICO},
            {'field': 'r_multiplo', 'headerName': 'R-Múltiplo', 'sortable': True, 'filter': True, 'maxWidth': 95},
            {'field': 'estrategia', 'headerName': 'Estratégia', 'sortable': True, 'filter': True, 'flex': 2},
            # "Score" (2026-08-01, redesenhado 2026-08-02 — ver docstring de
            # `_linhas_grid_historico`): Anticovardia Score + Aderência ao
            # Playbook, não mais aderência a traçado. `—` em trades sem
            # nenhum dado de Playbook (todo trade anterior à feature).
            {'field': 'score_setup', 'headerName': 'Score', 'sortable': True, 'filter': 'agNumberColumnFilter',
             'maxWidth': 85, 'valueFormatter': {'function': "params.data.score_setup_label"},
             'cellStyle': _CELL_STYLE_SCORE_HISTORICO},
            {'field': 'notas', 'headerName': 'Notas', 'sortable': True, 'filter': False, 'maxWidth': 75},
            # "Ação" (2026-08-01, pedido do Jesué: "precisa ter alguma forma
            # de abrir os detalhes... seja com botão ou duplo clique") —
            # rótulo fixo só pra deixar ÓBVIO que a linha é clicável; o
            # clique em QUALQUER célula já abre o detalhe (`cellClicked`,
            # ver `atualizar_detalhe_historico_grid`).
            #
            # 2026-08-02, causa raiz REAL do "Ver não funciona" (achada com
            # clique de verdade no Chrome real via claude-in-chrome, não a
            # ferramenta de teste isolada — aquela tinha limitações
            # confirmadas à parte): toda coluna com `sortable: False` NÃO
            # disparava `cellClicked` nenhum (confirmado clicando 'notas' e
            # 'acao_label' repetidamente, 0 requisições de rede; qualquer
            # coluna com `sortable: True` disparava normalmente, inclusive
            # 'score_setup'/'estrategia' testadas ao vivo). Nesta versão do
            # dash-ag-grid, `sortable: False` aparentemente suprime o
            # listener de clique da célula inteira, não só o cabeçalho.
            # `sortable: True` aqui é inofensivo (todas as linhas têm o
            # mesmo texto "Ver", ordenar por ele não muda nada) e restaura
            # o clique.
            {'field': 'acao_label', 'headerName': '', 'sortable': True, 'filter': False, 'maxWidth': 75,
             'cellStyle': {'color': 'var(--gold)', 'fontWeight': 700, 'cursor': 'pointer'}},
        ],
        defaultColDef={'resizable': True, 'sortable': True, 'flex': 1},
        # `domLayout: 'autoHeight'` (2026-08-01, pedido do Jesué: "expanda
        # essa tabela pra se ajustar ao tamanho da janela de forma
        # responsiva") — o grid cresce pra caber as linhas da PÁGINA atual,
        # sem barra de rolagem própria; só a página inteira rola (mesma
        # filosofia já estabelecida em `diario_iframe_resize.js`: UMA
        # barra de rolagem só, nunca duas aninhadas). `height` fixo em px
        # NÃO é mais usado de propósito — `autoHeight` ignora esse estilo e
        # depender de `vh` aqui seria perigoso (o iframe se auto-redimensiona
        # pro tamanho do CONTEÚDO, então `vh` dentro dele mede a altura já
        # inflada, não o viewport real — mesma classe de bug documentada em
        # `assets/style.css`/`diario_iframe_resize.js` pro `position:fixed`).
        # `paginationPageSize` menor (20, era 50) mantém cada página numa
        # altura de tela razoável mesmo com autoHeight.
        # 2026-08-02, causa raiz REAL do "Ver não abre o detalhe" —
        # achada clicando de verdade no Chrome real via claude-in-chrome,
        # inspecionando o payload de rede do próprio POST
        # /_dash-update-component: o `cellClicked` desta versão do
        # dash-ag-grid NUNCA trouxe a linha inteira em `data` — só
        # `{value, colId, rowIndex, rowId, timestamp}`. O código (e o
        # comentário antigo logo abaixo, agora corrigido) assumia
        # `cell_clicked['data']`, que é sempre None — ou seja, o clique
        # nunca abriu o Drawer, em NENHUMA coluna, desde que este grid
        # existe (Task #37). `getRowId` (acima, prop de nível superior)
        # fixa a identidade de cada linha em `id_trade` (estável mesmo
        # com sort/paginação client-side, ao contrário de `rowIndex`) —
        # o callback lê direto de `cell_clicked['rowId']`.
        dashGridOptions={'domLayout': 'autoHeight', 'pagination': True, 'paginationPageSize': 20,
                          'paginationPageSizeSelector': [10, 20, 50, 100]},
        style={'width': '100%'},
        className='ag-theme-alpine',
    )


_GALERIA_PAGINA_TAMANHO = 24


def _card_galeria_trade(linha: dict) -> html.Div:
    """1 card da Galeria (Task #30, "upgrade diario.docx" — referência
    Notion "Trader's Second Brain": grade de cards com imagem de capa +
    metadados abaixo). Clique abre o mesmo Drawer lateral do grid
    (`drawer-detalhe-trade`) — ver `abrir_detalhe_galeria`."""
    imagem = (
        html.Img(src=f"/diario-imagens/{linha['imagem_arquivo']}", style={
            'width': '100%', 'height': '140px', 'objectFit': 'cover', 'borderRadius': '10px 10px 0 0',
            'display': 'block',
        }) if linha.get('imagem_arquivo') else
        html.Div('Sem print', style={
            'width': '100%', 'height': '140px', 'display': 'flex', 'alignItems': 'center',
            'justifyContent': 'center', 'background': C['bg'], 'color': C['text2'], 'fontSize': '12px',
            'borderRadius': '10px 10px 0 0',
        })
    )
    cor = C['green'] if linha['resultado'] > 0 else (C['red'] if linha['resultado'] < 0 else C['text2'])
    return html.Div([
        imagem,
        html.Div([
            html.Div([
                html.Span(linha['data'], style={'fontSize': '11px', 'color': C['text2']}),
                html.Span(linha['ativo'], style={'fontSize': '11px', 'fontWeight': '700', 'marginLeft': '8px'}),
            ]),
            html.Div([
                html.Div(linha['resultado_principal'], style={'fontSize': '16px', 'fontWeight': '700', 'color': cor}),
            ] + ([html.Div(linha['resultado_secundario'], style={
                'fontSize': '11px', 'fontWeight': '500', 'color': C['text2'],
            })] if linha.get('resultado_secundario') else []), style={'marginTop': '2px'}),
            html.Div(linha['estrategia'], style={
                'fontSize': '11px', 'color': C['text2'], 'marginTop': '2px', 'whiteSpace': 'nowrap',
                'overflow': 'hidden', 'textOverflow': 'ellipsis',
            }),
        ], style={'padding': '8px 10px'}),
    ], id={'type': 'diario-galeria-card', 'index': linha['id_trade']}, n_clicks=0, className='tz-card', style={
        'cursor': 'pointer', 'borderRadius': '10px', 'overflow': 'hidden', 'background': C['panel'],
        'border': f'1px solid {C["border"]}',
    })


def _linha_dia_view(linha: dict) -> html.Div:
    """1 linha da Day/Week View (Task #31, referência TradeZella — tabela
    "Open time/Ticker/Side/.../Net P&L" da imagem de referência). Clique
    abre o mesmo painel de detalhe do grid — ver `abrir_detalhe_dia`.
    2026-08-02: ganhou Direção (Side) e R-Múltiplo pra bater mais de perto
    com as colunas reais do TradeZella — "Instrument"/"Net ROI"/
    "Commissions"/"Replay" da referência ficam de fora de propósito, este
    projeto não rastreia contrato específico por trade, ROI%, comissão,
    nem tem replay de execução; inventar esses campos seria pior que
    omiti-los."""
    cor = C['green'] if linha['resultado'] > 0 else (C['red'] if linha['resultado'] < 0 else C['text2'])
    cor_direcao = C['blue'] if linha.get('direcao_label') == 'Compra' else C['gold']
    r_mult_str = f"{linha['r_multiplo']:+.2f}R" if linha.get('r_multiplo') is not None else '—'
    return html.Div([
        html.Span(linha.get('hora') or '—', style={'fontSize': '11px', 'color': C['text2'], 'width': '55px', 'flexShrink': 0}),
        html.Span(linha['ativo'], style={'fontSize': '12px', 'fontWeight': '700', 'width': '55px', 'flexShrink': 0}),
        html.Span(linha.get('direcao_label') or '—', style={
            'fontSize': '11px', 'fontWeight': '600', 'color': cor_direcao, 'width': '65px', 'flexShrink': 0}),
        html.Span(linha['estrategia'], style={'fontSize': '12px', 'color': C['text2'], 'flex': 1,
                                               'whiteSpace': 'nowrap', 'overflow': 'hidden', 'textOverflow': 'ellipsis'}),
        html.Span(r_mult_str, style={'fontSize': '12px', 'color': C['text2'], 'width': '75px', 'flexShrink': 0}),
        html.Div([
            html.Div(linha['resultado_principal'], style={'fontSize': '12px', 'fontWeight': '700', 'color': cor}),
        ] + ([html.Div(linha['resultado_secundario'], style={'fontSize': '10px', 'color': C['text2']})]
             if linha.get('resultado_secundario') else []),
            style={'width': '95px', 'textAlign': 'right', 'flexShrink': 0}),
    ], id={'type': 'diario-dia-linha', 'index': linha['id_trade']}, n_clicks=0, style={
        'display': 'flex', 'alignItems': 'center', 'gap': '10px', 'padding': '8px 10px', 'cursor': 'pointer',
        'borderBottom': f'1px solid {C["border"]}',
    })


def _cabecalho_tabela_dia() -> html.Div:
    """Cabeçalho da tabela de trades do dia — mesmas colunas de
    `_linha_dia_view`, larguras idênticas pra alinhar."""
    colunas = [('Horário', '55px'), ('Ativo', '55px'), ('Direção', '65px'), ('Estratégia', None),
               ('R-Múltiplo', '75px'), ('Resultado', '95px')]
    filhos = []
    for nome, largura in colunas:
        estilo = {'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                  'letterSpacing': '0.04em', 'flexShrink': 0}
        if largura:
            estilo['width'] = largura
        else:
            estilo['flex'] = 1
        if nome == 'Resultado':
            estilo['textAlign'] = 'right'
        filhos.append(html.Span(nome, style=estilo))
    return html.Div(filhos, style={'display': 'flex', 'alignItems': 'center', 'gap': '10px',
                                    'padding': '4px 10px', 'borderBottom': f'2px solid {C["border"]}'})


def _stats_dia(trades_do_dia: list) -> dict:
    """Linha de stats do dia (Task #31, referência TradeZella: "Total
    Trades"/"Gross P&L"/"Win Rate"/"Volume"/"Winners/Losers"/
    "Profit Factor"). Sem "Commissions"/"Net ROI%" — dado que este
    projeto não rastreia, nunca fabricado."""
    n = len(trades_do_dia)
    saldo = sum(t['resultado'] for t in trades_do_dia)
    vencedoras = [t for t in trades_do_dia if t['resultado'] > 0]
    perdedoras = [t for t in trades_do_dia if t['resultado'] < 0]
    n_win, n_loss = len(vencedoras), len(perdedoras)
    winrate = (n_win / n * 100) if n else 0.0
    soma_ganhos = sum(t['resultado'] for t in vencedoras)
    soma_perdas = sum(t['resultado'] for t in perdedoras)
    profit_factor = abs(soma_ganhos / soma_perdas) if soma_perdas else None
    volume = sum(t['lote'] for t in trades_do_dia if t.get('lote') is not None)
    return {
        'n': n, 'saldo': saldo, 'n_win': n_win, 'n_loss': n_loss,
        'winrate': winrate, 'profit_factor': profit_factor, 'volume': volume,
    }


def _linha_stats_dia(stats: dict) -> html.Div:
    def _stat(label, valor):
        return html.Div([
            html.Div(label, style={'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                                    'letterSpacing': '0.04em', 'marginBottom': '2px'}),
            html.Div(valor, style={'fontSize': '13px', 'fontWeight': '700'}),
        ])
    pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] is not None else '—'
    volume_str = f"{stats['volume']:g}" if stats['volume'] else '—'
    cor_saldo = C['green'] if stats['saldo'] > 0 else (C['red'] if stats['saldo'] < 0 else C['text2'])
    saldo_html = html.Div(f"R$ {stats['saldo']:+.2f}", style={'fontSize': '13px', 'fontWeight': '700', 'color': cor_saldo})
    return html.Div([
        _stat('Operações', str(stats['n'])),
        # "Gross P&L" da referência — já aparecia como número grande no
        # topo do Dia único, mas faltava na PRÓPRIA linha de stats (2026-08-02,
        # comparação pixel a pixel pedida pelo Jesué contra o print real).
        html.Div([
            html.Div('Saldo', style={'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                                      'letterSpacing': '0.04em', 'marginBottom': '2px'}),
            saldo_html,
        ]),
        _stat('Vencedoras/Perdedoras', f"{stats['n_win']}/{stats['n_loss']}"),
        _stat('Taxa de Acerto', f"{stats['winrate']:.0f}%"),
        _stat('Fator de Lucro', pf_str),
        _stat('Volume', volume_str),
    ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(110px, 1fr))', 'gap': '10px'})


def _hex_para_rgba(hex_cor: str, alpha: float) -> str:
    hex_cor = hex_cor.lstrip('#')
    r, g, b = int(hex_cor[0:2], 16), int(hex_cor[2:4], 16), int(hex_cor[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'


def _grafico_mini_equity_dia(trades_do_dia: list, tema: str) -> go.Figure:
    """Mini equity curve do dia (Task #31, referência TradeZella — área
    verde/vermelha conforme o saldo do dia termina positivo/negativo,
    mesmo padrão visual de `dashboard.py::_grafico_sparkline`). Ordena por
    horário; trades sem horário registrado (`hora` vem de
    `datetime_entrada`, nem sempre preenchido) entram por último."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    ordenados = sorted(trades_do_dia, key=lambda t: t.get('hora') or '99:99')
    acumulado, serie = 0.0, [0.0]
    for t in ordenados:
        acumulado += t['resultado']
        serie.append(acumulado)
    cor = p['green'] if serie[-1] >= 0 else p['red']
    fig = go.Figure(go.Scatter(
        x=list(range(len(serie))), y=serie, mode='lines',
        line=dict(color=cor, width=2, shape='spline', smoothing=0.3),
        fill='tozeroy', fillcolor=_hex_para_rgba(cor, 0.18),
    ))
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=0, r=0, t=4, b=4), height=90,
        xaxis=dict(visible=False, fixedrange=True), yaxis=dict(visible=False, fixedrange=True),
        showlegend=False,
    )
    return fig


def _grafico_evolucao_saldo_periodo(linhas: list, tema: str) -> go.Figure:
    """Evolução do saldo acumulado dentro do PERÍODO FILTRADO no Histórico
    (item 2F do upgrade de 2026-08-04, pedido explícito: "coloque um
    gráfico mostrando a evolução dentro período selecionado") — não a base
    inteira, só `linhas` já filtradas (`store-diario-linhas-filtradas`).
    Mesmo efeito visual verde/vermelho por sinal do gráfico "Evolução de
    Saldo" do Dashboard (`dashboard.py::_grafico_evolucao_saldo`), reusando
    o algoritmo de segmentação (`metricas.segmentos_cor_por_sinal`) pra não
    duplicar a interpolação do cruzamento de zero."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    fig = go.Figure()
    ordenados = sorted(linhas, key=lambda l: (l['data'], l.get('hora') or '99:99'))
    if ordenados:
        datas, saldos, acumulado = [], [], 0.0
        for l in ordenados:
            acumulado += l['resultado']
            datas.append(l['data'])
            saldos.append(acumulado)
        cor_por_sinal = {'pos': p['green'], 'neg': p['red']}
        for sinal, xs_seg, ys_seg in segmentos_cor_por_sinal(datas, saldos):
            cor = cor_por_sinal[sinal]
            extremo = max(ys_seg) if sinal == 'pos' else min(ys_seg)
            fig.add_trace(go.Scatter(
                x=xs_seg, y=ys_seg, mode='lines', line=dict(color=cor, width=2, shape='spline', smoothing=0.3),
                fill='tozeroy', fillcolor=_hex_para_rgba(cor, 0.15),
                fillgradient=dict(type='vertical', start=0, stop=extremo,
                                   colorscale=[[0, _hex_para_rgba(cor, 0.0)], [1, _hex_para_rgba(cor, 0.45)]]),
                showlegend=False, hoverinfo='x+y',
            ))
        fig.add_hline(y=0, line_width=1, line_color=p['border'])
    fig.update_layout(
        paper_bgcolor=p['panel'], plot_bgcolor=p['panel'],
        font=dict(family=FONTS['body'], color=p['text2'], size=10),
        margin=dict(l=45, r=10, t=10, b=30), height=180,
        xaxis=dict(showgrid=False, gridcolor=p['border']),
        yaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5, title='R$ acumulado', zeroline=False),
        hovermode='x unified',
    )
    return fig


def _bloco_evolucao_periodo(linhas: list, tema: str) -> dmc.Card:
    return dmc.Card([
        dmc.Text('Evolução de Saldo (período selecionado)', size='sm', fw=600, mb='sm'),
        dcc.Graph(figure=_grafico_evolucao_saldo_periodo(linhas, tema),
                  config={'displayModeBar': False, 'responsive': True}, style={'width': '100%'}),
    ], withBorder=True, shadow='sm', radius='lg', p='md', mb='md', className='tz-card', w='100%')


def _corpo_dia(trades_do_dia: list, tema: str) -> html.Div:
    """Miolo compartilhado por Dia único e por cada cartão do modo Semana:
    mini equity curve + linha de stats + tabela de trades."""
    stats = _stats_dia(trades_do_dia)
    ordenados = sorted(trades_do_dia, key=lambda t: t.get('hora') or '99:99')
    return html.Div([
        html.Div([
            html.Div(
                dcc.Graph(figure=_grafico_mini_equity_dia(trades_do_dia, tema),
                          config={'displayModeBar': False}, style={'height': '90px'}),
                style={'width': '160px', 'flexShrink': 0},
            ),
            html.Div(_linha_stats_dia(stats), style={'flex': 1}),
        ], style={'display': 'flex', 'gap': '20px', 'alignItems': 'center', 'padding': '4px 4px 14px',
                   'flexWrap': 'wrap'}),
        _cabecalho_tabela_dia(),
        html.Div([_linha_dia_view(t) for t in ordenados]),
    ])


def _dias_da_semana(dia_atual: str, dias_disponiveis: list) -> list:
    """Dias da semana civil (dom-sáb) que contém `dia_atual`, restrito aos
    dias que têm operação real (mesmo espírito de `_dias_disponiveis`:
    nunca listar um dia vazio — a referência TradeZella também só lista
    dias com atividade, pula fins de semana/dias sem trade). Ordem
    decrescente (mais recente primeiro), igual à referência (Thu → Wed →
    Tue)."""
    data_ref = datetime.strptime(dia_atual, '%Y-%m-%d').date()
    inicio_semana = data_ref - timedelta(days=(data_ref.weekday() + 1) % 7)  # domingo
    fim_semana = inicio_semana + timedelta(days=6)
    na_semana = [d for d in dias_disponiveis
                 if inicio_semana <= datetime.strptime(d, '%Y-%m-%d').date() <= fim_semana]
    return sorted(na_semana, reverse=True)


def _cartao_dia_semana(data_str: str, trades_do_dia: list, expandido: bool, tema: str) -> html.Div:
    """1 dia do modo Semana — cartão recolhível (chevron + data + saldo no
    cabeçalho; mini equity + stats + tabela no corpo, só quando expandido).
    Mesma disciplina de sempre: o cabeçalho é ESTÁTICO (id pattern-matching
    com `n_clicks`), nunca recriado pela própria callback de toggle."""
    saldo_dia = sum(t['resultado'] for t in trades_do_dia)
    cor_saldo = C['green'] if saldo_dia > 0 else (C['red'] if saldo_dia < 0 else C['text2'])
    icone_chevron = 'tabler:chevron-down' if expandido else 'tabler:chevron-right'
    data_fmt = datetime.strptime(data_str, '%Y-%m-%d').strftime('%a, %d/%m/%Y')
    cabecalho = html.Div([
        html.Div([
            DashIconify(icon=icone_chevron, width=16, color=C['text2']),
            html.Span(data_fmt, style={'fontSize': '13px', 'fontWeight': '700', 'marginLeft': '8px'}),
        ], style={'display': 'flex', 'alignItems': 'center'}),
        html.Span(f"R$ {saldo_dia:+.2f}", style={'fontSize': '13px', 'fontWeight': '700', 'color': cor_saldo}),
    ], id={'type': 'diario-semana-header', 'index': data_str}, n_clicks=0, style={
        'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
        'cursor': 'pointer', 'padding': '10px 4px',
    })
    corpo = _corpo_dia(trades_do_dia, tema) if expandido else None
    return html.Div([cabecalho, corpo], style={
        'background': C['panel'], 'border': f'1px solid {C["border"]}', 'borderRadius': '10px',
        'marginBottom': '10px', 'padding': '2px 12px',
    })


def _bloco_pendencias_historico(pendencias: pd.DataFrame) -> html.Div | None:
    """Lista pequena de execuções ainda sem trade classificado — fora do
    `dag.AgGrid` de propósito, ver docstring de `_construir_diario_historico`.
    Mesmas ações de sempre (Editar / ✓ finalizar rápido), só que num cartão
    por pendência em vez de linha de `html.Table` (não precisa mais alinhar
    coluna a coluna com o grid ao lado)."""
    if pendencias.empty:
        return None
    linhas = []
    for _, p in pendencias.iterrows():
        saldo_est = p.get('saldo_estimado')
        saldo_txt = f"R$ {saldo_est:+.2f} (estimado)" if pd.notna(saldo_est) else '—'
        linhas.append(html.Div([
            html.Span('PENDENTE', style={
                'color': C['bg'], 'background': C['yellow'], 'fontWeight': '700',
                'fontSize': '10px', 'padding': '2px 8px', 'borderRadius': '4px', 'marginRight': '10px',
            }),
            html.Span(str(p.get('data') or '—'), style={'fontSize': '12px', 'marginRight': '14px'}),
            html.Span(str(p.get('ativo') or '—'), style={'fontSize': '12px', 'marginRight': '14px'}),
            html.Span(saldo_txt, style={'fontSize': '12px', 'color': C['text2'], 'fontStyle': 'italic', 'flex': 1}),
            html.Button('Editar', id={'type': 'diario-btn-editar-pendencia', 'index': p['id_editar']},
                        n_clicks=0, style={
                            'background': C['gold'], 'color': C['bg'], 'border': 'none',
                            'borderRadius': '4px', 'padding': '2px 12px', 'cursor': 'pointer',
                            'fontSize': '11px', 'fontWeight': '700',
                        }),
            html.Button(_icone('tabler:check', tamanho=14), id={'type': 'diario-btn-finalizar-rapido', 'index': p['id_editar']},
                        n_clicks=0, title='Gravar no Diário com os dados padrão do MT5, sem editar',
                        style={
                            'background': C['green'], 'color': C['bg'], 'border': 'none',
                            'borderRadius': '4px', 'padding': '2px 10px', 'cursor': 'pointer',
                            'fontWeight': '700', 'marginLeft': '6px',
                            'display': 'inline-flex', 'alignItems': 'center', 'justifyContent': 'center',
                        }),
        ], style={
            'display': 'flex', 'alignItems': 'center', 'padding': '8px 12px', 'marginBottom': '6px',
            'background': 'rgba(200,169,110,0.08)', 'border': f'1px solid {C["border"]}', 'borderRadius': '6px',
        }))
    return html.Div(linhas, style={'marginBottom': '14px'})


def _drawer_editor(id_base: str, titulo: str, size: str = 'lg') -> dmc.Drawer:
    """Casca de `dmc.Drawer` reutilizável pros pop-ups de edição (Editar
    Trade/Editar Pendência/Novo Trade — 2026-07-31, mesmo padrão do
    `drawer-detalhe-trade`). `id_base` vira `drawer-{id_base}` (visibilidade)
    e `drawer-{id_base}-conteudo` (placeholder de conteúdo, preenchido por
    uma callback própria de cada pop-up).

    **Achado importante (2026-07-31)**: `.mantine-Drawer-inner`/`.mantine-
    Drawer-overlay` usam `position: fixed` (confirmado via
    `getComputedStyle` num teste isolado) — dentro do iframe do Diário,
    que se auto-ajusta pra caber TODO o conteúdo (`assets/diario_iframe_
    resize.js`), isso ancora na altura TOTAL do conteúdo, não na fatia
    visível, EXATAMENTE o bug já documentado e corrigido pro `_overlay()`
    antigo (ver docstring dele). Corrigido reaproveitando a MESMA solução:
    `assets/style.css` tem uma regra `.diario-embutido .mantine-Drawer-
    inner/-overlay { position: absolute; top: var(--sq-vp-topo)... }` que
    troca `fixed` por `absolute` ancorado nas custom properties que
    `assets/diario_overlay_viewport.js` já calcula e publica — nenhum JS
    novo precisou ser escrito. `transitionProps={'duration': 220}` anima a
    entrada; sem isso (`duration: 0`) o Drawer perderia a animação de
    deslizar (só necessário evitar `duration: 0` numa ferramenta de teste
    automatizado específica, não no navegador real — ver comentário mais
    longo na 1ª instância deste padrão, `drawer-detalhe-trade`)."""
    return dmc.Drawer(
        id=f'drawer-{id_base}',
        opened=False,
        position='right',
        size=size,
        offset=16,
        radius='md',
        shadow='xl',
        title=titulo,
        transitionProps={'duration': 220, 'transition': 'slide-left'},
        overlayProps={'opacity': 0.15, 'blur': 2},
        children=html.Div(id=f'drawer-{id_base}-conteudo'),
    )


# `_overlay()`/`_registrar_scroll_ate_overlay()` (o pop-up centralizado
# via CSS custom properties + `scrollIntoView`) foram REMOVIDAS em
# 2026-07-31 — todos os 4 pop-ups do Diário (print ampliado, Editar
# Pendência, Editar Trade, Novo Trade) migraram pra `dmc.Drawer`/
# `dmc.Modal` reais (`_drawer_editor`, `modal-imagem-ampliada`), sem
# chamador restante. O RACIOCÍNIO sobre `position:fixed` quebrar dentro do
# iframe auto-crescido continua vivo — ver o comentário longo em
# `assets/style.css` (bloco ".diario-overlay-viewport"/".mantine-Drawer-
# inner"), que documenta o problema original E a correção equivalente
# aplicada aos componentes novos.


def _campo_detalhe(label: str, valor) -> html.Div:
    # Ver diario_app_98.py::_campo_detalhe_98 pro mesmo raciocínio
    # (comparar com pd.isna, não com a string 'nan').
    vazio = valor is None or valor == '' or (isinstance(valor, float) and pd.isna(valor))
    return html.Div([
        html.Span(f"{label}: ", style={'color': C['text2'], 'fontSize': '11px'}),
        html.Span('—' if vazio else str(valor), style={'fontSize': '12px'}),
    ], style={'marginBottom': '5px'})


def _conteudo_detalhe_trade(id_trade: int) -> html.Div | None:
    """Conteúdo do detalhe de uma operação — abre em `dmc.Drawer` lateral
    (2026-07-31, correção do Jesué: "não tente enfiar os detalhes do trade
    no bloco abaixo... abra um pop-up ou uma barra lateral"). Clicável a
    partir dos 3 modos de visualização do Histórico (Grade/Galeria/Dia,
    Tasks #29/#30/#31) — mesmo painel fixo (`drawer-detalhe-trade`) pros
    3, só muda como a linha/card é identificado. Sem `_bloco_diario`
    (moldura de card com título próprio): dentro do Drawer isso duplicaria
    a moldura que o próprio Drawer já dá (título + padding + sombra)."""
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
                             ('emo_confianca', 'emo_humor', 'emo_energia', 'emo_atencao', 'emo_presenca',
                              'emo_equilibrio', 'emo_disciplina', 'sentimento_mercado'))

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
                _texto_com_icone('tabler:alert-triangle', f"{p['tipo'].replace('_', ' ').title()}: {p['detalhe']}", tamanho=12),
                style={'color': C[_COR_TIPO_PADRAO.get(p['tipo'], 'text2')], 'fontSize': '12px', 'marginBottom': '4px',
                       'display': 'flex', 'alignItems': 'center', 'gap': '4px'}))

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
        # Print vira o "hero" do Drawer, logo no topo (2026-08-01, pedido
        # do Jesué com referência visual: "coloque o print nessa posição
        # dentro dos detalhes do trade") — antes vivia numa seção "Print
        # da Operação" lá embaixo, depois de tudo mais. Largura cheia,
        # `object-fit: cover` (preenche a faixa sem distorcer, corta só o
        # excesso) — mesmo princípio do card da Galeria.
        imagem = html.Img(src=f"/diario-imagens/{trade['imagem_arquivo']}",
                           id={'type': 'diario-img-thumb-detalhe', 'index': id_trade}, n_clicks=0, style={
            'width': '100%', 'height': '220px', 'objectFit': 'cover', 'borderRadius': '10px',
            'border': f"1px solid {C['border']}", 'display': 'block', 'cursor': 'pointer', 'marginBottom': '16px',
        })

    saldo_num_raw, _compacto, principal, secundario = _resultado_trade(trade)
    saldo_num = saldo_num_raw if saldo_num_raw is not None else 0.0
    cor_saldo = C['green'] if saldo_num > 0 else (C['red'] if saldo_num < 0 else C['text2'])
    status_label = 'Win' if saldo_num > 0 else ('Loss' if saldo_num < 0 else 'BE')
    cor_status = C['green'] if status_label == 'Win' else (C['red'] if status_label == 'Loss' else C['text2'])

    cabecalho = html.Div([
        html.Div([
            html.Span(str(trade['ativo']), style={
                'fontSize': '20px', 'fontWeight': '700', 'fontFamily': FONTS['display'],
            }),
            html.Span(status_label, style={
                'fontSize': '11px', 'fontWeight': '700', 'color': C['bg'], 'background': cor_status,
                'borderRadius': '4px', 'padding': '2px 10px', 'marginLeft': '10px',
            }),
        ]),
        html.Div(str(trade['data_op']), style={'fontSize': '12px', 'color': C['text2'], 'marginTop': '2px'}),
        # Ativo USD (cTrader) mostra o valor NATIVO em dólar como principal
        # — o R$ convertido (usado nas somatórias/estatísticas) vira
        # informação extra, fonte menor, EMBAIXO (2026-08-06, correção do
        # Jesué — antes só mostrava R$ pra todo ativo, escondendo o dólar;
        # depois foi corrigido pra "US$ (R$)" na mesma linha, mas o Jesué
        # queria empilhado, não entre parênteses ao lado).
        html.Div([
            html.Div(principal, style={'fontSize': '28px', 'fontWeight': '700', 'color': cor_saldo}),
        ] + ([html.Div(secundario, style={
            'fontSize': '13px', 'fontWeight': '500', 'color': C['text2'], 'marginTop': '2px',
        })] if secundario else []), style={'marginTop': '8px'}),
        dmc.Button('Editar', id={'type': 'diario-btn-editar-trade', 'index': id_trade}, n_clicks=0,
                   buttonProps={'title': 'Editar classificação e anotações desta operação'},
                   leftSection=DashIconify(icon='tabler:pencil', width=14),
                   variant='light', color='yellow', size='xs', mt='10px'),
    ], style={
        'paddingBottom': '14px', 'marginBottom': '14px', 'borderBottom': f'1px solid {C["border"]}',
    })

    secoes = [
        _secao_detalhe_trade('Dados da Operação', html.Div([
            _campo_detalhe('Data', trade['data_op']),
            _campo_detalhe('Ativo', trade['ativo']),
            _campo_detalhe('Janela', _formatar_janela(trade.get('janela_pregao'))),
            _campo_detalhe('Tipo', trade.get('tipo_operacao')),
            _campo_detalhe('Estratégia', tags_str),
            _campo_detalhe('Sala/Análise', trade.get('sala_analise')),
            _campo_detalhe(f'Alvo ({_unidade_pontuacao(trade.get("ativo"))})', trade.get('alvo_pontos')),
            _campo_detalhe(f'Stop ({_unidade_pontuacao(trade.get("ativo"))})', trade.get('stop_pontos')),
            _campo_detalhe('Lote', trade.get('lote')),
            _campo_detalhe('Preço Entrada', trade.get('preco_entrada')),
            _campo_detalhe('Preço Saída', trade.get('preco_saida')),
            _campo_detalhe('Tipo Saída', trade.get('tipo_saida')),
            _campo_detalhe(f'Resultado ({_unidade_pontuacao(trade.get("ativo"))})', trade.get('resultado_pontos')),
            _campo_detalhe('Nível S/R referenciado', trade.get('nivel_sr_referencia')),
            _campo_detalhe('Tags Setup/Erro', tags_erro_str),
        ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(160px, 1fr))', 'gap': '10px 16px'})),
    ]

    if tem_dado_emocional:
        secoes.append(_secao_detalhe_trade('Estado Emocional', html.Div([
            _campo_detalhe('Confiança', _estrelas_texto(trade.get('emo_confianca'))),
            _campo_detalhe('Humor', _estrelas_texto(trade.get('emo_humor'))),
            _campo_detalhe('Energia', _estrelas_texto(trade.get('emo_energia'))),
            _campo_detalhe('Presença', _estrelas_texto(trade.get('emo_presenca'))),
            _campo_detalhe('Equilíbrio', _estrelas_texto(trade.get('emo_equilibrio'))),
            _campo_detalhe('Disciplina', _estrelas_texto(trade.get('emo_disciplina'))),
            # "Atenção" saiu do formulário (2026-08-03) — mostrado só quando
            # o trade é antigo o bastante pra ter esse campo preenchido.
            *([_campo_detalhe('Atenção', _estrelas_texto(trade.get('emo_atencao')))]
              if pd.notna(trade.get('emo_atencao')) else []),
            _campo_detalhe('Sentimento de Mercado', _texto_sentimento(trade.get('sentimento_mercado'))),
            _campo_detalhe('Print Emocional', tags_emocionais_str),
        ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(160px, 1fr))', 'gap': '10px 16px'})))

    if trade.get('resumo_operacional') or trade.get('obs'):
        secoes.append(_secao_detalhe_trade('Anotações', html.Div([
            html.Div('Resumo Operacional', style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '2px'}),
            html.Div(trade.get('resumo_operacional') or '—', style={'fontSize': '12px', 'whiteSpace': 'pre-wrap', 'marginBottom': '10px'}),
            html.Div('Obs (autocoaching)', style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '2px'}),
            html.Div(trade.get('obs') or '—', style={'fontSize': '12px', 'whiteSpace': 'pre-wrap'}),
        ])))

    if bloco_aderencia is not None or flags_padroes:
        secoes.append(_secao_detalhe_trade('Traçados & Padrões', html.Div([
            bloco_aderencia,
            html.Div(flags_padroes) if flags_padroes else None,
        ])))

    secoes.append(_secao_detalhe_trade('Contexto do Motor', html.Div(
        snapshot_resumo or 'Snapshot do motor: não capturado (trade antigo).',
        style={'fontSize': '12px', 'color': C['text2']},
    )))

    return html.Div([imagem, cabecalho, *secoes])


def _secao_detalhe_trade(titulo: str, conteudo) -> html.Div:
    """1 seção do Drawer de detalhe (2026-07-31, correção do Jesué:
    conteúdo estava "tudo em cima do outro, sem hierarquia" — cada seção
    agora tem rótulo próprio + separador, mesma convenção visual já usada
    pro rótulo "Estado Emocional" antes desta correção, só generalizada
    pras outras seções)."""
    return html.Div([
        html.Div(titulo, style={
            'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'textTransform': 'uppercase', 'letterSpacing': '0.06em',
            'marginBottom': '10px',
        }),
        conteudo,
    ], style={
        'paddingTop': '16px', 'marginTop': '16px', 'borderTop': f'1px solid {C["border"]}',
    })


# ---------------------------------------------------------------------------
# Header de stat cards estilo TradeZella (item F, fase 2,
# `spec_redesenho_tradezella.md`) — Net P&L / Trade Win% / Avg Win-Loss /
# Profit Factor / Current Streak, substituindo os grupos Quantidade/Taxas/
# Valores antigos (que continuam existindo, só demovidos pra um bloco
# "Detalhes" logo abaixo — nenhum dado é descartado, só reorganizado).
# ---------------------------------------------------------------------------



















# ---------------------------------------------------------------------------
# Análise de Estratégia (2026-07-30, "upgrade diario.docx") — ver
# dt.analise_estrategia pro dado. Bloco ALL-TIME (mesmo recorte de
# "Confiabilidade por Estratégia", não reage ao seletor de mês/ano do
# calendário) — junta Por Ativo/Janela/Sala/Tags (que antes viviam soltos
# em `_resto_periodo_estatisticas`, reagindo ao período) com "melhor
# estratégia por X" e o destaque de Vencedora/Perdedora.
# ---------------------------------------------------------------------------











# ---------------------------------------------------------------------------
# Anticovardia Score (item F, fase 3, `spec_redesenho_tradezella.md`) —
# renomeado do "Zella Score" do TradeZella. Radar hexagonal + barra
# horizontal de posição, mesmo padrão visual de `_radar_roda_vida`
# (Bem-Estar), reaproveitado aqui. Dado vem de
# `dt.calcular_anticovardia_score()` (Fase 1, já testado).
# ---------------------------------------------------------------------------

# Ordem de exibição no radar — bate com a referência visual do TradeZella
# (`image1.png` do docx), não com a ordem de inserção do dict Python.


























# Stats configuráveis por dia no calendário (item F, fase 4,
# `spec_redesenho_tradezella.md`) — "Ticks" do TradeZella vira "Pontos"
# aqui (não operamos Forex). Conferido em detalhe contra `image2.png` do
# docx (anotada, mostra a lista real "Display stats" do produto): Daily
# P/L É UM TOGGLE de verdade lá (não é fixo como eu tinha assumido antes),
# então entra na lista igual aos outros — só por padrão vem marcado,
# porque esconder o R$ da célula sem querer seria uma surpresa ruim.
# Defaults batem com a referência: R Multiple/Daily P/L/Number of trades/
# Day Winrate ligados, Ticks (Pontos) desligado.










# `cellStyle` como função JS (dash-ag-grid aceita `{"function": "<corpo JS>"}`
# em vez de um dict fixo — precisa ser JS porque a cor depende do VALOR da
# célula, calculado no navegador a cada render/sort/filter, não em Python).






























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


def _fmt_risco_resumo(gerenciamento: dict | None) -> str:
    if not gerenciamento:
        return '—'
    alvo, stop = gerenciamento.get('alvo_pontos'), gerenciamento.get('stop_pontos')
    if pd.isna(alvo) or pd.isna(stop):
        return '—'
    unidade = _unidade_pontuacao(gerenciamento.get('ativo_mercado'))
    return f"{gerenciamento['nome']} ({float(alvo):.0f}/{float(stop):.0f} {unidade})"


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


def _tabela_gerenciamento_risco(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum gerenciamento de risco cadastrado.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    # Cabeçalho SEM unidade fixa (2026-08-05) — a tabela lista gerenciamentos
    # de VÁRIOS ativos ao mesmo tempo (WIN/WDO em pontos, XAUUSD/EURUSD em
    # pips), então a unidade certa só pode aparecer POR LINHA, não no
    # cabeçalho — ver `_unidade_pontuacao(linha['ativo_mercado'])` abaixo.
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', 'Ativo', 'Alvo', 'Stop', 'Breakeven', 'Padrão', '']])
    linhas = []
    for _, linha in df.iterrows():
        eh_padrao = bool(linha['padrao'])
        unidade = _unidade_pontuacao(linha['ativo_mercado'])
        linhas.append(html.Tr([
            _td_diario(linha['nome']),
            _td_diario(linha['ativo_mercado']),
            _td_diario(f"{linha['alvo_pontos']:.0f} {unidade}"),
            _td_diario(f"{linha['stop_pontos']:.0f} {unidade}"),
            _td_diario(f"{linha['breakeven_pontos']:.0f} {unidade}" if pd.notna(linha['breakeven_pontos']) else '—'),
            _td_diario(_texto_com_icone('tabler:star-filled', 'Padrão', cor=C['gold']) if eh_padrao else html.Button(
                'Definir padrão', id={'type': 'diario-gerenciamento-risco-padrao', 'index': int(linha['id'])},
                n_clicks=0, style=_estilo_btn_pequeno())),
            _td_diario(_botao_desativar('diario-gerenciamento-risco-desativar', linha['id'])),
        ]))
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


def _tabela_sessoes(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma sessão cadastrada.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', 'Horário', 'Ativos', '']])
    linhas = [html.Tr([
        _td_diario(linha['nome']),
        _td_diario(f"{linha['hora_inicio']}-{linha['hora_fim']}"),
        _td_diario(', '.join(linha['ativos']) if len(linha['ativos']) else 'Todos'),
        _td_diario(_botao_desativar('diario-sessao-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _tabela_janelas(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhuma janela cadastrada.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', 'Horário', 'Ativos', '']])
    linhas = [html.Tr([
        _td_diario(linha['nome']),
        _td_diario(f"{linha['hora_inicio']}-{linha['hora_fim']}"),
        _td_diario(', '.join(linha['ativos']) if len(linha['ativos']) else 'Todos'),
        _td_diario(_botao_desativar('diario-janela-desativar', linha['id'])),
    ]) for _, linha in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _td_ou_padrao(valor, default) -> str:
    v = valor if valor is not None and not (isinstance(valor, float) and pd.isna(valor)) else default
    return str(int(v))


def _tabela_checklists_pretrade(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div('Nenhum checklist pré-trade cadastrado.', style={'color': C['text2'], 'fontSize': '12px', 'padding': '8px'})
    cabecalho = html.Tr([html.Th(t, style={'textAlign': 'left', 'padding': '6px 10px', 'fontSize': '11px', 'color': C['text2']})
                          for t in ['Nome', 'Itens', 'Aviso', 'Crítico', 'Stops', 'Por Ativo', 'Metas R$', 'Padrão', '']])
    linhas = []
    for _, linha in df.iterrows():
        eh_padrao = bool(linha['padrao'])
        limite_por_ativo = linha.get('limite_trades_por_ativo')
        por_ativo_texto = (', '.join(f"{k}:{v}" for k, v in limite_por_ativo.items())
                            if isinstance(limite_por_ativo, dict) and limite_por_ativo else '—')
        meta_prejuizo = linha.get('meta_prejuizo_dia')
        meta_ganho = linha.get('meta_ganho_dia')
        partes_meta = []
        if meta_prejuizo is not None and not (isinstance(meta_prejuizo, float) and pd.isna(meta_prejuizo)):
            partes_meta.append(f"Prej. R$ {meta_prejuizo:.0f}")
        if meta_ganho is not None and not (isinstance(meta_ganho, float) and pd.isna(meta_ganho)):
            partes_meta.append(f"Ganho R$ {meta_ganho:.0f}")
        metas_texto = ' / '.join(partes_meta) if partes_meta else '—'
        linhas.append(html.Tr([
            _td_diario(linha['nome']),
            _td_diario(f"{len(_lista_segura(linha['itens']))} item(ns)"),
            _td_diario(_td_ou_padrao(linha.get('limite_trades_dia'), cfg.DIARIO_LIMITE_TRADES_DIA)),
            _td_diario(_td_ou_padrao(linha.get('limite_critico_trades_dia'), cfg.DIARIO_TRADES_DIA_CRITICO)),
            _td_diario(_td_ou_padrao(linha.get('limite_stops_dia'), cfg.DIARIO_LIMITE_STOPS_DIA)),
            _td_diario(por_ativo_texto),
            _td_diario(metas_texto),
            _td_diario(_texto_com_icone('tabler:star-filled', 'Padrão', cor=C['gold']) if eh_padrao else html.Button(
                'Definir padrão', id={'type': 'diario-checklist-pretrade-padrao', 'index': int(linha['id'])},
                n_clicks=0, style=_estilo_btn_pequeno())),
            _td_diario(_botao_desativar('diario-checklist-pretrade-desativar', linha['id'])),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)], style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario_estrategias(tema: str = 'light') -> html.Div:
    campo = _campo_diario
    estilo_input = _estilo_input_diario
    scores = dt.calcular_score_confiabilidade()
    saldos_por_estrategia = dt.calcular_estatisticas()['por_estrategia']
    return html.Div([
        # Mini dashboard (2026-08-06, pedido do Jesué "mini dashboard dentro
        # de Estratégias"): KPIs + gráfico de saldo por Categoria, ALL-TIME,
        # mesmo recorte que a grade abaixo usa.
        _mini_dashboard_estrategias(scores, saldos_por_estrategia, tema),
        # Confiabilidade por Estratégia (2026-08-05, item 18 do upgrade
        # "upgrade geral parte 1.docx": "Traga o bloco Confiabilidade por
        # Estratégia para o módulo Estratégias") — migrado de
        # `dashboard.py`, que agora só mostra um ranking Top3/Bottom3 +
        # botão "ver mais" que traz o usuário pra cá (ver `_ranking_
        # confiabilidade_compacto`/`navegar_para_estrategias` lá).
        # ALL-TIME (sem filtro de período), mesmo recorte que o resumo do
        # Dashboard usa, pra não mostrar números diferentes nos dois
        # lugares.
        _bloco_diario('Confiabilidade por Estratégia',
                      _tabela_score_confiabilidade(scores, saldos_por_estrategia)),
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

        # Gerenciamento de Risco (Playbook, 2026-08-02; corrigido 2026-08-03)
        # — cadastro SEPARADO e reutilizável (substitui os antigos R:R
        # Presets), definido PRIMEIRO por ATIVO (correção do Jesué: WIN/WDO
        # têm parâmetros de pontuação/volatilidade diferentes — NÃO é mais
        # vinculado à Estratégia). Ao selecionar o Ativo no trade, o
        # gerenciamento `padrao` daquele ativo autofilla Alvo/Stop; um
        # seletor no trade permite trocar (ver `aplicar_risco_ativo`).
        _bloco_diario('Novo Gerenciamento de Risco', html.Div([
            campo('Nome', dcc.Input(id='diario-novo-gerenciamento-risco-nome', type='text', style=estilo_input({'width': '220px'}))),
            campo('Ativo', dcc.Dropdown(
                id='diario-novo-gerenciamento-risco-ativo',
                options=_opcoes_diario(cfg.ATIVOS_DIARIO), clearable=False,
                className='classico-dropdown', style={'width': '160px'})),
            # Rótulos de Alvo/Stop/Breakeven com id próprio (em vez de
            # `campo()`, que não dá id ao texto do label) — precisam trocar
            # "pts"/"pips" conforme o Ativo escolhido acima (2026-08-05,
            # mesmo pedido/raciocínio de `rotular_pontuacao_por_ativo`).
            html.Div([
                html.Div([
                    html.Div('Meta Ganho — Alvo (pts)', id='label-risco-alvo', style={
                        'fontSize': '10px', 'color': C['text2'], 'marginBottom': '4px', 'fontFamily': FONTS['display']}),
                    dcc.Input(id='diario-novo-gerenciamento-risco-alvo', type='text',
                              inputMode='decimal', style=estilo_input({'width': '140px'})),
                ], style={'marginBottom': '10px'}),
                html.Div([
                    html.Div('Meta Perda — Stop (pts)', id='label-risco-stop', style={
                        'fontSize': '10px', 'color': C['text2'], 'marginBottom': '4px', 'fontFamily': FONTS['display']}),
                    dcc.Input(id='diario-novo-gerenciamento-risco-stop', type='text',
                              inputMode='decimal', style=estilo_input({'width': '140px'})),
                ], style={'marginBottom': '10px'}),
                html.Div([
                    html.Div('Breakeven (pts, opcional)', id='label-risco-be', style={
                        'fontSize': '10px', 'color': C['text2'], 'marginBottom': '4px', 'fontFamily': FONTS['display']}),
                    dcc.Input(id='diario-novo-gerenciamento-risco-be', type='text',
                              inputMode='decimal', style=estilo_input({'width': '150px'})),
                ], style={'marginBottom': '10px'}),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
            campo('Regra de Saída', dcc.Textarea(id='diario-novo-gerenciamento-risco-saida', rows=2,
                                                  style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            campo('Regra de Conduta Durante a Operação', dcc.Textarea(id='diario-novo-gerenciamento-risco-conduta', rows=2,
                                                                       style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            campo('Justificativa (por que este gerenciamento?)',
                  dcc.Textarea(id='diario-novo-gerenciamento-risco-justificativa', rows=3,
                               style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            dcc.Checklist(id='diario-novo-gerenciamento-risco-padrao',
                          options=[{'label': ' Definir como padrão deste ativo', 'value': 'padrao'}],
                          value=[], style={'fontSize': '12px', 'color': C['text2'], 'marginBottom': '8px'}),
            html.Button('Adicionar Gerenciamento', id='btn-diario-add-gerenciamento-risco', n_clicks=0,
                        style=_estilo_btn_primario()),
            html.Span(id='diario-gerenciamento-risco-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),
        _bloco_diario('Gerenciamentos de Risco Cadastrados',
                      html.Div(_tabela_gerenciamento_risco(dt.listar_gerenciamentos_risco()),
                               id='diario-tabela-gerenciamento-risco')),

        _bloco_diario('Novo Checklist Pré-Trade', html.Div([
            campo('Nome', dcc.Input(id='diario-novo-checklist-pretrade-nome', type='text', style=estilo_input({'width': '220px'}))),
            campo('Itens (1 por linha)', dcc.Textarea(id='diario-novo-checklist-pretrade-itens', rows=4,
                                                        style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            # Plano do dia (2026-08-03, pedido do Jesué: "esse plano deve
            # ser editável no checklist pré mercado e não uma constante
            # definida pelo sistema") — antes era cfg.DIARIO_LIMITE_TRADES_DIA
            # /DIARIO_TRADES_DIA_CRITICO/DIARIO_LIMITE_STOPS_DIA fixos; agora
            # cada checklist cadastrado tem o seu próprio plano (ver
            # `status_do_dia` em diario_trade.py).
            html.Div([
                campo('Limite de trades/dia (aviso)', dcc.Input(
                    id='diario-novo-checklist-pretrade-limite', type='number', min=1,
                    value=cfg.DIARIO_LIMITE_TRADES_DIA, style=estilo_input({'width': '140px'}))),
                campo('Limite crítico de trades/dia', dcc.Input(
                    id='diario-novo-checklist-pretrade-limite-critico', type='number', min=1,
                    value=cfg.DIARIO_TRADES_DIA_CRITICO, style=estilo_input({'width': '140px'}))),
                campo('Limite de stops/dia', dcc.Input(
                    id='diario-novo-checklist-pretrade-limite-stops', type='number', min=1,
                    value=cfg.DIARIO_LIMITE_STOPS_DIA, style=estilo_input({'width': '140px'}))),
            ], style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap'}),
            # Limite de trades/dia POR ATIVO (2026-08-06, pedido do Jesué —
            # ver [[project_reorg_cadastro_checklist_pretrade]]): cada ativo
            # cadastrado tem seu próprio limite; a SOMA deles substitui o
            # "Limite de trades/dia (aviso)" acima quando pelo menos 1
            # campo aqui for preenchido (ver `criar_checklist_pretrade`,
            # que faz a soma no momento do cadastro). Em branco pra todos =
            # comportamento anterior (só o campo flat acima vale).
            html.Div('Limite de trades/dia por Ativo (opcional — a soma substitui o limite acima)',
                     style={'fontSize': '11px', 'color': C['text2'], 'marginTop': '10px', 'marginBottom': '4px'}),
            html.Div([
                campo(ativo, dcc.Input(
                    id={'type': 'diario-novo-checklist-pretrade-limite-ativo', 'index': ativo},
                    type='number', min=0, placeholder='—', style=estilo_input({'width': '90px'})))
                for ativo in cfg.ATIVOS_DIARIO
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap', 'marginBottom': '10px'}),
            # Metas em R$ do dia (2026-08-06, mesmo pedido): prejuízo tem
            # peso MÁXIMO de alerta no guarda-corpo (`status_do_dia`), ganho
            # é conquista, não alerta — ver `_bloco_guarda_corpo_dia`.
            html.Div([
                campo('Meta de Prejuízo do Dia (R$, alerta máximo)', dcc.Input(
                    id='diario-novo-checklist-pretrade-meta-prejuizo', type='text', inputMode='decimal',
                    placeholder='ex: 200', style=estilo_input({'width': '160px'}))),
                campo('Meta de Ganho do Dia (R$, conquista)', dcc.Input(
                    id='diario-novo-checklist-pretrade-meta-ganho', type='text', inputMode='decimal',
                    placeholder='ex: 300', style=estilo_input({'width': '160px'}))),
            ], style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap', 'marginBottom': '8px'}),
            dcc.Checklist(id='diario-novo-checklist-pretrade-padrao', options=[{'label': ' Definir como padrão', 'value': 'padrao'}],
                          value=[], style={'fontSize': '12px', 'color': C['text2'], 'marginBottom': '8px'}),
            html.Button('Adicionar Checklist', id='btn-diario-add-checklist-pretrade', n_clicks=0, style=_estilo_btn_primario()),
            html.Span(id='diario-checklist-pretrade-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),
        _bloco_diario('Checklists Pré-Trade Cadastrados', html.Div(_tabela_checklists_pretrade(dt.listar_checklists_pretrade()),
                                                                     id='diario-tabela-checklist-pretrade')),

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

        # Sessão (Londres, NY, Tóquio etc) / Janela (recorte tático de
        # horário — substitui `cfg.JANELAS_PREGAO` fixo), 2026-08-04. Vale
        # pra WIN/WDO/BIT também, não só forex — por isso a lista de ativos
        # aqui é `cfg.ATIVOS + cfg.ATIVOS_CTRADER` (universo do motor de
        # features), não `ATIVOS_DIARIO` (universo do Diário). Detecção de
        # qual Sessão/Janela um candle/trade cai é SEMPRE automática por
        # horário (`features.calcular_temporal`) — este cadastro só define
        # os intervalos, nunca é selecionado manualmente por trade.
        _bloco_diario('Nova Sessão', html.Div([
            html.Div([
                campo('Nome', dcc.Input(id='diario-nova-sessao-nome', type='text', style=estilo_input({'width': '200px'}))),
                campo('Início', dcc.Input(id='diario-nova-sessao-inicio', type='text', placeholder='HH:MM',
                                           style=estilo_input({'width': '90px'}))),
                campo('Fim', dcc.Input(id='diario-nova-sessao-fim', type='text', placeholder='HH:MM',
                                        style=estilo_input({'width': '90px'}))),
                campo('Ativos (vazio = todos)', dcc.Dropdown(
                    id='diario-nova-sessao-ativos', multi=True,
                    options=_opcoes_diario(cfg.ATIVOS + cfg.ATIVOS_CTRADER),
                    className='classico-dropdown', style={'width': '220px'})),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap', 'alignItems': 'end'}),
            html.Button('Adicionar Sessão', id='btn-diario-add-sessao', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '8px'}),
            html.Span(id='diario-sessao-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),
        _bloco_diario('Sessões Cadastradas', html.Div(_tabela_sessoes(dt.listar_sessoes()),
                                                        id='diario-tabela-sessoes')),

        _bloco_diario('Nova Janela', html.Div([
            html.Div([
                campo('Nome', dcc.Input(id='diario-nova-janela-nome', type='text', style=estilo_input({'width': '200px'}))),
                campo('Início', dcc.Input(id='diario-nova-janela-inicio', type='text', placeholder='HH:MM',
                                           style=estilo_input({'width': '90px'}))),
                campo('Fim', dcc.Input(id='diario-nova-janela-fim', type='text', placeholder='HH:MM',
                                        style=estilo_input({'width': '90px'}))),
                campo('Ativos (vazio = todos)', dcc.Dropdown(
                    id='diario-nova-janela-ativos', multi=True,
                    options=_opcoes_diario(cfg.ATIVOS + cfg.ATIVOS_CTRADER),
                    className='classico-dropdown', style={'width': '220px'})),
            ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap', 'alignItems': 'end'}),
            html.Button('Adicionar Janela', id='btn-diario-add-janela', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '8px'}),
            html.Span(id='diario-janela-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),
        _bloco_diario('Janelas Cadastradas', html.Div(_tabela_janelas(dt.listar_janelas(somente_ativas=True)),
                                                        id='diario-tabela-janelas')),
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
                          for t in ['Data', 'Sono', 'Café', 'Água', 'Exercício', 'Alimentação', 'Duração Sono', 'Nível Hawkins']])
    linhas = []
    for _, linha in df.head(14).iterrows():
        checkin = linha.to_dict()
        resultado = dt.calcular_nivel_hawkins(checkin, dt.calcular_medias_emocionais_dia(checkin['data']))
        duracao = checkin.get('sono_duracao_horas')
        linhas.append(html.Tr([
            _td_diario(str(checkin['data'])),
            _td_diario(_estrelas_texto(checkin.get('sono_qualidade'))),
            _td_diario(_estrelas_texto(checkin.get('cafe_qualidade'))),
            _td_diario(_estrelas_texto(checkin.get('agua_qualidade'))),
            _td_diario(_estrelas_texto(checkin.get('exercicio_qualidade'))),
            _td_diario(_estrelas_texto(checkin.get('alimentacao_qualidade'))),
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


_ROTULO_EIXO_DISCIPLINA = {
    'checklist_pretrade': 'Checklist Pré-Mercado',
    'checklist_estrategia': 'Checklist da Estratégia',
    'risco': 'Gerenciamento de Risco',
    'coaching_checkin': 'Check-in de Coaching',
}


def _conteudo_discipline_score() -> html.Div:
    """Pontos de Disciplina (Playbook, 2026-08-02) — complemento do
    Anticovardia Score (dashboard.py, mesma métrica, cálculo idêntico via
    `dt.calcular_discipline_score`, sem import cruzado entre os 2
    processos — mesmo padrão de `_bloco_anticovardia_score` não ser
    compartilhado hoje)."""
    resultado = dt.calcular_discipline_score()
    if resultado['score'] is None:
        return html.Div('Sem trades reais suficientes pra calcular os Pontos de Disciplina ainda.',
                         style={'fontSize': '12px', 'color': C['text2']})
    barras = []
    for chave, rotulo in _ROTULO_EIXO_DISCIPLINA.items():
        valor = resultado['eixos'].get(chave)
        barras.append(dmc.Stack([
            dmc.Group([
                dmc.Text(rotulo, size='xs', c=C['text2']),
                dmc.Text(f"{valor:.0f}" if valor is not None else '—', size='xs', fw=700),
            ], justify='space-between'),
            dmc.Progress(value=valor or 0, color='yellow', size='sm', radius='xl'),
        ], gap=4, mb='xs'))
    return html.Div([
        html.Div(barras),
        dmc.Text(f"{resultado['score']:.1f}", size='sm', fw=700, mt='sm'),
    ])


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
                campo('Qualidade do Sono', _icone_rating('diario-checkin-sono', 'sono', _num_estrela_ou_padrao(_valor('sono_qualidade')))),
                campo('Qualidade do Café', _icone_rating('diario-checkin-cafe', 'cafe', _num_estrela_ou_padrao(_valor('cafe_qualidade')))),
                campo('Água', _icone_rating('diario-checkin-agua', 'agua', _num_estrela_ou_padrao(_valor('agua_qualidade')))),
                campo('Exercício Físico', _icone_rating('diario-checkin-exercicio', 'exercicio', _num_estrela_ou_padrao(_valor('exercicio_qualidade')))),
                campo('Alimentação', _icone_rating('diario-checkin-alimentacao', 'alimentacao', _num_estrela_ou_padrao(_valor('alimentacao_qualidade')))),
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
                               spellCheck=False,
                               style=estilo_input({'resize': 'none', 'height': 'auto'}))),
            html.Button('Salvar Check-in do Dia', id='btn-diario-salvar-checkin', n_clicks=0,
                        style={**_estilo_btn_primario(), 'marginTop': '8px'}),
            html.Span(id='diario-checkin-status', style={'marginLeft': '10px', 'fontSize': '12px', 'color': C['text2']}),
        ])),

        _bloco_diario('Escala Hawkins (proxy, não calibrado)',
                       html.Div(_bloco_gauge_hawkins(date.fromisoformat(hoje)), id='diario-gauge-hawkins')),

        _bloco_diario('Pontos de Disciplina', _conteudo_discipline_score()),

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
    'diario-emo-presenca', 'diario-emo-equilibrio', 'diario-emo-disciplina',
    # Editar Operação (2026-08-06) — `_bloco_emocional` foi reaproveitado
    # aqui com ids próprios (`diario-edit-emo-*`), mas o registro do clique
    # é um passo SEPARADO por campo singleton (não acontece dentro de
    # `_estrela_rating`/`_bloco_emocional`) — sem isto as estrelas
    # renderizam mas clicar nelas não faz nada (achado real do Jesué:
    # "o clique na estrelinha não funciona na edição do trade importado").
    'diario-edit-emo-confianca', 'diario-edit-emo-humor', 'diario-edit-emo-energia',
    'diario-edit-emo-presenca', 'diario-edit-emo-equilibrio', 'diario-edit-emo-disciplina',
):
    _registrar_clique_estrela(app, _campo_estrela)
for _tipo_estrela in (
    'ea-emo-confianca', 'ea-emo-humor', 'ea-emo-energia', 'ea-emo-presenca',
    'ea-emo-equilibrio', 'ea-emo-disciplina',
):
    _registrar_clique_estrela_match(app, _tipo_estrela)

# Sono/Café/Água/Exercício Físico/Alimentação (2026-08-03) — mesmo padrão
# dos ícones custom, só singleton (Check-in do Dia não é por-posição).
_registrar_clique_icone(app, 'diario-checkin-sono', 'sono')
_registrar_clique_icone(app, 'diario-checkin-cafe', 'cafe')
_registrar_clique_icone(app, 'diario-checkin-agua', 'agua')
_registrar_clique_icone(app, 'diario-checkin-exercicio', 'exercicio')
_registrar_clique_icone(app, 'diario-checkin-alimentacao', 'alimentacao')

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
    # 2026-08-06 — Editar Operação precisa distinguir "nenhuma mudança no
    # print" (não mexe no que já tava salvo) de "usuário clicou no X pra
    # remover o print existente" (grava None por cima). Resetada toda vez
    # que o Drawer abre pra um trade novo (ver `renderizar_drawer_editar_
    # trade`) e sempre que um print NOVO é colado/subido (a intenção de
    # remover não sobrevive a uma substituição).
    dcc.Store(id='store-diario-edit-imagem-removida', data=False),
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
    # Fileira de botões de sub-aba REMOVIDA (rollout TradeZella, 2026-07-31)
    # — a navegação entre estas 6 seções agora vem do menu lateral do pai
    # (`dashboard.py`), via postMessage (ver `assets/secao_diario.js`),
    # não de botões próprios deste processo.

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
    # Print ampliado — `dmc.Modal` real (2026-07-31, varredura completa
    # pedida pelo Jesué depois dos Drawers: "visualização de print não
    # está no ux novo"). `fullScreen=True` porque isto é um visualizador
    # de imagem com zoom/pan (não um formulário) — faz mais sentido ocupar
    # a tela toda que um painel lateral de largura fixa. `withCloseButton
    # =False` porque o cabeçalho já tem seu próprio "✕ Fechar" (mantido
    # explícito de propósito, ao lado dos controles de zoom e navegação
    # entre imagens — um X isolado no canto do Mantine ficaria longe
    # demais desses outros controles). Mesma ressalva de `_drawer_editor`
    # sobre `position:fixed` dentro do iframe — CSS corrigido em
    # `assets/style.css` cobre `.mantine-Modal-*` também.
    #
    # `zIndex=1000` (2026-07-31, achado pelo Jesué: "o print deve abrir em
    # primeiro plano, e não atrás da janela") — a imagem é aberta a partir
    # de dentro do `drawer-detalhe-trade` (clique no print do detalhe),
    # que continua aberto por baixo. Todo `dmc.Drawer` usa z-index 200 por
    # padrão (mesmo valor pra todos, sem hierarquia entre eles) — sem um
    # z-index MAIOR aqui, o Modal (aberto depois, mas com z-index igual)
    # podia renderizar atrás do Drawer ainda aberto, dependendo da ordem
    # de montagem no DOM. Fixar acima de 200 garante que o visualizador de
    # imagem SEMPRE fica por cima de qualquer Drawer, não importa qual
    # esteja aberto.
    dmc.Modal(
        id='modal-imagem-ampliada',
        opened=False,
        fullScreen=True,
        withCloseButton=False,
        radius=0,
        zIndex=1000,
        transitionProps={'duration': 220, 'transition': 'fade'},
        overlayProps={'opacity': 0.15, 'blur': 2},
        children=html.Div(id='modal-imagem-ampliada-conteudo', style={'height': '100%'}),
        styles={'body': {'height': '100%', 'padding': '16px'}, 'content': {'height': '100%'}},
    ),
    # Editar Pendência / Editar Trade / Novo Trade — `dmc.Drawer` real
    # (2026-07-31, varredura pedida pelo Jesué depois do Drawer de detalhe
    # do trade: "veja como essa tela de editar está horrível... faça uma
    # varredura em todas as janelas"). MESMA casca em todos os 3
    # (`_drawer_editor`, offset/radius/shadow/overlay leve/animação —
    # idêntico ao `drawer-detalhe-trade`), conteúdo interno (`_bloco_
    # editar_trade`/`_bloco_posicao_completo`/`_construir_diario_novo_
    # trade`) intocado — só a moldura ao redor mudou.
    #
    # O print ampliado (`modal-imagem-ampliada`, acima) TAMBÉM entrou nesta
    # rodada (2ª correção do Jesué: "visualização de print não está no ux
    # novo") — virou `dmc.Modal` em tela cheia, preservando o zoom/pan por
    # JS próprio (`assets/diario_zoom.js`, acoplado só por classe CSS/
    # atributo `data-zoom-acao`, nunca pela estrutura do overlay em si —
    # seguro trocar a moldura ao redor).
    _drawer_editor('editar-pendencia', 'Editar Pendência'),
    _drawer_editor('editar-trade', 'Editar Operação'),
    _drawer_editor('novo-trade', '+ Novo Trade', size='xl'),
    dcc.Store(id='store-novo-trade-aberto', data=False),
    # Importar Extrato (2026-08-05) — mesma casca de drawer, conteúdo
    # próprio (`_construir_diario_importar_extrato`). `store-importar-
    # extrato-linhas` guarda o preview já parseado (lista de dicts, editável
    # na tabela) entre o upload e o clique em "Importar".
    _drawer_editor('importar-extrato', 'Importar Extrato (cTrader)', size='1100px'),
    dcc.Store(id='store-importar-extrato-aberto', data=False),
    dcc.Store(id='store-importar-extrato-linhas', data=[]),
    # Playbook (2026-08-02) — bridge estável pro Checklist da Estratégia
    # dentro de Novo Trade: `diario-checklist-estrategia-itens` só existe
    # quando 1 estratégia é resolvida (é recriado a cada troca de
    # estratégia), então `salvar_novo_trade` NÃO pode ler seu `.value`
    # direto como `State` — se o usuário não selecionar estratégia
    # nenhuma, o componente nunca existe e a callback inteira seria
    # rejeitada em silêncio (a armadilha de ReferenceError já documentada
    # neste arquivo). Este Store, sempre montado, é o que `salvar_novo_
    # trade` de fato lê.
    dcc.Store(id='store-diario-checklist-estrategia-marcados', data=[]),

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


# `selecionar_sub_aba_diario`/`atualizar_estilo_sub_aba_diario` REMOVIDAS
# (rollout TradeZella, 2026-07-31) — sem botões próprios (ver acima),
# `store-diario-sub-aba` agora é escrito só por `assets/secao_diario.js`
# (postMessage do menu lateral do pai).

_DIARIO_SUBABA_CONSTRUTORES = {
    'posicao': _construir_diario_em_andamento,
    'historico': _construir_diario_historico,
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
    construtor = _DIARIO_SUBABA_CONSTRUTORES.get(sub_aba_ativa, _construir_diario_historico)
    return construtor()


@app.callback(
    Output('diario-estrategias-conteudo', 'children'),
    Input('store-diario-sub-aba', 'data'),
    State('store-tema', 'data'),
)
def atualizar_estrategias(sub_aba_ativa, tema_atual):
    if sub_aba_ativa != 'estrategias':
        return no_update
    return _construir_diario_estrategias(tema_atual or 'light')


@app.callback(
    Output('diario-detalhe-estrategia-selecionada', 'children'),
    Input('grid-confiabilidade-estrategia', 'cellClicked'),
    State('grid-confiabilidade-estrategia', 'rowData'),
    prevent_initial_call=True,
)
def atualizar_detalhe_estrategia_grid(cell_clicked, row_data):
    """Clique numa linha do AG Grid "Confiabilidade por Estratégia" —
    migrado de `dashboard.py` (2026-08-05, item 18 do upgrade). Usa
    `cell_clicked['rowIndex']` pra indexar direto no `State` de `rowData`,
    NÃO `cell_clicked['data']` — esta versão do dash-ag-grid nunca inclui
    `data` no payload de `cellClicked` (mesma causa raiz documentada no
    bug irmão `atualizar_detalhe_historico_grid`, mais acima neste mesmo
    arquivo)."""
    if not cell_clicked or not row_data:
        return no_update
    row_index = cell_clicked.get('rowIndex')
    if row_index is None or not (0 <= row_index < len(row_data)):
        return no_update
    linha = row_data[row_index]
    tag = linha.get('tag')
    if not tag or not linha.get('n'):
        return None
    return _detalhe_estrategia_expandido(tag)


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
    está setado) voltam ao modo criar. Gerenciamento de Risco NÃO é mais
    referenciado aqui (correção do Jesué 2026-08-03: é definido por ATIVO,
    não por estratégia — ver `gerenciar_gerenciamento_risco`)."""
    ctx = callback_context
    n_saidas = 10
    if not ctx.triggered:
        return (no_update,) * n_saidas
    origem = ctx.triggered[0]['prop_id']
    prop_curto = origem.rsplit('.', 1)[0]
    estilo_cancelar_oculto = {**_estilo_btn_pequeno(), 'marginLeft': '8px', 'display': 'none'}
    estilo_cancelar_visivel = {**_estilo_btn_pequeno(), 'marginLeft': '8px', 'display': 'inline-block',
                                'color': C['gold'], 'borderColor': C['gold']}

    if prop_curto == 'btn-diario-cancelar-edicao-estrategia':
        if not n_cancelar:
            return (no_update,) * n_saidas
        return (no_update, 'Edição cancelada.', '', '', '', None, None,
                'Adicionar Estratégia', estilo_cancelar_oculto, None)

    if prop_curto == 'btn-diario-add-estrategia':
        if not nome or not nome.strip():
            return (no_update, _texto_com_icone('tabler:alert-triangle', 'Informe um nome.')) + (no_update,) * (n_saidas - 2)
        checklist = [linha.strip() for linha in (checklist_texto or '').split('\n') if linha.strip()]
        if editando_id:
            dt.editar_estrategia(editando_id, nome=nome.strip(), descricao=descricao or '',
                                  checklist=checklist, categoria=categoria)
            return (_tabela_estrategias(dt.listar_estrategias()), _texto_com_icone('tabler:check', f'"{nome.strip()}" atualizada.'),
                    '', '', '', None, None, 'Adicionar Estratégia', estilo_cancelar_oculto, None)
        dt.criar_estrategia(nome, descricao or '', checklist, categoria=categoria)
        return (_tabela_estrategias(dt.listar_estrategias()), _texto_com_icone('tabler:check', f'"{nome.strip()}" cadastrada.'),
                '', '', '', None, no_update, no_update, no_update, no_update)

    if prop_curto.startswith('{'):
        id_componente = json.loads(prop_curto)
        if id_componente['type'] == 'diario-estrategia-editar':
            if not any(n_editar or []):
                return (no_update,) * n_saidas
            df = dt.listar_estrategias(somente_ativas=False)
            linha = df[df['id'] == id_componente['index']]
            if linha.empty:
                return (no_update,) * n_saidas
            linha0 = linha.iloc[0]
            checklist_texto_novo = '\n'.join(_lista_segura(linha0['checklist']))
            categoria_atual = linha0.get('categoria')
            categoria_atual = None if pd.isna(categoria_atual) else str(categoria_atual)
            aviso = html.Div(_texto_com_icone('tabler:pencil', f'Editando "{linha0["nome"]}" (ID {linha0["id"]}).', tamanho=12),
                              style={'fontSize': '11px', 'color': C['gold'], 'marginBottom': '8px',
                                     'display': 'flex', 'alignItems': 'center', 'gap': '4px'})
            return (no_update, '', linha0['nome'], linha0['descricao'] or '', checklist_texto_novo,
                    categoria_atual, int(linha0['id']), 'Salvar Alterações', estilo_cancelar_visivel, aviso)
        if id_componente['type'] == 'diario-estrategia-desativar':
            if not any(n_desativar or []):
                return (no_update,) * n_saidas
            dt.desativar_estrategia(id_componente['index'])
            return (_tabela_estrategias(dt.listar_estrategias()), _texto_com_icone('tabler:check', 'Estratégia desativada.')) + (no_update,) * (n_saidas - 2)
    return (no_update,) * n_saidas


@app.callback(
    Output('diario-tabela-checklist-pretrade', 'children'),
    Output('diario-checklist-pretrade-status', 'children'),
    Output('diario-novo-checklist-pretrade-nome', 'value'),
    Output('diario-novo-checklist-pretrade-itens', 'value'),
    Output('diario-novo-checklist-pretrade-padrao', 'value'),
    Input('btn-diario-add-checklist-pretrade', 'n_clicks'),
    Input({'type': 'diario-checklist-pretrade-desativar', 'index': ALL}, 'n_clicks'),
    Input({'type': 'diario-checklist-pretrade-padrao', 'index': ALL}, 'n_clicks'),
    State('diario-novo-checklist-pretrade-nome', 'value'),
    State('diario-novo-checklist-pretrade-itens', 'value'),
    State('diario-novo-checklist-pretrade-padrao', 'value'),
    State('diario-novo-checklist-pretrade-limite', 'value'),
    State('diario-novo-checklist-pretrade-limite-critico', 'value'),
    State('diario-novo-checklist-pretrade-limite-stops', 'value'),
    State({'type': 'diario-novo-checklist-pretrade-limite-ativo', 'index': ALL}, 'value'),
    State({'type': 'diario-novo-checklist-pretrade-limite-ativo', 'index': ALL}, 'id'),
    State('diario-novo-checklist-pretrade-meta-prejuizo', 'value'),
    State('diario-novo-checklist-pretrade-meta-ganho', 'value'),
    prevent_initial_call=True,
)
def gerenciar_checklist_pretrade(n_add, n_desativar, n_padrao, nome, itens_texto, padrao_marcado,
                                  limite_trades, limite_critico, limite_stops,
                                  limites_ativo_valores, limites_ativo_ids, meta_prejuizo, meta_ganho):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario-add-checklist-pretrade'):
        itens = [linha.strip() for linha in (itens_texto or '').split('\n') if linha.strip()]
        if not nome or not nome.strip() or not itens:
            return no_update, _texto_com_icone('tabler:alert-triangle', 'Informe nome e ao menos 1 item.'), no_update, no_update, no_update
        _para_int = lambda v: int(v) if _num(v) is not None else None
        limite_por_ativo = {
            id_comp['index']: _para_int(valor)
            for id_comp, valor in zip(limites_ativo_ids, limites_ativo_valores)
            if _para_int(valor)
        }
        dt.criar_checklist_pretrade(nome, itens, padrao='padrao' in (padrao_marcado or []),
                                     limite_trades_dia=_para_int(limite_trades), limite_critico_trades_dia=_para_int(limite_critico),
                                     limite_stops_dia=_para_int(limite_stops), limite_trades_por_ativo=limite_por_ativo,
                                     meta_prejuizo_dia=_num(meta_prejuizo), meta_ganho_dia=_num(meta_ganho))
        return _tabela_checklists_pretrade(dt.listar_checklists_pretrade()), _texto_com_icone('tabler:check', f'"{nome.strip()}" cadastrado.'), '', '', []
    prop_curto = origem.rsplit('.', 1)[0]
    if not prop_curto.startswith('{'):
        return no_update, no_update, no_update, no_update, no_update
    id_componente = json.loads(prop_curto)
    if id_componente['type'] == 'diario-checklist-pretrade-desativar':
        if not any(n_desativar or []):
            return no_update, no_update, no_update, no_update, no_update
        dt.desativar_checklist_pretrade(id_componente['index'])
        return _tabela_checklists_pretrade(dt.listar_checklists_pretrade()), _texto_com_icone('tabler:check', 'Checklist desativado.'), no_update, no_update, no_update
    if id_componente['type'] == 'diario-checklist-pretrade-padrao':
        if not any(n_padrao or []):
            return no_update, no_update, no_update, no_update, no_update
        dt.definir_checklist_pretrade_padrao(id_componente['index'])
        return _tabela_checklists_pretrade(dt.listar_checklists_pretrade()), _texto_com_icone('tabler:check', 'Padrão atualizado.'), no_update, no_update, no_update
    return no_update, no_update, no_update, no_update, no_update


@app.callback(
    Output('diario-tabela-gerenciamento-risco', 'children'),
    Output('diario-gerenciamento-risco-status', 'children'),
    Output('diario-novo-gerenciamento-risco-nome', 'value'),
    Output('diario-novo-gerenciamento-risco-ativo', 'value'),
    Output('diario-novo-gerenciamento-risco-alvo', 'value'),
    Output('diario-novo-gerenciamento-risco-stop', 'value'),
    Output('diario-novo-gerenciamento-risco-be', 'value'),
    Output('diario-novo-gerenciamento-risco-saida', 'value'),
    Output('diario-novo-gerenciamento-risco-conduta', 'value'),
    Output('diario-novo-gerenciamento-risco-justificativa', 'value'),
    Output('diario-novo-gerenciamento-risco-padrao', 'value'),
    Input('btn-diario-add-gerenciamento-risco', 'n_clicks'),
    Input({'type': 'diario-gerenciamento-risco-desativar', 'index': ALL}, 'n_clicks'),
    Input({'type': 'diario-gerenciamento-risco-padrao', 'index': ALL}, 'n_clicks'),
    State('diario-novo-gerenciamento-risco-nome', 'value'),
    State('diario-novo-gerenciamento-risco-ativo', 'value'),
    State('diario-novo-gerenciamento-risco-alvo', 'value'),
    State('diario-novo-gerenciamento-risco-stop', 'value'),
    State('diario-novo-gerenciamento-risco-be', 'value'),
    State('diario-novo-gerenciamento-risco-saida', 'value'),
    State('diario-novo-gerenciamento-risco-conduta', 'value'),
    State('diario-novo-gerenciamento-risco-justificativa', 'value'),
    State('diario-novo-gerenciamento-risco-padrao', 'value'),
    prevent_initial_call=True,
)
def gerenciar_gerenciamento_risco(n_add, n_desativar, n_padrao, nome, ativo_mercado, alvo, stop, be,
                                  saida, conduta, justificativa, padrao_marcado):
    """CRUD do cadastro SEPARADO de Gerenciamento de Risco (Playbook,
    2026-08-02; corrigido 2026-08-03 — definido por ATIVO, não mais
    referenciado pela Estratégia)."""
    n_saidas = 11
    ctx = callback_context
    if not ctx.triggered:
        return (no_update,) * n_saidas
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario-add-gerenciamento-risco'):
        alvo_n, stop_n, be_n = _num(alvo), _num(stop), _num(be)
        if not nome or not nome.strip() or not ativo_mercado or alvo_n is None or stop_n is None:
            return (no_update, _texto_com_icone('tabler:alert-triangle', 'Informe nome, ativo, alvo e stop.')) + (no_update,) * (n_saidas - 2)
        dt.criar_gerenciamento_risco(nome, ativo_mercado, alvo_n, stop_n, be_n, saida or '', conduta or '',
                                      justificativa or '', padrao='padrao' in (padrao_marcado or []))
        return (_tabela_gerenciamento_risco(dt.listar_gerenciamentos_risco()), _texto_com_icone('tabler:check', f'"{nome.strip()}" cadastrado.'),
                '', None, None, None, None, '', '', '', [])
    prop_curto = origem.rsplit('.', 1)[0]
    if not prop_curto.startswith('{'):
        return (no_update,) * n_saidas
    id_componente = json.loads(prop_curto)
    if id_componente['type'] == 'diario-gerenciamento-risco-padrao':
        if not any(n_padrao or []):
            return (no_update,) * n_saidas
        dt.definir_gerenciamento_risco_padrao(id_componente['index'])
        return (_tabela_gerenciamento_risco(dt.listar_gerenciamentos_risco()), _texto_com_icone('tabler:check', 'Padrão atualizado.')) + \
               (no_update,) * (n_saidas - 2)
    if not any(n_desativar or []):
        return (no_update,) * n_saidas
    dt.desativar_gerenciamento_risco(id_componente['index'])
    return (_tabela_gerenciamento_risco(dt.listar_gerenciamentos_risco()), _texto_com_icone('tabler:check', 'Gerenciamento desativado.')) + \
           (no_update,) * (n_saidas - 2)


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
            return no_update, _texto_com_icone('tabler:alert-triangle', 'Informe um nome.'), no_update
        dt.criar_tag_setup_erro(nome, tipo or 'ambos')
        return _tabela_tags(dt.listar_tags_setup_erro()), _texto_com_icone('tabler:check', f'"{nome.strip()}" cadastrada.'), ''
    if not any(n_desativar or []):
        return no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_tag_setup_erro(id_componente['index'])
    return _tabela_tags(dt.listar_tags_setup_erro()), _texto_com_icone('tabler:check', 'Tag desativada.'), no_update


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
            return no_update, _texto_com_icone('tabler:alert-triangle', 'Informe um nome.'), no_update
        dt.criar_tag_emocional(nome)
        return _tabela_tags_emocionais(dt.listar_tags_emocionais()), _texto_com_icone('tabler:check', f'"{nome.strip()}" cadastrada.'), ''
    if not any(n_desativar or []):
        return no_update, no_update, no_update
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_tag_emocional(id_componente['index'])
    return _tabela_tags_emocionais(dt.listar_tags_emocionais()), _texto_com_icone('tabler:check', 'Tag desativada.'), no_update


def _validar_horario(valor: str | None) -> str | None:
    """Retorna `None` se `valor` for um horário HH:MM plausível, senão uma
    mensagem de erro pronta pra exibir. Mesmo parsing de
    `features.calcular_temporal` (`map(int, valor.split(':'))`) — validar
    aqui evita salvar uma linha que quebraria o cálculo de feature depois."""
    if not valor or ':' not in valor:
        return 'Use o formato HH:MM (ex: 09:00).'
    try:
        h, m = map(int, valor.strip().split(':'))
    except ValueError:
        return 'Use o formato HH:MM (ex: 09:00).'
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return 'Horário fora do intervalo válido (00:00-23:59).'
    return None


@app.callback(
    Output('diario-tabela-sessoes', 'children'),
    Output('diario-sessao-status', 'children'),
    Output('diario-nova-sessao-nome', 'value'),
    Output('diario-nova-sessao-inicio', 'value'),
    Output('diario-nova-sessao-fim', 'value'),
    Output('diario-nova-sessao-ativos', 'value'),
    Input('btn-diario-add-sessao', 'n_clicks'),
    Input({'type': 'diario-sessao-desativar', 'index': ALL}, 'n_clicks'),
    State('diario-nova-sessao-nome', 'value'),
    State('diario-nova-sessao-inicio', 'value'),
    State('diario-nova-sessao-fim', 'value'),
    State('diario-nova-sessao-ativos', 'value'),
    prevent_initial_call=True,
)
def gerenciar_sessoes(n_add, n_desativar, nome, inicio, fim, ativos):
    ctx = callback_context
    if not ctx.triggered:
        return (no_update,) * 6
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario-add-sessao.'):
        if not nome or not nome.strip():
            return (no_update, _texto_com_icone('tabler:alert-triangle', 'Informe um nome.')) + (no_update,) * 4
        erro = _validar_horario(inicio) or _validar_horario(fim)
        if erro:
            return (no_update, _texto_com_icone('tabler:alert-triangle', erro)) + (no_update,) * 4
        dt.criar_sessao(nome, inicio.strip(), fim.strip(), ativos=ativos or [])
        return (_tabela_sessoes(dt.listar_sessoes()), _texto_com_icone('tabler:check', f'"{nome.strip()}" cadastrada.'),
                '', '', '', [])
    if not any(n_desativar or []):
        return (no_update,) * 6
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_sessao(id_componente['index'])
    return (_tabela_sessoes(dt.listar_sessoes()), _texto_com_icone('tabler:check', 'Sessão desativada.')) + (no_update,) * 4


@app.callback(
    Output('diario-tabela-janelas', 'children'),
    Output('diario-janela-status', 'children'),
    Output('diario-nova-janela-nome', 'value'),
    Output('diario-nova-janela-inicio', 'value'),
    Output('diario-nova-janela-fim', 'value'),
    Output('diario-nova-janela-ativos', 'value'),
    Input('btn-diario-add-janela', 'n_clicks'),
    Input({'type': 'diario-janela-desativar', 'index': ALL}, 'n_clicks'),
    State('diario-nova-janela-nome', 'value'),
    State('diario-nova-janela-inicio', 'value'),
    State('diario-nova-janela-fim', 'value'),
    State('diario-nova-janela-ativos', 'value'),
    prevent_initial_call=True,
)
def gerenciar_janelas(n_add, n_desativar, nome, inicio, fim, ativos):
    ctx = callback_context
    if not ctx.triggered:
        return (no_update,) * 6
    origem = ctx.triggered[0]['prop_id']
    if origem.startswith('btn-diario-add-janela.'):
        if not nome or not nome.strip():
            return (no_update, _texto_com_icone('tabler:alert-triangle', 'Informe um nome.')) + (no_update,) * 4
        erro = _validar_horario(inicio) or _validar_horario(fim)
        if erro:
            return (no_update, _texto_com_icone('tabler:alert-triangle', erro)) + (no_update,) * 4
        dt.criar_janela(nome, inicio.strip(), fim.strip(), ativos=ativos or [])
        return (_tabela_janelas(dt.listar_janelas()), _texto_com_icone('tabler:check', f'"{nome.strip()}" cadastrada.'),
                '', '', '', [])
    if not any(n_desativar or []):
        return (no_update,) * 6
    id_componente = json.loads(origem.rsplit('.', 1)[0])
    dt.desativar_janela(id_componente['index'])
    return (_tabela_janelas(dt.listar_janelas()), _texto_com_icone('tabler:check', 'Janela desativada.')) + (no_update,) * 4


def _conteudo_checklist_estrategia(estrategia_tags, id_checklist):
    """Checklist da estratégia resolvida via `dt.resolver_estrategia_unica`
    — `None`/2+ estratégias mostram um aviso, não adivinha qual usar.
    `id_checklist` é o id do `dcc.Checklist` renderizado (string em Novo
    Trade, dict pattern-matching em Posição)."""
    estrategia = dt.resolver_estrategia_unica(estrategia_tags)
    if estrategia is None:
        return html.Div('Selecione exatamente 1 estratégia pra ver o Checklist da Estratégia.',
                         style={'fontSize': '12px', 'color': C['text2']})
    itens = _lista_segura(estrategia.get('checklist'))
    if not itens:
        return html.Div('Esta estratégia não tem checklist cadastrado.',
                         style={'fontSize': '12px', 'color': C['text2']})
    return dcc.Checklist(
        id=id_checklist,
        options=[{'label': f' {item}', 'value': i} for i, item in enumerate(itens)],
        value=[], style={'fontSize': '12px', 'color': C['text'], 'marginTop': '8px'},
        labelStyle={'display': 'block', 'marginBottom': '4px'},
    )


def _conteudo_risco_info(gerenciamento: dict | None) -> tuple:
    """Resumo textual (Alvo/Stop/Saída/Conduta) do Gerenciamento de Risco
    RESOLVIDO (já não vem mais da estratégia — vem do ativo, ver
    `aplicar_risco_ativo`/`aplicar_gerenciamento_risco_selecionado`).
    Devolve `(resumo_html, alvo_pontos|None, stop_pontos|None)`."""
    if not gerenciamento:
        return (html.Div('Nenhum gerenciamento de risco cadastrado para este ativo.',
                          style={'fontSize': '12px', 'color': C['text2']}), None, None)
    partes = []
    if pd.notna(gerenciamento.get('alvo_pontos')) or pd.notna(gerenciamento.get('stop_pontos')):
        partes.append(_fmt_risco_resumo(gerenciamento))
    if gerenciamento.get('regra_saida'):
        partes.append(f"Saída: {gerenciamento['regra_saida']}")
    if gerenciamento.get('regra_conduta'):
        partes.append(f"Conduta: {gerenciamento['regra_conduta']}")
    resumo = html.Div(' · '.join(partes) if partes else 'Sem parâmetros cadastrados.',
                       style={'fontSize': '12px', 'color': C['text2']})
    alvo_bruto, stop_bruto = gerenciamento.get('alvo_pontos'), gerenciamento.get('stop_pontos')
    alvo = float(alvo_bruto) if pd.notna(alvo_bruto) else None
    stop = float(stop_bruto) if pd.notna(stop_bruto) else None
    return resumo, alvo, stop


@app.callback(
    Output('diario-checklist-estrategia-conteudo', 'children'),
    Output('store-diario-checklist-estrategia-marcados', 'data'),
    Input('diario-estrategia', 'value'),
    prevent_initial_call=True,
)
def aplicar_checklist_estrategia(estrategia_tags):
    # Store zera sempre que a estratégia muda (o checklist antigo some/
    # troca) — nunca deixa marcações de uma estratégia vazarem pra outra.
    return _conteudo_checklist_estrategia(estrategia_tags, 'diario-checklist-estrategia-itens'), []


@app.callback(
    Output('diario-gerenciamento-risco', 'options'),
    Output('diario-gerenciamento-risco', 'value'),
    Input('diario-ativo', 'value'),
    prevent_initial_call=True,
)
def aplicar_risco_ativo(ativo):
    """Selecionar o Ativo carrega o gerenciamento `padrao` DAQUELE ativo
    (correção do Jesué 2026-08-03: risco é definido primeiro por ativo,
    não por estratégia — WIN/WDO têm parâmetros diferentes). O seletor
    resultante pode ser trocado por qualquer outro gerenciamento cadastrado
    pra esse ativo (ver `aplicar_gerenciamento_risco_selecionado`)."""
    if not ativo:
        return [], None
    padrao = dt.obter_gerenciamento_risco_padrao(ativo)
    return _opcoes_gerenciamento_risco(ativo), (int(padrao['id']) if padrao else None)


@app.callback(
    Output('diario-saldo', 'label'),
    Input('diario-ativo', 'value'),
    prevent_initial_call=True,
)
def rotular_saldo_por_moeda(ativo):
    """Ativos cTrader (XAUUSD/EURUSD) liquidam em USD, não BRL (2026-08-05)
    — o rótulo do campo troca pra deixar claro em qual moeda digitar; a
    conversão pra BRL (proxy WDO) acontece em `salvar_novo_trade`, nunca
    aqui (só rótulo, sem lógica de negócio na camada de UI)."""
    moeda = cfg.MOEDA_ATIVO.get(ativo, 'BRL')
    return 'Saldo (US$)' if moeda == 'USD' else 'Saldo (R$)'


@app.callback(
    Output('diario-alvo', 'label'),
    Output('diario-stop', 'label'),
    Output('diario-resultado', 'label'),
    Input('diario-ativo', 'value'),
    prevent_initial_call=True,
)
def rotular_pontuacao_por_ativo(ativo):
    """Mesmo raciocínio de `rotular_saldo_por_moeda` — ativos cTrader
    (XAUUSD/EURUSD) reportam Alvo/Stop/Resultado em PIPS, não pontos
    (2026-08-05, pedido do Jesué: "ajuste isso"). `cfg.UNIDADE_PONTUACAO_
    ATIVO` é só rótulo — o número digitado não é convertido, é o dado que
    já vem na unidade certa da origem (extrato cTrader já é pips)."""
    unidade = _unidade_pontuacao(ativo)
    return f'Alvo ({unidade})', f'Stop ({unidade})', f'Resultado ({unidade})'


@app.callback(
    Output('label-risco-alvo', 'children'),
    Output('label-risco-stop', 'children'),
    Output('label-risco-be', 'children'),
    Input('diario-novo-gerenciamento-risco-ativo', 'value'),
    prevent_initial_call=True,
)
def rotular_pontuacao_gerenciamento_risco(ativo):
    """Mesmo raciocínio de `rotular_pontuacao_por_ativo`, aplicado ao
    cadastro de Gerenciamento de Risco (que já é definido por ativo)."""
    unidade = _unidade_pontuacao(ativo)
    return f'Meta Ganho — Alvo ({unidade})', f'Meta Perda — Stop ({unidade})', f'Breakeven ({unidade}, opcional)'


@app.callback(
    Output('diario-janela', 'data'),
    Output('diario-janela', 'value'),
    Input('diario-ativo', 'value'),
    prevent_initial_call=True,
)
def atualizar_janelas_por_ativo(ativo):
    """Janela é cadastro POR ATIVO (2026-08-04) — B3 (WIN/WDO/BIT) e mercado
    internacional (XAUUSD/EURUSD) têm sessões diferentes. Trocar o Ativo
    recarrega as opções pra esse ativo e limpa a seleção — a janela válida
    pra um ativo quase nunca é válida pro outro (2026-08-06, correção do
    Jesué: o Novo Trade mostrava Abertura/Prime/Almoço/Tarde/Final de B3
    mesmo pra XAUUSD)."""
    if not ativo:
        return _opcoes_janelas(), None
    return _opcoes_janelas(ativo=ativo), None


@app.callback(
    Output('diario-risco-info', 'children'),
    Output('diario-alvo', 'value'),
    Output('diario-stop', 'value'),
    Input('diario-gerenciamento-risco', 'value'),
    prevent_initial_call=True,
)
def aplicar_gerenciamento_risco_selecionado(id_gerenciamento):
    resumo, alvo, stop = _conteudo_risco_info(dt.obter_gerenciamento_risco(id_gerenciamento))
    return resumo, (alvo if alvo is not None else no_update), (stop if stop is not None else no_update)


@app.callback(
    Output('store-diario-checklist-estrategia-marcados', 'data', allow_duplicate=True),
    Input('diario-checklist-estrategia-itens', 'value'),
    prevent_initial_call=True,
)
def espelhar_checklist_estrategia_novo_trade(marcados):
    """Espelha o `.value` do checklist (que só existe quando 1 estratégia
    está resolvida) pro Store sempre-montado — ver comentário no
    `dcc.Store` estático do porquê isso é necessário."""
    return marcados or []


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


def _snapshot_playbook_trade(estrategia_tags, alvo_pontos, stop_pontos, checklist_estrategia_marcados,
                              id_gerenciamento_risco) -> dict:
    """Monta os 6 campos de snapshot do Playbook (COLUNAS_DIARIO) no
    momento do save/finalize de um trade — CONGELADOS ali, nunca uma
    referência viva ao cadastro (ver comentário em COLUNAS_DIARIO).
    `checklist_pretrade_*_dia`/`coaching_checkin_feito` vêm do registro
    DIÁRIO (ritual único, não escolhido por trade). `id_gerenciamento_risco`
    é o gerenciamento REALMENTE selecionado no trade (corrigido 2026-08-03
    — vem do seletor por ativo, não mais resolvido pela estratégia)."""
    hoje = date.today()
    estrategia = dt.resolver_estrategia_unica(estrategia_tags)
    checklist_estrategia_total = len(_lista_segura(estrategia.get('checklist'))) if estrategia else 0
    gerenciamento = dt.obter_gerenciamento_risco(id_gerenciamento_risco)
    risco_seguido = dt.avaliar_risco_seguido(alvo_pontos, stop_pontos, gerenciamento)

    checklist_pretrade_dia = dt.obter_checklist_pretrade_dia(hoje)
    checklist_pretrade_marcados = checklist_pretrade_dia['marcados'] if checklist_pretrade_dia else []
    checklist_pretrade_total = checklist_pretrade_dia['total'] if checklist_pretrade_dia else None

    return dict(
        checklist_estrategia_slug=estrategia.get('slug') if estrategia else None,
        checklist_estrategia_marcados=list(checklist_estrategia_marcados or []) if estrategia else None,
        checklist_estrategia_total=checklist_estrategia_total if estrategia else None,
        checklist_pretrade_marcados_dia=list(checklist_pretrade_marcados),
        checklist_pretrade_total_dia=checklist_pretrade_total,
        risco_gerenciamento_seguido=risco_seguido,
        coaching_checkin_feito=bool(dt.obter_checkin_dia(hoje)),
    )


def _preview_imagem_com_remover(src: str, id_botao) -> html.Div:
    """Print + X pra remover (2026-08-06, pedido do Jesué depois de achar o
    print colado sozinho num trade — precisa de como desfazer isso sem
    fechar o formulário inteiro). `id_botao` liga num callback dedicado que
    limpa a fonte (upload/store de colar) além do preview em si — só
    esconder a imagem não bastaria, o próximo Salvar reanexaria do mesmo
    jeito que causou o bug original."""
    return html.Div([
        html.Img(src=src, style={'maxWidth': '240px', 'borderRadius': '6px',
                                  'border': f'1px solid {C["border"]}', 'display': 'block'}),
        html.Button('×', id=id_botao, n_clicks=0, title='Remover print', style={
            'position': 'absolute', 'top': '-8px', 'right': '-8px', 'width': '22px', 'height': '22px',
            'borderRadius': '50%', 'border': f'1px solid {C["border"]}', 'background': C['red'],
            'color': '#fff', 'cursor': 'pointer', 'fontSize': '14px', 'lineHeight': '1', 'padding': 0,
            'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
        }),
    ], style={'position': 'relative', 'display': 'inline-block'})


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
    return _preview_imagem_com_remover(data_url, 'btn-remover-print-novo-trade')


@app.callback(
    Output('diario-preview-imagem', 'children', allow_duplicate=True),
    Output('diario-upload-imagem', 'contents', allow_duplicate=True),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-remover-print-novo-trade', 'n_clicks'),
    prevent_initial_call=True,
)
def remover_print_novo_trade(n_clicks):
    if not n_clicks:
        return no_update, no_update, no_update
    return None, None, None


@app.callback(
    Output('diario-edit-preview-imagem', 'children'),
    Output('store-diario-edit-imagem-removida', 'data', allow_duplicate=True),
    Input('diario-edit-upload-imagem', 'contents'),
    Input('store-diario-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def preview_imagem_editar_trade(upload_contents, imagem_colada):
    """Mesmo padrão de `preview_imagem_diario` (Novo Trade) — reaproveita o
    MESMO store global de colar (`store-diario-imagem-colada`), já que só
    um drawer fica aberto por vez na prática (2026-08-06). Um print NOVO
    (colado ou subido) sempre reseta `store-diario-edit-imagem-removida`
    pra False — a intenção de remover não sobrevive a uma substituição."""
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    data_url = upload_contents if origem == 'diario-edit-upload-imagem' else imagem_colada
    if not data_url:
        return no_update, no_update
    return _preview_imagem_com_remover(data_url, 'btn-remover-print-editar-trade'), False


@app.callback(
    Output('diario-edit-preview-imagem', 'children', allow_duplicate=True),
    Output('diario-edit-upload-imagem', 'contents', allow_duplicate=True),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
    Output('store-diario-edit-imagem-removida', 'data', allow_duplicate=True),
    Input('btn-remover-print-editar-trade', 'n_clicks'),
    prevent_initial_call=True,
)
def remover_print_editar_trade(n_clicks):
    """Cobre os 2 casos: print JÁ SALVO (o `True` aqui vira `imagem_arquivo
    = None` em `salvar_trade_editado`) e print recém colado/subido ainda
    não salvo (limpo direto das fontes, nunca chega a ser gravado)."""
    if not n_clicks:
        return no_update, no_update, no_update, no_update
    return None, None, None, True


@app.callback(
    Output('diario-edit-sentimento-label', 'children'),
    Output('diario-edit-sentimento-label', 'style'),
    Input('diario-edit-sentimento-mercado', 'value'),
)
def atualizar_label_sentimento_editar_trade(valor):
    resultado = dt.classificar_sentimento_mercado(valor)
    if resultado is None:
        return '—', {'fontSize': '12px', 'color': C['text2']}
    nome, cor_chave = resultado
    return nome, {'fontSize': '12px', 'fontWeight': '700', 'color': C.get(cor_chave, C['text'])}


@app.callback(
    Output('diario-status', 'children'),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
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
    State('diario-tags-setup-erro', 'data'),
    State('diario-emo-confianca', 'value'),
    State('diario-emo-humor', 'value'),
    State('diario-emo-energia', 'value'),
    State('diario-emo-presenca', 'value'),
    State('diario-emo-equilibrio', 'value'),
    State('diario-emo-disciplina', 'value'),
    State('diario-tags-emocionais', 'data'),
    State('diario-sentimento-mercado', 'value'),
    State('diario-upload-imagem', 'contents'),
    State('store-diario-imagem-colada', 'data'),
    State('store-diario-checklist-estrategia-marcados', 'data'),
    State('diario-gerenciamento-risco', 'value'),
    prevent_initial_call=True,
)
def salvar_novo_trade(n_clicks, data_op, ativo, janela, estrategia_tags, sala, tipo_op, alvo, stop, lote,
                       preco_entrada, preco_saida, tipo_saida, resultado, saldo, resumo, obs,
                       nivel_sr, nivel_sr_tipo, tags_setup_erro,
                       emo_confianca, emo_humor, emo_energia, emo_presenca, emo_equilibrio, emo_disciplina,
                       tags_emocionais, sentimento_mercado, upload_contents, imagem_colada,
                       checklist_estrategia_marcados, id_gerenciamento_risco):
    if not data_op or not ativo:
        return _texto_com_icone('tabler:alert-triangle', 'Preencha ao menos Data e Ativo.'), no_update

    alvo, stop, preco_entrada, preco_saida, resultado, saldo, nivel_sr = (
        _num(alvo), _num(stop), _num(preco_entrada), _num(preco_saida),
        _num(resultado), _num(saldo), _num(nivel_sr),
    )
    # Ativos cTrader (XAUUSD/EURUSD, 2026-08-05) liquidam em USD — o valor
    # digitado em "Saldo" é o nativo, convertido pra BRL via proxy WDO
    # (`dt.converter_usd_para_reais`) pra `saldo_operacao_reais` continuar
    # SEMPRE homogêneo em BRL (estatísticas consolidadas não precisam saber
    # de moeda). `data_op` é a melhor referência de horário disponível no
    # formulário manual (não pede hora, só data).
    saldo_usd = None
    if cfg.MOEDA_ATIVO.get(ativo) == 'USD':
        saldo_usd = saldo
        saldo = dt.converter_usd_para_reais(saldo_usd, data_op)
    campos = dict(
        data_op=data_op, ativo=ativo, janela_pregao=janela,
        estrategia_tags=estrategia_tags or [], sala_analise=sala,
        tipo_operacao=tipo_op, alvo_pontos=alvo, stop_pontos=stop, lote=lote or 1,
        preco_entrada=preco_entrada, preco_saida=preco_saida, tipo_saida=tipo_saida,
        resultado_pontos=resultado, saldo_operacao_reais=saldo, saldo_operacao_usd=saldo_usd,
        resumo_operacional=resumo, obs=obs,
        nivel_sr_referencia=nivel_sr, nivel_sr_tipo=nivel_sr_tipo,
        tags_setup_erro=tags_setup_erro or [],
        emo_confianca=emo_confianca, emo_humor=emo_humor, emo_energia=emo_energia,
        emo_presenca=emo_presenca, emo_equilibrio=emo_equilibrio, emo_disciplina=emo_disciplina,
        tags_emocionais=tags_emocionais or [], sentimento_mercado=sentimento_mercado,
        conta='real',
    )
    campos.update(_snapshot_playbook_trade(estrategia_tags, alvo, stop, checklist_estrategia_marcados,
                                            id_gerenciamento_risco))

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
        return (_texto_com_icone('tabler:alert-triangle',
                                  'Diário ocupado por outra escrita — tente novamente em instantes.'), no_update)
    # 2026-08-06, mesmo fix de `salvar_trade_editado` — limpa o store GLOBAL
    # de colar depois de consumido, senão um print colado uma vez fica
    # preso ali e reanexa sozinho no PRÓXIMO trade salvo (Novo Trade ou
    # Editar), mesmo sem colar nada de novo.
    return _texto_com_icone('tabler:check', f'Operação #{id_trade} salva.'), None


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
    Output('diario-tags-setup-erro-wrap', 'children'),
    Output('diario-emo-confianca', 'value'),
    Output('diario-emo-humor', 'value'),
    Output('diario-emo-energia', 'value'),
    Output('diario-emo-presenca', 'value'),
    Output('diario-emo-equilibrio', 'value'),
    Output('diario-emo-disciplina', 'value'),
    Output('diario-tags-emocionais-wrap', 'children'),
    Output('diario-sentimento-mercado', 'value'),
    Output('diario-preview-imagem', 'children', allow_duplicate=True),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-diario-limpar', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_form_diario(n_clicks):
    # Tags Setup/Erro e Print Emocional são `_campo_tag_widget` (ver
    # `_wrap_id_tag_widget`) — resetar só o `dcc.Store` escondido não
    # apagaria os chips já desenhados no DOM, então a "limpeza" desses 2
    # campos reconstrói o widget do zero (chips vazios) em vez de zerar um
    # `value`.
    return (None, None, None, None, None, None, None, None, 1,
            None, None, None, None, None, None, None, None, None,
            _campo_tag_widget('diario-tags-setup-erro', _opcoes_tags_setup_erro(), []),
            3, 3, 3, 3, 3, 3,
            _campo_tag_widget('diario-tags-emocionais', _opcoes_tags_emocionais(), []),
            50, None, None)


def _indicador_resumo_historico(df: pd.DataFrame) -> html.Div:
    """Indicador do recorte filtrado, ao lado do título "Diário de Trade"
    (2026-08-01, referência TradeZella: a contagem de trades fica junto
    do título do Trade View, não escondida). 3 pílulas — nº de operações,
    saldo líquido (colorido), taxa de acerto — recalculadas a cada troca
    de filtro, nunca a base inteira."""
    n = len(df)
    if n == 0:
        return dmc.Badge('Nenhuma operação neste filtro', variant='light', color='gray')
    saldo = pd.to_numeric(df['saldo_operacao_reais'], errors='coerce')
    saldo_total = float(saldo.sum(skipna=True))
    wins = int((saldo > 0).sum())
    taxa_acerto = (wins / n * 100) if n else 0.0
    cor_saldo = 'green' if saldo_total > 0 else ('red' if saldo_total < 0 else 'gray')
    return dmc.Group([
        dmc.Badge(f'{n} operações', variant='light', color='gray'),
        dmc.Badge(f'R$ {saldo_total:+.2f}', variant='light', color=cor_saldo),
        dmc.Badge(f'{taxa_acerto:.0f}% de acerto', variant='light', color='gray'),
    ], gap='6px')


@app.callback(
    Output('diario-hist-data-ini', 'value'),
    Output('diario-hist-data-fim', 'value'),
    Output('diario-hist-calendario-range', 'value'),
    Input('diario-hist-calendario-range', 'value'),
    Input('diario-hist-data-ini', 'value'),
    Input('diario-hist-data-fim', 'value'),
    prevent_initial_call=True,
)
def sincronizar_filtro_data_range(range_cal, data_ini_texto, data_fim_texto):
    """Item 2D — calendário de range sempre visível + os 2 inputs de texto
    continuam funcionando (pedido: "mantenha o input para digitar a
    data"). Só 1 direção por disparo (nunca reescreve a própria fonte que
    disparou), então não há loop: calendário clicado atualiza os 2 textos
    (`no_update` no próprio calendário); texto editado atualiza o
    calendário (`no_update` nos 2 textos)."""
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    if origem == 'diario-hist-calendario-range':
        range_cal = range_cal or [None, None]
        ini = range_cal[0] if len(range_cal) > 0 else None
        fim = range_cal[1] if len(range_cal) > 1 else None
        return ini, fim, no_update
    return no_update, no_update, [data_ini_texto, data_fim_texto]


@app.callback(
    Output('grid-historico', 'rowData'),
    Output('diario-hist-pendencias', 'children'),
    Output('diario-hist-banner-filtro-cal', 'children'),
    Output('store-diario-linhas-filtradas', 'data'),
    Output('diario-hist-indicador', 'children'),
    Input('diario-hist-ativo', 'value'),
    Input('diario-hist-janela', 'value'),
    Input('diario-hist-estrategia', 'value'),
    Input('diario-hist-data-ini', 'value'),
    Input('diario-hist-data-fim', 'value'),
    Input('diario-hist-filtro-data-calendario', 'data'),
    Input('store-diario-refresh', 'data'),
)
def atualizar_historico(ativo, janela, estrategia, data_ini, data_fim, filtro_cal, _refresh):
    """Única dona de `grid-historico`/`diario-hist-pendencias`. `_refresh`
    não é usado no corpo de propósito — é só o gatilho de invalidação (ver
    `store-diario-refresh` no layout): finalizar/descartar/"✓" incrementam
    aquele contador e o grid se atualiza aqui, com os filtros que já estão
    na tela, sem que nenhuma daquelas callbacks precise conhecer os ids
    desta aba.

    Task #37 (2026-07-31): substituiu `_montar_tabela_historico`, que capava
    a `html.Table` manual em 50/300 linhas pra não travar o navegador (Task
    #29) — o `dag.AgGrid` (`grid-historico`) virtualiza nativamente, então
    `rowData` recebe a base INTEIRA que bate com os filtros, sem corte.

    `store-diario-linhas-filtradas` (Tasks #30/#31) recebe o MESMO
    `rowData` — Galeria e Day View leem dali em vez de repetir a query."""
    # O clique num dia do calendário (Estatísticas/Dashboard) força um filtro
    # de dia único aqui, sobrepondo o filtro manual de intervalo.
    data_ini_efetivo = filtro_cal or data_ini
    data_fim_efetivo = filtro_cal or data_fim
    df = dt.listar_trades(ativo=ativo, janela=janela, estrategia_tag=estrategia,
                           data_ini=data_ini_efetivo, data_fim=data_fim_efetivo, is_simulated=False)

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
    banner = html.Div(partes_banner, style={'display': 'flex', 'alignItems': 'center', 'gap': '8px',
                                            'flexWrap': 'wrap'}) if partes_banner else ''

    linhas = _linhas_grid_historico(df)
    return linhas, _bloco_pendencias_historico(pendencias), banner, linhas, _indicador_resumo_historico(df)


@app.callback(
    Output('diario-hist-evolucao-periodo', 'children'),
    Input('store-diario-linhas-filtradas', 'data'),
    State('store-tema', 'data'),
)
def atualizar_evolucao_periodo(linhas, tema_atual):
    """Gráfico de Evolução de Saldo do período filtrado (item 2F) — lê do
    MESMO `store-diario-linhas-filtradas` que a Grade/Galeria/Semana já
    usam, nunca refaz o filtro por conta própria."""
    linhas = linhas or []
    tema_atual = tema_atual or 'light'
    if not linhas:
        return html.Div('Nenhuma operação no período filtrado.', style={
            'padding': '12px', 'color': C['text2'], 'fontSize': '12px'})
    return _bloco_evolucao_periodo(linhas, tema_atual)


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
    Output('drawer-detalhe-trade', 'opened', allow_duplicate=True),
    Output('drawer-detalhe-trade-conteudo', 'children', allow_duplicate=True),
    Input('grid-historico', 'cellClicked'),
    State('grid-historico', 'rowData'),
    prevent_initial_call=True,
)
def atualizar_detalhe_historico_grid(cell_clicked, row_data):
    """Clique numa linha do `dag.AgGrid` do Histórico (Task #37, 2026-07-31)
    — substitui a seta ▶/▼ por linha (`alternar_detalhe_historico`, agora
    removida). Abre o MESMO Drawer lateral fixo (`drawer-detalhe-trade`,
    ver `_construir_diario_historico`), nunca um id por linha.

    2026-08-02, causa raiz REAL do "Ver não abre o detalhe" (achada
    clicando de verdade no Chrome real via claude-in-chrome, inspecionando
    o payload de rede do POST /_dash-update-component): o `cellClicked`
    desta versão do dash-ag-grid NUNCA inclui `data`, só
    value/colId/rowIndex/rowId/timestamp — o código antigo sempre caía no
    `no_update` silenciosamente, em TODA coluna, desde que este grid
    existe (Task #37).

    Tentativa de corrigir com `getRowId` (AG-Grid) foi REVERTIDA — ver
    comentário em `_grid_historico`, colapsou a grade inteira pra 1 linha
    (`params.data.id_trade` sempre avaliava `undefined` dentro do
    `getRowId`, motivo ainda não identificado). Fix seguro adotado: usa
    `cell_clicked['rowIndex']` pra indexar direto no `State` do
    `rowData` (que o Dash já mantém populado por `atualizar_historico`).
    Limitação conhecida: se o usuário clicar no cabeçalho pra ordenar a
    grade (client-side, sem passar pelo Dash), `rowIndex` passa a refletir
    a ordem ordenada, que diverge da ordem do `rowData` guardado no State
    — nesse caso o clique pode abrir o trade errado. Não bloqueante pro
    caso comum (sem sort ativo), mas é uma limitação real, documentada
    aqui pra não ser esquecida.

    `allow_duplicate=True`: Galeria e Day View também escrevem nestes
    MESMOS Outputs (`abrir_detalhe_galeria`/`abrir_detalhe_dia`), cada
    modo de visualização com seu próprio jeito de identificar a linha
    clicada."""
    if not cell_clicked or not row_data:
        return no_update, no_update
    row_index = cell_clicked.get('rowIndex')
    if row_index is None or not (0 <= row_index < len(row_data)):
        return no_update, no_update
    id_trade = row_data[row_index].get('id_trade')
    if id_trade is None:
        return no_update, no_update
    return True, _conteudo_detalhe_trade(id_trade)


@app.callback(
    Output('diario-view-grid', 'style'),
    Output('diario-view-galeria', 'style'),
    Output('diario-view-dia', 'style'),
    Output('diario-dia-nav-botoes', 'style'),
    Output('diario-galeria-nav-botoes', 'style'),
    Input('diario-hist-view-modo', 'value'),
)
def alternar_view_modo_historico(modo):
    """Grade/Galeria/Dia (Tasks #29/#30/#31) — os 3 containers ficam
    sempre montados, só `display` alterna (mesma disciplina de sempre). As
    setas de navegação por semana (`diario-dia-nav-botoes`) e de página da
    Galeria (`diario-galeria-nav-botoes`) — ambas na mesma linha do
    seletor de view desde 2026-08-05 — só aparecem no modo correspondente."""
    def estilo(mostrar):
        return {} if mostrar else {'display': 'none'}
    return (estilo(modo == 'grid'), estilo(modo == 'galeria'), estilo(modo == 'dia'),
            estilo(modo == 'dia'), estilo(modo == 'galeria'))


@app.callback(
    Output('diario-galeria-grade', 'children'),
    Output('diario-galeria-label', 'children'),
    Output('btn-galeria-pag-anterior', 'disabled'),
    Output('btn-galeria-pag-proxima', 'disabled'),
    Input('store-diario-linhas-filtradas', 'data'),
    Input('store-diario-galeria-pagina', 'data'),
)
def renderizar_galeria_historico(linhas, pagina):
    """Galeria (Task #30, referência Notion "Trader's Second Brain") — grade
    de cards com print de capa. Paginada (24/página) de propósito: os cards
    têm `<img>` real (não thumbnail lazy), então, diferente do `dag.AgGrid`
    (que virtualiza sozinho), montar as ~225 operações de uma vez aqui
    reintroduziria a MESMA classe de lentidão do Task #29 — ver docstring
    de `_construir_diario_historico`."""
    linhas = linhas or []
    if not linhas:
        vazio = html.Div('Nenhuma operação encontrada.', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
        return vazio, '', True, True
    total_paginas = max(1, -(-len(linhas) // _GALERIA_PAGINA_TAMANHO))
    pagina = min(max(pagina or 1, 1), total_paginas)
    inicio = (pagina - 1) * _GALERIA_PAGINA_TAMANHO
    fatia = linhas[inicio:inicio + _GALERIA_PAGINA_TAMANHO]
    grade = html.Div([_card_galeria_trade(l) for l in fatia], style={
        'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fill, minmax(180px, 1fr))', 'gap': '14px',
    })
    label = f'Página {pagina} de {total_paginas} ({len(linhas)} operações)'
    return grade, label, pagina <= 1, pagina >= total_paginas


@app.callback(
    Output('store-diario-galeria-pagina', 'data'),
    Input('btn-galeria-pag-anterior', 'n_clicks'),
    Input('btn-galeria-pag-proxima', 'n_clicks'),
    Input('store-diario-linhas-filtradas', 'data'),
    State('store-diario-galeria-pagina', 'data'),
    prevent_initial_call=True,
)
def navegar_galeria_historico(n_ant, n_prox, linhas, pagina_atual):
    """Botões ESTÁTICOS (nunca recriados — ver `_construir_diario_
    historico`), só o `n_clicks` deles dispara isto. Filtro mudando
    (`store-diario-linhas-filtradas`) reseta pra página 1 — senão o
    usuário podia ficar numa página que não existe mais no conjunto novo."""
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    if origem == 'store-diario-linhas-filtradas':
        return 1
    if not any([n_ant, n_prox]):
        return no_update
    pagina_atual = pagina_atual or 1
    if origem == 'btn-galeria-pag-anterior':
        return max(1, pagina_atual - 1)
    return pagina_atual + 1


@app.callback(
    Output('drawer-detalhe-trade', 'opened', allow_duplicate=True),
    Output('drawer-detalhe-trade-conteudo', 'children', allow_duplicate=True),
    Input({'type': 'diario-galeria-card', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def abrir_detalhe_galeria(n_clicks_lista):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update, no_update
    id_componente = json.loads(ctx.triggered[0]['prop_id'].rsplit('.', 1)[0])
    return True, _conteudo_detalhe_trade(id_componente['index'])


def _dias_disponiveis(linhas: list) -> list:
    return sorted({l['data'] for l in linhas}, reverse=True)


@app.callback(
    Output('diario-dia-conteudo', 'children'),
    Output('btn-dia-anterior', 'disabled'),
    Output('btn-dia-proximo', 'disabled'),
    Input('store-diario-linhas-filtradas', 'data'),
    Input('store-diario-dia-atual', 'data'),
    Input('store-diario-semana-expandido', 'data'),
    State('store-tema', 'data'),
)
def renderizar_dia_historico(linhas, dia_atual, semana_expandido, tema_atual):
    """Week View (Task #31, referência TradeZella — item 2B do upgrade de
    2026-08-04 removeu o modo "Dia" solto, Semana é o único modo agora):
    dias da semana civil de `dia_atual` empilhados, cada um recolhível —
    o mais recente vem expandido por padrão, o resto recolhido, igual à
    referência (Thu expandido, Wed/Tue recolhidos). "Anterior"/"Próxima"
    agora navegam por SEMANA — desabilitado quando não há nenhum dia com
    trade fora da semana atual naquela direção (ver `navegar_dia_
    historico`). `diario-dia-label` foi removido (2026-08-05, "essa data
    feia no meio não precisa") — o período já aparece preenchido nos
    filtros de data acima."""
    linhas = linhas or []
    tema_atual = tema_atual or 'light'
    if not linhas:
        vazio = html.Div('Nenhuma operação encontrada.', style={
            'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px',
        })
        return vazio, True, True
    dias = _dias_disponiveis(linhas)
    dia_atual = dia_atual if dia_atual in dias else dias[0]

    dias_semana = _dias_da_semana(dia_atual, dias)
    indices_semana = [dias.index(d) for d in dias_semana]
    # Outputs são (btn-dia-anterior.disabled, btn-dia-proximo.disabled),
    # nessa ordem — nomeado direto pelo botão que cada um desabilita
    # (o código original tinha os nomes trocados em relação à posição,
    # "lógica consistente mas confuso" por si só; aqui os nomes batem com
    # a posição de verdade, pra não repetir aquele achado).
    nav_anterior_desabilitada = max(indices_semana) >= len(dias) - 1  # sem semana mais ANTIGA no conjunto filtrado
    nav_proximo_desabilitada = min(indices_semana) <= 0                # sem semana mais RECENTE no conjunto filtrado

    semana_expandido = semana_expandido or {}
    cartoes = []
    for i, d in enumerate(dias_semana):
        trades_do_dia = [l for l in linhas if l['data'] == d]
        expandido = semana_expandido.get(d, i == 0)
        cartoes.append(_cartao_dia_semana(d, trades_do_dia, expandido, tema_atual))
    conteudo = html.Div(cartoes) if cartoes else html.Div(
        'Nenhuma operação nesta semana.',
        style={'padding': '20px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '13px'})
    return conteudo, nav_anterior_desabilitada, nav_proximo_desabilitada


@app.callback(
    Output('store-diario-semana-expandido', 'data'),
    Input({'type': 'diario-semana-header', 'index': ALL}, 'n_clicks'),
    State('store-diario-semana-expandido', 'data'),
    State('store-diario-linhas-filtradas', 'data'),
    State('store-diario-dia-atual', 'data'),
    prevent_initial_call=True,
)
def alternar_semana_dia(n_clicks_lista, expandido, linhas, dia_atual):
    """Toggle de expandir/recolher 1 dia do modo Semana. Estado guardado
    num dict {data: bool} — dias nunca clicados usam o padrão (1º dia da
    semana expandido, resto recolhido, ver `renderizar_dia_historico`), só
    grava no dict a partir do primeiro clique."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    id_componente = json.loads(ctx.triggered[0]['prop_id'].rsplit('.', 1)[0])
    data_clicada = id_componente['index']
    expandido = dict(expandido or {})
    dias = _dias_disponiveis(linhas or [])
    dia_atual = dia_atual if dia_atual in dias else (dias[0] if dias else None)
    dias_semana = _dias_da_semana(dia_atual, dias) if dia_atual else []
    padrao_atual = expandido.get(data_clicada, bool(dias_semana) and dias_semana[0] == data_clicada)
    expandido[data_clicada] = not padrao_atual
    return expandido


@app.callback(
    Output('store-diario-dia-atual', 'data'),
    Input('btn-dia-anterior', 'n_clicks'),
    Input('btn-dia-proximo', 'n_clicks'),
    Input('store-diario-linhas-filtradas', 'data'),
    State('store-diario-dia-atual', 'data'),
    prevent_initial_call=True,
)
def navegar_dia_historico(n_ant, n_prox, linhas, dia_atual):
    """Botões ESTÁTICOS (mesma disciplina da Galeria). Filtro mudando
    reseta pra semana mais recente do conjunto novo (`dias[0]`, já
    ordenado decrescente — ver `_dias_disponiveis`). Item 2B (2026-08-04):
    Semana virou o único modo, então as setas agora pulam pra semana
    anterior/próxima com trade real — não mais dia a dia (que podia ficar
    "preso" na mesma semana civil por vários cliques) — saltando pro
    primeiro dia disponível FORA da semana atual, na direção pedida."""
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    dias = _dias_disponiveis(linhas or [])
    if not dias:
        return None
    if origem == 'store-diario-linhas-filtradas':
        return dias[0]
    if not any([n_ant, n_prox]):
        return no_update
    dia_atual = dia_atual if dia_atual in dias else dias[0]
    dias_semana = _dias_da_semana(dia_atual, dias)
    indices_semana = [dias.index(d) for d in dias_semana]
    if origem == 'btn-dia-anterior':
        indice_alvo = max(indices_semana) + 1
        if indice_alvo >= len(dias):
            return no_update
    else:
        indice_alvo = min(indices_semana) - 1
        if indice_alvo < 0:
            return no_update
    return dias[indice_alvo]


@app.callback(
    Output('drawer-detalhe-trade', 'opened', allow_duplicate=True),
    Output('drawer-detalhe-trade-conteudo', 'children', allow_duplicate=True),
    Input({'type': 'diario-dia-linha', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def abrir_detalhe_dia(n_clicks_lista):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update, no_update
    id_componente = json.loads(ctx.triggered[0]['prop_id'].rsplit('.', 1)[0])
    return True, _conteudo_detalhe_trade(id_componente['index'])


@app.callback(
    Output('diario-status-conexao', 'children'),
    Input('interval-em-andamento', 'n_intervals'),
)
def atualizar_status_conexao(_n):
    """Cabeçalho de conexão, a cada 6s — mesma cadência do guarda-corpo,
    mesmo raciocínio de `atualizar_guarda_corpo_dia` (Input é o interval da
    PRÓPRIA Posição, não um Store global)."""
    return _conteudo_status_conexao()


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
    prevent_initial_call=True,
)
def resumir_checklist(marcados):
    """Playbook (2026-08-02): cada toggle PERSISTE no registro diário
    (`dt.salvar_checklist_pretrade_dia`) — ritual único do dia, igual ao
    check-in emocional, não mais efêmero. Todo trade lançado hoje herda
    este estado automaticamente (ver `salvar_novo_trade`/Finalizar)."""
    checklist = dt.obter_checklist_pretrade()
    total = len(checklist['itens']) if checklist else 0
    n = len(marcados or [])
    if checklist:
        dt.salvar_checklist_pretrade_dia(date.today(), checklist['id'], [int(i) for i in (marcados or [])], total)
    base = {'fontSize': '11px', 'marginTop': '8px', 'fontWeight': '700'}
    if total and n == total:
        return (_texto_com_icone('tabler:check', f'{n}/{total} — dentro do plano.'),
                {**base, 'color': C['green'], 'display': 'flex', 'alignItems': 'center', 'gap': '4px'})
    return (f'{n}/{total} conferidos — faltam {total - n}.',
            {**base, 'color': C['text2'] if n else C['text2']})






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
    Input({'type': 'diario-img-thumb-detalhe', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def ampliar_imagem(n_clicks_lista_detalhe):
    """Amplia a imagem dentro do detalhe de um trade (`diario-img-thumb-
    detalhe`, feedback 2026-07-16 — hoje renderizado abaixo do `dag.AgGrid`
    do Histórico, ver `_conteudo_detalhe_trade`/`atualizar_detalhe_
    historico_grid`). Até 2026-07-31 também cobria a miniatura de linha da
    antiga `html.Table` (`diario-img-thumb`), removida junto com ela.

    Feedback 2026-07-20: "deve ter botões de navegação entre outros prints
    do mesmo trade quando tiver" — a imagem principal (`imagem_arquivo`)
    sempre entra primeiro, seguida das adicionais (`imagens_adicionais`,
    coluna existente desde a Fase C, nunca exposta na UI até agora).
    `indice` sempre começa em 0 (a principal), navegação em
    `navegar_imagem_ampliada`."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista_detalhe or []):
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
    Output('modal-imagem-ampliada', 'opened', allow_duplicate=True),
    Input('btn-diario-fechar-imagem', 'n_clicks'),
    prevent_initial_call=True,
)
def fechar_imagem_manual(n_clicks):
    """Botão "✕ Fechar" do próprio cabeçalho (mantido explícito ao lado dos
    controles de zoom/navegação — ver comentário do `dmc.Modal` no
    layout). Só fecha o Modal; `sincronizar_fechamento_imagem` reage a
    `opened` virando `False` (por AQUI, pelo Esc nativo do Modal, ou
    clique no overlay) e reseta `diario-imagem-ampliada` de verdade."""
    if not n_clicks:
        return no_update
    return False


@app.callback(
    Output('diario-imagem-ampliada', 'data', allow_duplicate=True),
    Input('modal-imagem-ampliada', 'opened'),
    prevent_initial_call=True,
)
def sincronizar_fechamento_imagem(opened):
    if opened:
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
    Output('modal-imagem-ampliada', 'opened'),
    Output('modal-imagem-ampliada-conteudo', 'children'),
    Input('diario-imagem-ampliada', 'data'),
)
def renderizar_modal_imagem_ampliada(dados):
    """`dmc.Modal` em tela cheia (2026-07-31, varredura completa — "a
    visualização de print não está no ux novo"). Os 3 botões de zoom
    (`data-zoom-acao`) continuam `html.Button` cru de propósito: não têm
    callback Python (comportamento 100% client-side, ver `assets/diario_
    zoom.js`) — só o ícone virou `DashIconify` (Tabler), classe CSS
    `.diario-zoom-btn-icone` em `assets/style.css`. Fechar/Anterior/Próxima
    viraram `dmc.ActionIcon` circular com ícone Tabler (2026-08-01,
    correção do Jesué: "essa fonte com emoji... parecem ter saído de um
    site velho dos anos 2000" — nenhum glifo Unicode vira ícone de botão em
    lugar nenhum do Diário a partir daqui)."""
    if not dados or not dados.get('imagens'):
        return False, no_update
    imagens = dados['imagens']
    indice = dados.get('indice', 0)
    # Controles de zoom (feedback 2026-07-30: "com a opção de expandir e dar
    # zoom"). A roda do mouse já dava zoom desde 2026-07-20, mas nada na tela
    # dizia isso. Estes botões não têm callback Python — o comportamento é
    # 100% client-side via `data-zoom-acao` (ver assets/diario_zoom.js);
    # colocá-los no Dash só pra emitir um n_clicks que voltaria pro browser
    # seria um round-trip inútil num controle que precisa ser instantâneo.
    # 2026-08-01, correção do Jesué ("cantos quadrados, fonte com emoji...
    # parecem ter saído de um site velho dos anos 2000"): nenhum glifo
    # Unicode (◀▶✕⛶) mais vira ícone de botão em lugar nenhum do Diário —
    # só `DashIconify` (Tabler) dentro de `dmc.ActionIcon`/`dmc.Button`,
    # mesma família de ícone já usada no menu lateral.
    def _botao_zoom_icone(icone, acao, titulo):
        return html.Button(
            DashIconify(icon=icone, width=18), **{'data-zoom-acao': acao}, title=titulo,
            className='diario-zoom-btn-icone',
        )
    controles_zoom = html.Div([
        _botao_zoom_icone('tabler:zoom-out', 'menos', 'Diminuir (tecla −)'),
        _botao_zoom_icone('tabler:zoom-in', 'mais', 'Ampliar (tecla +)'),
        _botao_zoom_icone('tabler:maximize', 'reset', 'Voltar ao tamanho da tela (tecla 0)'),
        html.Span('roda do mouse amplia · arraste para mover · Esc fecha',
                  style={'fontSize': '11px', 'color': C['text2'], 'marginLeft': '4px'}),
    ], style={'display': 'flex', 'gap': '4px', 'alignItems': 'center', 'flexWrap': 'wrap'})

    topo = [
        dmc.Group([
            dmc.ActionIcon(DashIconify(icon='tabler:x', width=18), id='btn-diario-fechar-imagem', n_clicks=0,
                           variant='subtle', color='gray', radius='xl', size='lg'),
            controles_zoom,
        ], justify='space-between'),
    ]
    if len(imagens) > 1:
        topo.append(dmc.Group([
            dmc.ActionIcon(DashIconify(icon='tabler:chevron-left', width=20), id='btn-diario-imagem-anterior',
                           n_clicks=0, variant='subtle', radius='xl', size='lg'),
            html.Span(f'{indice + 1} / {len(imagens)}', style={'fontSize': '12px', 'color': C['text2']}),
            dmc.ActionIcon(DashIconify(icon='tabler:chevron-right', width=20), id='btn-diario-imagem-proxima',
                           n_clicks=0, variant='subtle', radius='xl', size='lg'),
        ], gap='10px'))
    # Feedback 2026-07-20: "preencher o bloco inteiro com zoom pela rolagem
    # do scroll, e clicar e arrastar" — comportamento em assets/diario_zoom.js
    # (delegação de evento em document, wheel = zoom centrado no cursor,
    # pointer capture = arrastar). `key=imagens[indice]` força o React a
    # remontar um <img>/container NOVO por imagem (não só trocar o src de um
    # nó reciclado) — sem isso o zoom/pan da imagem anterior (guardado direto
    # no elemento DOM pelo JS) vazaria pra próxima ao navegar.
    conteudo = html.Div([
        html.Div(topo, style={'display': 'flex', 'flexDirection': 'column', 'gap': '8px', 'flexShrink': '0'}),
        html.Div(
            html.Img(src=imagens[indice], className='diario-zoom-img', draggable='false', key=imagens[indice]),
            className='diario-zoom-container', key=imagens[indice],
            style={'flex': '1', 'minHeight': '0', 'marginTop': '8px', 'borderRadius': '8px', 'overflow': 'hidden'},
        ),
    ], style={'display': 'flex', 'flexDirection': 'column', 'height': '100%'})
    return True, conteudo




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
    State('diario-checkin-agua', 'value'),
    State('diario-checkin-exercicio', 'value'),
    State('diario-checkin-alimentacao', 'value'),
    State('diario-checkin-hora-dormir', 'value'),
    State('diario-checkin-hora-acordar', 'value'),
    State('diario-checkin-autocuidado', 'value'),
    State('diario-checkin-meditacao', 'value'),
    State('diario-checkin-obs', 'value'),
    prevent_initial_call=True,
)
def salvar_checkin(n_clicks, data_str, sono, cafe, agua, exercicio, alimentacao, hora_dormir, hora_acordar,
                    autocuidado, meditacao, obs):
    if not n_clicks or not data_str:
        return no_update, no_update, no_update
    campos = {
        'sono_qualidade': sono, 'cafe_qualidade': cafe,
        'agua_qualidade': agua, 'exercicio_qualidade': exercicio, 'alimentacao_qualidade': alimentacao,
        'sono_hora_dormir': hora_dormir, 'sono_hora_acordar': hora_acordar,
        'autocuidado_minutos': autocuidado, 'meditacao_minutos': meditacao,
        'observacoes': obs,
    }
    ok = dt.salvar_checkin_dia(data_str, campos)
    if not ok:
        return _texto_com_icone('tabler:alert-triangle', 'Não foi possível salvar (tente novamente).'), no_update, no_update
    return (_texto_com_icone('tabler:check', 'Check-in salvo.'), _bloco_gauge_hawkins(date.fromisoformat(data_str)),
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
    return (_radar_roda_vida(ultimo, tema_atual or 'light'), _texto_com_icone('tabler:check', 'Registro salvo.'),
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
    Input('drawer-editar-pendencia', 'opened'),
    prevent_initial_call=True,
)
def sincronizar_fechamento_pendencia(opened):
    """Fecha o Drawer via `store-pendencia-editar` sempre que `opened`
    virar `False` — seja pelo X do próprio Drawer, clique no overlay, Esc,
    ou o "Descartar" (que já escreve `None` na store diretamente). Substitui
    `fechar_pendencia_editar` (botão "✕ Fechar"/clique no backdrop manuais,
    específicos do `_overlay()` antigo — o `dmc.Drawer` já sincroniza
    `opened` sozinho quando fechado por qualquer uma dessas vias, ver
    `_drawer_editor`).

    Sem checar `if opened: return no_update` aqui, reabrir a MESMA
    pendência (`store-pendencia-editar` já no mesmo valor) não dispararia
    `renderizar_drawer_editar_pendencia` de novo — mantém o comportamento
    de sempre: fechar sempre limpa a store, então reabrir É sempre uma
    transição real None→id_pos."""
    if opened:
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
    Output('drawer-editar-pendencia', 'opened'),
    Output('drawer-editar-pendencia-conteudo', 'children'),
    Input('store-pendencia-editar', 'data'),
)
def renderizar_drawer_editar_pendencia(id_pos):
    """Constrói o conteúdo do Drawer a partir do `id_pos` guardado em
    `store-pendencia-editar` — pode ser um ticket avulso OU um par de
    hedge composto "ticket_a-ticket_b" (ver `dt.listar_pendencias_
    historico`/`materializar_pendencia_como_rascunho`), cada um com uma
    forma diferente de virar o dict que `_bloco_posicao_completo` espera."""
    if not id_pos:
        return False, no_update
    posicoes = dt.listar_posicoes_abertas()
    if posicoes is None or posicoes.empty:
        return False, no_update

    par = _parse_index_par(id_pos)
    if par:
        pares = dt.detectar_pares_hedge(posicoes)
        par_match = next((p for p in pares if p['ticket_a'] == par[0] and p['ticket_b'] == par[1]), None)
        if par_match is None:
            return False, no_update
        pos = dt.montar_posicao_sintetica_par(par_match, posicoes)
    else:
        linha = posicoes[posicoes['id_posicao'] == id_pos]
        if linha.empty:
            return False, no_update
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
    botoes = []
    if mostrar_descartar:
        botoes.append(dmc.Button('Descartar (não é uma operação real)', id='btn-diario-descartar-pendencia',
                                  n_clicks=0, color='red', variant='outline'))
    conteudo = html.Div([
        html.Div(botoes, style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '4px'}) if botoes else None,
        html.Div(id='diario-descartar-pendencia-status', style={'fontSize': '12px', 'color': C['red'], 'marginBottom': '4px'}),
        html.Div(_bloco_posicao_completo(pos, imagens), style={'overflowY': 'auto'}),
    ])
    return True, conteudo


# ---------------------------------------------------------------------------
# Editar operação JÁ GRAVADA (botão ✎ do Histórico) — 2026-07-30
# ---------------------------------------------------------------------------
# Fluxo inteiro passa por 3 ids GLOBAIS (`store-trade-editar`,
# `drawer-editar-trade`, `store-diario-refresh`), nunca por
# `diario-historico-tabela`/`diario-hist-*` — ver o comentário de
# `store-diario-refresh` no layout pro motivo (callback que referencia id
# desmontado é rejeitada INTEIRA, em silêncio).

@app.callback(
    Output('store-trade-editar', 'data'),
    Output('drawer-detalhe-trade', 'opened', allow_duplicate=True),
    Input({'type': 'diario-btn-editar-trade', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def abrir_trade_editar(n_clicks_lista):
    """Botão ✎ — pode vir de dentro do `drawer-detalhe-trade` (Grade/
    Galeria/Dia todos abrem detalhe ali agora, ver Tasks #29/#30/#31) ou
    de dentro do próprio `_bloco_editar_trade`. Guarda o `id_trade` na
    store; quem monta o formulário é `renderizar_drawer_editar_trade`.

    BUG REAL (2026-07-31, achado pelo Jesué: "botão editar não funciona"):
    os dois Drawers (`drawer-detalhe-trade`/`drawer-editar-trade`) são
    `position='right'` com o MESMO `offset`/`size` — abrir o de edição
    SEM fechar o de detalhe deixava os dois exatamente sobrepostos, o de
    detalhe (montado depois na árvore) por cima, escondendo o de edição
    por completo. Clicar em "Editar" parecia não fazer nada. Fecha o
    detalhe explicitamente ao abrir a edição."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update, no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    return json.loads(prop_id)['index'], False


@app.callback(
    Output('store-trade-editar', 'data', allow_duplicate=True),
    Input('drawer-editar-trade', 'opened'),
    prevent_initial_call=True,
)
def sincronizar_fechamento_trade(opened):
    """Ver `sincronizar_fechamento_pendencia` pro mesmo raciocínio —
    substitui `fechar_trade_editar` (botão/backdrop manuais do `_overlay()`
    antigo) agora que o `dmc.Drawer` sincroniza `opened` sozinho."""
    if opened:
        return no_update
    return None


def _bloco_editar_trade(trade: pd.Series) -> html.Div:
    """Formulário do pop-up de edição. Mostra os fatos da execução como
    texto SÓ-LEITURA no cabeçalho (preço, resultado, saldo, data) e deixa
    editável apenas o que é leitura/opinião do trade — a mesma fronteira
    que `dt.CAMPOS_EDITAVEIS_TRADE` impõe do lado do motor.

    2026-08-06, pedido do Jesué ("preciso ter todos os campos de cadastro
    do trade, inclusive a inserção de prints... essa tela está no UX
    antigo"): reescrito pra usar os mesmos componentes Mantine do Novo
    Trade (`_construir_diario_novo_trade`) em vez do `dcc.Dropdown`/
    `dcc.Input` cru que sobrou daqui de antes do rollout TradeZella (2026-
    07-31 só trocou a MOLDURA do drawer, o conteúdo interno ficou pra
    trás — ver comentário de `_drawer_editor`) — e ganha os 2 blocos que
    faltavam pra paridade com o Novo Trade: Autoavaliação (reaproveita
    `_bloco_emocional`, que já sabia pré-popular via `valores=`, só nunca
    tinha sido chamado aqui) e Print (novo — `imagem_arquivo` entrou em
    `dt.CAMPOS_EDITAVEIS_TRADE`, é anotação do usuário, não fato de
    execução). Nível S/R Forte fica de fora de propósito — nem o Novo
    Trade tem controle pra esse campo hoje, adicionar um exclusivo aqui
    seria inconsistência nova, não paridade."""
    saldo_num, _resultado_label, principal, secundario = _resultado_trade(trade)
    cor_saldo = C['green'] if (saldo_num or 0) > 0 else (C['red'] if (saldo_num or 0) < 0 else C['text2'])
    pontos = trade.get('resultado_pontos')
    cabecalho = (f"{trade.get('data_op')} · {trade.get('ativo')} · "
                 f"{trade.get('tipo_operacao') or '—'}")
    if pd.notna(pontos):
        cabecalho += f" · {float(pontos):+.0f} pts"

    grade = lambda *filhos: html.Div(list(filhos), style={
        'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(150px, 1fr))',
        'gap': '14px 16px', 'alignItems': 'end',
    })

    est_atuais = _lista_segura(trade.get('estrategia_tags'))
    erro_atuais = _lista_segura(trade.get('tags_setup_erro'))

    imagem_atual = trade.get('imagem_arquivo')
    preview_inicial = (
        _preview_imagem_com_remover(f"/diario-imagens/{imagem_atual}", 'btn-remover-print-editar-trade')
        if imagem_atual and pd.notna(imagem_atual) else None
    )

    return html.Div([
        html.Div([
            html.Span(f"#{int(trade['id_trade'])}", style={
                'fontFamily': FONTS['mono'], 'fontSize': '16px', 'fontWeight': '700', 'marginRight': '10px'}),
            html.Span(cabecalho, style={'fontSize': '12px', 'color': C['text2']}),
        ], style={'marginBottom': '4px'}),
        html.Div([
            html.Span(principal, style={
                'fontFamily': FONTS['mono'], 'fontSize': '16px', 'fontWeight': '700', 'color': cor_saldo}),
        ] + ([html.Span(secundario, style={
            'fontFamily': FONTS['mono'], 'fontSize': '11px', 'color': C['text2'],
            'display': 'block', 'marginTop': '1px',
        })] if secundario else []), style={'marginBottom': '4px'}),
        html.Div('Execução (preço, resultado, saldo, data) vem do MT5/corretora e não é editável aqui.',
                 style={'fontSize': '10px', 'color': C['text2'], 'fontStyle': 'italic', 'marginBottom': '14px'}),

        _bloco_diario('Classificação', html.Div([
            dmc.MultiSelect(id='diario-edit-estrategia', label='Estratégia',
                             data=_opcoes_com_valores_atuais(_opcoes_estrategias(), est_atuais),
                             value=est_atuais, mb='12px'),
            grade(
                dmc.Select(id='diario-edit-janela', label='Janela', clearable=True,
                           data=_opcoes_janelas(ativo=trade.get('ativo')),
                           value=trade.get('janela_pregao') if pd.notna(trade.get('janela_pregao')) else None),
                dmc.Select(id='diario-edit-sala', label='Sala/Análise', clearable=True,
                           data=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
                           value=trade.get('sala_analise') if pd.notna(trade.get('sala_analise')) else None),
            ),
        ])),

        _bloco_diario('Nível de S/R referenciado (opcional)', html.Div([
            grade(
                dmc.NumberInput(id='diario-edit-nivel-sr', label='Preço', hideControls=True, decimalScale=2,
                                 value=(float(trade['nivel_sr_referencia'])
                                        if pd.notna(trade.get('nivel_sr_referencia')) else None)),
                dmc.TextInput(id='diario-edit-nivel-sr-tipo', label='Tipo', placeholder='ex: suporte',
                              value=trade.get('nivel_sr_tipo') if pd.notna(trade.get('nivel_sr_tipo')) else None),
            ),
        ])),

        _bloco_diario('Anotações', html.Div([
            dmc.Textarea(id='diario-edit-resumo', label='Resumo Operacional', autosize=True, minRows=3, mb='10px',
                         value=trade.get('resumo_operacional') if pd.notna(trade.get('resumo_operacional')) else ''),
            dmc.Textarea(id='diario-edit-obs', label='Obs (autocoaching)', autosize=True, minRows=2, mb='10px',
                         value=trade.get('obs') if pd.notna(trade.get('obs')) else ''),
            _campo_diario('Tags de Setup/Erro', html.Div(
                _campo_tag_widget('diario-edit-tags-erro', _opcoes_tags_setup_erro(), erro_atuais),
                id=_wrap_id_tag_widget('diario-edit-tags-erro'))),
        ])),

        _bloco_emocional(
            id_confianca='diario-edit-emo-confianca', id_humor='diario-edit-emo-humor',
            id_energia='diario-edit-emo-energia', id_presenca='diario-edit-emo-presenca',
            id_equilibrio='diario-edit-emo-equilibrio', id_disciplina='diario-edit-emo-disciplina',
            id_tags='diario-edit-tags-emocionais',
            id_sentimento='diario-edit-sentimento-mercado', id_label_sentimento='diario-edit-sentimento-label',
            valores={
                'confianca': trade.get('emo_confianca'), 'humor': trade.get('emo_humor'),
                'energia': trade.get('emo_energia'), 'presenca': trade.get('emo_presenca'),
                'equilibrio': trade.get('emo_equilibrio'), 'disciplina': trade.get('emo_disciplina'),
                'tags': trade.get('tags_emocionais'), 'sentimento': trade.get('sentimento_mercado'),
            },
        ),

        _bloco_diario('Print', html.Div([
            html.Div('Cole (Ctrl+V) um print ou envie um arquivo — substitui o atual, se houver:',
                     style={'fontSize': '11px', 'color': C['text2'], 'marginBottom': '8px'}),
            dcc.Upload(
                id='diario-edit-upload-imagem',
                children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '12px'}),
                style={
                    'background': C['bg'], 'border': f'1px dashed {C["border"]}', 'borderRadius': '8px',
                    'padding': '16px', 'textAlign': 'center', 'cursor': 'pointer', 'color': C['text2'],
                },
            ),
            html.Div(preview_inicial, id='diario-edit-preview-imagem', style={'marginTop': '10px'}),
        ])),
    ])


@app.callback(
    Output('drawer-editar-trade', 'opened'),
    Output('drawer-editar-trade-conteudo', 'children'),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
    Output('store-diario-edit-imagem-removida', 'data', allow_duplicate=True),
    Input('store-trade-editar', 'data'),
    prevent_initial_call=True,
)
def renderizar_drawer_editar_trade(id_trade):
    """2026-08-06 — SEMPRE limpa `store-diario-imagem-colada` ao abrir um
    trade pra editar. Achado real do Jesué: esse store é GLOBAL e nunca era
    limpo depois de consumido — colar UM print, em qualquer contexto,
    deixava o valor preso ali pra sempre; toda edição salva DAÍ EM DIANTE
    reanexava esse mesmo print (mesmo sem colar nada de novo, mesmo tendo
    colado só TEXTO numa Obs em outro momento — o listener global de paste
    só reage a paste de IMAGEM, então o texto colado não foi a causa; a
    causa foi um print colado em algum momento anterior que nunca tinha
    sido limpo). Ver também o clear em `salvar_trade_editado`/
    `salvar_novo_trade` — os dois pontos juntos fecham o vazamento."""
    if not id_trade:
        return False, no_update, no_update, no_update
    df = dt.listar_trades()
    linha = df[df['id_trade'] == int(id_trade)]
    if linha.empty:
        return False, no_update, no_update, no_update
    conteudo = html.Div([
        html.Div([
            dmc.Button('Salvar alterações', id='btn-diario-salvar-trade', n_clicks=0, color='green'),
            html.Span(id='diario-editar-trade-status',
                      style={'fontSize': '12px', 'color': C['red'], 'marginLeft': '10px'}),
        ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '14px'}),
        html.Div(_bloco_editar_trade(linha.iloc[0]),
                 style={'overflowY': 'auto', 'padding': '14px',
                        'background': C['bg'], 'borderRadius': '8px',
                        'border': f'1px solid {C["border"]}'}),
    ])
    return True, conteudo, None, False


@app.callback(
    Output('store-trade-editar', 'data', allow_duplicate=True),
    Output('diario-editar-trade-status', 'children'),
    Output('store-diario-refresh', 'data', allow_duplicate=True),
    Output('store-diario-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-diario-salvar-trade', 'n_clicks'),
    State('store-trade-editar', 'data'),
    State('diario-edit-estrategia', 'value'),
    State('diario-edit-janela', 'value'),
    State('diario-edit-sala', 'value'),
    State('diario-edit-nivel-sr', 'value'),
    State('diario-edit-nivel-sr-tipo', 'value'),
    State('diario-edit-resumo', 'value'),
    State('diario-edit-obs', 'value'),
    State('diario-edit-tags-erro', 'data'),
    State('diario-edit-emo-confianca', 'value'),
    State('diario-edit-emo-humor', 'value'),
    State('diario-edit-emo-energia', 'value'),
    State('diario-edit-emo-presenca', 'value'),
    State('diario-edit-emo-equilibrio', 'value'),
    State('diario-edit-emo-disciplina', 'value'),
    State('diario-edit-tags-emocionais', 'data'),
    State('diario-edit-sentimento-mercado', 'value'),
    State('diario-edit-upload-imagem', 'contents'),
    State('store-diario-imagem-colada', 'data'),
    State('store-diario-edit-imagem-removida', 'data'),
    State('store-diario-refresh', 'data'),
    prevent_initial_call=True,
)
def salvar_trade_editado(n_clicks, id_trade, estrategias, janela, sala, nivel_sr, nivel_sr_tipo,
                         resumo, obs, tags_erro,
                         emo_confianca, emo_humor, emo_energia, emo_presenca, emo_equilibrio, emo_disciplina,
                         tags_emocionais, sentimento_mercado, upload_contents, imagem_colada, imagem_removida,
                         refresh_atual):
    """Grava a edição, fecha o pop-up e pede o refresh da tabela.

    Guarda de `n_clicks` obrigatória (regra fixada em 2026-07-27): o botão é
    criado com `n_clicks=0` toda vez que o pop-up é montado, e essa inserção
    dispara a callback — sem a guarda, ABRIR o formulário já gravaria por
    cima do trade com o que estivesse na tela. Aqui seria pior que no caso
    original: os `State` de um pop-up recém-montado ainda podem estar None,
    então o "save fantasma" APAGARIA a classificação existente.

    2026-08-06, BUG REAL achado pelo Jesué: `store-diario-imagem-colada` é
    GLOBAL e, antes desta correção, NUNCA era limpo depois de consumido —
    um print colado em QUALQUER momento anterior (mesmo dias antes, mesmo
    numa sessão de Novo Trade) ficava preso ali e era reanexado em SILÊNCIO
    a todo trade editado depois disso, mesmo sem o usuário colar nada de
    novo. `renderizar_drawer_editar_trade` já limpa esse store ao ABRIR um
    trade; aqui, limpa de novo ao SALVAR — os dois pontos junto fecham o
    vazamento (abrir limpa o que sobrou de antes; salvar limpa o que acabou
    de ser consumido, pra não vazar pro PRÓXIMO trade editado)."""
    if not n_clicks or not id_trade:
        return no_update, no_update, no_update, no_update
    campos = {
        'estrategia_tags': estrategias or [],
        'janela_pregao': janela,
        'sala_analise': sala,
        'nivel_sr_referencia': _num(nivel_sr),
        'nivel_sr_tipo': (nivel_sr_tipo or '').strip() or None,
        'resumo_operacional': (resumo or '').strip() or None,
        'obs': (obs or '').strip() or None,
        'tags_setup_erro': tags_erro or [],
        'emo_confianca': emo_confianca, 'emo_humor': emo_humor, 'emo_energia': emo_energia,
        'emo_presenca': emo_presenca, 'emo_equilibrio': emo_equilibrio, 'emo_disciplina': emo_disciplina,
        'tags_emocionais': tags_emocionais or [], 'sentimento_mercado': sentimento_mercado,
    }
    # Print — só grava um arquivo NOVO se o usuário colou/subiu um NESTA
    # sessão de edição (garantido pelo clear-on-abrir acima, não mais só
    # "o store não está vazio"). Sem print novo, mas com o X de remover
    # clicado (`imagem_removida`), grava None por cima do que já existia —
    # sem nenhum dos dois, `imagem_arquivo` nem entra em `campos` e
    # `dt.atualizar_trade` preserva o que já estava (allowlist ignora
    # chaves ausentes, não sobrescreve com None sozinha).
    data_url = upload_contents or imagem_colada
    if data_url:
        try:
            imagem_bytes = base64.b64decode(data_url.split(',', 1)[1])
            df_atual = dt.listar_trades()
            linha_atual = df_atual[df_atual['id_trade'] == int(id_trade)]
            if not linha_atual.empty:
                trade_atual = linha_atual.iloc[0]
                campos['imagem_arquivo'] = dt.salvar_imagem(
                    imagem_bytes, int(id_trade), trade_atual['ativo'], trade_atual['data_op'])
        except Exception:
            pass  # print é anexo opcional — falha no upload não deve bloquear o resto da edição
    elif imagem_removida:
        campos['imagem_arquivo'] = None
    if not dt.atualizar_trade(int(id_trade), campos):
        return (no_update,
                _texto_com_icone('tabler:alert-triangle', 'Ocupado — não foi possível gravar. Tente de novo.'),
                no_update, no_update)
    _log_diario.info('trade #%s editado no Histórico — estratégias: %s',
                     id_trade, campos['estrategia_tags'])
    return None, '', (refresh_atual or 0) + 1, None


# ---------------------------------------------------------------------------
# Pop-up "Novo Trade" (rollout TradeZella, 2026-07-31) — "Novo Trade vira um
# botão no diário que abre em pop-up". Mesmo trio de callbacks de
# abrir/renderizar/fechar já usado pra pendência/trade editado acima, só que
# `store-novo-trade-aberto` é um bool simples (não precisa guardar QUAL
# trade — é sempre o mesmo formulário em branco).
# ---------------------------------------------------------------------------

@app.callback(
    Output('store-novo-trade-aberto', 'data'),
    Input('btn-abrir-novo-trade', 'n_clicks'),
    prevent_initial_call=True,
)
def abrir_novo_trade(n_clicks):
    if not n_clicks:
        return no_update
    return True


@app.callback(
    Output('store-novo-trade-aberto', 'data', allow_duplicate=True),
    Input('drawer-novo-trade', 'opened'),
    prevent_initial_call=True,
)
def sincronizar_fechamento_novo_trade(opened):
    """Ver `sincronizar_fechamento_pendencia` pro mesmo raciocínio —
    substitui `fechar_novo_trade` (botão/backdrop manuais do `_overlay()`
    antigo)."""
    if opened:
        return no_update
    return False


@app.callback(
    Output('drawer-novo-trade', 'opened'),
    Output('drawer-novo-trade-conteudo', 'children'),
    Input('store-novo-trade-aberto', 'data'),
)
def renderizar_drawer_novo_trade(aberto):
    if not aberto:
        return False, no_update
    return True, _construir_diario_novo_trade()


@app.callback(
    Output('diario-novo-trade-extras', 'style'),
    Input('diario-novo-trade-modo', 'value'),
)
def alternar_modo_novo_trade(modo):
    """Modo Rápido/Completo (item 1 da auditoria de UX, 2026-08-05) — só
    troca `display`, nunca desmonta (mesma disciplina de sempre: campos já
    preenchidos em Anotações/Emocional/etc. não se perdem ao alternar)."""
    return {} if modo == 'completo' else {'display': 'none'}


# ---------------------------------------------------------------------------
# Pop-up "Importar Extrato" (2026-08-05) — mesmo trio abrir/renderizar/
# fechar do Novo Trade acima, ver comentário lá pro raciocínio.
# ---------------------------------------------------------------------------

@app.callback(
    Output('store-importar-extrato-aberto', 'data'),
    Input('btn-abrir-importar-extrato', 'n_clicks'),
    prevent_initial_call=True,
)
def abrir_importar_extrato(n_clicks):
    if not n_clicks:
        return no_update
    return True


@app.callback(
    Output('store-importar-extrato-aberto', 'data', allow_duplicate=True),
    Input('drawer-importar-extrato', 'opened'),
    prevent_initial_call=True,
)
def sincronizar_fechamento_importar_extrato(opened):
    if opened:
        return no_update
    return False


@app.callback(
    Output('drawer-importar-extrato', 'opened'),
    Output('drawer-importar-extrato-conteudo', 'children'),
    Input('store-importar-extrato-aberto', 'data'),
)
def renderizar_drawer_importar_extrato(aberto):
    if not aberto:
        return False, no_update
    return True, _construir_diario_importar_extrato()


@app.callback(
    Output('store-importar-extrato-linhas', 'data'),
    Output('importar-extrato-status', 'children'),
    Output('importar-extrato-preview', 'children'),
    Output('btn-importar-extrato-confirmar', 'disabled'),
    Input('importar-extrato-upload', 'contents'),
    State('importar-extrato-upload', 'filename'),
    prevent_initial_call=True,
)
def processar_upload_extrato(contents, filename):
    if not contents or not filename:
        return no_update, no_update, no_update, no_update
    extensao = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    try:
        _cabecalho, data_url = contents.split(',', 1)
        conteudo_bytes = base64.b64decode(data_url)
        if extensao in ('htm', 'html', 'xlsx'):
            df = ctrader_importar_extrato.parsear_extrato(filename, conteudo_bytes)
            moeda, fonte, campo_saldo = 'USD', dt.FONTE_IMPORTADO_CTRADER, 'saldo_usd'
        elif extensao == 'csv':
            df = profit_importar_extrato.parsear_extrato_csv(conteudo_bytes)
            moeda, fonte, campo_saldo = 'BRL', dt.FONTE_IMPORTADO_PROFIT, 'saldo_reais'
        else:
            raise ValueError(f'Formato não suportado: .{extensao} (use .htm, .xlsx ou .csv)')
    except Exception as e:
        return [], _texto_com_icone('tabler:alert-triangle', f'Erro ao ler o arquivo: {e}'), None, True

    if df.empty:
        return [], _texto_com_icone('tabler:alert-triangle',
                                     'Nenhum trade reconhecido nesse arquivo.'), None, True

    linhas = []
    for _, row in df.iterrows():
        ts_entrada = row.get('datetime_entrada')
        tem_entrada = pd.notna(ts_entrada) if ts_entrada is not None else False
        ts_ref = ts_entrada if tem_entrada else row['datetime_saida']
        duplicado = dt.trade_ja_importado(
            row['ativo'], row['datetime_saida'], row['preco_entrada'], row['preco_saida'], row['lote'])
        janela, sessao = dt.resolver_janela_sessao(row['ativo'], ts_ref)
        linhas.append({
            'ativo': row['ativo'], 'tipo_operacao': row['tipo_operacao'],
            'datetime_entrada': ts_entrada.isoformat() if tem_entrada else None,
            'datetime_saida': row['datetime_saida'].isoformat(),
            'preco_entrada': float(row['preco_entrada']), 'preco_saida': float(row['preco_saida']),
            'lote': float(row['lote']), 'moeda': moeda, 'saldo_nativo': float(row[campo_saldo]),
            'pips': float(row['pips']) if 'pips' in row and pd.notna(row.get('pips')) else None,
            'janela': janela, 'sessao': sessao, 'fonte': fonte, 'duplicado': duplicado,
        })
    n_dup = sum(1 for l in linhas if l['duplicado'])
    aviso_dup = f' ({n_dup} já importados antes, desmarcados)' if n_dup else ''
    status = _texto_com_icone('tabler:check', f'{len(linhas)} trades encontrados no arquivo{aviso_dup}.')
    return linhas, status, _tabela_preview_importacao(linhas), False


@app.callback(
    Output('importar-extrato-resultado', 'children'),
    Output('store-diario-refresh', 'data', allow_duplicate=True),
    Input('btn-importar-extrato-confirmar', 'n_clicks'),
    State('store-importar-extrato-linhas', 'data'),
    State({'type': 'importar-linha-incluir', 'index': ALL}, 'checked'),
    State('store-diario-refresh', 'data'),
    prevent_initial_call=True,
)
def confirmar_importacao_extrato(n_clicks, linhas, marcados, refresh_atual):
    if not n_clicks or not linhas:
        return no_update, no_update
    selecionadas = [l for l, incluir in zip(linhas, marcados or []) if incluir]
    if not selecionadas:
        return _texto_com_icone('tabler:alert-triangle', 'Nenhum trade selecionado.'), no_update

    campos_lote = []
    for l in selecionadas:
        ts_saida = pd.Timestamp(l['datetime_saida'])
        ts_entrada = pd.Timestamp(l['datetime_entrada']) if l.get('datetime_entrada') else None
        ts_ref = ts_entrada if ts_entrada is not None else ts_saida
        if l['moeda'] == 'USD':
            saldo_usd, saldo_brl = l['saldo_nativo'], dt.converter_usd_para_reais(l['saldo_nativo'], ts_ref)
        else:
            saldo_usd, saldo_brl = None, l['saldo_nativo']
        resultado_pontos = (
            (l['preco_saida'] - l['preco_entrada']) if l['tipo_operacao'] == 'long'
            else (l['preco_entrada'] - l['preco_saida'])
        )
        campos_lote.append(dict(
            data_op=ts_ref.date().isoformat(), ativo=l['ativo'], tipo_operacao=l['tipo_operacao'],
            datetime_entrada=ts_entrada, datetime_saida=ts_saida,
            preco_entrada=l['preco_entrada'], preco_saida=l['preco_saida'],
            lote=l['lote'], tipo_saida='manual', resultado_pontos=round(resultado_pontos, 5),
            saldo_operacao_usd=saldo_usd, saldo_operacao_reais=saldo_brl,
            janela_pregao=l.get('janela'), fonte_registro=l['fonte'],
        ))
    ids = dt.importar_trades_lote(campos_lote)
    if not ids:
        return (_texto_com_icone('tabler:alert-triangle', 'Diário ocupado — tente novamente em instantes.'),
                no_update)
    return (_texto_com_icone('tabler:check', f'{len(ids)} trade(s) importado(s) (#{ids[0]}–#{ids[-1]}). '
                              'Veja e classifique no Histórico.'),
            (refresh_atual or 0) + 1)


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
        return no_update, _texto_com_icone('tabler:alert-triangle', f'Erro ao detectar: {str(e)[:100]}')
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
    Input('segmented-posicao-ativo', 'value'),
)
def selecionar_subaba_posicao(valor):
    """2026-08-02: passthrough trivial — o `dmc.SegmentedControl` já
    resolve seleção/estilo sozinho (substituiu o par de `html.Button` +
    `atualizar_estilo_subaba_posicao`, que manipulava `style` na mão).
    `store-posicao-ativo-subaba` continua existindo só porque
    `atualizar_em_andamento` já lê dali — sem motivo pra também reescrever
    aquele callback agora."""
    return valor



@app.callback(
    Output({'type': 'ea-status', 'index': dash.MATCH}, 'children', allow_duplicate=True),
    Output({'type': 'ea-nota-ultimo-salvo', 'index': dash.MATCH}, 'data'),
    Input({'type': 'ea-nota', 'index': dash.MATCH}, 'n_blur'),
    State({'type': 'ea-nota', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-nota', 'index': dash.MATCH}, 'id'),
    State({'type': 'ea-nota-ultimo-salvo', 'index': dash.MATCH}, 'data'),
    prevent_initial_call=True,
)
def salvar_nota_em_andamento(_n_blur, nota, id_componente, ultimo_salvo):
    """Salva a nota (append no log cronológico) sozinha ao sair do campo
    (`n_blur`) — sem botão "Salvar" (feedback 2026-07-20).

    BUG REAL reportado pelo Jesué (2026-08-03): "esse campo de anotação
    apaga quando eu mudo de janela (Alt+Tab)". A versão anterior desta
    callback limpava a caixa (`value=''`) depois de cada save, pra nunca
    reenviar a MESMA nota como uma entrada nova se o usuário só clicasse
    pra fora e voltasse sem editar. O problema: o navegador dispara
    `n_blur` também quando a JANELA INTEIRA perde o foco do SO (Alt+Tab pra
    outro programa) — não só quando o usuário clica em outro campo DENTRO
    da página. Isso limpava o texto no meio da escrita, mesmo já tendo sido
    salvo no log — pro usuário parecia (e na prática se comportava como)
    apagar o que ele tinha acabado de escrever.

    Fix: a caixa NUNCA mais é limpa sozinha — o texto permanece visível
    depois de Alt+Tab, então continuar escrevendo é só continuar de onde
    parou. O dcc.Store `ea-nota-ultimo-salvo` substitui a limpeza como
    forma de evitar duplicata: só grava uma NOVA entrada no log se o texto
    mudou desde o último save bem-sucedido (evita reenviar a mesma nota
    inalterada a cada Alt+Tab de ida e volta)."""
    if not nota or not nota.strip() or nota == ultimo_salvo:
        return no_update, no_update
    ok = dt.atualizar_rascunho(_ticket_alvo(id_componente['index']), nota_texto=nota)
    if not ok:
        return _texto_com_icone('tabler:alert-triangle', 'Ocupado — tente salvar a nota de novo.'), no_update
    return _texto_com_icone('tabler:check', 'Nota salva.'), nota


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
    Input({'type': 'ea-emo-presenca', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-emo-equilibrio', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-emo-disciplina', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-tags-emocionais', 'index': dash.MATCH}, 'data'),
    Input({'type': 'ea-sentimento-mercado', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-resumo', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-estrategia', 'index': dash.MATCH}, 'id'),
    prevent_initial_call=True,
)
def auto_salvar_campos_em_andamento(estrategia, sala, alvo, stop, nivel_sr, _resumo_blur,
                                     emo_confianca, emo_humor, emo_energia, emo_presenca,
                                     emo_equilibrio, emo_disciplina,
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
    if emo_presenca is not None:
        campos['emo_presenca'] = emo_presenca
    if emo_equilibrio is not None:
        campos['emo_equilibrio'] = emo_equilibrio
    if emo_disciplina is not None:
        campos['emo_disciplina'] = emo_disciplina
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
    return _texto_com_icone('tabler:check', 'Salvo automaticamente.') if ok else _texto_com_icone('tabler:alert-triangle', 'Ocupado — tente de novo.')


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
    Output({'type': 'ea-checklist-estrategia-conteudo', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea-checklist-estrategia-marcados', 'index': dash.MATCH}, 'data'),
    Input({'type': 'ea-estrategia', 'index': dash.MATCH}, 'value'),
    prevent_initial_call=True,
)
def aplicar_checklist_estrategia_posicao(estrategia_tags):
    id_pos = callback_context.triggered_id['index']
    conteudo = _conteudo_checklist_estrategia(
        estrategia_tags, {'type': 'ea-checklist-estrategia-itens', 'index': id_pos})
    return conteudo, []


@app.callback(
    Output({'type': 'ea-risco-info', 'index': dash.MATCH}, 'children'),
    Output({'type': 'ea-alvo', 'index': dash.MATCH}, 'value'),
    Output({'type': 'ea-stop', 'index': dash.MATCH}, 'value'),
    Input({'type': 'ea-gerenciamento-risco', 'index': dash.MATCH}, 'value'),
    prevent_initial_call=True,
)
def aplicar_gerenciamento_risco_selecionado_posicao(id_gerenciamento):
    """Gerenciamento de Risco (corrigido 2026-08-03) — o ativo da posição
    já pré-carrega o padrão no render (`_bloco_posicao_form`); esta
    callback só reage quando o usuário TROCA o seletor pra outro
    gerenciamento cadastrado."""
    resumo, alvo, stop = _conteudo_risco_info(dt.obter_gerenciamento_risco(id_gerenciamento))
    return resumo, (alvo if alvo is not None else no_update), (stop if stop is not None else no_update)


@app.callback(
    Output({'type': 'ea-checklist-estrategia-marcados', 'index': dash.MATCH}, 'data', allow_duplicate=True),
    Input({'type': 'ea-checklist-estrategia-itens', 'index': dash.MATCH}, 'value'),
    prevent_initial_call=True,
)
def espelhar_checklist_estrategia_posicao(marcados):
    """Ver `espelhar_checklist_estrategia_novo_trade` pro mesmo raciocínio
    (bridge sempre-montado pro checklist condicional)."""
    return marcados or []


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
        return preview, _texto_com_icone('tabler:alert-triangle', 'Imagem inválida — não foi possível anexar.')
    # Par de hedge (ver _ticket_alvo): a galeria/imagem vive na perna de
    # ENTRADA, mesma decisão de onde as notas/tags preliminares ficam.
    ok = dt.atualizar_rascunho(_ticket_alvo(id_componente['index']), imagem_bytes=imagem_bytes)
    return preview, (_texto_com_icone('tabler:check', 'Print anexado automaticamente.') if ok
                      else _texto_com_icone('tabler:alert-triangle', 'Ocupado — tente colar/enviar de novo.'))


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
        return no_update, no_update, _texto_com_icone('tabler:alert-triangle', 'Não foi possível remover o print — tente de novo.')

    df = dt.listar_posicoes_abertas()
    linha = df[df['id_posicao'] == _ticket_alvo(id_pos)] if df is not None and not df.empty else None
    imagens = _lista_segura(linha.iloc[0].get('imagens_em_andamento')) if linha is not None and not linha.empty else []
    return (_thumbs_galeria_live(id_pos, imagens),
            _conteudo_imagem_principal(id_pos, imagens),
            _texto_com_icone('tabler:check', 'Print removido.'))


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
    State({'type': 'ea-emo-presenca', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-emo-equilibrio', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-emo-disciplina', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-tags-emocionais', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea-sentimento-mercado', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-tags-erro', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea-imagem-principal', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea-upload-imagem', 'index': dash.MATCH}, 'contents'),
    State('store-diario-imagem-colada', 'data'),
    State('store-diario-posicao-ativa-print', 'data'),
    State({'type': 'ea-preco-saida-correcao', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-datahora-saida-correcao', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-btn-finalizar', 'index': dash.MATCH}, 'id'),
    State('store-pendencia-editar', 'data'),
    State('store-diario-refresh', 'data'),
    State({'type': 'ea-checklist-estrategia-marcados', 'index': dash.MATCH}, 'data'),
    State({'type': 'ea-gerenciamento-risco', 'index': dash.MATCH}, 'value'),
    State({'type': 'ea-nota-ultimo-salvo', 'index': dash.MATCH}, 'data'),
    prevent_initial_call=True,
)
def finalizar_em_andamento(n_clicks, nota, estrategia, sala, alvo, stop, nivel_sr, resumo,
                            emo_confianca, emo_humor, emo_energia, emo_presenca,
                            emo_equilibrio, emo_disciplina,
                            tags_emocionais, sentimento_mercado, tags_erro, imagem_principal,
                            upload_contents, imagem_colada, posicao_ativa,
                            preco_saida_correcao, datahora_saida_correcao, id_componente,
                            pendencia_aberta, refresh_atual, checklist_estrategia_marcados,
                            id_gerenciamento_risco, nota_ultimo_salvo):
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
    if emo_presenca is not None:
        campos_rascunho['emo_presenca'] = emo_presenca
    if emo_equilibrio is not None:
        campos_rascunho['emo_equilibrio'] = emo_equilibrio
    if emo_disciplina is not None:
        campos_rascunho['emo_disciplina'] = emo_disciplina
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

    # 2026-08-03: a caixa de nota não é mais limpa sozinha (ver
    # `salvar_nota_em_andamento`), então `nota` normalmente já É o mesmo
    # texto que o auto-save de blur acabou de gravar — sem este `!=
    # nota_ultimo_salvo`, Finalizar duplicaria a MESMA nota como uma
    # segunda entrada no log toda vez que o campo tivesse algo digitado.
    nota_para_gravar = nota if (nota and nota.strip() and nota != nota_ultimo_salvo) else None
    if nota_para_gravar or campos_rascunho or imagem_bytes is not None:
        if not dt.atualizar_rascunho(ticket_alvo, nota_texto=nota_para_gravar,
                                      campos=campos_rascunho or None, imagem_bytes=imagem_bytes):
            return responder(_texto_com_icone('tabler:alert-triangle',
                             'Arquivo ocupado — clique de novo em instantes (nada foi perdido).'))

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
    campos_classificacao.update(
        _snapshot_playbook_trade(estrategia, alvo, stop, checklist_estrategia_marcados, id_gerenciamento_risco))

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
                return responder(_texto_com_icone('tabler:alert-triangle', 'Data/Hora de Saída inválida — use o formato AAAA-MM-DD HH:MM.'))
            if not dt.corrigir_saida_rascunho(id_pos, preco_corrigido, datahora_corrigida):
                return responder(_texto_com_icone('tabler:alert-triangle', 'Arquivo ocupado — clique de novo em instantes.'))
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
        return responder(_texto_com_icone('tabler:alert-triangle',
                         'Não foi possível finalizar — talvez já tenha sido gravada antes. '
                         'Confira a lista do Histórico.'), fechar, (refresh_atual or 0) + 1)

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
    return responder(_texto_com_icone('tabler:check', f'Finalizado como operação #{id_trade} no Diário.'),
                     fechar_popup, (refresh_atual or 0) + 1)


if __name__ == '__main__':
    gp.iniciar_monitor_processo_pai(PORTA_PAI)
    print("=" * 60)
    print("  Diário de Trade — processo clássico (porta 8053)")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=PORTA_PROPRIA, threaded=True)
