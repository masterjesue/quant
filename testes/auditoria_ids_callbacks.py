"""Auditoria estatica da classe de bug que mais reincide neste projeto.

REGRA: uma callback do Dash e rejeitada INTEIRA, em silencio, se qualquer
Output/Input/State apontar pra um id que nao esta montado no DOM naquele
momento. Como trocar de sub-aba do Diario substitui a arvore inteira, um id
de outra aba numa callback = botao que nao faz nada, sem erro visivel.

Este script monta o layout de cada sub-aba, coleta os ids de cada escopo, e
denuncia toda callback cujos Outputs/States nao existem em TODA aba onde os
Inputs dela existem.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def coletar_ids(componente, alvo):
    """Anda a arvore de componentes Dash somando os ids string encontrados."""
    if componente is None:
        return
    if isinstance(componente, (list, tuple)):
        for filho in componente:
            coletar_ids(filho, alvo)
        return
    cid = getattr(componente, "id", None)
    if isinstance(cid, str):
        alvo.add(cid)
    for prop in ("children", "conteudo"):
        if hasattr(componente, prop):
            coletar_ids(getattr(componente, prop), alvo)


def ids_de_dependencia(dep):
    """Devolve (id_string, is_pattern). Inputs/State no callback_map vem como
    dict {'id':..,'property':..}; Outputs vem como objeto Output."""
    cid = dep["id"] if isinstance(dep, dict) else dep.component_id
    if isinstance(cid, dict):
        return (cid.get("type"), True)
    return (cid, False)


def auditar(modulo, construtores, nome_modulo):
    print(f"\n{'=' * 70}\n  {nome_modulo}\n{'=' * 70}")
    globais = set()
    coletar_ids(modulo.app.layout, globais)

    escopos = {"GLOBAL": globais}
    for chave, fn in construtores.items():
        ids = set()
        try:
            coletar_ids(fn(), ids)
        except Exception as e:  # noqa: BLE001
            print(f"  (nao consegui montar '{chave}': {type(e).__name__}: {e})")
            continue
        escopos[chave] = ids - globais

    print("\n  Ids por escopo:")
    for chave, ids in escopos.items():
        print(f"    {chave:<14} {len(ids):>4} ids")

    def escopos_de(id_str):
        """Em quais escopos este id existe?"""
        return {k for k, ids in escopos.items() if id_str in ids}

    problemas = []
    for chave_cb, cb in modulo.app.callback_map.items():
        inputs = cb.get("inputs", [])
        states = cb.get("state", [])
        outputs = cb["output"] if isinstance(cb["output"], list) else [cb["output"]]

        # Escopos onde a callback PODE disparar = uniao dos escopos dos Inputs
        # string. (Pattern-matching nao da pra resolver estaticamente aqui.)
        escopos_gatilho = set()
        tem_input_string = False
        for dep in inputs:
            id_str, is_pattern = ids_de_dependencia(dep)
            if is_pattern or id_str is None:
                continue
            tem_input_string = True
            escopos_gatilho |= escopos_de(id_str)
        if not tem_input_string or not escopos_gatilho:
            continue  # so pattern-matching: fora do alcance desta checagem
        escopos_gatilho.discard("GLOBAL")
        if not escopos_gatilho:
            escopos_gatilho = {"GLOBAL"}

        for dep, papel in [(d, "Output") for d in outputs] + [(d, "State") for d in states]:
            id_str, is_pattern = ids_de_dependencia(dep)
            if is_pattern or id_str is None:
                continue
            onde = escopos_de(id_str)
            if not onde:
                continue  # id criado dinamicamente por outra callback
            if "GLOBAL" in onde:
                continue  # sempre montado, sempre seguro
            faltando = escopos_gatilho - onde
            if faltando:
                problemas.append(
                    f"    {chave_cb}\n"
                    f"      {papel} '{id_str}' existe em {sorted(onde)}\n"
                    f"      mas a callback dispara tambem em {sorted(faltando)}"
                )

    if problemas:
        print(f"\n  {len(problemas)} ACOPLAMENTO(S) ENTRE ABAS:\n")
        for p in problemas:
            print(p)
    else:
        print("\n  OK — nenhuma callback referencia id de aba onde ela nao esta montada.")
    return len(problemas)


import diario_app_classico as dac  # noqa: E402

total = auditar(dac, {
    "estatisticas": lambda: dac._construir_diario_estatisticas(
        dac.dt.calcular_estatisticas(), dac.dt.calcular_score_confiabilidade()),
    "posicao": dac._construir_diario_em_andamento,
    "historico": dac._construir_diario_historico,
    "estrategias": dac._construir_diario_estrategias,
    "padroes": dac._construir_diario_padroes,
    "bem_estar": dac._construir_diario_bem_estar,
}, "diario_app_classico.py")

import diario_app_98 as d98  # noqa: E402

total += auditar(d98, {
    "estatisticas": lambda: d98._construir_diario98_estatisticas(
        d98.dt.calcular_estatisticas(), d98.dt.calcular_score_confiabilidade()),
    "posicao": d98._construir_diario98_em_andamento,
    "historico": d98._construir_diario98_historico,
    "estrategias": d98._construir_diario98_estrategias,
    "padroes": d98._construir_diario98_padroes,
    "bem_estar": d98._construir_diario98_bem_estar,
}, "diario_app_98.py")

print(f"\n{'=' * 70}")
print(f"  TOTAL DE ACOPLAMENTOS: {total}")
sys.exit(1 if total else 0)
