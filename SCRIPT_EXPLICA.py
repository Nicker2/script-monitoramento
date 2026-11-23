# MONITOR-FEEGOW-SCRIPT :: Monitor de Senha do Feegow (github.com/Nicker2/script-monitoramento)
# A linha acima identifica este arquivo na atualização automática: não remova.
import io
import os
import re
import sys
import time
import queue
import socket
import base64
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
VERSAO = "2.3.3"
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
# Assinatura das versões publicadas (Ed25519). A chave PRIVADA fica fora do
# repositório, com quem publica; o versao.json sem assinatura válida é recusado.
CHAVE_PUBLICA_RELEASE = "b0247314424abf05e303b11a7e6120059d6bc49528db2855027a02b2eb01902f"
CANAIS_PERMITIDOS = ("main", "beta")
ARQUIVOS_EXTRAS_PERMITIDOS = ("iniciar_monitor.pyw",)   # além do script, só estes se atualizam


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
ARQ_HEARTBEAT = os.path.join(PASTA_LOGS, "heartbeat.json")   # lido pelo iniciar_monitor.pyw
ARQ_STATUS = os.path.join(PASTA_LOGS, "status.json")         # estado legível (diagnóstico)


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


def _gravar_json_atomico(caminho, dados, indent=2):
    """Grava JSON sem nunca deixar o arquivo pela metade (queda de energia,
    processo morto no meio): escreve num .tmp ao lado e troca com os.replace."""
    pasta = os.path.dirname(os.path.abspath(caminho))
    os.makedirs(pasta, exist_ok=True)
    # nome único por processo E por thread (heartbeat é gravado por duas threads)
    temporario = f"{caminho}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=indent)
            arquivo.write("\n")
            arquivo.flush()
            try:
                os.fsync(arquivo.fileno())
            except OSError:
                pass
        for tentativa in range(6):
            try:
                os.replace(temporario, caminho)
                return
            except PermissionError:
                # antivírus/indexador segurando o arquivo por um instante
                if tentativa == 5:
                    raise
                time.sleep(0.2)
    finally:
        try:
            if os.path.exists(temporario):
                os.remove(temporario)
        except OSError:
            pass


# --- Assinatura Ed25519 do versao.json ------------------------------------------
def _ed25519_verificar_puro(publica, assinatura, mensagem):
    """Verificação Ed25519 (RFC 8032) em Python puro — usada quando o pacote
    `cryptography` não está instalado (ex.: .exe compilado). Só verifica, não assina."""
    p = 2 ** 255 - 19
    q = 2 ** 252 + 27742317777372353535851937790883648493

    def inv(x):
        return pow(x, p - 2, p)
    d = -121665 * inv(121666) % p
    raiz_m1 = pow(2, (p - 1) // 4, p)

    def recuperar_x(y, sinal):
        if y >= p:
            return None
        x2 = (y * y - 1) * inv(d * y * y + 1) % p
        if x2 == 0:
            return None if sinal else 0
        x = pow(x2, (p + 3) // 8, p)
        if (x * x - x2) % p:
            x = x * raiz_m1 % p
        if (x * x - x2) % p:
            return None
        if (x & 1) != sinal:
            x = p - x
        return x

    def descomprimir(s):
        if len(s) != 32:
            return None
        y = int.from_bytes(s, "little")
        sinal = y >> 255
        y &= (1 << 255) - 1
        x = recuperar_x(y, sinal)
        return None if x is None else (x, y, 1, x * y % p)

    def somar(a, b):
        A = (a[1] - a[0]) * (b[1] - b[0]) % p
        B = (a[1] + a[0]) * (b[1] + b[0]) % p
        C = 2 * a[3] * b[3] * d % p
        D = 2 * a[2] * b[2] % p
        E, F, G, H = B - A, D - C, D + C, B + A
        return (E * F % p, G * H % p, F * G % p, E * H % p)

    def multiplicar(s, ponto):
        resultado = (0, 1, 1, 0)
        while s > 0:
            if s & 1:
                resultado = somar(resultado, ponto)
            ponto = somar(ponto, ponto)
            s >>= 1
        return resultado

    def iguais(a, b):
        return (a[0] * b[2] - b[0] * a[2]) % p == 0 and (a[1] * b[2] - b[1] * a[2]) % p == 0

    if len(publica) != 32 or len(assinatura) != 64:
        return False
    gy = 4 * inv(5) % p
    gx = recuperar_x(gy, 0)
    base = (gx, gy, 1, gx * gy % p)
    ponto_a, ponto_r = descomprimir(publica), descomprimir(assinatura[:32])
    if ponto_a is None or ponto_r is None:
        return False
    s = int.from_bytes(assinatura[32:], "little")
    if s >= q:
        return False
    h = int.from_bytes(hashlib.sha512(assinatura[:32] + publica + mensagem).digest(), "little") % q
    return iguais(multiplicar(s, base), somar(ponto_r, multiplicar(h, ponto_a)))


def verificar_assinatura_ed25519(publica, assinatura, mensagem):
    """True se a assinatura confere. Usa `cryptography` se houver; senão, Python puro."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:
        return _ed25519_verificar_puro(publica, assinatura, mensagem)
    try:
        Ed25519PublicKey.from_public_bytes(publica).verify(assinatura, mensagem)
        return True
    except Exception:
        return False


def mensagem_assinada_versao(dados):
    """Bytes assinados do versao.json: tudo menos o campo "assinatura", em JSON canônico."""
    conteudo = {k: v for k, v in dados.items() if k != "assinatura"}
    return json.dumps(conteudo, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def validar_versao_assinada(dados, canal_esperado=None):
    """Recusa versao.json sem assinatura válida, sem sha256 ou de outro canal."""
    import base64
    if not isinstance(dados, dict):
        raise ValueError("versao.json inválido")
    try:
        assinatura = base64.b64decode(str(dados.get("assinatura") or ""), validate=True)
    except Exception:
        assinatura = b""
    if not assinatura or not verificar_assinatura_ed25519(
            bytes.fromhex(CHAVE_PUBLICA_RELEASE), assinatura, mensagem_assinada_versao(dados)):
        raise ValueError("versao.json sem assinatura válida — atualização recusada")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(dados.get("sha256") or "")):
        raise ValueError("versao.json sem sha256 — atualização recusada")
    canal = str(dados.get("canal") or "").strip()
    if canal_esperado and canal and canal != canal_esperado:
        raise ValueError(f"versao.json é do canal '{canal}', esperado '{canal_esperado}'")
    return True


# --- Senha protegida pelo Windows (DPAPI) ----------------------------------------
class _BlobDpapi(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_ENTROPIA_DPAPI = b"MonitorFeegow/credenciais/v1"
PREFIXO_DPAPI = "dpapi:"


def _dpapi(dados, proteger):
    """CryptProtectData / CryptUnprotectData (escopo do usuário atual do Windows)."""
    if os.name != "nt":
        raise OSError("DPAPI só existe no Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    funcao = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
    funcao.argtypes = [ctypes.POINTER(_BlobDpapi), ctypes.c_void_p, ctypes.POINTER(_BlobDpapi),
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_BlobDpapi)]
    funcao.restype = ctypes.c_bool
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffers = []

    def blob(bruto):
        buffer = ctypes.create_string_buffer(bruto, len(bruto))
        buffers.append(buffer)
        return _BlobDpapi(len(bruto), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))

    entrada, entropia, saida = blob(dados), blob(_ENTROPIA_DPAPI), _BlobDpapi()
    if not funcao(ctypes.byref(entrada), None, ctypes.byref(entropia), None, None,
                  0x01, ctypes.byref(saida)):   # 0x01 = CRYPTPROTECT_UI_FORBIDDEN
        raise OSError(ctypes.get_last_error(), "DPAPI recusou os dados")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(saida.pbData, ctypes.c_void_p))


def proteger_texto(texto):
    import base64
    return PREFIXO_DPAPI + base64.b64encode(_dpapi(str(texto).encode("utf-8"), True)).decode("ascii")


def desproteger_texto(valor):
    import base64
    valor = str(valor or "")
    if not valor.startswith(PREFIXO_DPAPI):
        raise ValueError("formato desconhecido")
    return _dpapi(base64.b64decode(valor[len(PREFIXO_DPAPI):]), False).decode("utf-8")


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
        _gravar_json_atomico(ARQ_ESTADO_ATUALIZACAO, dados)
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
try:   # erros de conexão com o chromedriver (vem junto com o Selenium)
    from urllib3.exceptions import HTTPError as _Urllib3Erro
    _ERROS_CONEXAO_DRIVER = (ConnectionError, _Urllib3Erro)
except Exception:
    _ERROS_CONEXAO_DRIVER = (ConnectionError,)
# Com o chromedriver morto, cada chamada do Selenium enchia o console com
# "Retrying (Retry(total=2...)) ... Failed to establish a new connection".
# O monitor detecta e loga a recuperação ele mesmo.
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

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
    # fala por filtro (desde a 2.2.0): a senha que alertou é comparada com os
    # "termos" de cada filtro, na ordem; o primeiro que casar decide a fala.
    # "fala" vazia = fala a senha da tela ("Atenção! Senha ..."); "voz": false =
    # não fala (a janela vermelha continua valendo, se alerta_tela estiver ligado).
    "falas_filtros": {
        "dilatacao": {"nome": "Dilatação", "termos": ["DILATAÇÃO", "DILATACAO", "AUTO REFRATOR"],
                      "comparacao": "contem", "fala": "Atenção! Senha de dilatação", "voz": True},
        "lentes": {"nome": "Lentes", "termos": ["LENTES"],
                   "comparacao": "contem", "fala": "Nova Senha", "voz": True},
        "mc": {"nome": "Marcação MC", "termos": ["MC"],
               "comparacao": "exata", "fala": "", "voz": False},
        "r2": {"nome": "R2", "termos": ["R2"],
               "comparacao": "contem", "fala": "", "voz": True},
    },
    # watchdog (auto-recuperação)
    "watchdog_limite_sem_batimento_segundos": 180,
    "watchdog_recarregar_pagina_minutos": 30,
    "watchdog_reiniciar_chrome_horas": 0,
    "watchdog_max_reinicios_por_hora": 6,
    # escada de recuperação (2.2.2): Chrome sumiu -> recria o driver; falhou de novo -> reinicia o programa
    "recuperacao_falhas_para_reiniciar": 3,    # reaberturas do Chrome seguidas, sem ciclo OK...
    "recuperacao_janela_falhas_min": 10,       # ...dentro desta janela = reinicia o programa
    "recuperacao_min_sem_ok": 3,               # monitorando sem nenhum ciclo OK há X min = reinicia
    "recuperacao_min_sem_ok_total": 10,        # em qualquer etapa (abrir/login) sem ciclo OK há X min = reinicia
    # (2.3.3) sem teto de reinícios por hora: só um intervalo mínimo de 30 s entre um
    # reinício do programa e o próximo (REINICIO_INTERVALO_MINIMO_SEGUNDOS)
    # cronômetro de login (2.3.3): do início de qualquer tentativa de login até o login OK
    # (token no modo requisição / página logada no modo Chrome). Passou disso = reinicia o programa.
    "login_timeout_seg": 120,
    # reinício programado do programa inteiro (Chrome + processo), em minutos (0 = desligado)
    "reiniciar_a_cada_min": 60,
    # atualização automática pelo GitHub (versao.json)
    "auto_atualizar": True,
    "atualizacao_intervalo_minutos": 60,
    "atualizacao_canal": "main",
    # hosts usados para saber se há internet ("host" ou "host:porta", porta padrão 443)
    "hosts_verificacao_internet": ["app.feegow.com", "www.google.com"],
    # se a sessão cair mais que isso numa hora, o monitor para de relogar por 60 min
    "max_relogins_por_hora": 3,
    # modo de busca (2.3.0): "http" = depois do login lê a fila direto da API do Feegow
    # (Chrome fechado, 1 requisição a cada intervalo_requisicao_seg); "chrome" = o
    # método antigo (clique no "Chamar senha" e leitura do modal).
    "modo_busca": "http",
    "intervalo_requisicao_seg": 10,
    # a API respondeu fora do formato esperado (sem lista / sem NomeTipo) N vezes
    # seguidas = volta sozinho para o modo chrome até o próximo reinício do programa
    "http_ciclos_formato_invalido": 3,
    # loga cada requisição do modo requisição (→ GET / ← resposta e, quando muda, o corpo
    # com dados de paciente mascarados). O token nunca vai para o log.
    "log_requisicoes": True,
    # navegador
    "ciclo_leve": True,                     # clique + leitura do modal num único JavaScript
    "chrome_reinicio_diario_hora": "03:00", # reabre o Chrome 1x por dia nesse horário ("" = não)
    "chrome_limite_ram_mb": 1500,           # reabre se o Chrome passar disso (0 = não mede)
    "chrome_bloquear_imagens": False,       # economiza memória; teste antes de ligar
    # heartbeat remoto (VPS/Telegram): DESLIGADO enquanto a URL estiver vazia.
    # O token fica no credenciais.json protegido por DPAPI (aba Sistema).
    "heartbeat_remoto_url": "",
    "heartbeat_remoto_intervalo_minutos": 5,
    "nome_maquina": "",
    # horário de funcionamento (desligado = 24 h por dia, 7 dias por semana)
    "horario_funcionamento": {"ativo": False, "inicio": "06:00", "fim": "20:00",
                              "dias": ["seg", "ter", "qua", "qui", "sex", "sab"]},
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
_ETAPA = "parado"              # parado | abrindo_chrome | login | monitorando | reabrindo | offline | aguardando_login | fora_horario
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
_AVISOU_SEM_EXE = ""
_RELOGINS = []                   # horários dos relogins por "sessão caiu" (última hora)
_RELOGIN_BLOQUEADO_ATE = 0.0     # > agora: parou de relogar (outro PC com o mesmo usuário?)
_REINICIO_SEM_ESPERA = False     # reabrir o Chrome já (troca de login), sem backoff
_TK_TICK = time.time()           # última volta do laço do Tk (vai no status.json)
_ULTIMO_HEARTBEAT = 0.0
_PID_CHROMEDRIVER = 0
# escada de recuperação (2.2.2)
_MARCO_ETAPA = time.time()       # quando a etapa atual começou
_MARCO_ATIVO = time.time()       # quando saiu da última etapa "pausada" (offline, fora do horário...)
_FALHAS_CHROME = []              # reaberturas do Chrome desde o último ciclo OK
_AVISO_RECUPERACAO = ""          # último aviso de recuperação logado (loga só quando muda)
# cronômetro de login (2.3.3)
_LOGIN_INICIO = 0.0              # início da tentativa de login em andamento (0 = nenhuma)
_AVISO_CRONOMETRO = ""           # último aviso do cronômetro logado (loga só quando muda)


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


REINICIO_PROGRAMADO_MINIMO_MIN = 10


def _minutos_reinicio_programado(valor):
    """reiniciar_a_cada_min: 0 desliga; valores de 1 a 9 viram 10 (não reinicia em loop)."""
    minutos = _para_int(valor, 60, 0)
    if 0 < minutos < REINICIO_PROGRAMADO_MINIMO_MIN:
        minutos = REINICIO_PROGRAMADO_MINIMO_MIN
    return minutos


def _lista_normalizada(valor):
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return []
    return [normalizar_texto(item) for item in valor if normalizar_texto(item)]


def _hora_valida(valor, padrao=""):
    """'3:00' / '03:00' -> '03:00'; vazio ou inválido -> padrao."""
    achado = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(valor or ""))
    if not achado or int(achado.group(1)) > 23 or int(achado.group(2)) > 59:
        return padrao
    return f"{int(achado.group(1)):02d}:{achado.group(2)}"


DIAS_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sab", "dom")


def _horario_normalizado(valor):
    base = dict(FILTROS_PADRAO["horario_funcionamento"])
    if isinstance(valor, dict):
        base.update(valor)
    dias = base.get("dias") or list(DIAS_SEMANA)
    if isinstance(dias, str):
        dias = dias.replace(";", ",").split(",")
    dias_ok = []
    for dia in dias:
        chave = normalizar_texto(dia).lower()[:3]
        if chave in DIAS_SEMANA and chave not in dias_ok:
            dias_ok.append(chave)
    return {
        "ativo": _para_bool(base.get("ativo"), False),
        "inicio": _hora_valida(base.get("inicio"), "06:00"),
        "fim": _hora_valida(base.get("fim"), "20:00"),
        "dias": dias_ok or list(DIAS_SEMANA),
    }


def dentro_do_horario(cfg=None, agora=None):
    """True quando o monitor deve rodar. Desligado = sempre (24/7).
    Aceita virada da meia-noite (ex.: 22:00 -> 06:00)."""
    horario = (cfg or carregar_filtros()).get("horario_funcionamento") or {}
    if not horario.get("ativo"):
        return True
    agora = agora or datetime.datetime.now()
    minutos = agora.hour * 60 + agora.minute
    ini_h, ini_m = map(int, horario["inicio"].split(":"))
    fim_h, fim_m = map(int, horario["fim"].split(":"))
    inicio, fim = ini_h * 60 + ini_m, fim_h * 60 + fim_m
    dia = DIAS_SEMANA[agora.weekday()]
    dia_anterior = DIAS_SEMANA[(agora.weekday() - 1) % 7]
    if inicio == fim:
        return dia in horario["dias"]
    if inicio < fim:
        return dia in horario["dias"] and inicio <= minutos < fim
    return (minutos >= inicio and dia in horario["dias"]) or (minutos < fim and dia_anterior in horario["dias"])


def _falas_normalizadas(valor):
    """falas_filtros do filtros.json -> lista ordenada de filtros prontos para comparar.

    Filtro conhecido (dilatacao/lentes/mc/r2) sem algum campo usa o padrão dele;
    filtro novo sem "fala"/"voz" fala a senha da tela, como antes da 2.2.0.
    Sem a chave (filtros.json antigo) valem os padrões."""
    padroes = FILTROS_PADRAO["falas_filtros"]
    if not isinstance(valor, dict):
        valor = padroes
    filtros = []
    for chave, dados in valor.items():
        if not isinstance(dados, dict):
            continue
        base = dict(padroes.get(chave) or {"nome": str(chave), "termos": [str(chave)],
                                           "comparacao": "contem", "fala": "", "voz": True})
        base.update({k: v for k, v in dados.items() if v is not None})
        termos = base.get("termos")
        if isinstance(termos, str):
            termos = termos.split(",")
        termos_texto = [str(t).strip() for t in (termos or []) if str(t).strip()]
        comparacao = str(base.get("comparacao") or "contem").strip().lower()
        filtros.append({
            "chave": str(chave),
            "nome": str(base.get("nome") or chave).strip() or str(chave),
            "termos": [normalizar_texto(t) for t in termos_texto if normalizar_texto(t)],
            "termos_texto": termos_texto,
            "comparacao": "exata" if comparacao in ("exata", "exato", "igual") else "contem",
            "fala": str(base.get("fala") or "").strip()[:200],
            "voz": _para_bool(base.get("voz"), True),
        })
    return filtros


def migrar_falas_filtros(dados):
    """Primeira execução da 2.2.0 num PC que já tinha filtros.json: grava as falas
    padrão SÓ onde os campos novos não existem. Nunca apaga nem troca o que o PC
    já personalizou (nem recria um filtro que foi tirado de propósito).
    Devolve True quando mudou alguma coisa."""
    import copy
    padroes = FILTROS_PADRAO["falas_filtros"]
    atual = dados.get("falas_filtros")
    if not isinstance(atual, dict):
        dados["falas_filtros"] = copy.deepcopy(padroes)
        return True
    mudou = False
    for chave, filtro in atual.items():
        if not isinstance(filtro, dict) or chave not in padroes:
            continue
        for campo, valor in padroes[chave].items():
            if campo not in filtro:
                filtro[campo] = copy.deepcopy(valor)
                mudou = True
    return mudou


CHAVES_MODO_REQUISICAO = ("modo_busca", "intervalo_requisicao_seg", "http_ciclos_formato_invalido",
                          "log_requisicoes")


def migrar_modo_requisicao(dados):
    """Primeira execução da 2.3.0: só ACRESCENTA as chaves do modo requisição que
    faltam no filtros.json (nunca troca um valor que já existe). True = mudou."""
    mudou = False
    for chave in CHAVES_MODO_REQUISICAO:
        if chave not in dados:
            dados[chave] = FILTROS_PADRAO[chave]
            mudou = True
    return mudou


def _modo_busca_valido(valor):
    modo = str(valor or "").strip().lower()
    return modo if modo in ("http", "chrome") else "http"


def filtro_da_senha(ticket, cfg=None):
    """Filtro (de falas_filtros) em que a senha se encaixa, ou None."""
    cfg = cfg or carregar_filtros()
    texto = normalizar_texto(ticket)
    if not texto:
        return None
    for filtro in cfg.get("falas_filtros") or []:
        for termo in filtro["termos"]:
            if (texto == termo) if filtro["comparacao"] == "exata" else (termo in texto):
                return filtro
    return None


def _gravar_json(caminho, dados):
    """Gravação atômica (.tmp + os.replace): o JSON nunca fica pela metade."""
    _gravar_json_atomico(caminho, dados)


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
        if migrar_falas_filtros(dados):
            try:
                _gravar_json(ARQ_FILTROS, dados)
                mtime = os.path.getmtime(ARQ_FILTROS)
                log_msg("🗣️ filtros.json: falas por filtro adicionadas (Dilatação = 'Atenção! Senha de "
                        "dilatação', Lentes = 'Nova Senha', MC = sem voz). Personalizações mantidas.")
            except Exception as e:
                log_msg(f"⚠️ Não consegui gravar as falas por filtro no filtros.json ({e}). "
                        "Usando os padrões só na memória.")
        if migrar_modo_requisicao(dados):
            try:
                _gravar_json(ARQ_FILTROS, dados)
                mtime = os.path.getmtime(ARQ_FILTROS)
                log_msg(f"🌐 filtros.json: modo requisição adicionado (modo_busca='{dados['modo_busca']}', "
                        f"intervalo_requisicao_seg={dados['intervalo_requisicao_seg']}). "
                        "O resto ficou como estava.")
            except Exception as e:
                log_msg(f"⚠️ Não consegui gravar as chaves do modo requisição no filtros.json ({e}). "
                        "Usando os padrões só na memória.")
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
            # texto como foi digitado (com acento): usado para pré-gerar a fala
            "whitelist_texto": [str(t).strip() for t in (
                combinado.get("whitelist") if isinstance(combinado.get("whitelist"), list)
                else [combinado.get("whitelist") or ""]) if str(t).strip()],
            "pausa_minutos": max(1, int(combinado.get("pausa_minutos") or 15)),
            "intervalo_busca_segundos": max(1, int(combinado.get("intervalo_busca_segundos") or 10)),
            "alerta_tela": _para_bool(combinado.get("alerta_tela"), True),
            "alerta_voz": _para_bool(combinado.get("alerta_voz"), True),
            "alarme_max_segundos": _para_int(combinado.get("alarme_max_segundos"), 120, 0),
            "intervalo_repeticao_segundos": _para_int(combinado.get("intervalo_repeticao_segundos"), 15, 1),
            "volume_alarme": _para_int(combinado.get("volume_alarme"), 100, 0, 100),
            "saida_audio": str(combinado.get("saida_audio") or "padrao").strip(),
            "anti_repeticao_segundos": _para_int(combinado.get("anti_repeticao_segundos"), 60, 0),
            "falas_filtros": _falas_normalizadas(combinado.get("falas_filtros")),
            "watchdog_limite_sem_batimento_segundos": _para_int(
                combinado.get("watchdog_limite_sem_batimento_segundos"), 180, 60),
            "watchdog_recarregar_pagina_minutos": _para_int(
                combinado.get("watchdog_recarregar_pagina_minutos"), 30, 0),
            "watchdog_reiniciar_chrome_horas": _para_int(
                combinado.get("watchdog_reiniciar_chrome_horas"), 0, 0),
            "watchdog_max_reinicios_por_hora": _para_int(
                combinado.get("watchdog_max_reinicios_por_hora"), 6, 1),
            "recuperacao_falhas_para_reiniciar": _para_int(
                combinado.get("recuperacao_falhas_para_reiniciar"), 3, 1, 50),
            "recuperacao_janela_falhas_min": _para_int(
                combinado.get("recuperacao_janela_falhas_min"), 10, 1, 240),
            "recuperacao_min_sem_ok": _para_int(combinado.get("recuperacao_min_sem_ok"), 3, 1, 240),
            "recuperacao_min_sem_ok_total": _para_int(
                combinado.get("recuperacao_min_sem_ok_total"), 10, 3, 480),
            "login_timeout_seg": _para_int(combinado.get("login_timeout_seg"), 120, 30, 3600),
            "reiniciar_a_cada_min": _minutos_reinicio_programado(combinado.get("reiniciar_a_cada_min")),
            "auto_atualizar": _para_bool(combinado.get("auto_atualizar"), True),
            "atualizacao_intervalo_minutos": _para_int(
                combinado.get("atualizacao_intervalo_minutos"), 60, 10),
            "atualizacao_canal": str(combinado.get("atualizacao_canal") or CANAL_ATUALIZACAO_PADRAO).strip(),
            "max_relogins_por_hora": _para_int(combinado.get("max_relogins_por_hora"), 3, 1),
            "ciclo_leve": _para_bool(combinado.get("ciclo_leve"), True),
            "modo_busca": _modo_busca_valido(combinado.get("modo_busca")),
            "intervalo_requisicao_seg": _para_int(combinado.get("intervalo_requisicao_seg"), 10, 5, 300),
            "http_ciclos_formato_invalido": _para_int(
                combinado.get("http_ciclos_formato_invalido"), 3, 1, 100),
            "log_requisicoes": _para_bool(combinado.get("log_requisicoes"), True),
            "chrome_reinicio_diario_hora": _hora_valida(combinado.get("chrome_reinicio_diario_hora")),
            "chrome_limite_ram_mb": _para_int(combinado.get("chrome_limite_ram_mb"), 1500, 0),
            "chrome_bloquear_imagens": _para_bool(combinado.get("chrome_bloquear_imagens"), False),
            "heartbeat_remoto_url": str(combinado.get("heartbeat_remoto_url") or "").strip(),
            "heartbeat_remoto_intervalo_minutos": _para_int(
                combinado.get("heartbeat_remoto_intervalo_minutos"), 5, 1, 1440),
            "nome_maquina": str(combinado.get("nome_maquina") or "").strip()[:60],
            "horario_funcionamento": _horario_normalizado(combinado.get("horario_funcionamento")),
            "hosts_verificacao_internet": [
                str(h).strip() for h in (combinado.get("hosts_verificacao_internet")
                                         or FILTROS_PADRAO["hosts_verificacao_internet"])
                if str(h).strip()] or list(FILTROS_PADRAO["hosts_verificacao_internet"]),
        }
        _FILTROS_MTIME = mtime
        try:
            pregerar_audios_em_segundo_plano()
        except NameError:
            pass   # ainda importando o arquivo: a pré-geração é chamada na inicialização
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
    senha = _senha_do_arquivo(dados)
    credenciais = {
        "usuario": str(combinado.get("usuario") or "").strip(),
        "senha": senha,
        "rota_login": str(combinado.get("rota_login") or "auto").strip().lower(),
        "url_login": str(combinado.get("url_login") or CREDENCIAIS_PADRAO["url_login"]),
        "url_login_sso": str(combinado.get("url_login_sso") or CREDENCIAIS_PADRAO["url_login_sso"]),
        "url_lista_espera": str(combinado.get("url_lista_espera") or CREDENCIAIS_PADRAO["url_lista_espera"]),
    }

    if not credenciais["usuario"] or credenciais["usuario"].lower() == CREDENCIAIS_PADRAO["usuario"]:
        log_msg("⚠️ ATENÇÃO: credenciais.json está com os dados de EXEMPLO. Preencha usuário e senha.")
    elif dados and "senha" in dados and "senha_protegida" not in dados:
        _migrar_senha_para_dpapi(dados, senha)
    else:
        if _CRED_USUARIO_LOGADO != credenciais["usuario"]:
            _CRED_USUARIO_LOGADO = credenciais["usuario"]
            log_msg(f"🔐 Credenciais carregadas para: {credenciais['usuario']}")
    return credenciais


_AVISOU_DPAPI = set()


def _senha_do_arquivo(dados):
    """Senha em claro (só na memória): de senha_protegida (DPAPI) ou do campo antigo."""
    protegida = dados.get("senha_protegida") if isinstance(dados, dict) else None
    if protegida:
        try:
            return desproteger_texto(protegida)
        except Exception as e:
            if "abrir" not in _AVISOU_DPAPI:
                _AVISOU_DPAPI.add("abrir")
                log_msg("🔒 Não consegui abrir a senha protegida do credenciais.json "
                        f"({type(e).__name__}). O arquivo veio de outro usuário/PC? "
                        "Digite a senha de novo na janela ⚙️ (aba Login).")
            return ""
    return str((dados or {}).get("senha") or "")


def _pode_migrar_senha():
    """Não migra enquanto uma versão nova está em teste: se ela for revertida,
    a versão antiga (que só lê o campo "senha") continua conseguindo entrar."""
    if os.name != "nt":
        return False
    estado = _ler_estado_atualizacao()
    return not (estado.get("status") == "em_teste" and estado.get("para") == VERSAO)


def _migrar_senha_para_dpapi(dados, senha):
    if not senha or senha == CREDENCIAIS_PADRAO["senha"] or not _pode_migrar_senha():
        return
    try:
        novo = dict(dados)
        novo["senha_protegida"] = proteger_texto(senha)
        novo.pop("senha", None)
        _gravar_json(ARQ_CREDENCIAIS, novo)
        log_msg("🔒 Senha do credenciais.json protegida com DPAPI (o texto puro foi apagado do arquivo).")
    except Exception as e:
        if "migrar" not in _AVISOU_DPAPI:
            _AVISOU_DPAPI.add("migrar")
            log_msg(f"⚠️ Não consegui proteger a senha com DPAPI ({type(e).__name__}); ela continua no arquivo.")


def salvar_credenciais(usuario, senha, rota="auto"):
    """Grava o login. A senha vai protegida por DPAPI (texto puro só se o DPAPI falhar)."""
    try:
        cred = _ler_json(ARQ_CREDENCIAIS)
        if not isinstance(cred, dict):
            cred = {}
    except Exception:
        cred = {}
    cred["usuario"] = str(usuario or "").strip()
    cred["rota_login"] = str(rota or "auto").lower()
    try:
        cred["senha_protegida"] = proteger_texto(senha)
        cred.pop("senha", None)
    except Exception as e:
        log_msg(f"⚠️ DPAPI indisponível ({type(e).__name__}): a senha foi salva sem proteção.")
        cred["senha"] = senha
        cred.pop("senha_protegida", None)
    _gravar_json(ARQ_CREDENCIAIS, cred)


def ler_segredo(campo):
    """Lê um segredo protegido do credenciais.json (ex.: token do heartbeat remoto)."""
    try:
        valor = _ler_json(ARQ_CREDENCIAIS).get(campo)
        return desproteger_texto(valor) if valor else ""
    except Exception:
        return ""


def gravar_segredo(campo, valor):
    try:
        cred = _ler_json(ARQ_CREDENCIAIS)
        if not isinstance(cred, dict):
            cred = {}
    except Exception:
        cred = {}
    if valor:
        cred[campo] = proteger_texto(valor)
    else:
        cred.pop(campo, None)
    _gravar_json(ARQ_CREDENCIAIS, cred)


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
        na_ui(_mostrar)
    except Exception:
        _PEDIDO_CRED_PENDENTE = False


# Só o áudio do pygame (antes pygame.init() subia vídeo/joystick/etc. à toa).
# Sem placa de som o mixer falha: o monitor segue, sem voz, e avisa no log/bandeja.
try:
    pygame.mixer.init()
    DISPOSITIVOS_AUDIO = sdl2_audio.get_audio_device_names(False)
except Exception:
    DISPOSITIVOS_AUDIO = []

# ==============================================================================
# FUNÇÕES DE INTERFACE E JANELAS (TKINTER & OS)
# ==============================================================================
_FILA_LOG = queue.Queue()
_FILA_UI = queue.Queue()


def na_ui(funcao, *argumentos):
    """Agenda `funcao(*argumentos)` na thread do Tk. Seguro de qualquer thread.

    root.after() chamado de outra thread (Selenium, áudio, bandeja) não é
    garantido pelo Tkinter; a fila é esvaziada pela própria thread do Tk."""
    _FILA_UI.put((funcao, argumentos))


def _drenar_fila_ui():
    for _ in range(200):
        try:
            funcao, argumentos = _FILA_UI.get_nowait()
        except queue.Empty:
            break
        try:
            funcao(*argumentos)
        except Exception as e:
            log_msg(f"⚠️ Interface: erro em {getattr(funcao, '__name__', 'tarefa')}: {type(e).__name__}: {e}")
    try:
        root.after(100, _drenar_fila_ui)
    except Exception:
        pass


_RE_EMAIL = re.compile(r"([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mascarar_email(texto):
    """'fulano.silva@clinica.com.br' -> 'fu***@clinica.com.br' (log e diagnóstico)."""
    return _RE_EMAIL.sub(lambda m: f"{m.group(1)[:2]}***@{m.group(2)}", str(texto))


def log_msg(mensagem):
    """Log com hora: console + logs/monitor.log + painel.

    Pode ser chamado de qualquer thread. O painel (Tk) NÃO é tocado daqui:
    a linha vai para uma fila que a thread do Tk esvazia (_drenar_fila_log).
    Antes o Selenium escrevia direto no widget do Tk, que não é thread-safe —
    isso podia travar o painel depois de horas rodando.
    """
    mensagem = mascarar_email(mensagem)
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


# --- Tamanho da janela do Chrome (2.2.1) --------------------------------------
# Visível = MAXIMIZADA. Escondida = do tamanho da tela real (1920x1080, 1280x720...),
# nunca pequena: a Lista de Espera renderiza igual à visível e os seletores valem
# nos dois casos. Abaixo de 1280x720 (px CSS) usa 1280x720: acima do breakpoint
# "lg" (1200 px) do Bootstrap do Feegow, o layout é sempre o de desktop.
TAMANHO_MINIMO_CHROME = (1280, 720)
_SW_HIDE, _SW_SHOWMAXIMIZED = 0, 3
_SWP_NOSIZE, _SWP_NOZORDER, _SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
POSICAO_LIMBO_CHROME = (-10000, -10000)   # igual ao --window-position de ARGUMENTOS_CHROME


def _user32():
    return ctypes.windll.user32


def resolucao_tela():
    """(largura, altura, escala) da tela principal: pixels físicos e DPI/96."""
    try:
        u = _user32()
        largura, altura = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:
        return 0, 0, 1.0
    escala = 1.0
    try:
        dpi = int(u.GetDpiForSystem())
        if dpi > 0:
            escala = dpi / 96.0
    except Exception:
        pass
    return largura, altura, escala


def tamanho_janela_chrome(largura=None, altura=None, escala=None):
    """Tamanho da janela escondida em px CSS (o que o --window-size usa): a tela
    real dividida pela escala do Windows, nunca menor que TAMANHO_MINIMO_CHROME."""
    if largura is None or altura is None:
        largura, altura, escala_lida = resolucao_tela()
        escala = escala if escala else escala_lida
    escala = escala if escala and escala > 0 else 1.0
    minimo_l, minimo_a = TAMANHO_MINIMO_CHROME
    l = int(round((largura or 0) / escala))
    a = int(round((altura or 0) / escala))
    return max(l, minimo_l), max(a, minimo_a)


def argumentos_janela_chrome(visivel, tamanho=None):
    """Argumentos de janela do Chrome. Visível: maximizado (sem o "limbo").
    Escondido: fora da tela com o tamanho da tela real (sem piscar na tela)."""
    largura, altura = tamanho or tamanho_janela_chrome()
    argumentos = [f"--window-size={largura},{altura}"]
    if visivel:
        argumentos.append("--start-maximized")
    return argumentos


def maximizar_chrome(hwnd):
    """Mostra a janela do Chrome MAXIMIZADA. Se ela está no limbo (-10000), traz
    para a tela principal antes; senão o Windows maximiza no monitor "mais perto"."""
    u = _user32()
    if not u.IsZoomed(hwnd):
        u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
    u.ShowWindow(hwnd, _SW_SHOWMAXIMIZED)


def esconder_chrome(hwnd):
    """Esconde o Chrome. Se não está maximizado (o usuário restaurou e diminuiu),
    devolve ao limbo com o tamanho da tela real — nunca fica pequeno escondido.
    Maximizado escondido já tem o tamanho da tela e fica como está."""
    u = _user32()
    u.ShowWindow(hwnd, _SW_HIDE)
    if not u.IsZoomed(hwnd):
        largura, altura, escala = resolucao_tela()
        css_l, css_a = tamanho_janela_chrome(largura, altura, escala)
        x, y = POSICAO_LIMBO_CHROME
        u.SetWindowPos(hwnd, 0, x, y, int(round(css_l * escala)), int(round(css_a * escala)),
                       _SWP_NOZORDER | _SWP_NOACTIVATE)


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
                esconder_chrome(hwnd)
            except Exception:
                pass
        is_visible = False
        log_msg("Painel e Navegador ocultados da barra de tarefas.")
    elif acao == "mostrar":
        if _HTTP["ativo"]:
            _HTTP["pedido_mostrar"] = True   # modo requisição: abre o Chrome na Lista de Espera
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
                maximizar_chrome(hwnd)
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

def textos_janela_alarme(ticket=None, pausa_minutos=None):
    """(título, explicação) da janela vermelha, a partir da senha real e da pausa configurada."""
    ticket = str(ticket if ticket is not None else _TICKET_ALARME).strip()
    if pausa_minutos is None:
        pausa_minutos = carregar_filtros().get("pausa_minutos", 15)
    titulo = f"NOVA SENHA '{ticket}' NA FILA" if ticket else "NOVA SENHA NA FILA"
    unidade = "minuto" if int(pausa_minutos) == 1 else "minutos"
    explicacao = ("Clique em CIENTE (ou Enter / Espaço / Esc) para assumir o atendimento\n"
                  f"e pausar os alertas sonoros por {pausa_minutos} {unidade}.")
    return titulo, explicacao


def _fonte_que_cabe(familia, tamanho_px, peso, texto, largura_max, minimo_px=12):
    """Diminui a fonte até o texto caber na largura (senhas com nome comprido)."""
    from tkinter import font as tkfont
    tamanho = int(tamanho_px)
    while tamanho > minimo_px:
        fonte = tkfont.Font(family=familia, size=-tamanho, weight=peso)
        if max(fonte.measure(linha) for linha in texto.split("\n")) <= largura_max:
            break
        tamanho -= 1
    return (familia, -tamanho, peso)


def criar_janela_alarme():
    global janela_alerta, canvas_bg
    if janela_alerta is not None:
        return
    titulo, explicacao = textos_janela_alarme()
    janela_alerta = tk.Toplevel(root)
    janela_alerta.title("ALERTA DE SENHA")
    janela_alerta.attributes("-topmost", True)
    janela_alerta.overrideredirect(True)
    janela_alerta.configure(bg="#990000")

    # Tamanho proporcional ao DPI, mas sempre cabendo na tela (notebook pequeno).
    tela_l, tela_a = janela_alerta.winfo_screenwidth(), janela_alerta.winfo_screenheight()
    largura = min(int(800 * _ESCALA), int(tela_l * 0.92))
    altura = min(int(450 * _ESCALA), int(tela_a * 0.85))
    f = min(largura / 800, altura / 450)
    pos_x, pos_y = int((tela_l - largura) / 2), int((tela_a - altura) / 2)
    janela_alerta.geometry(f"{largura}x{altura}+{pos_x}+{pos_y}")

    alt_canvas = int(280 * f)
    canvas_bg = tk.Canvas(janela_alerta, width=largura, height=alt_canvas, bg="#990000", highlightthickness=0)
    canvas_bg.pack(pady=(int(40 * f), int(20 * f)))
    margem = int(40 * f)
    desenhar_retangulo_arredondado(canvas_bg, margem, int(20 * f), largura - margem, int(260 * f),
                                   radius=int(30 * f), fill="white", outline="#cccccc", width=2)
    espaco_icones = int(150 * f)
    canvas_bg.create_text(largura / 2, int(90 * f), text=titulo, fill="black",
                          font=_fonte_que_cabe("Segoe UI", 37 * f, "bold", titulo, largura - 2 * espaco_icones))
    desenhar_icone_alerta(canvas_bg, int(110 * f), int(90 * f))
    desenhar_icone_alerta(canvas_bg, largura - int(110 * f), int(90 * f))
    canvas_bg.create_text(largura / 2, int(185 * f), text=explicacao, fill="#555555", justify=tk.CENTER,
                          font=_fonte_que_cabe("Segoe UI", 24 * f, "bold", explicacao, largura - 2 * margem - 20))

    btn_ciente = tk.Button(janela_alerta, text=" CIENTE ", font=("Arial", -int(32 * f), "bold"), bg="#dddddd",
                           fg="black", relief=tk.RAISED, borderwidth=5, cursor="hand2",
                           command=lambda: desarmar_alarme(manual=True))
    btn_ciente.pack(ipady=int(15 * f), ipadx=int(50 * f), pady=(0, int(20 * f)))

    # Teclado: Enter / Espaço / Esc = CIENTE (quem está no balcão não precisa do mouse)
    for tecla in ("<Return>", "<KP_Enter>", "<space>", "<Escape>"):
        janela_alerta.bind(tecla, lambda _e: desarmar_alarme(manual=True))

    def _puxar_foco():
        try:
            if janela_alerta is not None:
                janela_alerta.lift()
                janela_alerta.focus_force()
                btn_ciente.focus_set()
        except Exception:
            pass
    janela_alerta.after(150, _puxar_foco)
    janela_alerta.after(1200, _puxar_foco)
    piscar_janela()

def desarmar_alarme(manual=False):
    """Roda na thread do Tk (use na_ui() de outras threads)."""
    global alarme_ativo, janela_alerta, pausa_ate, canvas_bg, _TICKET_ALARME
    with _TRAVA_ALARME:
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
# --- Fala: cache permanente + voz offline do Windows + bipe --------------------
FRASE_GENERICA = "Atenção! Nova senha na fila."
_FALHAS_AUDIO_LOGADAS = set()
_SEM_AUDIO = ""                  # motivo quando não há como falar (vai para a bandeja)
_GTTS_FALHOU_EM = 0.0
_TRAVA_GERACAO = threading.Lock()
_PREGERACAO = {"thread": None, "de_novo": False}


def _pasta_audio():
    """%LOCALAPPDATA%\\MonitorFeegow\\audio: as falas ficam guardadas de vez
    (não é apagado ao sair), então a voz funciona mesmo sem internet."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", PASTA_SCRIPT), "MonitorFeegow", "audio")
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = PASTA_TEMP
    return base


def _texto_para_fala(ticket):
    """'DILATAÇÃO - CCC' -> 'Dilatação - CCC' (siglas curtas continuam em maiúsculas)."""
    palavras = []
    for palavra in str(ticket or "").split():
        letras = "".join(c for c in palavra if c.isalpha())
        palavras.append(palavra.capitalize() if len(letras) > 3 and palavra.isupper() else palavra)
    return " ".join(palavras).strip()


def frase_alarme(ticket=None, cfg=None):
    """A frase falada: a "fala" do filtro da senha (falas_filtros) ou, quando o
    filtro não tem fala própria, a senha REAL que está na tela."""
    ticket = ticket if ticket is not None else _TICKET_ALARME
    try:
        filtro = filtro_da_senha(ticket, cfg)
    except Exception:
        filtro = None
    if filtro and filtro.get("fala"):
        return filtro["fala"]
    texto = _texto_para_fala(ticket)
    return f"Atenção! Senha {texto}." if texto else FRASE_GENERICA


def voz_ligada_para(ticket, cfg=None):
    """(fala?, filtro): False quando o filtro da senha está com "voz": false."""
    filtro = filtro_da_senha(ticket, cfg)
    return (filtro["voz"] if filtro else True), filtro


def _registrar_falha_audio(tipo, erro):
    """Cada tipo de falha aparece UMA vez no log (sem encher o arquivo)."""
    if tipo in _FALHAS_AUDIO_LOGADAS:
        return
    _FALHAS_AUDIO_LOGADAS.add(tipo)
    descricoes = {
        "gtts": "voz do Google (gTTS) indisponível — usando a voz do Windows",
        "sapi": "voz do Windows (SAPI) indisponível",
        "sapi_sem_pt": "nenhuma voz pt-BR instalada no Windows — a voz offline usa a voz padrão",
        "reproducao": "não consegui tocar o áudio (placa de som/dispositivo)",
        "sem_arquivo": "sem nenhum áudio para a fala — tocando bipe",
    }
    log_msg(f"🔈 Áudio: {descricoes.get(tipo, tipo)} ({type(erro).__name__ if isinstance(erro, BaseException) else erro}"
            f"{': ' + str(erro)[:120] if isinstance(erro, BaseException) else ''}).")


def _definir_sem_audio(motivo):
    global _SEM_AUDIO
    if _SEM_AUDIO == motivo:
        return
    _SEM_AUDIO = motivo
    try:
        _atualizar_titulo_bandeja()
    except NameError:
        pass


def _arquivo_cache(frase, extensao):
    chave = hashlib.sha1(f"pt|{frase.strip()}".encode("utf-8")).hexdigest()
    return os.path.join(_pasta_audio(), chave + extensao)


def _gerar_mp3_gtts(frase, destino):
    try:
        tts = gTTS(text=frase, lang="pt", timeout=10)
    except TypeError:   # gTTS antigo, sem o parâmetro timeout
        tts = gTTS(text=frase, lang="pt")
    temporario = f"{destino}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        tts.save(temporario)
        if os.path.getsize(temporario) < 500:
            raise ValueError("mp3 vazio")
        os.replace(temporario, destino)
    finally:
        if os.path.exists(temporario):
            try:
                os.remove(temporario)
            except OSError:
                pass


_SCRIPT_SAPI = (
    "$ErrorActionPreference='Stop';"
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'pt*' } "
    "| Select-Object -First 1;"
    "if ($v) { $s.SelectVoice($v.VoiceInfo.Name); Write-Output 'VOZ_PT' } else { Write-Output 'VOZ_OUTRA' };"
    "$s.SetOutputToWaveFile($env:MONITOR_TTS_DESTINO);"
    "$s.Speak($env:MONITOR_TTS_TEXTO);"
    "$s.Dispose()"
)


def _gerar_wav_sapi(frase, destino):
    """Voz offline do Windows (System.Speech/SAPI) gravada num WAV.

    O texto vai por variável de ambiente (nunca montado dentro do comando)."""
    temporario = f"{destino}.{os.getpid()}.{threading.get_ident()}.tmp.wav"
    ambiente = dict(os.environ, MONITOR_TTS_TEXTO=frase, MONITOR_TTS_DESTINO=temporario)
    try:
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _SCRIPT_SAPI],
            env=ambiente, capture_output=True, text=True, timeout=60, creationflags=0x08000000)
        if "VOZ_OUTRA" in (saida.stdout or ""):
            _registrar_falha_audio("sapi_sem_pt", "sem voz pt-BR")
        if not os.path.isfile(temporario) or os.path.getsize(temporario) < 1000:
            raise RuntimeError((saida.stderr or saida.stdout or "WAV não gerado").strip()[:200])
        os.replace(temporario, destino)
    finally:
        if os.path.exists(temporario):
            try:
                os.remove(temporario)
            except OSError:
                pass


def obter_audio(frase, usar_rede=True):
    """Caminho de um áudio com a frase: cache mp3 (gTTS) -> gTTS -> cache wav ->
    voz do Windows (SAPI). None quando nada funcionou (o alarme toca bipe)."""
    global _GTTS_FALHOU_EM
    frase = (frase or FRASE_GENERICA).strip()
    mp3, wav = _arquivo_cache(frase, ".mp3"), _arquivo_cache(frase, ".wav")
    if os.path.isfile(mp3) and os.path.getsize(mp3) > 0:
        return mp3
    with _TRAVA_GERACAO:
        if os.path.isfile(mp3) and os.path.getsize(mp3) > 0:
            return mp3
        # depois de uma falha do gTTS, espera 10 min antes de tentar a internet de novo
        if usar_rede and time.time() - _GTTS_FALHOU_EM > 600:
            try:
                _gerar_mp3_gtts(frase, mp3)
                return mp3
            except Exception as e:
                _GTTS_FALHOU_EM = time.time()
                _registrar_falha_audio("gtts", e)
        if os.path.isfile(wav) and os.path.getsize(wav) > 0:
            return wav
        if os.name == "nt":
            try:
                _gerar_wav_sapi(frase, wav)
                return wav
            except Exception as e:
                _registrar_falha_audio("sapi", e)
    return None


def _frases_para_pregerar(cfg=None):
    cfg = cfg or carregar_filtros()
    frases = [FRASE_GENERICA]
    for texto in cfg.get("whitelist_texto") or []:
        falar, _filtro = voz_ligada_para(texto, cfg)
        frase = frase_alarme(texto, cfg)
        if falar and frase not in frases:
            frases.append(frase)
    return frases


def pregerar_audios_em_segundo_plano():
    """Gera (uma vez só, fica no cache) a fala de cada senha da whitelist, fora
    da thread principal — a inicialização não espera a internet."""
    atual = _PREGERACAO.get("thread")
    if atual is not None and atual.is_alive():
        _PREGERACAO["de_novo"] = True
        return

    def _tarefa():
        while True:
            _PREGERACAO["de_novo"] = False
            geradas = 0
            for frase in _frases_para_pregerar():
                if _ENCERRANDO:
                    return
                antes = os.path.isfile(_arquivo_cache(frase, ".mp3"))
                if obter_audio(frase) and not antes:
                    geradas += 1
            if geradas:
                log_msg(f"🔊 {geradas} fala(s) de senha preparada(s) no cache de áudio.")
            if not _PREGERACAO["de_novo"]:
                return
    thread = threading.Thread(target=_tarefa, name="pregeracao_audio", daemon=True)
    _PREGERACAO["thread"] = thread
    thread.start()


def _bipe_alarme():
    """Último recurso quando não existe áudio nenhum."""
    try:
        import winsound
        for _ in range(3):
            if not alarme_ativo:
                break
            winsound.Beep(880, 350)
            time.sleep(0.15)
    except Exception as e:
        _registrar_falha_audio("bipe", e)

_TRAVA_AUDIO = threading.Lock()      # uma fala por vez, nunca duas sobrepostas
_TRAVA_ALARME = threading.RLock()    # estado do alarme (alarme_ativo / _TICKET_ALARME)
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


def tocar_audio_em_dispositivo(nome_dispositivo, arquivo_audio, volume=1.0, parar_se_desarmar=True):
    """Toca o áudio (mp3/wav) no dispositivo informado (None = saída padrão)."""
    with _TRAVA_AUDIO:
        return _tocar_audio_sem_trava(nome_dispositivo, arquivo_audio, volume, parar_se_desarmar)


def _tocar_audio_sem_trava(nome_dispositivo, arquivo_audio, volume=1.0, parar_se_desarmar=True):
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
            if parar_se_desarmar and not alarme_ativo:
                # desarmou no meio da fala: para na hora (antes ficava até acabar o mp3)
                pygame.mixer.music.stop()
                break
            time.sleep(0.1)
        _definir_sem_audio("")
        return True
    except Exception as e:
        _registrar_falha_audio("reproducao", e)
        _definir_sem_audio("não consegui tocar o som")
        return False


def gerar_e_tocar_alerta_teste(mensagem, modo="configurado", dispositivo_especifico=None):
    """Teste do painel. 'configurado' = mesma saída do alarme; 'unico' = um dispositivo."""
    try:
        arquivo = obter_audio(mensagem)
        if not arquivo:
            log_msg("🔈 Teste de áudio: não consegui gerar a fala (sem internet e sem voz do Windows).")
            return
        if modo == "unico" and dispositivo_especifico:
            tocar_audio_em_dispositivo(dispositivo_especifico, arquivo, parar_se_desarmar=False)
        else:
            for disp in dispositivos_de_saida():
                tocar_audio_em_dispositivo(disp, arquivo, parar_se_desarmar=False)
        try:
            pygame.mixer.quit()
        except Exception:
            pass
    except Exception as e:
        log_msg(f"Erro no áudio de teste: {str(e)}")

def loop_audio_alarme():
    global alarme_ativo

    cfg = carregar_filtros()
    intervalo = _para_int(cfg.get("intervalo_repeticao_segundos"), 15, 1)
    limite_seg = _para_int(cfg.get("alarme_max_segundos"), 120, 0)
    volume = _para_int(cfg.get("volume_alarme"), 100, 0, 100) / 100.0
    inicio = time.time()

    # A fala é a da senha que está na tela (cache -> gTTS -> voz do Windows).
    ticket = _TICKET_ALARME
    arquivo = obter_audio(frase_alarme(ticket, cfg)) or obter_audio(FRASE_GENERICA, usar_rede=False)
    if not arquivo:
        _registrar_falha_audio("sem_arquivo", "nenhuma voz disponível")
        _definir_sem_audio("sem voz disponível (só bipe)")

    def estourou():
        return bool(limite_seg) and (time.time() - inicio) > limite_seg

    try:
        while alarme_ativo:
            if estourou():
                log_msg(f"⏱️ Alarme tocou por {limite_seg}s e foi desarmado automaticamente.")
                na_ui(desarmar_alarme, False)
                break

            tocou = False
            if arquivo and os.path.exists(arquivo):
                for disp in dispositivos_de_saida(cfg):
                    if not alarme_ativo:
                        break
                    tocou = tocar_audio_em_dispositivo(disp, arquivo, volume) or tocou
            if not tocou and alarme_ativo:
                _bipe_alarme()
            _ULTIMO_ANUNCIO[normalizar_texto(ticket)] = time.time()

            for _ in range(intervalo):
                if not alarme_ativo or estourou():
                    break
                time.sleep(1)

        try:
            pygame.mixer.quit()
        except Exception:
            pass
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
    with _TRAVA_ALARME:   # duas threads nunca disparam o mesmo alarme duas vezes
        _disparar_estado_alarme_sem_trava()


def _disparar_estado_alarme_sem_trava():
    global alarme_ativo
    if alarme_ativo:
        return

    cfg = carregar_filtros()
    quer_tela = cfg.get("alerta_tela", True)
    quer_voz = cfg.get("alerta_voz", True)
    voz_filtro, filtro = voz_ligada_para(_TICKET_ALARME, cfg)
    if quer_voz and not voz_filtro:
        quer_voz = False
        log_msg(f"🔇 '{_TICKET_ALARME}': voz desligada no filtro '{filtro['nome']}' (aba Falas).")

    if not quer_tela and not quer_voz:
        if voz_filtro:
            log_msg("⚠️ Chamada detectada, mas alerta_tela e alerta_voz estão desligados no filtros.json — nada será exibido nem tocado.")
        else:
            log_msg("⚠️ Chamada detectada sem voz (filtro) e com alerta_tela desligado — nada será exibido nem tocado.")
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
        na_ui(criar_janela_alarme)
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
    # (sem --no-sandbox: a sandbox do Chrome fica LIGADA; só é desligada como
    #  último recurso se o Chrome não abrir de jeito nenhum — ver _abrir_chrome)
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
    # mais leve para PC fraco: nada rodando em segundo plano além da Lista de Espera
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--mute-audio",
    "--disk-cache-size=52428800",     # cache em disco limitado a 50 MB
)
ARGUMENTO_SEM_SANDBOX = "--no-sandbox"


def _abrir_chrome():
    """Abre o Chrome (perfil fixo; se falhar, temporário). True quando abriu."""
    global driver, CHROME_HWND, _PID_CHROMEDRIVER
    log_msg("Iniciando WebDriver no 'limbo' (fora da tela)...")

    perfil_chrome = _pasta_perfil_chrome()
    if perfil_chrome:
        # perfil fixo + Chrome órfão de execução anterior = pasta travada
        _encerrar_instancias_antigas(perfil_chrome)
        _limpar_sessao_antiga(perfil_chrome)
        _marcar_perfil_limpo(perfil_chrome)

    cfg = carregar_filtros()
    abrir_visivel = _janela_visivel()
    tamanho_janela = tamanho_janela_chrome()
    log_msg(f"🖥️ Janela do Chrome: {'maximizada' if abrir_visivel else 'escondida'} "
            f"({tamanho_janela[0]}x{tamanho_janela[1]} px quando fora da tela).")

    def montar_options(com_perfil=True, sem_sandbox=False):
        opcoes = webdriver.ChromeOptions()
        prefs = {"profile.default_content_setting_values.notifications": 2}
        if cfg.get("chrome_bloquear_imagens"):
            prefs["profile.managed_default_content_settings.images"] = 2
        opcoes.add_experimental_option("prefs", prefs)
        for argumento in ARGUMENTOS_CHROME:
            if abrir_visivel and argumento.startswith("--window-position"):
                continue   # visível = maximizado na tela, não no limbo
            opcoes.add_argument(argumento)
        for argumento in argumentos_janela_chrome(abrir_visivel, tamanho_janela):
            opcoes.add_argument(argumento)
        if sem_sandbox:
            opcoes.add_argument(ARGUMENTO_SEM_SANDBOX)
        if com_perfil and perfil_chrome:
            opcoes.add_argument(f"--user-data-dir={perfil_chrome}")
        return opcoes

    _limpar_scoped_dirs_orfas()

    # 1) perfil fixo  2) perfil temporário  3) perfil temporário sem sandbox (último recurso)
    tentativas = (
        (True, False, None),
        (False, False, "✔️ Chrome aberto com perfil temporário (o perfil fixo estava em uso)."),
        (False, True, "✔️ Chrome aberto SEM sandbox (antivírus/política bloqueando a sandbox?)."),
    )
    driver, ultimo_erro = None, None
    for indice, (com_perfil, sem_sandbox, aviso_ok) in enumerate(tentativas):
        try:
            driver = criar_driver_chrome(montar_options(com_perfil, sem_sandbox))
            if aviso_ok:
                log_msg(aviso_ok)
            break
        except Exception as erro:
            ultimo_erro = erro
            driver = None
            if indice == 0:
                log_msg(f"⚠️ O Chrome não abriu com o perfil fixo ({type(erro).__name__}). "
                        "Tentando com perfil temporário...")
            elif indice == 1:
                log_msg(f"⚠️ Também não abriu com perfil temporário ({type(erro).__name__}). "
                        "Última tentativa: sem a sandbox do Chrome (--no-sandbox)...")
    if driver is None:
        log_msg(f"🛑 O WebDriver não iniciou: {type(ultimo_erro).__name__}: {ultimo_erro}")
        log_msg("   Se persistir, baixe o chromedriver da sua versão do Chrome em "
                "https://googlechromelabs.github.io/chrome-for-testing/ e salve como "
                f"'chromedriver.exe' nesta pasta: {PASTA_SCRIPT}")
        return False

    try:
        _PID_CHROMEDRIVER = int(driver.service.process.pid)
    except Exception:
        _PID_CHROMEDRIVER = 0

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
            _maximizar_chrome_aberto(CHROME_HWND)
    else:
        log_msg("ERRO: Não foi possível rastrear a janela do Chrome gerada.")
        if _janela_visivel():
            _maximizar_chrome_aberto(None)
    return True


def _maximizar_chrome_aberto(hwnd):
    """Painel visível na abertura/reinício: Chrome maximizado (Win32; reserva:
    driver.maximize_window())."""
    try:
        if hwnd:
            maximizar_chrome(hwnd)
            return True
    except Exception:
        pass
    try:
        if driver is not None:
            driver.maximize_window()
            return True
    except Exception as erro:
        log_msg(f"⚠️ Não consegui maximizar o Chrome ({type(erro).__name__}).")
    return False


def _matar_chromedriver_por_pid():
    """Reserva do fechamento: mata o chromedriver que ESTE monitor abriu (e os
    Chromes filhos dele), pelo PID guardado — pega também o Chrome aberto com
    perfil temporário, que a limpeza por perfil não enxerga."""
    global _PID_CHROMEDRIVER
    pid, _PID_CHROMEDRIVER = _PID_CHROMEDRIVER, 0
    if not pid:
        return 0
    mortos = 0
    try:
        processo = psutil.Process(pid)
        if "chromedriver" not in (processo.name() or "").lower():
            return 0   # o PID foi reaproveitado por outro programa: não mexe
        for filho in processo.children(recursive=True):
            try:
                filho.kill()
                mortos += 1
            except Exception:
                pass
        processo.kill()
        mortos += 1
    except Exception:
        return mortos
    if mortos:
        log_msg(f"🧹 Encerrei o chromedriver (PID {pid}) e {mortos - 1} processo(s) filho(s) que sobraram.")
    return mortos


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
    try:
        _matar_chromedriver_por_pid()
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
    global _SELENIUM_RODANDO, _PEDIDO_REINICIO_CHROME, _CRED_RECUSADAS, _REINICIO_SEM_ESPERA
    _SELENIUM_RODANDO = True
    _CRED_RECUSADAS = False
    try:
        while not _ENCERRANDO:
            if not dentro_do_horario():
                _aguardar_horario_funcionamento()
                continue
            _PEDIDO_REINICIO_CHROME = ""
            try:
                motivo = executar_sessao_chrome()
            except Exception as e:
                motivo = f"erro inesperado: {type(e).__name__}: {e}"
            _fechar_chrome()
            if motivo is None or _ENCERRANDO or _CRED_RECUSADAS:
                break
            if motivo == MOTIVO_FORA_HORARIO:
                continue
            log_msg(f"♻️ [N2] Reabrindo o Chrome. Motivo: {motivo}")
            _registrar_falha_chrome(motivo)
            if _REINICIO_SEM_ESPERA:
                _REINICIO_SEM_ESPERA = False
                _dormir(2, progresso=True)
                continue
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
    if carregar_filtros().get("modo_busca") == "http" and not _HTTP["desligado"]:
        motivo = sessao_modo_requisicao()
        if motivo != VOLTAR_PARA_CHROME:
            return motivo
        # reserva: a API mudou de formato (ou o modo foi trocado no filtros.json)
        if driver is None or not chrome_vivo(ping=False)[0]:
            _fechar_chrome()
            _definir_etapa("abrindo_chrome")
            if not _abrir_chrome():
                return "o Chrome não abriu"
            _definir_etapa("login")
            if not fazer_login():
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
            except Exception as erro:
                if _erro_chrome_morto(erro):
                    raise   # Chrome/chromedriver morto: não fica 20 s batendo na porta fechada
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


def _confirmar_troca_sessao(driver_alvo, senha=None, timeout=90.0):
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
        except Exception as erro:
            if _erro_chrome_morto(erro):
                raise
            tem_disconnect = False
        if tem_disconnect:
            if not avisou:
                log_msg("⚠️ Sessão ativa em outra máquina. Confirmando a troca de sessão (a outra máquina será desconectada)...")
                avisou = True
            if senha:
                try:
                    campo = driver_alvo.find_element(By.CSS_SELECTOR, "#password")
                    if not (campo.get_attribute("value") or "").strip():
                        campo.clear()
                        campo.send_keys(senha)
                except Exception:
                    pass
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
                if _confirmar_troca_sessao(driver, senha):
                    return True, "sessão assumida direto no handshake do SSO"
        except Exception:
            pass
        return False, "a etapa da senha não apareceu"

    campo_senha.clear()
    campo_senha.send_keys(senha)
    if not _enviar_form(driver):
        return False, "não encontrei o botão de enviar a senha no SSO"
    log_msg("SSO: senha enviada. Validando com o servidor...")

    # Conflito de sessao: assume pelo reenvio rapido antes de esperar o site.
    ok_bypass, motivo_bypass = _executar_bypass_sso(usuario, senha)
    if ok_bypass:
        return True, motivo_bypass
    if _credenciais_recusadas(_texto_corpo(driver)):
        return False, "senha incorreta no SSO"
    return False, motivo_bypass


BYPASS_SSO_INTERVALO_MS = 1500      # reenvio do login no SSO a cada 1,5 s
BYPASS_SSO_MAX_TENTATIVAS = 40       # ~60 s no pior caso

# Bypass do SSO: o form de conflito volta com input[name=disconnect] e a SENHA
# APAGADA. Reenviar o login em intervalo curto assume a sessao rapido — o
# handshake nativo do site faz check de 5 em 5 s por ate 60 s.
JS_BYPASS_SESSAO_SSO = """
const user = arguments[0], pass = arguments[1];
const intervalo = arguments[2], maxTentativas = arguments[3];
const alvo = window.location.origin + '/main/sso/';
window.__monitorBypassParar = false;
window.__monitorBypass = {tentativas: 0, estado: 'rodando'};
(async function() {
    for (let i = 0; i < maxTentativas && !window.__monitorBypassParar; i++) {
        window.__monitorBypass.tentativas = i + 1;
        try {
            const corpo = new URLSearchParams();
            corpo.set('step', 'password');
            corpo.set('User', user);
            corpo.set('password', pass);
            const res = await fetch(alvo, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: corpo.toString()
            });
            const html = await res.text();
            if (html.indexOf('E-mail ou senha incorretos') !== -1 ||
                html.indexOf('conferem') !== -1) {
                window.__monitorBypass.estado = 'credenciais';
                return;
            }
            const aindaConflito = html.indexOf('conectado em outra') !== -1 ||
                                  /name=["']disconnect["']/i.test(html);
            if (!aindaConflito) {
                window.__monitorBypass.estado = 'ok';
                window.location.href = res.url || (window.location.origin + '/main/');
                return;
            }
        } catch (e) { /* rede oscilou: tenta de novo no proximo ciclo */ }
        await new Promise(function (r) { setTimeout(r, intervalo); });
    }
    window.__monitorBypass.estado = window.__monitorBypassParar ? 'parado' : 'desistiu';
})();
"""

BYPASS_MAX_TENTATIVAS = 120          # tentativas do JS antes de desistir sozinho
BYPASS_ESPERA_INICIAL_MS = 1000      # 1 s, crescendo 1,5x até 10 s
BYPASS_ESPERA_MAXIMA_MS = 10000
BYPASS_LIMITE_SEGUNDOS = 300         # o Python desiste depois disso e manda o JS parar

# Usuário/senha chegam como ARGUMENTOS do execute_script (arguments[0..]) — nunca
# montados dentro do texto do JavaScript.
JS_BYPASS_SESSAO = """
const user = arguments[0], pass = arguments[1];
const maxTentativas = arguments[2], esperaInicial = arguments[3], esperaMaxima = arguments[4];
const actionUrl = window.location.href;
window.__monitorBypassParar = false;
window.__monitorBypass = {tentativas: 0, estado: 'rodando'};
document.body.innerHTML = '<div style="display:flex; justify-content:center; align-items:center; height:100vh; background:#17a2b8; color:white; font-family:sans-serif; flex-direction:column;"><h2>Autenticando Acesso</h2><p>Aguardando a outra sessão liberar...</p></div>';
(async function() {
    let espera = esperaInicial;
    while (!window.__monitorBypassParar && window.__monitorBypass.tentativas < maxTentativas) {
        window.__monitorBypass.tentativas++;
        try {
            let res = await fetch(actionUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams({User: user, password: pass})
            });
            let html = await res.text();
            if (!html.includes('Este usuário já está conectado')) {
                window.__monitorBypass.estado = 'ok';
                window.location.href = res.url || 'https://app.feegow.com/main/';
                return;
            }
        } catch (e) {}
        await new Promise(resolve => setTimeout(resolve, espera));
        espera = Math.min(Math.round(espera * 1.5), esperaMaxima);
    }
    window.__monitorBypass.estado = window.__monitorBypassParar ? 'parado' : 'desistiu';
})();
"""


def _parar_bypass_js():
    try:
        driver.execute_script("window.__monitorBypassParar = true;")
    except Exception:
        pass


def _executar_bypass_sso(usuario, senha):
    """Conflito de sessao no SSO: reenvia o POST do login em intervalo curto.

    O site sozinho faz o handshake disconnect/check/force com check de 5 s e teto
    de 60 s. Aqui o login e reenviado a cada BYPASS_SSO_INTERVALO_MS ate a
    resposta deixar de ser a tela de conflito. Senha errada aborta na hora.
    """
    try:
        driver.execute_script(JS_BYPASS_SESSAO_SSO, usuario, senha,
                              BYPASS_SSO_INTERVALO_MS, BYPASS_SSO_MAX_TENTATIVAS)
    except Exception as e:
        log_msg(f"Nao consegui iniciar o reenvio automatico no SSO ({e}). "
                "Caindo no handshake normal do site.")
        return _confirmar_troca_sessao(driver, senha), "handshake do site"

    log_msg("Sessao dupla no SSO: reenviando o login a cada "
            f"{BYPASS_SSO_INTERVALO_MS / 1000:.1f}s (ate {BYPASS_SSO_MAX_TENTATIVAS} tentativas)...")

    fim = time.time() + BYPASS_LIMITE_SEGUNDOS
    while time.time() < fim:
        if _ENCERRANDO or _PEDIDO_REINICIO_CHROME:
            _parar_bypass_js()
            return False, "bypass interrompido (encerrando/reiniciando)"
        if _sessao_ativa(driver):
            log_msg("Sessao assumida no SSO pelo reenvio do login.")
            return True, "bypass SSO concluido"
        try:
            estado = driver.execute_script("return (window.__monitorBypass || {}).estado || '';")
        except Exception as erro:
            if _erro_chrome_morto(erro):
                return False, f"Chrome parou no meio do login: {type(erro).__name__}: {erro}"
            estado = ""   # pagina navegando
        if estado == "credenciais":
            return False, "senha incorreta no SSO"
        if estado == "desistiu":
            return False, f"o reenvio desistiu depois de {BYPASS_SSO_MAX_TENTATIVAS} tentativas"
        registrar_batimento(ok=False)
        time.sleep(0.5)

    _parar_bypass_js()   # nao deixa o JS martelando o servidor
    return False, "o reenvio do login no SSO nao confirmou a tempo"


def login_legado(usuario, senha, url_login):
    """Login na rota antiga (?P=Login) mantendo o bypass de sessão dupla."""
    driver.get(url_login)
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "User")))
    except TimeoutException:
        return False, "formulário antigo não encontrado"

    driver.execute_script(
        "document.getElementById('User').value = arguments[0];"
        "document.getElementById('password').value = arguments[1];", usuario, senha)
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

    log_msg("⚠️ Sessão dupla detectada! Tentando assumir a sessão (com limite e espera crescente)...")
    driver.execute_script(JS_BYPASS_SESSAO, usuario, senha, BYPASS_MAX_TENTATIVAS,
                          BYPASS_ESPERA_INICIAL_MS, BYPASS_ESPERA_MAXIMA_MS)
    log_msg(f"JS do bypass rodando (até {BYPASS_MAX_TENTATIVAS} tentativas, 1 s -> 10 s entre elas).")

    fim = time.time() + BYPASS_LIMITE_SEGUNDOS
    while time.time() < fim:
        if _ENCERRANDO or _PEDIDO_REINICIO_CHROME:
            _parar_bypass_js()
            return False, "bypass interrompido (encerrando/reiniciando)"
        if _sessao_ativa(driver):
            log_msg("✔️ Bypass efetuado! Conexão estabelecida com sucesso.")
            return True, "bypass concluído"
        try:
            estado = driver.execute_script("return (window.__monitorBypass || {}).estado || '';")
        except Exception as erro:
            if _erro_chrome_morto(erro):
                return False, f"Chrome parou no meio do login: {type(erro).__name__}: {erro}"
            estado = ""   # página navegando
        if estado == "desistiu":
            return False, f"o bypass desistiu depois de {BYPASS_MAX_TENTATIVAS} tentativas"
        registrar_batimento(ok=False)
        time.sleep(1.0)
    _parar_bypass_js()   # o Python desistiu: o JS não fica martelando o servidor
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
        parar_cronometro_login()   # dados de exemplo: reiniciar não adianta (2.3.3)
        log_msg("🛑 credenciais.json ainda está com os dados de exemplo. Corrija antes de continuar.")
        solicitar_credenciais("primeira_vez")
        return False
    iniciar_cronometro_login()     # 2.3.3: o watchdog reinicia o programa se passar de login_timeout_seg

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
                if not _modo_http_em_uso():
                    parar_cronometro_login()   # modo Chrome: página logada = login OK
                # modo requisição: o cronômetro só para quando o token chega
                return True
            log_msg(f"⚠️ Rota {nome_rota} falhou: {motivo}")
            if ("credenciais" in motivo or "senha incorreta" in motivo
                    or "e-mail recusado" in motivo):
                log_msg("🛑 Credenciais recusadas pelo Feegow. Confira usuario/senha no credenciais.json — "
                        "parando para não travar a conta.")
                _CRED_RECUSADAS = True
                parar_cronometro_login()   # senha errada: reiniciar não adianta
                solicitar_credenciais("recusadas")
                return False
            if _erro_chrome_morto(motivo):
                log_msg("🩺 O Chrome parou durante o login — recriando o navegador.")
                return False
            vivo, motivo_chrome = chrome_vivo()
            if not vivo:
                log_msg(f"🩺 {motivo_chrome} — recriando o navegador em vez de insistir no login.")
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
        except Exception as erro:
            if _erro_chrome_morto(erro):
                raise
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


_ULTIMO_CONJUNTO_SENHAS = None   # conjunto avaliado no ciclo anterior (log só quando muda)
_AVISO_PAUSA_ATE = None


def verificar_botao_explicacao(textos=None, fechar=True):
    """Avalia as senhas do modal. `textos` vem do ciclo leve (JS) ou da API (modo
    requisição); sem ele, procura os botões pelo caminho antigo (XPath + polling).
    fechar=False (modo requisição): não manda ESC para o Chrome (não há modal)."""
    global _TICKET_ALARME, _ULTIMO_CONJUNTO_SENHAS, _AVISO_PAUSA_ATE
    cfg = carregar_filtros()

    if textos is None:
        senhas = [texto for texto, _elemento in _senhas_na_tela(cfg, timeout=8.0)]
    else:
        senhas, vistos = [], set()
        for texto in textos:
            texto = str(texto or "").strip()
            chave = normalizar_texto(texto)
            if texto and chave not in vistos and _texto_parece_senha(texto, cfg):
                vistos.add(chave)
                senhas.append(texto)

    conjunto = tuple(sorted(normalizar_texto(t) for t in senhas))
    mudou = conjunto != _ULTIMO_CONJUNTO_SENHAS
    _ULTIMO_CONJUNTO_SENHAS = conjunto

    if not senhas:
        if mudou:
            log_msg("🔎 Nenhuma senha das listas no modal.")
        # Nenhuma senha no modal: se o alarme estava tocando, a senha saiu da fila
        if alarme_ativo:
            log_msg("🔇 A senha saiu da tela — desarmando o alarme.")
            na_ui(desarmar_alarme, False)
        if fechar:
            fechar_modal()
        return

    a_alertar = []
    try:
        for texto in senhas:
            disparar, motivo = avaliar_chamada(texto)
            if mudou:
                log_msg(f"🔎 Senha '{texto}' -> {motivo}")
            if disparar:
                a_alertar.append(texto)

        if a_alertar:
            if datetime.datetime.now() > pausa_ate:
                with _TRAVA_ALARME:
                    if not alarme_ativo:
                        log_msg(f"🚨 Senha(s) para alertar na tela: {', '.join(a_alertar)}")
                    _TICKET_ALARME = a_alertar[0]
                disparar_estado_alarme()
            elif _AVISO_PAUSA_ATE != pausa_ate:
                _AVISO_PAUSA_ATE = pausa_ate
                log_msg(f"Ignorando chamada. Dentro do período de pausa até {pausa_ate.strftime('%H:%M:%S')}.")
        elif alarme_ativo:
            log_msg("🔇 Nenhuma senha para alertar na tela — desarmando o alarme.")
            na_ui(desarmar_alarme, False)
    except Exception as e:
        log_msg(f"⚠️ Falha ao avaliar as senhas: {e}")
    finally:
        if fechar:
            fechar_modal()


# --- Ciclo leve: clique + leitura do modal num único JavaScript -------------------
# Antes: WebDriverWait + clique + polling de até 8 s com dezenas de chamadas ao
# Selenium por ciclo. Agora um execute_async_script clica no "Chamar senha",
# espera o modal com MutationObserver (no máximo ~3 s) e devolve os textos dos
# botões. Se não der certo, o caminho antigo continua lá (fallback).
JS_CICLO_LEVE = r"""
var done = arguments[arguments.length - 1];
var termos = arguments[0] || [];
var limiteMs = arguments[1] || 3000;
var t0 = Date.now();
function maiusculas(t) { return (t || '').replace(/[a-z]/g, function (c) { return c.toUpperCase(); }); }
function visivel(el) {
    if (!el.getClientRects().length) return false;
    for (var n = el; n && n.nodeType === 1; n = n.parentElement) {
        var s = window.getComputedStyle(n);
        if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity || '1') === 0) return false;
    }
    return true;
}
function candidatos() {
    var r = [], bs = document.getElementsByTagName('button');
    for (var i = 0; i < bs.length; i++) {
        var b = bs[i];
        var norm = maiusculas((b.textContent || '').replace(/\s+/g, ' ').trim());
        if (!norm) continue;
        var casou = false;
        for (var j = 0; j < termos.length; j++) { if (norm.indexOf(termos[j]) >= 0) { casou = true; break; } }
        if (!casou || !visivel(b)) continue;
        var texto = (b.innerText || b.textContent || '').replace(/\s+/g, ' ').trim();
        if (texto) r.push(texto);
    }
    return r;
}
var botao = document.querySelector('button.callTicketBtn');
if (!botao) { done({ok: false, erro: 'sem_botao'}); return; }
if (botao.disabled || !visivel(botao)) { done({ok: false, erro: 'botao_indisponivel'}); return; }
var antes = candidatos().join('\n'), ultimo = antes, mutacoes = 0, terminou = false;
var obs = null, timerFim = null, timerCalmo = null;
function finalizar(motivo) {
    if (terminou) return;
    terminou = true;
    if (obs) obs.disconnect();
    clearTimeout(timerFim); clearTimeout(timerCalmo);
    done({ok: true, textos: candidatos(), motivo: motivo, mutacoes: mutacoes, ms: Date.now() - t0});
}
function conferir() {
    var agora = candidatos(), chave = agora.join('\n');
    if (agora.length && chave !== antes && chave !== ultimo) {
        ultimo = chave;
        clearTimeout(timerCalmo);
        timerCalmo = setTimeout(function () { finalizar('modal'); }, 250);  // espera a lista assentar
    }
}
obs = new MutationObserver(function (lista) { mutacoes += lista.length; conferir(); });
obs.observe(document.body, {childList: true, subtree: true, attributes: true,
                            attributeFilter: ['style', 'class', 'hidden', 'aria-hidden']});
timerFim = setTimeout(function () { finalizar('limite'); }, limiteMs);
try { botao.click(); } catch (e) {
    terminou = true; obs.disconnect(); clearTimeout(timerFim);
    done({ok: false, erro: 'clique: ' + e}); return;
}
conferir();
"""
CICLO_LEVE_LIMITE_MS = 3000
_CICLO_LEVE = {"desligado": False, "falhas": 0, "ok": 0, "ms": 0}


def ciclo_leve(cfg):
    """Lista de textos dos botões de senha visíveis depois do clique, ou None
    quando o caminho leve não serviu neste ciclo (aí usa o caminho antigo)."""
    termos = [str(t).strip().upper() for t in (cfg.get("termos_gatilho") or []) if str(t).strip()]
    try:
        resultado = driver.execute_async_script(JS_CICLO_LEVE, termos, CICLO_LEVE_LIMITE_MS)
    except Exception as e:
        if _erro_chrome_morto(e):
            raise
        _falha_ciclo_leve(f"{type(e).__name__}")
        return None
    if not isinstance(resultado, dict):
        _falha_ciclo_leve("resposta inesperada")
        return None
    if not resultado.get("ok"):
        return None   # sem botão / botão ainda carregando: o caminho antigo espera até 12 s
    if not resultado.get("mutacoes"):
        _falha_ciclo_leve("o clique não mudou nada na página")
        return None
    _CICLO_LEVE["falhas"] = 0
    _CICLO_LEVE["ok"] += 1
    _CICLO_LEVE["ms"] = int(resultado.get("ms") or 0)
    return [str(t) for t in (resultado.get("textos") or [])]


def _falha_ciclo_leve(motivo):
    _CICLO_LEVE["falhas"] += 1
    if _CICLO_LEVE["falhas"] >= 3 and not _CICLO_LEVE["desligado"]:
        _CICLO_LEVE["desligado"] = True
        log_msg(f"ℹ️ Ciclo leve desligado até reabrir o Chrome ({motivo}, 3 vezes seguidas) — "
                "usando o método antigo (clique + busca pelos botões).")


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
    _CICLO_LEVE.update(desligado=False, falhas=0)

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
            textos = None
            if cfg.get("ciclo_leve", True) and not _CICLO_LEVE["desligado"]:
                textos = ciclo_leve(cfg)
            if textos is None:
                botao = WebDriverWait(driver, 12).until(
                    EC.element_to_be_clickable((By.XPATH, XPATH_BOTAO_CHAMAR)))
                botao.click()
                verificar_botao_explicacao()
            else:
                verificar_botao_explicacao(textos)
            if falhas:
                log_msg(f"✅ Monitoramento normalizado depois de {falhas} falha(s).")
            falhas = 0
            registrar_batimento(ok=True)
            _atualizar_status("ok", "Conectado")
        except Exception as e:
            if _erro_chrome_morto(e):
                return f"Chrome parou de responder ({type(e).__name__})"
            vivo, motivo_chrome = chrome_vivo(ping=False)
            if not vivo:
                return f"Chrome ausente ({motivo_chrome})"
            falhas += 1
            estado, detalhe = diagnosticar_pagina()
            motivo = tratar_falha(estado, detalhe, falhas, url_lista)
            if motivo == PARAR_MONITORAMENTO:
                return None
            if motivo:
                return motivo
            if estado == "login":
                falhas = 0
                ultimo_reload = time.time()
            _dormir(5)
            continue

        # Fora do horário de funcionamento: fecha o Chrome e espera (só sem alarme)
        if not alarme_ativo and not dentro_do_horario(cfg):
            return MOTIVO_FORA_HORARIO

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
            motivo_preventivo = motivo_reinicio_preventivo(cfg, inicio_chrome)
            if motivo_preventivo:
                return motivo_preventivo

        _dormir(cfg["intervalo_busca_segundos"])
    return None


# ==============================================================================
# 🌐 MODO REQUISIÇÃO (2.3.0): a fila vem direto da API do Feegow
# ==============================================================================
# O modal "Chamar senha" do Feegow só faz um GET JSON em
#   https://core.feegow.com/totem-queue/admin/get-queue-by-filter?filter=
# com UM header, x-access-token = sessionStorage["tk"] (JWT do login, ~12 h).
# Cada ticket traz NomeTipo (o texto do botão "Tipo" do modal), id, Sigla, Senha...
# No modo "http" o Selenium continua fazendo o login; depois disso o token é lido,
# o Chrome é FECHADO (libera RAM) e o laço é urllib puro: 1 requisição a cada
# intervalo_requisicao_seg, nunca em paralelo. Os NomeTipo passam pela MESMA
# avaliação do modo Chrome (verificar_botao_explicacao): termos_gatilho,
# _texto_parece_senha, whitelist > blacklist, filtro/fala, janela e voz.
URL_API_FILA = "https://core.feegow.com/totem-queue/admin/get-queue-by-filter?filter="
VOLTAR_PARA_CHROME = "__voltar_para_chrome__"   # sessao_modo_requisicao: usar o modo antigo
HTTP_TIMEOUT_SEGUNDOS = 15
HTTP_ESPERA_MAXIMA = 60             # espera progressiva 10 -> 20 -> 40 -> 60 s
HTTP_MAX_SEM_OK_SEGUNDOS = 180      # 3 min sem resposta boa = reinicia o programa
HTTP_MAX_RELOGINS_FALHOS = 2        # relogin falhou 2x seguidas = reinicia o programa
HTTP_RENOVAR_ANTES_SEGUNDOS = 600   # renova o token quando faltar 10 min para vencer
HTTP_TAMANHO_MAXIMO = 5_000_000
_HTTP = {"ativo": False, "desligado": "", "token_exp": 0.0, "ciclos_ok": 0, "ultimo_ms": 0,
         "falhas_seguidas": 0, "formato_invalido": 0, "relogins_falhos": 0,
         "chrome_dispensado": False, "pedido_mostrar": False, "requisicoes": 0,
         "ultimo_corpo": None}


class SessaoExpirada(Exception):
    """401/403, SESSION_EXPIRED, HTML/login no lugar do JSON ou token ausente: relogar na hora."""


class FormatoInesperado(Exception):
    """A API respondeu, mas sem a lista de tickets / sem NomeTipo."""


def validade_token(token):
    """Timestamp do "exp" do JWT (0 = não deu para ler). Não valida a assinatura."""
    try:
        parte = str(token).split(".")[1]
        parte += "=" * (-len(parte) % 4)
        return float(json.loads(base64.urlsafe_b64decode(parte.encode("ascii")))["exp"])
    except Exception:
        return 0.0


def _token_serve(token, evitar=""):
    if not token or token == evitar:
        return False
    exp = validade_token(token)
    return not exp or exp - time.time() > HTTP_RENOVAR_ANTES_SEGUNDOS + 60


def espera_progressiva(base, falhas):
    """Espera depois de `falhas` erros seguidos de rede/servidor: 10, 20, 40, 60, 60..."""
    teto = max(HTTP_ESPERA_MAXIMA, base)
    return min(teto, base * (2 ** max(0, int(falhas) - 1)))


def _lista_da_resposta(dados):
    """A lista de tickets da resposta, ou FormatoInesperado/SessaoExpirada."""
    if isinstance(dados, dict):
        if str(dados.get("code") or "").upper() == "SESSION_EXPIRED":
            raise SessaoExpirada("SESSION_EXPIRED")
        lista = next((dados[k] for k in ("data", "tickets", "queue", "result", "results")
                      if isinstance(dados.get(k), list)), None)
        if lista is None:
            raise FormatoInesperado(f"objeto sem lista de senhas (chaves: {', '.join(sorted(map(str, dados))[:6])})")
    elif isinstance(dados, list):
        lista = dados
    else:
        raise FormatoInesperado(f"resposta do tipo {type(dados).__name__}")
    if lista and not any(isinstance(t, dict) and "NomeTipo" in t for t in lista):
        raise FormatoInesperado("senhas sem o campo NomeTipo")
    return lista


def http_buscar_fila(token, timeout=HTTP_TIMEOUT_SEGUNDOS, registro=None):
    """GET da fila. SessaoExpirada = relogar; FormatoInesperado = API mudou;
    qualquer outro erro (timeout, 5xx, 429...) sobe como está (espera progressiva).
    `registro` (dict), se vier, recebe status, bytes e corpo da resposta (para o log)."""
    import urllib.error
    registro = registro if registro is not None else {}
    if not token:
        registro["status"] = "sem token"
        raise SessaoExpirada("token ausente")
    pedido = urllib.request.Request(URL_API_FILA, headers={
        "x-access-token": token, "Accept": "application/json",
        "Origin": "https://app.feegow.com", "Referer": "https://app.feegow.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            final = (resposta.geturl() or "").lower()
            tipo = (resposta.headers.get("content-type") or "").lower()
            corpo = resposta.read(HTTP_TAMANHO_MAXIMO)
            registro.update(status=getattr(resposta, "status", 200), bytes=len(corpo), corpo=corpo)
    except urllib.error.HTTPError as erro:
        try:
            corpo_erro = erro.read(20_000) or b""
        except Exception:
            corpo_erro = b""
        registro.update(status=erro.code, bytes=len(corpo_erro), corpo=corpo_erro)
        if erro.code in (401, 403):
            raise SessaoExpirada(f"HTTP {erro.code}") from None
        raise
    if "/sso" in final or "login" in final:
        raise SessaoExpirada("a API redirecionou para o login")
    if "html" in tipo or corpo.lstrip()[:1] == b"<":
        raise SessaoExpirada("veio HTML no lugar do JSON")
    try:
        dados = json.loads(corpo.decode("utf-8", errors="replace"))
    except ValueError:
        raise FormatoInesperado("a resposta não é JSON") from None
    return _lista_da_resposta(dados)


_RE_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_RE_TELEFONE = re.compile(r"\(?\b\d{2}\)?\s?9?\d{4}-\d{4}\b")
_RE_NUMERO_LONGO = re.compile(r"\b\d{11}\b|\b\d{15}\b")   # CPF/telefone sem pontuação, CNS
_CHAVES_SENSIVEIS = ("paciente", "cpf", "telefone", "celular", "fone", "whats", "email", "e_mail",
                     "cns", "nascimento", "rg", "mae", "responsavel", "endereco", "documento")
_NOMES_QUE_NAO_SAO_PESSOA = ("tipo", "fila", "unidade", "guiche", "local", "sala", "setor", "status")


def _chave_sensivel(chave):
    c = normalizar_texto(chave).lower().replace(" ", "_")
    compacta = c.replace("_", "")
    if any(p in compacta for p in _CHAVES_SENSIVEIS):
        return True
    return ("nome" in compacta or compacta in ("name", "fullname")) and not any(
        p in compacta for p in _NOMES_QUE_NAO_SAO_PESSOA)


def _iniciais(valor):
    """"Maria da Silva" -> "M.d.S."; número/CPF/telefone -> "***"."""
    if valor is None or valor == "":
        return valor
    palavras = [p for p in re.split(r"[\s.]+", str(valor)) if p]
    if not any(ch.isalpha() for ch in str(valor)):
        return "***"
    return "".join(p[0] + "." for p in palavras if p[0].isalpha()) or "***"


def mascarar_dados_paciente(dados):
    """Cópia da resposta com nome/CPF/telefone/e-mail... trocados pelas iniciais."""
    if isinstance(dados, dict):
        return {k: (_iniciais(v) if _chave_sensivel(k) and not isinstance(v, (dict, list))
                    else mascarar_dados_paciente(v)) for k, v in dados.items()}
    if isinstance(dados, list):
        return [mascarar_dados_paciente(v) for v in dados]
    if isinstance(dados, str):
        texto = _RE_CPF.sub("***", dados)
        texto = _RE_TELEFONE.sub("***", texto)
        return _RE_NUMERO_LONGO.sub("***", texto)
    return dados


def _corpo_para_log(corpo, limite=2000):
    """JSON compacto e mascarado (ou o texto cru mascarado), truncado em `limite`."""
    texto = (corpo or b"").decode("utf-8", errors="replace")
    try:
        texto = json.dumps(mascarar_dados_paciente(json.loads(texto)), ensure_ascii=False,
                           separators=(",", ":"))
    except ValueError:
        texto = mascarar_dados_paciente(" ".join(texto.split()))
    if len(texto) > limite:
        texto = texto[:limite] + f"... (+{len(texto) - limite} caracteres)"
    return texto


def _hora_chegada(valor):
    achado = re.search(r"(\d{1,2}):(\d{2})", str(valor or ""))
    return f"{int(achado.group(1)):02d}:{achado.group(2)}" if achado else "--:--"


def resumo_senhas(fila):
    """[<Sigla><Senha> <NomeTipo> <HH:MM da chegada> (id N)], ... — sem dado de paciente."""
    partes = []
    for t in fila:
        if not isinstance(t, dict):
            continue
        senha = f"{t.get('Sigla') or ''}{t.get('Senha') or ''}".strip() or "?"
        tipo = " ".join(str(t.get("NomeTipo") or "").split())
        partes.append(f"[{senha} {tipo} {_hora_chegada(t.get('DataHoraChegada'))} (id {t.get('id')})]")
    return ", ".join(partes)


def log_requisicao(ciclo, registro, ms, fila=None, erro=None):
    """← resposta no log (o → GET é logado antes de enviar). O corpo vai quando
    mudou em relação à resposta anterior ou quando é erro/formato inesperado."""
    status = registro.get("status")
    if status is None:
        status = f"erro ({_descrever_erro_http(erro)})" if erro is not None else "?"
    tamanho = registro.get("bytes")
    linha = f"← {status} em {ms} ms" + (f", {tamanho} B" if tamanho is not None else "")
    if fila is not None:
        linha += f", {len(fila)} senha(s)" + (f": {resumo_senhas(fila)}" if fila else "")
    elif erro is not None and registro.get("status") is not None:
        linha += f" ({type(erro).__name__}: {str(erro)[:100]})"
    log_msg(linha)
    corpo = registro.get("corpo")
    if corpo is None:
        return
    assinatura = hashlib.sha256(corpo).hexdigest()
    if erro is not None or assinatura != _HTTP["ultimo_corpo"]:
        log_msg(f"   corpo: {_corpo_para_log(corpo)}")
    _HTTP["ultimo_corpo"] = assinatura if erro is None else None


def _maiusculas_js(texto):
    """Igual ao maiusculas() do JS_CICLO_LEVE: só a-z vira A-Z (acentos ficam)."""
    return "".join(c.upper() if "a" <= c <= "z" else c for c in texto)


def textos_da_fila(fila, cfg):
    """NomeTipo de cada ticket que o modo Chrome também pegaria: o mesmo filtro dos
    termos_gatilho que o JS_CICLO_LEVE aplica aos botões do modal."""
    termos = [str(t).strip().upper() for t in (cfg.get("termos_gatilho") or []) if str(t).strip()]
    textos = []
    for ticket in fila:
        if not isinstance(ticket, dict):
            continue
        texto = " ".join(str(ticket.get("NomeTipo") or "").split())
        if texto and any(termo in _maiusculas_js(texto) for termo in termos):
            textos.append(texto)
    return textos


def _registrar_ids_novos(fila, anteriores, cfg):
    """Loga cada senha NOVA (id que não estava no ciclo anterior) das listas do filtro.
    Só o tipo da senha vai para o log, nunca dado de paciente."""
    atuais = {}
    for ticket in fila:
        if isinstance(ticket, dict) and ticket.get("id") is not None:
            atuais[str(ticket.get("id"))] = " ".join(str(ticket.get("NomeTipo") or "").split())
    if anteriores is not None:
        for id_ticket, texto in atuais.items():
            if id_ticket not in anteriores and texto and _texto_parece_senha(texto, cfg):
                log_msg(f"🆕 Senha nova na fila (id {id_ticket}): {texto}")
    return set(atuais)


def ler_token_driver(timeout=15.0):
    """sessionStorage["tk"] da aba aberta (espera aparecer). "" quando não veio."""
    fim = time.time() + timeout
    while True:
        try:
            token = driver.execute_script(
                "return sessionStorage.getItem('tk') || localStorage.getItem('tk') || '';")
        except Exception as erro:
            if _erro_chrome_morto(erro):
                raise
            token = ""
        token = str(token or "").strip().strip('"')
        if token or _ENCERRANDO or time.time() >= fim:
            return token
        time.sleep(0.5)


def _quer_chrome_visivel():
    """O dono pediu para ver a Lista de Espera (bandeja -> Mostrar, com o modo requisição
    já rodando) e o painel continua visível. Painel visível no início não conta: depois
    do login o Chrome fecha sempre (2.3.2)."""
    return bool(is_visible) and _HTTP["pedido_mostrar"] and not _HTTP["chrome_dispensado"]


def fechar_chrome_modo_requisicao():
    """driver.quit + restos DESTE perfil e DESTE chromedriver (pelo PID)."""
    if driver is None and not _PID_CHROMEDRIVER:
        return
    _fechar_chrome()
    log_msg("🌐 Chrome fechado (modo requisição).")


def obter_token_via_chrome(evitar=""):
    """Abre o Chrome (se preciso), reaproveita a sessão ou faz o login e lê o token.
    Fecha o Chrome no fim, a não ser que o painel esteja visível. "" = falhou.
    O cronômetro de login (2.3.3) começa aqui e só para quando o token chega."""
    iniciar_cronometro_login()
    token = _obter_token_via_chrome(evitar)
    if token:
        parar_cronometro_login()
    return token


def _obter_token_via_chrome(evitar=""):
    url_lista = carregar_credenciais()["url_lista_espera"]
    try:
        if driver is None or not chrome_vivo(ping=False)[0]:
            _fechar_chrome()
            if not _abrir_chrome():
                return ""
        abrir_lista_espera(url_lista)
        if _sessao_ativa(driver):
            token = ler_token_driver(timeout=8)
            if _token_serve(token, evitar):
                return token
        if not fazer_login():
            return ""
        abrir_lista_espera(url_lista)
        return ler_token_driver()
    except Exception as erro:
        log_msg(f"⚠️ Modo requisição: não consegui ler o token no Chrome ({type(erro).__name__}).")
        return ""
    finally:
        if not _quer_chrome_visivel():
            fechar_chrome_modo_requisicao()


def _sincronizar_chrome_painel(token):
    """Painel oculto = Chrome fechado. Painel visível = Chrome maximizado na Lista de
    Espera (regra 2.2.1), só para o dono ver; a busca continua por requisição.
    Devolve o token (o novo, se a janela aberta trouxe outro)."""
    if not is_visible:
        _HTTP["chrome_dispensado"] = False
        _HTTP["pedido_mostrar"] = False
    if driver is not None:
        if not chrome_vivo(ping=False)[0]:
            log_msg("🌐 O Chrome foi fechado — o modo requisição segue sem ele.")
            _fechar_chrome()
            _HTTP["chrome_dispensado"] = bool(is_visible)
        elif not _quer_chrome_visivel():
            fechar_chrome_modo_requisicao()
        return token
    if not _quer_chrome_visivel():
        return token
    log_msg("🖥️ Painel visível — abrindo o Chrome maximizado na Lista de Espera "
            "(a busca continua por requisição).")
    url_lista = carregar_credenciais()["url_lista_espera"]
    try:
        if not _abrir_chrome():
            _HTTP["chrome_dispensado"] = True
            return token
        abrir_lista_espera(url_lista)
        if not _sessao_ativa(driver):
            _definir_etapa("login")
            ok = fazer_login()
            _definir_etapa("monitorando")
            if not ok:
                return token
            abrir_lista_espera(url_lista)
        novo = ler_token_driver(timeout=8)
    except Exception as erro:
        log_msg(f"⚠️ Não consegui abrir a Lista de Espera no Chrome ({type(erro).__name__}).")
        return token
    if novo and novo != token and validade_token(novo) >= validade_token(token):
        _HTTP["token_exp"] = validade_token(novo)
        return novo
    return token


def _reiniciar_pela_escada(motivo):
    """N3 pelo mesmo caminho da 2.2.2 (reiniciar_processo; desde a 2.3.3 sem teto por hora,
    só o intervalo mínimo de 30 s).
    Nunca com alarme/voz e nunca sem internet. True = reiniciou."""
    if _ENCERRANDO:
        return False
    if alarme_ativo or _TRAVA_AUDIO.locked():
        _avisar_recuperacao(f"⏳ [N3] Reinício do programa adiado: {motivo} — esperando o alarme/a fala terminar.")
        return False
    if not internet_ok():
        _avisar_recuperacao("📴 [N3] Falhando, mas sem internet — não reinicio (espero a conexão voltar).",
                            "offline", "Sem internet — aguardando")
        return False
    log_msg(f"🪜 [N3] Escada de recuperação (modo requisição): {motivo}. Reiniciando o programa inteiro.")
    return reiniciar_processo(motivo)


def _relogar_http(erro, token):
    """Sessão expirada: reloga SEMPRE na hora (sem limite por hora, decisão do dono na
    2.3.1). A única proteção é a escada: 2 relogins falhos seguidos -> reinício.
    ("ok", novo) | ("falhou", token) | ("parar", token)."""
    log_msg(f"🔐 [N1] Modo requisição: sessão do Feegow expirada ({erro}) — entrando de novo agora.")
    _definir_etapa("login")
    _atualizar_status("recuperando", "Sessão caiu — entrando de novo...")
    novo = obter_token_via_chrome(evitar=token)
    if _CRED_RECUSADAS or credenciais_incompletas():
        _definir_etapa("aguardando_login")
        _atualizar_status("falha", "Login recusado — confira as credenciais")
        return "parar", token
    _definir_etapa("monitorando")
    if novo and novo != token:
        _HTTP["relogins_falhos"] = 0
        _HTTP["token_exp"] = validade_token(novo)
        log_msg("✔️ Relogin OK — repetindo a requisição.")
        return "ok", novo
    _HTTP["relogins_falhos"] += 1
    falhos = _HTTP["relogins_falhos"]
    log_msg(f"⚠️ O relogin não trouxe um token novo ({falhos}/{HTTP_MAX_RELOGINS_FALHOS} seguidos).")
    if falhos >= HTTP_MAX_RELOGINS_FALHOS:
        if _reiniciar_pela_escada(f"relogin falhou {falhos}x seguidas no modo requisição"):
            return "parar", token
    return "falhou", token


def _descrever_erro_http(erro):
    codigo = getattr(erro, "code", None)
    if isinstance(codigo, int):
        return f"HTTP {codigo}"
    return f"{type(erro).__name__}: {str(erro)[:100]}"


def sessao_modo_requisicao():
    """Chamado logo depois do login (Chrome aberto e logado). Devolve como o
    verificar_botao_chamar_senha (motivo para reabrir / None para parar) ou
    VOLTAR_PARA_CHROME (usar o modo antigo nesta sessão)."""
    token = ""
    try:
        abrir_lista_espera(carregar_credenciais()["url_lista_espera"])
        token = ler_token_driver()
    except Exception as erro:
        log_msg(f"⚠️ Modo requisição: falha ao ler o token depois do login ({type(erro).__name__}).")
    if token:
        parar_cronometro_login()   # modo requisição: token obtido = login OK
    else:
        log_msg("⚠️ Modo requisição: o token (sessionStorage 'tk') não apareceu depois do login — vou relogar.")
    return laco_http(token)


def laco_http(token):
    """Laço do modo requisição. Recuperação:
      * sessão expirada (401/403/SESSION_EXPIRED/HTML/token ausente) -> reloga na hora
        e repete; 2 relogins seguidos sem token novo -> reinicia o programa (N3);
      * rede/servidor (timeout, 5xx, 429) -> espera 10 -> 20 -> 40 -> 60 s, volta a
        10 no primeiro sucesso; 3 min sem resposta boa -> reinicia o programa (N3);
      * formato inesperado N vezes seguidas -> VOLTAR_PARA_CHROME;
      * token a menos de 10 min de vencer -> renova antes."""
    global _PEDIDO_RECARGA
    cfg = carregar_filtros()
    _HTTP.update(ativo=True, falhas_seguidas=0, formato_invalido=0, relogins_falhos=0,
                 chrome_dispensado=False, pedido_mostrar=False, ultimo_corpo=None,
                 token_exp=validade_token(token))
    if token:
        validade = (datetime.datetime.fromtimestamp(_HTTP["token_exp"]).strftime("%d/%m %H:%M")
                    if _HTTP["token_exp"] else "?")
        log_msg(f"🌐 Modo requisição ligado: a fila vem direto da API do Feegow a cada "
                f"{cfg['intervalo_requisicao_seg']} s (token válido até {validade}). O Chrome fecha "
                "agora e só abre para relogar ou quando pedir pela bandeja (Mostrar).")
    token = _sincronizar_chrome_painel(token)
    _definir_etapa("monitorando")
    registrar_batimento(ok=False)
    anteriores = None
    ultimo_bom = time.time()
    ultima_tentativa_n3 = 0.0
    proxima_renovacao = 0.0
    try:
        while not _ENCERRANDO:
            if _PEDIDO_REINICIO_CHROME:
                return f"pedido do watchdog ({_PEDIDO_REINICIO_CHROME})"
            _PEDIDO_RECARGA = False   # não há página para recarregar
            cfg = carregar_filtros()
            if cfg.get("modo_busca") != "http":
                log_msg("🔁 modo_busca = 'chrome' no filtros.json — voltando ao método do Chrome.")
                return VOLTAR_PARA_CHROME
            base = cfg["intervalo_requisicao_seg"]

            agora = time.time()
            if agora - ultimo_bom >= HTTP_MAX_SEM_OK_SEGUNDOS and agora - ultima_tentativa_n3 >= 60:
                ultima_tentativa_n3 = agora
                if _reiniciar_pela_escada(f"modo requisição sem resposta boa há {int(agora - ultimo_bom)} s"):
                    return None

            token = _sincronizar_chrome_painel(token)
            exp = validade_token(token)
            if token and exp and exp - time.time() < HTTP_RENOVAR_ANTES_SEGUNDOS and time.time() >= proxima_renovacao:
                log_msg("🔑 O token do Feegow vence em menos de 10 min — renovando agora.")
                _definir_etapa("login")
                novo = obter_token_via_chrome(evitar=token)
                if _CRED_RECUSADAS:
                    _definir_etapa("aguardando_login")
                    return None
                _definir_etapa("monitorando")
                if novo:
                    token = novo
                    _HTTP["token_exp"] = validade_token(novo)
                    log_msg("✔️ Token renovado.")
                else:
                    proxima_renovacao = time.time() + 120
                    log_msg("⚠️ Não consegui renovar o token agora — sigo com o atual (se vencer, reloga).")

            t0 = time.time()
            registro = {}
            logar = cfg.get("log_requisicoes", True)
            _HTTP["requisicoes"] += 1
            if logar:
                log_msg(f"→ GET {URL_API_FILA} (ciclo {_HTTP['requisicoes']})")
            try:
                fila = http_buscar_fila(token, registro=registro)
            except Exception as erro:
                if logar:
                    log_requisicao(_HTTP["requisicoes"], registro, int((time.time() - t0) * 1000), erro=erro)
                erro_requisicao = erro
            else:
                erro_requisicao = None
                if logar:
                    log_requisicao(_HTTP["requisicoes"], registro, int((time.time() - t0) * 1000), fila=fila)
            try:
                if erro_requisicao is not None:
                    raise erro_requisicao
            except SessaoExpirada as erro:
                acao, token = _relogar_http(erro, token)
                if acao == "parar":
                    return None
                if acao == "falhou":
                    _dormir(base, progresso=True)
                continue
            except FormatoInesperado as erro:
                _HTTP["formato_invalido"] += 1
                limite = cfg["http_ciclos_formato_invalido"]
                log_msg(f"⚠️ Modo requisição: resposta da API fora do formato esperado ({erro}) — "
                        f"{_HTTP['formato_invalido']}/{limite}.")
                if _HTTP["formato_invalido"] >= limite:
                    motivo = f"a API da fila mudou de formato ({erro})"
                    _HTTP["desligado"] = motivo
                    log_msg(f"🔁 {motivo}: voltando sozinho para o modo Chrome (clique no 'Chamar senha') "
                            "até o próximo reinício do programa.")
                    _avisar_bandeja("O Feegow mudou a resposta da fila de senhas. O monitor voltou para o "
                                    "modo Chrome e continua funcionando.")
                    return VOLTAR_PARA_CHROME
                _dormir(base, progresso=True)
                continue
            except Exception as erro:
                _HTTP["falhas_seguidas"] += 1
                espera = espera_progressiva(base, _HTTP["falhas_seguidas"])
                log_msg(f"⚠️ Modo requisição: falha de rede/servidor ({_descrever_erro_http(erro)}) — "
                        f"{_HTTP['falhas_seguidas']} seguida(s), {int(time.time() - ultimo_bom)} s sem "
                        f"resposta boa; nova tentativa em {int(espera)} s.")
                _atualizar_status("recuperando", "Sem resposta do Feegow — tentando de novo")
                if not internet_ok():
                    aguardar_internet()
                    ultimo_bom = time.time()   # o tempo sem internet não conta para o N3
                    continue
                _dormir(espera, progresso=True)
                continue

            ms = int((time.time() - t0) * 1000)
            if _HTTP["falhas_seguidas"] or _HTTP["formato_invalido"]:
                log_msg(f"✅ Modo requisição normalizado depois de "
                        f"{_HTTP['falhas_seguidas'] + _HTTP['formato_invalido']} falha(s).")
            _HTTP.update(falhas_seguidas=0, formato_invalido=0, relogins_falhos=0, ultimo_ms=ms)
            _HTTP["ciclos_ok"] += 1
            ultimo_bom = time.time()
            anteriores = _registrar_ids_novos(fila, anteriores, cfg)
            verificar_botao_explicacao(textos_da_fila(fila, cfg), fechar=False)
            registrar_batimento(ok=True)
            _atualizar_status("ok", "Conectado")

            if not alarme_ativo and not dentro_do_horario(cfg):
                return MOTIVO_FORA_HORARIO
            _dormir(max(0.0, base - (time.time() - t0)))   # sequencial: nunca em paralelo
        return None
    finally:
        _HTTP["ativo"] = False


MOTIVO_FORA_HORARIO = "fora do horário de funcionamento"
_RAM = {"medido_em": 0.0, "mb": 0}


def memoria_chrome_mb():
    """Memória privada da árvore chromedriver -> Chrome deste monitor (MB). 0 = não deu para medir."""
    pid = _PID_CHROMEDRIVER
    if not pid:
        return 0
    try:
        raiz = psutil.Process(pid)
        total = 0
        for processo in [raiz] + raiz.children(recursive=True):
            try:
                info = processo.memory_info()
                total += getattr(info, "private", 0) or info.rss
            except Exception:
                continue
        return int(total / (1024 * 1024))
    except Exception:
        return 0


def motivo_reinicio_preventivo(cfg, inicio_chrome, agora=None):
    """Motivo para reabrir o Chrome por prevenção (vazamento de memória do Feegow
    depois de horas aberto), ou "" para seguir."""
    agora = agora or time.time()
    horas = cfg.get("watchdog_reiniciar_chrome_horas") or 0
    if horas and agora - inicio_chrome > horas * 3600:
        return f"reinício preventivo do Chrome (a cada {horas} h)"
    hora = cfg.get("chrome_reinicio_diario_hora") or ""
    if hora and agora - inicio_chrome > 3600:
        h, m = map(int, hora.split(":"))
        momento = datetime.datetime.fromtimestamp(agora)
        alvo = momento.replace(hour=h, minute=m, second=0, microsecond=0)
        if alvo > momento:
            alvo -= datetime.timedelta(days=1)   # última vez que o relógio passou pela hora
        if inicio_chrome < alvo.timestamp():
            return f"reinício diário do Chrome ({hora})"
    limite = cfg.get("chrome_limite_ram_mb") or 0
    if limite and agora - _RAM["medido_em"] > 300:
        _RAM["medido_em"] = agora
        _RAM["mb"] = memoria_chrome_mb()
        if _RAM["mb"] > limite:
            return f"o Chrome está usando {_RAM['mb']} MB (limite {limite} MB)"
    return ""


def _aguardar_horario_funcionamento():
    """Fora do horário: Chrome fechado, só espera (o watchdog não age nesta etapa)."""
    horario = carregar_filtros().get("horario_funcionamento") or {}
    log_msg(f"🌙 Fora do horário de funcionamento ({horario.get('inicio')}–{horario.get('fim')}, "
            f"{', '.join(horario.get('dias') or [])}) — navegador fechado, aguardando.")
    _definir_etapa("fora_horario")
    _atualizar_status("offline", "Fora do horário de funcionamento")
    while not _ENCERRANDO and not dentro_do_horario():
        _dormir(30, progresso=True)
    if not _ENCERRANDO:
        log_msg("☀️ Dentro do horário de funcionamento — abrindo o monitor.")


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


PARAR_MONITORAMENTO = "__parar_monitoramento__"   # tratar_falha: encerra a sessão sem reabrir


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
        if not _relogin_permitido():
            return PARAR_MONITORAMENTO
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


def _relogin_permitido():
    """Conta um relogin por "sessão caiu". Passou de max_relogins_por_hora: para de
    relogar por 60 min (outro PC com o mesmo usuário?) e devolve False."""
    global _RELOGIN_BLOQUEADO_ATE
    agora = time.time()
    _RELOGINS[:] = [t for t in _RELOGINS if agora - t < 3600]
    maximo = carregar_filtros().get("max_relogins_por_hora", 3)
    if len(_RELOGINS) >= maximo:
        _RELOGIN_BLOQUEADO_ATE = agora + 3600
        log_msg(f"🛑 A sessão do Feegow caiu {len(_RELOGINS) + 1} vezes na última hora "
                f"(limite {maximo}). Outro PC usando o mesmo usuário? Parei de relogar para não "
                "ficar derrubando a outra máquina. Tento de novo em 60 min — ou salve o login "
                "na janela ⚙️ para tentar já.")
        _atualizar_status("falha", "Sessão derrubada várias vezes — outro PC com o mesmo usuário?")
        _avisar_bandeja("A sessão do Feegow foi derrubada várias vezes nesta hora. "
                        "Outro PC está usando o mesmo usuário? O monitor parou de relogar por 60 min.")
        _definir_etapa("aguardando_login")
        return False
    _RELOGINS.append(agora)
    return True


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
    # 2.2.2: chromedriver morto/pendurado (urllib3) e textos do Windows
    "read timed out", "remote end closed connection", "connection aborted",
    "connection reset", "winerror 10061", "winerror 10054", "no such session",
    "not connected to devtools",
)


def _erro_chrome_morto(erro):
    """True quando o erro (exceção ou texto) indica Chrome/chromedriver fora do ar."""
    vistos = 0
    while erro is not None and vistos < 5:
        if isinstance(erro, (InvalidSessionIdException, NoSuchWindowException) + _ERROS_CONEXAO_DRIVER):
            return True
        texto = str(erro).lower()
        if any(trecho in texto for trecho in ERROS_CHROME_MORTO):
            return True
        erro = getattr(erro, "__cause__", None) or getattr(erro, "__context__", None)
        vistos += 1
    return False


def _chrome_do_perfil_aberto():
    """Há algum chrome.exe usando o perfil do monitor? (reserva quando o Chrome não é filho do chromedriver)"""
    perfil = _pasta_perfil_chrome()
    if not perfil:
        return False
    alvo = os.path.normcase(os.path.abspath(perfil))
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if (proc.info.get("name") or "").lower() != "chrome.exe":
                continue
            if alvo in os.path.normcase(" ".join(proc.info.get("cmdline") or [])):
                return True
        except Exception:
            continue
    return False


def chrome_vivo(ping=True, timeout=8.0):
    """(True, "") quando o Chrome do monitor existe e responde; (False, motivo) quando não.

    1) o chromedriver que ESTE monitor abriu (pelo PID) ainda existe?
    2) ele ainda tem um Chrome aberto (filho, ou um chrome.exe com o nosso perfil)?
    3) ping=True: driver.title responde em `timeout` s? (só na thread do monitor)
    """
    alvo = driver
    if alvo is None:
        return False, "o navegador não está aberto"
    pid = _PID_CHROMEDRIVER
    if pid:
        try:
            processo = psutil.Process(pid)
            if not processo.is_running() or "chromedriver" not in (processo.name() or "").lower():
                return False, f"o chromedriver (PID {pid}) não existe mais"
            if processo.status() == psutil.STATUS_ZOMBIE:
                return False, f"o chromedriver (PID {pid}) morreu"
            filhos = [f for f in processo.children(recursive=True)
                      if (f.name() or "").lower() == "chrome.exe"]
            if not filhos and not _chrome_do_perfil_aberto():
                return False, "o Chrome fechou (o chromedriver ficou sem navegador)"
        except psutil.NoSuchProcess:
            return False, f"o chromedriver (PID {pid}) não existe mais"
        except Exception:
            pass   # sem permissão para olhar: decide pelo ping
    if not ping:
        return True, ""
    resultado = {}

    def _ping():
        try:
            resultado["titulo"] = alvo.title
        except Exception as erro:
            resultado["erro"] = erro
    tarefa = threading.Thread(target=_ping, name="ping-chrome", daemon=True)
    tarefa.start()
    tarefa.join(timeout)
    if tarefa.is_alive():
        return False, f"o Chrome não respondeu em {int(timeout)} s"
    erro = resultado.get("erro")
    if erro is not None and _erro_chrome_morto(erro):
        return False, f"o Chrome não responde ({type(erro).__name__})"
    return True, ""


ETAPAS_PAUSADAS = ("parado", "offline", "aguardando_login", "fora_horario")


def _registrar_falha_chrome(motivo):
    """Uma reabertura do Chrome (N2). Seguidas, sem ciclo OK, sobem para o N3."""
    agora = time.time()
    cfg = carregar_filtros()
    janela = cfg["recuperacao_janela_falhas_min"] * 60
    _FALHAS_CHROME[:] = [t for t in _FALHAS_CHROME if agora - t <= janela] + [agora]
    log_msg(f"🪜 Reabertura {len(_FALHAS_CHROME)}/{cfg['recuperacao_falhas_para_reiniciar']} sem ciclo OK "
            f"(em {cfg['recuperacao_janela_falhas_min']} min). Ao chegar no limite o programa inteiro reinicia.")
    _definir_etapa("reabrindo")


def decidir_reinicio_por_falha(cfg, agora, etapa, ultimo_ok, marco_ativo, marco_etapa,
                               falhas_chrome, alarme=False, falando=False):
    """Degrau N3 da escada. ("nao", "") | ("adiar", motivo) | ("reiniciar", motivo).

    Reinicia o programa inteiro quando:
      * houve `recuperacao_falhas_para_reiniciar` reaberturas do Chrome sem nenhum ciclo OK
        dentro de `recuperacao_janela_falhas_min`; ou
      * está monitorando há mais de `recuperacao_min_sem_ok` min sem nenhum ciclo OK; ou
      * em qualquer etapa ativa (abrindo o Chrome, login...) há mais de
        `recuperacao_min_sem_ok_total` min sem nenhum ciclo OK.
    Etapas pausadas (sem internet, fora do horário, aguardando login) nunca reiniciam.
    Nunca reinicia com alarme tocando ou fala em andamento: adia até terminar.
    """
    if etapa in ETAPAS_PAUSADAS:
        return "nao", ""
    motivo = ""
    janela = cfg["recuperacao_janela_falhas_min"] * 60
    recentes = [t for t in falhas_chrome if agora - t <= janela]
    if len(recentes) >= cfg["recuperacao_falhas_para_reiniciar"]:
        motivo = (f"{len(recentes)} reaberturas do Chrome seguidas sem nenhum ciclo OK "
                  f"(em {cfg['recuperacao_janela_falhas_min']} min)")
    if not motivo and etapa == "monitorando":
        referencia = max(ultimo_ok, marco_etapa)
        if agora - referencia > cfg["recuperacao_min_sem_ok"] * 60:
            motivo = f"monitorando sem nenhum ciclo OK há {int((agora - referencia) / 60)} min"
    if not motivo:
        referencia = max(ultimo_ok, marco_ativo)
        if agora - referencia > cfg["recuperacao_min_sem_ok_total"] * 60:
            motivo = f"sem nenhum ciclo OK há {int((agora - referencia) / 60)} min (etapa '{etapa}')"
    if not motivo:
        return "nao", ""
    if alarme:
        return "adiar", f"{motivo} — esperando o alarme terminar"
    if falando:
        return "adiar", f"{motivo} — esperando a fala terminar"
    return "reiniciar", motivo


def _avisar_recuperacao(texto, nivel=None, status="", bandeja=""):
    """Loga (e mostra) só quando o aviso muda — o watchdog chama a cada 15 s."""
    global _AVISO_RECUPERACAO
    if _AVISO_RECUPERACAO == texto:
        return
    _AVISO_RECUPERACAO = texto
    log_msg(texto)
    if nivel:
        _atualizar_status(nivel, status)
    if bandeja:
        _avisar_bandeja(bandeja)


def _checar_reinicio_por_falha(agora):
    """Chamado pelo watchdog: sobe para o N3 (reiniciar o programa) quando a escada manda."""
    global _AVISO_RECUPERACAO
    if _ENCERRANDO:
        return False
    cfg = carregar_filtros()
    acao, motivo = decidir_reinicio_por_falha(
        cfg, agora, _ETAPA, _ULTIMO_OK, _MARCO_ATIVO, _MARCO_ETAPA, list(_FALHAS_CHROME),
        alarme=bool(alarme_ativo), falando=_TRAVA_AUDIO.locked())
    if acao == "nao":
        _AVISO_RECUPERACAO = ""
        return False
    if acao == "adiar":
        _avisar_recuperacao(f"⏳ [N3] Reinício do programa adiado: {motivo}.")
        return False
    if not internet_ok():
        _avisar_recuperacao("📴 [N3] Falhando, mas sem internet — não reinicio (espero a conexão voltar).",
                            "offline", "Sem internet — aguardando")
        return False
    if _reinicio_bloqueado_ate(agora) > agora:
        _avisar_recuperacao(
            f"⏳ [N3] {motivo}, mas o último reinício foi há menos de "
            f"{REINICIO_INTERVALO_MINIMO_SEGUNDOS} s — reinicio assim que der.")
        return False
    _AVISO_RECUPERACAO = ""
    log_msg(f"🪜 [N3] Escada de recuperação: {motivo}. Reiniciando o programa inteiro.")
    return reiniciar_processo(motivo)


# --- Cronômetro de login (2.3.3) ---------------------------------------------------
# Do início de QUALQUER tentativa de login (fazer_login / relogin do modo requisição)
# até o login OK (token obtido no modo requisição, página logada no modo Chrome, ou
# qualquer ciclo OK). A thread do watchdog confere de fora (vale com o Chrome travado):
# passou de login_timeout_seg = reinicia o programa pelo caminho do "Reiniciar monitor".
def _modo_http_em_uso():
    return carregar_filtros().get("modo_busca") == "http" and not _HTTP["desligado"]


def iniciar_cronometro_login(agora=None):
    """Marca o início da tentativa de login. Se já está correndo, não zera (retentativas,
    reabertura do Chrome e relogins seguidos contam no mesmo cronômetro)."""
    global _LOGIN_INICIO
    if not _LOGIN_INICIO:
        _LOGIN_INICIO = agora or time.time()


def parar_cronometro_login():
    global _LOGIN_INICIO, _AVISO_CRONOMETRO
    _LOGIN_INICIO = 0.0
    _AVISO_CRONOMETRO = ""


def _recomecar_cronometro_login():
    """Volta da internet: se havia login em andamento, o cronômetro recomeça do zero."""
    global _LOGIN_INICIO
    if _LOGIN_INICIO:
        _LOGIN_INICIO = time.time()


def _texto_duracao(segundos):
    segundos = int(segundos)
    return f"{segundos // 60} min" if segundos % 60 == 0 else f"{segundos} s"


def decidir_cronometro_login(cfg, agora, inicio, etapa, alarme=False, falando=False,
                             cred_invalidas=False):
    """("nao", "") | ("avisar", texto) | ("adiar", texto) | ("reiniciar", motivo)."""
    limite = cfg.get("login_timeout_seg") or 120
    if not inicio or agora - inicio <= limite:
        return "nao", ""
    if etapa in ETAPAS_PAUSADAS:
        return "nao", ""
    if cred_invalidas:
        return "avisar", ("⏱️ Login demorou mais de " + _texto_duracao(limite) + ", mas o login do "
                          "Feegow está vazio/de exemplo ou foi recusado — reiniciar não adianta. "
                          "Salve o login na janela ⚙️ (aba Login).")
    if alarme:
        return "adiar", "⏳ Login demorou mais de " + _texto_duracao(limite) + ": reinício adiado — esperando o alarme terminar."
    if falando:
        return "adiar", "⏳ Login demorou mais de " + _texto_duracao(limite) + ": reinício adiado — esperando a fala terminar."
    return "reiniciar", f"login demorou mais de {_texto_duracao(limite)} ({int(agora - inicio)} s)"


def _avisar_cronometro(texto, nivel=None, status=""):
    global _AVISO_CRONOMETRO
    if _AVISO_CRONOMETRO == texto:
        return
    _AVISO_CRONOMETRO = texto
    log_msg(texto)
    if nivel:
        _atualizar_status(nivel, status)


def _checar_cronometro_login(agora):
    """Chamado pelo watchdog (outra thread) a cada 15 s. True = reiniciou."""
    if _ENCERRANDO or not _LOGIN_INICIO:
        return False
    cfg = carregar_filtros()
    acao, texto = decidir_cronometro_login(
        cfg, agora, _LOGIN_INICIO, _ETAPA, alarme=bool(alarme_ativo), falando=_TRAVA_AUDIO.locked(),
        cred_invalidas=bool(_CRED_RECUSADAS or credenciais_incompletas()))
    if acao == "nao":
        return False
    if acao == "avisar":
        _avisar_cronometro(texto, "falha", "Login recusado — confira as credenciais")
        return False
    if acao == "adiar":
        _avisar_cronometro(texto)
        return False
    if not internet_ok():
        _recomecar_cronometro_login()   # o tempo sem internet não conta
        _avisar_cronometro("📴 Login demorado, mas sem internet — não reinicio (espero a conexão voltar).",
                           "offline", "Sem internet — aguardando")
        return False
    limite = cfg.get("login_timeout_seg") or 120
    log_msg(f"⏱️ Login demorou mais de {_texto_duracao(limite)}: reiniciando o programa")
    if reiniciar_processo(texto):
        return True
    return False


def _definir_etapa(etapa):
    global _ETAPA, _ULTIMO_PROGRESSO, _MARCO_ETAPA, _MARCO_ATIVO
    agora = time.time()
    if etapa in ("aguardando_login", "fora_horario", "parado"):
        parar_cronometro_login()   # nenhum login andando (credenciais/horário/parado)
    if etapa != _ETAPA:
        _MARCO_ETAPA = agora
        if _ETAPA in ETAPAS_PAUSADAS and etapa not in ETAPAS_PAUSADAS:
            _MARCO_ATIVO = agora   # voltou da pausa: o relógio do "sem ciclo OK" recomeça
    _ETAPA = etapa
    _ULTIMO_PROGRESSO = agora


def registrar_batimento(ok=True):
    """Batimento: o ciclo andou. ok=True quando a página respondeu certo."""
    global _ULTIMO_OK, _ULTIMO_PROGRESSO, _PRIMEIRO_OK
    agora = time.time()
    _ULTIMO_PROGRESSO = agora
    if ok:
        _ULTIMO_OK = agora
        if _LOGIN_INICIO:
            parar_cronometro_login()   # ciclo OK: está logado (rede de segurança do cronômetro)
        if _FALHAS_CHROME:
            log_msg(f"🪜 Ciclo OK depois de {len(_FALHAS_CHROME)} reabertura(s) do Chrome — escada zerada.")
            _FALHAS_CHROME.clear()
        if not _PRIMEIRO_OK:
            _PRIMEIRO_OK = True
            confirmar_atualizacao("primeiro ciclo de monitoramento OK")
        if agora - _ULTIMO_HEARTBEAT > 5:
            gravar_heartbeat()


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
    _recomecar_cronometro_login()   # o tempo sem internet não conta para o cronômetro de login
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


REINICIO_INTERVALO_MINIMO_SEGUNDOS = 30   # (2.3.3) único freio: 30 s entre um reinício e o próximo


def _ultimo_reinicio_processo(agora=None):
    """Horário do último reinício do programa (gravado em disco, vale entre processos)."""
    agora = agora or time.time()
    try:
        lista = [float(t) for t in (_ler_estado_watchdog().get("reinicios_processo") or [])]
    except Exception:
        lista = []
    lista = [t for t in lista if t <= agora + 5]   # relógio voltou: ignora horários "do futuro"
    return max(lista) if lista else 0.0


def _reinicio_bloqueado_ate(agora=None):
    """Até quando o próximo reinício espera o intervalo mínimo (0 = liberado)."""
    agora = agora or time.time()
    ultimo = _ultimo_reinicio_processo(agora)
    ate = ultimo + REINICIO_INTERVALO_MINIMO_SEGUNDOS if ultimo else 0.0
    return ate if ate > agora else 0.0


def _pode_reiniciar_processo(agora=None):
    """Desde a 2.3.3 não há teto por hora (decisão do dono): reinicia sempre que a regra
    mandar. Só segura se o último reinício foi há menos de REINICIO_INTERVALO_MINIMO_SEGUNDOS,
    para um defeito permanente não virar laço apertado. Grava o horário em disco."""
    agora = agora or time.time()
    if _reinicio_bloqueado_ate(agora):
        return False
    dados = _ler_estado_watchdog()
    try:
        lista = [float(t) for t in (dados.get("reinicios_processo") or []) if agora - float(t) < 3600]
    except Exception:
        lista = []
    lista.append(agora)
    dados["reinicios_processo"] = lista          # só informativo (última hora)
    dados.pop("reinicio_bloqueado_ate", None)    # campo da 2.2.2 (teto por hora), não existe mais
    _gravar_estado_watchdog(dados)
    return True


def _gravar_estado_watchdog(dados):
    try:
        _gravar_json_atomico(ARQ_ESTADO_WATCHDOG, dados, indent=None)
    except Exception:
        pass


def reiniciar_processo(motivo, ignorar_limite=False):
    """Nível 3: reinicia o programa inteiro (fecha o Chrome, sobe outro processo)."""
    global _ENCERRANDO
    if ignorar_limite:
        _pode_reiniciar_processo()   # registra o horário (o intervalo mínimo conta a partir dele)
    elif not _pode_reiniciar_processo():
        faltam = max(1, int(_reinicio_bloqueado_ate() - time.time()))
        log_msg(f"⏳ Reinício do programa pedido ({motivo}), mas o último foi há menos de "
                f"{REINICIO_INTERVALO_MINIMO_SEGUNDOS} s — tento de novo em ~{faltam} s.")
        return False
    log_msg(f"🔁 [N3] Reiniciando o programa inteiro. Motivo: {motivo}")
    _ENCERRANDO = True
    _salvar_pausa()   # a pausa do CIENTE continua valendo no processo novo
    # Antes (2.0.0) _limpeza_para_sair() terminava com os._exit(0): o processo
    # morria com código 0 ANTES de relançar e o supervisor entendia "usuário
    # encerrou" e saía também. Agora a limpeza só limpa; quem sai é o
    # relancar_processo_agora() (código 75 sob o supervisor).
    _limpeza_para_sair()
    relancar_processo_agora()
    return True


# --- Reinício programado (padrão: a cada 60 min) ---------------------------------
# O watchdog confere a cada 15 s. Na hora, reinicia pelo MESMO caminho do nível 3
# (reiniciar_processo -> _limpeza_para_sair -> relancar_processo_agora: código 75
# sob o supervisor, ou nova instância sem ele). Nunca no meio de um alerta.
ARQ_PAUSA = os.path.join(PASTA_LOGS, "pausa.json")
_ADIAMENTO_REINICIO = ""   # último motivo de adiamento já logado (loga só quando muda)


def decidir_reinicio_programado(cfg, inicio_processo, agora, alarme=False, falando=False,
                                pausa_ate_ts=0.0):
    """("nao", "") | ("adiar", motivo) | ("reiniciar", "reinicio programado (N min)")."""
    minutos = cfg.get("reiniciar_a_cada_min") or 0
    if not minutos or agora - inicio_processo < minutos * 60:
        return "nao", ""
    if alarme:
        return "adiar", "alarme ativo (janela vermelha/voz)"
    if falando:
        return "adiar", "fala em andamento"
    if pausa_ate_ts and agora < pausa_ate_ts:
        return "adiar", "pausa do CIENTE"
    return "reiniciar", f"reinicio programado ({minutos} min)"


def _checar_reinicio_programado(agora):
    """Chamado pelo watchdog. Adia enquanto houver alarme, fala ou pausa do CIENTE."""
    global _ADIAMENTO_REINICIO
    if _ENCERRANDO:
        return False
    cfg = carregar_filtros()
    acao, motivo = decidir_reinicio_programado(
        cfg, _INICIO_PROCESSO, agora, alarme=bool(alarme_ativo),
        falando=_TRAVA_AUDIO.locked(), pausa_ate_ts=pausa_ate.timestamp())
    if acao == "nao":
        _ADIAMENTO_REINICIO = ""
        return False
    if acao == "adiar":
        if _ADIAMENTO_REINICIO != motivo:
            _ADIAMENTO_REINICIO = motivo
            log_msg(f"⏳ reinicio programado ({cfg['reiniciar_a_cada_min']} min) adiado: {motivo}.")
        return False
    _ADIAMENTO_REINICIO = ""
    log_msg(f"⏰ {motivo}")
    reiniciar_processo(motivo, ignorar_limite=True)   # planejado: não espera o intervalo mínimo
    return True


def _salvar_pausa():
    """Grava a pausa do CIENTE em logs/pausa.json (se estiver valendo) para o próximo processo."""
    try:
        restante = (pausa_ate - datetime.datetime.now()).total_seconds()
        if restante > 0:
            _gravar_json_atomico(ARQ_PAUSA, {"pausa_ate": pausa_ate.timestamp()}, indent=None)
        elif os.path.isfile(ARQ_PAUSA):
            os.remove(ARQ_PAUSA)
    except Exception:
        pass


def restaurar_pausa(agora=None):
    """Na inicialização: retoma a pausa do CIENTE gravada pelo processo anterior.
    Ignora valor vencido ou maior que a pausa configurada (arquivo velho/editado)."""
    global pausa_ate
    agora = agora or time.time()
    try:
        with open(ARQ_PAUSA, "r", encoding="utf-8") as arquivo:
            ate = float((json.load(arquivo) or {}).get("pausa_ate") or 0)
    except Exception:
        return False
    try:
        os.remove(ARQ_PAUSA)
    except Exception:
        pass
    limite = carregar_filtros().get("pausa_minutos", 15) * 60 + 60
    if not (agora < ate <= agora + limite):
        return False
    pausa_ate = datetime.datetime.fromtimestamp(ate)
    log_msg(f"⏸️ Pausa do CIENTE retomada após o reinício (até {pausa_ate.strftime('%H:%M:%S')}).")
    return True


def montar_status():
    """Estado do monitor para o status.json, o diagnóstico e o heartbeat remoto (sem senha)."""
    nivel, texto = _STATUS
    agora = time.time()
    return {
        "versao": VERSAO,
        "pid": os.getpid(),
        "atualizado_em": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": nivel,
        "texto": texto,
        "etapa": _ETAPA,
        "ultimo_ok_ha_s": int(agora - _ULTIMO_OK) if _ULTIMO_OK else None,
        "ultimo_progresso_ha_s": int(agora - _ULTIMO_PROGRESSO),
        "rodando_ha_s": int(agora - _INICIO_PROCESSO),
        "tk_ha_s": int(agora - _TK_TICK),
        "alarme_ativo": bool(alarme_ativo),
        "senha_no_alarme": _TICKET_ALARME or "",
        "sem_audio": _SEM_AUDIO,
        "relogins_ultima_hora": len([t for t in _RELOGINS if agora - t < 3600]),
        "relogin_bloqueado": _RELOGIN_BLOQUEADO_ATE > agora,
        "reinicios_chrome_ultima_hora": len([t for t in _REINICIOS_CHROME if agora - t < 3600]),
        "reaberturas_sem_ok": len(_FALHAS_CHROME),
        "reinicio_bloqueado_ate": _reinicio_bloqueado_ate(),
        "ciclo_leve": {"ligado": not _CICLO_LEVE["desligado"], "ciclos_ok": _CICLO_LEVE["ok"],
                       "ultimo_ms": _CICLO_LEVE["ms"]},
        "chrome_ram_mb": _RAM["mb"],
        "modo_busca": {"configurado": carregar_filtros().get("modo_busca", "http"),
                       "em_uso": "http" if _HTTP["ativo"] else "chrome",
                       "reserva_chrome": _HTTP["desligado"],
                       "ciclos_ok": _HTTP["ciclos_ok"], "ultimo_ms": _HTTP["ultimo_ms"],
                       "falhas_seguidas": _HTTP["falhas_seguidas"],
                       "relogins_falhos": _HTTP["relogins_falhos"],
                       "token_valido_ate": _HTTP["token_exp"]},
        "supervisionado": _supervisionado(),
        "reinicio_programado_min": carregar_filtros().get("reiniciar_a_cada_min", 0),
        "reinicio_adiado": _ADIAMENTO_REINICIO,
    }


def gravar_heartbeat():
    """logs/heartbeat.json (o supervisor mata e relança se parar > 5 min) + logs/status.json."""
    global _ULTIMO_HEARTBEAT
    agora = time.time()
    _ULTIMO_HEARTBEAT = agora
    try:
        _gravar_json_atomico(ARQ_HEARTBEAT, {
            "pid": os.getpid(), "ts": agora, "etapa": _ETAPA, "versao": VERSAO,
            # o supervisor usa como reserva: ativo e sem ciclo OK há muito tempo = relança
            "ativo": _ETAPA not in ETAPAS_PAUSADAS,
            "ok_ref_ts": max(_ULTIMO_OK, _MARCO_ATIVO),
            "alarme": bool(alarme_ativo),
        }, indent=None)
        _gravar_json_atomico(ARQ_STATUS, montar_status())
    except Exception:
        pass


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
    global _ULTIMO_PROGRESSO, _PEDIDO_RECARGA, _MARCO_ATIVO
    ultimo_giro = time.time()
    gravar_heartbeat()
    while not _ENCERRANDO:
        time.sleep(WATCHDOG_PERIODO_SEGUNDOS)
        agora = time.time()
        gravar_heartbeat()
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
                _MARCO_ATIVO = agora   # não conta o tempo suspenso como "sem ciclo OK"
                if _ETAPA == "monitorando":
                    _PEDIDO_RECARGA = True
            ultimo_giro = agora
            _passo_watchdog(agora)
            if _checar_cronometro_login(agora):
                return
            _checar_reinicio_por_falha(agora)
            _checar_confirmacao_por_tempo(agora)
            _checar_reinicio_programado(agora)
        except Exception as e:
            log_msg(f"⚠️ Watchdog: erro interno ignorado ({type(e).__name__}: {e})")


def _passo_watchdog(agora):
    global _PEDIDO_REINICIO_CHROME, _HORA_PEDIDO_CHROME
    cfg = carregar_filtros()

    # A thread do monitor morreu sem ninguém pedir? Religa.
    if (_MONITOR_DESEJADO and not _SELENIUM_RODANDO and not _CRED_RECUSADAS
            and _ETAPA != "aguardando_login" and agora >= _RELOGIN_BLOQUEADO_ATE
            and not credenciais_incompletas()):
        if _RELOGIN_BLOQUEADO_ATE:
            log_msg("🔐 Passaram 60 min desde o bloqueio de relogin — tentando entrar de novo.")
        log_msg("🩺 Watchdog: o monitoramento tinha parado — religando.")
        iniciar_selenium()
        return

    if not _SELENIUM_RODANDO or _ETAPA in ("offline", "parado", "aguardando_login", "fora_horario"):
        return

    # N2 rápido: o Chrome/chromedriver do monitor sumiu (fechado, travou e morreu...)?
    if (_ETAPA in ("monitorando", "login") and not _PEDIDO_REINICIO_CHROME and driver is not None
            and not _HTTP["ativo"]):
        vivo, motivo_chrome = chrome_vivo(ping=False)
        if not vivo:
            _PEDIDO_REINICIO_CHROME = f"Chrome ausente: {motivo_chrome}"
            _HORA_PEDIDO_CHROME = agora
            log_msg(f"🩺 [N2] Watchdog: {motivo_chrome}. Recriando o navegador e entrando de novo.")
            _atualizar_status("recuperando", "O Chrome fechou — reabrindo o navegador")
            try:
                _matar_chromedriver_por_pid()   # chamadas do Selenium penduradas estouram na hora
            except Exception:
                pass
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
    _atualizar_titulo_bandeja()


def _atualizar_titulo_bandeja():
    nivel, texto = _STATUS
    try:
        if tray_icon is not None:
            tray_icon.icon = criar_icone_imagem(CORES_STATUS.get(nivel, "blue"))
            extra = " | Sem áudio" if _SEM_AUDIO else ""
            tray_icon.title = f"Monitor Feegow v{VERSAO} — {texto}{extra}"[:120]
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
    audio = f"  |  🔇 Sem áudio ({_SEM_AUDIO})" if _SEM_AUDIO else ""
    return nivel, f"● {texto}  |  {ultimo}{audio}  |  v{VERSAO}"


# ==============================================================================
# ⬆️ ATUALIZAÇÃO AUTOMÁTICA PELO GITHUB (versao.json)
# ==============================================================================
_AVISOU_CANAL = ""


def _canal_atualizacao():
    """Só main/beta. Outro nome no filtros.json cai na main (com aviso no log)."""
    global _AVISOU_CANAL
    canal = str(carregar_filtros().get("atualizacao_canal") or CANAL_ATUALIZACAO_PADRAO).strip()
    if canal in CANAIS_PERMITIDOS:
        return canal
    if _AVISOU_CANAL != canal:
        _AVISOU_CANAL = canal
        log_msg(f"⚠️ Canal de atualização '{canal}' não é permitido (só {', '.join(CANAIS_PERMITIDOS)}) — usando 'main'.")
    return CANAL_ATUALIZACAO_PADRAO


def _url_raw(arquivo, canal=None):
    canal = canal or _canal_atualizacao()
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
    canal = _canal_atualizacao()
    dados = json.loads(_baixar(_url_raw(NOME_ARQUIVO_VERSAO, canal), timeout=30).decode("utf-8-sig"))
    if not isinstance(dados, dict) or not dados.get("versao"):
        raise ValueError("versao.json sem o campo 'versao'")
    validar_versao_assinada(dados, canal)
    return dados


def validar_script_baixado(bruto, versao_esperada, sha256=""):
    """Só aceita o arquivo se for MESMO o script, inteiro e sem erro de sintaxe."""
    if len(bruto) < TAMANHO_MINIMO_SCRIPT:
        raise ValueError(f"arquivo pequeno demais ({len(bruto)} bytes)")
    if not sha256:
        raise ValueError("versao.json sem sha256: o script não é instalado sem conferência")
    if hashlib.sha256(bruto).hexdigest().lower() != sha256.strip().lower():
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
    if _ETAPA in ("aguardando_login", "parado", "fora_horario"):
        return True
    return _ETAPA == "monitorando" and _ULTIMO_OK and (time.time() - _ULTIMO_OK) < 120


def verificar_atualizacao(manual=False):
    """Consulta o versao.json e, se houver versão nova, atualiza com segurança."""
    global _ATUALIZACAO_ADIADA, _AVISOU_GIT, _AVISOU_SEM_EXE
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
        if nova == VERSAO:
            _atualizar_arquivos_extras(remoto)
        if manual:
            log_msg(f"✅ Já está na versão mais nova ({VERSAO}).")
        return "atual"
    if nova in (estado.get("bloqueadas") or []):
        if manual:
            log_msg(f"⛔ A versão {nova} já falhou aqui e foi revertida — ignorando.")
        return "bloqueada"
    if _caminho_programa_atual().lower().endswith(".exe") and not str(remoto.get("sha256_exe") or "").strip():
        if manual or _AVISOU_SEM_EXE != nova:
            _AVISOU_SEM_EXE = nova
            log_msg(f"ℹ️ A versão {nova} foi publicada sem .exe (sha256_exe vazio): "
                    f"este MonitorFeegow.exe continua na {VERSAO} até sair um .exe conferido.")
        return "sem_exe"
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


def _atualizar_arquivos_extras(remoto):
    """Atualiza arquivos que acompanham o script (hoje só o iniciar_monitor.pyw),
    conferidos pelo sha256 do versao.json ASSINADO. Só troca arquivos que já
    existem na pasta. O supervisor novo passa a valer no próximo início dele
    (ao entrar no Windows ou ao abrir o iniciar_monitor.pyw de novo)."""
    extras = remoto.get("arquivos_extras")
    if not isinstance(extras, dict) or _rodando_compilado():
        return
    for nome, info in extras.items():
        if nome not in ARQUIVOS_EXTRAS_PERMITIDOS or not isinstance(info, dict):
            continue
        esperado = str(info.get("sha256") or "").lower()
        destino = os.path.join(PASTA_SCRIPT, nome)
        if not re.fullmatch(r"[0-9a-f]{64}", esperado) or not os.path.isfile(destino):
            continue
        try:
            with open(destino, "rb") as arquivo:
                atual = arquivo.read().replace(b"\r\n", b"\n")
            if hashlib.sha256(atual).hexdigest() == esperado:
                continue
            bruto = _baixar(_url_raw(nome), timeout=60, limite=2_000_000)
            if hashlib.sha256(bruto).hexdigest() != esperado:
                raise ValueError("sha256 não confere com o versao.json")
            compile(bruto.decode("utf-8"), nome, "exec")
            novo, backup = destino + ".novo", destino + ".anterior"
            with open(novo, "wb") as arquivo:
                arquivo.write(bruto)
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(destino, backup)
            os.replace(novo, destino)
            log_msg(f"⬆️ {nome} atualizado (backup em {os.path.basename(backup)}). "
                    "Vale a partir do próximo início do supervisor (ex.: ao entrar no Windows).")
        except Exception as e:
            log_msg(f"⚠️ Não consegui atualizar {nome}: {type(e).__name__}: {e}")


def confirmar_atualizacao(motivo):
    estado = _ler_estado_atualizacao()
    if estado.get("status") != "em_teste" or estado.get("para") != VERSAO:
        return
    estado["status"] = "confirmada"
    estado["confirmada_em"] = datetime.datetime.now().isoformat(timespec="seconds")
    estado["motivo"] = motivo
    _gravar_estado_atualizacao(estado)
    log_msg(f"✅ Versão {VERSAO} confirmada ({motivo}). A anterior fica guardada como .anterior.")
    try:
        carregar_credenciais()   # agora pode proteger a senha com DPAPI (migração adiada no teste)
    except Exception:
        pass


def _checar_confirmacao_por_tempo(agora):
    """Confirma ou reverte a versão em teste quando o 1º ciclo OK não vem."""
    if _PRIMEIRO_OK:
        return
    estado = _ler_estado_atualizacao()
    if estado.get("status") != "em_teste" or estado.get("para") != VERSAO:
        return
    vivo_ha = agora - _INICIO_PROCESSO
    sem_como_testar = (_CRED_RECUSADAS or credenciais_incompletas()
                       or _ETAPA in ("offline", "fora_horario", "aguardando_login"))
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


# ==============================================================================
# 📡 HEARTBEAT REMOTO (opcional, desligado por padrão) + 🩻 DIAGNÓSTICO
# ==============================================================================
CAMPO_TOKEN_HEARTBEAT = "heartbeat_remoto_token_protegido"


def _url_heartbeat_permitida(url):
    """Só HTTPS (ou http em 127.0.0.1/localhost, para teste)."""
    url = str(url or "").strip().lower()
    return url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost"))


def enviar_heartbeat_remoto(url, token, cfg=None):
    cfg = cfg or carregar_filtros()
    status = montar_status()
    status["maquina"] = cfg.get("nome_maquina") or socket.gethostname()
    corpo = json.dumps(status, ensure_ascii=False).encode("utf-8")
    cabecalhos = {"Content-Type": "application/json; charset=utf-8", "User-Agent": f"MonitorFeegow/{VERSAO}"}
    if token:
        cabecalhos["Authorization"] = f"Bearer {token}"
    requisicao = urllib.request.Request(url, data=corpo, headers=cabecalhos, method="POST")
    with urllib.request.urlopen(requisicao, timeout=10) as resposta:
        if getattr(resposta, "status", 200) >= 300:
            raise RuntimeError(f"HTTP {resposta.status}")
    return True


def thread_heartbeat_remoto():
    """Avisa a VPS de N em N minutos que este monitor está vivo. Só roda com
    heartbeat_remoto_url preenchida (o servidor alerta no Telegram se parar de chegar)."""
    _dormir(60)
    ultimo_aviso_erro, avisou_url = 0.0, ""
    while not _ENCERRANDO:
        cfg = carregar_filtros()
        url = cfg.get("heartbeat_remoto_url") or ""
        if url and not _url_heartbeat_permitida(url):
            if avisou_url != url:
                avisou_url = url
                log_msg("⚠️ heartbeat_remoto_url precisa ser https:// — heartbeat remoto não enviado.")
        elif url:
            try:
                enviar_heartbeat_remoto(url, ler_segredo(CAMPO_TOKEN_HEARTBEAT), cfg)
            except Exception as e:
                if time.time() - ultimo_aviso_erro > 6 * 3600:
                    ultimo_aviso_erro = time.time()
                    log_msg(f"📡 Heartbeat remoto falhou ({type(e).__name__}: {str(e)[:120]}). "
                            "Próximo aviso deste erro só daqui a 6 h.")
        _dormir(cfg.get("heartbeat_remoto_intervalo_minutos", 5) * 60)


def texto_diagnostico():
    """Texto para colar no suporte: versão, estado, configuração (sem segredos) e
    as últimas linhas do log — com e-mails mascarados."""
    import platform
    cfg = dict(carregar_filtros())
    cfg.pop("whitelist", None)
    cfg.pop("blacklist", None)
    if cfg.get("heartbeat_remoto_url"):
        cfg["heartbeat_remoto_url"] = re.sub(r"(https?://[^/]+).*", r"\1/...", cfg["heartbeat_remoto_url"])
    partes = [
        f"Monitor de Senha Feegow v{VERSAO} — diagnóstico de {datetime.datetime.now():%d/%m/%Y %H:%M:%S}",
        f"Windows {platform.release()} ({platform.version()}) | Python {sys.version.split()[0]}"
        f" | {'compilado' if _rodando_compilado() else 'script'} | pasta: {PASTA_SCRIPT}",
        "",
        "== Estado",
        json.dumps(montar_status(), ensure_ascii=False, indent=2),
        "",
        "== Configuração (filtros.json, sem listas e sem segredos)",
        json.dumps(cfg, ensure_ascii=False, indent=2, default=str),
        f"credenciais: usuário {mascarar_email(_CRED_USUARIO_LOGADO or '(não carregado)')}, "
        f"senha {'protegida (DPAPI)' if _senha_esta_protegida() else 'NÃO protegida'}",
    ]
    for titulo, caminho, quantidade in (("monitor.log", ARQ_LOG, 80),
                                        ("supervisor.log", os.path.join(PASTA_LOGS, "supervisor.log"), 20)):
        try:
            with open(caminho, "r", encoding="utf-8", errors="replace") as arquivo:
                linhas = arquivo.readlines()[-quantidade:]
        except OSError:
            linhas = ["(sem arquivo)\n"]
        partes += ["", f"== Últimas {quantidade} linhas de {titulo}", "".join(linhas).rstrip()]
    return mascarar_email("\n".join(partes))


def _senha_esta_protegida():
    try:
        dados = _ler_json(ARQ_CREDENCIAIS)
        return bool(dados.get("senha_protegida")) and not dados.get("senha")
    except Exception:
        return False


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
    na_ui(_alternar_janelas)


def on_tray_mostrar(icon, item):
    na_ui(gerenciar_janelas, "mostrar")


def on_tray_ocultar(icon, item):
    na_ui(gerenciar_janelas, "ocultar")

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
        _matar_chromedriver_por_pid()
    except Exception:
        pass

    # o cache de áudio (%LOCALAPPDATA%\MonitorFeegow\audio) fica: é o que faz a
    # voz funcionar sem internet no próximo início.


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
        indice = {"senhas": 0, "login": 1, "alertas": 2, "sistema": 3, "falas": 4}.get(str(aba).lower(), 0)
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
    janela.geometry(_geometria(820, 640))
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
    entrada_senha.insert(0, carregar_credenciais().get("senha") or "")
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
                  "Ao salvar um login diferente, o navegador é reaberto sozinho com ele.\n"
                  "A senha fica protegida pelo Windows (DPAPI) — só este usuário deste PC a abre.",
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
        ("chrome_reinicio_diario_hora", "Reabrir o Chrome todo dia às (HH:MM, vazio = não):", "03:00"),
        ("chrome_limite_ram_mb", "Reabrir se o Chrome passar de (MB, 0 = não):", 1500),
        ("max_relogins_por_hora", "Parar de relogar após N quedas de sessão/hora:", 3),
        ("heartbeat_remoto_url", "Heartbeat remoto: URL https (vazio = desligado):", ""),
        ("heartbeat_remoto_intervalo_minutos", "Heartbeat remoto a cada (min):", 5),
        ("nome_maquina", "Nome desta máquina (heartbeat):", ""),
    )):
        tk.Label(form3, text=rotulo_campo, bg="#1e1e1e", fg="#e5e7eb",
                 font=("Consolas", 10)).grid(row=linha, column=0, sticky="w", pady=4)
        campo = tk.Entry(form3, width=34 if isinstance(padrao_campo, str) else 14,
                         bg="#111111", fg="#eeeeee", insertbackground="#eeeeee")
        valor = filtros.get(chave)
        campo.insert(0, str(padrao_campo if valor is None else valor))
        campo.grid(row=linha, column=1, padx=8, sticky="w")
        campos_sistema[chave] = (campo, padrao_campo)

    linha_token = len(campos_sistema)
    tk.Label(form3, text="Heartbeat remoto: token (fica protegido):", bg="#1e1e1e", fg="#e5e7eb",
             font=("Consolas", 10)).grid(row=linha_token, column=0, sticky="w", pady=4)
    entrada_token = tk.Entry(form3, width=34, show="•", bg="#111111", fg="#eeeeee", insertbackground="#eeeeee")
    entrada_token.insert(0, ler_segredo(CAMPO_TOKEN_HEARTBEAT))
    entrada_token.grid(row=linha_token, column=1, padx=8, sticky="w")

    def copiar_diagnostico():
        try:
            texto = texto_diagnostico()
            janela.clipboard_clear()
            janela.clipboard_append(texto)
            janela.update()
            messagebox.showinfo("Diagnóstico copiado",
                                "O diagnóstico (sem senha, e-mails mascarados) foi copiado.\n"
                                "Cole (Ctrl+V) na conversa com o suporte.", parent=janela)
        except Exception as e:
            messagebox.showerror("Diagnóstico", str(e), parent=janela)

    linha_botoes = tk.Frame(aba_sistema, bg="#1e1e1e")
    linha_botoes.pack(anchor="w", padx=14, pady=12)
    tk.Button(linha_botoes, text="📋 Copiar diagnóstico", command=copiar_diagnostico,
              bg="#374151", fg="white", relief=tk.FLAT, padx=10, pady=4).pack(side=tk.RIGHT, padx=(8, 0))
    tk.Button(linha_botoes, text="⬆️ Procurar atualização agora", command=procurar_atualizacao_agora,
              bg="#1f3a5f", fg="white", relief=tk.FLAT, padx=10, pady=4).pack(side=tk.LEFT)
    tk.Button(linha_botoes, text="📂 Abrir pasta de logs",
              command=lambda: os.startfile(PASTA_LOGS) if os.path.isdir(PASTA_LOGS) else None,
              bg="#374151", fg="white", relief=tk.FLAT, padx=10, pady=4).pack(side=tk.LEFT, padx=8)

    # ---------------- aba FALAS (fala e voz de cada filtro) ----------------
    aba_falas = tk.Frame(abas, bg="#1e1e1e")
    abas.add(aba_falas, text="  Falas  ")
    _selecionar()

    tk.Label(aba_falas,
             text="O que a voz fala em cada filtro. A senha que alertou é comparada com as\n"
                  "senhas/termos de cada linha (de cima para baixo); a primeira que casar decide.\n"
                  "Fala vazia = fala a senha da tela (\"Atenção! Senha ...\").\n"
                  "Sem \"Falar em voz alta\" a voz fica muda nesse filtro (a janela vermelha segue a aba Alertas).",
             bg="#1e1e1e", fg="#9ca3af", font=("Consolas", 9), justify=tk.LEFT
             ).pack(anchor="w", padx=14, pady=(10, 6))

    form_falas = tk.Frame(aba_falas, bg="#1e1e1e")
    form_falas.pack(anchor="w", padx=14, fill=tk.X)
    for coluna, titulo in enumerate(("Filtro", "Senhas / termos (vírgula)", "Fala", "", "")):
        tk.Label(form_falas, text=titulo, bg="#1e1e1e", fg="#9ca3af",
                 font=("Consolas", 9, "bold")).grid(row=0, column=coluna, sticky="w", padx=4, pady=(0, 4))

    falas_brutas = filtros.get("falas_filtros")
    if not isinstance(falas_brutas, dict):
        falas_brutas = FILTROS_PADRAO["falas_filtros"]
    linhas_falas = []
    for linha, filtro_fala in enumerate(_falas_normalizadas(falas_brutas), start=1):
        tk.Label(form_falas, text=filtro_fala["nome"], bg="#1e1e1e", fg="#e5e7eb",
                 font=("Consolas", 10)).grid(row=linha, column=0, sticky="w", padx=4, pady=4)
        campo_termos = tk.Entry(form_falas, width=24, bg="#111111", fg="#eeeeee", insertbackground="#eeeeee")
        campo_termos.insert(0, ", ".join(filtro_fala["termos_texto"]))
        campo_termos.grid(row=linha, column=1, padx=4, sticky="w")
        campo_fala = tk.Entry(form_falas, width=30, bg="#111111", fg="#eeeeee", insertbackground="#eeeeee")
        campo_fala.insert(0, filtro_fala["fala"])
        campo_fala.grid(row=linha, column=2, padx=4, sticky="w")
        var_fala_voz = tk.BooleanVar(value=filtro_fala["voz"])
        tk.Checkbutton(form_falas, text="Falar em voz alta", variable=var_fala_voz,
                       bg="#1e1e1e", fg="#e5e7eb", selectcolor="#111111", activebackground="#1e1e1e",
                       activeforeground="#e5e7eb", font=("Consolas", 9)
                       ).grid(row=linha, column=3, sticky="w", padx=4)

        def testar_fala(c_fala=campo_fala, c_termos=campo_termos):
            frase = c_fala.get().strip()
            if not frase:
                primeiro = (c_termos.get().split(",") or [""])[0].strip()
                frase = f"Atenção! Senha {_texto_para_fala(primeiro)}." if primeiro else FRASE_GENERICA
            threading.Thread(target=gerar_e_tocar_alerta_teste, args=(frase,), daemon=True).start()

        tk.Button(form_falas, text="▶ Testar", command=testar_fala, bg="#374151", fg="white",
                  relief=tk.FLAT, padx=8).grid(row=linha, column=4, padx=4)
        linhas_falas.append((filtro_fala["chave"], campo_termos, campo_fala, var_fala_voz))

    def coletar_falas(atuais):
        """Monta falas_filtros a partir da aba, mantendo campos extras do arquivo."""
        import copy
        novo = copy.deepcopy(atuais) if isinstance(atuais, dict) else copy.deepcopy(FILTROS_PADRAO["falas_filtros"])
        for chave_f, c_termos, c_fala, v_voz in linhas_falas:
            item_f = novo.get(chave_f) if isinstance(novo.get(chave_f), dict) else dict(
                FILTROS_PADRAO["falas_filtros"].get(chave_f) or {"nome": chave_f, "comparacao": "contem"})
            item_f["termos"] = [t.strip().upper() for t in c_termos.get().split(",") if t.strip()]
            item_f["fala"] = c_fala.get().strip()
            item_f["voz"] = bool(v_voz.get())
            novo[chave_f] = item_f
        return novo

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
            dados["falas_filtros"] = coletar_falas(dados.get("falas_filtros"))
            dados["auto_atualizar"] = bool(var_auto.get())
            for chave, (campo, padrao_campo) in campos_sistema.items():
                if isinstance(padrao_campo, int):
                    dados[chave] = _para_int(campo.get(), padrao_campo, 0)
                elif chave == "chrome_reinicio_diario_hora":
                    dados[chave] = _hora_valida(campo.get())   # vazio = desligado
                elif chave in ("heartbeat_remoto_url", "nome_maquina"):
                    dados[chave] = campo.get().strip()
                else:
                    dados[chave] = campo.get().strip() or padrao_campo
            if entrada_token.get() != ler_segredo(CAMPO_TOKEN_HEARTBEAT):
                gravar_segredo(CAMPO_TOKEN_HEARTBEAT, entrada_token.get().strip())
            _sincronizar_termos_filtro(dados)
            _gravar_json(ARQ_FILTROS, dados)

            antes = carregar_credenciais()
            novo_usuario = entrada_usuario.get().strip()
            nova_senha = entrada_senha.get()
            nova_rota = (combo_rota.get() or "auto").lower()
            login_mudou = (novo_usuario != antes.get("usuario") or nova_senha != antes.get("senha")
                           or nova_rota != antes.get("rota_login"))
            salvar_credenciais(novo_usuario, nova_senha, nova_rota)

            carregar_filtros(forcar=True)
            global _CRED_USUARIO_LOGADO, _CRED_RECUSADAS, _RELOGIN_BLOQUEADO_ATE
            global _PEDIDO_REINICIO_CHROME, _HORA_PEDIDO_CHROME, _REINICIO_SEM_ESPERA
            _CRED_USUARIO_LOGADO = None  # força relogar com as credenciais novas
            reabriu = False
            if login_mudou:
                _CRED_RECUSADAS = False
                _RELOGIN_BLOQUEADO_ATE = 0.0
                _RELOGINS.clear()
                if _SELENIUM_RODANDO and not credenciais_incompletas():
                    log_msg("🔐 Login alterado na configuração — reabrindo o navegador com o login novo.")
                    _REINICIO_SEM_ESPERA = True
                    _HORA_PEDIDO_CHROME = time.time()
                    _PEDIDO_REINICIO_CHROME = "login alterado na configuração"
                    reabriu = True

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

            if iniciou_agora:
                aviso_login = "O monitoramento foi iniciado com o login novo."
            elif reabriu:
                aviso_login = "O navegador está sendo reaberto com o login novo."
            else:
                aviso_login = "O login não mudou."
            messagebox.showinfo(
                "Configurações salvas",
                "filtros.json e credenciais.json atualizados.\n\n"
                "As senhas, os alertas e as falas valem na hora.\n" + aviso_login,
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
def _ativar_dpi():
    """Sem isto o Windows "estica" a janela em telas com 125/150% (texto borrado)."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system-aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_ativar_dpi()
root = tk.Tk()
try:
    _ESCALA = max(1.0, min(3.0, float(root.winfo_fpixels("1i")) / 96.0))
except Exception:
    _ESCALA = 1.0


def _geometria(largura, altura):
    """Tamanho escalado pelo DPI e limitado à tela."""
    l = min(int(largura * _ESCALA), int(root.winfo_screenwidth() * 0.95))
    a = min(int(altura * _ESCALA), int(root.winfo_screenheight() * 0.9))
    return f"{l}x{a}"


root.title(f"Painel de Depuração - Monitor Feegow v{VERSAO}")
root.geometry(_geometria(800, 650))
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
    global _TK_TICK
    _TK_TICK = time.time()
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
_FRASES_INICIAIS = _frases_para_pregerar()
entry_voz.insert(0, _FRASES_INICIAIS[1] if len(_FRASES_INICIAIS) > 1 else _FRASES_INICIAIS[0])
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
    restaurar_pausa()
    carregar_credenciais()
    pregerar_audios_em_segundo_plano()   # fora da thread principal (não trava a abertura)

    if credenciais_incompletas():
        log_msg("🔐 credenciais.json sem login/senha válidos — aguardando preenchimento na janela de configuração.")
        root.after(600, lambda: solicitar_credenciais("primeira_vez"))
    else:
        threading.Thread(target=iniciar_selenium, daemon=True).start()

    threading.Thread(target=iniciar_tray, daemon=True).start()
    threading.Thread(target=thread_watchdog, name="watchdog", daemon=True).start()
    threading.Thread(target=thread_atualizacao, name="atualizacao", daemon=True).start()
    threading.Thread(target=thread_heartbeat_remoto, name="heartbeat_remoto", daemon=True).start()
    root.after(300, _drenar_fila_log)
    root.after(100, _drenar_fila_ui)
    root.after(1000, _atualizar_rotulo_status)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C no console: encerra sem traceback e sem deixar Chrome aberto
        log_msg("⌨️ Ctrl+C detectado — encerrando o sistema e fechando o navegador...")
        encerrar_sistema()
