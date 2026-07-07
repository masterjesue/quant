# =============================================================================
# csv_loader.py — Carga e parsing dos CSVs exportados do ProfitChart
# =============================================================================
# Responsabilidade: ler todos os CSVs da pasta configurada, aplicar parsing
# correto (formato BR + correção de escala do WIN), mesclar múltiplos
# arquivos do mesmo ativo sem duplicar timestamps sobrepostos.
#
# Toda a lógica aqui foi validada empiricamente em 30/06/2026 contra dados
# reais do MT5 (ver validacao_cruzada_csv_mt5.py) — dois bugs reais foram
# encontrados e corrigidos nesse processo:
#   1. Detecção de dtype 'string' (pandas moderno) vs 'object' (legado)
#   2. Escala do WIN: CSV vem em milhares de pontos com 3 casas decimais
# =============================================================================

import pandas as pd
from pathlib import Path
from datetime import datetime

import config_backtest as cfg


def _parse_numero_br(serie: pd.Series) -> pd.Series:
    """
    Converte uma coluna numérica do formato BR (ponto = milhar, vírgula =
    decimal) para float. Detecta tanto o dtype legado 'object' quanto o
    dtype 'string' moderno do pandas — checar só "== object" deixa colunas
    'string' passarem sem conversão (bug real encontrado em teste).
    Se a coluna já vier como float nativo (pandas já interpretou sozinho,
    caso comum quando não há separador de milhar), apenas retorna como float.
    """
    if serie.dtype == object or pd.api.types.is_string_dtype(serie):
        return (serie.astype(str)
                .str.replace('.', '', regex=False)
                .str.replace(',', '.', regex=False)
                .astype(float))
    return serie.astype(float)


def _identificar_ativo(nome_arquivo: str) -> str | None:
    """Identifica se o arquivo é WIN ou WDO pelo nome."""
    nome_upper = nome_arquivo.upper()
    if cfg.PADRAO_ARQUIVO_WIN in nome_upper:
        return "WIN"
    if cfg.PADRAO_ARQUIVO_WDO in nome_upper:
        return "WDO"
    return None


def carregar_csv_individual(caminho: Path, ativo: str) -> pd.DataFrame:
    """
    Lê um único CSV do Profit, faz parsing completo (datetime, números BR,
    correção de escala) e retorna DataFrame padronizado.

    Colunas de saída: datetime, open, high, low, close, volume, qtd_negocios
    """
    df = pd.read_csv(caminho, sep=';', encoding='latin1')

    df['datetime'] = pd.to_datetime(
        df['Data'] + ' ' + df['Hora'], format='%d/%m/%Y %H:%M:%S'
    )

    for col in ['Abertura', 'Máximo', 'Mínimo', 'Fechamento']:
        df[col] = _parse_numero_br(df[col])

    # Volume também pode vir em formato BR como string
    if 'Volume' in df.columns:
        df['Volume'] = _parse_numero_br(df['Volume'])
    if 'Quantidade' in df.columns:
        df['Quantidade'] = _parse_numero_br(df['Quantidade'])

    # Correção de escala — validado empiricamente: WIN vem em milhares de
    # pontos (174.620 = 174620), WDO já vem na escala correta nativamente.
    fator = cfg.FATOR_ESCALA_CSV.get(ativo, 1)
    if fator != 1:
        for col in ['Abertura', 'Máximo', 'Mínimo', 'Fechamento']:
            df[col] = df[col] * fator

    df = df.rename(columns={
        'Abertura': 'open', 'Máximo': 'high',
        'Mínimo': 'low', 'Fechamento': 'close',
        'Volume': 'volume_financeiro', 'Quantidade': 'qtd_negocios'
    })

    df['fonte'] = f'csv:{caminho.name}'
    df = df.sort_values('datetime').reset_index(drop=True)

    colunas_saida = ['datetime', 'open', 'high', 'low', 'close', 'fonte']
    if 'volume_financeiro' in df.columns:
        colunas_saida.append('volume_financeiro')
    if 'qtd_negocios' in df.columns:
        colunas_saida.append('qtd_negocios')

    return df[colunas_saida]


def carregar_todos_csvs(ativo: str, pasta: Path = None) -> pd.DataFrame:
    """
    Varre a pasta configurada, identifica todos os CSVs pertencentes ao
    ativo solicitado, carrega cada um e mescla numa série única.

    Em caso de timestamps sobrepostos entre arquivos diferentes, mantém o
    dado do arquivo mais recentemente modificado (presume-se export mais
    novo = mais confiável/atualizado).
    """
    pasta = pasta or cfg.PASTA_CSV

    if not pasta.exists():
        print(f"[csv_loader] ⚠️ Pasta não encontrada: {pasta}")
        return pd.DataFrame()

    arquivos_candidatos = [
        f for f in pasta.glob("*.csv")
        if _identificar_ativo(f.name) == ativo
    ]

    if not arquivos_candidatos:
        print(f"[csv_loader] ⚠️ Nenhum CSV de {ativo} encontrado em {pasta}")
        return pd.DataFrame()

    # Ordena por data de modificação (mais antigo primeiro) — assim, ao
    # concatenar e remover duplicatas mantendo o último, o arquivo mais
    # recente "vence" em caso de sobreposição.
    arquivos_candidatos.sort(key=lambda f: f.stat().st_mtime)

    print(f"[csv_loader] {ativo}: {len(arquivos_candidatos)} arquivo(s) encontrado(s):")
    for f in arquivos_candidatos:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        print(f"    - {f.name} (modificado em {mtime})")

    partes = []
    for arquivo in arquivos_candidatos:
        df_parte = carregar_csv_individual(arquivo, ativo)
        partes.append(df_parte)
        print(f"    {arquivo.name}: {len(df_parte)} linhas | "
              f"{df_parte['datetime'].min()} até {df_parte['datetime'].max()}")

    df_completo = pd.concat(partes, ignore_index=True)

    antes = len(df_completo)
    df_completo = df_completo.drop_duplicates(subset='datetime', keep='last')
    depois = len(df_completo)

    df_completo = df_completo.sort_values('datetime').reset_index(drop=True)

    print(f"[csv_loader] {ativo}: mesclado para {depois} linhas "
          f"({antes - depois} duplicatas de timestamp removidas)")
    print(f"[csv_loader] {ativo}: cobertura final {df_completo['datetime'].min()} "
          f"até {df_completo['datetime'].max()}\n")

    return df_completo


if __name__ == '__main__':
    # Teste rápido standalone — roda o carregamento e mostra resumo
    print("=" * 90)
    print("  TESTE STANDALONE — csv_loader.py")
    print("=" * 90)

    for ativo in cfg.ATIVOS:
        df = carregar_todos_csvs(ativo)
        if not df.empty:
            print(f"\n{ativo} — amostra das últimas 3 linhas:")
            print(df.tail(3).to_string(index=False))
        print()
