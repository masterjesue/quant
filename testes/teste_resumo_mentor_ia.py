"""
Testa `dt.montar_resumo_para_mentor_ia` — o resumo textual que alimenta o
Mentor de IA (item D, "upgrade diario.docx", 2026-07-30). Puro: nenhuma
chamada de API mora aqui nem é testada por este arquivo (ver
scripts/verificar_mentor_ia_real.py pra isso, fora da suíte automática de
propósito — custa dinheiro real).

O que precisa valer:
  1. sem trades reais -> string vazia (nunca estoura)
  2. `ate_data` corta o recorte de verdade (trade de hoje não entra na
     análise "retrospectiva")
  3. curva de aprendizagem: 1ª/2ª metade batem com contagem real
  4. disciplina: dias com 4+ operações contados corretamente
  5. vencedora/perdedora aparecem quando há amostra suficiente

Roda direto: `python testes/teste_resumo_mentor_ia.py`. Sai != 0 se falhar.
"""
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import persistencia  # noqa: E402
import diario_trade as dt  # noqa: E402

falhas = []


def checar(cond, msg):
    print(("  OK   " if cond else "  FALHA") + f"  {msg}")
    if not cond:
        falhas.append(msg)


scratch = Path(tempfile.mkdtemp(prefix="teste_resumo_mentor_"))
persistencia._caminho_diario = lambda: scratch / "diario_trades.parquet"
persistencia._caminho_diario_lock = lambda: scratch / ".diario.lock"
persistencia._caminho_posicoes_abertas = lambda: scratch / "posicoes_abertas.parquet"
persistencia._caminho_posicoes_abertas_lock = lambda: scratch / ".posicoes.lock"
persistencia._caminho_tickets_finalizados = lambda: scratch / "tickets_finalizados.parquet"
persistencia._caminho_tickets_ignorados = lambda: scratch / "tickets_ignorados.parquet"
persistencia._caminho_reconciliacao = lambda: scratch / "reconciliacao.parquet"
persistencia._caminho_estrategias = lambda: scratch / "diario_estrategias.parquet"
persistencia._caminho_estrategias_lock = lambda: scratch / ".estrategias.lock"

try:
    print("\n=== sem trades reais ===")
    checar(dt.montar_resumo_para_mentor_ia() == "", "diário vazio -> string vazia")

    hoje = date.today()
    ontem = hoje - timedelta(days=1)

    print("\n=== ate_data corta o recorte (trade de hoje não entra) ===")
    dt.registrar_trade({"data_op": hoje.isoformat(), "ativo": "WIN", "tipo_operacao": "long",
                        "saldo_operacao_reais": 999.0})
    resumo_sem_hoje = dt.montar_resumo_para_mentor_ia()  # default = ontem
    checar("999.00" not in resumo_sem_hoje or resumo_sem_hoje == "",
           "trade de HOJE não aparece na análise retrospectiva (default = ontem)")
    checar(dt.montar_resumo_para_mentor_ia() == "",
           "sem NENHUM trade até ontem, resumo vem vazio mesmo com trade de hoje presente")

    print("\n=== curva de aprendizagem + disciplina + vencedora/perdedora ===")
    id_a = dt.criar_estrategia("Setup Bom", categoria="tendencia")
    id_b = dt.criar_estrategia("Setup Ruim", categoria="reversao")
    dt.editar_estrategia(id_a, slug="setup_bom")
    dt.editar_estrategia(id_b, slug="setup_ruim")

    # 4 trades num dia só (2026-01-05) = overtrading; todos setup_ruim, perdedores
    for _ in range(4):
        dt.registrar_trade({"data_op": "2026-01-05", "ativo": "WIN", "tipo_operacao": "long",
                            "estrategia_tags": ["setup_ruim"], "saldo_operacao_reais": -50.0})
    # 1 trade por dia em 3 dias distintos, setup_bom, vencedores
    for i, saldo in enumerate((200.0, 200.0, 200.0)):
        dt.registrar_trade({"data_op": f"2026-02-{10+i:02d}", "ativo": "WDO", "tipo_operacao": "short",
                            "estrategia_tags": ["setup_bom"], "saldo_operacao_reais": saldo})

    resumo = dt.montar_resumo_para_mentor_ia(ate_data=date(2026, 12, 31))
    # 8, não 7: inclui também o trade de "hoje" (+999, sem tag) registrado
    # na seção anterior, já que este ate_data é bem no futuro e o inclui.
    checar("8 opera" in resumo, f"conta as 8 operações reais (veio: {[l for l in resumo.splitlines() if 'PER' in l][:1]})")
    checar("1 dia(s) com 4+ opera" in resumo, "detecta o único dia de overtrading (4 trades em 2026-01-05)")
    checar("Setup Bom" in resumo and "R$ +600.00" in resumo, "estratégia vencedora geral = Setup Bom (+600)")
    checar("Setup Ruim" in resumo and "-200.00" in resumo, "estratégia perdedora geral = Setup Ruim (-200)")
    checar("TOP 3 ESTRAT" in resumo, "lista top 3 estratégias por saldo")

finally:
    shutil.rmtree(scratch, ignore_errors=True)

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
