"""Escada de recuperação (2.2.2): Chrome morto -> recria; falha repetida -> reinicia
o programa; intervalo mínimo entre reinícios (2.3.3: sem teto por hora); nunca durante alarme; supervisor relança.
Tudo com driver/processos falsos: nenhum Chrome é aberto e o Feegow não é acessado."""
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time

import pytest
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError

from conftest import RAIZ, SaidaInterceptada


def _erro_porta_fechada():
    """O erro que o Selenium devolve quando o chromedriver morreu."""
    motivo = NewConnectionError(None, "Failed to establish a new connection: [WinError 10061] recusou")
    return MaxRetryError(None, "/session/abc/title", reason=motivo)


class DriverFalso:
    """Chromedriver morto: toda chamada estoura como no Selenium de verdade."""
    def __init__(self, erro=None, demora=0.0):
        self.erro = erro
        self.demora = demora
        self.chamadas = 0

    def _chamar(self, *_a, **_k):
        self.chamadas += 1
        if self.demora:
            time.sleep(self.demora)
        if self.erro is not None:
            raise self.erro
        return "Lista de Espera"

    title = property(lambda self: self._chamar())
    execute_async_script = _chamar
    execute_script = _chamar
    find_elements = _chamar
    get = _chamar


@pytest.fixture
def estado_limpo(monitor, monkeypatch, tmp_path):
    """Isola os globais e o watchdog_estado.json de cada teste."""
    agora = time.time()
    monkeypatch.setattr(monitor, "ARQ_ESTADO_WATCHDOG", str(tmp_path / "watchdog_estado.json"))
    monkeypatch.setattr(monitor, "_FALHAS_CHROME", [])
    monkeypatch.setattr(monitor, "_ETAPA", "monitorando")
    monkeypatch.setattr(monitor, "_ULTIMO_OK", agora)
    monkeypatch.setattr(monitor, "_MARCO_ATIVO", agora)
    monkeypatch.setattr(monitor, "_MARCO_ETAPA", agora)
    monkeypatch.setattr(monitor, "_AVISO_RECUPERACAO", "")
    monkeypatch.setattr(monitor, "_STATUS", monitor._STATUS)
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    monkeypatch.setattr(monitor, "_ENCERRANDO", False)
    monkeypatch.setattr(monitor, "_PEDIDO_REINICIO_CHROME", "")
    monkeypatch.setattr(monitor, "_PID_CHROMEDRIVER", 0)
    monkeypatch.setattr(monitor, "driver", None)
    monkeypatch.setattr(monitor, "internet_ok", lambda timeout=5: True)
    monkeypatch.setattr(monitor, "_avisar_bandeja", lambda mensagem: None)
    return monitor


# --- detecção ---------------------------------------------------------------------------
def test_erros_de_chrome_morto(monitor):
    from selenium.common.exceptions import WebDriverException, TimeoutException
    assert monitor._erro_chrome_morto(_erro_porta_fechada())
    assert monitor._erro_chrome_morto(ConnectionRefusedError(10061, "recusou"))
    assert monitor._erro_chrome_morto(ProtocolError("Connection aborted.", ConnectionResetError()))
    assert monitor._erro_chrome_morto(WebDriverException("chrome not reachable"))
    assert monitor._erro_chrome_morto(WebDriverException("disconnected: not connected to DevTools"))
    assert monitor._erro_chrome_morto("erro inesperado: HTTPConnectionPool(...): Max retries exceeded")
    embrulhado = RuntimeError("falhou")
    embrulhado.__cause__ = _erro_porta_fechada()
    assert monitor._erro_chrome_morto(embrulhado)
    assert not monitor._erro_chrome_morto(TimeoutException("botão não apareceu"))
    assert not monitor._erro_chrome_morto("campo de e-mail não apareceu na tela do SSO")


def test_chrome_vivo_ping(estado_limpo, monkeypatch):
    m = estado_limpo
    assert m.chrome_vivo() == (False, "o navegador não está aberto")
    monkeypatch.setattr(m, "driver", DriverFalso())
    assert m.chrome_vivo() == (True, "")
    monkeypatch.setattr(m, "driver", DriverFalso(_erro_porta_fechada()))
    vivo, motivo = m.chrome_vivo()
    assert not vivo and "MaxRetryError" in motivo
    monkeypatch.setattr(m, "driver", DriverFalso(demora=3))
    inicio = time.time()
    vivo, motivo = m.chrome_vivo(timeout=0.3)
    assert not vivo and "não respondeu" in motivo and time.time() - inicio < 2


def test_chrome_vivo_pelo_processo(estado_limpo, monkeypatch):
    m = estado_limpo
    monkeypatch.setattr(m, "driver", DriverFalso())
    monkeypatch.setattr(m, "_PID_CHROMEDRIVER", 4242)

    def processo_sumiu(pid):
        raise m.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(m.psutil, "Process", processo_sumiu)
    vivo, motivo = m.chrome_vivo(ping=False)
    assert not vivo and "4242" in motivo

    class FilhoFalso:
        def __init__(self, nome):
            self._nome = nome

        def name(self):
            return self._nome

    class DriverProcessoFalso:
        filhos = []

        def __init__(self, pid):
            pass

        def is_running(self):
            return True

        def name(self):
            return "chromedriver.exe"

        def status(self):
            return "running"

        def children(self, recursive=False):
            return self.filhos
    monkeypatch.setattr(m.psutil, "Process", DriverProcessoFalso)
    monkeypatch.setattr(m, "_chrome_do_perfil_aberto", lambda: False)
    vivo, motivo = m.chrome_vivo(ping=False)
    assert not vivo and "Chrome fechou" in motivo
    DriverProcessoFalso.filhos = [FilhoFalso("chrome.exe")]
    assert m.chrome_vivo(ping=False) == (True, "")


# --- degrau N2: Chrome morto -> recria o driver -------------------------------------------
def test_laco_principal_devolve_para_recriar(estado_limpo, monkeypatch):
    m = estado_limpo
    monkeypatch.setattr(m, "driver", DriverFalso(_erro_porta_fechada()))
    monkeypatch.setattr(m, "carregar_credenciais", lambda: {"url_lista_espera": "https://x/listaespera"})
    monkeypatch.setattr(m, "abrir_lista_espera", lambda url: None)
    monkeypatch.setattr(m, "fechar_modal", lambda: None)
    motivo = m.verificar_botao_chamar_senha()
    assert motivo and "Chrome parou de responder" in motivo


def test_chrome_morto_recria_e_relogga(estado_limpo, monkeypatch):
    m = estado_limpo
    sessoes, fechamentos = [], []
    respostas = iter(["Chrome ausente (o chromedriver (PID 1) não existe mais)", None])
    monkeypatch.setattr(m, "dentro_do_horario", lambda cfg=None, agora=None: True)
    monkeypatch.setattr(m, "executar_sessao_chrome", lambda: sessoes.append(1) or next(respostas))
    monkeypatch.setattr(m, "_fechar_chrome", lambda: fechamentos.append(1))
    monkeypatch.setattr(m, "_proxima_espera_reinicio", lambda: 0)
    monkeypatch.setattr(m, "_dormir", lambda segundos, progresso=False: None)
    monkeypatch.setattr(m, "_CRED_RECUSADAS", False)
    monkeypatch.setattr(m, "_REINICIO_SEM_ESPERA", False)
    m.loop_supervisionado()
    assert len(sessoes) == 2 and len(fechamentos) == 2      # fechou os restos e abriu de novo
    assert len(m._FALHAS_CHROME) == 1
    m.registrar_batimento(ok=True)                           # ciclo OK zera a escada
    assert m._FALHAS_CHROME == []


def test_watchdog_ve_chromedriver_sumido(estado_limpo, monkeypatch):
    m = estado_limpo
    mortos = []
    monkeypatch.setattr(m, "driver", DriverFalso())
    monkeypatch.setattr(m, "_SELENIUM_RODANDO", True)
    monkeypatch.setattr(m, "_MONITOR_DESEJADO", False)
    monkeypatch.setattr(m, "_ULTIMO_PROGRESSO", time.time())
    monkeypatch.setattr(m, "chrome_vivo", lambda ping=True, timeout=8.0: (False, "o Chrome fechou"))
    monkeypatch.setattr(m, "_matar_chromedriver_por_pid", lambda: mortos.append(1))
    m._passo_watchdog(time.time())
    assert m._PEDIDO_REINICIO_CHROME.startswith("Chrome ausente") and mortos == [1]


def test_login_nao_insiste_com_chrome_morto(estado_limpo, monkeypatch):
    m = estado_limpo
    tentativas = []
    monkeypatch.setattr(m, "carregar_credenciais", lambda: {
        "usuario": "teste@exemplo.com", "senha": "x", "rota_login": "sso",
        "url_login_sso": "https://x/sso", "url_login": "https://x/login"})
    monkeypatch.setattr(m, "login_sso", lambda u, s, url: tentativas.append(1) or (False, "a etapa da senha não apareceu"))
    monkeypatch.setattr(m, "chrome_vivo", lambda ping=True, timeout=8.0: (False, "o Chrome fechou"))
    monkeypatch.setattr(m, "_dormir", lambda segundos, progresso=False: None)
    assert m.fazer_login() is False and tentativas == [1]


# --- degrau N3: falha repetida -> reinicia o programa ----------------------------------------
def test_regra_do_reinicio_por_falha(monitor):
    cfg = dict(monitor.FILTROS_PADRAO)
    agora = 100_000.0
    decide = monitor.decidir_reinicio_por_falha
    ok = agora - 10
    assert decide(cfg, agora, "monitorando", ok, ok, ok, [])[0] == "nao"
    assert decide(cfg, agora, "reabrindo", ok, ok, ok, [agora - 60, agora - 30])[0] == "nao"
    acao, motivo = decide(cfg, agora, "reabrindo", ok, ok, ok, [agora - 90, agora - 60, agora - 30])
    assert acao == "reiniciar" and "3 reaberturas" in motivo
    # falhas antigas (fora da janela de 10 min) não contam
    assert decide(cfg, agora, "reabrindo", ok, ok, ok, [agora - 900, agora - 800, agora - 30])[0] == "nao"
    # monitorando sem ciclo OK há mais de 3 min
    assert decide(cfg, agora, "monitorando", agora - 200, 0, agora - 200, [])[0] == "reiniciar"
    assert decide(cfg, agora, "monitorando", agora - 120, 0, agora - 120, [])[0] == "nao"
    # login/abrindo: só o limite total (10 min)
    assert decide(cfg, agora, "login", agora - 300, agora - 300, agora - 300, [])[0] == "nao"
    assert decide(cfg, agora, "login", 0, agora - 700, agora - 60, [])[0] == "reiniciar"
    # etapas pausadas nunca reiniciam
    for etapa in ("offline", "fora_horario", "aguardando_login", "parado"):
        assert decide(cfg, agora, etapa, 0, 0, 0, [agora] * 5)[0] == "nao"


def test_falha_repetida_reinicia_o_programa(estado_limpo, monkeypatch, saidas):
    m = estado_limpo
    agora = time.time()
    monkeypatch.setattr(m, "_ETAPA", "reabrindo")
    monkeypatch.setattr(m, "_FALHAS_CHROME", [agora - 120, agora - 60, agora - 5])
    monkeypatch.setattr(m, "_limpeza_para_sair", lambda: None)
    monkeypatch.setattr(m, "_salvar_pausa", lambda: None)
    assert m._checar_reinicio_por_falha(agora) is True
    assert saidas == [("relancar",)]       # mesmo caminho do "Reiniciar monitor"
    estado = json.loads(open(m.ARQ_ESTADO_WATCHDOG, encoding="utf-8").read())
    assert len(estado["reinicios_processo"]) == 1


def test_sem_internet_nao_reinicia(estado_limpo, monkeypatch):
    m = estado_limpo
    chamadas = []
    monkeypatch.setattr(m, "_ETAPA", "monitorando")
    monkeypatch.setattr(m, "_ULTIMO_OK", time.time() - 600)
    monkeypatch.setattr(m, "_MARCO_ETAPA", time.time() - 600)
    monkeypatch.setattr(m, "internet_ok", lambda timeout=5: False)
    monkeypatch.setattr(m, "reiniciar_processo", lambda motivo, ignorar_limite=False, **_k: chamadas.append(motivo))
    assert m._checar_reinicio_por_falha(time.time()) is False and chamadas == []


# --- sem teto por hora (2.3.3): só o intervalo mínimo de 30 s ------------------------------------
def test_sem_limite_de_reinicios_por_hora(estado_limpo):
    m = estado_limpo
    t = 1_000_000.0
    for i in range(10):
        assert m._pode_reiniciar_processo(agora=t + i * 40) is True      # 10 na hora: todos
    assert m._pode_reiniciar_processo(agora=t + 9 * 40 + 20) is False    # < 30 s do último
    assert m._pode_reiniciar_processo(agora=t + 9 * 40 + 30) is True


def test_intervalo_minimo_avisa_uma_vez_e_nao_reinicia(estado_limpo, monkeypatch):
    m = estado_limpo
    agora = time.time()
    chamadas, logs = [], []
    m._gravar_estado_watchdog({"reinicios_processo": [agora - 10]})
    monkeypatch.setattr(m, "_ETAPA", "reabrindo")
    monkeypatch.setattr(m, "_FALHAS_CHROME", [agora - 30] * 3)
    monkeypatch.setattr(m, "reiniciar_processo", lambda motivo, ignorar_limite=False, **_k: chamadas.append(motivo))
    monkeypatch.setattr(m, "log_msg", lambda texto: logs.append(texto))
    for _ in range(4):
        assert m._checar_reinicio_por_falha(agora) is False
    assert chamadas == [] and len(logs) == 1 and "30 s" in logs[0]
    # passou dos 30 s do último reinício: reinicia
    assert m._checar_reinicio_por_falha(agora + 25) is None and len(chamadas) == 1


def test_estado_antigo_com_bloqueio_da_222_nao_segura(estado_limpo):
    """watchdog_estado.json gravado pela 2.3.2 (teto por hora + 10 min) não segura mais."""
    m = estado_limpo
    agora = time.time()
    m._gravar_estado_watchdog({"reinicios_processo": [agora - 100] * 5, "reinicio_bloqueado_ate": agora + 600})
    assert m._pode_reiniciar_processo(agora=agora) is True


# --- nunca no meio de um alarme ------------------------------------------------------------------
def test_nao_reinicia_durante_alarme(estado_limpo, monkeypatch):
    m = estado_limpo
    agora = time.time()
    chamadas = []
    monkeypatch.setattr(m, "_ETAPA", "reabrindo")
    monkeypatch.setattr(m, "_FALHAS_CHROME", [agora - 30] * 3)
    monkeypatch.setattr(m, "reiniciar_processo", lambda motivo, ignorar_limite=False, **_k: chamadas.append(motivo) or True)
    monkeypatch.setattr(m, "alarme_ativo", True)
    assert m._checar_reinicio_por_falha(agora) is False and chamadas == []
    trava = m._TRAVA_AUDIO
    monkeypatch.setattr(m, "alarme_ativo", False)
    trava.acquire()                                    # fala em andamento
    try:
        assert m._checar_reinicio_por_falha(agora) is False and chamadas == []
    finally:
        trava.release()
    assert m._checar_reinicio_por_falha(agora) is True and len(chamadas) == 1


def test_heartbeat_leva_dados_para_o_supervisor(estado_limpo, monkeypatch, tmp_path):
    m = estado_limpo
    monkeypatch.setattr(m, "ARQ_HEARTBEAT", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(m, "ARQ_STATUS", str(tmp_path / "status.json"))
    m.gravar_heartbeat()
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["ativo"] is True and hb["alarme"] is False and hb["ok_ref_ts"] > 0


# --- supervisor ----------------------------------------------------------------------------------
def test_supervisor_sem_ciclo_ok_e_limite(supervisor, tmp_path, monkeypatch):
    supervisor.HEARTBEAT = str(tmp_path / "heartbeat.json")
    agora = time.time()

    def hb(**campos):
        (tmp_path / "heartbeat.json").write_text(json.dumps(dict({"pid": 7, "ts": agora}, **campos)))
    hb()                                                               # monitor antigo: sem os campos
    assert not supervisor.sem_ciclo_ok_demais(7, 0, agora)
    hb(ativo=True, alarme=False, ok_ref_ts=agora - 4000)
    assert supervisor.sem_ciclo_ok_demais(7, 0, agora)
    assert not supervisor.sem_ciclo_ok_demais(8, 0, agora)             # outro processo
    hb(ativo=True, alarme=True, ok_ref_ts=agora - 4000)
    assert not supervisor.sem_ciclo_ok_demais(7, 0, agora)             # alarme tocando: não mexe
    hb(ativo=False, alarme=False, ok_ref_ts=agora - 4000)
    assert not supervisor.sem_ciclo_ok_demais(7, 0, agora)             # offline/fora do horário

    monkeypatch.setattr(supervisor, "_ULTIMA_MORTE", {"ts": 0.0})
    monkeypatch.setattr(supervisor, "log", lambda texto: None)
    t = 1_000_000.0
    assert all(supervisor.pode_forcar_reinicio(t + i * 30) for i in range(8))   # sem teto por hora
    assert not supervisor.pode_forcar_reinicio(t + 7 * 30 + 10)                  # só os 30 s mínimos
    assert supervisor.pode_forcar_reinicio(t + 7 * 30 + 30)


def test_supervisor_relanca_monitor_ativo_sem_ciclo_ok(tmp_path):
    """iniciar_monitor.pyw de verdade: heartbeat andando, mas sem ciclo OK -> relança."""
    shutil.copy(os.path.join(RAIZ, "iniciar_monitor.pyw"), tmp_path)
    contador, logs = tmp_path / "contador.txt", tmp_path / "logs"
    (tmp_path / "SCRIPT_EXPLICA.py").write_text(textwrap.dedent(f"""
        import os, sys, json, time
        c = r'{contador}'
        n = int(open(c).read()) if os.path.exists(c) else 0
        open(c, 'w').write(str(n + 1))
        if n >= 1:
            sys.exit(0)
        os.makedirs(r'{logs}', exist_ok=True)
        inicio = time.time()
        while time.time() - inicio < 120:
            json.dump({{"pid": os.getpid(), "ts": time.time(), "ativo": True, "alarme": False,
                        "ok_ref_ts": inicio - 100}}, open(os.path.join(r'{logs}', 'heartbeat.json'), 'w'))
            time.sleep(0.3)
    """), encoding="utf-8")
    ambiente = dict(os.environ, MONITOR_SUP_LIMITE_SEM_OK="5", MONITOR_SUP_FOLGA="1", MONITOR_SUP_INTERVALO="1")
    ambiente.pop("MONITOR_SUPERVISOR", None)
    inicio = time.time()
    r = subprocess.run([sys.executable, str(tmp_path / "iniciar_monitor.pyw"), "--console"],
                       capture_output=True, text=True, errors="replace", timeout=180, env=ambiente)
    log = (logs / "supervisor.log").read_text(encoding="utf-8")
    assert "sem nenhum ciclo OK" in log
    assert contador.read_text() == "2" and r.returncode == 0
    assert time.time() - inicio < 100
