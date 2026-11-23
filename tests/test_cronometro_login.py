"""Cronômetro de login (2.3.3): do início de qualquer tentativa de login até o login OK.
Passou de login_timeout_seg (120 s) = reinicia o programa pelo caminho do "Reiniciar monitor".
Sem teto por hora (2.3.3): só o intervalo mínimo de 30 s entre reinícios.
Tudo com login/driver falsos e relógio adiantado: nada espera 2 min de verdade,
nenhum Chrome é aberto e o Feegow não é acessado."""
import threading
import time

import pytest

from conftest import SaidaInterceptada  # noqa: F401  (usado pelo fixture saidas)

MSG = "⏱️ Login demorou mais de 2 min: reiniciando o programa"


@pytest.fixture
def m(monitor, monkeypatch, tmp_path):
    agora = time.time()
    monkeypatch.setattr(monitor, "ARQ_ESTADO_WATCHDOG", str(tmp_path / "watchdog_estado.json"))
    monkeypatch.setattr(monitor, "_LOGIN_INICIO", 0.0)
    monkeypatch.setattr(monitor, "_AVISO_CRONOMETRO", "")
    monkeypatch.setattr(monitor, "_ETAPA", "login")
    monkeypatch.setattr(monitor, "_FALHAS_CHROME", [])
    monkeypatch.setattr(monitor, "_ULTIMO_OK", agora)
    monkeypatch.setattr(monitor, "_MARCO_ATIVO", agora)
    monkeypatch.setattr(monitor, "_MARCO_ETAPA", agora)
    monkeypatch.setattr(monitor, "_STATUS", monitor._STATUS)
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    monkeypatch.setattr(monitor, "_ENCERRANDO", False)
    monkeypatch.setattr(monitor, "_CRED_RECUSADAS", False)
    monkeypatch.setattr(monitor, "_PEDIDO_REINICIO_CHROME", "")
    monkeypatch.setattr(monitor, "credenciais_incompletas", lambda: False)
    monkeypatch.setattr(monitor, "internet_ok", lambda timeout=5: True)
    monkeypatch.setattr(monitor, "_avisar_bandeja", lambda mensagem: None)
    monkeypatch.setattr(monitor, "_limpeza_para_sair", lambda: None)
    monkeypatch.setattr(monitor, "_salvar_pausa", lambda: None)
    monkeypatch.setattr(monitor, "chrome_vivo", lambda ping=True, timeout=8.0: (True, ""))
    monkeypatch.setattr(monitor, "_dormir", lambda segundos, progresso=False: None)
    monkeypatch.setattr(monitor, "carregar_credenciais", lambda: {
        "usuario": "recepcao@clinica.com.br", "senha": "x", "rota_login": "sso",
        "url_login_sso": "https://x/sso", "url_login": "https://x/login",
        "url_lista_espera": "https://x/listaespera"})
    logs = []
    monkeypatch.setattr(monitor, "log_msg", lambda texto: logs.append(texto))
    monitor._logs_teste = logs
    return monitor


def _login_travado(m, monkeypatch, resultado=(True, "login direto")):
    """fazer_login() numa thread com o login_sso pendurado (Chrome sem responder)."""
    liberar, entrou = threading.Event(), threading.Event()

    def login_sso_falso(usuario, senha, url):
        entrou.set()
        liberar.wait(10)
        return resultado
    monkeypatch.setattr(m, "login_sso", login_sso_falso)
    retorno = {}
    thread = threading.Thread(target=lambda: retorno.setdefault("ok", m.fazer_login()), daemon=True)
    thread.start()
    assert entrou.wait(5)
    return liberar, thread, retorno


def test_config_padrao(monitor):
    assert monitor.FILTROS_PADRAO["login_timeout_seg"] == 120
    assert monitor.carregar_filtros()["login_timeout_seg"] == 120
    assert "recuperacao_max_reinicios_por_hora" not in monitor.FILTROS_PADRAO


def test_login_travado_mais_de_2_min_reinicia(m, monkeypatch, saidas):
    """O watchdog (outra thread) vê o cronômetro estourar com o login pendurado."""
    monkeypatch.setattr(m, "_modo_http_em_uso", lambda: False)
    liberar, thread, _ = _login_travado(m, monkeypatch)
    try:
        inicio = m._LOGIN_INICIO
        assert inicio > 0
        assert m._checar_cronometro_login(inicio + 60) is False and saidas == []
        assert m._checar_cronometro_login(inicio + 121) is True
        assert saidas == [("relancar",)]               # mesmo caminho do "Reiniciar monitor"
        assert MSG in m._logs_teste
    finally:
        liberar.set()
        thread.join(5)


def test_login_que_termina_em_100_s_nao_reinicia(m, monkeypatch, saidas):
    monkeypatch.setattr(m, "_modo_http_em_uso", lambda: False)
    liberar, thread, retorno = _login_travado(m, monkeypatch)
    inicio = m._LOGIN_INICIO
    assert m._checar_cronometro_login(inicio + 100) is False
    liberar.set()
    thread.join(5)
    assert retorno["ok"] is True and m._LOGIN_INICIO == 0.0   # página logada = cronômetro parado
    assert m._checar_cronometro_login(inicio + 500) is False and saidas == []


def test_retentativas_contam_no_mesmo_cronometro(m, monkeypatch):
    m.iniciar_cronometro_login(agora=1000.0)
    m.iniciar_cronometro_login(agora=1090.0)          # nova tentativa/reabertura: não zera
    assert m._LOGIN_INICIO == 1000.0


def test_modo_requisicao_so_para_quando_o_token_chega(m, monkeypatch, saidas):
    monkeypatch.setattr(m, "_modo_http_em_uso", lambda: True)
    monkeypatch.setattr(m, "login_sso", lambda u, s, url: (True, "login direto"))
    assert m.fazer_login() is True
    assert m._LOGIN_INICIO > 0                         # logou, mas ainda sem token
    monkeypatch.setattr(m, "_obter_token_via_chrome", lambda evitar="": "")
    m._LOGIN_INICIO = time.time() - 50
    assert m.obter_token_via_chrome() == "" and m._LOGIN_INICIO > 0
    monkeypatch.setattr(m, "_obter_token_via_chrome", lambda evitar="": "tok.novo")
    assert m.obter_token_via_chrome() == "tok.novo" and m._LOGIN_INICIO == 0.0
    # relogin que nunca traz o token: estoura e reinicia
    monkeypatch.setattr(m, "_obter_token_via_chrome", lambda evitar="": "")
    m.obter_token_via_chrome()
    assert m._checar_cronometro_login(m._LOGIN_INICIO + 130) is True and saidas == [("relancar",)]


def test_ciclo_ok_para_o_cronometro(m):
    m.iniciar_cronometro_login(agora=time.time() - 30)
    m.registrar_batimento(ok=True)
    assert m._LOGIN_INICIO == 0.0


def test_credenciais_de_exemplo_nao_reiniciam(m, monkeypatch, saidas):
    monkeypatch.setattr(m, "solicitar_credenciais", lambda motivo="": None)
    monkeypatch.setattr(m, "carregar_credenciais", lambda: {
        "usuario": m.CREDENCIAIS_PADRAO["usuario"], "senha": "", "rota_login": "auto",
        "url_login_sso": "https://x/sso", "url_login": "https://x/login"})
    assert m.fazer_login() is False and m._LOGIN_INICIO == 0.0   # nem começa
    # cronômetro correndo e o login virou exemplo/vazio no meio: só avisa (uma vez)
    m._LOGIN_INICIO = time.time() - 300
    monkeypatch.setattr(m, "credenciais_incompletas", lambda: True)
    for _ in range(3):
        assert m._checar_cronometro_login(time.time()) is False
    assert saidas == []
    avisos = [t for t in m._logs_teste if "reiniciar não adianta" in t]
    assert len(avisos) == 1


def test_senha_recusada_para_o_cronometro(m, monkeypatch):
    monkeypatch.setattr(m, "solicitar_credenciais", lambda motivo="": None)
    monkeypatch.setattr(m, "login_sso", lambda u, s, url: (False, "senha incorreta no SSO"))
    assert m.fazer_login() is False and m._LOGIN_INICIO == 0.0


def test_durante_alarme_espera(m, monkeypatch, saidas):
    m._LOGIN_INICIO = time.time() - 200
    monkeypatch.setattr(m, "alarme_ativo", True)
    assert m._checar_cronometro_login(time.time()) is False and saidas == []
    monkeypatch.setattr(m, "alarme_ativo", False)
    trava = m._TRAVA_AUDIO
    trava.acquire()                                    # voz falando
    try:
        assert m._checar_cronometro_login(time.time()) is False and saidas == []
    finally:
        trava.release()
    assert m._checar_cronometro_login(time.time()) is True and saidas == [("relancar",)]


def test_sem_internet_nao_reinicia_e_nao_conta(m, monkeypatch, saidas):
    m._LOGIN_INICIO = time.time() - 300
    monkeypatch.setattr(m, "internet_ok", lambda timeout=5: False)
    assert m._checar_cronometro_login(time.time()) is False and saidas == []
    assert time.time() - m._LOGIN_INICIO < 5            # o tempo offline não conta


def test_sem_internet_o_login_nem_comeca(m, monkeypatch):
    chamadas = []
    monkeypatch.setattr(m, "internet_ok", lambda timeout=5: False)
    monkeypatch.setattr(m, "aguardar_internet", lambda: monkeypatch.setattr(m, "_ENCERRANDO", True))
    monkeypatch.setattr(m, "fazer_login", lambda: chamadas.append(1) or True)
    assert m.executar_sessao_chrome() is None
    assert chamadas == [] and m._LOGIN_INICIO == 0.0


def test_etapas_sem_login_zeram(m):
    for etapa in ("aguardando_login", "fora_horario", "parado"):
        m._LOGIN_INICIO = time.time() - 500
        m._definir_etapa(etapa)
        assert m._LOGIN_INICIO == 0.0
        m._definir_etapa("login")


def test_regra_pura(monitor):
    cfg = {"login_timeout_seg": 120}
    decide = monitor.decidir_cronometro_login
    assert decide(cfg, 1000, 0, "login")[0] == "nao"
    assert decide(cfg, 1100, 1000, "login")[0] == "nao"
    assert decide(cfg, 1121, 1000, "login")[0] == "reiniciar"
    assert decide(cfg, 1121, 1000, "reabrindo")[0] == "reiniciar"
    assert decide(cfg, 1121, 1000, "offline")[0] == "nao"
    assert decide(cfg, 1121, 1000, "login", alarme=True)[0] == "adiar"
    assert decide(cfg, 1121, 1000, "login", falando=True)[0] == "adiar"
    assert decide(cfg, 1121, 1000, "login", cred_invalidas=True)[0] == "avisar"
    assert decide({"login_timeout_seg": 300}, 1200, 1000, "login")[0] == "nao"


# --- sem teto por hora (2.3.3): só o intervalo mínimo de 30 s ---------------------------------
def test_sem_limite_por_hora_so_intervalo_minimo(m):
    t = 1_000_000.0
    for i in range(12):                                # 12 reinícios numa hora: todos liberados
        assert m._pode_reiniciar_processo(agora=t + i * 31) is True
    ultimo = t + 11 * 31
    assert m._pode_reiniciar_processo(agora=ultimo + 10) is False      # < 30 s do último
    assert m._reinicio_bloqueado_ate(ultimo + 10) == pytest.approx(ultimo + 30)
    assert m._pode_reiniciar_processo(agora=ultimo + 31) is True


def test_cronometro_estoura_varias_vezes_na_hora(m, monkeypatch, saidas):
    """Seis estouros na mesma hora (espaçados): seis reinícios, sem espera de 10 min."""
    base = time.time()
    for i in range(6):
        monkeypatch.setattr(m, "_ENCERRANDO", False)
        agora = base + i * 200
        monkeypatch.setattr(m.time, "time", lambda agora=agora: agora)
        m._LOGIN_INICIO = agora - 150
        assert m._checar_cronometro_login(agora) is True
    assert saidas == [("relancar",)] * 6


def test_supervisor_sem_teto_so_intervalo(supervisor, monkeypatch):
    monkeypatch.setattr(supervisor, "_ULTIMA_MORTE", {"ts": 0.0})
    t = 1_000_000.0
    assert all(supervisor.pode_forcar_reinicio(t + i * 31) for i in range(10))
    assert not supervisor.pode_forcar_reinicio(t + 9 * 31 + 5)
    assert supervisor.pode_forcar_reinicio(t + 9 * 31 + 31)
