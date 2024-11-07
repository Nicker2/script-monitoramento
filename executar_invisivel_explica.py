import os
import subprocess
import sys
import traceback
import time

# Caminho do interpretador Python e do script que você quer executar
python_exe = r"D:\Meus Documentos\Documentos\Nova pasta\Winpython64-3.12.4.1\WPy64-31241\python-3.12.4.amd64\python.exe"
script_py = r"D:\Meus Documentos\Documentos\Nova pasta\SCRIPT VERIFICACAO\EXPLICA\SCRIPT_EXPLICA.py"

# Caminho para log de erros
error_log_path = os.path.join(os.path.dirname(script_py), "error_log.txt")

while True:
    try:
        print("Verificando se o executável do Python existe...")
        if not os.path.isfile(python_exe):
            raise FileNotFoundError(f"O executável do Python não foi encontrado: {python_exe}")

        print("Verificando se o script existe...")
        if not os.path.isfile(script_py):
            raise FileNotFoundError(f"O script Python não foi encontrado: {script_py}")

        # Configura o subprocesso para ser executado de maneira oculta
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Executa o script Python de maneira oculta
        print(f"Iniciando o script: {script_py}")
        process = subprocess.Popen([python_exe, script_py], startupinfo=si, creationflags=subprocess.CREATE_NO_WINDOW)
        process.wait()

        if process.returncode != 0:
            raise Exception("O script Python retornou um código de erro: {}".format(process.returncode))

    except Exception as e:
        with open(error_log_path, "a") as f:
            f.write("Ocorreu um erro ao tentar executar o script:\n")
            f.write(traceback.format_exc())
            f.write("\n")
        print("Ocorreu um erro:", e)  # Mostra o erro no console

    print("Reiniciando o script após 5 segundos...")
    time.sleep(5)  # Aguarda 5 segundos antes de reiniciar
