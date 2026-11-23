"""Testes de unidade do SCRIPT_EXPLICA.py (sem Chrome, sem Feegow, sem som)."""
import base64
import datetime as dt
import hashlib
import json
import os
import time

import pytest

from conftest import RAIZ, SaidaInterceptada


# --- filtros ----------------------------------------------------------------------
def test_normalizar_texto(monitor):
    assert monitor.normalizar_texto("DILATAÇÃO - CCC") == "DILATACAO CCC"
    assert monitor.normalizar_texto("RECEPÇÃO 2º ANDAR") == "RECEPCAO 2 ANDAR"
    assert monitor.normalizar_texto("  auto   refrator (sem dilatar) ") == "AUTO REFRATOR SEM DILATAR"


def test_hora_e_horario(monitor):
    assert monitor._hora_valida("3:00") == "03:00"
    assert monitor._hora_valida("25:00") == ""
    cfg = dict(monitor.FILTROS_PADRAO)
    assert monitor.dentro_do_horario(cfg, dt.datetime(2026, 11, 22, 3, 0))      # desligado = 24/7
    cfg["horario_funcionamento"] = monitor._horario_normalizado(
        {"ativo": True, "inicio": "07:00", "fim": "19:00", "dias": "seg,ter,qua,qui,sex"})
    assert monitor.dentro_do_horario(cfg, dt.datetime(2026, 11, 20, 10))         # sexta 10h
    assert not monitor.dentro_do_horario(cfg, dt.datetime(2026, 11, 20, 19, 30))
    assert not monitor.dentro_do_horario(cfg, dt.datetime(2026, 11, 21, 10))     # sábado
    cfg["horario_funcionamento"] = monitor._horario_normalizado(
        {"ativo": True, "inicio": "22:00", "fim": "06:00", "dias": ["sex"]})
    assert monitor.dentro_do_horario(cfg, dt.datetime(2026, 11, 21, 5))          # sáb 05h (virada de sexta)
    assert not monitor.dentro_do_horario(cfg, dt.datetime(2026, 11, 21, 23))     # sáb 23h


def test_reinicio_preventivo(monitor):
    cfg = dict(monitor.FILTROS_PADRAO, chrome_limite_ram_mb=0)
    agora = dt.datetime(2026, 11, 20, 3, 1).timestamp()
    assert "diário" in monitor.motivo_reinicio_preventivo(cfg, agora - 20 * 3600, agora)
    assert monitor.motivo_reinicio_preventivo(cfg, agora - 30 * 60, agora) == ""
    assert monitor.motivo_reinicio_preventivo(dict(cfg, chrome_reinicio_diario_hora=""), agora - 48 * 3600, agora) == ""
    assert "6 h" in monitor.motivo_reinicio_preventivo(
        dict(cfg, chrome_reinicio_diario_hora="", watchdog_reiniciar_chrome_horas=6), agora - 7 * 3600, agora)


# --- textos do alarme / voz -------------------------------------------------------
def test_textos_do_alarme_vem_da_senha(monitor):
    titulo, texto = monitor.textos_janela_alarme("LENTES DE CONTATO", 7)
    assert titulo == "NOVA SENHA 'LENTES DE CONTATO' NA FILA"
    assert "7 minutos" in texto and "Enter" in texto
    assert "1 minuto." in monitor.textos_janela_alarme("MC", 1)[1]
    assert monitor.frase_alarme("DILATAÇÃO - CCC") == "Atenção! Senha de dilatação"   # fala do filtro (2.2.0)
    assert monitor.frase_alarme("RECEPÇÃO SUS") == "Atenção! Senha Recepção SUS."
    assert monitor.frase_alarme("") == monitor.FRASE_GENERICA


def test_mascarar_email(monitor):
    assert monitor.mascarar_email("usuário fulano.silva@clinica.com.br ok") == "usuário fu***@clinica.com.br ok"


# --- gravação atômica -----------------------------------------------------------------
def test_gravacao_atomica(monitor, tmp_path):
    alvo = tmp_path / "x.json"
    monitor._gravar_json(str(alvo), {"a": "ç"})
    assert json.loads(alvo.read_text(encoding="utf-8")) == {"a": "ç"}
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


# --- assinatura Ed25519 -----------------------------------------------------------------
# Vetores do RFC 8032, seção 7.1 (testes 1 e 2)
VETORES = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


@pytest.mark.parametrize("publica,mensagem,assinatura", VETORES)
def test_ed25519_puro_vetores_rfc8032(monitor, publica, mensagem, assinatura):
    pub, msg, sig = bytes.fromhex(publica), bytes.fromhex(mensagem), bytes.fromhex(assinatura)
    assert monitor._ed25519_verificar_puro(pub, sig, msg)
    assert not monitor._ed25519_verificar_puro(pub, sig, msg + b"x")
    assert not monitor._ed25519_verificar_puro(pub, sig[:-1] + bytes([sig[-1] ^ 1]), msg)


def test_versao_json_do_repo_assinado(monitor):
    with open(os.path.join(RAIZ, "versao.json"), encoding="utf-8") as arquivo:
        dados = json.load(arquivo)
    assert monitor.validar_versao_assinada(dados, "main")
    pub = bytes.fromhex(monitor.CHAVE_PUBLICA_RELEASE)
    sig = base64.b64decode(dados["assinatura"])
    assert monitor._ed25519_verificar_puro(pub, sig, monitor.mensagem_assinada_versao(dados))  # sem cryptography
    for alteracao, canal in (({"sha256": "1" * 64}, "main"), ({"assinatura": ""}, "main"),
                             ({"versao": "99.0.0"}, "main"), ({}, "beta")):
        with pytest.raises(ValueError):
            monitor.validar_versao_assinada(dict(dados, **alteracao), canal)


def test_canais_permitidos(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "carregar_filtros",
                        lambda forcar=False: dict(monitor.FILTROS_PADRAO, atualizacao_canal="minha-branch"))
    assert monitor._canal_atualizacao() == "main"
    monkeypatch.setattr(monitor, "carregar_filtros",
                        lambda forcar=False: dict(monitor.FILTROS_PADRAO, atualizacao_canal="beta"))
    assert monitor._canal_atualizacao() == "beta"


def test_script_baixado_exige_sha(monitor):
    with open(os.path.join(RAIZ, "SCRIPT_EXPLICA.py"), "rb") as arquivo:
        bruto = arquivo.read().replace(b"\r\n", b"\n")
    sha = hashlib.sha256(bruto).hexdigest()
    assert monitor.validar_script_baixado(bruto, monitor.VERSAO, sha)
    with pytest.raises(ValueError):
        monitor.validar_script_baixado(bruto, monitor.VERSAO, "")
    with pytest.raises(ValueError):
        monitor.validar_script_baixado(bruto, monitor.VERSAO, "0" * 64)


def test_marcador_compativel_com_a_200(monitor):
    """A 2.0.0 só instala se o arquivo começar com o marcador e tiver VERSAO = "..."."""
    with open(os.path.join(RAIZ, "SCRIPT_EXPLICA.py"), encoding="utf-8") as arquivo:
        texto = arquivo.read()
    assert texto.startswith("# MONITOR-FEEGOW-SCRIPT")
    assert f'\nVERSAO = "{monitor.VERSAO}"' in texto
    assert len(texto.encode("utf-8")) > 40_000


# --- reinício (hotfix 2.0.1) ---------------------------------------------------------
def test_reinicio_relanca_sem_sair_com_zero(monitor, saidas, monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ARQ_ESTADO_WATCHDOG", str(tmp_path / "wd.json"))
    try:
        monitor.reiniciar_processo("teste", ignorar_limite=True)
    except SaidaInterceptada:
        pass
    assert saidas == [("relancar",)]
    monitor._ENCERRANDO = False


def test_limpeza_nao_encerra_e_encerrar_sai_com_zero(monitor, saidas):
    monitor._limpeza_para_sair()
    assert saidas == []
    with pytest.raises(SaidaInterceptada):
        monitor.encerrar_sistema()
    assert saidas == [("exit", 0)]
    monitor._ENCERRANDO = False


def test_relancar_sob_supervisor_sai_com_75(monitor, monkeypatch):
    codigos = []

    def saida_falsa(codigo):
        codigos.append(codigo)
        raise SaidaInterceptada(codigo)
    monkeypatch.setattr(os, "_exit", saida_falsa)
    monkeypatch.setenv("MONITOR_SUPERVISOR", "1")
    with pytest.raises(SaidaInterceptada):
        monitor.relancar_processo_agora()
    assert codigos == [75]


# --- relogin / log ---------------------------------------------------------------------
def test_limite_de_relogins(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "fazer_login", lambda: True)
    monkeypatch.setattr(monitor, "_recarregar_seguro", lambda url: None)
    monitor._RELOGINS.clear()
    monitor._RELOGIN_BLOQUEADO_ATE = 0.0
    resultados = [monitor.tratar_falha("login", "teste", 1, "x") for _ in range(4)]
    assert resultados[:3] == [None, None, None]
    assert resultados[3] == monitor.PARAR_MONITORAMENTO
    assert monitor._RELOGIN_BLOQUEADO_ATE > time.time()
    monitor._RELOGINS.clear()
    monitor._RELOGIN_BLOQUEADO_ATE = 0.0


def test_log_so_quando_o_conjunto_muda(monitor, monkeypatch):
    logs = []
    monkeypatch.setattr(monitor, "log_msg", logs.append)
    monkeypatch.setattr(monitor, "fechar_modal", lambda: None)
    monitor._ULTIMO_CONJUNTO_SENHAS = None
    for _ in range(3):
        monitor.verificar_botao_explicacao(["AGENDAMENTOS"])
    assert len([l for l in logs if "Senha '" in l]) == 1


def test_bypass_js_recebe_senha_por_argumento(monitor):
    assert "arguments[0]" in monitor.JS_BYPASS_SESSAO and "arguments[1]" in monitor.JS_BYPASS_SESSAO
    assert "{" + "senha" not in monitor.JS_BYPASS_SESSAO
    assert monitor.BYPASS_MAX_TENTATIVAS <= 150


# --- DPAPI -------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="DPAPI só existe no Windows")
def test_dpapi_migracao(monitor, monkeypatch, tmp_path):
    cred = tmp_path / "credenciais.json"
    cred.write_text(json.dumps({"usuario": "fulano@clinica.local", "senha": "senha-ficticia"}), encoding="utf-8")
    monkeypatch.setattr(monitor, "ARQ_CREDENCIAIS", str(cred))
    monkeypatch.setattr(monitor, "ARQ_ESTADO_ATUALIZACAO", str(tmp_path / "estado.json"))
    assert monitor.carregar_credenciais()["senha"] == "senha-ficticia"
    gravado = json.loads(cred.read_text(encoding="utf-8"))
    assert "senha" not in gravado and gravado["senha_protegida"].startswith("dpapi:")
    assert monitor.carregar_credenciais()["senha"] == "senha-ficticia"
    monitor.gravar_segredo("token_teste", "abc")
    assert monitor.ler_segredo("token_teste") == "abc"


# --- heartbeat / diagnóstico -----------------------------------------------------------
def test_heartbeat_e_status(monitor, monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ARQ_HEARTBEAT", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(monitor, "ARQ_STATUS", str(tmp_path / "status.json"))
    monitor.gravar_heartbeat()
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["pid"] == os.getpid() and time.time() - hb["ts"] < 5
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))["versao"] == monitor.VERSAO
    texto = monitor.texto_diagnostico()
    assert "== Estado" in texto and "senha-ficticia" not in texto


def test_heartbeat_remoto_so_https(monitor):
    assert monitor._url_heartbeat_permitida("https://vps.exemplo/hb")
    assert monitor._url_heartbeat_permitida("http://127.0.0.1:9/hb")
    assert not monitor._url_heartbeat_permitida("http://vps.exemplo/hb")
    assert monitor.FILTROS_PADRAO["heartbeat_remoto_url"] == ""      # desligado por padrão


def test_exe_sem_sha_nao_atualiza_nem_da_erro(monitor, monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "_caminho_programa_atual", lambda: str(tmp_path / "MonitorFeegow.exe"))
    monkeypatch.setattr(monitor, "_em_clone_git", lambda: False)
    monkeypatch.setattr(monitor, "ARQ_ESTADO_ATUALIZACAO", str(tmp_path / "estado.json"))
    monkeypatch.setattr(monitor, "consultar_versao_remota",
                        lambda: {"versao": "99.0.0", "sha256": "0" * 64, "sha256_exe": ""})
    assert monitor.verificar_atualizacao(manual=True) == "sem_exe"


# --- reinício programado (2.1.2) ----------------------------------------------------------
def test_reinicio_programado_regra(monitor):
    decidir = monitor.decidir_reinicio_programado
    cfg = {"reiniciar_a_cada_min": 60}
    inicio = 1_000_000.0
    assert monitor.FILTROS_PADRAO["reiniciar_a_cada_min"] == 60
    assert decidir(cfg, inicio, inicio + 59 * 60) == ("nao", "")
    assert decidir(cfg, inicio, inicio + 60 * 60) == ("reiniciar", "reinicio programado (60 min)")
    # adia no meio de um alerta, de uma fala ou da pausa do CIENTE
    passou = inicio + 61 * 60
    assert decidir(cfg, inicio, passou, alarme=True)[0] == "adiar"
    assert decidir(cfg, inicio, passou, falando=True)[0] == "adiar"
    assert decidir(cfg, inicio, passou, pausa_ate_ts=passou + 300) == ("adiar", "pausa do CIENTE")
    assert decidir(cfg, inicio, passou, pausa_ate_ts=passou - 1)[0] == "reiniciar"   # pausa vencida
    # 0 desliga; 1..9 vira 10; texto/None tolerados
    assert decidir({"reiniciar_a_cada_min": 0}, inicio, inicio + 99 * 3600) == ("nao", "")
    assert monitor._minutos_reinicio_programado(0) == 0
    assert monitor._minutos_reinicio_programado(3) == 10
    assert monitor._minutos_reinicio_programado("90") == 90
    assert monitor._minutos_reinicio_programado(None) == 60


def test_reinicio_programado_usa_reiniciar_processo(monitor, monkeypatch, tmp_path):
    chamadas = []
    monkeypatch.setattr(monitor, "reiniciar_processo", lambda motivo, ignorar_limite=False:
                        chamadas.append((motivo, ignorar_limite)))
    monkeypatch.setattr(monitor, "carregar_filtros", lambda forcar=False: dict(
        monitor.FILTROS_PADRAO, reiniciar_a_cada_min=60))
    monkeypatch.setattr(monitor, "_INICIO_PROCESSO", time.time() - 3700)
    monkeypatch.setattr(monitor, "pausa_ate", dt.datetime.now() - dt.timedelta(minutes=1))
    monkeypatch.setattr(monitor, "alarme_ativo", True)
    assert monitor._checar_reinicio_programado(time.time()) is False and chamadas == []
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    assert monitor._checar_reinicio_programado(time.time()) is True
    assert chamadas == [("reinicio programado (60 min)", True)]


def test_pausa_do_ciente_sobrevive_ao_reinicio(monitor, monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "ARQ_PAUSA", str(tmp_path / "pausa.json"))
    ate = dt.datetime.now() + dt.timedelta(minutes=10)
    monkeypatch.setattr(monitor, "pausa_ate", ate)
    monitor._salvar_pausa()
    monkeypatch.setattr(monitor, "pausa_ate", dt.datetime.now() - dt.timedelta(minutes=1))
    assert monitor.restaurar_pausa() is True
    assert abs(monitor.pausa_ate.timestamp() - ate.timestamp()) < 1
    assert not (tmp_path / "pausa.json").exists()
    # vencida ou absurda (maior que a pausa configurada) é ignorada
    (tmp_path / "pausa.json").write_text(json.dumps({"pausa_ate": time.time() + 86400}))
    assert monitor.restaurar_pausa() is False
