"""
Categoria da estratégia (Tendência/Reversão/Lateralidade) + split de
Topo/Fundo Duplo — 2026-07-30, pedido do Jesué em "upgrade diario.docx".

## O pedido

"Adicionar Categoria da Estratégia, são três: Tendência, Reversão e
Lateralidade. Essa é uma característica editável no cadastro da estratégia,
crie isso. Já classifique as categorias que você identificar pela própria
característica da estratégia (definição padrão do conceito em si, nos
livros e etc...). Você terá que fazer alguns ajustes, por exemplo, a
estratégia Topo/Fundo Duplo se tornarão duas estratégias diferentes (Topo
Duplo na venda, Fundo Duplo na compra)."

## A regra usada pra classificar (uma só, aplicada com consistência)

- **Tendência** = a entrada aposta na CONTINUAÇÃO de um movimento já em
  curso — pullback numa média/trendline a favor da tendência, alinhamento
  de médias, cruzamento de médias, rompimento com throwback na direção do
  próprio rompimento.
- **Reversão** = a entrada aposta que um movimento ACABOU e vai virar —
  rejeição num nível horizontal (suporte/resistência bate e volta),
  rompimento de uma LTA/LTB que definia a tendência atual (a própria linha
  que sustentava a tendência sendo quebrada), inversão de polaridade
  (suporte vira resistência ou vice-versa — a marca registrada de reversão
  nos livros de price action), e as figuras gráficas clássicas de reversão
  (OCO, topo duplo, fundo duplo).
- **Lateralidade** = o setup só faz sentido num mercado SEM direção — figura
  de indecisão/consolidação (triângulo) ou compressão de volatilidade
  (Bandas de Bollinger, "boca do jacaré").

## Onde a distinção é genuinamente sutil (documentado, não escondido)

- `bounce_em_lta`/`bounce_em_ltb` (bounce numa LINHA DE TENDÊNCIA) viraram
  Tendência — é o "pullback na linha de tendência" clássico, MUITO diferente
  de `bounce_em_suporte`/`bounce_em_resistencia` (bounce num NÍVEL
  HORIZONTAL), que viraram Reversão — a rejeição num nível fixo é o setup de
  reversão-local por definição nos livros (Al Brooks, Murphy).
- `rompimento_de_suporte`/`rompimento_de_resistencia` viraram Reversão, não
  Tendência — porque NESTE vocabulário elas significam especificamente
  "inversão de polaridade" (o nível rompido vira o oposto), que é o critério
  técnico de reversão, não simplesmente "rompeu e seguiu".
- `bandas_de_bollinger` virou Lateralidade — a característica que DEFINE o
  setup (Bollinger squeeze) é a compressão/indecisão que precede a entrada,
  mesmo a entrada em si sendo um rompimento.
- `canal` virou Tendência — um canal (LTA+LTB paralelas com inclinação) é
  uma estrutura de tendência; os trades registrados aqui operam bounces NA
  MESMA direção da inclinação do canal, não uma lateralidade horizontal.

Se o Jesué discordar de alguma, corrige na aba Estratégias — o campo é
editável, esta migração só dá o primeiro palpite documentado.

## O split de Topo/Fundo Duplo

`topo_fundo_duplo` tinha exatamente 1 trade real usando o slug (#143,
compra, resumo "Fundo Duplo W + Bipolaridade + Trowback") — mapeado agora
pra `fundo_duplo`. O slug antigo é desativado (nunca deletado — trades
futuros que porventura ainda o referenciem continuam resolvendo o nome).

Uso:
    python migracao_categoria_estrategias.py --simular
    python migracao_categoria_estrategias.py --aplicar
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diario_trade as dt  # noqa: E402

# ---------------------------------------------------------------------------
# Categoria por slug — cobre todo o cadastro ativo hoje.
# ---------------------------------------------------------------------------
CATEGORIA_POR_SLUG = {
    # Tendência (continuação)
    "bandeira": "tendencia",
    "bounce_em_lta": "tendencia",
    "bounce_em_ltb": "tendencia",
    "bounce_mme21": "tendencia",
    "canal": "tendencia",
    "fibonacci": "tendencia",
    "pullback_de_pivot": "tendencia",
    "trowback_de_pivot": "tendencia",
    "alinhamento_de_medias": "tendencia",
    "espadada": "tendencia",
    # Reversão
    "bounce_em_resistencia": "reversao",
    "bounce_em_suporte": "reversao",
    "rompimento_de_lta": "reversao",
    "rompimento_de_ltb": "reversao",
    "rompimento_de_resistencia": "reversao",
    "rompimento_de_suporte": "reversao",
    "oco": "reversao",
    "topo_duplo": "reversao",
    "fundo_duplo": "reversao",
    # Lateralidade
    "triangulo": "lateralidade",
    "bandas_de_bollinger": "lateralidade",
    # Padrões de candle únicos, reversão clássica dos livros (Nison,
    # Bulkowski) — martelo no fundo de baixa, estrela cadente no topo de
    # alta. Criadas fora desta migração (2026-07-30, 14:16 — o próprio botão
    # ✎ do Histórico sendo usado ao vivo pra reclassificar #141 de "outros"
    # pra "martelo"), adicionadas aqui só pra ganhar categoria.
    "martelo": "reversao",
    "estrela_cadente": "reversao",
    # Sem categoria própria (não é UM setup, é o "resto")
    "outros": None,
}

# Split de topo_fundo_duplo (venda=Topo Duplo, compra=Fundo Duplo).
NOVAS_ESTRATEGIAS_SPLIT = [
    ("Topo Duplo", "topo_duplo",
     "Padrão de reversão em dois topos na mesma região, o segundo mostrando "
     "perda de força compradora. Entrada na venda, no rompimento da linha de "
     "pescoço entre os dois topos.",
     ["Os 2 topos estão na mesma região de preço (ou muito próximos)",
      "O segundo topo mostra rejeição mais fraca que o primeiro",
      "A linha de pescoço (fundo entre os topos) foi rompida",
      "Volume menor no segundo topo, idealmente",
      "Alvo projetado pela distância do topo até a linha de pescoço"]),
    ("Fundo Duplo", "fundo_duplo",
     "Padrão de reversão em dois fundos na mesma região, o segundo mostrando "
     "perda de força vendedora. Entrada na compra, no rompimento da linha de "
     "pescoço entre os dois fundos.",
     ["Os 2 fundos estão na mesma região de preço (ou muito próximos)",
      "O segundo fundo mostra rejeição mais fraca que o primeiro",
      "A linha de pescoço (topo entre os fundos) foi rompida",
      "Volume menor no segundo fundo, idealmente",
      "Alvo projetado pela distância do fundo até a linha de pescoço"]),
]

# Trade que usa o slug antigo -> slug novo (só #143, ver docstring).
RECLASSIFICACAO_TRADES = {
    143: "fundo_duplo",
}

SLUG_ANTIGO = "topo_fundo_duplo"


def como_lista(v):
    if v is None or isinstance(v, str):
        return []
    return [str(x) for x in v] if hasattr(v, "__iter__") else []


def aplicar_categorias(simular: bool) -> None:
    existentes = dt.listar_estrategias(somente_ativas=False)
    for _, linha in existentes.iterrows():
        slug = str(linha["slug"])
        if slug not in CATEGORIA_POR_SLUG:
            continue
        categoria_nova = CATEGORIA_POR_SLUG[slug]
        categoria_atual = linha.get("categoria")
        categoria_atual = None if (categoria_atual is None or str(categoria_atual) == "nan") else str(categoria_atual)
        if categoria_atual == categoria_nova:
            print(f"  já correta   {slug:<26} -> {categoria_nova}")
            continue
        print(f"  {'simulado' if simular else 'aplicado '}   {slug:<26} -> {categoria_nova}")
        if not simular:
            dt.editar_estrategia(int(linha["id"]), categoria=categoria_nova)


def aplicar_split(simular: bool) -> None:
    existentes = dt.listar_estrategias(somente_ativas=False)
    por_slug = {str(r["slug"]): r for _, r in existentes.iterrows()}

    for nome, slug, descricao, checklist in NOVAS_ESTRATEGIAS_SPLIT:
        if slug in por_slug:
            print(f"  já existe    {slug}")
            continue
        print(f"  CRIANDO      {slug:<16} {nome}")
        if not simular:
            novo_id = dt.criar_estrategia(nome, descricao, checklist, categoria="reversao")
            if novo_id is None:
                raise RuntimeError(f"não consegui criar {slug} (trava ocupada)")
            dt.editar_estrategia(novo_id, slug=slug)

    linha_antiga = por_slug.get(SLUG_ANTIGO)
    if linha_antiga is not None and bool(linha_antiga["ativo"]):
        print(f"  desativando  {SLUG_ANTIGO} (virou topo_duplo/fundo_duplo)")
        if not simular:
            dt.desativar_estrategia(int(linha_antiga["id"]))
    elif linha_antiga is not None:
        print(f"  já inativa   {SLUG_ANTIGO}")

    print()
    for tid, slug_novo in RECLASSIFICACAO_TRADES.items():
        trades = dt.listar_trades()
        linha = trades[trades["id_trade"] == tid]
        if linha.empty:
            print(f"  ⚠ trade #{tid} não encontrado — pulando")
            continue
        atuais = como_lista(linha.iloc[0]["estrategia_tags"])
        novas = [t for t in atuais if t != SLUG_ANTIGO]
        if slug_novo not in novas:
            novas.append(slug_novo)
        if sorted(atuais) == sorted(novas):
            print(f"  #{tid} já correto: {atuais}")
            continue
        print(f"  #{tid}: {atuais} -> {novas}")
        if not simular:
            if not dt.atualizar_trade(tid, {"estrategia_tags": novas}):
                raise RuntimeError(f"falhou ao reclassificar #{tid}")


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--simular", action="store_true")
    g.add_argument("--aplicar", action="store_true")
    args = p.parse_args()
    simular = args.simular

    print("=" * 78)
    print("  SPLIT TOPO/FUNDO DUPLO")
    print("=" * 78)
    aplicar_split(simular)

    print()
    print("=" * 78)
    print("  CATEGORIAS")
    print("=" * 78)
    aplicar_categorias(simular)

    if not simular:
        print()
        print("=" * 78)
        print("  VERIFICAÇÃO")
        print("=" * 78)
        depois = dt.listar_estrategias()
        sem_categoria = depois[depois["categoria"].isna() & (depois["slug"] != "outros")]
        print(f"  estratégias ativas sem categoria (exceto 'outros'): {len(sem_categoria)}")
        if len(sem_categoria):
            print("   ", list(sem_categoria["slug"]))
        ativas = set(depois["slug"].astype(str))
        print(f"  topo_duplo ativo: {'topo_duplo' in ativas}   fundo_duplo ativo: {'fundo_duplo' in ativas}")
        print(f"  topo_fundo_duplo ainda ativo: {SLUG_ANTIGO in ativas} (esperado: False)")
        trade143 = dt.listar_trades().query("id_trade == 143").iloc[0]
        tags143 = como_lista(trade143["estrategia_tags"])
        print(f"  #143 tags: {tags143}")
        return 1 if (len(sem_categoria) or SLUG_ANTIGO in ativas or "fundo_duplo" not in tags143) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
