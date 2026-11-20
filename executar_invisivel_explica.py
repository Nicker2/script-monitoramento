"""LEGADO: mantido para as máquinas que ainda chamam este arquivo.

O antigo laço "roda o SCRIPT_EXPLICA.py e reinicia em 5 s" agora é o
iniciar_monitor.pyw (que também trata reinício pedido, atualização e log).
Este arquivo só executa o supervisor novo no mesmo processo.
"""
import os
import runpy
import sys

pasta = os.path.dirname(os.path.abspath(__file__))
sys.argv = [os.path.join(pasta, "iniciar_monitor.pyw")] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
