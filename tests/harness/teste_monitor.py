"""Bateria completa do Monitor de Senha (watchdog, atualização, áudio, supervisor, Chrome).

Uso:  python tests/harness/teste_monitor.py [--sem-chrome]
Roda numa cópia (sandbox/ ao lado deste arquivo), com páginas simuladas e dados
fictícios — NUNCA acessa o Feegow real (o "Feegow" é um servidor local em 127.0.0.1).
Com Chrome: um Chrome headless e depois um Chrome real fora da tela (um de cada vez).
Também roda pelo pytest: MONITOR_TESTES_CHROME=1 python -m pytest tests -k harness
"""
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
# Funciona dentro do repositório (tests/harness) e na pasta de teste ao lado dele (teste/)
REPO = os.environ.get("MONITOR_REPO") or (
    os.path.join(os.path.dirname(AQUI), "repo") if os.path.isdir(os.path.join(os.path.dirname(AQUI), "repo"))
    else os.path.dirname(os.path.dirname(AQUI)))
SANDBOX = os.path.join(AQUI, "sandbox")
# %LOCALAPPDATA% isolado: perfil do Chrome, drivers e cache de áudio do teste ficam
# na sandbox (o monitor nunca mata/usa o Chrome de um monitor real desta máquina).
LOCALAPPDATA_REAL = os.environ.get("LOCALAPPDATA", "")
LOCALAPPDATA_TESTE = os.path.join(AQUI, "sandbox_localappdata")
RESULTADOS = []
sys.path.insert(0, AQUI)
import feegow_simulado  # noqa: E402


class _SaidaInterceptada(BaseException):
    """os._exit() trocado por esta exceção nos testes de unidade."""


def interceptar_saidas(m):
    """Troca os._exit e relancar_processo_agora por gravadores. Devolve (chamadas, restaurar)."""
    chamadas = []
    original = os._exit

    def saida_falsa(codigo):
        chamadas.append(("exit", codigo))
        raise _SaidaInterceptada(codigo)
    os._exit = saida_falsa
    m.relancar_processo_agora = lambda: chamadas.append(("relancar",))

    def restaurar():
        os._exit = original
    return chamadas, restaurar


def ok(nome, condicao, detalhe=""):
    RESULTADOS.append((nome, bool(condicao), detalhe))
    print(("  [OK]   " if condicao else "  [FALHA]") + f" {nome}" + (f" — {detalhe}" if detalhe else ""), flush=True)


def preparar_sandbox():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX)
    os.makedirs(LOCALAPPDATA_TESTE, exist_ok=True)
    drivers_reais = os.path.join(LOCALAPPDATA_REAL, "MonitorFeegow", "drivers")
    drivers_teste = os.path.join(LOCALAPPDATA_TESTE, "MonitorFeegow", "drivers")
    if os.path.isdir(drivers_reais) and not os.path.isdir(drivers_teste):
        shutil.copytree(drivers_reais, drivers_teste)   # evita baixar o chromedriver de novo
    os.environ["LOCALAPPDATA"] = LOCALAPPDATA_TESTE
    for nome in ("SCRIPT_EXPLICA.py", "filtros.json", "iniciar_monitor.pyw"):
        shutil.copy(os.path.join(REPO, nome), SANDBOX)
    with open(os.path.join(SANDBOX, "credenciais.json"), "w", encoding="utf-8") as f:
        json.dump({"usuario": "teste@teste.local", "senha": "senha-ficticia", "rota_login": "auto",
                   "url_lista_espera": "file:///" + os.path.join(AQUI, "ListaEspera_simulada.html").replace("\\", "/")}, f)


def importar_monitor():
    spec = importlib.util.spec_from_file_location("monitor", os.path.join(SANDBOX, "SCRIPT_EXPLICA.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------------------
def teste_audio(m):
    print("\n== Áudio: só a saída padrão + anti-repetição + sem fala sobreposta")
    m.DISPOSITIVOS_AUDIO[:] = ["Alto-falantes (Realtek)", "Monitor HDMI (NVIDIA)", "Fone USB"]
    cfg = dict(m.carregar_filtros(forcar=True))
    cfg["saida_audio"] = "padrao"
    ok("saida_audio=padrao toca 1 vez, no dispositivo padrão", m.dispositivos_de_saida(cfg) == [None])
    cfg["saida_audio"] = "todos"
    ok("saida_audio=todos mantém o modo antigo (3 dispositivos)", len(m.dispositivos_de_saida(cfg)) == 3)
    cfg["saida_audio"] = "fone usb"
    ok("saida_audio=<nome> escolhe um dispositivo", m.dispositivos_de_saida(cfg) == ["Fone USB"])
    cfg["saida_audio"] = "inexistente"
    ok("dispositivo inexistente cai na saída padrão", m.dispositivos_de_saida(cfg) == [None])

    # O loop do alarme: conta quantas vezes a fala sai num ciclo
    tocadas = []
    original = m._tocar_audio_sem_trava
    m._tocar_audio_sem_trava = lambda disp, arq, *a: tocadas.append(disp) or True
    falso_mp3 = os.path.join(SANDBOX, "falso.mp3")
    with open(falso_mp3, "wb") as f:
        f.write(b"ID3")
    frases = []
    obter_original = m.obter_audio
    m.obter_audio = lambda frase, usar_rede=True: frases.append(frase) or falso_mp3
    filtros_orig = m.carregar_filtros
    m.carregar_filtros = lambda forcar=False: dict(filtros_orig(), saida_audio="padrao",
                                                   alarme_max_segundos=1, intervalo_repeticao_segundos=5)
    m._TICKET_ALARME = "DILATAÇÃO - CCC"
    m.alarme_ativo = True
    agendados = []
    m.root.after = lambda *a, **k: agendados.append(a)
    m.loop_audio_alarme()
    m.alarme_ativo = False
    ok("um ciclo do alarme fala UMA vez (antes: uma por dispositivo)", tocadas == [None], f"tocadas={tocadas}")
    ok("a fala vem da senha real da tela (nada fixo)", frases[:1] == ["Atenção! Senha Dilatação - CCC."], str(frases[:1]))
    m.obter_audio = obter_original

    m.carregar_filtros = lambda forcar=False: dict(filtros_orig(), anti_repeticao_segundos=60)
    ok("mesma senha falada agora há pouco -> não repete", m.anunciado_recentemente("DILATAÇÃO - CCC"))
    ok("outra senha -> pode falar", not m.anunciado_recentemente("DILATAÇÃO - MMM"))
    m._ULTIMO_ANUNCIO[m.normalizar_texto("DILATAÇÃO - CCC")] = time.time() - 61
    ok("passada a janela de 60 s -> fala de novo", not m.anunciado_recentemente("DILATAÇÃO - CCC"))
    m.carregar_filtros = lambda forcar=False: dict(filtros_orig(), anti_repeticao_segundos=0)
    m._ULTIMO_ANUNCIO[m.normalizar_texto("X")] = time.time()
    ok("anti_repeticao_segundos=0 desliga a proteção", not m.anunciado_recentemente("X"))
    m.carregar_filtros = filtros_orig

    # Duas threads tentando falar ao mesmo tempo: nunca sobrepõe
    ativos, maximo = [0], [0]

    def falso(disp, arq, *a):
        ativos[0] += 1
        maximo[0] = max(maximo[0], ativos[0])
        time.sleep(0.2)
        ativos[0] -= 1
        return True
    m._tocar_audio_sem_trava = falso
    threads = [threading.Thread(target=m.tocar_audio_em_dispositivo, args=(None, "x")) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ok("3 falas simultâneas na mesma instância saem uma de cada vez", maximo[0] == 1, f"máx. sobrepostas={maximo[0]}")
    m._tocar_audio_sem_trava = original

    # Cache permanente + voz offline do Windows (SAPI) quando o gTTS falha
    frase = f"Teste de cache {int(time.time())}."
    gtts_original = m._gerar_mp3_gtts

    def gtts_fora(frase, destino):
        raise OSError("sem internet (simulado)")
    m._gerar_mp3_gtts = gtts_fora
    m._GTTS_FALHOU_EM = 0.0
    inicio = time.time()
    arquivo = m.obter_audio(frase)
    ok("gTTS fora do ar: gera a fala com a voz do Windows (WAV) no cache",
       bool(arquivo) and arquivo.endswith(".wav") and os.path.getsize(arquivo) > 1000,
       f"{arquivo and os.path.basename(arquivo)} em {time.time() - inicio:.1f}s")
    ok("cache fica em %LOCALAPPDATA%/MonitorFeegow/audio", bool(arquivo) and
       os.path.normcase(os.path.join(LOCALAPPDATA_TESTE, "MonitorFeegow", "audio")) in os.path.normcase(arquivo))
    inicio = time.time()
    ok("segunda vez sai do cache, na hora", m.obter_audio(frase) == arquivo and time.time() - inicio < 0.5)
    m._gerar_mp3_gtts = gtts_original
    logs = []
    log_original = m.log_msg
    m.log_msg = lambda t: logs.append(t)
    m._FALHAS_AUDIO_LOGADAS.clear()
    for _ in range(3):
        m._registrar_falha_audio("reproducao", RuntimeError("x"))
    m.log_msg = log_original
    ok("falha de áudio vai para o log UMA vez por tipo", len(logs) == 1, str(len(logs)))
    m._definir_sem_audio("teste")
    ok("estado 'Sem áudio' aparece no status do painel/bandeja", "Sem áudio" in m._texto_status()[1])
    m._definir_sem_audio("")


def teste_instancia_unica():
    print("\n== Instância única: duas cópias do monitor -> só uma fica (só uma fala)")
    fonte = open(os.path.join(SANDBOX, "SCRIPT_EXPLICA.py"), encoding="utf-8").read()
    arvore = ast.parse(fonte)
    quero = {"garantir_instancia_unica", "_liberar_instancia_unica"}
    partes = ["import ctypes, os, sys, time", "NOME_MUTEX = 'Local\\\\MonitorFeegowSenhaTESTE'", "_MUTEX = None"]
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and no.name in quero:
            partes.append(ast.get_source_segment(fonte, no))
    partes.append(textwrap.dedent("""
        if not garantir_instancia_unica():
            sys.exit(3)
        print('FALOU', flush=True)   # só a instância que segura a trava chega aqui
        time.sleep(float(sys.argv[1]))
    """))
    script = os.path.join(SANDBOX, "_instancia.py")
    open(script, "w", encoding="utf-8").write("\n\n".join(partes))
    primeira = subprocess.Popen([sys.executable, script, "8"], stdout=subprocess.PIPE, text=True)
    time.sleep(1.5)
    inicio = time.time()
    segunda = subprocess.run([sys.executable, script, "0"], capture_output=True, text=True)
    ok("a 2ª instância sai com código 3 (outra instância)", segunda.returncode == 3,
       f"rc={segunda.returncode}, esperou {time.time() - inicio:.1f}s")
    ok("a 2ª instância não falou", "FALOU" not in segunda.stdout)
    primeira.wait()
    ok("a 1ª instância falou normalmente", "FALOU" in primeira.stdout.read())
    terceira = subprocess.run([sys.executable, script, "0"], capture_output=True, text=True)
    ok("depois que a 1ª sai, uma nova pode rodar", terceira.returncode == 0 and "FALOU" in terceira.stdout)


def teste_atualizacao(m):
    print("\n== Atualização automática: validação, instalação, confirmação e rollback")
    fonte = open(os.path.join(SANDBOX, "SCRIPT_EXPLICA.py"), "rb").read().replace(b"\r\n", b"\n")
    nova = fonte.replace(f'VERSAO = "{m.VERSAO}"'.encode(), b'VERSAO = "9.9.9"')
    sha = hashlib.sha256(nova).hexdigest()

    def rejeita(nome, bruto, versao="9.9.9", sha256=""):
        try:
            m.validar_script_baixado(bruto, versao, sha256)
            ok(nome, False, "aceitou")
        except Exception as e:
            ok(nome, True, f"{type(e).__name__}: {str(e)[:60]}")

    try:
        ok("aceita o script válido (cabeçalho, VERSAO, sha256, compila)", m.validar_script_baixado(nova, "9.9.9", sha))
    except Exception as e:
        ok("aceita o script válido", False, str(e))
    def sha_de(b):
        return hashlib.sha256(b).hexdigest()
    rejeita("rejeita arquivo pequeno (página de erro 404)", b"404: Not Found", sha256=sha_de(b"404: Not Found"))
    rejeita("rejeita sem sha256 no versao.json (agora obrigatório)", nova, sha256="")
    errado = b"# outro arquivo\n" + nova
    rejeita("rejeita cabeçalho errado", errado, sha256=sha_de(errado))
    rejeita("rejeita VERSAO diferente do versao.json", nova, versao="9.9.8", sha256=sha)
    rejeita("rejeita sha256 diferente", nova, sha256="0" * 64)
    quebrado = nova + b"\ndef quebrado(:\n"
    rejeita("rejeita erro de sintaxe", quebrado, sha256=sha_de(quebrado))

    remoto = json.load(open(os.path.join(REPO, "versao.json"), encoding="utf-8"))
    try:
        ok("versao.json do repositório: assinatura Ed25519 válida", m.validar_versao_assinada(remoto, "main"))
    except Exception as e:
        ok("versao.json do repositório: assinatura Ed25519 válida", False, str(e))
    for nome, alterar, canal in (("rejeita versao.json adulterado (sha trocado)", {"sha256": "1" * 64}, "main"),
                                 ("rejeita versao.json sem assinatura", {"assinatura": ""}, "main"),
                                 ("rejeita versao.json de outro canal", {}, "beta")):
        try:
            m.validar_versao_assinada(dict(remoto, **alterar), canal)
            ok(nome, False, "aceitou")
        except ValueError as e:
            ok(nome, True, str(e)[:70])
    filtros_orig = m.carregar_filtros
    m.carregar_filtros = lambda forcar=False: dict(m.FILTROS_PADRAO, atualizacao_canal="minha-branch")
    ok("canal fora de main/beta cai na main", m._canal_atualizacao() == "main")
    m.carregar_filtros = lambda forcar=False: dict(m.FILTROS_PADRAO, atualizacao_canal="beta")
    ok("canal beta é aceito", m._canal_atualizacao() == "beta")
    m.carregar_filtros = filtros_orig
    ok("compara versões numericamente (2.10.0 > 2.9.1)", m._versao_tupla("2.10.0") > m._versao_tupla("2.9.1"))

    # Instalação numa pasta temporária
    pasta = tempfile.mkdtemp(prefix="atualiza_", dir=SANDBOX)
    alvo = os.path.join(pasta, "SCRIPT_EXPLICA.py")
    open(alvo, "wb").write(fonte)
    m.ARQ_ESTADO_ATUALIZACAO = os.path.join(pasta, "atualizacao_estado.json")
    m._caminho_programa_atual = lambda: alvo
    m._baixar = lambda url, timeout=60, limite=0: nova
    # NÃO mocka reiniciar_processo (o mock escondia o bug da 2.0.0): só intercepta
    # a saída final do processo e o relançamento.
    chamadas, restaurar = interceptar_saidas(m)
    try:
        m.aplicar_atualizacao({"versao": "9.9.9", "sha256": sha}, {})
    except _SaidaInterceptada:
        pass
    finally:
        restaurar()
    m._ENCERRANDO = False
    estado = json.load(open(m.ARQ_ESTADO_ATUALIZACAO, encoding="utf-8"))
    ok("instala: arquivo novo no lugar", open(alvo, "rb").read() == nova)
    ok("instala: backup .anterior guardado", open(alvo + ".anterior", "rb").read() == fonte)
    ok("instala: estado 'em_teste' e RELANÇA (sem sair com código 0 antes)",
       estado.get("status") == "em_teste" and chamadas == [("relancar",)],
       f"status={estado.get('status')}, chamadas={chamadas}")

    # Inícios sem confirmar -> rollback
    versao_real = m.VERSAO
    m.VERSAO = "9.9.9"
    relancou = []
    m.relancar_processo_agora = lambda: relancou.append(1)
    for _ in range(m.MAX_INICIOS_SEM_CONFIRMAR):
        m._checar_atualizacao_em_teste()
    ok(f"{m.MAX_INICIOS_SEM_CONFIRMAR} inícios sem confirmar: ainda em teste", not relancou)
    m._checar_atualizacao_em_teste()
    estado = json.load(open(m.ARQ_ESTADO_ATUALIZACAO, encoding="utf-8"))
    ok("4º início sem confirmar: volta para a versão anterior e relança",
       relancou and open(alvo, "rb").read() == fonte and estado.get("status") == "revertida",
       f"status={estado.get('status')}")
    ok("a versão revertida fica bloqueada (não reinstala em loop)", "9.9.9" in estado.get("bloqueadas", []))

    # Confirmação
    json.dump({"status": "em_teste", "para": "9.9.9", "de": versao_real}, open(m.ARQ_ESTADO_ATUALIZACAO, "w"))
    m.confirmar_atualizacao("teste")
    ok("primeiro ciclo OK confirma a versão nova",
       json.load(open(m.ARQ_ESTADO_ATUALIZACAO)).get("status") == "confirmada")
    m.VERSAO = versao_real

    # Momento seguro
    m.alarme_ativo = True
    ok("não atualiza com alarme tocando", not m.momento_seguro_para_atualizar())
    m.alarme_ativo = False
    m._ETAPA, m._ULTIMO_OK = "monitorando", time.time()
    ok("atualiza com a página OK e sem chamada", bool(m.momento_seguro_para_atualizar()))

    # .exe exige sha256
    try:
        m.validar_exe_baixado(b"MZ" + b"0" * 6_000_000, "")
        ok("exe sem sha256_exe é recusado", False)
    except ValueError:
        ok("exe sem sha256_exe é recusado", True)


def importar_supervisor():
    import importlib.machinery
    carregador = importlib.machinery.SourceFileLoader("supervisor_teste", os.path.join(SANDBOX, "iniciar_monitor.pyw"))
    spec = importlib.util.spec_from_loader("supervisor_teste", carregador)
    modulo = importlib.util.module_from_spec(spec)
    carregador.exec_module(modulo)
    modulo.HEARTBEAT = os.path.join(SANDBOX, "nao_existe_heartbeat.json")
    return modulo


def teste_supervisor():
    print("\n== Supervisor (iniciar_monitor.pyw): relança no código 75 e para no 0")
    pasta = tempfile.mkdtemp(prefix="sup_", dir=SANDBOX)
    shutil.copy(os.path.join(SANDBOX, "iniciar_monitor.pyw"), pasta)
    contador = os.path.join(pasta, "contador.txt")
    open(os.path.join(pasta, "SCRIPT_EXPLICA.py"), "w").write(textwrap.dedent(f"""
        import os, sys
        n = int(open(r'{contador}').read()) if os.path.exists(r'{contador}') else 0
        open(r'{contador}', 'w').write(str(n + 1))
        assert os.environ.get('MONITOR_SUPERVISOR') == '1'
        sys.exit(75 if n == 0 else (1 if n == 1 else 0))
    """))
    inicio = time.time()
    r = subprocess.run([sys.executable, os.path.join(pasta, "iniciar_monitor.pyw"), "--console"],
                       capture_output=True, text=True, errors="replace", timeout=60)
    execucoes = int(open(contador).read())
    log = open(os.path.join(pasta, "logs", "supervisor.log"), encoding="utf-8").read()
    ok("75 = relança na hora; queda = relança com espera; 0 = para", execucoes == 3 and r.returncode == 0,
       f"execuções={execucoes}, rc={r.returncode}, {time.time() - inicio:.1f}s")
    ok("supervisor registra cada evento em logs/supervisor.log",
       "pediu reinício" in log and "caiu" in log and "encerrado pelo usuário" in log)

    # Monitor congelado: grava UM heartbeat e trava. O supervisor mata pelo PID e relança.
    pasta = tempfile.mkdtemp(prefix="sup_hb_", dir=SANDBOX)
    shutil.copy(os.path.join(SANDBOX, "iniciar_monitor.pyw"), pasta)
    contador = os.path.join(pasta, "contador.txt")
    open(os.path.join(pasta, "SCRIPT_EXPLICA.py"), "w").write(textwrap.dedent(f"""
        import os, sys, json, time
        n = int(open(r'{contador}').read()) if os.path.exists(r'{contador}') else 0
        open(r'{contador}', 'w').write(str(n + 1))
        if n >= 1:
            sys.exit(0)
        os.makedirs(r'{pasta}\\logs', exist_ok=True)
        json.dump({{"pid": os.getpid(), "ts": time.time()}}, open(r'{pasta}\\logs\\heartbeat.json', 'w'))
        time.sleep(600)   # "travado"
    """))
    ambiente = dict(os.environ, MONITOR_SUP_LIMITE_HEARTBEAT="4", MONITOR_SUP_FOLGA="1", MONITOR_SUP_INTERVALO="1")
    inicio = time.time()
    r = subprocess.run([sys.executable, os.path.join(pasta, "iniciar_monitor.pyw"), "--console"],
                       capture_output=True, text=True, errors="replace", timeout=120, env=ambiente)
    log = open(os.path.join(pasta, "logs", "supervisor.log"), encoding="utf-8").read()
    ok("heartbeat parado: supervisor mata o monitor travado (por PID) e relança",
       "sem heartbeat" in log and int(open(contador).read()) == 2 and r.returncode == 0,
       f"{time.time() - inicio:.0f}s, rc={r.returncode}")
    # monitor antigo (não grava heartbeat) nunca é morto
    sup = importar_supervisor()
    ok("sem heartbeat do PID (versão antiga) nunca conta como travado",
       not sup.heartbeat_parado(999999, 0, time.time() + 10 ** 6))


# ---------------------------------------------------------------------------
def teste_chrome(m):
    print("\n== Watchdog com Chrome headless e a página simulada")
    from selenium import webdriver
    perfil = os.path.join(SANDBOX, "chrome_perfil_teste")
    os.makedirs(perfil, exist_ok=True)
    m._pasta_perfil_chrome = lambda: perfil   # o watchdog só mata o Chrome DESTE perfil
    opcoes = webdriver.ChromeOptions()
    for argumento in m.ARGUMENTOS_CHROME:
        if not argumento.startswith("--window-position"):
            opcoes.add_argument(argumento)
    opcoes.add_argument("--headless=new")
    opcoes.add_argument("--allow-file-access-from-files")
    opcoes.add_argument(f"--user-data-dir={perfil}")
    m.driver = m.criar_driver_chrome(opcoes)
    m.driver.set_page_load_timeout(20)
    m.driver.set_script_timeout(10)

    url = "file:///" + os.path.join(AQUI, "ListaEspera_simulada.html").replace("\\", "/")
    filtros_orig = m.carregar_filtros
    extra = {}
    m.carregar_filtros = lambda forcar=False: dict(filtros_orig(), intervalo_busca_segundos=1,
                                                   watchdog_limite_sem_batimento_segundos=20, **extra)
    m.carregar_credenciais = lambda: {"url_lista_espera": url}
    alertas, logins = [], []
    m.disparar_estado_alarme = lambda: alertas.append(m._TICKET_ALARME)
    m.root.after = lambda *a, **k: None
    m.fazer_login = lambda: (logins.append(1), True)[1]
    internet = {"ok": True}
    m.internet_ok = lambda timeout=5: internet["ok"]
    retorno = {}

    def rodar():
        retorno["motivo"] = m.verificar_botao_chamar_senha()
    t = threading.Thread(target=rodar, daemon=True)
    t.start()

    def esperar(cond, segundos):
        fim = time.time() + segundos
        while time.time() < fim:
            if cond():
                return True
            time.sleep(0.3)
        return False

    def js(codigo):
        try:
            m.driver.execute_script(codigo)
        except Exception:
            pass

    ok("ciclo normal: registra batimento", esperar(lambda: m._ULTIMO_OK > 0, 30))
    ok("avalia TODAS as senhas do modal e alerta a de dilatação",
       esperar(lambda: "DILATAÇÃO - CCC" in alertas, 15), f"alertas={alertas[:2]}")
    ok("estado da página = ok_com_botao", m.diagnosticar_pagina()[0] == "ok_com_botao")
    ok("ciclo leve (1 execute_async_script) em uso e rápido",
       m._CICLO_LEVE["ok"] > 0 and not m._CICLO_LEVE["desligado"] and m._CICLO_LEVE["ms"] < 3000,
       f"ciclos leves={m._CICLO_LEVE['ok']}, último={m._CICLO_LEVE['ms']} ms")
    textos = m.ciclo_leve(m.carregar_filtros())
    ok("ciclo leve devolve TODAS as senhas do modal",
       textos is not None and "AGENDAMENTOS" in textos and "DILATAÇÃO - CCC" in textos, str(textos))
    js("fecharModal()")
    extra["ciclo_leve"] = False
    marca = time.time()
    ok("com ciclo_leve=false o caminho antigo continua funcionando",
       esperar(lambda: m._ULTIMO_OK > marca + 1, 40))
    extra.pop("ciclo_leve")

    js("simular('sem_botao')")
    ok("botão sumiu: detecta 'ok_sem_botao' e recarrega (N1)",
       esperar(lambda: m._ULTIMO_RELOAD_SEM_BOTAO > 0, 40))

    antes = len(logins)
    js("simular('login')")
    ok("caiu no login: detecta e refaz o login (N1)", esperar(lambda: len(logins) > antes, 40),
       f"logins={len(logins)}")
    ok("volta a monitorar depois do login", esperar(lambda: time.time() - m._ULTIMO_OK < 5, 30))

    js("simular('branca')")
    marca = time.time()
    ok("tela branca: recarrega e volta ao normal", esperar(lambda: m._ULTIMO_OK > marca + 1, 40))

    internet["ok"] = False
    js("simular('branca')")
    ok("sem internet: entra em 'offline' sem reiniciar nada", esperar(lambda: m._ETAPA == "offline", 40),
       f"etapa={m._ETAPA}")
    internet["ok"] = True
    marca = time.time()
    ok("internet voltou: retoma o monitoramento", esperar(lambda: m._ULTIMO_OK > marca, 40))

    # Página travada (loop infinito no JS): só o watchdog resolve
    js("simular('travar')")
    time.sleep(3)
    m._SELENIUM_RODANDO = True                  # como se o loop_supervisionado estivesse rodando
    m._ULTIMO_PROGRESSO = time.time() - 30     # simula 30 s sem batimento (limite do teste: 20 s)
    m._passo_watchdog(time.time())
    ok("página travada: watchdog pede reabrir o Chrome (N2)", bool(m._PEDIDO_REINICIO_CHROME),
       m._PEDIDO_REINICIO_CHROME)
    ok("o loop destrava e devolve o motivo para reabrir", esperar(lambda: "motivo" in retorno, 150),
       str(retorno.get("motivo"))[:80])
    chamadas, restaurar = interceptar_saidas(m)
    m._HORA_PEDIDO_CHROME = time.time() - 400
    m._ULTIMO_PROGRESSO = time.time() - 400
    m._SELENIUM_RODANDO, m._ETAPA = True, "monitorando"
    try:
        m._passo_watchdog(time.time())
    except _SaidaInterceptada:
        pass
    finally:
        restaurar()
    ok("se nem fechando o Chrome a thread volta: reinicia o programa (N3) e relança",
       chamadas == [("relancar",)], str(chamadas))
    m._SELENIUM_RODANDO = False
    m._fechar_chrome()
    m.carregar_filtros = filtros_orig


def teste_reiniciar_unidade(m):
    print("\n== reiniciar_processo(): limpa e RELANÇA; só o encerramento sai com 0")
    chamadas, restaurar = interceptar_saidas(m)
    m.ARQ_ESTADO_WATCHDOG = os.path.join(SANDBOX, "logs", "watchdog_unidade.json")
    try:
        try:
            m.reiniciar_processo("teste de unidade", ignorar_limite=True)
        except _SaidaInterceptada:
            pass
        ok("N3: não sai com código 0 antes de relançar", chamadas == [("relancar",)], str(chamadas))
        chamadas.clear()
        try:
            m._limpeza_para_sair()
        except _SaidaInterceptada:
            pass
        ok("_limpeza_para_sair() não encerra o processo", chamadas == [], str(chamadas))
        chamadas.clear()
        try:
            m.encerrar_sistema()
        except _SaidaInterceptada:
            pass
        ok("encerrar_sistema() sai com código 0", chamadas == [("exit", 0)], str(chamadas))
    finally:
        restaurar()
    # relancar_processo_agora REAL sob o supervisor: sai com 75
    m2 = importar_monitor()
    saidas = []
    original = os._exit
    os.environ["MONITOR_SUPERVISOR"] = "1"

    def saida_falsa(codigo):
        saidas.append(codigo)
        raise _SaidaInterceptada(codigo)
    os._exit = saida_falsa
    try:
        m2.relancar_processo_agora()
    except _SaidaInterceptada:
        pass
    finally:
        os._exit = original
        os.environ.pop("MONITOR_SUPERVISOR", None)
    ok("sob o supervisor o relançamento sai com o código 75", saidas == [m2.CODIGO_SAIDA_REINICIAR], str(saidas))


def teste_reinicio_integrado():
    """Supervisor REAL + SCRIPT_EXPLICA.py REAL (sem mock), Chrome real e Feegow simulado local."""
    print("\n== Integração: supervisor + monitor reais — pedido de reinício volta a monitorar")
    import psutil
    srv = feegow_simulado.iniciar()
    pasta = os.path.join(SANDBOX, "integrado")
    os.makedirs(pasta, exist_ok=True)
    for nome in ("SCRIPT_EXPLICA.py", "iniciar_monitor.pyw"):
        shutil.copy(os.path.join(REPO, nome), pasta)
    filtros = json.load(open(os.path.join(REPO, "filtros.json"), encoding="utf-8-sig"))
    filtros.update({"alerta_tela": False, "alerta_voz": False, "auto_atualizar": False,
                    "intervalo_busca_segundos": 3, "hosts_verificacao_internet": [f"127.0.0.1:{srv.porta}"],
                    "heartbeat_remoto_url": ""})
    json.dump(filtros, open(os.path.join(pasta, "filtros.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"usuario": "teste@teste.local", "senha": "senha-ficticia", "rota_login": "legado",
               "url_login": srv.url_base + "/main/?P=Login",
               "url_login_sso": srv.url_base + "/main/sso/",
               "url_lista_espera": srv.url_base + "/pre-v8/?P=ListaEspera&Pers=1&tickets=AGENDAMENTOS"},
              open(os.path.join(pasta, "credenciais.json"), "w", encoding="utf-8"))
    log_monitor = os.path.join(pasta, "logs", "monitor.log")
    log_sup = os.path.join(pasta, "logs", "supervisor.log")
    ambiente = dict(os.environ, LOCALAPPDATA=LOCALAPPDATA_TESTE, PYTHONIOENCODING="utf-8")
    ambiente.pop("MONITOR_SUPERVISOR", None)
    saida = open(os.path.join(pasta, "saida_console.txt"), "w", encoding="utf-8")
    sup = subprocess.Popen([sys.executable, os.path.join(pasta, "iniciar_monitor.pyw"), "--console"],
                           cwd=pasta, env=ambiente, stdout=saida, stderr=subprocess.STDOUT)

    def ler(caminho):
        try:
            return open(caminho, encoding="utf-8", errors="replace").read()
        except OSError:
            return ""

    def esperar(cond, segundos):
        fim = time.time() + segundos
        while time.time() < fim:
            if cond():
                return True
            if sup.poll() is not None:
                return cond()
            time.sleep(1)
        return False

    ciclos = lambda: ler(log_monitor).count("AGENDAMENTOS")  # noqa: E731
    try:
        ok("1º processo: abre o Chrome, entra no Feegow simulado e monitora",
           esperar(lambda: ciclos() >= 1, 180), f"ciclos={ciclos()}")
        try:
            hb = json.load(open(os.path.join(pasta, "logs", "heartbeat.json")))
            filhos = [c.pid for c in psutil.Process(sup.pid).children()]
        except Exception as e:
            hb, filhos = {"erro": str(e)}, []
        ok("o monitor real grava logs/heartbeat.json com o próprio PID", hb.get("pid") in filhos,
           f"hb={hb.get('pid')}, filhos={filhos}")
        cred = json.load(open(os.path.join(pasta, "credenciais.json"), encoding="utf-8"))
        ok("credenciais.json: senha migrada para DPAPI na máquina real",
           "senha" not in cred and str(cred.get("senha_protegida", "")).startswith("dpapi:"))
        ok("ciclo leve ativo no monitor real", "Ciclo leve desligado" not in ler(log_monitor))
        inicios_antes = ler(log_monitor).count("iniciando (pasta")
        open(os.path.join(pasta, "logs", "reiniciar.pedido"), "w").close()
        ok("pedido de reinício: o monitor sai com 75 e o supervisor RELANÇA",
           esperar(lambda: "pediu reinício" in ler(log_sup)
                   and ler(log_monitor).count("iniciando (pasta") > inicios_antes, 90),
           "supervisor.log: " + " / ".join(l.split("| ")[-1] for l in ler(log_sup).strip().splitlines()[-3:]))
        ok("o supervisor NÃO saiu (antes saía com código 0)", sup.poll() is None, f"rc={sup.poll()}")
        marca = len(ler(log_monitor))
        ok("2º processo volta a monitorar depois do reinício",
           esperar(lambda: "AGENDAMENTOS" in ler(log_monitor)[marca:], 180))
        open(os.path.join(pasta, "logs", "encerrar.pedido"), "w").close()
        try:
            rc = sup.wait(timeout=90)
        except subprocess.TimeoutExpired:
            rc = None
        ok("encerrar: monitor sai com 0 e o supervisor para", rc == 0 and "encerrado pelo usuário" in ler(log_sup),
           f"rc={rc}")
    finally:
        if sup.poll() is None:
            for filho in psutil.Process(sup.pid).children(recursive=True):
                try:
                    filho.kill()
                except Exception:
                    pass
            sup.kill()
        saida.close()
        srv.shutdown()
    time.sleep(2)
    perfil = os.path.normcase(os.path.join(LOCALAPPDATA_TESTE, "MonitorFeegow"))
    sobras = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if (proc.info["name"] or "").lower() in ("chrome.exe", "chromedriver.exe") and \
                    perfil in os.path.normcase(" ".join(proc.info["cmdline"] or [])):
                sobras.append(proc.pid)
        except Exception:
            pass
    ok("nenhum Chrome do teste ficou aberto", not sobras, str(sobras))


def teste_recursos(m):
    print("\n== Recursos da 2.1.0: horário, reinício preventivo, relogin, log, DPAPI, heartbeat, textos")
    import datetime as dt
    cfg = dict(m.FILTROS_PADRAO)
    ok("horário desligado = 24/7", m.dentro_do_horario(cfg, dt.datetime(2026, 11, 22, 3, 0)))
    cfg["horario_funcionamento"] = m._horario_normalizado({"ativo": True, "inicio": "07:00", "fim": "19:00",
                                                           "dias": "seg,ter,qua,qui,sex"})
    sexta_10h, sexta_20h, sabado_10h = dt.datetime(2026, 11, 20, 10), dt.datetime(2026, 11, 20, 20), dt.datetime(2026, 11, 21, 10)
    ok("horário 07-19 seg-sex: sexta 10h dentro, 20h fora, sábado fora",
       m.dentro_do_horario(cfg, sexta_10h) and not m.dentro_do_horario(cfg, sexta_20h)
       and not m.dentro_do_horario(cfg, sabado_10h))
    cfg["horario_funcionamento"] = m._horario_normalizado({"ativo": True, "inicio": "22:00", "fim": "06:00",
                                                           "dias": ["sex"]})
    ok("horário que vira a meia-noite (sex 22h -> sáb 06h)",
       m.dentro_do_horario(cfg, dt.datetime(2026, 11, 20, 23)) and m.dentro_do_horario(cfg, dt.datetime(2026, 11, 21, 5))
       and not m.dentro_do_horario(cfg, dt.datetime(2026, 11, 21, 7)))

    cfg = dict(m.FILTROS_PADRAO, chrome_limite_ram_mb=0)
    hoje_0301 = dt.datetime(2026, 11, 20, 3, 1).timestamp()
    ok("reinício diário 03:00: Chrome aberto ontem -> reabre às 03:01",
       "diário" in m.motivo_reinicio_preventivo(cfg, hoje_0301 - 20 * 3600, hoje_0301))
    ok("reinício diário: Chrome aberto às 02:30 (< 1 h) não reabre",
       m.motivo_reinicio_preventivo(cfg, hoje_0301 - 31 * 60, hoje_0301) == "")
    ok("reinício diário: aberto às 03:30 não reabre até amanhã",
       m.motivo_reinicio_preventivo(cfg, hoje_0301 + 29 * 60, hoje_0301 + 10 * 3600) == "")
    m._PID_CHROMEDRIVER = os.getpid()   # mede a "árvore" do próprio teste
    m._RAM["medido_em"] = 0
    cfg_ram = dict(cfg, chrome_reinicio_diario_hora="", chrome_limite_ram_mb=1)
    ok("limite de RAM do Chrome (psutil) pede reabrir", "MB" in m.motivo_reinicio_preventivo(cfg_ram, time.time(), time.time()))
    m._PID_CHROMEDRIVER = 0

    # relogin: no máximo 3 por hora
    m.fazer_login = lambda: True
    m._recarregar_seguro = lambda url: None
    m._RELOGINS.clear()
    m._RELOGIN_BLOQUEADO_ATE = 0.0
    resultados = [m.tratar_falha("login", "teste", 1, "x") for _ in range(4)]
    ok("relogin: 3 na hora seguem, o 4º para (outro PC com o mesmo usuário?)",
       resultados[:3] == [None, None, None] and resultados[3] == m.PARAR_MONITORAMENTO
       and m._RELOGIN_BLOQUEADO_ATE > time.time(), str(resultados))
    m._RELOGINS.clear()
    m._RELOGIN_BLOQUEADO_ATE = 0.0

    # log das senhas só quando o conjunto muda
    logs = []
    log_original = m.log_msg
    m.log_msg = lambda t: logs.append(t)
    m.fechar_modal = lambda: None
    m.disparar_estado_alarme = lambda: None
    m._ULTIMO_CONJUNTO_SENHAS = None
    for _ in range(3):
        m.verificar_botao_explicacao(["AGENDAMENTOS", "CIRURGIA"])
    primeiro = len([l for l in logs if "Senha '" in l])
    m.verificar_botao_explicacao(["AGENDAMENTOS"])
    m.log_msg = log_original
    ok("log de avaliação só quando o conjunto de senhas muda", primeiro == 2 and
       len([l for l in logs if "Senha '" in l]) == 3, f"{primeiro} e depois {len([l for l in logs if 'Senha' in l])}")

    # DPAPI: migração automática + leitura
    m.ARQ_CREDENCIAIS = os.path.join(SANDBOX, "cred_dpapi.json")
    json.dump({"usuario": "fulano@clinica.local", "senha": "senha-ficticia-123", "rota_login": "auto"},
              open(m.ARQ_CREDENCIAIS, "w", encoding="utf-8"))
    m.ARQ_ESTADO_ATUALIZACAO = os.path.join(SANDBOX, "estado_sem_teste.json")
    cred = m.carregar_credenciais()
    gravado = json.load(open(m.ARQ_CREDENCIAIS, encoding="utf-8"))
    ok("DPAPI: senha em texto é migrada e some do arquivo",
       "senha" not in gravado and str(gravado.get("senha_protegida", "")).startswith("dpapi:"))
    ok("DPAPI: a senha volta certinha na memória", cred["senha"] == "senha-ficticia-123"
       and m.carregar_credenciais()["senha"] == "senha-ficticia-123")
    json.dump({"status": "em_teste", "para": m.VERSAO}, open(m.ARQ_ESTADO_ATUALIZACAO, "w"))
    json.dump({"usuario": "fulano@clinica.local", "senha": "outra-ficticia"}, open(m.ARQ_CREDENCIAIS, "w"))
    m.carregar_credenciais()
    ok("DPAPI: não migra enquanto a versão nova está em teste (rollback seguro)",
       json.load(open(m.ARQ_CREDENCIAIS)).get("senha") == "outra-ficticia")
    ok("e-mail mascarado no log", m.mascarar_email("usuário fulano@clinica.local") == "usuário fu***@clinica.local")

    # heartbeat + status
    m.ARQ_HEARTBEAT = os.path.join(SANDBOX, "logs", "heartbeat.json")
    m.ARQ_STATUS = os.path.join(SANDBOX, "logs", "status.json")
    m.gravar_heartbeat()
    hb = json.load(open(m.ARQ_HEARTBEAT))
    st = json.load(open(m.ARQ_STATUS, encoding="utf-8"))
    ok("heartbeat.json com PID e horário; status.json legível",
       hb.get("pid") == os.getpid() and time.time() - hb.get("ts", 0) < 5 and st.get("versao") == m.VERSAO)
    diag = m.texto_diagnostico()
    ok("diagnóstico sem senha e com e-mail mascarado",
       "senha-ficticia" not in diag and "fulano@" not in diag and "Estado" in diag)

    # textos do alarme a partir da senha real e da pausa
    titulo, texto = m.textos_janela_alarme("LENTES DE CONTATO", 7)
    ok("janela do alarme usa a senha e a pausa configurada", "LENTES DE CONTATO" in titulo and "7 minutos" in texto
       and "MC" not in titulo, f"{titulo} / {texto.splitlines()[-1]}")
    ok("frase falada usa a senha real", m.frase_alarme("R2") == "Atenção! Senha R2.")

    # gravação atômica
    alvo = os.path.join(SANDBOX, "atomico.json")
    m._gravar_json(alvo, {"a": 1})
    ok("gravação atômica sem sobrar .tmp", json.load(open(alvo)) == {"a": 1}
       and not [f for f in os.listdir(SANDBOX) if f.endswith(".tmp")])

    # sha256_exe vazio = sem atualização de .exe, sem erro
    m._caminho_programa_atual = lambda: os.path.join(SANDBOX, "MonitorFeegow.exe")
    m._em_clone_git = lambda: False
    m.ARQ_ESTADO_ATUALIZACAO = os.path.join(SANDBOX, "estado_exe.json")
    m.consultar_versao_remota = lambda: {"versao": "9.9.9", "sha256": "0" * 64, "sha256_exe": ""}
    ok("versão sem .exe (sha256_exe vazio): o .exe fica onde está, sem erro",
       m.verificar_atualizacao(manual=True) == "sem_exe")


def teste_limites(m):
    print("\n== Limites contra loop de reinício")
    m._REINICIOS_CHROME[:] = []
    m._atualizar_status = lambda *a: None
    esperas = [m._proxima_espera_reinicio() for _ in range(7)]
    ok("backoff do Chrome: 5, 15, 30, 60, 120, 240 s e depois 15 min", esperas == [5, 15, 30, 60, 120, 240, 900],
       str(esperas))
    m.ARQ_ESTADO_WATCHDOG = os.path.join(SANDBOX, "logs", "watchdog_teste.json")
    t = time.time()
    resultados = [m._pode_reiniciar_processo(agora=t + i * 31) for i in range(8)] +         [m._pode_reiniciar_processo(agora=t + 7 * 31 + 5)]
    ok("sem teto por hora, só 30 s entre reinícios (2.3.3)", resultados == [True] * 8 + [False], str(resultados))
    erro = Exception("invalid session id: session deleted because of page crash")
    ok("reconhece 'Chrome morto' pelas mensagens do Selenium", m._erro_chrome_morto(erro))


def main():
    preparar_sandbox()
    m = importar_monitor()
    print(f"Monitor v{m.VERSAO} importado da sandbox.")
    teste_audio(m)
    teste_instancia_unica()
    teste_atualizacao(importar_monitor())
    teste_reiniciar_unidade(importar_monitor())
    teste_recursos(importar_monitor())
    teste_limites(importar_monitor())
    teste_supervisor()
    if "--sem-chrome" not in sys.argv:
        teste_chrome(importar_monitor())
        teste_reinicio_integrado()
    falhas = [r for r in RESULTADOS if not r[1]]
    print(f"\nResultado: {len(RESULTADOS) - len(falhas)}/{len(RESULTADOS)} verificações OK.", flush=True)
    for nome, _, detalhe in falhas:
        print(f"  FALHOU: {nome} {detalhe}")
    os._exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
