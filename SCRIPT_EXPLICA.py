# MONITOR-FEEGOW-SCRIPT :: Monitor de Senha do Feegow (github.com/Nicker2/script-monitoramento)
# A linha acima identifica este arquivo na atualização automática: não remova.
import io
import os
import re
import sys
import time
import queue
import socket
import hashlib
import threading
import datetime
import ctypes
import json
import shutil
import logging
import logging.handlers
import unicodedata
import urllib.request
import tkinter as tk
from tkinter import scrolledtext
import tempfile  # pasta TEMP do Windows (sem erro 13 de permissão)
import subprocess


# ==============================================================================
# 🖥️ SEM CONSOLE (pythonw / .exe compilado): sys.stdout vem None e TODO print() estoura
#    AttributeError -> a thread de inicialização morria em silêncio.
# ==============================================================================
class _FluxoSemConsole:
    encoding = "utf-8"
    errors = "replace"

    def write(self, *_args):
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("sem console nesta execução")


if sys.stdout is None:
    sys.stdout = _FluxoSemConsole()
if sys.stderr is None:
    sys.stderr = _FluxoSemConsole()


# ==============================================================================
# ⚙️ CONFIGURAÇÃO GERAL DO PROGRAMA
#    Os ajustes do dia a dia (senhas, alertas, watchdog, atualização) ficam no
#    filtros.json / janela ⚙️ Configurações. Aqui fica só o que é do programa.
# ==============================================================================
VERSAO = "2.0.1"
MARCADOR_SCRIPT = "# MONITOR-FEEGOW-SCRIPT"     # o arquivo baixado precisa começar com isto
REPO_GITHUB = "Nicker2/script-monitoramento"
CANAL_ATUALIZACAO_PADRAO = "main"               # branch do GitHub consultada
NOME_ARQUIVO_VERSAO = "versao.json"
URL_RAW = "https://raw.githubusercontent.com/{repo}/{canal}/{arquivo}"
TAMANHO_MINIMO_SCRIPT = 40_000                  # bytes; menor que isso é página de erro
TAMANHO_MINIMO_EXE = 5_000_000
CODIGO_SAIDA_REINICIAR = 75                     # iniciar_monitor.pyw relança na hora
CODIGO_SAIDA_OUTRA_INSTANCIA = 3                # já existe um monitor rodando
MAX_INICIOS_SEM_CONFIRMAR = 3                   # versão nova que não confirma em 3 inícios -> volta
NOME_MUTEX = "Local\\MonitorFeegowSenha"        # trava de instância única
WATCHDOG_PERIODO_SEGUNDOS = 15                  # de quanto em quanto tempo o watchdog olha
LOG_MAX_LINHAS_PAINEL = 3000                    # o painel guarda só as últimas N linhas


def _rodando_compilado():
    return "__compiled__" in globals() or getattr(sys, "frozen", False)


def _pasta_do_programa():
    """Pasta dos JSONs — vale rodando o .py e também o .exe compilado.

    No .exe (Nuitka --onefile) o `__file__` aponta para a pasta temporária de
    extração, que é apagada ao fechar; aí a referência certa é o executável.
    """
    if _rodando_compilado():
        candidatos = [
            os.environ.get("NUITKA_ONEFILE_BINARY", ""),
            sys.argv[0] if sys.argv else "",
            sys.executable,
        ]
        for caminho in candidatos:
            if caminho and os.path.isfile(caminho):
                return os.path.dirname(os.path.abspath(caminho))
    return os.path.dirname(os.path.abspath(__file__))


PASTA_SCRIPT = _pasta_do_programa()
PASTA_LOGS = os.path.join(PASTA_SCRIPT, "logs")
ARQ_LOG = os.path.join(PASTA_LOGS, "monitor.log")
ARQ_ESTADO_ATUALIZACAO = os.path.join(PASTA_SCRIPT, "atualizacao_estado.json")
ARQ_ESTADO_WATCHDOG = os.path.join(PASTA_LOGS, "watchdog_estado.json")


def _configurar_log_arquivo():
    """logs/monitor.log com rotação (1 MB x 5 arquivos)."""
    registro = logging.getLogger("monitor")
    registro.setLevel(logging.INFO)
    registro.propagate = False
    if registro.handlers:
        return registro
    try:
        os.makedirs(PASTA_LOGS, exist_ok=True)
        manipulador = logging.handlers.RotatingFileHandler(
            ARQ_LOG, maxBytes=1_000_000, backupCount=5, encoding="utf-8", delay=True)
        manipulador.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
        registro.addHandler(manipulador)
    except Exception:
        registro.addHandler(logging.NullHandler())
    return registro


_LOGGER = _configurar_log_arquivo()


def _caminho_programa_atual():
    """Arquivo que a atualização substitui: o .exe compilado ou este .py."""
    if _rodando_compilado():
        for caminho in (os.environ.get("NUITKA_ONEFILE_BINARY", ""),
                        sys.argv[0] if sys.argv else "", sys.executable):
            if caminho and os.path.isfile(caminho) and caminho.lower().endswith(".exe"):
                return os.path.abspath(caminho)
    return os.path.abspath(__file__)


def _supervisionado():
    """True quando quem abriu foi o iniciar_monitor.pyw (ele relança sozinho)."""
    return os.environ.get("MONITOR_SUPERVISOR") == "1"


def _comando_relancar():
    alvo = _caminho_programa_atual()
    argumentos = [a for a in sys.argv[1:] if a != "--reiniciado"] + ["--reiniciado"]
    if alvo.lower().endswith(".exe"):
        return [alvo] + argumentos
    return [sys.executable, alvo] + argumentos


def relancar_processo_agora():
    """Sobe o próximo processo e sai. Quem chama já deve ter fechado o Chrome.

    Com o supervisor (iniciar_monitor.pyw) só sai com o código 75 e ele relança;
    sem supervisor, o próprio programa abre a nova instância antes de sair.
    """
    _liberar_instancia_unica()
    if _supervisionado():
        os._exit(CODIGO_SAIDA_REINICIAR)
    try:
        comando = _comando_relancar()
        com_console = os.path.basename(comando[0]).lower() == "python.exe"
        flags = 0x00000010 if com_console else 0x00000008  # NEW_CONSOLE / DETACHED_PROCESS
        subprocess.Popen(comando, cwd=PASTA_SCRIPT, creationflags=flags | 0x00000200, close_fds=True)
    except Exception as e:
        _LOGGER.info(f"Falha ao relançar o programa: {e}")
    os._exit(0)


# --- Instância única -----------------------------------------------------------
_MUTEX = None


def garantir_instancia_unica():
    """Impede dois monitores no mesmo PC (dois Chromes brigando pelo perfil e
    a fala saindo duplicada). Depois de um reinício espera a instância antiga sair."""
    global _MUTEX
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    except Exception:
        return True
    espera = 45 if "--reiniciado" in sys.argv else 3
    fim = time.time() + espera
    while True:
        handle = kernel32.CreateMutexW(None, False, NOME_MUTEX)
        erro = ctypes.get_last_error()
        if handle and erro != 183:  # 183 = ERROR_ALREADY_EXISTS
            _MUTEX = handle
            return True
        if handle:
            kernel32.CloseHandle(handle)
        if time.time() > fim:
            return False
        time.sleep(1)


def _liberar_instancia_unica():
    global _MUTEX
    if _MUTEX:
        try:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_MUTEX))
        except Exception:
            pass
        _MUTEX = None


# --- Atualização em teste: volta para a versão anterior se a nova não firmar ---
def _ler_estado_atualizacao():
    try:
        with open(ARQ_ESTADO_ATUALIZACAO, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def _gravar_estado_atualizacao(dados):
    try:
        temporario = ARQ_ESTADO_ATUALIZACAO + ".tmp"
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        os.replace(temporario, ARQ_ESTADO_ATUALIZACAO)
    except Exception as e:
        _LOGGER.info(f"Não consegui gravar {ARQ_ESTADO_ATUALIZACAO}: {e}")


def _restaurar_versao_anterior(estado, motivo):
    """Troca o arquivo atual pelo backup (.anterior). Funciona com o .exe em uso:
    o Windows deixa RENOMEAR um executável aberto, só não deixa sobrescrever."""
    alvo = estado.get("arquivo") or ""
    backup = estado.get("backup") or ""
    if not alvo or not backup or not os.path.isfile(backup):
        _LOGGER.info(f"Rollback impossível: backup ausente ({backup or 'sem caminho'}).")
        return False
    falhou = alvo + ".falhou"
    try:
        if os.path.exists(falhou):
            os.remove(falhou)
    except Exception:
        pass
    try:
        if os.path.exists(alvo):
            os.replace(alvo, falhou)
        os.replace(backup, alvo)
    except Exception as e:
        _LOGGER.info(f"Rollback falhou: {e}")
        return False
    bloqueadas = set(estado.get("bloqueadas") or [])
    if estado.get("para"):
        bloqueadas.add(estado["para"])
    estado.update({
        "status": "revertida",
        "motivo": motivo,
        "revertida_em": datetime.datetime.now().isoformat(timespec="seconds"),
        "bloqueadas": sorted(bloqueadas),
    })
    _gravar_estado_atualizacao(estado)
    _LOGGER.info(f"↩️ Versão {estado.get('para')} revertida para {estado.get('de')}: {motivo}")
    return True


def _checar_atualizacao_em_teste():
    """Conta os inícios da versão recém-instalada. Se ela reinicia várias vezes
    sem chegar ao primeiro ciclo OK, volta para a anterior e relança."""
    estado = _ler_estado_atualizacao()
    if estado.get("status") != "em_teste" or estado.get("para") != VERSAO:
        return
    estado["inicios"] = int(estado.get("inicios") or 0) + 1
    if estado["inicios"] > MAX_INICIOS_SEM_CONFIRMAR:
        if _restaurar_versao_anterior(
                estado, f"não confirmou após {MAX_INICIOS_SEM_CONFIRMAR} inícios"):
            relancar_processo_agora()
        return
    _gravar_estado_atualizacao(estado)


if __name__ == "__main__":
    _checar_atualizacao_em_teste()
    if not garantir_instancia_unica():
        _LOGGER.info("Já existe um Monitor de Senha rodando neste PC — esta cópia foi encerrada.")
        print("Já existe um Monitor de Senha rodando neste PC. Saindo.")
        sys.exit(CODIGO_SAIDA_OUTRA_INSTANCIA)


# ==============================================================================
# 📦 AUTO-INSTALAÇÃO DAS DEPENDÊNCIAS (roda ANTES dos imports de terceiros)
# ==============================================================================
MODULOS_NECESSARIOS = [
    ("psutil", "psutil", True),
    ("gtts", "gTTS", True),
    ("pygame", "pygame", True),
    ("pystray", "pystray", True),
    ("PIL", "Pillow", True),
    ("selenium", "selenium", True),
    ("webdriver_manager", "webdriver-manager", False),  # opcional: só fallback do driver
]


def _modulo_disponivel(nome_modulo: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(nome_modulo) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def garantir_dependencias() -> bool:
    """Confere os pacotes de terceiros e instala com pip o que estiver faltando.

    Roda antes de qualquer import pesado, então o programa não morre com
    ModuleNotFoundError em um PC novo. No .exe compilado as dependências já
    vêm embutidas, então não há nada para instalar.
    """
    if "__compiled__" in globals():
        return True

    faltando = [pacote for modulo, pacote, _obr in MODULOS_NECESSARIOS
                if not _modulo_disponivel(modulo)]
    if not faltando:
        return True

    print("=" * 70)
    print("📦 Dependências faltando: " + ", ".join(faltando))
    print("   Instalando com pip — na primeira vez pode levar alguns minutos...")
    print("=" * 70, flush=True)

    tentativas = [
        [sys.executable, "-m", "pip", "install", "--upgrade"] + faltando,
        [sys.executable, "-m", "pip", "install", "--user"] + faltando,
    ]
    for comando in tentativas:
        try:
            subprocess.check_call(comando)
            break
        except Exception as e:
            print(f"⚠️ Falhou: {' '.join(comando[:4])} ... -> {e}", flush=True)

    try:
        import importlib
        importlib.invalidate_caches()
    except Exception:
        pass

    ainda_faltando = [pacote for modulo, pacote, obrig in MODULOS_NECESSARIOS
                      if obrig and not _modulo_disponivel(modulo)]
    if ainda_faltando:
        print("=" * 70)
        print("🛑 Não foi possível instalar: " + ", ".join(ainda_faltando))
        print("   Rode manualmente na pasta do script:")
        print(f"   {sys.executable} -m pip install " + " ".join(ainda_faltando))
        print("=" * 70, flush=True)
        return False

    print("✅ Dependências instaladas. Continuando...\n", flush=True)
    return True


if not garantir_dependencias():
    sys.exit(1)


import psutil
from gtts import gTTS
import pygame
from pygame._sdl2 import audio as sdl2_audio

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

from selenium import webdriver
try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # pacote opcional: só é usado se não houver chromedriver local
    ChromeDriverManager = None
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException, ElementNotInteractableException, TimeoutException,
    InvalidSessionIdException, NoSuchWindowException,
)

# ==============================================================================
# VARIÁVEIS GLOBAIS E ESTADOS
# ==============================================================================
is_visible = False
alarme_ativo = False
pausa_ate = datetime.datetime.now() - datetime.timedelta(minutes=1)
driver = None
tray_icon = None
CHROME_HWND = None
janela_alerta = None
cor_alerta_atual = "red"

# Usa a pasta Temporária do Windows para NUNCA dar Erro 13 de Permissão
PASTA_TEMP = tempfile.gettempdir()
ARQUIVO_OFICIAL_ALERTA = os.path.join(PASTA_TEMP, f"alerta_oficial_{int(time.time())}.mp3")

# ==============================================================================
# ARQUIVOS DE CONFIGURAÇÃO EXTERNOS (edite sem mexer no código)
#   filtros.json      -> blacklist / whitelist / termo gatilho / tempos
#   credenciais.json  -> usuário e senha do Feegow (nunca versionar)
# ==============================================================================
ARQ_FILTROS = os.path.join(PASTA_SCRIPT, "filtros.json")
ARQ_CREDENCIAIS = os.path.join(PASTA_SCRIPT, "credenciais.json")
ARQ_CREDENCIAIS_EXEMPLO = os.path.join(PASTA_SCRIPT, "credenciais.exemplo.json")

FILTROS_PADRAO = {
    "termos_gatilho": [
        "DILATAÇÃO", "DILATACAO", "AUTO REFRATOR",
        "MC", "CONSULTA", "EXAME", "PRIORITÁRIA", "AGENDAMENTOS",
        "CIRURGIA", "RETORNO", "LENTES", "R2",
        "RECEPÇÃO", "OUTRAS ESPECIALIDADES",
    ],
    "comparacao": "contem",
    "padrao_sem_match": "ignorar",
    "whitelist": [
        "DILATAÇÃO - CCC",
        "DILATAÇÃO - MMM",
        "DILATAÇÃO - FENIL",
        "DILATAÇÃO - CMC",
        "DILATAÇÃO - CMM",
        "DILATAÇÃO COM PILOCARPINA",
        "AUTO REFRATOR (SEM DILATAR)",
    ],
    "blacklist": [
        "CONSULTA - COMUM",
        "EXAME - COMUM",
        "PRIORITÁRIA",
        "AGENDAMENTOS",
        "CIRURGIA",
        "RETORNO",
        "LENTES DE CONTATO",
        "R2",
        "MC",
        "RECEPÇÃO 1° ANDAR",
        "RECEPÇÃO SUS",
        "OUTRAS ESPECIALIDADES",
        "RECEPÇÃO 2° ANDAR",
    ],
    "pausa_minutos": 15,
    "intervalo_busca_segundos": 10,
    "alerta_tela": True,
    "alerta_voz": True,
    "alarme_max_segundos": 120,
    "intervalo_repeticao_segundos": 15,
    "volume_alarme": 100,
    # áudio: "padrao" = só a saída padrão do Windows (sem fala duplicada),
    # "todos" = toca em cada dispositivo, ou o nome exato de um dispositivo.
    "saida_audio": "padrao",
    "anti_repeticao_segundos": 60,
    # watchdog (auto-recuperação)
    "watchdog_limite_sem_batimento_segundos": 180,
    "watchdog_recarregar_pagina_minutos": 30,
    "watchdog_reiniciar_chrome_horas": 0,
    "watchdog_max_reinicios_por_hora": 6,
    # atualização automática pelo GitHub (versao.json)
    "auto_atualizar": True,
    "atualizacao_intervalo_minutos": 60,
    "atualizacao_canal": "main",
    # hosts usados para saber se há internet ("host" ou "host:porta", porta padrão 443)
    "hosts_verificacao_internet": ["app.feegow.com", "www.google.com"],
}

# Padrões prontos de filtro (botões na janela de configuração)
PADROES_SENHA = {
    "dilatacao": {
        "rotulo": "🩺 Dilatação",
        "comparacao": "contem",
        "whitelist": [
            "DILATAÇÃO - CCC", "DILATAÇÃO - MMM", "DILATAÇÃO - FENIL",
            "DILATAÇÃO - CMC", "DILATAÇÃO - CMM",
            "DILATAÇÃO COM PILOCARPINA", "AUTO REFRATOR (SEM DILATAR)",
        ],
        "blacklist": [
            "CONSULTA - COMUM", "EXAME - COMUM", "PRIORITÁRIA", "AGENDAMENTOS",
            "CIRURGIA", "RETORNO", "LENTES DE CONTATO", "R2", "MC",
            "RECEPÇÃO 1° ANDAR", "RECEPÇÃO SUS", "OUTRAS ESPECIALIDADES",
            "RECEPÇÃO 2° ANDAR",
        ],
        "termos_gatilho": [
            "DILATAÇÃO", "DILATACAO", "AUTO REFRATOR", "MC", "CONSULTA", "EXAME",
            "PRIORITÁRIA", "AGENDAMENTOS", "CIRURGIA", "RETORNO", "LENTES", "R2",
            "RECEPÇÃO", "OUTRAS ESPECIALIDADES",
        ],
    },
    "lentes": {
        "rotulo": "👓 Lentes",
        "comparacao": "contem",
        "whitelist": ["LENTES DE CONTATO"],
        "blacklist": [],
        "termos_gatilho": ["LENTES"],
    },
    "mc": {
        "rotulo": "🔔 MC",
        "comparacao": "exata",
        "whitelist": ["MC"],
        "blacklist": [],
        "termos_gatilho": ["MC"],
    },
    "r2": {
        "rotulo": "🎫 R2",
        "comparacao": "contem",
        "whitelist": ["R2"],
        "blacklist": [],
        "termos_gatilho": ["R2"],
    },
}

CREDENCIAIS_PADRAO = {
    "usuario": "exemplo@exemplo.com",
    "senha": "exemplo",
    "rota_login": "auto",
    "url_login": "https://app.feegow.com/main/?P=Login",
    "url_login_sso": "https://app.feegow.com/main/sso/",
    "url_lista_espera": "https://app.feegow.com/pre-v8/?P=ListaEspera&Pers=1",
}

_FILTROS = dict(FILTROS_PADRAO)
_FILTROS_MTIME = 0.0
_CRED_USUARIO_LOGADO = None
_JANELA_CONFIG = None
_ABAS_CONFIG = None
_SELENIUM_RODANDO = False
_PEDIDO_CRED_PENDENTE = False
_CRED_ARQUIVO_CRIADO = False
_TICKET_ALARME = ""

# Estado do watchdog / atualização (ver seções 🩺 e ⬆️)
_ENCERRANDO = False
_ETAPA = "parado"              # parado | abrindo_chrome | login | monitorando | offline | aguardando_login
_ULTIMO_PROGRESSO = time.time()  # qualquer avanço do monitor (etapa, tentativa, ciclo, espera proposital)
_ULTIMO_OK = 0.0                 # último ciclo em que a página respondeu certo
_PEDIDO_REINICIO_CHROME = ""     # motivo, quando o watchdog manda reabrir o Chrome
_PEDIDO_RECARGA = False
_HORA_PEDIDO_CHROME = 0.0
_REINICIOS_CHROME = []
_STATUS = ("iniciando", "Iniciando...")
_INICIO_PROCESSO = time.time()
_PRIMEIRO_OK = False
_THREAD_MONITOR = None
_TRAVA_MONITOR = threading.Lock()
_MONITOR_DESEJADO = False
_CRED_RECUSADAS = False
_ULTIMO_RELOAD_SEM_BOTAO = 0.0
_OFFLINE_DESDE = 0.0
_ATUALIZACAO_ADIADA = False
_AVISOU_GIT = False


def normalizar_texto(texto):
    """Maiúsculas, sem acento, sem hífen/barra/pontuação e com espaços simples.

    A tela do Feegow varia entre 'DILATAÇÃO - CCC', 'DILATAÇÃO-CCC' e
    'RECEPÇÃO 1° ANDAR' (ou '1º'), então tudo vira a mesma forma canônica:
    'DILATACAO CCC', 'RECEPCAO 1 ANDAR'.

    Os ordinais (º, ª) e o grau (°) são removidos ANTES do NFKD: o NFKD
    transforma 'º' em 'o' e 'ª' em 'a', o que geraria '2O ANDAR' em vez de
    '2 ANDAR' e a senha deixaria de casar.
    """
    bruto = str(texto or "")
    for simbolo in ("°", "º", "ª"):
        bruto = bruto.replace(simbolo, " ")

    base = unicodedata.normalize("NFKD", bruto)
    sem_acento = "".join(c for c in base if not unicodedata.combining(c))
    for simbolo in ("-", "–", "—", ".", ",", "/", "\\", "(", ")", "[", "]", ":", ";", "|", "_"):
        sem_acento = sem_acento.replace(simbolo, " ")
    return " ".join(sem_acento.upper().split())


def _ler_json(caminho):
    with open(caminho, "r", encoding="utf-8-sig") as arquivo:
        return json.load(arquivo)


def _para_bool(valor, padrao=True):
    """Converte true/false, 1/0, "sim"/"não", "on"/"off" em booleano."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return valor != 0
    if isinstance(valor, str):
        texto = normalizar_texto(valor)
        if texto in ("1", "SIM", "S", "TRUE", "VERDADEIRO", "ATIVO", "ATIVADA", "ON", "LIGADO"):
            return True
        if texto in ("0", "NAO", "N", "FALSE", "FALSO", "INATIVO", "INATIVA", "OFF", "DESLIGADO"):
            return False
    return padrao


def _para_int(valor, padrao, minimo=None, maximo=None):
    """Converte número de forma tolerante (aceita None, '12', '12,5')."""
    if valor is None:
        numero = padrao
    else:
        try:
            numero = int(float(str(valor).replace(",", ".")))
        except Exception:
            numero = padrao
    if minimo is not None and numero < minimo:
        numero = minimo
    if maximo is not None and numero > maximo:
        numero = maximo
    return numero


def _lista_normalizada(valor):
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return []
    return [normalizar_texto(item) for item in valor if normalizar_texto(item)]


def _gravar_json(caminho, dados):
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")


def _criar_filtros_padrao():
    """Cria filtros.json na primeira execução (ou se o arquivo for apagado)."""
    dados = dict(FILTROS_PADRAO)
    dados["_comentario"] = (
        "Edite este arquivo e salve: o script recarrega sozinho, sem reiniciar. "
        "whitelist = senhas que DEVEM alertar (vence a blacklist); "
        "blacklist = senhas que devem ser IGNORADAS; "
        "termos_gatilho = textos usados para localizar o botão da senha no modal; "
        "padrao_sem_match = o que fazer quando o texto não está em nenhuma das listas."
    )
    _gravar_json(ARQ_FILTROS, dados)
    log_msg(f"📄 filtros.json criado com os valores padrão em {ARQ_FILTROS}")


def carregar_filtros(forcar=False):
    """Carrega filtros.json recarregando sozinho quando o arquivo é editado."""
    global _FILTROS, _FILTROS_MTIME
    try:
        mtime = os.path.getmtime(ARQ_FILTROS)
    except OSError:
        mtime = 0.0

    if not forcar and mtime and mtime == _FILTROS_MTIME:
        return _FILTROS

    if not mtime:
        try:
            _criar_filtros_padrao()
            mtime = os.path.getmtime(ARQ_FILTROS)
        except Exception as e:
            log_msg(f"⚠️ Não foi possível criar filtros.json ({e}). Usando a blacklist padrão embutida.")
            _FILTROS_MTIME = -1.0
            return _FILTROS

    try:
        dados = _ler_json(ARQ_FILTROS)
        if not isinstance(dados, dict):
            raise ValueError("o conteúdo precisa ser um objeto JSON { ... }")
        combinado = dict(FILTROS_PADRAO)
        combinado.update({k: v for k, v in dados.items() if not str(k).startswith("_") and v is not None})
        _FILTROS = {
            # termos_gatilho NÃO é normalizado: ele vai literal para o XPath de
            # busca do botão (o texto da tela é comparado como está escrito).
            "termos_gatilho": [
                str(t).strip() for t in (
                    combinado.get("termos_gatilho") or [combinado.get("termo_gatilho") or ""]
                ) if str(t).strip()
            ],
            "comparacao": str(combinado.get("comparacao") or "contem").strip().lower(),
            "padrao_sem_match": str(combinado.get("padrao_sem_match") or "ignorar").strip().lower(),
            "blacklist": _lista_normalizada(combinado.get("blacklist")),
            "whitelist": _lista_normalizada(combinado.get("whitelist")),
            "pausa_minutos": max(1, int(combinado.get("pausa_minutos") or 15)),
            "intervalo_busca_segundos": max(1, int(combinado.get("intervalo_busca_segundos") or 10)),
            "alerta_tela": _para_bool(combinado.get("alerta_tela"), True),
            "alerta_voz": _para_bool(combinado.get("alerta_voz"), True),
            "alarme_max_segundos": _para_int(combinado.get("alarme_max_segundos"), 120, 0),
            "intervalo_repeticao_segundos": _para_int(combinado.get("intervalo_repeticao_segundos"), 15, 1),
            "volume_alarme": _para_int(combinado.get("volume_alarme"), 100, 0, 100),
            "saida_audio": str(combinado.get("saida_audio") or "padrao").strip(),
            "anti_repeticao_segundos": _para_int(combinado.get("anti_repeticao_segundos"), 60, 0),
            "watchdog_limite_sem_batimento_segundos": _para_int(
                combinado.get("watchdog_limite_sem_batimento_segundos"), 180, 60),
            "watchdog_recarregar_pagina_minutos": _para_int(
                combinado.get("watchdog_recarregar_pagina_minutos"), 30, 0),
            "watchdog_reiniciar_chrome_horas": _para_int(
                combinado.get("watchdog_reiniciar_chrome_horas"), 0, 0),
            "watchdog_max_reinicios_por_hora": _para_int(
                combinado.get("watchdog_max_reinicios_por_hora"), 6, 1),
            "auto_atualizar": _para_bool(combinado.get("auto_atualizar"), True),
            "atualizacao_intervalo_minutos": _para_int(
                combinado.get("atualizacao_intervalo_minutos"), 60, 10),
            "atualizacao_canal": str(combinado.get("atualizacao_canal") or CANAL_ATUALIZACAO_PADRAO).strip(),
            "hosts_verificacao_internet": [
                str(h).strip() for h in (combinado.get("hosts_verificacao_internet")
                                         or FILTROS_PADRAO["hosts_verificacao_internet"])
                if str(h).strip()] or list(FILTROS_PADRAO["hosts_verificacao_internet"]),
        }
        _FILTROS_MTIME = mtime
        log_msg(
            f"⚙️ filtros.json carregado: {len(_FILTROS['whitelist'])} para ALERTAR, "
            f"{len(_FILTROS['blacklist'])} para IGNORAR, "
            f"{len(_FILTROS['termos_gatilho'])} termos de busca | sem correspondência: "
            f"{_FILTROS['padrao_sem_match']} | alerta_tela="
            f"{'ON' if _FILTROS['alerta_tela'] else 'OFF'}, alerta_voz="
            f"{'ON' if _FILTROS['alerta_voz'] else 'OFF'}."
        )
    except Exception as e:
        _FILTROS_MTIME = mtime
        log_msg(f"⚠️ Erro ao ler filtros.json ({e}). Mantendo a configuração anterior.")
    return _FILTROS


def avaliar_chamada(texto_botao):
    """Decide se o texto da senha dispara o alarme.

    Prioridade:
      1. Whitelist -> ALERTA (vence a blacklist, mesmo com termo em comum).
      2. Blacklist -> IGNORA a chamada.
      3. Nada casou -> segue 'padrao_sem_match' (padrão: ignorar).
    """
    cfg = carregar_filtros()
    texto = normalizar_texto(texto_botao)
    modo_exato = cfg["comparacao"] in ("exata", "exato", "igual")

    def casou(item):
        return texto == item if modo_exato else item in texto

    for item in cfg["whitelist"]:
        if casou(item):
            return True, f"ALERTAR (whitelist: '{item}')"

    for item in cfg["blacklist"]:
        if casou(item):
            return False, f"IGNORAR (blacklist: '{item}')"

    if cfg.get("padrao_sem_match") == "alertar":
        return True, "sem correspondência -> ALERTAR (padrao_sem_match)"
    return False, "sem correspondência -> IGNORAR (padrao_sem_match)"


def carregar_credenciais():
    """Lê credenciais.json (cria a partir do exemplo na primeira execução)."""
    global _CRED_USUARIO_LOGADO, _CRED_ARQUIVO_CRIADO
    if not os.path.exists(ARQ_CREDENCIAIS):
        _CRED_ARQUIVO_CRIADO = True
        try:
            modelo = dict(CREDENCIAIS_PADRAO)
            modelo["_comentario"] = (
                "Preencha usuario e senha do Feegow. "
                "NUNCA envie este arquivo para o GitHub (ele já está no .gitignore)."
            )
            _gravar_json(ARQ_CREDENCIAIS, modelo)
            log_msg(f"📄 credenciais.json criado com valores de exemplo em {ARQ_CREDENCIAIS}. Preencha usuário e senha.")
        except Exception as e:
            try:
                if os.path.exists(ARQ_CREDENCIAIS_EXEMPLO):
                    shutil.copyfile(ARQ_CREDENCIAIS_EXEMPLO, ARQ_CREDENCIAIS)
                    log_msg("📄 credenciais.json criado a partir do exemplo.")
            except Exception:
                log_msg(f"⚠️ Não foi possível criar credenciais.json ({e}).")

    dados = {}
    try:
        if os.path.exists(ARQ_CREDENCIAIS):
            lido = _ler_json(ARQ_CREDENCIAIS)
            if isinstance(lido, dict):
                dados = lido
    except Exception as e:
        log_msg(f"⚠️ Erro ao ler credenciais.json ({e}). Usando valores padrão.")

    combinado = dict(CREDENCIAIS_PADRAO)
    combinado.update({k: v for k, v in dados.items() if not str(k).startswith("_") and v is not None})
    credenciais = {
        "usuario": str(combinado.get("usuario") or "").strip(),
        "senha": str(combinado.get("senha") or ""),
        "rota_login": str(combinado.get("rota_login") or "auto").strip().lower(),
        "url_login": str(combinado.get("url_login") or CREDENCIAIS_PADRAO["url_login"]),
        "url_login_sso": str(combinado.get("url_login_sso") or CREDENCIAIS_PADRAO["url_login_sso"]),
        "url_lista_espera": str(combinado.get("url_lista_espera") or CREDENCIAIS_PADRAO["url_lista_espera"]),
    }

    if not credenciais["usuario"] or credenciais["usuario"].lower() == CREDENCIAIS_PADRAO["usuario"]:
        log_msg("⚠️ ATENÇÃO: credenciais.json está com os dados de EXEMPLO. Preencha usuário e senha.")
    else:
        if _CRED_USUARIO_LOGADO != credenciais["usuario"]:
            _CRED_USUARIO_LOGADO = credenciais["usuario"]
            log_msg(f"🔐 Credenciais carregadas para: {credenciais['usuario']}")
    return credenciais


def credenciais_incompletas():
    """True quando o login ainda não foi preenchido (arquivo novo/vazio/exemplo)."""
    try:
        cred = carregar_credenciais()
    except Exception:
        return True
    usuario = (cred.get("usuario") or "").strip()
    senha = cred.get("senha") or ""
    if not usuario or not senha:
        return True
    if usuario.lower() == CREDENCIAIS_PADRAO["usuario"]:
        return True
    if senha == CREDENCIAIS_PADRAO["senha"]:
        return True
    return False


def solicitar_credenciais(motivo="primeira_vez"):
    """Abre a janela de configuração na aba Login pedindo usuário e senha.

    Chamado quando o credenciais.json acabou de ser criado ou quando o Feegow
    recusou a senha. Seguro para chamar de dentro da thread do Selenium.
    """
    global _PEDIDO_CRED_PENDENTE
    if _PEDIDO_CRED_PENDENTE:
        return
    _PEDIDO_CRED_PENDENTE = True

    textos = {
        "primeira_vez": (
            "🔐 Login do Feegow",
            "O arquivo credenciais.json foi criado agora.\n\n"
            "Preencha o e-mail e a senha do Feegow na aba Login e clique em Salvar.\n"
            "Assim que salvar, o monitoramento começa sozinho.",
        ),
        "recusadas": (
            "🔐 Senha recusada pelo Feegow",
            "O Feegow não aceitou o e-mail/senha do credenciais.json.\n\n"
            "Confira os dados na aba Login e clique em Salvar.\n"
            "Assim que salvar, eu tento entrar de novo automaticamente.",
        ),
    }
    titulo, mensagem = textos.get(motivo, textos["primeira_vez"])

    def _mostrar():
        global _PEDIDO_CRED_PENDENTE
        try:
            from tkinter import messagebox
            try:
                root.deiconify()
                root.lift()
            except Exception:
                pass
            try:
                messagebox.showinfo(titulo, mensagem, parent=root)
            except Exception:
                pass
            abrir_configuracoes(aba="login")
        finally:
            _PEDIDO_CRED_PENDENTE = False

    try:
        root.after(0, _mostrar)
    except Exception:
        _PEDIDO_CRED_PENDENTE = False


pygame.init()
try:
    DISPOSITIVOS_AUDIO = sdl2_audio.get_audio_device_names(False)
except Exception:
    DISPOSITIVOS_AUDIO = []

# ==============================================================================
# FUNÇÕES DE INTERFACE E JANELAS (TKINTER & OS)
# ==============================================================================
_FILA_LOG = queue.Queue()


def log_msg(mensagem):
    """Log com hora: console + logs/monitor.log + painel.

    Pode ser chamado de qualquer thread. O painel (Tk) NÃO é tocado daqui:
    a linha vai para uma fila que a thread do Tk esvazia (_drenar_fila_log).
    Antes o Selenium escrevia direto no widget do Tk, que não é thread-safe —
    isso podia travar o painel depois de horas rodando.
    """
    timestamp = datetime.datetime.now().strftime("[%H:%M:%S]")
    texto_formatado = f"{timestamp} - {mensagem}\n"
    try:
        print(texto_formatado, end="", flush=True)
    except Exception:
        pass
    try:
        _LOGGER.info(str(mensagem))
    except Exception:
        pass
    _FILA_LOG.put(texto_formatado)


def _drenar_fila_log():
    """Roda na thread do Tk: passa a fila para o painel e corta o excesso."""
    lote = []
    try:
        while len(lote) < 500:
            lote.append(_FILA_LOG.get_nowait())
    except queue.Empty:
        pass
    if lote and 'janela_logs' in globals():
        try:
            janela_logs.configure(state='normal')
            janela_logs.insert(tk.END, "".join(lote))
            linhas = int(janela_logs.index("end-1c").split(".")[0])
            if linhas > LOG_MAX_LINHAS_PAINEL:
                janela_logs.delete("1.0", f"{linhas - LOG_MAX_LINHAS_PAINEL}.0")
            janela_logs.see(tk.END)
            janela_logs.configure(state='disabled')
        except Exception:
            pass
    try:
        root.after(300, _drenar_fila_log)
    except Exception:
        pass

def capturar_hwnd_pelo_pid():
    if driver is None:
        return None
    try:
        driver_pid = driver.service.process.pid
        driver_process = psutil.Process(driver_pid)
        filhos = driver_process.children(recursive=True)
        pids_chrome = [p.pid for p in filhos if 'chrome' in p.name().lower()]

        hwnds_encontrados = []

        def callback(hwnd, lParam):
            # hwnd chega como c_void_p: converter para int, senão o log e o
            # ShowWindow recebem um objeto ctypes em vez do handle.
            h = hwnd.value if hasattr(hwnd, "value") else hwnd
            if not h:
                return True
            pid_janela = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(pid_janela))
            if pid_janela.value in pids_chrome:
                if ctypes.windll.user32.GetWindowTextLengthW(h) > 0:
                    hwnds_encontrados.append(int(h))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), 0)

        return hwnds_encontrados[0] if hwnds_encontrados else None
    except Exception as e:
        log_msg(f"Erro ao rastrear PID: {e}")
        return None

def _janela_visivel():
    """Estado REAL da janela — não confia só no flag (o Tk pode mudar sozinho)."""
    try:
        if not root.winfo_exists():
            return False
        return root.state() not in ("withdrawn", "iconic")
    except Exception:
        return False


def _hwnd_chrome():
    """HWND válido do Chrome; reencontra se o processo foi reaberto."""
    global CHROME_HWND
    try:
        if CHROME_HWND and ctypes.windll.user32.IsWindow(CHROME_HWND):
            return CHROME_HWND
    except Exception:
        pass
    try:
        novo = capturar_hwnd_pelo_pid()
        if novo:
            CHROME_HWND = novo
            return novo
    except Exception:
        pass
    return None


def gerenciar_janelas(acao):
    global is_visible
    if acao == "ocultar":
        try:
            root.withdraw()
        except Exception:
            pass
        hwnd = _hwnd_chrome()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
            except Exception:
                pass
        is_visible = False
        log_msg("Painel e Navegador ocultados da barra de tarefas.")
    elif acao == "mostrar":
        try:
            root.deiconify()
            root.state("normal")
            root.lift()
            root.focus_force()
        except Exception:
            pass
        hwnd = _hwnd_chrome()
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, 5)
                ctypes.windll.user32.SetWindowPos(hwnd, 0, 100, 100, 1024, 768, 0x0040)
            except Exception:
                pass
        is_visible = True
        log_msg("Painel e Navegador agora estão visíveis.")

def ao_fechar_janela():
    log_msg("Janela principal fechada no 'X'. Minimizando para bandeja...")
    gerenciar_janelas("ocultar")

# --- LÓGICA DA JANELA DE ALARME ---
canvas_bg = None

def piscar_janela():
    global cor_alerta_atual, alarme_ativo, canvas_bg
    if not alarme_ativo or not janela_alerta:
        return
    cor_alerta_atual = "#ff3333" if cor_alerta_atual == "#990000" else "#990000"
    try:
        janela_alerta.configure(bg=cor_alerta_atual)
        if canvas_bg:
            canvas_bg.configure(bg=cor_alerta_atual)
    except:
        pass
    root.after(1000, piscar_janela)

def desenhar_icone_alerta(canvas, cx, cy):
    h = 24
    w = 28
    pontos = [cx, cy - h - 5, cx + w, cy + h, cx - w, cy + h]
    canvas.create_polygon([cx+2, cy-h-3, cx+w+2, cy+h+2, cx-w+2, cy+h+2], fill="#cccccc")
    canvas.create_polygon(pontos, fill="#FFD700", outline="#DAA520", width=4, joinstyle=tk.ROUND)
    canvas.create_text(cx, cy + 3, text="!", font=("Segoe UI", 24, "bold"), fill="black")

def desenhar_retangulo_arredondado(canvas, x1, y1, x2, y2, radius=25, **kwargs):
    points = [x1+radius, y1, x1+radius, y1, x2-radius, y1, x2-radius, y1, x2, y1, x2, y1+radius, x2, y1+radius,
              x2, y2-radius, x2, y2-radius, x2, y2, x2-radius, y2, x2-radius, y2, x1+radius, y2, x1+radius, y2,
              x1, y2, x1, y2-radius, x1, y2-radius, x1, y1+radius, x1, y1+radius, x1, y1]
    return canvas.create_polygon(points, **kwargs, smooth=True)

def criar_janela_alarme():
    global janela_alerta, canvas_bg
    if janela_alerta is not None:
        return
    janela_alerta = tk.Toplevel(root)
    janela_alerta.title("ALERTA DE MARCAÇÃO CIRÚRGICA")
    janela_alerta.geometry("800x450")
    janela_alerta.attributes("-topmost", True)
    janela_alerta.overrideredirect(True)
    janela_alerta.configure(bg="#990000")
    
    pos_x = int(janela_alerta.winfo_screenwidth()/2 - 400)
    pos_y = int(janela_alerta.winfo_screenheight()/2 - 225)
    janela_alerta.geometry(f"+{pos_x}+{pos_y}")

    canvas_bg = tk.Canvas(janela_alerta, width=800, height=280, bg="#990000", highlightthickness=0)
    canvas_bg.pack(pady=(40, 20))

    desenhar_retangulo_arredondado(canvas_bg, 40, 20, 760, 260, radius=30, fill="white", outline="#cccccc", width=2)
    canvas_bg.create_text(400, 90, text="NOVA SENHA 'MC' NA FILA", font=("Segoe UI", 28, "bold"), fill="black")
    desenhar_icone_alerta(canvas_bg, 110, 90)
    desenhar_icone_alerta(canvas_bg, 690, 90)
    canvas_bg.create_text(400, 180, text="Clique em CIENTE para assumir o atendimento\ne pausar os alertas sonoros por 15 minutos.", font=("Segoe UI", 20, "bold"), fill="#555555", justify=tk.CENTER)

    btn_ciente = tk.Button(janela_alerta, text=" CIENTE ", font=("Arial", 24, "bold"), bg="#dddddd", fg="black", relief=tk.RAISED, borderwidth=5, cursor="hand2", command=lambda: desarmar_alarme(manual=True))
    btn_ciente.pack(ipady=15, ipadx=50, pady=(0, 20))
    piscar_janela()

def desarmar_alarme(manual=False):
    global alarme_ativo, janela_alerta, pausa_ate, canvas_bg, _TICKET_ALARME
    alarme_ativo = False
    _TICKET_ALARME = ""
    if janela_alerta:
        janela_alerta.destroy()
        janela_alerta = None
        canvas_bg = None
    if manual:
        cfg = carregar_filtros()
        pausa_ate = datetime.datetime.now() + datetime.timedelta(minutes=cfg["pausa_minutos"])
        # (antes usava cfg['termo_gatilho'], chave que não existe mais: o CIENTE
        #  estourava KeyError no Tk depois de pausar)
        log_msg(f"✅ Alerta pausado manualmente por {cfg['pausa_minutos']} min "
                f"(até {pausa_ate.strftime('%H:%M:%S')}).")
    else:
        log_msg("🔄 A senha sumiu. Desarme remoto efetuado. Retomando busca imediata.")

# ==============================================================================
# FUNÇÕES DE ÁUDIO E LOOP
# ==============================================================================
def preparar_audio_inicial():
    texto_padrao = "Atenção! Senha de dilatação."
    log_msg("Baixando e pré-carregando áudio oficial do alarme...")
    try:
        tts = gTTS(text=texto_padrao, lang='pt')
        tts.save(ARQUIVO_OFICIAL_ALERTA)
        log_msg(f"Áudio oficial gerado com sucesso na pasta Temp.")
    except Exception as e:
        log_msg(f"Erro ao pré-carregar áudio inicial: {str(e)}")

_TRAVA_AUDIO = threading.Lock()      # uma fala por vez, nunca duas sobrepostas
_ULTIMO_ANUNCIO = {}                 # senha normalizada -> hora do último anúncio


def dispositivos_de_saida(cfg=None):
    """Para onde a fala vai. None = saída PADRÃO do Windows.

    Antes a fala tocava em TODOS os dispositivos, um depois do outro (alto-falante,
    fone, monitor HDMI...): a mesma frase saía 2-3 vezes seguidas. Agora o padrão é
    só a saída padrão do sistema; "todos" continua disponível no filtros.json.
    """
    cfg = cfg or carregar_filtros()
    escolha = str(cfg.get("saida_audio") or "padrao").strip()
    chave = normalizar_texto(escolha)
    if chave in ("", "PADRAO", "DEFAULT", "SISTEMA"):
        return [None]
    if chave in ("TODOS", "TODAS", "ALL"):
        return list(DISPOSITIVOS_AUDIO) or [None]
    for nome in DISPOSITIVOS_AUDIO:
        if normalizar_texto(nome) == chave:
            return [nome]
    log_msg(f"⚠️ Dispositivo de áudio '{escolha}' não encontrado — usando a saída padrão.")
    return [None]


def tocar_audio_em_dispositivo(nome_dispositivo, arquivo_audio, volume=1.0):
    """Toca o mp3 no dispositivo informado (None = saída padrão do Windows)."""
    with _TRAVA_AUDIO:
        _tocar_audio_sem_trava(nome_dispositivo, arquivo_audio, volume)


def _tocar_audio_sem_trava(nome_dispositivo, arquivo_audio, volume=1.0):
    try:
        pygame.mixer.quit()
        if nome_dispositivo:
            pygame.mixer.init(devicename=nome_dispositivo)
        else:
            pygame.mixer.init()
        pygame.mixer.music.load(arquivo_audio)
        pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if not alarme_ativo:
                # desarmou no meio da fala: para na hora (antes ficava até acabar o mp3)
                pygame.mixer.music.stop()
                break
            time.sleep(0.1)
    except Exception:
        pass

def gerar_e_tocar_alerta_teste(mensagem, modo="configurado", dispositivo_especifico=None):
    """Teste do painel. 'configurado' = mesma saída do alarme; 'unico' = um dispositivo."""
    try:
        timestamp = int(time.time() * 1000)
        audio_file = os.path.join(PASTA_TEMP, f"alerta_teste_{timestamp}.mp3")
        tts = gTTS(text=mensagem, lang='pt')
        tts.save(audio_file)

        if modo == "unico" and dispositivo_especifico:
            tocar_audio_em_dispositivo(dispositivo_especifico, audio_file)
        else:
            for disp in dispositivos_de_saida():
                tocar_audio_em_dispositivo(disp, audio_file)
            
        pygame.mixer.quit()
        try: os.remove(audio_file)
        except: pass
    except Exception as e:
        log_msg(f"Erro no áudio de teste: {str(e)}")

def loop_audio_alarme():
    global alarme_ativo

    cfg = carregar_filtros()
    intervalo = _para_int(cfg.get("intervalo_repeticao_segundos"), 15, 1)
    limite_seg = _para_int(cfg.get("alarme_max_segundos"), 120, 0)
    volume = _para_int(cfg.get("volume_alarme"), 100, 0, 100) / 100.0
    inicio = time.time()

    # PLANO B: Se por algum motivo o arquivo não existir (erro no startup ou apagado), cria agora!
    if not os.path.exists(ARQUIVO_OFICIAL_ALERTA):
        log_msg("⚠️ Arquivo de áudio não encontrado. Gerando arquivo de emergência agora...")
        try:
            texto_padrao = "Atenção! Senha de dilatação."
            tts = gTTS(text=texto_padrao, lang='pt')
            tts.save(ARQUIVO_OFICIAL_ALERTA)
            log_msg("Áudio de emergência gerado com sucesso!")
        except Exception as e:
            log_msg(f"Falha ao gerar áudio de emergência. O alarme piscará sem som. Erro: {e}")

    def estourou():
        return bool(limite_seg) and (time.time() - inicio) > limite_seg

    try:
        while alarme_ativo:
            if estourou():
                log_msg(f"⏱️ Alarme tocou por {limite_seg}s e foi desarmado automaticamente.")
                root.after(0, lambda: desarmar_alarme(manual=False))
                break

            if os.path.exists(ARQUIVO_OFICIAL_ALERTA):
                for disp in dispositivos_de_saida(cfg):
                    if not alarme_ativo:
                        break
                    tocar_audio_em_dispositivo(disp, ARQUIVO_OFICIAL_ALERTA, volume)
                _ULTIMO_ANUNCIO[normalizar_texto(_TICKET_ALARME)] = time.time()

            for _ in range(intervalo):
                if not alarme_ativo or estourou():
                    break
                time.sleep(1)

        pygame.mixer.quit()
    except Exception as e:
        log_msg(f"Erro no loop de alarme: {e}")

_AVISO_REPETICAO = {}


def anunciado_recentemente(ticket, cfg=None):
    """True se a mesma senha já foi falada há menos de anti_repeticao_segundos."""
    cfg = cfg or carregar_filtros()
    janela = _para_int(cfg.get("anti_repeticao_segundos"), 60, 0)
    chave = normalizar_texto(ticket)
    if not janela or not chave:
        return False
    ultimo = _ULTIMO_ANUNCIO.get(chave)
    if ultimo is None or (time.time() - ultimo) >= janela:
        return False
    if time.time() - _AVISO_REPETICAO.get(chave, 0) > janela:
        _AVISO_REPETICAO[chave] = time.time()
        log_msg(f"🔕 '{ticket}' já foi anunciada há {int(time.time() - ultimo)}s — "
                f"sem repetir a fala (anti_repeticao_segundos={janela}).")
    return True


def disparar_estado_alarme():
    global alarme_ativo
    if alarme_ativo:
        return

    cfg = carregar_filtros()
    quer_tela = cfg.get("alerta_tela", True)
    quer_voz = cfg.get("alerta_voz", True)

    if not quer_tela and not quer_voz:
        log_msg("⚠️ Chamada detectada, mas alerta_tela e alerta_voz estão desligados no filtros.json — nada será exibido nem tocado.")
        return

    # Anti-repetição: a MESMA senha não é anunciada de novo dentro da janela
    # (ex.: alarme desarmado pelo tempo máximo e a senha ainda na tela).
    if quer_voz and anunciado_recentemente(_TICKET_ALARME, cfg):
        quer_voz = False
        if not quer_tela:
            return

    alarme_ativo = True
    ativos = []
    if quer_tela:
        ativos.append("janela na tela")
        root.after(0, criar_janela_alarme)
    if quer_voz:
        ativos.append("voz")
        threading.Thread(target=loop_audio_alarme, daemon=True).start()
    log_msg("🚨 Estado de alarme iniciado (" + " + ".join(ativos) + ").")

# ==============================================================================
# LÓGICA DO SELENIUM E BYPASS INVISÍVEL VIA JS
# ==============================================================================
def _versao_chrome_instalada():
    """Versão do Chrome instalado: registro BLBeacon -> pasta -> ''.

    O registro é o caminho mais confiável; a pasta serve de reserva.
    (GetFileVersionInfo em chrome.exe devolve lixo nessa build.)
    """
    try:
        import winreg
        for raiz, caminho in (
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
        ):
            try:
                with winreg.OpenKey(raiz, caminho) as chave:
                    valor, _tipo = winreg.QueryValueEx(chave, "version")
                    valor = str(valor or "").strip()
                    if valor:
                        return valor
            except OSError:
                continue
    except Exception:
        pass

    versoes = []
    for raiz in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        if not raiz:
            continue
        base = os.path.join(raiz, "Google", "Chrome", "Application")
        if not os.path.isdir(base):
            continue
        for nome in os.listdir(base):
            if nome[:1].isdigit() and os.path.isfile(os.path.join(base, nome, "chrome.exe")):
                versoes.append(nome)
    if versoes:
        versoes.sort(key=lambda v: [int(p) if p.isdigit() else 0 for p in v.split(".")], reverse=True)
        return versoes[0]
    return ""


def _pasta_drivers():
    """Pasta onde o próprio programa guarda os chromedriver baixados."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", PASTA_SCRIPT), "MonitorFeegow", "drivers")
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        return None


def _versao_de_um_driver(caminho):
    """Lê a versão de um chromedriver.exe ('ChromeDriver 157.0.8103.51')."""
    try:
        saida = subprocess.run(
            [caminho, "--version"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: não pisca console
        )
        texto = (saida.stdout or "") + (saida.stderr or "")
        achado = re.search(r"ChromeDriver\s+([0-9]+(?:\.[0-9]+)*)", texto)
        return achado.group(1) if achado else ""
    except Exception:
        return ""


def _driver_compativel(versao_driver, versao_chrome):
    """Compara major.minor.build (157.0.8103).

    O patch (último número) pode mudar sem problema; se o build mudar, o Chrome
    foi atualizado e o driver precisa ser baixado de novo.
    """
    if not versao_driver or not versao_chrome:
        return False
    return ".".join(versao_driver.split(".")[:3]) == ".".join(versao_chrome.split(".")[:3])


def _baixar_chromedriver(versao_chrome):
    """Baixa o ChromeDriver da versão exata do Chrome instalado."""
    import io
    import ssl
    import urllib.request
    import zipfile

    if not versao_chrome:
        return ""
    pasta = _pasta_drivers()
    if not pasta:
        log_msg("⚠️ Sem pasta para salvar o ChromeDriver (%LOCALAPPDATA% indisponível).")
        return ""

    alvo = os.path.join(pasta, f"chromedriver-{versao_chrome}.exe")
    enderecos = (
        f"https://storage.googleapis.com/chrome-for-testing-public/{versao_chrome}/win64/chromedriver-win64.zip",
        f"https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/{versao_chrome}/win64/chromedriver-win64.zip",
    )
    contexto = ssl.create_default_context()

    for url in enderecos:
        try:
            log_msg(f"⬇️ Baixando ChromeDriver {versao_chrome} ({url.split('/')[2]})...")
            requisicao = urllib.request.Request(url, headers={"User-Agent": "MonitorFeegow/1.0"})
            with urllib.request.urlopen(requisicao, timeout=90, context=contexto) as resposta:
                if getattr(resposta, "status", 200) != 200:
                    continue
                bruto = resposta.read()
            if len(bruto) < 1_000_000:
                log_msg(f"⚠️ Download muito pequeno ({len(bruto)} bytes) — tentando o próximo endereço.")
                continue

            with zipfile.ZipFile(io.BytesIO(bruto)) as pacote:
                interno = next((n for n in pacote.namelist()
                                if n.lower().endswith("chromedriver.exe")), "")
                if not interno:
                    continue
                temporario = alvo + ".tmp"
                with pacote.open(interno) as origem, open(temporario, "wb") as saida:
                    shutil.copyfileobj(origem, saida)

            baixada = _versao_de_um_driver(temporario)
            if not _driver_compativel(baixada, versao_chrome):
                log_msg(f"⚠️ Driver baixado ({baixada or 'ilegível'}) não casa com o Chrome {versao_chrome}.")
                try:
                    os.remove(temporario)
                except Exception:
                    pass
                continue

            os.replace(temporario, alvo)
            log_msg(f"✅ ChromeDriver {baixada} salvo em {alvo}")
            return alvo
        except Exception as e:
            log_msg(f"⚠️ Falha no download ({url.split('/')[2]}): {e}")
            continue
    return ""


def _candidatos_driver():
    """Todos os chromedriver.exe plausíveis, do mais novo para o mais antigo."""
    vistos, lista = set(), []

    def adicionar(caminho):
        if not caminho:
            return
        chave = os.path.normcase(os.path.abspath(caminho))
        if chave in vistos or not os.path.isfile(caminho):
            return
        vistos.add(chave)
        lista.append(caminho)

    pasta = _pasta_drivers()
    if pasta and os.path.isdir(pasta):
        arquivos = [a for a in os.listdir(pasta) if a.lower().endswith(".exe")]
        arquivos.sort(
            key=lambda a: [int(p) for p in re.findall(r"\d+", a)] or [0],
            reverse=True,
        )
        for nome in arquivos:
            adicionar(os.path.join(pasta, nome))

    adicionar(os.path.join(PASTA_SCRIPT, "chromedriver.exe"))
    adicionar(os.path.join(PASTA_SCRIPT, "drivers", "chromedriver.exe"))
    adicionar(os.path.join(PASTA_SCRIPT, "chromedriver", "chromedriver.exe"))
    adicionar(shutil.which("chromedriver"))

    for raiz in (os.path.join(os.path.expanduser("~"), ".cache", "selenium"),
                 os.path.join(os.path.expanduser("~"), ".wdm"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "WebDriver")):
        if not raiz or not os.path.isdir(raiz):
            continue
        for pasta_raiz, _sub, arquivos in os.walk(raiz):
            if "chromedriver.exe" in arquivos:
                adicionar(os.path.join(pasta_raiz, "chromedriver.exe"))

    return lista


def _encerrar_instancias_antigas(perfil):
    """Encerra Chrome/chromedriver órfãos que ficaram segurando o nosso perfil.

    Sem isso, um Chrome que sobrou de uma execução morta na força trava a pasta
    e o próximo início morre com 'Chrome failed to start: crashed
    (DevToolsActivePort file doesn't exist)'.
    """
    if not perfil:
        return 0
    alvo = os.path.normcase(os.path.abspath(perfil))
    mortos = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            nome = (proc.info.get("name") or "").lower()
            if nome not in ("chrome.exe", "chromedriver.exe"):
                continue
            linha = os.path.normcase(" ".join(proc.info.get("cmdline") or []))
            if alvo and alvo in linha:
                proc.kill()
                mortos += 1
        except Exception:
            continue
    if mortos:
        log_msg(f"🧹 Encerrei {mortos} processo(s) de Chrome antigos que seguravam o perfil.")
        time.sleep(1.5)
    return mortos


def _marcar_perfil_limpo(perfil):
    """Marca o perfil como fechado corretamente.

    Sem isso, depois de uma execução morta na força o Chrome abre o balão
    "Restaurar páginas?" por cima da lista de espera.
    """
    if not perfil:
        return
    caminho = os.path.join(perfil, "Default", "Preferences")
    if not os.path.isfile(caminho):
        return
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        if not isinstance(dados, dict):
            return
        config_perfil = dados.get("profile")
        if not isinstance(config_perfil, dict):
            config_perfil = {}
        config_perfil["exit_type"] = "Normal"
        config_perfil["exited_cleanly"] = True
        dados["profile"] = config_perfil

        temporario = caminho + ".tmp"
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo)
        os.replace(temporario, caminho)
    except Exception:
        pass


def _limpar_sessao_antiga(perfil):
    """Apaga os arquivos de sessão do perfil (fonte do 'Restaurar páginas?')."""
    if not perfil:
        return
    default = os.path.join(perfil, "Default")
    for nome in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        try:
            caminho = os.path.join(default, nome)
            if os.path.isfile(caminho):
                os.remove(caminho)
        except Exception:
            pass
    pasta_sessoes = os.path.join(default, "Sessions")
    if os.path.isdir(pasta_sessoes):
        try:
            shutil.rmtree(pasta_sessoes)
        except Exception:
            pass


def _pasta_perfil_chrome():
    """Perfil fixo do Chrome do monitoramento.

    Sem `--user-data-dir`, o chromedriver cria uma pasta nova `scoped_dir<pid>_*`
    no TEMP a cada execução (60-90 MB cada) e nunca apaga.
    """
    base = os.path.join(os.environ.get("LOCALAPPDATA", PASTA_SCRIPT), "MonitorFeegow", "chrome_profile")
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        return None


def _limpar_scoped_dirs_orfas(idade_min_horas=1.0):
    """Remove perfis temporários de Chrome deixados por execuções anteriores.

    Só apaga `scoped_dir<pid>_*` do TEMP quando o PID dono já morreu e a pasta
    tem mais de `idade_min_horas` (nunca mexe em uso por outro ChromeDriver).
    """
    import tempfile
    temporario = tempfile.gettempdir()
    removidas = 0
    agora = time.time()
    try:
        nomes = os.listdir(temporario)
    except Exception:
        return 0

    for nome in nomes:
        if not nome.startswith("scoped_dir"):
            continue
        caminho = os.path.join(temporario, nome)
        if not os.path.isdir(caminho):
            continue
        try:
            if (agora - os.path.getmtime(caminho)) < idade_min_horas * 3600:
                continue
            resto = nome[len("scoped_dir"):]
            parte_pid = resto.split("_")[0]
            pid = int(parte_pid) if parte_pid.isdigit() else 0
            if pid and psutil.pid_exists(pid):
                continue
            shutil.rmtree(caminho, ignore_errors=True)
            removidas += 1
        except Exception:
            continue

    if removidas:
        log_msg(f"🧹 Limpeza: {removidas} perfil(is) temporário(s) de Chrome antigos removidos do TEMP.")
    return removidas


def _garantir_chromedriver():
    """Devolve um chromedriver compatível com o Chrome instalado.

    Fluxo: lê a versão do Chrome, procura um driver compatível na máquina e,
    se não houver, baixa em %LOCALAPPDATA%\\MonitorFeegow\\drivers.
    """
    versao_chrome = _versao_chrome_instalada()
    candidatos = _candidatos_driver()

    if versao_chrome:
        log_msg(f"🔎 Chrome instalado: {versao_chrome}")
        for caminho in candidatos:
            versao_driver = _versao_de_um_driver(caminho)
            if _driver_compativel(versao_driver, versao_chrome):
                log_msg(f"✅ ChromeDriver compatível: {os.path.basename(caminho)} ({versao_driver})")
                return caminho

        log_msg("⬇️ Nenhum ChromeDriver compatível na máquina — baixando o da versão do Chrome...")
        baixado = _baixar_chromedriver(versao_chrome)
        if baixado:
            return baixado
        log_msg("⚠️ Não consegui baixar o ChromeDriver. Vou tentar com o que já existe.")
    else:
        log_msg("⚠️ Não consegui descobrir a versão do Chrome instalado.")

    if candidatos:
        log_msg(f"⚠️ Usando ChromeDriver sem confirmar a versão: {os.path.basename(candidatos[0])}")
        return candidatos[0]
    return ""


def criar_driver_chrome(options):
    """Cria o webdriver.Chrome priorizando o chromedriver local.

    Só tenta baixar da internet se não houver nenhum chromedriver na máquina.
    """
    achado = _garantir_chromedriver()
    if achado:
        log_msg(f"🚗 chromedriver em uso: {achado}")
        return webdriver.Chrome(service=Service(executable_path=achado), options=options)

    if ChromeDriverManager is None:
        raise RuntimeError(
            "nenhum chromedriver.exe encontrado e o pacote webdriver-manager não está instalado"
        )
    log_msg("🚗 Nenhum chromedriver local — tentando baixar (webdriver_manager)...")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


ARGUMENTOS_CHROME = (
    "--disable-gpu",
    "--no-sandbox",
    "--log-level=3",
    "--window-position=-10000,-10000",
    # sem "Restaurar páginas?" e sem telas de primeira execução
    "--no-first-run",
    "--no-default-browser-check",
    "--hide-crash-restore-bubble",
    "--disable-session-crashed-bubble",
    "--disable-infobars",
    # A janela fica fora da tela/oculta: sem estas flags o Chrome trata a aba
    # como "em segundo plano" e passa a congelar timers, suspender a página
    # (Page Lifecycle freeze) e até descartar a aba para economizar memória.
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling,HighEfficiencyModeAvailable",
    "--disable-hang-monitor",
)


def _abrir_chrome():
    """Abre o Chrome (perfil fixo; se falhar, temporário). True quando abriu."""
    global driver, CHROME_HWND
    log_msg("Iniciando WebDriver no 'limbo' (fora da tela)...")

    perfil_chrome = _pasta_perfil_chrome()
    if perfil_chrome:
        # perfil fixo + Chrome órfão de execução anterior = pasta travada
        _encerrar_instancias_antigas(perfil_chrome)
        _limpar_sessao_antiga(perfil_chrome)
        _marcar_perfil_limpo(perfil_chrome)

    def montar_options(com_perfil=True):
        opcoes = webdriver.ChromeOptions()
        opcoes.add_experimental_option(
            "prefs", {"profile.default_content_setting_values.notifications": 2})
        for argumento in ARGUMENTOS_CHROME:
            opcoes.add_argument(argumento)
        if com_perfil and perfil_chrome:
            opcoes.add_argument(f"--user-data-dir={perfil_chrome}")
        return opcoes

    _limpar_scoped_dirs_orfas()

    try:
        driver = criar_driver_chrome(montar_options(True))
    except Exception as e:
        log_msg(f"⚠️ O Chrome não abriu com o perfil fixo ({type(e).__name__}). "
                "Tentando com perfil temporário...")
        try:
            driver = criar_driver_chrome(montar_options(False))
            log_msg("✔️ Chrome aberto com perfil temporário (o perfil fixo estava em uso).")
        except Exception as e2:
            log_msg(f"🛑 O WebDriver não iniciou: {type(e2).__name__}: {e2}")
            log_msg("   Se persistir, baixe o chromedriver da sua versão do Chrome em "
                    "https://googlechromelabs.github.io/chrome-for-testing/ e salve como "
                    f"'chromedriver.exe' nesta pasta: {PASTA_SCRIPT}")
            driver = None
            return False

    # Nenhuma chamada fica pendurada para sempre: página trava -> exceção -> recuperação
    try:
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
    except Exception:
        pass

    time.sleep(2)
    CHROME_HWND = capturar_hwnd_pelo_pid()
    if CHROME_HWND:
        log_msg(f"Navegador interceptado com sucesso pelo PID. HWND: {CHROME_HWND}")
        if not _janela_visivel():
            gerenciar_janelas("ocultar")
    else:
        log_msg("ERRO: Não foi possível rastrear a janela do Chrome gerada.")
    return True


def _fechar_chrome():
    """Fecha o Chrome do monitor sem travar (quit com tempo limite + kill)."""
    global driver, CHROME_HWND
    atual, driver, CHROME_HWND = driver, None, None
    if atual is not None:
        fechador = threading.Thread(target=lambda: _ignorar_erro(atual.quit), daemon=True)
        fechador.start()
        fechador.join(timeout=10)
    try:
        _encerrar_instancias_antigas(_pasta_perfil_chrome())
    except Exception:
        pass


def _ignorar_erro(funcao):
    try:
        funcao()
    except Exception:
        pass


def iniciar_selenium():
    """Sobe a thread do monitoramento (no máximo uma)."""
    global _THREAD_MONITOR, _MONITOR_DESEJADO
    with _TRAVA_MONITOR:
        if _THREAD_MONITOR is not None and _THREAD_MONITOR.is_alive():
            log_msg("🔁 O monitoramento já está rodando — ignorando novo pedido de início.")
            return
        _MONITOR_DESEJADO = True
        _THREAD_MONITOR = threading.Thread(target=loop_supervisionado, name="monitor", daemon=True)
        _THREAD_MONITOR.start()


def loop_supervisionado():
    """Abre o Chrome, faz login e monitora. Se cair, reabre (nível 2) com
    espera crescente e limite de reinícios por hora."""
    global _SELENIUM_RODANDO, _PEDIDO_REINICIO_CHROME, _CRED_RECUSADAS
    _SELENIUM_RODANDO = True
    _CRED_RECUSADAS = False
    try:
        while not _ENCERRANDO:
            _PEDIDO_REINICIO_CHROME = ""
            try:
                motivo = executar_sessao_chrome()
            except Exception as e:
                motivo = f"erro inesperado: {type(e).__name__}: {e}"
            _fechar_chrome()
            if motivo is None or _ENCERRANDO or _CRED_RECUSADAS:
                break
            log_msg(f"♻️ [N2] Reabrindo o Chrome. Motivo: {motivo}")
            espera = _proxima_espera_reinicio()
            if espera > 5:
                log_msg(f"⏳ Aguardando {espera}s antes de reabrir (proteção contra loop de reinício).")
            _dormir(espera, progresso=True)
    finally:
        _SELENIUM_RODANDO = False
        _definir_etapa("parado")


def executar_sessao_chrome():
    """Uma "vida" do Chrome. Devolve o motivo para reabrir ou None para parar."""
    if not internet_ok():
        aguardar_internet()
        if _ENCERRANDO:
            return None
    _definir_etapa("abrindo_chrome")
    _atualizar_status("recuperando", "Abrindo o navegador...")
    if not _abrir_chrome():
        return "o Chrome não abriu"
    _definir_etapa("login")
    _atualizar_status("recuperando", "Entrando no Feegow...")
    if not fazer_login():
        if _CRED_RECUSADAS or credenciais_incompletas():
            _definir_etapa("aguardando_login")
            _atualizar_status("falha", "Login recusado — confira as credenciais")
            log_msg("🛑 Login não concluído — monitoramento parado até salvar o login na janela ⚙️.")
            return None
        return "o login não foi concluído"
    return verificar_botao_chamar_senha()


# --- Helpers de login ------------------------------------------------------
def _visivel(driver_alvo, seletores, timeout=12.0):
    """Retorna o primeiro elemento visível entre os seletores CSS informados."""
    fim = time.time() + timeout
    while time.time() < fim:
        for seletor in seletores:
            try:
                for elemento in driver_alvo.find_elements(By.CSS_SELECTOR, seletor):
                    if elemento.is_displayed() and elemento.is_enabled():
                        return elemento
            except Exception:
                continue
        time.sleep(0.25)
    return None


def _texto_corpo(driver_alvo):
    try:
        return (driver_alvo.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        return ""


def _enviar_form(driver_alvo):
    """Clica no botão de envio do formulário; se não achar, submete via JS."""
    for seletor in (".sl-btn-primary", "button[type='submit']", "#Entrar", "input[type='submit']"):
        try:
            botao = driver_alvo.find_element(By.CSS_SELECTOR, seletor)
            if botao.is_displayed():
                botao.click()
                return True
        except Exception:
            continue
    try:
        driver_alvo.execute_script("var f=document.querySelector('form'); if(f){f.submit();}")
        return True
    except Exception:
        return False


def _sessao_ativa(driver_alvo):
    """True quando já saiu da tela de login e está dentro do /main/."""
    try:
        url = (driver_alvo.current_url or "").lower()
    except Exception:
        return False
    return "/main/" in url and "/sso" not in url and "p=login" not in url


def _credenciais_recusadas(texto):
    """Mensagens de e-mail/senha errados — nunca tratar como conflito de sessão."""
    return ("incorret" in texto) or ("não conferem" in texto) or ("nao conferem" in texto)


def _confirmar_troca_sessao(driver_alvo, timeout=90.0):
    """SSO: com outra máquina logada o form vem com input[name=disconnect].

    Confirmar derruba a outra máquina (é assim que o SSO funciona). Se o input
    não existir, apenas espera a sessão ficar ativa.
    """
    fim = time.time() + timeout
    avisou = False
    while time.time() < fim:
        if _sessao_ativa(driver_alvo):
            return True
        try:
            tem_disconnect = bool(driver_alvo.find_elements(By.CSS_SELECTOR, "input[name='disconnect']"))
        except Exception:
            tem_disconnect = False
        if tem_disconnect:
            if not avisou:
                log_msg("⚠️ Sessão ativa em outra máquina. Confirmando a troca de sessão (a outra máquina será desconectada)...")
                avisou = True
            _enviar_form(driver_alvo)
            time.sleep(2.0)
            continue
        if _credenciais_recusadas(_texto_corpo(driver_alvo)):
            return False
        time.sleep(1.0)
    return _sessao_ativa(driver_alvo)


def login_sso(usuario, senha, url_sso):
    """Login na rota nova (/main/sso/): etapa e-mail -> etapa senha -> handshake."""
    driver.get(url_sso)

    campo_email = _visivel(driver, ["#User", "input[name='User']", "input[type='email']"], timeout=20)
    if not campo_email:
        return False, "campo de e-mail não apareceu na tela do SSO"
    campo_email.clear()
    campo_email.send_keys(usuario)
    if not _enviar_form(driver):
        return False, "não encontrei o botão 'Continuar' do SSO"
    log_msg("SSO: e-mail enviado. Aguardando a etapa da senha...")

    campo_senha = _visivel(
        driver,
        ["#password", "input[name='password']", "input[type='password']"],
        timeout=25,
    )
    if not campo_senha:
        texto = _texto_corpo(driver)
        if _credenciais_recusadas(texto):
            return False, "e-mail recusado na etapa 1 do SSO"
        try:
            if driver.find_elements(By.CSS_SELECTOR, "input[name='disconnect']"):
                if _confirmar_troca_sessao(driver):
                    return True, "sessão assumida direto no handshake do SSO"
        except Exception:
            pass
        return False, "a etapa da senha não apareceu"

    campo_senha.clear()
    campo_senha.send_keys(senha)
    if not _enviar_form(driver):
        return False, "não encontrei o botão de enviar a senha no SSO"
    log_msg("SSO: senha enviada. Validando com o servidor...")

    if _confirmar_troca_sessao(driver, timeout=90):
        return True, "login SSO concluído"
    if _credenciais_recusadas(_texto_corpo(driver)):
        return False, "senha incorreta no SSO"
    return False, "o SSO não confirmou o login a tempo"


def login_legado(usuario, senha, url_login):
    """Login na rota antiga (?P=Login) mantendo o bypass de sessão dupla."""
    usuario_js = json.dumps(usuario)
    senha_js = json.dumps(senha)

    driver.get(url_login)
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "User")))
    except TimeoutException:
        return False, "formulário antigo não encontrado"

    driver.execute_script(f"document.getElementById('User').value = {usuario_js};")
    driver.execute_script(f"document.getElementById('password').value = {senha_js};")
    driver.execute_script("document.getElementById('Entrar').click();")
    log_msg("Login preenchido. Aguardando validação do servidor...")

    try:
        WebDriverWait(driver, 6).until(EC.presence_of_element_located(
            (By.XPATH, "//*[contains(text(), 'Este usuário já está conectado em outra máquina.')]")))
    except TimeoutException:
        if _sessao_ativa(driver):
            log_msg("✔️ Login direto bem-sucedido. Nenhuma barreira encontrada.")
            return True, "login direto bem-sucedido"
        if _credenciais_recusadas(_texto_corpo(driver)):
            return False, "credenciais recusadas na rota antiga"
        return _sessao_ativa(driver), "o Feegow não confirmou o login"

    log_msg("⚠️ Sessão dupla detectada! Acionando metralhadora de Bypass Invisível...")

    js_bypass = f"""
    const user = {usuario_js};
    const pass = {senha_js};
    const actionUrl = window.location.href;

    document.body.innerHTML = '<div style="display:flex; justify-content:center; align-items:center; height:100vh; background:#17a2b8; color:white; font-family:sans-serif; flex-direction:column;"><h2>Autenticando Acesso</h2><p>Bypass Invisível em andamento...</p></div>';

    (async function() {{
        let sucesso = false;
        while (!sucesso) {{
            try {{
                let res = await fetch(actionUrl, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
                    body: new URLSearchParams({{User: user, password: pass}})
                }});
                let html = await res.text();

                if (html.includes('Este usuário já está conectado')) {{
                    await new Promise(resolve => setTimeout(resolve, 50));
                }} else {{
                    sucesso = true;
                    window.location.href = res.url || 'https://app.feegow.com/main/';
                }}
            }} catch (e) {{
                await new Promise(resolve => setTimeout(resolve, 500));
            }}
        }}
    }})();
    """
    driver.execute_script(js_bypass)
    log_msg("JS injetado. Fuzilando requisições em background...")

    fim = time.time() + 300
    while time.time() < fim:
        if _sessao_ativa(driver):
            log_msg("✔️ Bypass efetuado! Conexão estabelecida com sucesso.")
            return True, "bypass concluído"
        time.sleep(1.0)
    return False, "o bypass não confirmou a sessão a tempo"


def fazer_login():
    """Autentica no Feegow tentando a rota SSO e a legada (configurável no JSON)."""
    cred = carregar_credenciais()
    usuario = cred["usuario"]
    senha = cred["senha"]
    rota = cred["rota_login"]
    url_sso = cred["url_login_sso"]
    url_legado = cred["url_login"]

    if not usuario or usuario.lower() == CREDENCIAIS_PADRAO["usuario"] or not senha:
        log_msg("🛑 credenciais.json ainda está com os dados de exemplo. Corrija antes de continuar.")
        solicitar_credenciais("primeira_vez")
        return False

    if rota in ("sso", "nova", "novo"):
        rotas = [("SSO", login_sso, url_sso)]
    elif rota in ("legado", "legacy", "antigo", "antiga"):
        rotas = [("LEGADO", login_legado, url_legado)]
    else:
        rotas = [("SSO", login_sso, url_sso), ("LEGADO", login_legado, url_legado)]

    global _CRED_RECUSADAS
    espera = 5.0
    for tentativa in range(1, 21):
        if _ENCERRANDO or _PEDIDO_REINICIO_CHROME:
            return False
        for nome_rota, funcao_login, url in rotas:
            registrar_batimento(ok=False)  # o login está andando (o watchdog não deve agir)
            log_msg(f"🔐 Login pela rota {nome_rota} (tentativa {tentativa}): {url}")
            try:
                ok, motivo = funcao_login(usuario, senha, url)
            except Exception as e:
                ok, motivo = False, f"erro inesperado: {e}"
            if ok:
                log_msg(f"✔️ Login OK pela rota {nome_rota} ({motivo}).")
                return True
            log_msg(f"⚠️ Rota {nome_rota} falhou: {motivo}")
            if ("credenciais" in motivo or "senha incorreta" in motivo
                    or "e-mail recusado" in motivo):
                log_msg("🛑 Credenciais recusadas pelo Feegow. Confira usuario/senha no credenciais.json — "
                        "parando para não travar a conta.")
                _CRED_RECUSADAS = True
                solicitar_credenciais("recusadas")
                return False
            if _erro_chrome_morto(motivo):
                return False
        _dormir(espera, progresso=True)
        espera = min(espera * 1.8, 30.0)

    log_msg("🛑 Não foi possível autenticar após várias tentativas.")
    return False

def fechar_modal():
    try:
        webdriver.ActionChains(driver).send_keys('\ue00c').perform() # ESC
    except:
        pass

def _xpath_botao_senha(termos):
    """Monta o XPath (método tradicional) que acha o botão da senha.

    Usa contains() no texto do botão, com translate() para ignorar a diferença
    entre maiúsculas e minúsculas — os acentos precisam bater como aparecem na
    tela, então os termos do filtros.json devem ser escritos igual ao Feegow.
    """
    condicoes = []
    for termo in termos:
        seguro = termo.replace("'", "")
        condicoes.append(
            "contains(translate(normalize-space(.), "
            "'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
            f"'{seguro}')"
        )
    return "//button[" + " or ".join(condicoes) + "]"


def _texto_parece_senha(texto, cfg):
    """True quando o texto do botão é mesmo uma senha das listas do filtro.

    Sem esta checagem, qualquer botão da página que apenas CONTENHA um
    `termos_gatilho` (ex.: um filtro escrito "RECEPÇÃO") era confundido com a
    senha no modal: o script achava que a senha continuava na tela e o alarme
    nunca desarmava.
    """
    alvo = normalizar_texto(texto)
    if not alvo:
        return False

    modo_exato = cfg["comparacao"] in ("exata", "exato", "igual")
    for senha in (cfg["whitelist"] + cfg["blacklist"]):
        if not senha:
            continue
        if modo_exato:
            if alvo == senha:
                return True
        elif alvo == senha or alvo.startswith(senha):
            return True

    # Senha fora das listas: só conta se o filtro manda alertar no que não casar.
    if cfg.get("padrao_sem_match") == "alertar":
        return any(normalizar_texto(t) in alvo for t in cfg["termos_gatilho"] if t)
    return False


def _senhas_na_tela(cfg, timeout=8.0):
    """TODAS as senhas visíveis no modal: lista de (texto, elemento).

    O modal do Feegow lista **várias** senhas ao mesmo tempo (uma linha por
    ticket). Antes o script pegava só a primeira que casava com o XPath — se
    fosse uma senha de IGNORAR (ex.: AGENDAMENTOS), a de ALERTAR (DILATAÇÃO -
    CCC) que estava na linha de baixo nunca era avaliada.
    """
    termos = [str(t).strip().upper() for t in (cfg.get("termos_gatilho") or []) if str(t).strip()]
    if not termos:
        return []

    xpath = _xpath_botao_senha(termos)
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            elementos = driver.find_elements(By.XPATH, xpath)
        except Exception:
            elementos = []

        achadas, vistos = [], set()
        for elemento in elementos:
            try:
                if not elemento.is_displayed():
                    continue
                texto = (elemento.text or "").strip()
            except Exception:
                continue
            if not texto or not _texto_parece_senha(texto, cfg):
                continue
            chave = normalizar_texto(texto)
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            achadas.append((texto, elemento))

        if achadas:
            return achadas
        time.sleep(0.35)
    return []


def verificar_botao_explicacao():
    global alarme_ativo, _TICKET_ALARME
    cfg = carregar_filtros()

    senhas = _senhas_na_tela(cfg, timeout=8.0)

    if not senhas:
        # Nenhuma senha no modal: se o alarme estava tocando, a senha saiu da fila
        if alarme_ativo:
            log_msg("🔇 A senha saiu da tela — desarmando o alarme.")
            root.after(0, lambda: desarmar_alarme(manual=False))
        fechar_modal()
        return

    a_alertar = []
    try:
        for texto, _elemento in senhas:
            disparar, motivo = avaliar_chamada(texto)
            log_msg(f"🔎 Senha '{texto}' -> {motivo}")
            if disparar:
                a_alertar.append(texto)

        if a_alertar:
            if datetime.datetime.now() > pausa_ate:
                if not alarme_ativo:
                    log_msg(f"🚨 Senha(s) para alertar na tela: {', '.join(a_alertar)}")
                _TICKET_ALARME = a_alertar[0]
                disparar_estado_alarme()
            else:
                log_msg(f"Ignorando chamada. Dentro do período de pausa até {pausa_ate.strftime('%H:%M:%S')}.")
        elif alarme_ativo:
            log_msg("🔇 Nenhuma senha para alertar na tela — desarmando o alarme.")
            root.after(0, lambda: desarmar_alarme(manual=False))
    except Exception as e:
        log_msg(f"⚠️ Falha ao avaliar as senhas: {e}")
    finally:
        fechar_modal()


XPATH_BOTAO_CHAMAR = ("//button[contains(concat(' ', normalize-space(@class), ' '), "
                      "' callTicketBtn ')]")
ESTADOS_PAGINA_VIVA = ("ok_com_botao", "ok_sem_botao", "carregando")


def abrir_lista_espera(url_lista):
    """Vai (ou volta) para a Lista de Espera."""
    driver.get(url_lista)


def verificar_botao_chamar_senha():
    """Loop principal do monitoramento.

    A cada ciclo clica em "Chamar senha", avalia o modal e registra o batimento.
    Se algo falha, diagnostica a página e recupera em níveis:
      N1 recarregar a Lista de Espera / refazer o login se a sessão caiu;
      N2 devolver o motivo para o loop_supervisionado reabrir o Chrome.
    """
    global _PEDIDO_RECARGA
    cred = carregar_credenciais()
    url_lista = cred["url_lista_espera"]
    try:
        abrir_lista_espera(url_lista)
    except Exception as e:
        if _erro_chrome_morto(e):
            return f"Chrome parou ao abrir a lista ({type(e).__name__})"
    _definir_etapa("monitorando")
    registrar_batimento(ok=False)
    falhas = 0
    ultimo_reload = time.time()
    inicio_chrome = time.time()

    while not _ENCERRANDO:
        if _PEDIDO_REINICIO_CHROME:
            return f"pedido do watchdog ({_PEDIDO_REINICIO_CHROME})"
        cfg = carregar_filtros()

        if _PEDIDO_RECARGA:
            _PEDIDO_RECARGA = False
            log_msg("🔄 [N1] Recarregando a Lista de Espera (pedido do watchdog).")
            try:
                abrir_lista_espera(url_lista)
                ultimo_reload = time.time()
            except Exception as e:
                if _erro_chrome_morto(e):
                    return f"Chrome parou ao recarregar ({type(e).__name__})"

        try:
            fechar_modal()
            botao = WebDriverWait(driver, 12).until(
                EC.element_to_be_clickable((By.XPATH, XPATH_BOTAO_CHAMAR)))
            botao.click()
            verificar_botao_explicacao()
            if falhas:
                log_msg(f"✅ Monitoramento normalizado depois de {falhas} falha(s).")
            falhas = 0
            registrar_batimento(ok=True)
            _atualizar_status("ok", "Conectado")
        except Exception as e:
            if _erro_chrome_morto(e):
                return f"Chrome parou de responder ({type(e).__name__})"
            falhas += 1
            estado, detalhe = diagnosticar_pagina()
            motivo = tratar_falha(estado, detalhe, falhas, url_lista)
            if motivo:
                return motivo
            if estado == "login":
                falhas = 0
                ultimo_reload = time.time()
            _dormir(5)
            continue

        # Manutenção preventiva, só sem alarme tocando
        if not alarme_ativo:
            minutos = cfg["watchdog_recarregar_pagina_minutos"]
            if minutos and time.time() - ultimo_reload > minutos * 60:
                log_msg(f"🧽 Recarga preventiva da Lista de Espera (a cada {minutos} min).")
                try:
                    abrir_lista_espera(url_lista)
                except Exception as e:
                    if _erro_chrome_morto(e):
                        return f"Chrome parou na recarga preventiva ({type(e).__name__})"
                ultimo_reload = time.time()
            horas = cfg["watchdog_reiniciar_chrome_horas"]
            if horas and time.time() - inicio_chrome > horas * 3600:
                return f"reinício preventivo do Chrome (a cada {horas} h)"

        _dormir(cfg["intervalo_busca_segundos"])
    return None


def diagnosticar_pagina():
    """Olha a página com JavaScript e devolve (estado, detalhe).

    Estados: chrome_morto, travada, erro_rede, login, branca, outra_pagina,
    carregando, ok_com_botao, ok_sem_botao.
    """
    try:
        info = driver.execute_script(
            "var b=document.body;"
            "return {u: location.href, r: document.readyState,"
            " t: b ? (b.innerText || '').length : 0,"
            " btn: !!document.querySelector('button.callTicketBtn'),"
            " senha: !!document.querySelector(\"input[type='password']\")};") or {}
    except Exception as e:
        if _erro_chrome_morto(e):
            return "chrome_morto", type(e).__name__
        return "travada", f"o JavaScript da página não respondeu ({type(e).__name__})"

    url = str(info.get("u") or "").lower()
    if url.startswith("chrome-error://") or "neterror" in url:
        return "erro_rede", "o Chrome mostrou página de erro de rede"
    if "p=login" in url or "/sso" in url or (info.get("senha") and "listaespera" not in url):
        return "login", "a sessão caiu e voltou para a tela de login"
    if info.get("r") != "complete":
        return "carregando", f"readyState={info.get('r')}"
    if int(info.get("t") or 0) < 30:
        return "branca", "página em branco"
    if "listaespera" not in url:
        return "outra_pagina", f"saiu da Lista de Espera ({url[:80]})"
    if info.get("btn"):
        return "ok_com_botao", "página OK (botão presente)"
    return "ok_sem_botao", "página OK, mas sem o botão 'Chamar senha'"


def tratar_falha(estado, detalhe, falhas, url_lista):
    """Decide a recuperação. Devolve None (seguir) ou o motivo para reabrir o Chrome."""
    global _ULTIMO_RELOAD_SEM_BOTAO
    if estado == "chrome_morto":
        return f"Chrome morto ({detalhe})"

    if estado in ("ok_com_botao", "carregando") and falhas < 3:
        registrar_batimento(ok=False)  # falha passageira (modal na frente, clique interceptado)
        return None

    if estado == "ok_sem_botao":
        # a página responde e está na Lista de Espera: conta como viva
        registrar_batimento(ok=True)
        if time.time() - _ULTIMO_RELOAD_SEM_BOTAO > 300:
            _ULTIMO_RELOAD_SEM_BOTAO = time.time()
            log_msg(f"🔄 [N1] {detalhe} — recarregando a página (no máximo a cada 5 min).")
            _recarregar_seguro(url_lista)
        return None

    if estado in ("erro_rede", "branca", "travada") and not internet_ok():
        aguardar_internet()
        _recarregar_seguro(url_lista)
        return None

    if estado == "login":
        log_msg(f"🔐 [N1] {detalhe}. Entrando de novo...")
        _definir_etapa("login")
        _atualizar_status("recuperando", "Sessão caiu — entrando de novo...")
        if not fazer_login():
            return "a sessão caiu e o novo login não foi concluído"
        _recarregar_seguro(url_lista)
        _definir_etapa("monitorando")
        return None

    if falhas <= 4:
        log_msg(f"🔄 [N1] Página com problema ({estado}: {detalhe}) — recarregando (falha {falhas}/4).")
        _atualizar_status("recuperando", "Recarregando a página...")
        _recarregar_seguro(url_lista)
        return None

    return f"{falhas} falhas seguidas ({estado}: {detalhe})"


def _recarregar_seguro(url_lista):
    try:
        abrir_lista_espera(url_lista)
    except Exception as e:
        log_msg(f"⚠️ Recarga falhou: {type(e).__name__}")


# ==============================================================================
# 🩺 WATCHDOG: batimento, internet, suspensão do PC e reinício em níveis
# ==============================================================================
ERROS_CHROME_MORTO = (
    "invalid session id", "no such window", "chrome not reachable", "disconnected",
    "target window already closed", "session deleted", "connection refused",
    "max retries exceeded", "failed to establish a new connection", "web view not found",
    "tab crashed", "no such execution context", "unable to receive message from renderer",
)


def _erro_chrome_morto(erro):
    if isinstance(erro, (InvalidSessionIdException, NoSuchWindowException)):
        return True
    texto = str(erro).lower()
    return any(trecho in texto for trecho in ERROS_CHROME_MORTO)


def _definir_etapa(etapa):
    global _ETAPA, _ULTIMO_PROGRESSO
    _ETAPA = etapa
    _ULTIMO_PROGRESSO = time.time()


def registrar_batimento(ok=True):
    """Batimento: o ciclo andou. ok=True quando a página respondeu certo."""
    global _ULTIMO_OK, _ULTIMO_PROGRESSO, _PRIMEIRO_OK
    agora = time.time()
    _ULTIMO_PROGRESSO = agora
    if ok:
        _ULTIMO_OK = agora
        if not _PRIMEIRO_OK:
            _PRIMEIRO_OK = True
            confirmar_atualizacao("primeiro ciclo de monitoramento OK")


def _dormir(segundos, progresso=False):
    """sleep interrompível; com progresso=True o watchdog sabe que é espera de propósito."""
    fim = time.time() + max(0, segundos)
    while not _ENCERRANDO and time.time() < fim:
        if progresso:
            registrar_batimento(ok=False)
        time.sleep(min(1.0, max(0.05, fim - time.time())))


def internet_ok(timeout=5):
    try:
        hosts = carregar_filtros().get("hosts_verificacao_internet") or []
    except Exception:
        hosts = []
    for entrada in (hosts or FILTROS_PADRAO["hosts_verificacao_internet"]):
        host, _sep, porta = str(entrada).strip().rpartition(":") if ":" in str(entrada) else (str(entrada).strip(), "", "443")
        try:
            socket.create_connection((host, int(porta or 443)), timeout=timeout).close()
            return True
        except (OSError, ValueError):
            continue
    return False


def aguardar_internet():
    """Espera a internet voltar sem escalar a recuperação (equivale ao online/offline)."""
    global _OFFLINE_DESDE
    if internet_ok():
        return
    _OFFLINE_DESDE = time.time()
    etapa_anterior = _ETAPA
    _definir_etapa("offline")
    log_msg("📴 Sem internet. O monitor espera a conexão voltar (sem reiniciar nada).")
    _atualizar_status("offline", "Sem internet — aguardando")
    while not _ENCERRANDO and not internet_ok():
        _dormir(10, progresso=True)
    minutos = int((time.time() - _OFFLINE_DESDE) / 60)
    _OFFLINE_DESDE = 0.0
    log_msg(f"📶 Internet de volta (fora por ~{minutos} min). Retomando.")
    _definir_etapa(etapa_anterior if etapa_anterior != "offline" else "monitorando")


def _proxima_espera_reinicio():
    """Backoff exponencial dos reinícios do Chrome + teto por hora."""
    cfg = carregar_filtros()
    agora = time.time()
    _REINICIOS_CHROME[:] = [t for t in _REINICIOS_CHROME if agora - t < 3600]
    quantidade = len(_REINICIOS_CHROME)
    _REINICIOS_CHROME.append(agora)
    if quantidade >= cfg["watchdog_max_reinicios_por_hora"]:
        log_msg(f"🚧 {quantidade} reinícios do Chrome na última hora — esperando 15 min antes de tentar de novo.")
        _atualizar_status("falha", "Muitas falhas — nova tentativa em 15 min")
        _avisar_bandeja("O monitor de senhas está com falhas repetidas. Nova tentativa em 15 minutos.")
        return 900
    if quantidade == 0:
        return 5
    return min(15 * (2 ** (quantidade - 1)), 600)


def _ler_estado_watchdog():
    try:
        with open(ARQ_ESTADO_WATCHDOG, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def _pode_reiniciar_processo(maximo_por_hora=4):
    """Limite persistente (sobrevive ao reinício) para não entrar em loop."""
    dados = _ler_estado_watchdog()
    agora = time.time()
    lista = [t for t in (dados.get("reinicios_processo") or []) if agora - float(t) < 3600]
    if len(lista) >= maximo_por_hora:
        return False
    lista.append(agora)
    dados["reinicios_processo"] = lista
    try:
        os.makedirs(PASTA_LOGS, exist_ok=True)
        with open(ARQ_ESTADO_WATCHDOG, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo)
    except Exception:
        pass
    return True


def reiniciar_processo(motivo, ignorar_limite=False):
    """Nível 3: reinicia o programa inteiro (fecha o Chrome, sobe outro processo)."""
    global _ENCERRANDO
    if not ignorar_limite and not _pode_reiniciar_processo():
        log_msg(f"🚧 Reinício do programa pedido ({motivo}), mas já foram 4 na última hora. Segurando.")
        _atualizar_status("falha", "Falhas repetidas — aguardando")
        return False
    log_msg(f"🔁 [N3] Reiniciando o programa inteiro. Motivo: {motivo}")
    _ENCERRANDO = True
    # Antes (2.0.0) _limpeza_para_sair() terminava com os._exit(0): o processo
    # morria com código 0 ANTES de relançar e o supervisor entendia "usuário
    # encerrou" e saía também. Agora a limpeza só limpa; quem sai é o
    # relancar_processo_agora() (código 75 sob o supervisor).
    _limpeza_para_sair()
    relancar_processo_agora()
    return True


ARQ_PEDIDO_REINICIO = os.path.join(PASTA_LOGS, "reiniciar.pedido")
ARQ_PEDIDO_ENCERRAR = os.path.join(PASTA_LOGS, "encerrar.pedido")


def _checar_pedidos_externos():
    """Arquivos de pedido em logs/: reiniciar.pedido (reinicia o programa) e
    encerrar.pedido (encerra). Útil para manutenção remota/atalho .bat."""
    for caminho, acao in ((ARQ_PEDIDO_ENCERRAR, "encerrar"), (ARQ_PEDIDO_REINICIO, "reiniciar")):
        if not os.path.isfile(caminho):
            continue
        try:
            os.remove(caminho)
        except Exception:
            continue
        if acao == "encerrar":
            log_msg("🛑 Pedido externo de encerramento (logs/encerrar.pedido).")
            encerrar_sistema()
        else:
            reiniciar_processo("pedido externo (logs/reiniciar.pedido)", ignorar_limite=True)
        return


def thread_watchdog():
    """Olha o batimento a cada 15 s e age quando o monitor para de andar."""
    global _ULTIMO_PROGRESSO, _PEDIDO_RECARGA
    ultimo_giro = time.time()
    while not _ENCERRANDO:
        time.sleep(WATCHDOG_PERIODO_SEGUNDOS)
        agora = time.time()
        try:
            _checar_pedidos_externos()
        except Exception as e:
            log_msg(f"⚠️ Watchdog: falha ao tratar pedido externo ({type(e).__name__}: {e})")
        try:
            # PC suspenso/hibernado: o relógio "pula". Dá uma folga e recarrega a página.
            if agora - ultimo_giro > max(90, WATCHDOG_PERIODO_SEGUNDOS * 6):
                log_msg(f"💤 O PC ficou parado/suspenso por ~{int((agora - ultimo_giro) / 60)} min. "
                        "Conferindo a página...")
                _ULTIMO_PROGRESSO = agora
                if _ETAPA == "monitorando":
                    _PEDIDO_RECARGA = True
            ultimo_giro = agora
            _passo_watchdog(agora)
            _checar_confirmacao_por_tempo(agora)
        except Exception as e:
            log_msg(f"⚠️ Watchdog: erro interno ignorado ({type(e).__name__}: {e})")


def _passo_watchdog(agora):
    global _PEDIDO_REINICIO_CHROME, _HORA_PEDIDO_CHROME
    cfg = carregar_filtros()

    # A thread do monitor morreu sem ninguém pedir? Religa.
    if (_MONITOR_DESEJADO and not _SELENIUM_RODANDO and not _CRED_RECUSADAS
            and _ETAPA != "aguardando_login" and not credenciais_incompletas()):
        log_msg("🩺 Watchdog: o monitoramento tinha parado — religando.")
        iniciar_selenium()
        return

    if not _SELENIUM_RODANDO or _ETAPA in ("offline", "parado", "aguardando_login"):
        return

    limite = cfg["watchdog_limite_sem_batimento_segundos"]
    if _ETAPA != "monitorando":
        limite = max(limite, 600)  # abrir o Chrome + login (com bypass) leva mais tempo
    parado_ha = agora - _ULTIMO_PROGRESSO
    if parado_ha < limite:
        if _ETAPA == "monitorando" and _ULTIMO_OK and agora - _ULTIMO_OK > limite:
            _atualizar_status("recuperando", "Sem resposta da página — recuperando")
        return

    if not internet_ok():
        _atualizar_status("offline", "Sem internet — aguardando")
        return

    if not _PEDIDO_REINICIO_CHROME:
        _PEDIDO_REINICIO_CHROME = f"sem batimento há {int(parado_ha)}s na etapa '{_ETAPA}'"
        _HORA_PEDIDO_CHROME = agora
        log_msg(f"🩺 [N2] Watchdog: {_PEDIDO_REINICIO_CHROME}. Fechando o Chrome para destravar e reabrir.")
        _atualizar_status("recuperando", "Travou — reabrindo o navegador")
        # Mata o Chrome do perfil: qualquer chamada do Selenium pendurada estoura e o loop reabre.
        try:
            _encerrar_instancias_antigas(_pasta_perfil_chrome())
        except Exception:
            pass
        return

    if agora - _HORA_PEDIDO_CHROME > 300:
        # Nem fechando o Chrome a thread voltou: reinicia o programa.
        if reiniciar_processo(f"monitor travado mesmo após fechar o Chrome ({_PEDIDO_REINICIO_CHROME})"):
            return
        _HORA_PEDIDO_CHROME = agora


# --- Indicador discreto de estado (bandeja + painel) ---------------------------
CORES_STATUS = {
    "iniciando": "#3b82f6",
    "ok": "#22c55e",
    "recuperando": "#f59e0b",
    "offline": "#9ca3af",
    "falha": "#ef4444",
}


def _atualizar_status(nivel, texto):
    global _STATUS
    if _STATUS == (nivel, texto):
        return
    _STATUS = (nivel, texto)
    try:
        if tray_icon is not None:
            tray_icon.icon = criar_icone_imagem(CORES_STATUS.get(nivel, "blue"))
            tray_icon.title = f"Monitor Feegow v{VERSAO} — {texto}"[:120]
    except Exception:
        pass


def _avisar_bandeja(mensagem):
    try:
        if tray_icon is not None and getattr(tray_icon, "HAS_NOTIFICATION", False):
            tray_icon.notify(mensagem, "Monitor de Senha")
    except Exception:
        pass


def _texto_status():
    nivel, texto = _STATUS
    if _ULTIMO_OK:
        segundos = int(time.time() - _ULTIMO_OK)
        ultimo = f"último OK há {segundos}s" if segundos < 120 else f"último OK há {segundos // 60} min"
    else:
        ultimo = "aguardando o primeiro ciclo"
    return nivel, f"● {texto}  |  {ultimo}  |  v{VERSAO}"


# ==============================================================================
# ⬆️ ATUALIZAÇÃO AUTOMÁTICA PELO GITHUB (versao.json)
# ==============================================================================
def _url_raw(arquivo, canal=None):
    canal = canal or carregar_filtros().get("atualizacao_canal") or CANAL_ATUALIZACAO_PADRAO
    return URL_RAW.format(repo=REPO_GITHUB, canal=canal, arquivo=arquivo) + f"?t={int(time.time())}"


def _baixar(url, timeout=60, limite=80_000_000):
    requisicao = urllib.request.Request(url, headers={
        "User-Agent": f"MonitorFeegow/{VERSAO}",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
        if getattr(resposta, "status", 200) != 200:
            raise RuntimeError(f"HTTP {resposta.status}")
        bruto = resposta.read(limite + 1)
    if len(bruto) > limite:
        raise RuntimeError("arquivo grande demais")
    return bruto


def _versao_tupla(texto):
    numeros = re.findall(r"\d+", str(texto or ""))[:4]
    return tuple(int(n) for n in numeros) if numeros else (0,)


def consultar_versao_remota():
    dados = json.loads(_baixar(_url_raw(NOME_ARQUIVO_VERSAO), timeout=30).decode("utf-8-sig"))
    if not isinstance(dados, dict) or not dados.get("versao"):
        raise ValueError("versao.json sem o campo 'versao'")
    return dados


def validar_script_baixado(bruto, versao_esperada, sha256=""):
    """Só aceita o arquivo se for MESMO o script, inteiro e sem erro de sintaxe."""
    if len(bruto) < TAMANHO_MINIMO_SCRIPT:
        raise ValueError(f"arquivo pequeno demais ({len(bruto)} bytes)")
    if sha256 and hashlib.sha256(bruto).hexdigest().lower() != sha256.strip().lower():
        raise ValueError("sha256 não confere com o versao.json")
    texto = bruto.decode("utf-8")
    if not texto.lstrip("﻿").startswith(MARCADOR_SCRIPT):
        raise ValueError("cabeçalho inesperado (não é o SCRIPT_EXPLICA.py)")
    achado = re.search(r'^VERSAO\s*=\s*"([^"]+)"', texto, re.M)
    if not achado or achado.group(1) != versao_esperada:
        raise ValueError(f"VERSAO do arquivo ({achado.group(1) if achado else '?'}) "
                         f"≠ versao.json ({versao_esperada})")
    compile(texto, "SCRIPT_EXPLICA.py", "exec")
    return True


def validar_exe_baixado(bruto, sha256):
    if not sha256:
        raise ValueError("versao.json sem sha256_exe: o .exe não é atualizado sem conferência")
    if len(bruto) < TAMANHO_MINIMO_EXE or bruto[:2] != b"MZ":
        raise ValueError("não parece um executável do Windows")
    if hashlib.sha256(bruto).hexdigest().lower() != sha256.strip().lower():
        raise ValueError("sha256 do .exe não confere com o versao.json")
    return True


def _em_clone_git():
    return not _rodando_compilado() and os.path.isdir(os.path.join(PASTA_SCRIPT, ".git"))


def momento_seguro_para_atualizar():
    """Sem alarme tocando, sem recuperação em andamento e com a página respondendo."""
    if alarme_ativo or _PEDIDO_REINICIO_CHROME:
        return False
    if _ETAPA in ("aguardando_login", "parado"):
        return True
    return _ETAPA == "monitorando" and _ULTIMO_OK and (time.time() - _ULTIMO_OK) < 120


def verificar_atualizacao(manual=False):
    """Consulta o versao.json e, se houver versão nova, atualiza com segurança."""
    global _ATUALIZACAO_ADIADA, _AVISOU_GIT
    cfg = carregar_filtros()
    if not manual and not cfg["auto_atualizar"]:
        return "desligada"
    if _em_clone_git():
        if manual or not _AVISOU_GIT:
            _AVISOU_GIT = True
            log_msg("ℹ️ Esta pasta é um clone do git: a atualização automática não mexe nela (use git pull).")
        return "git"
    estado = _ler_estado_atualizacao()
    if estado.get("status") == "em_teste" and estado.get("para") == VERSAO:
        return "em_teste"

    remoto = consultar_versao_remota()
    nova = str(remoto.get("versao")).strip()
    if _versao_tupla(nova) <= _versao_tupla(VERSAO):
        if manual:
            log_msg(f"✅ Já está na versão mais nova ({VERSAO}).")
        return "atual"
    if nova in (estado.get("bloqueadas") or []):
        if manual:
            log_msg(f"⛔ A versão {nova} já falhou aqui e foi revertida — ignorando.")
        return "bloqueada"
    if not momento_seguro_para_atualizar():
        if not _ATUALIZACAO_ADIADA:
            log_msg(f"⏸️ Versão {nova} disponível — aguardando um momento sem chamada em andamento.")
        _ATUALIZACAO_ADIADA = True
        return "adiada"
    _ATUALIZACAO_ADIADA = False
    aplicar_atualizacao(remoto, estado)
    return "aplicada"


def aplicar_atualizacao(remoto, estado_anterior=None):
    nova = str(remoto.get("versao")).strip()
    alvo = _caminho_programa_atual()
    log_msg(f"⬇️ Baixando a versão {nova} (atual {VERSAO})...")
    if alvo.lower().endswith(".exe"):
        url = remoto.get("url_exe") or ""
        if not url:
            raise ValueError("versao.json sem url_exe")
        bruto = _baixar(url, timeout=300)
        validar_exe_baixado(bruto, remoto.get("sha256_exe") or "")
    else:
        arquivo = remoto.get("arquivo") or "SCRIPT_EXPLICA.py"
        bruto = _baixar(_url_raw(arquivo), timeout=60)
        validar_script_baixado(bruto, nova, remoto.get("sha256") or "")

    novo = alvo + ".novo"
    backup = alvo + ".anterior"
    with open(novo, "wb") as arquivo_novo:
        arquivo_novo.write(bruto)
    if os.path.exists(backup):
        os.remove(backup)
    os.replace(alvo, backup)          # o .exe em uso pode ser renomeado
    try:
        os.replace(novo, alvo)
    except Exception:
        os.replace(backup, alvo)
        raise

    _gravar_estado_atualizacao({
        "status": "em_teste",
        "de": VERSAO,
        "para": nova,
        "arquivo": alvo,
        "backup": backup,
        "inicios": 0,
        "instalada_em": datetime.datetime.now().isoformat(timespec="seconds"),
        "notas": str(remoto.get("notas") or "")[:500],
        "bloqueadas": (estado_anterior or {}).get("bloqueadas") or [],
    })
    log_msg(f"✅ Versão {nova} instalada (backup em {os.path.basename(backup)}). Reiniciando...")
    reiniciar_processo(f"atualização {VERSAO} -> {nova}", ignorar_limite=True)


def confirmar_atualizacao(motivo):
    estado = _ler_estado_atualizacao()
    if estado.get("status") != "em_teste" or estado.get("para") != VERSAO:
        return
    estado["status"] = "confirmada"
    estado["confirmada_em"] = datetime.datetime.now().isoformat(timespec="seconds")
    estado["motivo"] = motivo
    _gravar_estado_atualizacao(estado)
    log_msg(f"✅ Versão {VERSAO} confirmada ({motivo}). A anterior fica guardada como .anterior.")


def _checar_confirmacao_por_tempo(agora):
    """Confirma ou reverte a versão em teste quando o 1º ciclo OK não vem."""
    if _PRIMEIRO_OK:
        return
    estado = _ler_estado_atualizacao()
    if estado.get("status") != "em_teste" or estado.get("para") != VERSAO:
        return
    vivo_ha = agora - _INICIO_PROCESSO
    sem_como_testar = _CRED_RECUSADAS or credenciais_incompletas() or _ETAPA == "offline"
    if vivo_ha > 600 and sem_como_testar:
        confirmar_atualizacao("programa estável há 10 min (sem login/internet para testar o Feegow)")
    elif vivo_ha > 1200 and not sem_como_testar and internet_ok():
        if _restaurar_versao_anterior(estado, "a versão nova não monitorou com sucesso em 20 min"):
            log_msg(f"↩️ A versão {VERSAO} não conseguiu monitorar em 20 min — voltando para {estado.get('de')}.")
            reiniciar_processo("rollback da atualização", ignorar_limite=True)


def thread_atualizacao():
    """Consulta o GitHub de tempos em tempos (padrão: 60 min)."""
    _dormir(90)  # deixa o monitor subir primeiro
    while not _ENCERRANDO:
        try:
            verificar_atualizacao()
        except Exception as e:
            log_msg(f"⚠️ Atualização: não consegui consultar/aplicar ({type(e).__name__}: {e}). "
                    "Tento de novo mais tarde.")
        if _ATUALIZACAO_ADIADA:
            _dormir(120)
        else:
            _dormir(carregar_filtros()["atualizacao_intervalo_minutos"] * 60)


def procurar_atualizacao_agora():
    """Botão da janela ⚙️ (roda em thread separada)."""
    def _tarefa():
        try:
            resultado = verificar_atualizacao(manual=True)
            if resultado == "adiada":
                log_msg("⏸️ A atualização será aplicada assim que não houver chamada em andamento.")
        except Exception as e:
            log_msg(f"⚠️ Atualização manual falhou: {type(e).__name__}: {e}")
    threading.Thread(target=_tarefa, daemon=True).start()


# ==============================================================================
# SYSTEM TRAY (BANDEJA DO WINDOWS E ENCERRAMENTO LIMPO)
# ==============================================================================
def criar_icone_imagem(cor):
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.ellipse((16, 16, 48, 48), fill=cor)
    dc.rectangle((28, 40, 36, 56), fill=cor)
    return image

_ULTIMO_TOGGLE = 0.0


def _alternar_janelas():
    """Alterna mostrar/ocultar — SEMPRE agendado para a thread do Tk."""
    global _ULTIMO_TOGGLE
    agora = time.time()
    if (agora - _ULTIMO_TOGGLE) < 0.4:
        return  # clique duplo acidental (o pystray às vezes dispara 2x)
    _ULTIMO_TOGGLE = agora
    if _janela_visivel():
        gerenciar_janelas("ocultar")
    else:
        gerenciar_janelas("mostrar")


def on_tray_click(icon, item):
    """pystray roda em OUTRA thread: mexer no Tk direto daqui travava o app."""
    try:
        root.after(0, _alternar_janelas)
    except Exception:
        pass


def on_tray_mostrar(icon, item):
    try:
        root.after(0, lambda: gerenciar_janelas("mostrar"))
    except Exception:
        pass


def on_tray_ocultar(icon, item):
    try:
        root.after(0, lambda: gerenciar_janelas("ocultar"))
    except Exception:
        pass

def encerrar_sistema():
    """Fecha o Chrome, apaga o áudio temporário e sai limpo.

    Usado pelo menu da bandeja e pelo Ctrl+C no console (antes o Ctrl+C
    estourava um traceback de KeyboardInterrupt e deixava o Chrome aberto).
    """
    global _ENCERRANDO
    _ENCERRANDO = True
    _limpeza_para_sair()
    _liberar_instancia_unica()
    os._exit(0)   # código 0 = "o usuário encerrou": o supervisor para de relançar


def _limpeza_para_sair():
    """Fecha bandeja e Chrome e deixa o perfil limpo (usado ao sair e ao reiniciar).

    NÃO encerra o processo: quem sai é encerrar_sistema() (código 0) ou
    relancar_processo_agora() (código 75, reinício).
    """
    try:
        if tray_icon is not None:
            tray_icon.stop()
    except Exception:
        pass

    try:
        if driver is not None:
            # driver.quit() pode travar se a thread do Selenium estiver no meio de uma chamada
            fechador = threading.Thread(target=lambda: _ignorar_erro(driver.quit), daemon=True)
            fechador.start()
            fechador.join(timeout=5)
    except Exception:
        pass

    try:
        # deixa o perfil pronto para o próximo início (sem "Restaurar páginas?")
        _limpar_sessao_antiga(_pasta_perfil_chrome())
        _marcar_perfil_limpo(_pasta_perfil_chrome())
    except Exception:
        pass

    try:
        _encerrar_instancias_antigas(_pasta_perfil_chrome())
    except Exception:
        pass

    try:
        if os.path.exists(ARQUIVO_OFICIAL_ALERTA):
            os.remove(ARQUIVO_OFICIAL_ALERTA)
    except Exception:
        pass


def on_tray_exit(icon, item):
    log_msg("Desligando sistema. Limpando memória e rastros...")
    encerrar_sistema()

def iniciar_tray():
    global tray_icon
    menu = pystray.Menu(
        item('Mostrar / Ocultar Painel', on_tray_click, default=True),
        item('Mostrar Painel', on_tray_mostrar),
        item('Ocultar Painel', on_tray_ocultar),
        item('Procurar atualização', lambda icon, it: procurar_atualizacao_agora()),
        item('Reiniciar monitor', lambda icon, it: threading.Thread(
            target=reiniciar_processo, args=("pedido do usuário (bandeja)",),
            kwargs={"ignorar_limite": True}, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        item('Encerrar Sistema', on_tray_exit),
    )
    nivel, texto = _STATUS
    tray_icon = pystray.Icon("Monitor_Feegow", criar_icone_imagem(CORES_STATUS.get(nivel, "blue")),
                             f"Monitor Feegow v{VERSAO} — {texto}", menu)
    tray_icon.run()

# ==============================================================================
# ⚙️ JANELA DE CONFIGURAÇÕES (senhas que alertam/ignoram, login e alertas)
# ==============================================================================
def _sincronizar_termos_filtro(dados):
    """Garante termo de busca (XPath) para toda senha das listas.

    Sem isso, uma senha adicionada pela janela nunca seria encontrada no modal,
    porque o XPath só procura pelos termos_gatilho.
    """
    termos = [str(t).strip() for t in (dados.get("termos_gatilho") or []) if str(t).strip()]
    chaves = [normalizar_texto(t) for t in termos]
    entradas = list(dados.get("whitelist") or []) + list(dados.get("blacklist") or [])
    for entrada in entradas:
        texto = str(entrada).strip()
        if not texto:
            continue
        norm = normalizar_texto(texto)
        if not any(chave and chave in norm for chave in chaves):
            termos.append(texto.upper())
            chaves.append(norm)
    dados["termos_gatilho"] = termos
    return dados


def abrir_configuracoes(aba="senhas"):
    """Abre a janela de configuração. Salva direto nos JSONs (mesmo formato)."""
    from tkinter import ttk, messagebox

    global _JANELA_CONFIG, _ABAS_CONFIG

    def _selecionar():
        indice = {"senhas": 0, "login": 1, "alertas": 2, "sistema": 3}.get(str(aba).lower(), 0)
        try:
            if _ABAS_CONFIG is not None:
                _ABAS_CONFIG.select(indice)
        except Exception:
            pass

    try:
        if _JANELA_CONFIG is not None and _JANELA_CONFIG.winfo_exists():
            _JANELA_CONFIG.lift()
            _JANELA_CONFIG.focus_force()
            _selecionar()
            return
    except Exception:
        pass

    def ler_json(caminho):
        try:
            with open(caminho, "r", encoding="utf-8-sig") as arquivo:
                dados = json.load(arquivo)
            return dados if isinstance(dados, dict) else {}
        except Exception:
            return {}

    # garante que os arquivos existem antes de abrir a tela
    carregar_credenciais()
    carregar_filtros(forcar=True)
    filtros = ler_json(ARQ_FILTROS)
    credenciais = ler_json(ARQ_CREDENCIAIS)

    janela = tk.Toplevel(root)
    janela.title("⚙️ Configurações")
    janela.configure(bg="#1e1e1e")
    janela.geometry("820x600")
    janela.attributes("-topmost", True)
    _JANELA_CONFIG = janela

    abas = ttk.Notebook(janela)
    abas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    _ABAS_CONFIG = abas

    # ---------------- aba SENHAS ----------------
    aba_senhas = tk.Frame(abas, bg="#1e1e1e")
    abas.add(aba_senhas, text="  Senhas  ")

    tk.Label(
        aba_senhas,
        text="Whitelist = senhas que ALERTAM  •  Blacklist = senhas IGNORADAS\n"
             "Digite a senha como aparece na tela do Feegow e clique em Adicionar.",
        bg="#1e1e1e", fg="#9ca3af", font=("Consolas", 9), justify=tk.LEFT,
    ).pack(anchor="w", padx=8, pady=(8, 4))

    def aplicar_padrao(chave_padrao):
        """Carrega um padrão pronto: substitui as listas, salva e recarrega."""
        padrao = PADROES_SENHA.get(chave_padrao)
        if not padrao:
            return
        try:
            atual = ler_json(ARQ_FILTROS)
            pasta = os.path.dirname(ARQ_FILTROS)
            _gravar_json(os.path.join(pasta, "filtros.backup.json"), atual)

            novo = dict(atual)
            novo["whitelist"] = list(padrao["whitelist"])
            novo["blacklist"] = list(padrao["blacklist"])
            novo["comparacao"] = padrao.get("comparacao", "contem")
            novo["termos_gatilho"] = list(padrao["termos_gatilho"])
            _sincronizar_termos_filtro(novo)
            _gravar_json(ARQ_FILTROS, novo)
            carregar_filtros(forcar=True)

            lista_alertar.delete(0, tk.END)
            for item in novo["whitelist"]:
                lista_alertar.insert(tk.END, item)
            lista_ignorar.delete(0, tk.END)
            for item in novo["blacklist"]:
                lista_ignorar.insert(tk.END, item)

            log_msg(
                f"⚡ Padrão '{padrao['rotulo']}' aplicado: "
                f"{len(novo['whitelist'])} alertar / {len(novo['blacklist'])} ignorar "
                f"(comparação '{novo['comparacao']}')."
            )
            messagebox.showinfo(
                "Padrão aplicado",
                f"Padrão {padrao['rotulo']} carregado e salvo.\n\n"
                f"✅ ALERTAR: {len(novo['whitelist'])} senha(s)\n"
                f"🚫 IGNORAR: {len(novo['blacklist'])}\n"
                f"Comparação: {novo['comparacao']}\n\n"
                "O filtro anterior ficou salvo em filtros.backup.json.",
                parent=janela,
            )
        except Exception as e:
            messagebox.showerror("Erro ao aplicar o padrão", str(e), parent=janela)

    frame_padroes = tk.Frame(aba_senhas, bg="#1e1e1e")
    frame_padroes.pack(fill=tk.X, padx=8, pady=(0, 6))
    tk.Label(frame_padroes, text="⚡ Padrões prontos:", bg="#1e1e1e", fg="#9ca3af",
             font=("Consolas", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
    for chave_padrao, dados_padrao in PADROES_SENHA.items():
        tk.Button(
            frame_padroes, text=dados_padrao["rotulo"],
            bg="#1f3a5f", fg="white", relief=tk.FLAT, padx=10, pady=4,
            cursor="hand2", font=("Consolas", 9, "bold"),
            activebackground="#2b4f7d", activeforeground="white",
            command=lambda c=chave_padrao: aplicar_padrao(c),
        ).pack(side=tk.LEFT, padx=3)

    corpo = tk.Frame(aba_senhas, bg="#1e1e1e")
    corpo.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
    corpo.columnconfigure(0, weight=1)
    corpo.columnconfigure(1, weight=1)
    corpo.rowconfigure(0, weight=1)

    def montar_coluna(pai, titulo, cor, itens, coluna):
        moldura = tk.LabelFrame(pai, text=titulo, bg="#1e1e1e", fg=cor,
                                font=("Consolas", 10, "bold"))
        moldura.grid(row=0, column=coluna, sticky="nsew", padx=5)

        lista = tk.Listbox(moldura, bg="#111111", fg="#eeeeee", selectmode=tk.EXTENDED,
                           height=14, activestyle="none")
        lista.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        for item in itens:
            lista.insert(tk.END, item)

        def adicionar(_evento=None):
            texto = entrada.get().strip()
            if not texto:
                return
            existentes = [lista.get(i).upper() for i in range(lista.size())]
            if texto.upper() in existentes:
                entrada.delete(0, tk.END)
                return
            lista.insert(tk.END, texto.upper())
            entrada.delete(0, tk.END)

        def excluir(_evento=None):
            for indice in reversed(lista.curselection()):
                lista.delete(indice)

        linha = tk.Frame(moldura, bg="#1e1e1e")
        linha.pack(fill=tk.X, padx=6, pady=(0, 4))
        entrada = tk.Entry(linha, bg="#111111", fg="#eeeeee", insertbackground="#eeeeee")
        entrada.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entrada.bind("<Return>", adicionar)
        tk.Button(linha, text="Adicionar", command=adicionar, bg="#2d6a4f", fg="white",
                  relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=(4, 0))

        tk.Button(moldura, text="Excluir selecionado", command=excluir, bg="#7f1d1d",
                  fg="white", relief=tk.FLAT).pack(fill=tk.X, padx=6, pady=(0, 6))
        return lista

    lista_alertar = montar_coluna(
        corpo, "✅ ALERTAR (whitelist)", "#4ade80",
        filtros.get("whitelist") or [], 0,
    )
    lista_ignorar = montar_coluna(
        corpo, "🚫 IGNORAR (blacklist)", "#f87171",
        filtros.get("blacklist") or [], 1,
    )

    # ---------------- aba LOGIN ----------------
    aba_login = tk.Frame(abas, bg="#1e1e1e")
    abas.add(aba_login, text="  Login  ")

    tk.Label(aba_login, text="Credenciais do Feegow (salvas em credenciais.json)",
             bg="#1e1e1e", fg="#9ca3af", font=("Consolas", 9)).pack(anchor="w", padx=10, pady=(10, 6))

    form = tk.Frame(aba_login, bg="#1e1e1e")
    form.pack(anchor="w", padx=10)

    def rotulo(texto, linha):
        tk.Label(form, text=texto, bg="#1e1e1e", fg="#e5e7eb",
                 font=("Consolas", 10)).grid(row=linha, column=0, sticky="w", pady=6)

    rotulo("E-mail / usuário:", 0)
    entrada_usuario = tk.Entry(form, width=45, bg="#111111", fg="#eeeeee",
                               insertbackground="#eeeeee")
    entrada_usuario.insert(0, str(credenciais.get("usuario") or ""))
    entrada_usuario.grid(row=0, column=1, padx=8, pady=6, sticky="w")

    rotulo("Senha:", 1)
    entrada_senha = tk.Entry(form, width=45, show="•", bg="#111111", fg="#eeeeee",
                             insertbackground="#eeeeee")
    entrada_senha.insert(0, str(credenciais.get("senha") or ""))
    entrada_senha.grid(row=1, column=1, padx=8, pady=6, sticky="w")

    mostrar = tk.BooleanVar(value=False)

    def alternar_senha():
        entrada_senha.configure(show="" if mostrar.get() else "•")

    tk.Checkbutton(form, text="mostrar senha", variable=mostrar, command=alternar_senha,
                   bg="#1e1e1e", fg="#9ca3af", selectcolor="#111111",
                   activebackground="#1e1e1e", activeforeground="#e5e7eb",
                   font=("Consolas", 9)).grid(row=2, column=1, sticky="w", padx=8)

    rotulo("Rota de login:", 3)
    combo_rota = ttk.Combobox(form, width=12, state="readonly",
                              values=["auto", "sso", "legado"])
    combo_rota.set(str(credenciais.get("rota_login") or "auto").lower())
    combo_rota.grid(row=3, column=1, padx=8, pady=6, sticky="w")

    tk.Label(aba_login,
             text="auto = tenta o SSO (/main/sso/) e depois a rota antiga (?P=Login)\n"
                  "sso  = só a rota nova   •   legado = só a rota antiga\n\n"
                  "As alterações de login valem no próximo login (ao reiniciar o script).",
             bg="#1e1e1e", fg="#6b7280", font=("Consolas", 9), justify=tk.LEFT
             ).pack(anchor="w", padx=10, pady=(10, 0))

    # ---------------- aba ALERTAS ----------------
    aba_alertas = tk.Frame(abas, bg="#1e1e1e")
    abas.add(aba_alertas, text="  Alertas  ")
    _selecionar()

    var_tela = tk.BooleanVar(value=_para_bool(filtros.get("alerta_tela"), True))
    var_voz = tk.BooleanVar(value=_para_bool(filtros.get("alerta_voz"), True))

    for texto, var in (
        ("Mostrar a janela vermelha piscando na tela", var_tela),
        ("Tocar a voz sintetizada", var_voz),
    ):
        tk.Checkbutton(aba_alertas, text=texto, variable=var, bg="#1e1e1e", fg="#e5e7eb",
                       selectcolor="#111111", activebackground="#1e1e1e",
                       activeforeground="#e5e7eb", font=("Consolas", 10), anchor="w"
                       ).pack(anchor="w", padx=14, pady=6)

    form2 = tk.Frame(aba_alertas, bg="#1e1e1e")
    form2.pack(anchor="w", padx=14, pady=(10, 0))
    tk.Label(form2, text="Pausa após o CIENTE (min):", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=0, column=0, sticky="w", pady=4)
    entrada_pausa = tk.Entry(form2, width=8, bg="#111111", fg="#eeeeee",
                             insertbackground="#eeeeee")
    entrada_pausa.insert(0, str(filtros.get("pausa_minutos") or 15))
    entrada_pausa.grid(row=0, column=1, padx=8)

    tk.Label(form2, text="Intervalo entre buscas (s):", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=1, column=0, sticky="w", pady=4)
    entrada_intervalo = tk.Entry(form2, width=8, bg="#111111", fg="#eeeeee",
                                 insertbackground="#eeeeee")
    entrada_intervalo.insert(0, str(filtros.get("intervalo_busca_segundos") or 10))
    entrada_intervalo.grid(row=1, column=1, padx=8)

    tk.Label(form2, text="Tempo máximo do alarme (s, 0 = sem limite):", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=2, column=0, sticky="w", pady=4)
    entrada_max = tk.Entry(form2, width=8, bg="#111111", fg="#eeeeee",
                           insertbackground="#eeeeee")
    entrada_max.insert(0, str(_para_int(filtros.get("alarme_max_segundos"), 120, 0)))
    entrada_max.grid(row=2, column=1, padx=8)

    tk.Label(form2, text="Intervalo entre repetições da voz (s):", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=3, column=0, sticky="w", pady=4)
    entrada_repeticao = tk.Entry(form2, width=8, bg="#111111", fg="#eeeeee",
                                 insertbackground="#eeeeee")
    entrada_repeticao.insert(0, str(_para_int(filtros.get("intervalo_repeticao_segundos"), 15, 1)))
    entrada_repeticao.grid(row=3, column=1, padx=8)

    tk.Label(form2, text="Volume da voz (%):", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=4, column=0, sticky="w", pady=4)
    entrada_volume = tk.Entry(form2, width=8, bg="#111111", fg="#eeeeee",
                              insertbackground="#eeeeee")
    entrada_volume.insert(0, str(_para_int(filtros.get("volume_alarme"), 100, 0, 100)))
    entrada_volume.grid(row=4, column=1, padx=8)

    tk.Label(form2, text="Saída da voz:", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=5, column=0, sticky="w", pady=4)
    combo_saida = ttk.Combobox(form2, width=34, state="readonly",
                               values=["padrao", "todos"] + list(DISPOSITIVOS_AUDIO))
    combo_saida.set(str(filtros.get("saida_audio") or "padrao"))
    combo_saida.grid(row=5, column=1, padx=8, sticky="w")

    tk.Label(form2, text="Não repetir a mesma senha por (s):", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=6, column=0, sticky="w", pady=4)
    entrada_antirep = tk.Entry(form2, width=8, bg="#111111", fg="#eeeeee",
                               insertbackground="#eeeeee")
    entrada_antirep.insert(0, str(_para_int(filtros.get("anti_repeticao_segundos"), 60, 0)))
    entrada_antirep.grid(row=6, column=1, padx=8, sticky="w")

    tk.Label(aba_alertas,
             text="padrao = só a saída padrão do Windows (recomendado: a fala não sai duplicada)\n"
                  "todos  = fala em cada dispositivo, um depois do outro (comportamento antigo)",
             bg="#1e1e1e", fg="#6b7280", font=("Consolas", 9), justify=tk.LEFT
             ).pack(anchor="w", padx=14, pady=(8, 0))

    # ---------------- aba SISTEMA (watchdog + atualização) ----------------
    aba_sistema = tk.Frame(abas, bg="#1e1e1e")
    abas.add(aba_sistema, text="  Sistema  ")
    _selecionar()

    tk.Label(aba_sistema, text=f"Versão instalada: {VERSAO}   •   pasta: {PASTA_SCRIPT}",
             bg="#1e1e1e", fg="#9ca3af", font=("Consolas", 9)).pack(anchor="w", padx=14, pady=(10, 4))

    var_auto = tk.BooleanVar(value=_para_bool(filtros.get("auto_atualizar"), True))
    tk.Checkbutton(aba_sistema, text="Atualizar sozinho pelo GitHub (versao.json)", variable=var_auto,
                   bg="#1e1e1e", fg="#e5e7eb", selectcolor="#111111", activebackground="#1e1e1e",
                   activeforeground="#e5e7eb", font=("Consolas", 10), anchor="w"
                   ).pack(anchor="w", padx=14, pady=4)

    form3 = tk.Frame(aba_sistema, bg="#1e1e1e")
    form3.pack(anchor="w", padx=14, pady=(6, 0))
    campos_sistema = {}
    for linha, (chave, rotulo_campo, padrao_campo) in enumerate((
        ("atualizacao_intervalo_minutos", "Procurar atualização a cada (min):", 60),
        ("atualizacao_canal", "Canal (branch do GitHub):", "main"),
        ("watchdog_limite_sem_batimento_segundos", "Watchdog: agir após (s) sem batimento:", 180),
        ("watchdog_recarregar_pagina_minutos", "Recarregar a página a cada (min, 0 = nunca):", 30),
        ("watchdog_reiniciar_chrome_horas", "Reabrir o Chrome a cada (h, 0 = nunca):", 0),
        ("watchdog_max_reinicios_por_hora", "Máx. reinícios do Chrome por hora:", 6),
    )):
        tk.Label(form3, text=rotulo_campo, bg="#1e1e1e", fg="#e5e7eb",
                 font=("Consolas", 10)).grid(row=linha, column=0, sticky="w", pady=4)
        campo = tk.Entry(form3, width=14, bg="#111111", fg="#eeeeee", insertbackground="#eeeeee")
        valor = filtros.get(chave)
        campo.insert(0, str(padrao_campo if valor is None else valor))
        campo.grid(row=linha, column=1, padx=8, sticky="w")
        campos_sistema[chave] = (campo, padrao_campo)

    linha_botoes = tk.Frame(aba_sistema, bg="#1e1e1e")
    linha_botoes.pack(anchor="w", padx=14, pady=12)
    tk.Button(linha_botoes, text="⬆️ Procurar atualização agora", command=procurar_atualizacao_agora,
              bg="#1f3a5f", fg="white", relief=tk.FLAT, padx=10, pady=4).pack(side=tk.LEFT)
    tk.Button(linha_botoes, text="📂 Abrir pasta de logs",
              command=lambda: os.startfile(PASTA_LOGS) if os.path.isdir(PASTA_LOGS) else None,
              bg="#374151", fg="white", relief=tk.FLAT, padx=10, pady=4).pack(side=tk.LEFT, padx=8)

    # ---------------- rodapé ----------------
    rodape = tk.Frame(janela, bg="#1e1e1e")
    rodape.pack(fill=tk.X, padx=10, pady=(0, 10))

    def coletar_lista(lista):
        return [lista.get(i).strip() for i in range(lista.size()) if lista.get(i).strip()]

    def salvar():
        global _CRED_USUARIO_LOGADO
        try:
            dados = ler_json(ARQ_FILTROS)
            dados["whitelist"] = coletar_lista(lista_alertar)
            dados["blacklist"] = coletar_lista(lista_ignorar)
            dados["alerta_tela"] = bool(var_tela.get())
            dados["alerta_voz"] = bool(var_voz.get())
            try:
                dados["pausa_minutos"] = max(1, int(float(entrada_pausa.get().replace(",", "."))))
            except Exception:
                dados["pausa_minutos"] = 15
            try:
                dados["intervalo_busca_segundos"] = max(1, int(float(
                    entrada_intervalo.get().replace(",", "."))))
            except Exception:
                dados["intervalo_busca_segundos"] = 10
            dados["alarme_max_segundos"] = _para_int(entrada_max.get(), 120, 0)
            dados["intervalo_repeticao_segundos"] = _para_int(entrada_repeticao.get(), 15, 1)
            dados["volume_alarme"] = _para_int(entrada_volume.get(), 100, 0, 100)
            dados["saida_audio"] = combo_saida.get() or "padrao"
            dados["anti_repeticao_segundos"] = _para_int(entrada_antirep.get(), 60, 0)
            dados["auto_atualizar"] = bool(var_auto.get())
            for chave, (campo, padrao_campo) in campos_sistema.items():
                if isinstance(padrao_campo, int):
                    dados[chave] = _para_int(campo.get(), padrao_campo, 0)
                else:
                    dados[chave] = campo.get().strip() or padrao_campo
            _sincronizar_termos_filtro(dados)
            _gravar_json(ARQ_FILTROS, dados)

            cred = ler_json(ARQ_CREDENCIAIS)
            cred["usuario"] = entrada_usuario.get().strip()
            cred["senha"] = entrada_senha.get()
            cred["rota_login"] = (combo_rota.get() or "auto").lower()
            _gravar_json(ARQ_CREDENCIAIS, cred)

            carregar_filtros(forcar=True)
            global _CRED_USUARIO_LOGADO
            _CRED_USUARIO_LOGADO = None  # força relogar com as credenciais novas

            log_msg(
                f"⚙️ Configurações salvas: {len(dados['whitelist'])} alertar / "
                f"{len(dados['blacklist'])} ignorar | "
                f"alerta_tela={'ON' if dados['alerta_tela'] else 'OFF'} "
                f"alerta_voz={'ON' if dados['alerta_voz'] else 'OFF'}."
            )

            iniciou_agora = False
            if not _SELENIUM_RODANDO and not credenciais_incompletas():
                log_msg("▶️ Iniciando o monitoramento com as credenciais novas...")
                threading.Thread(target=iniciar_selenium, daemon=True).start()
                iniciou_agora = True

            messagebox.showinfo(
                "Configurações salvas",
                "filtros.json e credenciais.json atualizados.\n\n"
                "As senhas e os alertas valem na hora.\n"
                + ("O monitoramento foi iniciado com o login novo."
                   if iniciou_agora else
                   "O login novo é usado no próximo login (ao reiniciar o script)."),
                parent=janela,
            )
        except Exception as e:
            messagebox.showerror("Erro ao salvar", str(e), parent=janela)

    tk.Button(rodape, text="💾 Salvar", command=salvar, bg="#2d6a4f", fg="white",
              relief=tk.FLAT, padx=18, pady=6, font=("Consolas", 10, "bold")
              ).pack(side=tk.RIGHT)
    tk.Button(rodape, text="Fechar", command=janela.destroy, bg="#374151", fg="white",
              relief=tk.FLAT, padx=14, pady=6).pack(side=tk.RIGHT, padx=8)
    tk.Label(rodape, text=f"Arquivos: {os.path.basename(ARQ_FILTROS)} e {os.path.basename(ARQ_CREDENCIAIS)}",
             bg="#1e1e1e", fg="#4b5563", font=("Consolas", 8)).pack(side=tk.LEFT)


# ==============================================================================
# INTERFACE GRÁFICA (TKINTER) E INICIALIZAÇÃO
# ==============================================================================
root = tk.Tk()
root.title(f"Painel de Depuração - Monitor Feegow v{VERSAO}")
root.geometry("800x650")
root.configure(bg="#1e1e1e")
root.withdraw()
root.protocol("WM_DELETE_WINDOW", ao_fechar_janela)

frame_topo = tk.Frame(root, bg="#1e1e1e")
frame_topo.pack(fill=tk.X, padx=10, pady=(8, 0))
tk.Button(
    frame_topo, text="⚙️ Configurações", command=abrir_configuracoes,
    bg="#1f3a5f", fg="white", relief=tk.FLAT, padx=14, pady=6,
    font=("Consolas", 10, "bold"), cursor="hand2", activebackground="#2b4f7d",
    activeforeground="white",
).pack(side=tk.LEFT)

# Indicador discreto: conectado / reconectando / sem internet / falha
rotulo_status = tk.Label(frame_topo, text="● Iniciando...", bg="#1e1e1e", fg=CORES_STATUS["iniciando"],
                         font=("Consolas", 10, "bold"))
rotulo_status.pack(side=tk.LEFT, padx=14)


def _atualizar_rotulo_status():
    try:
        nivel, texto = _texto_status()
        rotulo_status.configure(text=texto, fg=CORES_STATUS.get(nivel, "#9ca3af"))
    except Exception:
        pass
    root.after(2000, _atualizar_rotulo_status)


frame_logs = tk.Frame(root, bg="#1e1e1e")
frame_logs.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
tk.Label(frame_logs, text="LOGS DE ATIVIDADE AVANÇADOS", bg="#1e1e1e", fg="#00ff00", font=("Consolas", 10, "bold")).pack(anchor="w")
janela_logs = scrolledtext.ScrolledText(frame_logs, bg="#000000", fg="#00ff00", font=("Consolas", 10), state='disabled', height=10)
janela_logs.pack(fill=tk.BOTH, expand=True)

frame_estudio = tk.LabelFrame(root, text="Estúdio de Voz", bg="#1e1e1e", fg="yellow", font=("Consolas", 10))
frame_estudio.pack(fill=tk.X, padx=10, pady=5)
entry_voz = tk.Entry(frame_estudio, font=("Consolas", 12), width=60)
entry_voz.insert(0, "Atenção! Senha de dilatação.")
entry_voz.pack(side=tk.LEFT, padx=10, pady=10, expand=True, fill=tk.X)
tk.Button(frame_estudio, text="Testar Texto", bg="#ff9900", fg="black", font=("Consolas", 10, "bold"), command=lambda: threading.Thread(target=gerar_e_tocar_alerta_teste, args=(entry_voz.get(), "configurado"), daemon=True).start()).pack(side=tk.RIGHT, padx=10)

frame_audio = tk.LabelFrame(root, text="Testes de Hardware de Áudio", bg="#1e1e1e", fg="white", font=("Consolas", 10))
frame_audio.pack(fill=tk.X, padx=10, pady=5)

for disp in DISPOSITIVOS_AUDIO:
    frame_linha = tk.Frame(frame_audio, bg="#1e1e1e")
    frame_linha.pack(fill=tk.X, padx=5, pady=2)
    tk.Label(frame_linha, text=f"🔊 {disp}", bg="#1e1e1e", fg="#cccccc").pack(side=tk.LEFT)
    tk.Button(frame_linha, text="Testar", bg="#444444", fg="white", command=lambda d=disp: threading.Thread(target=gerar_e_tocar_alerta_teste, args=("Teste.", "unico", d), daemon=True).start()).pack(side=tk.RIGHT)

if __name__ == "__main__":
    log_msg(f"▶️ Monitor de Senha v{VERSAO} iniciando (pasta: {PASTA_SCRIPT}"
            f"{', supervisionado' if _supervisionado() else ''}).")
    carregar_filtros(forcar=True)
    carregar_credenciais()
    preparar_audio_inicial()

    if credenciais_incompletas():
        log_msg("🔐 credenciais.json sem login/senha válidos — aguardando preenchimento na janela de configuração.")
        root.after(600, lambda: solicitar_credenciais("primeira_vez"))
    else:
        threading.Thread(target=iniciar_selenium, daemon=True).start()

    threading.Thread(target=iniciar_tray, daemon=True).start()
    threading.Thread(target=thread_watchdog, name="watchdog", daemon=True).start()
    threading.Thread(target=thread_atualizacao, name="atualizacao", daemon=True).start()
    root.after(300, _drenar_fila_log)
    root.after(1000, _atualizar_rotulo_status)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C no console: encerra sem traceback e sem deixar Chrome aberto
        log_msg("⌨️ Ctrl+C detectado — encerrando o sistema e fechando o navegador...")
        encerrar_sistema()
