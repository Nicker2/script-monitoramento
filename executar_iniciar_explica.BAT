@echo off
rem LEGADO: mantido para as maquinas que ja usam este caminho.
rem Agora so abre o ponto de entrada unico (iniciar_monitor.pyw), sem janela.
set "PYW=D:\Meus Documentos\Documentos\Nova pasta\Winpython64-3.12.4.1\WPy64-31241\python-3.12.4.amd64\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0iniciar_monitor.pyw"
