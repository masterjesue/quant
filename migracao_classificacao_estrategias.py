"""
Reclassificação retroativa das estratégias do Diário — 2026-07-30.

Pedido do Jesué: "organize e valide os trades classificando-os dentro das
estratégias definidas. Crie também a estratégia 'Outros' para aquelas
operações sem registro de diário ou com estratégias diferentes."

## O problema que isto resolve

O Diário tinha DOIS vocabulários que nunca foram reconciliados: 11 slugs
herdados da importação do XLSM (`suporte_resistencia`, `inversao_polaridade`,
`pullback_mme21`, `medias_moveis`, `outro`…) marcando 140 trades, e um
cadastro de 16 estratégias com checklist do qual 6 nunca tinham sido usadas
uma única vez. Os painéis "Score de Confiabilidade" e "Por Estratégia"
discordavam porque liam vocabulários diferentes.

## Decisões do Jesué que guiam o mapeamento

1. **"Inversão de Polaridade" e "Reteste" NÃO são estratégias** — são
   CARACTERÍSTICAS de uma estratégia. "Identifique se foi uma operação de
   Suporte/Resistência, se foi bounce/rompimento, se foi operação de pivô."
   Logo, o eixo real é (nível) × (interação):
     - **bounce_em_X**      → o nível SEGUROU, opero a rejeição
     - **rompimento_de_X**  → o nível ROMPEU, opero a continuação (a entrada
       quase sempre é no reteste/throwback — que é exatamente onde mora a
       "inversão de polaridade")
   Isso explica por que `rompimento_de_suporte`/`_de_resistencia` estavam
   cadastradas com checklist de 7 itens e **zero** uso: os trades delas
   estavam todos rotulados como `inversao_polaridade`.

2. **OCO é figura gráfica** → categoria própria.
3. **Médias = ALINHAMENTO de médias**, explicitamente ≠ usar a MME21 isolada
   (`bounce_mme21`, que já existe) → categoria própria.
4. **Espadada = cruzamento MMA21 × MME9** — mesma família de Médias, mas
   categoria específica própria.
5. **Outros** = sem registro de diário, ou estratégia fora do vocabulário.

## De onde veio cada classificação

Do `resumo_operacional` escrito pelo próprio Jesué na época (a maioria), do
`estrategia_raw` (a classificação contemporânea dele) e — para os trades cujo
resumo é só "Veja o print" — da leitura dos prints. `estrategia_raw` NÃO é
tocado, então a rotulagem original continua auditável linha a linha.

Nada de execução é alterado: preço, resultado, saldo e data continuam vindo
do MT5 (ver `dt.CAMPOS_EDITAVEIS_TRADE`).

Uso:
    python migracao_classificacao_estrategias.py --simular   # não grava nada
    python migracao_classificacao_estrategias.py --aplicar
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diario_trade as dt  # noqa: E402
import persistencia  # noqa: E402

# ---------------------------------------------------------------------------
# Categorias novas (decisões 2, 3, 4 e 5 do Jesué)
# ---------------------------------------------------------------------------
NOVAS_ESTRATEGIAS = [
    ("OCO (Ombro-Cabeça-Ombro)", "oco",
     "Figura gráfica de reversão: ombro, cabeça, ombro e linha de pescoço. "
     "Entrada no rompimento ou no pullback da neckline.",
     ["Os 3 topos/fundos estão claros e a cabeça é o extremo",
      "A linha de pescoço está traçada e foi rompida",
      "O volume acompanha (menor no 2º ombro)",
      "Alvo projetado pela altura da cabeça até a neckline",
      "Contexto/correlação (WIN×WDO×DI) não contradiz a reversão"]),
    ("Alinhamento de Médias", "alinhamento_de_medias",
     "Operar a favor de um conjunto de médias móveis EMPILHADAS na mesma "
     "direção (não é a MME21 isolada — para isso ver Bounce MME21).",
     ["As médias estão empilhadas na ordem certa para a direção",
      "O preço está do lado correto do conjunto",
      "A entrada é num pullback numa das médias, não a mercado",
      "O mercado NÃO está lateral (a lição do #24: em lateral o preço ignora as médias)",
      "Stop protegido atrás da média mais lenta do conjunto"]),
    ("Espadada (MMA21 × MME9)", "espadada",
     "Cruzamento da MMA21 com a MME9. Família de médias, mas gatilho "
     "específico: entrada no candle seguinte à confirmação do cruzamento.",
     ["O cruzamento MMA21 × MME9 fechou de fato (não é intra-candle)",
      "Entrada na abertura do candle seguinte à confirmação",
      "Há tendência/impulso, não consolidação",
      "Volatilidade normal — não operar logo após notícia (lição do #73)",
      "Stop atrás do cruzamento"]),
    ("Outros", "outros",
     "Operação sem registro de diário, ou com estratégia fora do vocabulário "
     "atual (VWAP, padrão de candle isolado, cópia de call sem setup próprio). "
     "Existe para que o denominador das estatísticas seja honesto.",
     []),
]

# Já cadastrada porém inativa, e usada de verdade (#143 Fundo Duplo W).
REATIVAR = ["topo_fundo_duplo"]

# Slugs do vocabulário legado — depois da migração nenhum trade deve citá-los.
LEGADOS = ["suporte_resistencia", "inversao_polaridade", "pullback_mme21",
           "outro", "rompimento", "lta_ltb", "medias_moveis", "reteste",
           "ombro_cabeca_ombro", "abc"]

# ---------------------------------------------------------------------------
# Mapa auditável: id_trade -> (tags novas, motivo)
# ---------------------------------------------------------------------------
MAPA = {
    # --- bloco de abril em Replay/simulador (raw "Médias") -------------------
    1:   (["rompimento_de_resistencia"], "[Replay] gatilho foi o rompimento do topo anterior confirmado pelo candle 3; a obs diz médias DESALINHADAS, então não é Alinhamento"),
    2:   (["alinhamento_de_medias"], "[Replay] após novo rompimento de topo, esperou o preço chegar na MME9 para comprar"),
    3:   (["alinhamento_de_medias"], "[Replay] reentrada na Golden Line dentro do setup de médias — a própria obs marca overtrading"),
    4:   (["alinhamento_de_medias"], "[Replay] esperou o cruzamento MME9×MMA20 alinhar as médias e entrou no pullback da MME9"),
    5:   (["alinhamento_de_medias", "fibonacci"], "[Replay] médias alinhadas para compra + entrada no pullback no fibo 0%"),
    6:   (["alinhamento_de_medias"], "[Replay] rompimento limpo com toque na MME9, em alinhamento com as médias (obs)"),
    7:   (["alinhamento_de_medias"], "raw 'Médias'; entrada de confirmação de venda seguindo o morning call"),
    8:   (["outros"], "a entrada foi na VWAP (o pullback na MME9 tinha sido invalidado) — VWAP está fora do vocabulário"),
    9:   (["bounce_em_suporte"], "confirmação de um fundo triplo = suporte validado por 3 toques"),
    36:  (["bounce_em_suporte"], "linha de suporte forte já traçada antes, seguia válida"),
    37:  (["bounce_em_suporte"], "três linhas de suporte validadas; entrada no terceiro toque da primeira"),
    44:  (["bounce_em_suporte"], "resumo: entrada em suporte forte, escapou do violino"),
    52:  (["bounce_em_suporte"], "resumo: suporte validado 6x, operação a favor da tendência"),
    76:  (["rompimento_de_suporte"], "venda a favor da tendência de baixa numa linha de suporte — ⚠ AMBÍGUO: o resumo diz 'suporte forte' mas a operação é short; revisar no Editar"),

    10:  (["bounce_em_ltb"], 'resumo: "confirmação de LTB forte" — venda na LTB respeitada'),
    11:  (["rompimento_de_suporte"], "resumo: rompimento vendedor com candle de força + entrada no pullback da linha de suporte"),
    16:  (["outros"], 'raw "Kita" é o professor, não um setup; resumo descreve scalp seguindo morning call'),
    17:  (["rompimento_de_resistencia"], "inversão de polaridade em COMPRA = resistência rompida virou suporte"),
    18:  (["bounce_mme21"], 'raw "Entrada MME21"'),
    19:  (["bounce_mme21"], "resumo: reentrada na média de 21 exponencial"),
    20:  (["outros"], 'raw "Pullback VWAP" — VWAP não faz parte do vocabulário de estratégias'),
    21:  (["bounce_em_suporte"], "resumo: suporte forte dos dias anteriores não rompido, compra na região"),
    22:  (["bounce_em_resistencia"], "resumo: gap do dia serviu como resistência, venda no throwback"),
    24:  (["rompimento_de_suporte", "alinhamento_de_medias"], "pivô de baixa rompeu fundo forte; venda no reteste da linha (agora resistência), com médias alinhadas"),
    25:  (["bounce_em_resistencia"], "resumo: vendidos em resistência forte, mercado lateralizado"),
    26:  (["alinhamento_de_medias"], "resumo: médias alinhadas para venda, projeção de exaustão"),
    27:  (["alinhamento_de_medias"], "médias alinhadas + pullback na MME **9** — NÃO é a MME21 isolada"),
    28:  (["alinhamento_de_medias"], "médias alinhadas + pullback na média de **20** — NÃO é a MME21 isolada"),
    29:  (["espadada"], "resumo: setup da espadada, cruzamento de médias"),
    30:  (["rompimento_de_resistencia"], "resumo: pullback na resistência (mudança de polaridade), compra"),
    31:  (["bounce_mme21"], "resumo: pullback na MME21"),
    32:  (["trowback_de_pivot"], "rompimento de pivô sem esperar confirmação (rompimento falso) — família pivô; entrada foi no rompimento, não no throwback"),
    33:  (["alinhamento_de_medias"], "resumo: entrada de risco ENTRE médias, no pullback da MME9"),
    34:  (["bounce_em_suporte"], "compra num fundo relevante que servia de suporte; o suporte é que rompeu depois"),
    38:  (["bounce_em_suporte"], "resumo: suporte forte respeitado novamente, execução perfeita"),
    39:  (["bounce_mme21"], "resumo: correção na MME21 respeitada anteriormente"),
    40:  (["bounce_mme21"], "resumo: MME21 respeitada e sem toques por longo período"),
    41:  (["rompimento_de_suporte"], "mudança de polaridade de um suporte, a favor da tendência (venda)"),
    42:  (["bounce_em_resistencia"], "resumo: entrada em resistência forte"),
    43:  (["bounce_em_resistencia"], "resumo: entrada em resistência forte, escapou do violino"),
    45:  (["espadada"], "resumo: cruzamento de médias, entrada no candle seguinte à confirmação"),
    46:  (["oco"], "resumo: padrão de reversão ombro-cabeça-ombro, figura de livro"),
    47:  (["bounce_em_ltb"], "resumo: LTB forte e bem respeitada, stop protegido por topo anterior"),
    48:  (["bandas_de_bollinger"], "resumo: estreitamento das bandas, setup da boca do jacaré"),
    49:  (["bounce_mme21"], "resumo: pullback na MME21 coincidindo com retração de 50%"),
    50:  (["bounce_em_resistencia"], "resumo: entrada em resistência forte"),
    51:  (["bounce_em_suporte"], "resumo: terceiro toque do suporte, a favor da tendência"),
    53:  (["bounce_em_resistencia"], "resumo: terceiro toque de resistência forte de dias anteriores"),
    54:  (["bounce_em_suporte"], "raw Suporte/Resistência em compra; resumo trata do breakeven, entrada correta na região"),
    55:  (["bounce_em_suporte"], "resumo: terceiro toque de um suporte forte"),
    56:  (["bounce_em_suporte"], "resumo: quinto toque do suporte forte"),
    57:  (["rompimento_de_suporte"], "resumo: após rompimento com barra de força, pullback na mesma linha — mudança de polaridade"),
    59:  (["rompimento_de_resistencia", "bounce_mme21"], "compra a favor da tendência na linha que virou suporte + confluência com a MME21"),
    60:  (["bounce_mme21"], "resumo: segundo teste na MME21 respeitada há 90 minutos"),
    61:  (["bounce_em_resistencia"], "resumo: terceiro toque de resistência em range lateral"),
    62:  (["bounce_em_suporte"], "resumo: suporte forte testado duas vezes"),
    63:  (["bounce_mme21"], "resumo: pullback na MME21 para surfar a queda"),
    64:  (["bounce_em_resistencia"], "resumo: resistência testada 3 vezes"),
    65:  (["bounce_em_suporte"], "raw Suporte/Resistência em compra; trade de teste de gerenciamento"),
    66:  (["bounce_mme21"], "resumo: preço buscando a MME21 respeitada, entrada apoiada nela"),
    67:  (["bounce_mme21"], "resumo: MME21 respeitada várias vezes, LTA confirmando"),
    68:  (["rompimento_de_resistencia"], "resumo: trowback de uma resistência forte que foi rompida com barra de força"),
    69:  (["bounce_mme21"], "resumo: martelo tocando na MME21 e respeitando a LTA"),
    70:  (["outros"], "resumo não descreve setup — operação escolhida entre as do professor Igor"),
    71:  (["bounce_mme21"], 'raw "Pullback MME21" (resumo é só "Veja o print")'),
    72:  (["rompimento_de_resistencia"], "compra na linha de resistência forte que serviria como suporte, a favor da tendência"),
    73:  (["espadada"], "resumo: setup de cruzamento de médias após impulsão vendedora"),
    74:  (["rompimento_de_suporte"], "rompimento de caixote para baixo, entrada no pullback da linha rompida"),
    75:  (["bounce_mme21"], "resumo: apoio na MME21 que seguia sendo respeitada"),
    77:  (["rompimento_de_suporte"], "rompimento de LTA e de suporte nos 50% de fibo; entrada no teste da linha que viraria resistência"),
    78:  (["rompimento_de_resistencia"], "print: compra acima da consolidação rompida — resistência virou suporte"),
    79:  (["bounce_em_resistencia"], "resumo: resistência de topo anterior em confluência com LTB respeitada"),
    80:  (["bounce_em_suporte"], "resumo: suporte forte do dia confluindo com suporte de janeiro/2026"),
    82:  (["rompimento_de_suporte"], "resumo: após rompimento de suporte, entrada no teste da linha (bipolaridade)"),
    83:  (["trowback_de_pivot"], 'resumo nomeia "rompimento de pivot"; entrada no pullback da linha rompida'),
    85:  (["rompimento_de_resistencia"], "resumo: linha que virou suporte (mudança de polaridade) respeitada, tendência de alta"),
    86:  (["rompimento_de_resistencia"], "resumo: rompimento de resistência com barra forte, linha testada, a favor da tendência"),
    87:  (["bounce_mme21"], "resumo: segundo toque da MME21 dentro de canal de alta"),
    88:  (["rompimento_de_suporte", "oco"], "pullback do suporte que virou resistência, em confluência com OCO próximo da neckline"),
    89:  (["bounce_em_suporte"], "resumo: suporte forte com mais de 5 toques sem rompimento"),
    90:  (["rompimento_de_suporte", "triangulo"], "suporte rompido, entrada na mudança de polaridade + confluência com triângulo descendente"),
    91:  (["bounce_em_resistencia"], "resumo: terceira tentativa na mesma resistência, respeitada de novo"),
    92:  (["trowback_de_pivot"], "resumo: rompimento de pivot deixou linha de suporte (resistência rompida — bipolaridade)"),
    93:  (["trowback_de_pivot"], "resumo: movimento de pivot com segunda onda complexa, entrada no pullback da linha rompida"),
    94:  (["bounce_em_resistencia"], "resumo: resistência forte, terceiro toque"),
    95:  (["rompimento_de_suporte"], "resumo: suporte do dia anterior virou resistência na abertura, terceiro toque"),
    96:  (["rompimento_de_suporte"], "resumo: inversão de polaridade + MME9, venda a favor da tendência"),
    97:  (["bounce_em_ltb"], "resumo: LTB testada 3 vezes, entrada no quarto toque"),
    98:  (["bounce_em_ltb"], "resumo: LTB testada 6 vezes, continuação do movimento anterior"),
    99:  (["rompimento_de_ltb"], "resumo: rompimento da LTB forte e formação de pivot de alta na sequência"),
    100: (["rompimento_de_resistencia"], "resumo: correção até resistência que serviria de suporte (mudança de polaridade)"),
    101: (["rompimento_de_resistencia"], "resumo: resistência forte que virou suporte, entrada no pullback"),
    102: (["bounce_em_resistencia"], "resumo: bounce na linha da máxima/mínima do dia anterior — backtest próprio (75% na janela do almoço)"),
    103: (["bounce_mme21"], "resumo: terceiro toque na MME21 intocável por 20 barras"),
    104: (["triangulo"], "resumo: triângulo simétrico, entrada no fechamento da barra de rompimento"),
    105: (["bounce_em_suporte"], "resumo: entrada de risco pós-notícia em direção a linha de suporte forte mapeada desde as 10h"),
    106: (["canal", "fibonacci"], "resumo: canal de baixa dentro de retângulo; entrada na retração de fibo 61,8%"),
    107: (["rompimento_de_resistencia"], "resumo: pullback esperado em linha de resistência que pode virar suporte"),
    108: (["bounce_mme21"], "resumo: pullback na MME21, segundo toque"),
    109: (["trowback_de_pivot"], "resumo: rompimento de pivot com barra de corpo relevante, entrada no pullback da linha rompida"),
    110: (["trowback_de_pivot"], "resumo: novo rompimento de topo com barra forte, entrada no pullback da linha rompida"),
    111: (["bounce_em_resistencia"], "resumo: confluência de resistência forte de 3 toques com LTB"),
    112: (["bounce_em_suporte"], "resumo: terceiro toque de suporte forte dentro de canal de baixa"),
    113: (["rompimento_de_resistencia", "triangulo"], "triângulo ascendente, rompimento da resistência, entrada no pullback (mudança de polaridade)"),
    114: (["rompimento_de_suporte", "bounce_mme21"], "suporte rompido, correção na linha que vira resistência, com a MME21 na mesma região"),
    115: (["rompimento_de_resistencia"], "rompimento de consolidação, pullback na região de bola que era resistência do topo anterior"),
    116: (["bounce_mme21"], 'raw "Entrada MME21" (resumo é só "Veja o print")'),
    117: (["bounce_em_suporte"], 'raw Suporte/Resistência em compra (resumo é só "Veja o print")'),
    118: (["triangulo", "rompimento_de_suporte"], "triângulo simétrico de 2h25; entrada no pullback do suporte rompido dentro da figura — melhor operação do ano"),
    119: (["bounce_em_suporte"], 'raw Suporte/Resistência em compra (resumo é só "Veja o print")'),
    120: (["bounce_mme21"], 'raw "Entrada MME21" (resumo é só "Veja o print")'),
    121: (["bounce_em_resistencia"], "raw Suporte/Resistência em venda; obs: entrada de confirmação, operação tecnicamente impecável"),
    122: (["rompimento_de_suporte"], "resumo: rompimento de suporte forte do dia anterior, entrada a mercado após o rompimento"),
    123: (["rompimento_de_suporte"], "resumo: teste da região de suporte rompida que poderia virar resistência"),
    124: (["rompimento_de_suporte"], "resumo: suporte rompido com candle de força, entrada no pullback"),
    125: (["bounce_em_resistencia"], 'raw Suporte/Resistência em venda (resumo é só "Veja o print")'),
    126: (["bounce_em_suporte"], 'raw Suporte/Resistência em compra (resumo é só "Veja o print")'),
    127: (["rompimento_de_resistencia"], 'raw "Inversao de Polaridade" em compra (resumo é só "Veja o print")'),
    128: (["canal"], "print: compra na banda inferior de um canal de baixa bem desenhado"),
    129: (["canal"], 'raw "LTB/Canal" em venda (resumo é só "Veja o print")'),
    130: (["rompimento_de_resistencia"], 'raw "Inversao de Polaridade" em compra (resumo é só "Veja o print")'),
    131: (["bounce_em_suporte"], 'raw "Bounce SnR" em compra (resumo é só "Veja o print")'),
    132: (["bounce_em_resistencia"], "resumo: entrada em resistência forte, mercado lateralizado"),
    133: (["bounce_mme21"], "resumo: correção na MME21 respeitada, entrada no segundo toque"),
    134: (["bounce_em_resistencia"], "resumo: resistência antiga (5 dias), entrada na confirmação do terceiro toque"),
    135: (["outros"], 'resumo: "Estrela da Tarde" — padrão de candle isolado, fora do vocabulário de estratégias'),
    136: (["pullback_de_pivot"], 'resumo: "Pullback de Pivot"'),
    137: (["rompimento_de_resistencia"], "resumo: trowback na linha de resistência rompida"),
    138: (["bounce_em_ltb"], 'resumo: "LTB Forte que foi respeitada"'),
    139: (["rompimento_de_suporte"], "resumo: estudo barra-a-barra da bipolaridade na linha 5.231"),
    140: (["bounce_em_resistencia"], 'raw "Bounce SnR" em venda (resumo é só "Veja o print")'),
    141: (["outros"], "print anotado pelo próprio Jesué: martelo/dragonfly/doji dentro de retângulo — padrão de candle, não figura clássica"),
    142: (["bounce_em_suporte"], 'raw "Bounce SnR" em compra (resumo é só "Veja o print")'),
    143: (["rompimento_de_resistencia", "topo_fundo_duplo"], 'resumo: "Fundo Duplo W + Bipolaridade + Trowback"'),
    144: (["bounce_em_suporte"], 'raw "Bounce SnR" em compra (resumo é só "Veja o print")'),
    145: (["bounce_em_ltb"], 'raw "LTA\\LTB" em venda (resumo é só "Veja o print")'),
    146: (["rompimento_de_resistencia"], "print: resistência em 5.177,50 rompida, entrada no trowback acima dela"),
    147: (["rompimento_de_resistencia"], "resumo: resistência 5095 rompida com força e volume, trowback usando a linha como suporte"),
    154: (["bounce_em_suporte"], "resumo: terceiro toque em um suporte forte vindo do dia anterior"),
}


def como_lista(v):
    if v is None or isinstance(v, str):
        return []
    return [str(x) for x in v] if hasattr(v, "__iter__") else []


def garantir_cadastro(simular: bool) -> None:
    """Cria as 4 categorias novas e reativa `topo_fundo_duplo`. Idempotente."""
    existentes = dt.listar_estrategias(somente_ativas=False)
    por_slug = {str(r["slug"]): r for _, r in existentes.iterrows()}

    for nome, slug, descricao, checklist in NOVAS_ESTRATEGIAS:
        if slug in por_slug:
            linha = por_slug[slug]
            if not bool(linha["ativo"]):
                print(f"  reativando  {slug}")
                if not simular:
                    dt.editar_estrategia(int(linha["id"]), ativo=True)
            else:
                print(f"  já existe   {slug}")
            continue
        print(f"  CRIANDO     {slug:<24} {nome}")
        if not simular:
            novo_id = dt.criar_estrategia(nome, descricao, checklist)
            if novo_id is None:
                raise RuntimeError(f"não consegui criar a estratégia {slug} (trava ocupada)")
            # `criar_estrategia` deriva o slug do NOME ("OCO (Ombro-Cabeça-Ombro)"
            # viraria "oco_ombro_cabeca_ombro"). Força o slug canônico, que é o
            # que o MAPA e os dropdowns usam.
            dt.editar_estrategia(novo_id, slug=slug)

    for slug in REATIVAR:
        linha = por_slug.get(slug)
        if linha is None:
            continue
        if bool(linha["ativo"]):
            print(f"  já ativa    {slug}")
        else:
            print(f"  reativando  {slug}  (usada de verdade em #143)")
            if not simular:
                dt.editar_estrategia(int(linha["id"]), ativo=True)

    # Só depois de criar E reativar tudo é que dá pra conferir o resultado.
    if not simular:
        agora = set(dt.listar_estrategias()["slug"].astype(str))
        faltando = [s for _, s, _, _ in NOVAS_ESTRATEGIAS if s not in agora]
        faltando += [s for s in REATIVAR if s not in agora]
        if faltando:
            raise RuntimeError(f"cadastro não ficou consistente — ausentes/inativas: {faltando}")


def desativar_legados(simular: bool) -> None:
    """Tira do dropdown os slugs do vocabulário antigo — depois da migração
    nenhum trade os cita mais, então deixá-los ativos só reintroduziria a
    ambiguidade que esta migração existe para acabar."""
    existentes = dt.listar_estrategias(somente_ativas=False)
    for _, linha in existentes.iterrows():
        slug = str(linha["slug"])
        if slug in LEGADOS and bool(linha["ativo"]):
            print(f"  desativando {slug}")
            if not simular:
                dt.desativar_estrategia(int(linha["id"]))


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--simular", action="store_true")
    g.add_argument("--aplicar", action="store_true")
    args = p.parse_args()
    simular = args.simular

    print("=" * 78)
    print("  CADASTRO DE ESTRATÉGIAS")
    print("=" * 78)
    garantir_cadastro(simular)

    print()
    print("=" * 78)
    print("  TRADES")
    print("=" * 78)
    trades = dt.listar_trades()
    ids_existentes = set(int(i) for i in trades["id_trade"])

    fantasmas = sorted(set(MAPA) - ids_existentes)
    if fantasmas:
        print(f"  ⚠ ids no MAPA que não existem no Diário: {fantasmas}")

    # Slugs que já são vocabulário bom — um trade fora do MAPA que só usa
    # estes já está classificado certo e NÃO pode ser tocado. Sem esta
    # verificação a migração jogaria 21 trades corretos (os de julho, já
    # classificados na UI nova) direto pra "Outros" — foi o que o --simular
    # pegou na primeira rodada.
    validos = set(dt.listar_estrategias(somente_ativas=False)["slug"].astype(str)) - set(LEGADOS)

    aplicados = erros = sem_mudanca = preservados = 0
    para_outros = []
    for _, trade in trades.iterrows():
        tid = int(trade["id_trade"])
        atuais = como_lista(trade["estrategia_tags"])
        if tid in MAPA:
            novas, motivo = MAPA[tid]
        elif atuais and all(s in validos for s in atuais):
            preservados += 1
            continue
        else:
            # Sem entrada no mapa e sem tag boa = sem registro de diário -> Outros.
            novas, motivo = ["outros"], "sem registro de diário (operação só reconciliada do MT5/relatório)"
            para_outros.append(tid)
        if sorted(atuais) == sorted(novas):
            sem_mudanca += 1
            continue
        if simular:
            aplicados += 1
            continue
        if dt.atualizar_trade(tid, {"estrategia_tags": novas}):
            aplicados += 1
        else:
            erros += 1
            print(f"  ⚠ FALHOU #{tid}")

    print(f"  {'simuladas' if simular else 'aplicadas'}: {aplicados}"
          f"   já corretas: {sem_mudanca}   preservadas (fora do mapa, já válidas): {preservados}"
          f"   falhas: {erros}")
    print(f"  → Outros por ausência de diário: {len(para_outros)}")

    print()
    print("=" * 78)
    print("  VOCABULÁRIO LEGADO")
    print("=" * 78)
    if not simular:
        desativar_legados(simular)
    else:
        print(f"  seriam desativados: {LEGADOS}")

    if not simular:
        print()
        print("=" * 78)
        print("  VERIFICAÇÃO")
        print("=" * 78)
        depois = dt.listar_trades()
        usados = set()
        for v in depois["estrategia_tags"]:
            usados |= set(como_lista(v))
        restos = sorted(usados & set(LEGADOS))
        print(f"  slugs legados ainda em uso: {restos or '(nenhum)'}")
        sem_tag = int(depois["estrategia_tags"].apply(lambda v: len(como_lista(v)) == 0).sum())
        print(f"  trades sem nenhuma estratégia: {sem_tag}")
        ativas = set(dt.listar_estrategias()["slug"].astype(str))
        fora = sorted(usados - ativas)
        print(f"  slugs usados fora do cadastro ATIVO: {fora or '(nenhum)'}")
        print(f"  total de trades: {len(depois)}   soma R$: {depois['saldo_operacao_reais'].sum():+.2f}")
        return 1 if (restos or sem_tag or fora or erros) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
