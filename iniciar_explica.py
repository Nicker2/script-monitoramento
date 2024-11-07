import os
import subprocess

# Caminho do executável do Python
python_exe = r"D:\Meus Documentos\Documentos\Nova pasta\Winpython64-3.12.4.1\WPy64-31241\python-3.12.4.amd64\python.exe"
# Caminho do script que você deseja executar
script_invisivel = r"D:\Meus Documentos\Documentos\Nova pasta\SCRIPT VERIFICACAO\EXPLICA\executar_invisivel_explica.py"

if not os.path.isfile(python_exe):
    raise FileNotFoundError(f"O executável do Python não foi encontrado: {python_exe}")

if not os.path.isfile(script_invisivel):
    raise FileNotFoundError(f"O script invisível não foi encontrado: {script_invisivel}")

# Executa o script invisível e fecha imediatamente
subprocess.Popen([python_exe, script_invisivel], creationflags=subprocess.CREATE_NO_WINDOW)
