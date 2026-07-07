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
    'yellow': '#FFFF00',
}

FONTS = {
    'display': '"Pixelated MS Sans Serif", Arial, sans-serif',
    'mono':    'Consolas, "Lucida Console", "Courier New", monospace',
    'body':    'Arial, Helvetica, sans-serif',
}

_BEVEL_RAISED = (
    f"inset -1px -1px {C['shadow_dk']}, "
    f"inset 1px 1px {C['hilight']}, "
    f"inset -2px -2px {C['shadow']}, "
    f"inset 2px 2px {C['light']}"
)
_BEVEL_SUNKEN = (
    f"inset -1px -1px {C['hilight']}, "
    f"inset 1px 1px {C['shadow_dk']}, "
    f"inset -2px -2px {C['light']}, "
    f"inset 2px 2px {C['shadow']}"
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


def win98_titlebar(texto: str, ativo: bool = True):
    """Barra de título de janela — gradiente navy + botões _/□/×."""
    grad = (f"linear-gradient(90deg, {C['titlebar']}, {C['titlebar_to']})" if ativo
            else f"linear-gradient(90deg, {C['titlebar_inactive']}, {C['titlebar_inactive_to']})")

    def _botao_controle(glifo):
        return html.Div(glifo, style={
            **win98_raised(padding='0'),
            'minWidth': '16px', 'minHeight': '14px', 'lineHeight': '14px',
            'fontSize': '9px', 'textAlign': 'center', 'color': C['text'],
        })

    return html.Div([
        html.Span(texto, style={
            'color': C['titletext'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'fontSize': '11px',
        }),
        html.Div([_botao_controle('_'), _botao_controle('□'), _botao_controle('×')],
                 style={'display': 'flex', 'gap': '2px'}),
    ], style={
        'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
        'background': grad, 'padding': '3px 4px 3px 6px',
    })


def win98_window(titulo: str, children, id=None, estilo_extra=None):
    """'Janela' completa — title bar navy + corpo raised. Envolve uma seção
    inteira (Market View, Price Action, Big Players), não um card individual
    (ver win98_groupbox para isso)."""
    corpo = html.Div([
        win98_titlebar(titulo),
        html.Div(children, style={'padding': '14px 10px 10px', 'background': C['panel']}),
    ], style={**win98_raised(padding='0'), **(estilo_extra or {})})
    if id is not None:
        corpo.id = id
    return corpo


def win98_groupbox(titulo: str, children, cor_led=None, estilo_extra=None):
    """Caixa de grupo clássica — moldura sunken com rótulo 'entalhado' no
    topo (efeito fieldset/legend). O LED colorido substitui o antigo
    borderLeft colorido dos cards."""
    led = html.Span(style={
        'display': 'inline-block', 'width': '8px', 'height': '8px',
        'background': cor_led or C['shadow'], 'marginRight': '6px',
        'border': f"1px solid {C['shadow_dk']}",
    }) if cor_led else None
    rotulo = html.Div([led, html.Span(titulo)], style={
        'position': 'absolute', 'top': '-8px', 'left': '10px',
        'background': C['panel'], 'padding': '0 4px',
        'fontSize': '10px', 'fontFamily': FONTS['display'], 'color': C['text'],
        'display': 'flex', 'alignItems': 'center',
        'textTransform': 'uppercase', 'letterSpacing': '0.04em',
    }) if titulo else None
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
