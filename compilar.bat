@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Compilando o Monitor de Senha do Feegow (Nuitka)
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale o Python 3.10+ e marque "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [1/3] Conferindo o Nuitka...
python -m nuitka --version >nul 2>&1
if errorlevel 1 (
    echo       instalando o Nuitka com pip...
    python -m pip install --upgrade nuitka
)

rem Versao: fonte unica = VERSAO no SCRIPT_EXPLICA.py
set "VERSAO_EXE="
for /f "usebackq delims=" %%v in (`python ferramentas\versao_atual.py --nuitka`) do set "VERSAO_EXE=%%v"
if not defined VERSAO_EXE (
    echo [ERRO] Nao consegui ler a VERSAO do SCRIPT_EXPLICA.py.
    pause
    exit /b 1
)

echo [2/3] Compilando a versao %VERSAO_EXE%... (a primeira vez baixa o compilador MinGW)
echo       Isso leva de 5 a 20 minutos. Pode deixar rodando.
echo.
python -m nuitka ^
  --onefile ^
  --assume-yes-for-downloads ^
  --mingw64 ^
  --enable-plugin=tk-inter ^
  --windows-disable-console ^
  --include-package=selenium ^
  --include-package=pygame ^
  --include-package=pystray ^
  --include-package=gtts ^
  --include-package-data=gtts ^
  --nofollow-import-to=pygame.tests ^
  --company-name="Monitor Feegow" ^
  --product-name="Monitor de Senha Feegow" ^
  --file-version=%VERSAO_EXE% --product-version=%VERSAO_EXE% ^
  --output-filename="MonitorFeegow.exe" ^
  --output-dir=dist ^
  SCRIPT_EXPLICA.py

if errorlevel 1 (
    echo.
    echo [ERRO] A compilacao falhou. Veja as mensagens acima.
    pause
    exit /b 1
)

echo.
echo [3/3] Pronto! O executavel esta em: dist\MonitorFeegow.exe
echo       Copie o exe para a pasta onde ficam filtros.json e credenciais.json.
echo.
if exist dist explorer dist
pause
