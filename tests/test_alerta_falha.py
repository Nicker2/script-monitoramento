"""Alerta de falha (2.3.5): ícone vermelho, balão com setinha acima da bandeja, voz
opcional, aviso de "voltou", erros não tratados e avisos do supervisor. Nenhuma janela
é aberta (o balão é só desenhado em memória; mostrar/esconder e voz são gravadores)."""
import json
import os
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from conftest import RAIZ

CHAVE = None


# --- desenho e posição do balão -----------------------------------------------------------
def test_desenho_do_balao_com_setinha(monitor):
    img, geo = monitor.desenhar_balao("aviso", "Monitor de senhas sem funcionar há 3 min",
                                      "Sessão caiu. Tentando recuperar...", seta_x=300)
    chave = monitor.BALAO_COR_CHAVE
    assert img.size == (geo["largura"], geo["altura"]) == (monitor.BALAO_LARGURA, geo["altura_corpo"] + 14)
    assert img.getpixel((0, 0)) == chave and img.getpixel((img.width - 1, 0)) == chave   # cantos arredondados
    # setinha: a ponta (embaixo) só existe na coluna da seta
    base = geo["altura"] - 4
    assert img.getpixel((300, base)) != chave
    assert img.getpixel((40, base)) == chave and img.getpixel((img.width - 10, base)) == chave
    assert geo["seta_x"] == 300
    x0, y0, x1, y1 = geo["fechar"]
    assert 0 < x0 < x1 < img.width and 0 < y0 < y1 < geo["altura_corpo"]
    # a cor-chave nunca aparece dentro do corpo (senão faria um furo na janela)
    meio = geo["altura_corpo"] // 2
    assert all(img.getpixel((x, meio)) != chave for x in range(3, img.width - 3))


@pytest.mark.parametrize("tipo", ["aviso", "ok", "erro", "info"])
def test_todos_os_tipos_e_texto_longo(monitor, tipo):
    img, geo = monitor.desenhar_balao(tipo, "Título", "palavra " * 200)
    assert geo["altura"] < 260   # texto longo é cortado (no máximo 8 linhas)


def test_posicao_aponta_para_o_icone_ou_canto(monitor):
    area = (0, 0, 1366, 728)
    x, y, seta = monitor.posicao_balao(area, (1176, 736, 1200, 760), 400, 120)
    assert y == 728 - 120 and x + seta == 1188          # ponta da setinha no centro do ícone
    assert 0 <= x and x + 400 <= 1366
    # ícone escondido (fora da barra de baixo) ou desconhecido: canto inferior direito
    for icone in (None, (1300, 600, 1324, 624), (0, 0, 0, 0)):
        x, y, seta = monitor.posicao_balao(area, icone, 400, 120)
        assert (x, y, seta) == (1366 - 400 - 12, 608, 360)


# --- gerenciador: uma mensagem por vez, some sozinho, nunca por cima do alarme -----------
@pytest.fixture
def ger(monitor):
    ev = {"mostrou": [], "escondeu": 0, "alarme": False, "cliques": []}

    def esconder():
        ev["escondeu"] += 1
    g = monitor.GerenciadorBalao(lambda m: ev["mostrou"].append(m["titulo"]), esconder,
                                 alarme=lambda: ev["alarme"])
    ev["g"] = g
    return ev


def test_some_sozinho_em_12s_e_persistente_fica(ger):
    g = ger["g"]
    g.avisar("ok", "voltou", "x")
    g.tick(1000)
    assert ger["mostrou"] == ["voltou"]
    g.tick(1011)
    assert g.atual is not None
    g.tick(1012.1)
    assert g.atual is None and ger["escondeu"] == 1
    g.avisar("aviso", "falha", "x", persistente=True, chave="falha")
    g.tick(2000)
    g.tick(9000)
    assert g.atual is not None and ger["mostrou"] == ["voltou", "falha"]


def test_uma_por_vez_a_nova_substitui(ger):
    g = ger["g"]
    g.avisar("aviso", "falha", "x", persistente=True, chave="falha")
    g.tick(1)
    g.avisar("ok", "voltou", "y", chave="falha")
    g.tick(2)
    assert ger["mostrou"] == ["falha", "voltou"] and g.atual["titulo"] == "voltou"


def test_nunca_por_cima_do_alarme(ger):
    g = ger["g"]
    ger["alarme"] = True
    g.avisar("aviso", "falha", "x", persistente=True)
    g.tick(1)
    assert ger["mostrou"] == []
    ger["alarme"] = False
    g.tick(2)
    assert ger["mostrou"] == ["falha"]
    ger["alarme"] = True            # alarme começou com o balão na tela: some
    g.tick(3)
    assert ger["escondeu"] == 1 and not g.visivel
    ger["alarme"] = False           # alarme acabou: volta
    g.tick(4)
    assert ger["mostrou"] == ["falha", "falha"]


def test_clique_abre_e_x_so_fecha(ger):
    g = ger["g"]
    abriu = []
    g.avisar("aviso", "a", "x", persistente=True, ao_clicar=lambda: abriu.append(1))
    g.tick(1)
    g.clicou(no_fechar=True)
    assert abriu == [] and g.atual is None
    g.avisar("aviso", "b", "x", persistente=True, ao_clicar=lambda: abriu.append(1))
    g.clicou()
    assert abriu == [1] and g.atual is None


def test_descartar_so_a_chave_certa(ger):
    g = ger["g"]
    g.avisar("info", "outra", "x", chave="info")
    g.descartar("falha")
    assert g.atual is not None
    g.descartar("info")
    assert g.atual is None


# --- alerta de falha no monitor ----------------------------------------------------------
@pytest.fixture
def al(monitor, monkeypatch, tmp_path):
    caminho = tmp_path / "filtros.json"
    dados = json.load(open(os.path.join(RAIZ, "filtros.json"), encoding="utf-8"))
    caminho.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(monitor, "ARQ_FILTROS", str(caminho))
    monkeypatch.setattr(monitor, "_FILTROS", dict(monitor._FILTROS))
    monkeypatch.setattr(monitor, "_FILTROS_MTIME", 0.0)
    monkeypatch.setattr(monitor, "ARQ_ALERTA_FALHA", str(tmp_path / "alerta_falha.json"))
    monkeypatch.setattr(monitor, "_ALERTA_FALHA", {
        "em_falha": False, "desde": 0.0, "ultimo_aviso": 0.0, "motivo": "", "herdado_ref": 0.0,
        "carregado": False, "reexibir": False})
    ev = {"baloes": [], "voz": [], "logs": [], "titulos": []}
    monkeypatch.setattr(monitor, "avisar_balao", lambda tipo, titulo, texto, persistente=False, chave="":
                        ev["baloes"].append((tipo, titulo, texto, persistente)))
    monkeypatch.setattr(monitor, "_falar_alerta_falha", lambda cfg: ev["voz"].append(1))
    monkeypatch.setattr(monitor, "log_msg", ev["logs"].append)

    class Bandeja:
        HAS_NOTIFICATION = True

        def __setattr__(self, nome, valor):
            if nome == "title":
                ev["titulos"].append(valor)
            object.__setattr__(self, nome, valor)
    monkeypatch.setattr(monitor, "tray_icon", Bandeja())
    monkeypatch.setattr(monitor, "criar_icone_imagem", lambda cor: cor)
    agora = time.time()
    monkeypatch.setattr(monitor, "_INICIO_PROCESSO", agora - 3600)
    monkeypatch.setattr(monitor, "_MARCO_ATIVO", agora - 3600)
    monkeypatch.setattr(monitor, "_ULTIMO_OK", agora - 100)
    monkeypatch.setattr(monitor, "_ETAPA", "monitorando")
    monkeypatch.setattr(monitor, "_MONITOR_DESEJADO", True)
    monkeypatch.setattr(monitor, "_SELENIUM_RODANDO", True)
    monkeypatch.setattr(monitor, "_ENCERRANDO", False)
    monkeypatch.setattr(monitor, "_STATUS", ("recuperando", "Sessão caiu — entrando de novo..."))
    ev["agora"] = agora
    return ev


def test_falha_avisa_repete_a_cada_10_min_e_volta(monitor, al, monkeypatch):
    t = al["agora"]
    assert monitor._checar_alerta_falha(t) == "nada"                      # 100 s sem OK
    assert monitor._checar_alerta_falha(t + 90) == "entrar"               # 190 s > 3 min
    assert al["baloes"] == [("aviso", "Monitor de senhas sem funcionar há 3 min",
                             "Sessão caiu — entrando de novo. Tentando recuperar...", True)]
    assert al["voz"] == [1]
    assert "⚠️ Monitor de senhas sem funcionar há 3 min: Sessão caiu — entrando de novo. Tentando recuperar..." in al["logs"]
    assert monitor.tray_icon.icon == monitor.CORES_STATUS["falha"]
    assert "Sem funcionar há 3 min: Sessão caiu" in al["titulos"][-1]
    for passo in range(1, 40):                                           # 15 s a cada passo: não repete
        assert monitor._checar_alerta_falha(t + 90 + passo * 15) in ("manter",)
    assert len(al["baloes"]) == 1 and len(al["voz"]) == 1
    assert monitor._checar_alerta_falha(t + 90 + 600) == "repetir"
    assert len(al["baloes"]) == 2 and len(al["voz"]) == 2
    monkeypatch.setattr(monitor, "_ULTIMO_OK", t + 700)
    monkeypatch.setattr(monitor, "_STATUS", ("ok", "Conectado"))
    assert monitor._checar_alerta_falha(t + 705) == "voltou"
    assert al["baloes"][-1][:2] == ("ok", "Monitor de senhas voltou a funcionar")
    assert any(l.startswith("✅ Monitor de senhas voltou a funcionar") for l in al["logs"])
    assert monitor.tray_icon.icon == monitor.CORES_STATUS["ok"]
    assert len(al["voz"]) == 2                                           # "voltou" não fala


def test_reinicio_no_meio_da_falha_reexibe_sem_voz(monitor, al, monkeypatch):
    t = al["agora"]
    monitor._checar_alerta_falha(t + 90)
    assert len(al["baloes"]) == 1
    # novo processo (a escada reiniciou): estado só no arquivo, nenhum OK ainda
    monkeypatch.setattr(monitor, "_ALERTA_FALHA", {
        "em_falha": False, "desde": 0.0, "ultimo_aviso": 0.0, "motivo": "", "herdado_ref": 0.0,
        "carregado": False, "reexibir": False})
    monkeypatch.setattr(monitor, "_INICIO_PROCESSO", t + 100)
    monkeypatch.setattr(monitor, "_MARCO_ATIVO", t + 105)
    monkeypatch.setattr(monitor, "_ULTIMO_OK", 0.0)
    assert monitor._checar_alerta_falha(t + 120) == "manter"
    assert len(al["baloes"]) == 2 and al["baloes"][-1][0] == "aviso" and len(al["voz"]) == 1
    assert monitor._checar_alerta_falha(t + 135) == "manter" and len(al["baloes"]) == 2


def test_herda_o_sem_ok_do_processo_anterior(monitor, al, monkeypatch):
    t = al["agora"]
    monitor._checar_alerta_falha(t)          # grava ref = t - 100, sem falha ainda
    monkeypatch.setattr(monitor, "_ALERTA_FALHA", dict(monitor._ALERTA_FALHA, carregado=False))
    monkeypatch.setattr(monitor, "_INICIO_PROCESSO", t + 10)
    monkeypatch.setattr(monitor, "_MARCO_ATIVO", t + 12)
    monkeypatch.setattr(monitor, "_ULTIMO_OK", 0.0)
    assert monitor._checar_alerta_falha(t + 85) == "entrar"   # 185 s desde o último OK do anterior


def test_fora_do_horario_ou_parado_pelo_usuario_nao_alerta(monitor, al, monkeypatch):
    t = al["agora"]
    monkeypatch.setattr(monitor, "_ETAPA", "fora_horario")
    assert monitor._checar_alerta_falha(t + 900) == "nada"
    monkeypatch.setattr(monitor, "_ETAPA", "monitorando")
    monkeypatch.setattr(monitor, "_MONITOR_DESEJADO", False)
    assert monitor._checar_alerta_falha(t + 900) == "nada"
    monkeypatch.setattr(monitor, "_MONITOR_DESEJADO", True)
    assert monitor._checar_alerta_falha(t + 900) == "entrar"
    monkeypatch.setattr(monitor, "_ETAPA", "fora_horario")
    assert monitor._checar_alerta_falha(t + 915) == "encerrar"
    assert al["baloes"][-1][0] == "aviso" and not monitor._ALERTA_FALHA["em_falha"]


def test_sem_internet_conta_com_motivo(monitor, al, monkeypatch):
    monkeypatch.setattr(monitor, "_ETAPA", "offline")
    assert monitor._checar_alerta_falha(al["agora"] + 120) == "entrar"
    assert "sem internet" in al["baloes"][0][2]


def test_configuravel_desliga(monitor, al, monkeypatch):
    dados = json.load(open(monitor.ARQ_FILTROS, encoding="utf-8"))
    dados.update(alerta_falha_min=0)
    open(monitor.ARQ_FILTROS, "w", encoding="utf-8").write(json.dumps(dados))
    monkeypatch.setattr(monitor, "_FILTROS_MTIME", 0.0)
    assert monitor._checar_alerta_falha(al["agora"] + 3600) == "nada"
    dados.update(alerta_falha_min=5, alerta_falha_notificar=False)
    open(monitor.ARQ_FILTROS, "w", encoding="utf-8").write(json.dumps(dados))
    monkeypatch.setattr(monitor, "_FILTROS_MTIME", 0.0)
    assert monitor._checar_alerta_falha(al["agora"] + 150) == "nada"      # 250 s < 5 min
    assert monitor._checar_alerta_falha(al["agora"] + 250) == "entrar"
    assert al["baloes"] == [] and al["voz"] == [1]                       # sem balão; voz é outra chave


def test_migracao_aditiva(monitor):
    dados = {"alerta_falha_voz": False}
    assert monitor.migrar_alerta_falha(dados)
    assert dados == {"alerta_falha_voz": False, "alerta_falha_min": 3, "alerta_falha_repetir_min": 10,
                     "alerta_falha_notificar": True}
    assert not monitor.migrar_alerta_falha(dados)


# --- voz: liga/desliga geral e nunca por cima do alarme -------------------------------------
@pytest.fixture
def voz(monitor, monkeypatch):
    tocou = []
    monkeypatch.setattr(monitor, "obter_audio", lambda frase, usar_rede=True: tocou.append(frase) or None)
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    return tocou


def test_voz_respeita_chaves_e_alarme(monitor, voz, monkeypatch):
    assert monitor._falar_alerta_falha({"alerta_voz": False, "alerta_falha_voz": True}) is False
    assert monitor._falar_alerta_falha({"alerta_voz": True, "alerta_falha_voz": False}) is False
    monkeypatch.setattr(monitor, "alarme_ativo", True)
    assert monitor._falar_alerta_falha({}) is False
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    with monitor._TRAVA_AUDIO:                           # outra fala (alarme) em andamento
        assert monitor._falar_alerta_falha({}) is False
    assert voz == []
    assert monitor._falar_alerta_falha({}) is True
    fim = time.time() + 5
    while not voz and time.time() < fim:
        time.sleep(0.05)
    assert voz == [monitor.FRASE_FALHA]


# --- erros não tratados ----------------------------------------------------------------------
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjE3MDAwMDAwMDB9.c2lnbmF0dXJh"


def test_erro_nao_tratado_vai_para_o_log_sem_dado_sensivel(monitor, al):
    def quebra():
        raise ValueError(f"token={JWT} usuario fulano@hoc.com.br cpf 123.456.789-09")
    t = threading.Thread(target=quebra, name="monitor")
    antigo = threading.excepthook
    threading.excepthook = monitor._gancho_erro_thread
    try:
        t.start()
        t.join()
    finally:
        threading.excepthook = antigo
    log = "\n".join(al["logs"])
    assert "💥 Erro não tratado em 'monitor' (ValueError)" in log and "Traceback" in log and "quebra" in log
    assert JWT not in log and "fulano@hoc.com.br" not in log and "123.456.789-09" not in log
    assert al["baloes"] and al["baloes"][0][:2] == ("erro", "Erro inesperado no monitor de senhas")
    # o mesmo tipo de aviso no máximo 1 vez a cada 10 min
    try:
        raise KeyError("x")
    except KeyError:
        assert monitor.registrar_erro_nao_tratado(*sys.exc_info(), "watchdog") is False
    assert len(al["baloes"]) == 1


def test_texto_seguro(monitor):
    texto = monitor.texto_seguro_para_log(f"x-access-token: {JWT} senha=abc123 tel (12) 99876-5432")
    assert JWT not in texto and "abc123" not in texto and "99876-5432" not in texto


# --- supervisor -------------------------------------------------------------------------------
def test_supervisor_avisa_sem_repetir_e_pede_suporte(supervisor, monkeypatch, tmp_path):
    baloes = []
    monkeypatch.setattr(supervisor, "mostrar_balao", lambda *a, **k: baloes.append(a) or True)
    monkeypatch.setattr(supervisor, "log", lambda m: None)
    monkeypatch.setattr(supervisor, "_avisos_ligados", lambda: True)
    monkeypatch.setattr(supervisor, "PASTA_LOGS", str(tmp_path))
    monkeypatch.setattr(supervisor, "AVISO_REINICIO_ERRO", str(tmp_path / "reinicio_erro.json"))
    t = 10_000.0
    supervisor.registrar_falha("Monitor travado (sem resposta)", agora=t)
    # 2.3.6: o supervisor não mostra balão próprio; o monitor novo avisa ao subir
    assert baloes == []
    assert json.loads((tmp_path / "reinicio_erro.json").read_text(encoding="utf-8"))["motivo"] == \
        "Monitor travado (sem resposta)"
    supervisor.registrar_falha("erro inesperado (o monitor fechou sozinho, código 1)", agora=t + 60)
    assert baloes == []
    supervisor.registrar_falha("erro inesperado (o monitor fechou sozinho, código 1)", agora=t + 120)    # 3ª seguida
    assert baloes[-1][:2] == ("erro", "Monitor de senhas não conseguiu voltar") and baloes[-1][3] is True
    supervisor.registrar_volta(agora=t + 200)
    assert baloes[-1][:2] == ("ok", "Monitor de senhas voltou a funcionar")
    assert supervisor._FALHAS == {"seguidas": 0, "suporte": False}


def test_supervisor_so_usa_balao_com_script_novo(supervisor, tmp_path, monkeypatch):
    script = tmp_path / "SCRIPT_EXPLICA.py"
    monkeypatch.setattr(supervisor, "SCRIPT", str(script))
    script.write_text('# x\nVERSAO = "2.3.4"\n', encoding="utf-8")
    assert not supervisor._script_tem_balao()
    script.write_text('# x\nVERSAO = "2.3.5"\n', encoding="utf-8")
    assert supervisor._script_tem_balao()
    script.write_text('# x\nVERSAO = "2.10.0"\n', encoding="utf-8")
    assert supervisor._script_tem_balao()


def test_supervisor_desligado_no_filtros(supervisor, tmp_path, monkeypatch):
    filtros = tmp_path / "filtros.json"
    filtros.write_text('{"alerta_falha_notificar": false}', encoding="utf-8")
    monkeypatch.setattr(supervisor, "FILTROS", str(filtros))
    assert supervisor._avisos_ligados() is False
    filtros.write_text("{}", encoding="utf-8")
    assert supervisor._avisos_ligados() is True


def test_supervisor_volta_pelo_heartbeat(supervisor, tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "HEARTBEAT", str(tmp_path / "heartbeat.json"))
    (tmp_path / "heartbeat.json").write_text('{"pid": 7, "ultimo_ok_ts": 500}')
    assert supervisor.voltou_a_funcionar(7, 400) and not supervisor.voltou_a_funcionar(7, 600)
    assert not supervisor.voltou_a_funcionar(8, 400)


def test_supervisor_real_tres_quedas_pede_suporte(tmp_path):
    import shutil
    shutil.copy(os.path.join(RAIZ, "iniciar_monitor.pyw"), tmp_path)
    contador = tmp_path / "contador.txt"
    (tmp_path / "SCRIPT_EXPLICA.py").write_text(textwrap.dedent(f"""
        import os, sys
        c = r'{contador}'
        n = int(open(c).read()) if os.path.exists(c) else 0
        open(c, 'w').write(str(n + 1))
        sys.exit(1 if n < 3 else 0)
    """), encoding="utf-8")
    ambiente = dict(os.environ, MONITOR_SUP_SEM_BALAO="1", MONITOR_SUP_ESPERA_BASE="1")
    ambiente.pop("MONITOR_SUPERVISOR", None)
    r = subprocess.run([sys.executable, str(tmp_path / "iniciar_monitor.pyw"), "--console"],
                       capture_output=True, text=True, errors="replace", timeout=120, env=ambiente)
    log = (tmp_path / "logs" / "supervisor.log").read_text(encoding="utf-8")
    assert r.returncode == 0 and contador.read_text() == "4"
    assert "Aviso de reinício por erro para o monitor novo: erro inesperado (o monitor fechou sozinho, código 1)" in log
    assert "Monitor de senhas não conseguiu voltar — avise o suporte" in log
    assert log.count("Aviso ao usuário: Monitor de senhas não conseguiu voltar") == 1   # não repete em 10 min
    assert "reiniciado" not in log.split("Aviso ao usuário:")[-1].split("\n")[0]



# --- 2.3.6: balão só quando o reinício é por ERRO ------------------------------------------
@pytest.fixture
def rein(monitor, monkeypatch, tmp_path):
    caminho = tmp_path / "reinicio_erro.json"
    monkeypatch.setattr(monitor, "ARQ_REINICIO_ERRO", str(caminho))
    monkeypatch.setattr(monitor, "ARQ_ESTADO_WATCHDOG", str(tmp_path / "watchdog_estado.json"))
    monkeypatch.setattr(monitor, "_limpeza_para_sair", lambda: None)
    monkeypatch.setattr(monitor, "_salvar_pausa", lambda: None)
    relancou = []
    monkeypatch.setattr(monitor, "relancar_processo_agora", lambda: relancou.append(1))
    monkeypatch.setattr(monitor, "_ENCERRANDO", False)
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    monkeypatch.setattr(monitor, "internet_ok", lambda timeout=5: True)
    monkeypatch.setattr(monitor, "log_msg", lambda m: None)
    monkeypatch.setattr(monitor, "_ultimo_reinicio_processo", lambda agora=None: 0.0)
    return {"arq": caminho, "relancou": relancou}


def _motivo_gravado(rein):
    return json.loads(rein["arq"].read_text(encoding="utf-8"))["motivo"] if rein["arq"].exists() else None


@pytest.mark.parametrize("chamar, esperado", [
    (lambda m: m._reiniciar_pela_escada("relogin falhou 2x seguidas no modo requisição"),
     "Sessão não voltou após 2 tentativas"),
    (lambda m: m._reiniciar_pela_escada("modo requisição sem resposta boa há 185 s"),
     "3 min sem resposta do Feegow"),
    (lambda m: m.reiniciar_processo("monitor travado mesmo após fechar o Chrome (x)", por_erro=True,
                                    curto="Monitor travado (sem resposta)"),
     "Monitor travado (sem resposta)"),
])
def test_reinicio_por_erro_grava_o_aviso(monitor, rein, chamar, esperado):
    assert chamar(monitor) is True and rein["relancou"] == [1]
    assert _motivo_gravado(rein) == esperado


def test_escada_e_cronometro_gravam(monitor, rein, monkeypatch):
    monkeypatch.setattr(monitor, "decidir_reinicio_por_falha",
                        lambda *a, **k: ("reiniciar", "monitorando sem nenhum ciclo OK há 4 min"))
    monkeypatch.setattr(monitor, "_reinicio_bloqueado_ate", lambda agora=None: 0.0)
    assert monitor._checar_reinicio_por_falha(time.time()) is True
    assert _motivo_gravado(rein) == "4 min sem resposta do Feegow"
    rein["arq"].unlink()
    monkeypatch.setattr(monitor, "_ENCERRANDO", False)   # o 1º reinício (gravador) marcou encerrando
    monkeypatch.setattr(monitor, "decidir_cronometro_login", lambda *a, **k: ("reiniciar", "login travado"))
    monkeypatch.setattr(monitor, "_LOGIN_INICIO", time.time() - 300)
    assert monitor._checar_cronometro_login(time.time()) is True
    assert _motivo_gravado(rein) == "Login demorou mais de 2 min"


@pytest.mark.parametrize("motivo", ["reinicio programado (60 min)", "atualização 2.3.5 -> 2.3.6",
                                    "pedido do usuário (bandeja)", "pedido externo (logs/reiniciar.pedido)",
                                    "rollback da atualização"])
def test_reinicio_normal_nao_avisa(monitor, rein, motivo):
    assert monitor.reiniciar_processo(motivo, ignorar_limite=True) is True
    assert rein["relancou"] == [1] and not rein["arq"].exists()


def test_reinicio_programado_pelo_watchdog_nao_avisa(monitor, rein, monkeypatch):
    monkeypatch.setattr(monitor, "decidir_reinicio_programado",
                        lambda *a, **k: ("reiniciar", "reinicio programado (60 min)"))
    assert monitor._checar_reinicio_programado(time.time()) is True
    assert rein["relancou"] == [1] and not rein["arq"].exists()


def test_processo_novo_mostra_o_balao_e_apaga(monitor, rein, monkeypatch):
    baloes = []
    monkeypatch.setattr(monitor, "avisar_balao", lambda *a, **k: baloes.append((a, k)))
    assert monitor.mostrar_aviso_reinicio_erro() is False and baloes == []     # sem arquivo
    monitor.gravar_aviso_reinicio_erro("Sessão não voltou após 2 tentativas")
    assert monitor.mostrar_aviso_reinicio_erro() is True
    (tipo, titulo, texto), extra = baloes[0]
    assert (tipo, texto) == ("reinicio", "Sessão não voltou após 2 tentativas")
    assert "reiniciado após erro" in titulo and extra["segundos"] == 10
    assert not rein["arq"].exists()
    # aviso velho (PC desligado no meio) é descartado
    monitor.gravar_aviso_reinicio_erro("x")
    assert monitor.mostrar_aviso_reinicio_erro(agora=time.time() + 3600) is False and len(baloes) == 1


def test_motivo_curto(monitor):
    m = monitor.motivo_curto_reinicio
    assert m("relogin falhou 3x seguidas no modo requisição") == "Sessão não voltou após 3 tentativas"
    assert m("3 reaberturas do Chrome seguidas sem nenhum ciclo OK (em 10 min)") == "O navegador caiu 3 vezes seguidas"
    assert m("sem nenhum ciclo OK há 11 min (etapa 'login')") == "11 min sem resposta do Feegow"
    assert m("qualquer coisa") == "erro inesperado"


def test_balao_com_tempo_proprio(ger):
    g = ger["g"]
    g.avisar("reinicio", "reiniciado", "x", segundos=10)
    g.tick(100)
    g.tick(109.9)
    assert g.atual is not None
    g.tick(110.1)
    assert g.atual is None


def test_desenho_icone_reinicio(monitor):
    img, geo = monitor.desenhar_balao("reinicio", "🔄 Monitor de senhas reiniciado após erro", "Login demorou mais de 2 min")
    assert geo["altura"] > 50


def test_supervisor_relancamento_normal_nao_avisa(tmp_path):
    import shutil
    shutil.copy(os.path.join(RAIZ, "iniciar_monitor.pyw"), tmp_path)
    contador = tmp_path / "contador.txt"
    (tmp_path / "SCRIPT_EXPLICA.py").write_text(textwrap.dedent(f"""
        import os, sys
        c = r'{contador}'
        n = int(open(c).read()) if os.path.exists(c) else 0
        open(c, 'w').write(str(n + 1))
        sys.exit(75 if n < 2 else 0)
    """), encoding="utf-8")
    ambiente = dict(os.environ, MONITOR_SUP_SEM_BALAO="1")
    ambiente.pop("MONITOR_SUPERVISOR", None)
    r = subprocess.run([sys.executable, str(tmp_path / "iniciar_monitor.pyw"), "--console"],
                       capture_output=True, text=True, errors="replace", timeout=60, env=ambiente)
    log = (tmp_path / "logs" / "supervisor.log").read_text(encoding="utf-8")
    assert r.returncode == 0 and contador.read_text() == "3"
    assert not (tmp_path / "logs" / "reinicio_erro.json").exists()
    assert "Aviso" not in log
