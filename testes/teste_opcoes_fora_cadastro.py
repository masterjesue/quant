"""
Trava a proteção mais importante do pop-up "Editar" do Histórico
(`_opcoes_com_valores_atuais`, nos 2 skins).

O risco que ela cobre: um `dcc.Dropdown` cujo `value` não consta das
`options` não desenha aquele chip. O usuário abriria o pop-up, veria o campo
vazio, clicaria "Salvar alterações" — e o `State` devolvido pelo Dash
APAGARIA a classificação que já existia. Não é hipótese: 140 dos 225 trades
do Diário carregam slugs herdados da importação do XLSM
(`suporte_resistencia`, `inversao_polaridade`, `pullback_mme21`…) que nunca
existiram no cadastro de estratégias, mais a tag emocional `ganância`.

Além da função pura, confere contra o DADO REAL que nenhum valor gravado
ficaria de fora das options — é o que garante que a reclassificação em
massa não vai comer tag de ninguém.

Roda direto: `python testes/teste_opcoes_fora_cadastro.py`. Sai != 0 se falhar.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import diario_trade as dt  # noqa: E402
import diario_app_classico as dac  # noqa: E402
import diario_app_98 as d98  # noqa: E402

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


def lista(v):
    if v is None or isinstance(v, str):
        return []
    return list(v) if hasattr(v, "__iter__") else []


for nome_skin, fn in (('clássico', dac._opcoes_com_valores_atuais),
                      ("'98", d98._opcoes_com_valores_atuais_98)):
    print(f"\n=== função pura — skin {nome_skin} ===")
    opcoes = [{'label': 'Bounce em Suporte', 'value': 'bounce_em_suporte'},
              {'label': 'Canal', 'value': 'canal'}]

    r = fn(opcoes, ['bounce_em_suporte'])
    checar([o['value'] for o in r] == ['bounce_em_suporte', 'canal'],
           'valor já cadastrado não duplica a option')

    r = fn(opcoes, ['suporte_resistencia'])
    checar([o['value'] for o in r] == ['bounce_em_suporte', 'canal', 'suporte_resistencia'],
           'valor órfão é acrescentado às options')
    checar(r[-1]['label'] == 'Suporte Resistencia (fora do cadastro)',
           'órfão vem rotulado como "(fora do cadastro)"')

    r = fn(opcoes, ['orfa', 'orfa', 'canal'])
    checar([o['value'] for o in r].count('orfa') == 1, 'órfão repetido entra uma vez só')

    checar(fn(opcoes, []) == opcoes, 'sem valores atuais, devolve as options originais')
    checar(fn([], ['x'])[0]['value'] == 'x', 'funciona com cadastro vazio')
    # A função não pode mutar a lista que recebeu — ela vem de
    # `_opcoes_estrategias()`, recriada a cada render, mas um efeito colateral
    # aqui vazaria pro próximo campo do mesmo formulário.
    base = [{'label': 'A', 'value': 'a'}]
    fn(base, ['z'])
    checar(len(base) == 1, 'não muta a lista de options recebida')

print("\n=== contra o dado REAL do Diário ===")
trades = dt.listar_trades()
print(f"  {len(trades)} trades")

casos = (
    ('estrategia_tags', dac._opcoes_estrategias, dac._opcoes_com_valores_atuais),
    ('tags_setup_erro', dac._opcoes_tags_setup_erro, dac._opcoes_com_valores_atuais),
    ('tags_emocionais', dac._opcoes_tags_emocionais, dac._opcoes_com_valores_atuais),
)
for coluna, opcoes_fn, merge_fn in casos:
    opcoes_base = opcoes_fn()
    perdidos = []
    for _, trade in trades.iterrows():
        atuais = lista(trade.get(coluna))
        if not atuais:
            continue
        disponiveis = {o['value'] for o in merge_fn(opcoes_base, atuais)}
        perdidos += [v for v in atuais if v not in disponiveis]
    checar(not perdidos,
           f'{coluna}: nenhum valor gravado ficaria fora das options '
           f'({len(set(perdidos))} perdidos)' if perdidos else
           f'{coluna}: nenhum valor gravado ficaria fora das options')

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
