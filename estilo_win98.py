# =============================================================================
# estilo_win98.py — Tokens e "chrome" de janela no visual Windows 98
# =============================================================================
# Paleta "Windows Standard" (cinza + navy, a mais reconhecível) e a técnica
# de bevel 3D via box-shadow em 4 camadas, ambas conferidas contra a
# biblioteca de referência 98.css (github.com/jdan/98.css) e o arquivo de
# tema oficial do Windows 98. Mantém os mesmos nomes de chave de C/FONTS que
# o dashboard.py já usava no tema anterior, para que os call sites existentes
# continuem funcionando trocando só os valores.
# =============================================================================

from dash import html

C = {
    'desktop': '#008080',   # teal do Desktop — só o fundo da página usa isso
    'panel':   '#C0C0C0',   # ButtonFace — cinza base de toda a UI
    'bg':      '#008080',   # alias legado, mesmo valor de 'desktop'
    'window':  '#FFFFFF',   # Window — inputs, área de gráfico, listas

    'shadow':    '#808080', # ButtonShadow
    'shadow_dk': '#000000', # ButtonDkShadow
    'light':     '#DFDFDF', # ButtonLight
    'hilight':   '#FFFFFF', # ButtonHilight
    'border':    '#808080', # alias legado (divisórias/grid finas) — mesmo valor de 'shadow'

    'text':  '#000000',     # WindowText
    'text2': '#404040',     # cinza escuro secundário

    'titlebar':             '#000080',
    'titlebar_to':           '#1084D0',
    'titlebar_inactive':     '#808080',
    'titlebar_inactive_to':  '#B5B5B5',
    'titletext':             '#FFFFFF',

    'tooltip':      '#FFFFE1',  # InfoWindow
    'tooltip_text': '#000000',

    # semânticos de mercado — paleta VGA 16 cores
    'gold':   '#000080',    # accent (era dourado) — agora navy
    'green':  '#008000',
    'red':    '#FF0000',
    'blue':   '#000080',
    'yellow': '#FFFF00',    # só pra FORMA (arco do gauge, fundo de badge) —
                            # como TEXTO em cima de branco/cinza claro fica
                            # quase ilegível (luminância ~226, quase igual
                            # ao branco). Ver 'amber' pra isso.
    'amber':  '#946F00',    # "amarelo" legível como texto/traço fino —
                            # mesma família de cor, contraste de verdade em
                            # cima de branco (áreas sunken) e do painel
                            # cinza (#C0C0C0).
}

# Tahoma (não a MS Sans Serif bitmap) — é a fonte de UI que o próprio
# Windows passou a usar a partir do 98/2000 pra ficar legível em qualquer
# tamanho sem virar bloco de pixel; é também a que aparece na referência
# que o Jesué mandou (felixrieseberg/clippy). A bitmap pixelada ficou
# "fiel" demais — praticamente ilegível em telas de hoje.
FONTS = {
    'display': '"Tahoma", "Segoe UI", Verdana, Arial, sans-serif',
    'mono':    'Consolas, "Lucida Console", "Courier New", monospace',
    'body':    '"Tahoma", "Segoe UI", Verdana, Arial, sans-serif',
}

# Valores conferidos direto contra o CSS compilado de 98.css v0.1.20
# (github.com/jdan/98.css, MIT) — a mesma lib que o design do Clippy de
# referência (felixrieseberg/clippy) credita para o "visual Windows 98".
# Bevel de BOTÃO (raised) e o de JANELA (frame externo) não são o mesmo
# token ali — a janela troca as camadas de 1px/2px claras entre si, dando
# um destaque mais forte na quina externa. Copiar às cegas o bevel de botão
# pro frame da janela (erro do reskin anterior) achatava a moldura.
_BEVEL_RAISED = (       # botões, groupbox raised
    f"inset -1px -1px {C['shadow_dk']}, "
    f"inset 1px 1px {C['hilight']}, "
    f"inset -2px -2px {C['shadow']}, "
    f"inset 2px 2px {C['light']}"
)
_BEVEL_RAISED_WINDOW = (   # moldura externa da janela (.window do 98.css)
    f"inset -1px -1px {C['shadow_dk']}, "
    f"inset 1px 1px {C['light']}, "
    f"inset -2px -2px {C['shadow']}, "
    f"inset 2px 2px {C['hilight']}"
)
_BEVEL_SUNKEN = (       # inputs, área de gráfico, painel afundado
    f"inset -1px -1px {C['hilight']}, "
    f"inset 1px 1px {C['shadow']}, "
    f"inset -2px -2px {C['light']}, "
    f"inset 2px 2px {C['shadow_dk']}"
)


def win98_raised(bg=None, padding='2px', extra=None):
    """Moldura 3D 'solta' — painel ou botão não pressionado."""
    estilo = {
        'background': bg or C['panel'],
        'border': 'none',
        'borderRadius': '0',
        'boxShadow': _BEVEL_RAISED,
        'padding': padding,
    }
    if extra:
        estilo.update(extra)
    return estilo


def win98_sunken(bg=None, padding='2px', extra=None):
    """Moldura 3D 'afundada' — inputs, área de gráfico, campos de status."""
    estilo = {
        'background': bg or C['window'],
        'border': 'none',
        'borderRadius': '0',
        'boxShadow': _BEVEL_SUNKEN,
        'padding': padding,
    }
    if extra:
        estilo.update(extra)
    return estilo


def win98_button(pressed=False, extra=None):
    """Botão 3D — combine com className='win98-btn' (o estado :active de
    clique rápido do mouse vive em assets/style.css, não dá pra fazer isso
    em style inline). `pressed` é para estado persistente e explícito
    (botão de ativo selecionado, aba ativa)."""
    estilo = (win98_sunken(padding='2px 12px') if pressed
              else win98_raised(padding='2px 12px'))
    estilo.update({
        'minHeight': '23px', 'minWidth': '75px',
        'fontFamily': FONTS['display'], 'fontSize': '11px',
        'color': C['text'], 'cursor': 'pointer',
        'display': 'inline-flex', 'alignItems': 'center', 'justifyContent': 'center',
    })
    if pressed:
        estilo['background'] = C['light']
    if extra:
        estilo.update(extra)
    return estilo


def win98_field(extra=None):
    """Campo de texto — sunken branco, 21px de altura (spec 98.css)."""
    estilo = win98_sunken(bg=C['window'], padding='2px 6px')
    estilo.update({
        'height': '21px', 'fontFamily': FONTS['body'], 'fontSize': '13px',
        'color': C['text'], 'outline': 'none',
    })
    if extra:
        estilo.update(extra)
    return estilo


def _icone_visual(icone: str, tamanho: str = '16px', style_extra=None):
    """Ícone de janela/desktop — renderiza um `html.Img` de verdade se
    `icone` for um caminho (ex. '/assets/icons/my-computer.png', vindo de
    assets_98/icons/, extraído do projeto 1j01/98), ou um glifo/emoji como
    fallback simples caso contrário."""
    estilo = {'width': tamanho, 'height': tamanho, 'imageRendering': 'pixelated',
              'verticalAlign': 'middle', **(style_extra or {})}
    if icone.startswith('/') or icone.endswith(('.png', '.gif', '.ico')):
        return html.Img(src=icone, style=estilo)
    return html.Span(icone, style={'fontSize': tamanho, 'lineHeight': tamanho, **(style_extra or {})})


# Ícones dos botões de titlebar — SVGs embutidos idênticos aos de 98.css
# (mesmos data URI, extraídos do CSS compilado). O design anterior usava
# glifos de texto ("_", "□", "×") que não existem no Windows 98 de verdade
# — os botões reais são desenhados em pixel art, não tipografia.
_SVG_MINIMIZE = "data:image/svg+xml;charset=utf-8,%3Csvg width='6' height='2' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath fill='%23000' d='M0 0h6v2H0z'/%3E%3C/svg%3E"
_SVG_MAXIMIZE = "data:image/svg+xml;charset=utf-8,%3Csvg width='9' height='9' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath fill-rule='evenodd' clip-rule='evenodd' d='M9 0H0v9h9V0zM8 2H1v6h7V2z' fill='%23000'/%3E%3C/svg%3E"
_SVG_CLOSE = "data:image/svg+xml;charset=utf-8,%3Csvg width='8' height='7' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath fill-rule='evenodd' clip-rule='evenodd' d='M0 0h2v1h1v1h2V1h1V0h2v1H7v1H6v1H5v1h1v1h1v1h1v1H6V6H5V5H3v1H2v1H0V6h1V5h1V4h1V3H2V2H1V1H0V0z' fill='%23000'/%3E%3C/svg%3E"


def win98_titlebar(texto: str, icone: str = '🗔', minimizavel: bool = True,
                    maximizavel: bool = True, fechavel: bool = True):
    """Barra de título arrastável (className='win98-titlebar', lido por
    assets/window_manager.js). O estado ativo/inativo (gradiente navy vs.
    cinza) é decidido 100% no cliente via classe `.win98-window.inactive`
    — o Python não sabe em tempo real qual janela está em foco."""

    def _botao(svg, pos, action):
        return html.Button(type='button', className='win98-titlebar-btn',
                            **{'data-action': action}, style={
            **win98_raised(padding='0'),
            'minWidth': '16px', 'minHeight': '14px',
            'backgroundImage': f"url(\"{svg}\")",
            'backgroundPosition': pos, 'backgroundRepeat': 'no-repeat',
            'cursor': 'pointer',
        })

    botoes = [b for cond, b in [
        (minimizavel, _botao(_SVG_MINIMIZE, 'bottom 3px left 4px', 'minimize')),
        (maximizavel, _botao(_SVG_MAXIMIZE, 'top 2px left 3px', 'maximize')),
        (fechavel, _botao(_SVG_CLOSE, 'top 3px left 4px', 'close')),
    ] if cond]

    return html.Div([
        html.Span([
            html.Span(_icone_visual(icone, '14px'), className='win98-titlebar-icon', style={'marginRight': '5px'}),
            html.Span(texto),
        ], className='win98-titlebar-text', style={
            'color': C['titletext'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'fontSize': '11px', 'whiteSpace': 'nowrap',
            'overflow': 'hidden', 'textOverflow': 'ellipsis',
        }),
        html.Div(botoes, style={'display': 'flex', 'gap': '2px', 'flexShrink': '0'}),
    ], className='win98-titlebar', style={
        'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
        'background': f"linear-gradient(90deg, {C['titlebar']}, {C['titlebar_to']})",
        'padding': '3px 4px 3px 6px', 'cursor': 'move', 'flexShrink': '0',
    })


def win98_window(window_id: str, titulo: str, children, icone: str = '🗔',
                  width: str = '480px', min_width: str = '300px',
                  default_top_pct: float = 8, default_left_pct: float = 6,
                  start_closed: bool = False, menubar=None, statusbar=None,
                  estilo_extra=None, resizable: bool = False):
    """Janela de desktop genérica. `window_id` é ao mesmo tempo o `id` do
    DOM (usado pelo Dash pra popular o conteúdo interno via callback,
    inalterado) e o `data-window` que assets/window_manager.js usa como
    chave de taskbar/localStorage — o JS nunca tem id de janela hardcoded.
    Envolve uma seção inteira (Market View, Price Action, Big Players, o
    chat), não um card individual (ver win98_groupbox para isso)."""
    corpo = [win98_titlebar(titulo, icone)]
    if menubar:
        corpo.append(html.Div(menubar, className='win98-menubar', style={
            'display': 'flex', 'gap': '14px', 'padding': '2px 8px',
            'background': C['panel'], 'fontSize': '11px',
            'borderBottom': f"1px solid {C['shadow']}", 'flexShrink': '0',
        }))
    corpo.append(html.Div(children, className='win98-window-body'))
    if statusbar:
        corpo.append(html.Div(statusbar, className='win98-statusbar', style={
            'display': 'flex', 'gap': '2px', 'padding': '2px 3px 3px', 'flexShrink': '0',
        }))

    classes = 'win98-window' + (' closed' if start_closed else '')
    data_attrs = {
        'data-window': window_id,
        'data-default-top': str(default_top_pct),
        'data-default-left': str(default_left_pct),
        'data-start-closed': 'true' if start_closed else 'false',
    }
    if width.endswith('%'):
        # Largura percentual — assets_98/window_manager.js recalcula essa
        # mesma porcentagem em cima da largura "útil" (viewport menos a
        # coluna de ícones do desktop), pra caber lado a lado sem precisar
        # de zoom e sem tampar os ícones (ver ICON_RAIL no JS).
        data_attrs['data-default-width'] = width[:-1]
    if resizable:
        # Pega os handles de borda/canto que assets_98/window_manager.js
        # injeta via JS (não HTML — mais simples manter os 8 <div> num só
        # lugar do que duplicar em cada chamada de win98_window).
        data_attrs['data-resizable'] = 'true'
    return html.Div(corpo, id=window_id, className=classes, **data_attrs, style={
        # 3px de padding (igual ao .window real de 98.css) é o que dá
        # espaço pro bevel aparecer — com padding 0, a titlebar/body (que
        # cobrem 100% da área) pintam por cima da sombra 3D e ela some
        # (era o motivo da janela parecer "flat, sem profundidade").
        'background': C['panel'], 'border': 'none', 'borderRadius': '0',
        'boxShadow': _BEVEL_RAISED_WINDOW, 'padding': '3px',
        'position': 'fixed', 'width': width, 'minWidth': min_width,
        'maxWidth': 'calc(100vw - 24px)', 'maxHeight': 'calc(100vh - 54px)',
        'display': 'flex', 'flexDirection': 'column', 'overflow': 'hidden',
        **(estilo_extra or {}),
    })


def win98_desktop_icon(window_id: str, titulo: str, icone: str) -> html.Div:
    """Ícone de desktop — clique seleciona, duplo-clique abre (ver
    assets/window_manager.js). Genérico por `data-open-window`, nunca
    hardcoded pra uma janela específica."""
    return html.Div([
        _icone_visual(icone, '32px'),
        html.Span(titulo, style={
            'color': '#FFFFFF', 'fontSize': '11px', 'textAlign': 'center',
            'textShadow': '1px 1px 1px #000', 'fontFamily': FONTS['display'],
        }),
    ], className='win98-icon', tabIndex=0, **{'data-open-window': window_id}, style={
        'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center',
        'gap': '4px', 'width': '76px', 'padding': '4px 2px', 'cursor': 'pointer',
    })


def win98_start_menu_item(window_id: str, titulo: str, icone: str) -> html.Div:
    """Item do menu Start — mesmo `data-open-window` do ícone de desktop."""
    return html.Div([_icone_visual(icone, '16px'), html.Span(titulo)],
                     className='win98-start-item', **{'data-open-window': window_id},
                     style={'padding': '5px 8px', 'fontSize': '11px',
                            'display': 'flex', 'alignItems': 'center', 'gap': '8px'})


def win98_taskbar(tray_children=None) -> html.Div:
    """Barra de tarefas fixa — botão Start, botões de janela (populados por
    assets/window_manager.js via #taskbar-buttons) e bandeja com relógio."""
    return html.Div([
        html.Button([
            html.Img(src='/assets/icons/start-logo.png', style={
                'width': '16px', 'height': '16px', 'imageRendering': 'pixelated',
            }),
            html.Span('Start', style={'fontStyle': 'italic'}),
        ], id='start-btn', type='button', className='win98-start-btn',
                    style={
                        **win98_raised(padding='2px 8px 2px 4px'),
                        'display': 'flex', 'alignItems': 'center', 'gap': '6px',
                        'fontWeight': '700', 'fontSize': '13px', 'cursor': 'pointer',
                    }),
        html.Div(className='win98-taskbar-sep'),
        html.Div(id='taskbar-buttons', className='win98-taskbar-buttons'),
        html.Div(className='win98-taskbar-sep'),
        html.Div([
            *(tray_children or []),
            # Easter egg discreto: cada clique avança uma animação do
            # catálogo do Clippy (ver assets_98/clippy_agent.js). Glifo
            # simples de propósito — um desenho do Clippy nesse tamanho
            # (18px) não dava pra reconhecer.
            html.Button('▶', id='clippy-play-all-btn', type='button',
                        title='Próxima animação do Clippy',
                        style={
                            **win98_raised(padding='0'),
                            'width': '18px', 'height': '18px', 'fontSize': '9px',
                            'lineHeight': '18px', 'cursor': 'pointer',
                        }),
            html.Span('--:--:--', id='taskbar-clock', style={'fontFamily': FONTS['mono']}),
        ], className='win98-tray', style={
            **win98_sunken(bg=C['panel'], padding='2px 8px'),
            'display': 'flex', 'alignItems': 'center', 'gap': '6px', 'fontSize': '11px',
        }),
    ], className='win98-taskbar')


# Fonte única de verdade sobre quais janelas existem no desktop — usada
# tanto pros ícones do desktop quanto pros itens do menu Start, pra nunca
# haver drift entre "o que existe" e "o que aparece" nos dois lugares.
# Ícones em assets_98/icons/ — extraídos do projeto 1j01/98 (recriação
# open-source do Windows 98, github.com/1j01/98).
#
# 'Mentor IA' NÃO está mais aqui — o Clippy deixou de ser uma janela
# (sem titlebar/taskbar/minimizar): é o assistente flutuante sempre
# ancorado no canto, com seu próprio balão de fala (ver
# assets_98/clippy_agent.js e o bloco #clippy-balloon em dashboard_98.py).
# 'Quant Lab' e 'Market View' também viraram uma janela só (win-quantlab)
# — a divisão anterior deixava a Quant Lab maior que o necessário, com
# espaço vazio sobrando.
# `implementada=False` = só existe como registro (placeholder), sem
# `win98_window(...)` correspondente em app.layout ainda — não entra na
# fatia usada pelos ícones de desktop/Start (ver dashboard_98.py, usa
# `[j for j in JANELAS if j['implementada']]` em vez de um slice hardcoded).
JANELAS = [
    {'id': 'win-quantlab',     'titulo': 'Quant Lab',     'icone': '/assets/icons/my-computer.png', 'start_closed': False, 'implementada': True},
    {'id': 'win-price-action', 'titulo': 'Price Action',  'icone': '/assets/icons/find-file.png',   'start_closed': False, 'implementada': True},
    {'id': 'win-big-players',  'titulo': 'Big Players',   'icone': '/assets/icons/favorites.png',   'start_closed': False, 'implementada': True},
    {'id': 'win-grafico',      'titulo': 'Gráfico',       'icone': '/assets/icons/grafico.png',     'start_closed': False, 'implementada': True},
    {'id': 'win-diario',       'titulo': 'Diário',        'icone': '/assets/icons/notepad.png',     'start_closed': True,  'implementada': True},
    {'id': 'win-posicao',      'titulo': 'Posição',       'icone': '/assets/icons/network.png',     'start_closed': True,  'implementada': True},
    {'id': 'win-rastreador',   'titulo': 'Rastreador de Tendência', 'icone': '/assets/icons/rastreador.png', 'start_closed': True, 'implementada': True},
    {'id': 'win-backtesting',  'titulo': 'Backtesting',   'icone': '/assets/icons/calculator.png',  'start_closed': True,  'implementada': False},
    {'id': 'win-config',       'titulo': 'Configurações', 'icone': '/assets/icons/settings.png',    'start_closed': True,  'implementada': False},
]


def win98_groupbox(titulo: str, children, cor_led=None, estilo_extra=None):
    """Caixa de grupo clássica — moldura sunken com rótulo 'entalhado' no
    topo (efeito fieldset/legend). O LED colorido substitui o antigo
    borderLeft colorido dos cards."""
    led = html.Span(style={
        'display': 'inline-block', 'width': '8px', 'height': '8px',
        'background': cor_led or C['shadow'], 'marginRight': '6px' if titulo else '0',
        'border': f"1px solid {C['shadow_dk']}",
    }) if cor_led else None
    rotulo = html.Div([led, html.Span(titulo) if titulo else None], style={
        'position': 'absolute', 'top': '-8px', 'left': '10px',
        'background': C['panel'], 'padding': '0 4px',
        'fontSize': '10px', 'fontFamily': FONTS['display'], 'color': C['text'],
        'display': 'flex', 'alignItems': 'center',
        'textTransform': 'uppercase', 'letterSpacing': '0.04em',
    }) if (titulo or cor_led) else None
    return html.Div([
        rotulo,
        html.Div(children, style={'padding': '12px 10px 10px'}),
    ], style={
        'position': 'relative', 'marginTop': '10px',
        **win98_sunken(bg=C['panel'], padding='0'),
        **(estilo_extra or {}),
    })


def texto_sobre(cor_fundo: str) -> str:
    """Branco ou preto pro texto em cima de um fundo colorido (badge/botão),
    escolhido por luminância — substitui o antipadrão de usar C['bg'] como
    'cor de contraste' que existia no tema anterior."""
    h = cor_fundo.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    luminancia = 0.299 * r + 0.587 * g + 0.114 * b
    return '#000000' if luminancia > 150 else '#FFFFFF'
