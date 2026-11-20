@echo off
rem LEGADO: mantido para as maquinas que ja usam este caminho.
rem Agora so abre o ponto de entrada unico (iniciar_monitor.pyw), sem janela.
setlocal
set "PYARG="
call :achar_python pythonw.exe pyw.exe
start "" "%PY%" %PYARG% "%~dp0iniciar_monitor.pyw"
goto :eof
:achar_python
rem Procura o Python sem caminho fixo: WinPython na pasta do monitor (ou 1-2 niveis
rem acima), depois o "py" launcher, depois o que estiver no PATH.
set "PY="
for %%B in ("%~dp0." "%~dp0.." "%~dp0..\..") do (
  for /d %%W in ("%%~fB\Winpython*") do (
    for /d %%Y in ("%%~W\WPy64-*") do (
      for /d %%P in ("%%~Y\python-*") do (
        if not defined PY if exist "%%~P\%~1" set "PY=%%~P\%~1"
      )
    )
  )
)
if defined PY goto :eof
where %~2 >nul 2>&1 && (set "PY=%~2" & set "PYARG=-3" & goto :eof)
set "PY=%~1"
goto :eof