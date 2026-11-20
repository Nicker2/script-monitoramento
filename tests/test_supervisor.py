"""iniciar_monitor.pyw de verdade, com um SCRIPT_EXPLICA.py falso ao lado."""
import os
import shutil
import subprocess
import sys
import textwrap
import time

from conftest import RAIZ


def _preparar(tmp_path, corpo):
    shutil.copy(os.path.join(RAIZ, "iniciar_monitor.pyw"), tmp_path)
    (tmp_path / "SCRIPT_EXPLICA.py").write_text(textwrap.dedent(corpo), encoding="utf-8")
    return tmp_path


def _rodar(pasta, **ambiente_extra):
    ambiente = dict(os.environ, **ambiente_extra)
    ambiente.pop("MONITOR_SUPERVISOR", None)
    return subprocess.run([sys.executable, str(pasta / "iniciar_monitor.pyw"), "--console"],
                          capture_output=True, text=True, errors="replace", timeout=180, env=ambiente)


def test_75_relanca_queda_espera_0_para(tmp_path):
    contador = tmp_path / "contador.txt"
    pasta = _preparar(tmp_path, f"""
        import os, sys
        c = r'{contador}'
        n = int(open(c).read()) if os.path.exists(c) else 0
        open(c, 'w').write(str(n + 1))
        assert os.environ.get('MONITOR_SUPERVISOR') == '1'
        sys.exit(75 if n == 0 else (1 if n == 1 else 0))
    """)
    r = _rodar(pasta)
    log = (pasta / "logs" / "supervisor.log").read_text(encoding="utf-8")
    assert r.returncode == 0 and contador.read_text() == "3"
    assert "pediu reinício" in log and "caiu" in log and "encerrado pelo usuário" in log


def test_heartbeat_parado_mata_e_relanca(tmp_path):
    contador = tmp_path / "contador.txt"
    logs = tmp_path / "logs"
    pasta = _preparar(tmp_path, f"""
        import os, sys, json, time
        c = r'{contador}'
        n = int(open(c).read()) if os.path.exists(c) else 0
        open(c, 'w').write(str(n + 1))
        if n >= 1:
            sys.exit(0)
        os.makedirs(r'{logs}', exist_ok=True)
        json.dump({{"pid": os.getpid(), "ts": time.time()}}, open(os.path.join(r'{logs}', 'heartbeat.json'), 'w'))
        time.sleep(600)
    """)
    inicio = time.time()
    r = _rodar(pasta, MONITOR_SUP_LIMITE_HEARTBEAT="4", MONITOR_SUP_FOLGA="1", MONITOR_SUP_INTERVALO="1")
    log = (logs / "supervisor.log").read_text(encoding="utf-8")
    assert "sem heartbeat" in log
    assert contador.read_text() == "2" and r.returncode == 0
    assert time.time() - inicio < 120


def test_sem_heartbeat_do_pid_nunca_mata(supervisor, tmp_path):
    supervisor.HEARTBEAT = str(tmp_path / "heartbeat.json")
    assert not supervisor.heartbeat_parado(12345, 0, time.time())          # arquivo não existe
    (tmp_path / "heartbeat.json").write_text('{"pid": 999, "ts": 0}')
    assert not supervisor.heartbeat_parado(12345, 0, time.time())          # é de outro processo
    assert supervisor.heartbeat_parado(999, 0, time.time())                # é dele e está velho
    assert not supervisor.heartbeat_parado(999, time.time() + 60, time.time())   # ainda na folga
