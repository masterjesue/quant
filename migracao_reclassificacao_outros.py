"""
Item C do "upgrade diario.docx" — reclassificação dos 69 trades hoje sob
"outros", 2026-07-30.

## O pedido do Jesué (verbatim)

"Aqui temos dois casos diferentes que preciso que me ajude a classificar. O
trades em 'Outros' desse mês estão sem registro no diário porque o sistema
não estava operacional no momento. Esses trades não foram testes de novas
estratégias, erros operacionais ou simplesmente trades sem estratégias. Eles
foram trades dentro do meu operacional mais usado (Suporte/Resistência/
Pivot) com a análise do Professor Igor, mas não houve registro dos detalhes
e não me lembro desses detalhes para classificar. Já os trades 'Outros' dos
meses anteriores entram na categoria de teste de nova estratégia ou erro
operacional. Quero que rode uma análise para buscar identificar pistas que
indicam trade 'errado' ou fora do operacional (...) Eu imagino que a divisão
seja algo assim: Sem Estratégia (Erro e Aprendizagem); Não Classificado
(devido a algum desafio operacional, pessoal ou técnico); Outras Estratégias
(Teste de novas formas de operar)."

Decisão confirmada antes de eu rodar isso: corte de data
`data_op >= 2026-07-01` = bloco de julho.

## O que os dados REALMENTE mostram (levantado antes de decidir qualquer coisa)

Os 69 trades sob "outros" se dividem em grupos com características bem
diferentes entre si — a classificação abaixo segue o que os PRÓPRIOS dados
mostram, não uma divisão arbitrária:

1. **#135 (01/07)** — tem resumo próprio: "Estrela da Tarde". É um padrão de
   candle de reversão, mesma família de Martelo/Estrela Cadente (criadas ao
   vivo nesta mesma sessão, fora deste script). Não faz parte do "sem
   registro" — vira estratégia própria.

2. **23 trades de julho (>= 01/07, exceto #135)** — ZERO resumo, ZERO
   estrategia_raw, ZERO alvo/stop, sem print. Batem 100% com a descrição do
   Jesué: "sistema não estava operacional", mas ele SABE que eram do
   operacional S/R/Pivô com o professor Igor. Viram uma estratégia própria
   "Suporte/Resistência/Pivô (sem registro)" — não uma das granulares
   (bounce_em_suporte etc.), porque não há NENHUM dado pra saber qual delas
   era cada trade; fabricar essa especificidade seria inventar, não
   classificar.

3. **#16 e #70 (16/04 e 29/05)** — DIFERENTE do bloco de julho: estes TÊM
   resumo e raw ("Kita" = nome do professor, "Suporte\\Resistência" com
   "professor Igor substituiu o Marquinhos"). Os dois descrevem seguir a
   call de um mentor SEM um setup próprio nomeado pelo Jesué — uma escolha
   intencional e documentada (copy-trade consciente), não uma lacuna de
   dado. Isso bate com a definição de "Outras Estratégias" do próprio
   Jesué: "teste de novas formas de operar, intencional e dentro do
   gerenciamento de risco".

4. **43 trades de fevereiro/março (antes de julho, excluindo #16/#70)** —
   ZERO resumo, ZERO raw, ZERO alvo/stop, igual ao bloco de julho — mas o
   Jesué foi claro que ESTES (meses anteriores) são erro/aprendizagem OU
   teste, não o operacional S/R/Pivô. A pista que ele pediu pra usar
   ("trades rápidos em sequência — provavelmente sem análise ou em estado
   emocional alterado") é medível de verdade nestes 43: agrupando por dia,
   3 dias saltam da massa por CONTAGEM de trades (não é achismo, é o mesmo
   padrão que o estudo de estratégias já tinha achado — dias de 5+
   operações destroem resultado):
     - 2026-02-12: 5 trades no dia
     - 2026-02-13: 4 trades no dia
     - 2026-02-23: 7 trades no dia (o pior saldo do dia: -R$169)
   Esses 3 dias (16 trades) viram **Sem Estratégia (Erro e Aprendizagem)**
   — overtrading mensurável, o próprio marcador que o Jesué pediu.
   Os outros 12 dias (27 trades, 1-3 operações/dia) não têm NENHUM outro
   sinal — nem alvo/stop atípico (o dado nem existe), nem sequência rápida.
   Não dá pra dizer se foi disciplinado ou não: viram **Não Classificado**,
   honestamente, em vez de eu inventar um veredito sem base.

## As 5 estratégias novas criadas aqui

| Slug | Nome | Trades | Categoria |
|---|---|---:|---|
| `estrela_da_tarde` | Estrela da Tarde | 1 | reversao |
| `srp_sem_registro` | Suporte/Resistência/Pivô (sem registro) | 23 | (nenhuma — mistura) |
| `outras_estrategias` | Outras Estratégias | 2 | (nenhuma — teste avulso) |
| `sem_estrategia_aprendizagem` | Sem Estratégia (Erro e Aprendizagem) | 16 | (nenhuma — não é setup) |
| `nao_classificado` | Não Classificado | 27 | (nenhuma — não é setup) |

`outros` continua cadastrada e ATIVA (não desativar) — segue sendo o
fallback genérico pra trades futuros que não caibam em nenhuma categoria;
só fica sem nenhum trade real associado depois desta migração.

Uso:
    python migracao_reclassificacao_outros.py --simular
    python migracao_reclassificacao_outros.py --aplicar
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diario_trade as dt  # noqa: E402

CORTE_JULHO = "2026-07-01"

NOVAS_ESTRATEGIAS = [
    ("Estrela da Tarde", "estrela_da_tarde",
     "Três candles: alta, indecisão, e forte candle de baixa que fecha "
     "abaixo do meio do primeiro — reversão de topo, espelho da Estrela da Manhã.",
     ["A vela do meio mostra indecisão real (corpo pequeno/doji)",
      "A terceira vela fecha abaixo de 50% do corpo da primeira",
      "Ocorre após uma alta consistente, não em lateralização"],
     "reversao"),
    ("Suporte/Resistência/Pivô (sem registro)", "srp_sem_registro",
     "Operações reais dentro do operacional habitual (Suporte/Resistência/"
     "Pivô, com a análise do Professor Igor) sem registro de diário — o "
     "sistema não estava operacional no momento. Sem dado suficiente pra "
     "saber qual variante específica (bounce/rompimento, suporte/"
     "resistência) foi cada trade.", [], None),
    ("Outras Estratégias", "outras_estrategias",
     "Teste de formas de operar fora do setup pessoal nomeado — ex: seguir "
     "a call de um mentor sem um operacional próprio identificado. "
     "Intencional e dentro do gerenciamento de risco, não erro.", [], None),
    ("Sem Estratégia (Erro e Aprendizagem)", "sem_estrategia_aprendizagem",
     "Trades da fase de aprendizagem sem registro, em dias com contagem "
     "de operações claramente acima do normal (overtrading mensurável, "
     "não achismo) — mesmo padrão que o estudo de estratégias de 2026-07-30 "
     "já tinha identificado como o maior vazamento de resultado.", [], None),
    ("Não Classificado", "nao_classificado",
     "Trades sem nenhum registro (resumo, raw, alvo/stop) e sem nenhum "
     "outro sinal mensurável (ex: sequência atípica no dia) que permita "
     "julgar se foram disciplinados ou não — a honestidade aqui é dizer "
     "'não sei', não inventar um veredito.", [], None),
]

# id_trade -> slug novo
RECLASSIFICACAO = {
    135: "estrela_da_tarde",
    # Bloco de julho (23 trades) -> S/R/Pivô sem registro
    178: "srp_sem_registro", 177: "srp_sem_registro", 176: "srp_sem_registro",
    170: "srp_sem_registro", 149: "srp_sem_registro", 148: "srp_sem_registro",
    151: "srp_sem_registro", 152: "srp_sem_registro", 153: "srp_sem_registro",
    150: "srp_sem_registro", 158: "srp_sem_registro", 157: "srp_sem_registro",
    155: "srp_sem_registro", 156: "srp_sem_registro", 159: "srp_sem_registro",
    166: "srp_sem_registro", 165: "srp_sem_registro", 175: "srp_sem_registro",
    168: "srp_sem_registro", 169: "srp_sem_registro", 183: "srp_sem_registro",
    174: "srp_sem_registro", 230: "srp_sem_registro",
    # Seguiram call de mentor sem setup próprio (documentado, intencional)
    16: "outras_estrategias", 70: "outras_estrategias",
    # Dias com contagem de trades claramente overtrading (>=4/dia)
    202: "sem_estrategia_aprendizagem", 203: "sem_estrategia_aprendizagem",
    204: "sem_estrategia_aprendizagem", 205: "sem_estrategia_aprendizagem",
    206: "sem_estrategia_aprendizagem",  # 2026-02-12, 5 trades
    207: "sem_estrategia_aprendizagem", 208: "sem_estrategia_aprendizagem",
    209: "sem_estrategia_aprendizagem", 210: "sem_estrategia_aprendizagem",  # 2026-02-13, 4 trades
    217: "sem_estrategia_aprendizagem", 218: "sem_estrategia_aprendizagem",
    219: "sem_estrategia_aprendizagem", 220: "sem_estrategia_aprendizagem",
    221: "sem_estrategia_aprendizagem", 222: "sem_estrategia_aprendizagem",
    223: "sem_estrategia_aprendizagem",  # 2026-02-23, 7 trades
    # Resto de fev/mar (1-3 trades/dia, sem outro sinal) -> Não Classificado
    184: "nao_classificado", 185: "nao_classificado", 186: "nao_classificado",
    187: "nao_classificado", 188: "nao_classificado", 189: "nao_classificado",
    190: "nao_classificado", 191: "nao_classificado", 192: "nao_classificado",
    193: "nao_classificado", 194: "nao_classificado", 195: "nao_classificado",
    196: "nao_classificado", 197: "nao_classificado", 198: "nao_classificado",
    199: "nao_classificado", 200: "nao_classificado", 201: "nao_classificado",
    211: "nao_classificado", 212: "nao_classificado", 213: "nao_classificado",
    214: "nao_classificado", 215: "nao_classificado", 216: "nao_classificado",
    224: "nao_classificado", 225: "nao_classificado", 226: "nao_classificado",
}


def como_lista(v):
    if v is None or isinstance(v, str):
        return []
    return [str(x) for x in v] if hasattr(v, "__iter__") else []


def criar_estrategias(simular: bool) -> None:
    existentes = dt.listar_estrategias(somente_ativas=False)
    slugs_existentes = set(existentes["slug"].astype(str))
    for nome, slug, descricao, checklist, categoria in NOVAS_ESTRATEGIAS:
        if slug in slugs_existentes:
            print(f"  já existe    {slug}")
            continue
        print(f"  CRIANDO      {slug:<28} {nome}")
        if not simular:
            novo_id = dt.criar_estrategia(nome, descricao, checklist, categoria=categoria)
            if novo_id is None:
                raise RuntimeError(f"não consegui criar {slug} (trava ocupada)")
            dt.editar_estrategia(novo_id, slug=slug)


def aplicar_reclassificacao(simular: bool) -> None:
    trades = dt.listar_trades()
    aplicados = ja_corretos = erros = 0
    for tid, slug_novo in RECLASSIFICACAO.items():
        linha = trades[trades["id_trade"] == tid]
        if linha.empty:
            print(f"  ⚠ trade #{tid} não encontrado — pulando")
            continue
        atuais = como_lista(linha.iloc[0]["estrategia_tags"])
        novas = [t for t in atuais if t != "outros"]
        if slug_novo not in novas:
            novas.append(slug_novo)
        if sorted(atuais) == sorted(novas):
            ja_corretos += 1
            continue
        print(f"  {'simulado' if simular else 'aplicado '}   #{tid}: {atuais} -> {novas}")
        if not simular:
            if not dt.atualizar_trade(tid, {"estrategia_tags": novas}):
                erros += 1
                print(f"  ⚠ FALHOU #{tid}")
                continue
        aplicados += 1
    print(f"\n  {'simulados' if simular else 'aplicados'}: {aplicados}   já corretos: {ja_corretos}   falhas: {erros}")


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--simular", action="store_true")
    g.add_argument("--aplicar", action="store_true")
    args = p.parse_args()
    simular = args.simular

    print("=" * 78)
    print("  CRIANDO ESTRATÉGIAS NOVAS")
    print("=" * 78)
    criar_estrategias(simular)

    print()
    print("=" * 78)
    print("  RECLASSIFICANDO TRADES")
    print("=" * 78)
    aplicar_reclassificacao(simular)

    if not simular:
        print()
        print("=" * 78)
        print("  VERIFICAÇÃO")
        print("=" * 78)
        depois = dt.listar_trades()

        def L(v):
            return [] if v is None or isinstance(v, str) else list(v)

        ainda_outros = depois[depois["estrategia_tags"].apply(lambda v: "outros" in L(v))]
        ids_esperados = set(RECLASSIFICACAO.keys())
        sobrou = set(ainda_outros["id_trade"].astype(int)) & ids_esperados
        print(f"  trades que deveriam ter saído de 'outros' e ainda estão lá: {len(sobrou)}")
        if sobrou:
            print("   ", sorted(sobrou))
        total_reais = len(depois[~depois["is_simulated"].astype(bool)])
        print(f"  total de trades reais (não deve ter mudado): {total_reais}")
        return 1 if sobrou else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
