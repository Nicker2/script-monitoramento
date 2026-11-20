@echo off
rem Roda o monitor COM console, para ver os erros na tela (depuracao).
set "PY=D:\Meus Documentos\Documentos\Nova pasta\Winpython64-3.12.4.1\WPy64-31241\python-3.12.4.amd64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0SCRIPT_EXPLICA.py"
pause
