"""Testes de `persistencia._limpar_temporarios_orfaos` — 2026-07-28.

`_salvar_parquet_atomico` grava num `.tmp<pid>.parquet` e so depois faz o
`os.replace`. Se o processo morre NESSE intervalo, o temporario fica pra
sempre — acumulei 7 deles (17 MB) matando dashboards durante a sessao de
correcoes.

O risco da limpeza e apagar o temporario de uma gravacao VIVA (ex:
`run_backtest --features` sobre a base inteira, que demora). Por isso o
criterio e o PID, nao a idade — e o teste negativo abaixo e o mais importante
do arquivo.
"""
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

SCRATCH = Path(__file__).parent / "_scratch_tmp"
if SCRATCH.exists():
    shutil.rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import persistencia  # noqa: E402

falhas = []


def checar(nome, ok, detalhe=""):
    print(("  PASS  " if ok else "  FALHA ") + nome + ("" if ok else "  " + str(detalhe)))
    if not ok:
        falhas.append(nome)


def criar(nome):
    p = SCRATCH / nome
    pd.DataFrame({"a": [1]}).to_parquet(p, index=False)
    return p


# PID que com certeza nao existe (PIDs reais sao pequenos; 2^22 nao e usado)
PID_MORTO = 4194303
PID_VIVO = os.getpid()

orfao = criar(f"WIN_candles.tmp{PID_MORTO}.parquet")
vivo = criar(f"WDO_candles.tmp{PID_VIVO}.parquet")
definitivo = criar("WIN_candles.parquet")
nao_tmp = criar("diario_trades.parquet")

persistencia._TEMPORARIOS_JA_LIMPOS = False
persistencia._limpar_temporarios_orfaos(SCRATCH)

print()
checar("apaga temporario de processo MORTO", not orfao.exists())
checar("PRESERVA temporario de processo VIVO", vivo.exists(),
       "apagar isto corromperia uma gravacao em andamento")
checar("nao toca no parquet definitivo", definitivo.exists())
checar("nao toca em arquivo que nao e .tmp", nao_tmp.exists())

print()
print("=== so roda uma vez por processo ===")
orfao2 = criar(f"WIN_metadata.tmp{PID_MORTO}.parquet")
persistencia._limpar_temporarios_orfaos(SCRATCH)   # flag ja marcada
checar("segunda chamada e no-op", orfao2.exists(),
       "deve pular; varredura de diretorio nao precisa repetir a cada save")

print()
print("=== pasta inexistente nao levanta excecao ===")
try:
    persistencia._TEMPORARIOS_JA_LIMPOS = False
    persistencia._limpar_temporarios_orfaos(SCRATCH / "nao_existe")
    checar("pasta inexistente e ignorada", True)
except Exception as e:  # noqa: BLE001
    checar("pasta inexistente e ignorada", False, repr(e))

print()
print("=== _processo_vivo ===")
checar("PID do proprio processo = vivo", persistencia._processo_vivo(PID_VIVO))
checar("PID inexistente = morto", not persistencia._processo_vivo(PID_MORTO))

shutil.rmtree(SCRATCH, ignore_errors=True)

print("\n" + "=" * 60)
if falhas:
    print(f"  {len(falhas)} FALHA(S): {falhas}")
    sys.exit(1)
print("  TODOS OS TESTES PASSARAM")
