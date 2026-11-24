"""Sessão guardada do modo requisição (2.3.4): o token fica em sessao.dat cifrado com a
DPAPI. Ao abrir/reiniciar, sessão válida é reaproveitada SEM Chrome e SEM login;
expirada/recusada/corrompida é apagada e o login normal grava a nova. Usa a API FALSA
do test_modo_requisicao (nenhum Chrome, nenhum Feegow de verdade)."""
import os
import time

import pytest

from test_modo_requisicao import TOKEN_A, TOKEN_B, _fila, _jwt, amb, api  # noqa: F401 (fixtures)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="DPAPI só existe no Windows")


@pytest.fixture
def ses(monitor, monkeypatch, amb):  # noqa: F811
    """executar_sessao_chrome com Chrome/login trocados por gravadores."""
    chamadas = []
    monkeypatch.setattr(monitor, "_abrir_chrome", lambda: chamadas.append("abrir") or True)
    monkeypatch.setattr(monitor, "fazer_login", lambda: chamadas.append("login") or True)
    monkeypatch.setattr(monitor, "_fechar_chrome", lambda: None)
    monkeypatch.setattr(monitor, "chrome_vivo", lambda ping=True, timeout=8.0: (False, "fechado"))
    monkeypatch.setattr(monitor, "verificar_botao_chamar_senha", lambda: chamadas.append("chrome") or "modo-chrome")

    def sessao_login():
        chamadas.append("http-depois-do-login")
        monitor.gravar_sessao(amb["novo_token"].pop(0))   # o que o caminho real faz com o token lido
        return None
    monkeypatch.setattr(monitor, "sessao_modo_requisicao", sessao_login)
    amb["chamadas"] = chamadas
    return amb


def _todo_log(amb):
    return "\n".join(map(str, amb["logs"]))


def test_arquivo_cifrado_sem_token_em_texto(monitor):
    assert monitor.gravar_sessao(TOKEN_A)
    caminho = monitor.arquivo_sessao()
    bruto = open(caminho, "rb").read()
    corpo = TOKEN_A.split(".")[1].encode()
    assert TOKEN_A.encode() not in bruto and corpo not in bruto and b"token" not in bruto
    assert bruto.startswith(b"MFS1")
    token, exp = monitor.ler_sessao()
    assert token == TOKEN_A and exp == monitor.validade_token(TOKEN_A)
    # a pasta do programa não recebe nada
    assert os.path.dirname(caminho) != monitor.PASTA_SCRIPT


def test_caminho_padrao_no_localappdata(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "ARQ_SESSAO", "")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\perfil\AppData\Local")
    assert monitor.arquivo_sessao() == r"C:\perfil\AppData\Local\MonitorFeegow\sessao.dat"


def test_sessao_valida_reaproveitada_sem_chrome(monitor, ses):
    monitor.gravar_sessao(TOKEN_A)
    ses["api"].roteiro = [("json", []), ("json", _fila("CONSULTA - COMUM"))]
    assert monitor.executar_sessao_chrome() is None
    assert ses["chamadas"] == []                         # nem Chrome, nem login
    assert ses["tokens_pedidos"] == []
    assert all(t == TOKEN_A for t in ses["api"].tokens) and len(ses["api"].tokens) == 2
    assert any(l.startswith("🔑 Sessão reaproveitada (válida até ") for l in ses["logs"])
    assert os.path.exists(monitor.arquivo_sessao())
    assert TOKEN_A not in _todo_log(ses)


def test_sessao_expirada_na_api_apaga_e_loga(monitor, ses):
    monitor.gravar_sessao(TOKEN_A)
    ses["api"].roteiro = [("status", 401, {"code": "SESSION_EXPIRED"})]
    assert monitor.executar_sessao_chrome() is None
    assert ses["chamadas"] == ["abrir", "login", "http-depois-do-login"]
    assert any("Sessão guardada não vale mais" in l and "apagada; fazendo login" in l for l in ses["logs"])
    assert monitor.ler_sessao()[0] == TOKEN_B            # regravada com o token do login
    assert TOKEN_A not in _todo_log(ses) and TOKEN_B not in _todo_log(ses)


@pytest.mark.parametrize("passo", [("html",), ("json", {"code": "SESSION_EXPIRED"}),
                                   ("json", {"x": 1}), ("status", 403, {})])
def test_respostas_invalidas_apagam(monitor, ses, passo):
    monitor.gravar_sessao(TOKEN_A)
    ses["api"].roteiro = [passo]
    assert monitor.tentar_sessao_guardada() == ""
    assert not os.path.exists(monitor.arquivo_sessao())


def test_perto_de_vencer_apaga_sem_requisicao(monitor, ses):
    monitor.gravar_sessao(_jwt(time.time() + 300))
    assert monitor.tentar_sessao_guardada() == ""
    assert ses["api"].tokens == [] and not os.path.exists(monitor.arquivo_sessao())


@pytest.mark.parametrize("conteudo", [b"", b"lixo qualquer", b"MFS1" + b"\x00" * 40])
def test_arquivo_corrompido_apaga_e_loga(monitor, ses, conteudo):
    caminho = monitor.arquivo_sessao()
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    open(caminho, "wb").write(conteudo)
    assert monitor.executar_sessao_chrome() is None
    assert ses["chamadas"] == ["abrir", "login", "http-depois-do-login"]
    assert ses["api"].tokens == []
    assert any("arquivo ilegível" in l and "apagada; fazendo login" in l for l in ses["logs"])
    assert monitor.ler_sessao()[0] == TOKEN_B


def test_erro_de_rede_no_teste_nao_apaga(monitor, ses):
    monitor.gravar_sessao(TOKEN_A)
    ses["api"].roteiro = [("status", 503, {})]
    assert monitor.tentar_sessao_guardada() == ""
    assert monitor.ler_sessao()[0] == TOKEN_A


def test_sem_arquivo_segue_login_normal(monitor, ses):
    assert monitor.executar_sessao_chrome() is None
    assert ses["chamadas"] == ["abrir", "login", "http-depois-do-login"]
    assert not any("Sessão guardada" in l for l in ses["logs"])


@pytest.fixture
def obter_real(monitor):
    """O obter_token_via_chrome de verdade (pedido ANTES do amb, que o troca)."""
    return monitor.obter_token_via_chrome


def test_relogin_no_laco_regrava(monitor, obter_real, amb, monkeypatch):  # noqa: F811
    monitor.gravar_sessao(TOKEN_A)
    # obter_token_via_chrome real com só o Chrome trocado: ele grava o token novo
    monkeypatch.setattr(monitor, "obter_token_via_chrome", obter_real)
    monkeypatch.setattr(monitor, "_obter_token_via_chrome", lambda evitar="": TOKEN_B)
    monkeypatch.setattr(monitor, "_LOGIN_INICIO", 0.0)
    amb["api"].roteiro = [("status", 401, {}), ("json", [])]
    monitor.laco_http(TOKEN_A)
    assert monitor.ler_sessao()[0] == TOKEN_B
    assert amb["api"].tokens == [TOKEN_A, TOKEN_B]


def test_obter_token_via_chrome_grava(monitor, monkeypatch, tmp_path):
    monkeypatch.setattr(monitor, "_obter_token_via_chrome", lambda evitar="": TOKEN_B)
    monkeypatch.setattr(monitor, "_LOGIN_INICIO", 0.0)
    assert monitor.obter_token_via_chrome() == TOKEN_B
    assert monitor.ler_sessao()[0] == TOKEN_B


def test_401_no_laco_apaga_antes_de_relogar(monitor, amb, monkeypatch):  # noqa: F811
    monitor.gravar_sessao(TOKEN_A)
    vistos = []
    monkeypatch.setattr(monitor, "obter_token_via_chrome",
                        lambda evitar="": vistos.append(os.path.exists(monitor.arquivo_sessao())) or "")
    amb["api"].roteiro = [("status", 401, {})]
    monitor.laco_http(TOKEN_A)
    assert vistos and vistos[0] is False


def test_falha_ao_gravar_nao_derruba_e_nao_loga_token(monitor, amb, monkeypatch):  # noqa: F811
    def quebra(*_a, **_k):
        raise OSError("DPAPI fora")
    monkeypatch.setattr(monitor, "_dpapi", quebra)
    assert monitor.gravar_sessao(TOKEN_A) is False
    assert TOKEN_A not in _todo_log(amb)
    assert not os.path.exists(monitor.arquivo_sessao())
