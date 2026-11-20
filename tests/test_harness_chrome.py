"""Bateria completa com Chrome (headless + Chrome real fora da tela) e Feegow SIMULADO local.

Pesada: só roda com MONITOR_TESTES_CHROME=1 (não roda no CI por padrão).
    set MONITOR_TESTES_CHROME=1 && python -m pytest tests -k harness -s
"""
import os
import subprocess
import sys

import pytest

AQUI = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(os.environ.get("MONITOR_TESTES_CHROME") != "1",
                    reason="defina MONITOR_TESTES_CHROME=1 para rodar a bateria com Chrome")
def test_harness_completo():
    r = subprocess.run([sys.executable, os.path.join(AQUI, "harness", "teste_monitor.py")],
                       capture_output=True, text=True, errors="replace", timeout=1500,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    print(r.stdout[-6000:])
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
