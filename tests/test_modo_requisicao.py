"""Modo requisição (2.3.0): a fila vem da API do Feegow (aqui uma API FALSA local).
Senha nova -> alerta idêntico ao modo Chrome; blacklist ignorada; 401 -> relogin;
2 relogins falhos -> reinício; 5xx/429 -> espera progressiva; formato quebrado ->
volta para o modo Chrome; migração do filtros.json. Nenhum Chrome é aberto, nada
fala em voz alta e o Feegow de verdade não é acessado."""
import base64
import copy
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import RAIZ


def _jwt(exp):
    def parte(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{parte({'alg': 'HS256'})}.{parte({'exp': exp})}.assinatura"


TOKEN_A = _jwt(time.time() + 12 * 3600)
TOKEN_B = _jwt(time.time() + 12 * 3600 + 60)


# --- API falsa --------------------------------------------------------------------------
class ApiFalsa:
    """Responde, em ordem, o roteiro: ("json", obj) | ("status", código, obj) | ("html",)."""

    def __init__(self):
        self.roteiro = []
        self.tokens = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                api.tokens.append(self.headers.get("x-access-token"))
                passo = api.roteiro.pop(0) if api.roteiro else ("json", [])
                if passo[0] == "html":
                    corpo, codigo, tipo = b"<html><body>login</body></html>", 200, "text/html"
                else:
                    codigo = 200 if passo[0] == "json" else passo[1]
                    corpo = json.dumps(passo[-1]).encode()
                    tipo = "application/json"
                self.send_response(codigo)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.servidor.server_address[1]}/get-queue-by-filter?filter="
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def fechar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


@pytest.fixture
def api():
    falsa = ApiFalsa()
    yield falsa
    falsa.fechar()


@pytest.fixture
def amb(monitor, monkeypatch, tmp_path, api):
    """Ambiente do laço HTTP com tudo que fala/abre janela/reinicia trocado por gravadores."""
    with open(os.path.join(RAIZ, "filtros.json"), encoding="utf-8") as arquivo:
        dados = json.load(arquivo)
    dados.update(alerta_tela=True, alerta_voz=True, modo_busca="http", intervalo_requisicao_seg=10,
                 http_ciclos_formato_invalido=3)
    caminho = tmp_path / "filtros.json"
    caminho.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(monitor, "ARQ_FILTROS", str(caminho))
    monkeypatch.setattr(monitor, "_FILTROS", dict(monitor._FILTROS))
    monkeypatch.setattr(monitor, "_FILTROS_MTIME", 0.0)
    monkeypatch.setattr(monitor, "pregerar_audios_em_segundo_plano", lambda: None)
    monkeypatch.setattr(monitor, "ARQ_ESTADO_WATCHDOG", str(tmp_path / "watchdog_estado.json"))
    monkeypatch.setattr(monitor, "URL_API_FILA", api.url)

    ev = {"eventos": [], "esperas": [], "batimentos": [], "logs": [], "bandeja": [],
          "reinicios": [], "tokens_pedidos": [], "novo_token": [TOKEN_B]}

    def na_ui(funcao, *argumentos):
        ev["eventos"].append((funcao.__name__,) + argumentos)
        if funcao is monitor.desarmar_alarme:
            funcao(*argumentos)

    def dormir(segundos, progresso=False):
        ev["esperas"].append((round(segundos), progresso))
        if not api.roteiro:
            monkeypatch.setattr(monitor, "_ENCERRANDO", True)

    def obter_token(evitar=""):
        ev["tokens_pedidos"].append(evitar)
        return ev["novo_token"].pop(0) if ev["novo_token"] else ""

    def reiniciar(motivo, ignorar_limite=False, **_k):
        ev["reinicios"].append(motivo)
        monkeypatch.setattr(monitor, "_ENCERRANDO", True)
        return True

    monkeypatch.setattr(monitor, "na_ui", na_ui)
    monkeypatch.setattr(monitor, "loop_audio_alarme", lambda: ev["eventos"].append(("voz", monitor._TICKET_ALARME)))
    monkeypatch.setattr(monitor, "log_msg", ev["logs"].append)
    monkeypatch.setattr(monitor, "_dormir", dormir)
    monkeypatch.setattr(monitor, "registrar_batimento", lambda ok=True: ev["batimentos"].append(ok))
    monkeypatch.setattr(monitor, "_avisar_bandeja", ev["bandeja"].append)
    monkeypatch.setattr(monitor, "internet_ok", lambda timeout=5: True)
    monkeypatch.setattr(monitor, "obter_token_via_chrome", obter_token)
    monkeypatch.setattr(monitor, "reiniciar_processo", reiniciar)
    monkeypatch.setattr(monitor, "credenciais_incompletas", lambda: False)
    monkeypatch.setattr(monitor, "carregar_credenciais", lambda: {"url_lista_espera": "http://127.0.0.1/"})
    monkeypatch.setattr(monitor, "_HTTP", copy.deepcopy(monitor._HTTP))
    monkeypatch.setattr(monitor, "_RELOGINS", [])
    monkeypatch.setattr(monitor, "_ULTIMO_ANUNCIO", {})
    monkeypatch.setattr(monitor, "_ULTIMO_CONJUNTO_SENHAS", None)
    monkeypatch.setattr(monitor, "_TICKET_ALARME", "")
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    monkeypatch.setattr(monitor, "pausa_ate", monitor.datetime.datetime.now() - monitor.datetime.timedelta(minutes=1))
    monkeypatch.setattr(monitor, "is_visible", False)
    monkeypatch.setattr(monitor, "driver", None)
    monkeypatch.setattr(monitor, "_ENCERRANDO", False)
    monkeypatch.setattr(monitor, "_PEDIDO_REINICIO_CHROME", "")
    monkeypatch.setattr(monitor, "_CRED_RECUSADAS", False)
    monkeypatch.setattr(monitor, "_ETAPA", "monitorando")
    monkeypatch.setattr(monitor, "_STATUS", monitor._STATUS)
    monkeypatch.setattr(monitor, "_AVISO_RECUPERACAO", "")
    ev["api"] = api
    return ev


def _fila(*tipos, inicio=1):
    return [{"id": inicio + i, "Sigla": "X", "Senha": str(100 + i), "NomeTipo": t,
             "DataHoraChegada": "2026-11-23 08:00:00"} for i, t in enumerate(tipos)]


# --- detecção: alerta idêntico ao modo Chrome ----------------------------------------------
CICLOS = [
    [],
    _fila("CONSULTA - COMUM"),
    _fila("CONSULTA - COMUM", "DILATAÇÃO - CCC"),
    _fila("DILATAÇÃO - CCC", "DILATAÇÃO - CCC", inicio=2),
    [],
]


def _alertas(eventos):
    return [e for e in eventos if e[0] in ("criar_janela_alarme", "voz", "desarmar_alarme")]


def test_senha_nova_alerta_identico_ao_modo_chrome(monitor, amb, monkeypatch):
    amb["api"].roteiro = [("json", c) for c in CICLOS]
    assert monitor.laco_http(TOKEN_A) is None
    http = _alertas(amb["eventos"])
    assert http == [("criar_janela_alarme",), ("voz", "DILATAÇÃO - CCC"), ("desarmar_alarme", False)]
    assert all(t == TOKEN_A for t in amb["api"].tokens) and len(amb["api"].tokens) == len(CICLOS)
    assert any("Senha nova na fila (id 2): DILATAÇÃO - CCC" in l for l in amb["logs"])
    assert any("ALERTAR (whitelist" in l for l in amb["logs"])

    # o mesmo roteiro pelo caminho do modo Chrome (textos dos botões do modal)
    amb["eventos"].clear()
    monkeypatch.setattr(monitor, "_ULTIMO_ANUNCIO", {})
    monkeypatch.setattr(monitor, "_ULTIMO_CONJUNTO_SENHAS", None)
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    monkeypatch.setattr(monitor, "fechar_modal", lambda: None)
    for ciclo in CICLOS:
        monitor.verificar_botao_explicacao([t["NomeTipo"] for t in ciclo])
    assert _alertas(amb["eventos"]) == http


def test_frase_e_voz_do_filtro_iguais(monitor, amb):
    cfg = monitor.carregar_filtros()
    textos = monitor.textos_da_fila(_fila("DILATAÇÃO - CCC", "MC", "LENTES DE CONTATO", "Outra coisa"), cfg)
    assert textos == ["DILATAÇÃO - CCC", "MC", "LENTES DE CONTATO"]
    assert monitor.frase_alarme("DILATAÇÃO - CCC", cfg) == "Atenção! Senha de dilatação"


def test_ignoradas_pela_blacklist(monitor, amb):
    amb["api"].roteiro = [("json", []), ("json", _fila("CONSULTA - COMUM", "PRIORITÁRIA", "R2", "MC"))]
    monitor.laco_http(TOKEN_A)
    assert _alertas(amb["eventos"]) == []
    assert monitor.alarme_ativo is False
    assert any("IGNORAR (blacklist" in l for l in amb["logs"])


def test_whitelist_vence_blacklist_e_pausa_do_ciente(monitor, amb, monkeypatch):
    monkeypatch.setattr(monitor, "pausa_ate", monitor.datetime.datetime.now() + monitor.datetime.timedelta(minutes=5))
    amb["api"].roteiro = [("json", _fila("AUTO REFRATOR (SEM DILATAR)"))]
    monitor.laco_http(TOKEN_A)
    assert _alertas(amb["eventos"]) == []
    assert any("período de pausa" in l for l in amb["logs"])


# --- sessão expirada --------------------------------------------------------------------
@pytest.mark.parametrize("resposta", [
    ("status", 401, {"code": "SESSION_EXPIRED"}),
    ("status", 403, {}),
    ("json", {"code": "SESSION_EXPIRED"}),
    ("html",),
])
def test_sessao_expirada_reloga_na_hora(monitor, amb, resposta):
    amb["api"].roteiro = [resposta, ("json", [])]
    monitor.laco_http(TOKEN_A)
    assert amb["api"].tokens == [TOKEN_A, TOKEN_B]
    assert amb["tokens_pedidos"] == [TOKEN_A]
    assert (10, True) not in amb["esperas"]   # sem espera entre o relogin e a nova requisição
    assert amb["batimentos"].count(True) == 1 and amb["reinicios"] == []


def test_token_ausente_reloga_sem_requisicao(monitor, amb):
    amb["api"].roteiro = [("json", [])]
    monitor.laco_http("")
    assert amb["api"].tokens == [TOKEN_B]


def test_dois_relogins_falhos_reiniciam_o_programa(monitor, amb):
    amb["novo_token"].clear()
    amb["api"].roteiro = [("status", 401, {"code": "SESSION_EXPIRED"})] * 5
    monitor.laco_http(TOKEN_A)
    assert len(amb["tokens_pedidos"]) == 2
    assert amb["reinicios"] == ["relogin falhou 2x seguidas no modo requisição"]
    assert True not in amb["batimentos"]


def test_reinicio_nunca_durante_alarme(monitor, amb, monkeypatch):
    amb["novo_token"].clear()
    monkeypatch.setattr(monitor, "alarme_ativo", True)
    amb["api"].roteiro = [("status", 401, {})] * 3
    monitor.laco_http(TOKEN_A)
    assert amb["reinicios"] == []
    assert any("adiado" in l for l in amb["logs"])


def test_renova_token_perto_de_vencer(monitor, amb):
    quase = _jwt(time.time() + 300)
    amb["api"].roteiro = [("json", [])]
    monitor.laco_http(quase)
    assert amb["tokens_pedidos"] == [quase] and amb["api"].tokens == [TOKEN_B]


# --- rede/servidor ---------------------------------------------------------------------
def test_5xx_espera_progressiva_e_volta_a_10(monitor, amb):
    amb["api"].roteiro = [("status", 503, {}), ("status", 502, {}), ("status", 500, {}),
                          ("status", 429, {}), ("status", 503, {}), ("json", []),
                          ("status", 503, {}), ("json", [])]
    monitor.laco_http(TOKEN_A)
    progressivas = [s for s, progresso in amb["esperas"] if progresso]
    assert progressivas == [10, 20, 40, 60, 60, 10]
    assert amb["batimentos"].count(True) == 2          # batimento só nos ciclos OK
    assert amb["batimentos"].count(False) == 1         # o inicial (registrar_batimento(ok=False))
    assert amb["reinicios"] == [] and amb["tokens_pedidos"] == []
    assert any("normalizado" in l for l in amb["logs"])


def test_espera_progressiva_formula(monitor):
    assert [monitor.espera_progressiva(10, n) for n in range(1, 7)] == [10, 20, 40, 60, 60, 60]


def test_3_min_sem_resposta_boa_reinicia(monitor, amb, monkeypatch):
    monkeypatch.setattr(monitor, "HTTP_MAX_SEM_OK_SEGUNDOS", 0.3)
    original = amb["esperas"].append

    def dormir(segundos, progresso=False):
        original((round(segundos), progresso))
        time.sleep(0.2)
        if not amb["api"].roteiro:
            monkeypatch.setattr(monitor, "_ENCERRANDO", True)
    monkeypatch.setattr(monitor, "_dormir", dormir)
    amb["api"].roteiro = [("status", 503, {})] * 10
    assert monitor.laco_http(TOKEN_A) is None
    assert len(amb["reinicios"]) == 1 and "sem resposta boa" in amb["reinicios"][0]


# --- formato quebrado -> modo Chrome ------------------------------------------------------
def test_formato_quebrado_cai_para_chrome(monitor, amb):
    amb["api"].roteiro = [("json", {"x": 1}), ("json", [{"id": 1, "Tipo": "DILATAÇÃO"}]), ("json", "texto"),
                          ("json", [])]
    assert monitor.laco_http(TOKEN_A) == monitor.VOLTAR_PARA_CHROME
    assert "mudou de formato" in monitor._HTTP["desligado"]
    assert amb["bandeja"] and len(amb["api"].tokens) == 3


def test_formato_quebrado_isolado_nao_desliga(monitor, amb):
    amb["api"].roteiro = [("json", {"x": 1}), ("json", []), ("json", {"x": 1}), ("json", [])]
    assert monitor.laco_http(TOKEN_A) is None
    assert monitor._HTTP["desligado"] == ""


def test_lista_dentro_de_objeto_e_aceita(monitor):
    assert monitor._lista_da_resposta({"data": _fila("MC")})[0]["NomeTipo"] == "MC"
    assert monitor._lista_da_resposta([]) == []


@pytest.fixture
def sessao(monitor, monkeypatch, amb):
    chamadas = []
    monkeypatch.setattr(monitor, "_abrir_chrome", lambda: chamadas.append("abrir") or True)
    monkeypatch.setattr(monitor, "fazer_login", lambda: chamadas.append("login") or True)
    monkeypatch.setattr(monitor, "_fechar_chrome", lambda: chamadas.append("fechar"))
    monkeypatch.setattr(monitor, "chrome_vivo", lambda ping=True, timeout=8.0: (False, "fechado"))
    monkeypatch.setattr(monitor, "verificar_botao_chamar_senha", lambda: chamadas.append("chrome") or "modo-chrome")
    monkeypatch.setattr(monitor, "sessao_modo_requisicao",
                        lambda: chamadas.append("http") or monitor.VOLTAR_PARA_CHROME)
    return chamadas


def test_sessao_volta_para_o_modo_chrome(monitor, sessao):
    assert monitor.executar_sessao_chrome() == "modo-chrome"
    assert sessao == ["abrir", "login", "http", "fechar", "abrir", "login", "chrome"]


def test_sessao_modo_chrome_configurado_ou_reserva(monitor, sessao, monkeypatch, amb):
    monkeypatch.setitem(monitor._HTTP, "desligado", "a API mudou")
    assert monitor.executar_sessao_chrome() == "modo-chrome"
    assert "http" not in sessao


def test_trocar_para_chrome_no_filtros_json(monitor, amb, monkeypatch):
    dados = json.loads(open(monitor.ARQ_FILTROS, encoding="utf-8").read())
    dados["modo_busca"] = "chrome"
    with open(monitor.ARQ_FILTROS, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False)
    monkeypatch.setattr(monitor, "_FILTROS_MTIME", 0.0)
    assert monitor.laco_http(TOKEN_A) == monitor.VOLTAR_PARA_CHROME
    assert amb["api"].tokens == []


# --- requisição de verdade (urllib) contra a API falsa -------------------------------------
def test_http_buscar_fila_header_e_erros(monitor, api, monkeypatch):
    monkeypatch.setattr(monitor, "URL_API_FILA", api.url)
    api.roteiro = [("json", _fila("MC")), ("status", 401, {}), ("status", 503, {}), ("html",)]
    assert monitor.http_buscar_fila(TOKEN_A)[0]["NomeTipo"] == "MC"
    assert api.tokens == [TOKEN_A]
    with pytest.raises(monitor.SessaoExpirada):
        monitor.http_buscar_fila(TOKEN_A)
    with pytest.raises(Exception) as erro:
        monitor.http_buscar_fila(TOKEN_A)
    assert getattr(erro.value, "code", None) == 503
    with pytest.raises(monitor.SessaoExpirada):
        monitor.http_buscar_fila(TOKEN_A)
    with pytest.raises(monitor.SessaoExpirada):
        monitor.http_buscar_fila("")
    assert len(api.tokens) == 4


def test_validade_token(monitor):
    assert monitor.validade_token(_jwt(1795000000)) == 1795000000.0
    assert monitor.validade_token("lixo") == 0.0


def test_verificar_sem_esc_no_modo_requisicao(monitor, amb, monkeypatch):
    escs = []
    monkeypatch.setattr(monitor, "fechar_modal", lambda: escs.append(1))
    monitor.verificar_botao_explicacao([], fechar=False)
    monitor.verificar_botao_explicacao(["CONSULTA - COMUM"], fechar=False)
    assert escs == []
    monitor.verificar_botao_explicacao([])
    assert escs == [1]


def test_status_mostra_modo(monitor, amb, monkeypatch):
    monkeypatch.setitem(monitor._HTTP, "ativo", True)
    status = monitor.montar_status()
    assert status["modo_busca"]["em_uso"] == "http" and status["modo_busca"]["configurado"] == "http"
    assert "token" not in json.dumps(status["modo_busca"]).replace("token_valido_ate", "")


def test_watchdog_nao_reabre_chrome_no_modo_requisicao(monitor, amb, monkeypatch):
    class DriverFalso:
        pass
    monkeypatch.setattr(monitor, "driver", DriverFalso())
    monkeypatch.setitem(monitor._HTTP, "ativo", True)
    monkeypatch.setattr(monitor, "_SELENIUM_RODANDO", True)
    monkeypatch.setattr(monitor, "_MONITOR_DESEJADO", True)
    monkeypatch.setattr(monitor, "_ULTIMO_PROGRESSO", time.time())
    monkeypatch.setattr(monitor, "chrome_vivo", lambda ping=True, timeout=8.0: (False, "fechado"))
    monitor._passo_watchdog(time.time())
    assert monitor._PEDIDO_REINICIO_CHROME == ""


# --- migração do filtros.json -----------------------------------------------------------
def test_migracao_so_acrescenta_chaves(monitor, monkeypatch, tmp_path):
    caminho = tmp_path / "filtros.json"
    antigo = {"termos_gatilho": ["MC"], "whitelist": ["MC"], "blacklist": [], "alerta_voz": False,
              "intervalo_busca_segundos": 7}
    caminho.write_text(json.dumps(antigo), encoding="utf-8")
    monkeypatch.setattr(monitor, "ARQ_FILTROS", str(caminho))
    monkeypatch.setattr(monitor, "_FILTROS", dict(monitor._FILTROS))
    monkeypatch.setattr(monitor, "_FILTROS_MTIME", 0.0)
    monkeypatch.setattr(monitor, "pregerar_audios_em_segundo_plano", lambda: None)
    cfg = monitor.carregar_filtros(forcar=True)
    gravado = json.loads(caminho.read_text(encoding="utf-8"))
    assert gravado["modo_busca"] == "http" and gravado["intervalo_requisicao_seg"] == 10
    assert gravado["http_ciclos_formato_invalido"] == 3
    for chave, valor in antigo.items():
        assert gravado[chave] == valor
    assert cfg["modo_busca"] == "http" and cfg["intervalo_requisicao_seg"] == 10
    mtime = os.path.getmtime(caminho)
    monitor.carregar_filtros(forcar=True)
    assert os.path.getmtime(caminho) == mtime          # segunda leitura não regrava


def test_migracao_mantem_modo_escolhido(monitor):
    dados = {"modo_busca": "chrome", "intervalo_requisicao_seg": 30}
    assert monitor.migrar_modo_requisicao(dados) is True
    assert dados == {"modo_busca": "chrome", "intervalo_requisicao_seg": 30, "http_ciclos_formato_invalido": 3,
                     "log_requisicoes": True}
    assert monitor.migrar_modo_requisicao(dados) is False
    assert monitor._modo_busca_valido("CHROME") == "chrome"
    assert monitor._modo_busca_valido("qualquer") == "http"


def test_filtros_json_do_repo_tem_modo_requisicao():
    with open(os.path.join(RAIZ, "filtros.json"), encoding="utf-8") as arquivo:
        dados = json.load(arquivo)
    assert dados["modo_busca"] == "http" and dados["intervalo_requisicao_seg"] == 10


def test_sem_limite_de_relogins_por_hora(monitor, amb, monkeypatch):
    """2.3.1: sessão expirada reloga SEMPRE na hora (sem o limite de 3/h nem a pausa de 60 min)."""
    tokens = [_jwt(time.time() + 12 * 3600 + i) for i in range(1, 7)]
    amb["novo_token"][:] = list(tokens)
    monkeypatch.setattr(monitor, "_RELOGINS", [time.time()] * 10)   # já "estourado" pela regra antiga
    amb["api"].roteiro = [("status", 401, {"code": "SESSION_EXPIRED"})] * 5 + [("json", [])]
    assert monitor.laco_http(TOKEN_A) is None
    assert len(amb["tokens_pedidos"]) == 5 and amb["reinicios"] == []
    assert amb["api"].tokens == [TOKEN_A] + tokens[:5]
    assert monitor._ETAPA != "aguardando_login" and amb["batimentos"].count(True) == 1
    assert not any("Parei de relogar" in l for l in amb["logs"])


# --- 2.3.2: log de cada requisição, dados mascarados, Chrome fechado depois do login -------
def _linhas(amb, prefixo):
    return [l for l in amb["logs"] if l.startswith(prefixo)]


def test_log_de_cada_requisicao_e_corpo_quando_muda(monitor, amb):
    fila = [{"id": 220703, "Sigla": "MC", "Senha": "105", "NomeTipo": "MC",
             "DataHoraChegada": "2026-11-23T18:35:10", "NomePaciente": "Maria da Silva",
             "CPF": "123.456.789-09", "Telefone": "(12) 99876-5432"}]
    amb["api"].roteiro = [("json", []), ("json", []), ("json", fila), ("status", 503, {"erro": "fora"})]
    monitor.laco_http(TOKEN_A)
    gets = _linhas(amb, "→ GET ")
    assert [g.rsplit("(", 1)[1] for g in gets] == ["ciclo 1)", "ciclo 2)", "ciclo 3)", "ciclo 4)"]
    assert all(amb["api"].url in g for g in gets)
    respostas = _linhas(amb, "← ")
    assert respostas[0].startswith("← 200 em ") and respostas[0].endswith("0 senha(s)")
    assert "1 senha(s): [MC105 MC 18:35 (id 220703)]" in respostas[2]
    assert respostas[3].startswith("← 503 em ")
    corpos = _linhas(amb, "   corpo: ")
    assert len(corpos) == 3                              # 1ª, a que mudou e o erro (a 2ª repetida não)
    texto = "\n".join(amb["logs"])
    assert "Maria" not in texto and "123.456.789-09" not in texto and "99876-5432" not in texto
    assert '"NomePaciente":"M.d.S."' in corpos[1] and '"NomeTipo":"MC"' in corpos[1]
    assert TOKEN_A not in texto and "x-access-token" not in texto


def test_log_requisicoes_desligado(monitor, amb):
    dados = json.loads(open(monitor.ARQ_FILTROS, encoding="utf-8").read())
    dados["log_requisicoes"] = False
    with open(monitor.ARQ_FILTROS, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False)
    amb["api"].roteiro = [("json", _fila("MC"))]
    monitor.laco_http(TOKEN_A)
    assert not _linhas(amb, "→ GET ") and not _linhas(amb, "← ")


def test_log_401_e_token_nunca_aparece(monitor, amb):
    amb["api"].roteiro = [("status", 401, {"code": "SESSION_EXPIRED"}), ("json", [])]
    monitor.laco_http(TOKEN_A)
    assert _linhas(amb, "← 401 em ")
    assert any("SESSION_EXPIRED" in l for l in _linhas(amb, "   corpo: "))
    texto = "\n".join(amb["logs"])
    assert TOKEN_A not in texto and TOKEN_B not in texto


def test_mascarar_dados_paciente(monitor):
    entrada = {"NomeTipo": "DILATAÇÃO - CCC", "Nome": "João Pedro Souza", "paciente": {"nome": "Ana Lima",
               "cpf": "12345678909"}, "obs": "ligar 12 3456-7890", "Sigla": "D", "Senha": "12",
               "NomeFila": "Recepção", "id": 99, "Celular": "11987654321"}
    saida = monitor.mascarar_dados_paciente(entrada)
    assert saida["NomeTipo"] == "DILATAÇÃO - CCC" and saida["NomeFila"] == "Recepção"
    assert saida["Nome"] == "J.P.S." and saida["paciente"] == {"nome": "A.L.", "cpf": "***"}
    assert saida["obs"] == "ligar ***" and saida["Celular"] == "***" and saida["id"] == 99
    assert entrada["Nome"] == "João Pedro Souza"   # não altera o original
    longo = monitor._corpo_para_log(json.dumps([{"NomeTipo": "X" * 50}] * 100).encode(), limite=200)
    assert len(longo) < 260 and "caracteres)" in longo


def test_chrome_fecha_depois_do_login_mesmo_com_painel_visivel(monitor, amb, monkeypatch):
    fechados = []
    monkeypatch.setattr(monitor, "driver", object())
    monkeypatch.setattr(monitor, "is_visible", True)
    monkeypatch.setattr(monitor, "chrome_vivo", lambda ping=True, timeout=8.0: (True, ""))
    monkeypatch.setattr(monitor, "_fechar_chrome", lambda: (fechados.append(1), setattr(monitor, "driver", None)))
    amb["api"].roteiro = [("json", [])]
    monitor.laco_http(TOKEN_A)
    assert fechados == [1]
    assert "🌐 Chrome fechado (modo requisição)." in amb["logs"]


def test_mostrar_pela_bandeja_pede_o_chrome(monitor, amb, monkeypatch):
    monkeypatch.setitem(monitor._HTTP, "ativo", True)
    monkeypatch.setattr(monitor, "root", type("R", (), {"deiconify": lambda s: None, "state": lambda s, *a: None,
                                                        "lift": lambda s: None, "focus_force": lambda s: None,
                                                        "withdraw": lambda s: None})())
    monkeypatch.setattr(monitor, "_hwnd_chrome", lambda: None)
    monitor.gerenciar_janelas("mostrar")
    assert monitor._HTTP["pedido_mostrar"] is True and monitor._quer_chrome_visivel()
    monitor.gerenciar_janelas("ocultar")
    assert not monitor._quer_chrome_visivel()
