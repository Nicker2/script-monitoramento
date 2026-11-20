"""LEGADO: mantido para as máquinas que ainda chamam este arquivo.

Só abre o ponto de entrada único (iniciar_monitor.pyw) sem janela e sai.
"""
import os
import subprocess
import sys

pasta = os.path.dirname(os.path.abspath(__file__))
interpretador = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.isfile(interpretador):
    interpretador = sys.executable
subprocess.Popen([interpretador, os.path.join(pasta, "iniciar_monitor.pyw")],
                 cwd=pasta, creationflags=0x08000000)
