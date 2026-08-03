@echo off
REM =============================================================================
REM  iniciar_sistema.bat — sobe o Super Quant (skin classico)
REM =============================================================================
REM  dashboard.py (porta 8050) e' o processo PAI: ele mesmo lanca o filho
REM  diario_app_classico.py (porta 8053) sozinho no startup (gerenciador_
REM  processos.garantir_processo_filho), entao so precisamos iniciar este.
REM  Da' dois cliques neste arquivo (ou "iniciar_sistema.bat" no terminal)
REM  para subir o sistema sem precisar pedir pelo Claude Code toda vez.
REM =============================================================================

cd /d "%~dp0"

echo Verificando se o Dashboard (porta 8050) ja esta rodando...
netstat -ano | findstr ":8050" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo Dashboard ja esta rodando em http://127.0.0.1:8050 — nao vou abrir outro.
    goto abrir_navegador
)

echo Iniciando dashboard.py em uma nova janela...
start "Super Quant - Dashboard (8050)" cmd /k python dashboard.py

echo Aguardando o servidor subir...
timeout /t 6 /nobreak >nul

:abrir_navegador
echo Abrindo o navegador...
start "" "http://127.0.0.1:8050"

echo.
echo Sistema iniciado. O Diario de Trade (porta 8053) sobe sozinho, embutido
echo dentro do Dashboard. Feche a janela do terminal "Super Quant - Dashboard"
echo para encerrar o sistema.
echo.
pause
