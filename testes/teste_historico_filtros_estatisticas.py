"""
Testa os 2 bugs reportados pelo Jesué em 2026-07-30 (arquivo "upgrade
diario.docx"):

1. "Nas estatísticas gerais do diário está faltando Ganho Total e Perda
   Total em R$." — `dt.calcular_estatisticas` só tinha `max_gain`/`max_loss`
   (melhor/pior trade ÚNICO), nunca a soma de todos os vencedores/
   perdedores.

2. "O histórico não está mostrando de forma completa, mesmo marcando o
   período no seletor de datas ele exibe apenas o mês atual... Quando eu
   marco algum filtro (Ativo, estratégia ou janela) eu vejo que consegue
   trazer trades de meses anteriores, mas na seleção do período sem
   filtros, não mostra o histórico completo." — `_montar_tabela_historico`
   (nos 2 skins) cortava incondicionalmente pras 50 linhas mais recentes a
   menos que um clique no calendário estivesse ativo. Um range de datas
   explícito no seletor não bastava pra levantar o corte, então um período
   amplo sem NENHUM outro filtro continuava mostrando só as 50 operações
   mais recentes (quase sempre do mês corrente) — exatamente o sintoma
   relatado. Com outro filtro (ativo/janela/estratégia) o subconjunto ficava
   menor e o corte passava despercebido, o que também bate com o relato.

   A regra corrigida testada aqui (`_sem_filtro_nenhum`, réplica exata da
   condição em diario_app_classico.py::_montar_tabela_historico e
   diario_app_98.py::_montar_tabela_historico_98) só corta em 50 quando
   NENHUM filtro está ativo — nem ativo, nem janela, nem estratégia, nem
   data_ini/data_fim, nem o clique no calendário.

Roda direto: `python testes/teste_historico_filtros_estatisticas.py`.
Sai != 0 se falhar.
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


def _sem_filtro_nenhum(ativo, janela, estrategia, data_ini_efetivo, data_fim_efetivo, filtro_cal):
    """Réplica EXATA da condição usada nos 2 skins — se o texto da condição
    lá mudar sem atualizar aqui, este teste para de proteger o bug real."""
    return not (ativo or janela or estrategia or data_ini_efetivo or data_fim_efetivo or filtro_cal)


scratch = Path(tempfile.mkdtemp(prefix="teste_hist_filtros_"))
persistencia._caminho_diario = lambda: scratch / "diario_trades.parquet"
persistencia._caminho_diario_lock = lambda: scratch / ".diario.lock"
persistencia._caminho_posicoes_abertas = lambda: scratch / "posicoes_abertas.parquet"
persistencia._caminho_posicoes_abertas_lock = lambda: scratch / ".posicoes.lock"
persistencia._caminho_tickets_finalizados = lambda: scratch / "tickets_finalizados.parquet"
persistencia._caminho_tickets_ignorados = lambda: scratch / "tickets_ignorados.parquet"
persistencia._caminho_reconciliacao = lambda: scratch / "reconciliacao.parquet"

try:
    print("\n=== A regra reimplementada aqui ainda existe de verdade no código-fonte ===")
    raiz = Path(__file__).resolve().parent.parent
    linha_fonte = (
        "sem_filtro_nenhum = not (ativo or janela or estrategia "
        "or data_ini_efetivo or data_fim_efetivo or filtro_cal)"
    )
    for arquivo in ("diario_app_classico.py", "diario_app_98.py"):
        texto = (raiz / arquivo).read_text(encoding="utf-8")
        checar(linha_fonte in texto, f"{arquivo} ainda contém a condição corrigida (não divergiu do teste)")

    print("\n=== Ganho Total / Perda Total ===")
    dt.registrar_trade({"data_op": "2026-01-05", "ativo": "WIN", "tipo_operacao": "long", "saldo_operacao_reais": 100.0})
    dt.registrar_trade({"data_op": "2026-01-06", "ativo": "WIN", "tipo_operacao": "long", "saldo_operacao_reais": 50.0})
    dt.registrar_trade({"data_op": "2026-01-07", "ativo": "WIN", "tipo_operacao": "short", "saldo_operacao_reais": -30.0})
    dt.registrar_trade({"data_op": "2026-01-08", "ativo": "WIN", "tipo_operacao": "short", "saldo_operacao_reais": -20.0})
    dt.registrar_trade({"data_op": "2026-01-09", "ativo": "WIN", "tipo_operacao": "short", "saldo_operacao_reais": 0.0})

    stats = dt.calcular_estatisticas()
    checar(abs(stats["ganho_total"] - 150.0) < 1e-9, f"ganho_total soma os 2 vencedores (150), veio {stats['ganho_total']}")
    checar(abs(stats["perda_total"] - (-50.0)) < 1e-9, f"perda_total soma os 2 perdedores (-50), veio {stats['perda_total']}")
    checar(abs(stats["max_gain"] - 100.0) < 1e-9, "max_gain continua sendo o MELHOR trade único (100), não a soma")
    checar(abs(stats["max_loss"] - (-30.0)) < 1e-9, "max_loss continua sendo o PIOR trade único (-30), não a soma")
    checar(abs(stats["saldo_total"] - 100.0) < 1e-9, "saldo_total (150-50) continua correto")

    vazio = dt.calcular_estatisticas(df=dt.listar_trades().iloc[0:0])
    checar(vazio["ganho_total"] == 0.0 and vazio["perda_total"] == 0.0,
           "dict vazio (sem trades) tem ganho_total/perda_total = 0")

    print("\n=== Regra do corte de 50 linhas do Histórico ===")
    checar(_sem_filtro_nenhum(None, None, None, None, None, None) is True,
           "sem NENHUM filtro -> corta em 50 (comportamento antigo preservado)")
    checar(_sem_filtro_nenhum(None, None, None, "2026-01-01", None, None) is False,
           "SÓ data_ini setado -> NÃO corta mais (o bug reportado: período no seletor sem efeito)")
    checar(_sem_filtro_nenhum(None, None, None, None, "2026-12-31", None) is False,
           "SÓ data_fim setado -> não corta")
    checar(_sem_filtro_nenhum("WIN", None, None, None, None, None) is False,
           "só ativo setado -> não corta (já funcionava, não pode regredir)")
    checar(_sem_filtro_nenhum(None, "almoco", None, None, None, None) is False,
           "só janela setada -> não corta")
    checar(_sem_filtro_nenhum(None, None, "bounce_em_suporte", None, None, None) is False,
           "só estratégia setada -> não corta")
    checar(_sem_filtro_nenhum(None, None, None, None, None, "2026-07-15") is False,
           "filtro de calendário -> não corta")

    print("\n=== Contra o dado real: range amplo sem outro filtro devolve tudo ===")
    for i in range(60):
        dt.registrar_trade({
            "data_op": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
            "ativo": "WIN", "tipo_operacao": "long", "saldo_operacao_reais": 1.0,
        })
    todos = dt.listar_trades(is_simulated=False, data_ini="2026-01-01", data_fim="2026-12-31")
    checar(len(todos) == 65, f"65 trades no total no range amplo (veio {len(todos)})")
    ordenado = todos.sort_values("data_op", ascending=False)
    resultado = ordenado if not _sem_filtro_nenhum(None, None, None, "2026-01-01", "2026-12-31", None) \
        else ordenado.head(50)
    checar(len(resultado) == 65,
           f"com range de datas explícito, mostra as 65 (não trunca em 50) — veio {len(resultado)}")

finally:
    shutil.rmtree(scratch, ignore_errors=True)

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
