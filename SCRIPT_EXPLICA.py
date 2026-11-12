import os
import sys
import time
import threading
import datetime
import ctypes
import json
import shutil
import unicodedata
import tkinter as tk
from tkinter import scrolledtext
import tempfile # NOVA BIBLIOTECA PARA USAR A PASTA TEMP DO WINDOWS
import subprocess

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
    ModuleNotFoundError em um PC novo.
    """
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
from selenium.common.exceptions import NoSuchElementException, ElementNotInteractableException, TimeoutException

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
PASTA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

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
def log_msg(mensagem):
    timestamp = datetime.datetime.now().strftime("[%H:%M:%S]")
    texto_formatado = f"{timestamp} - {mensagem}\n"
    print(texto_formatado, end="")
    
    if 'janela_logs' in globals():
        try:
            janela_logs.configure(state='normal')
            janela_logs.insert(tk.END, texto_formatado)
            janela_logs.see(tk.END)
            janela_logs.configure(state='disabled')
        except:
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
        log_msg(f"✅ Alerta pausado manualmente por {cfg['pausa_minutos']} min. Busca ignorará {cfg['termo_gatilho']} até {pausa_ate.strftime('%H:%M:%S')}.")
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

def tocar_audio_em_dispositivo(nome_dispositivo, arquivo_audio, volume=1.0):
    try:
        pygame.mixer.quit()
        pygame.mixer.init(devicename=nome_dispositivo)
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

def gerar_e_tocar_alerta_teste(mensagem, modo="todos", dispositivo_especifico=None):
    try:
        timestamp = int(time.time() * 1000)
        audio_file = os.path.join(PASTA_TEMP, f"alerta_teste_{timestamp}.mp3")
        tts = gTTS(text=mensagem, lang='pt')
        tts.save(audio_file)
        
        if modo == "todos":
            for disp in DISPOSITIVOS_AUDIO:
                tocar_audio_em_dispositivo(disp, audio_file)
        elif modo == "unico" and dispositivo_especifico:
            tocar_audio_em_dispositivo(dispositivo_especifico, audio_file)
            
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
                for disp in DISPOSITIVOS_AUDIO:
                    if not alarme_ativo:
                        break
                    tocar_audio_em_dispositivo(disp, ARQUIVO_OFICIAL_ALERTA, volume)

            for _ in range(intervalo):
                if not alarme_ativo or estourou():
                    break
                time.sleep(1)

        pygame.mixer.quit()
    except Exception as e:
        log_msg(f"Erro no loop de alarme: {e}")

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
def _achar_chromedriver():
    """Procura um chromedriver.exe local antes de tentar baixar qualquer coisa.

    Ordem: pasta do script -> drivers\\ -> PATH -> caches do Selenium Manager e
    do webdriver_manager. Evita depender de internet/download no arranque.
    """
    candidatos = [
        os.path.join(PASTA_SCRIPT, "chromedriver.exe"),
        os.path.join(PASTA_SCRIPT, "drivers", "chromedriver.exe"),
        os.path.join(PASTA_SCRIPT, "chromedriver", "chromedriver.exe"),
    ]
    do_path = shutil.which("chromedriver")
    if do_path:
        candidatos.append(do_path)

    raizes = [
        os.path.join(os.path.expanduser("~"), ".cache", "selenium"),
        os.path.join(os.path.expanduser("~"), ".wdm"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "WebDriver"),
    ]
    for raiz in raizes:
        if not raiz or not os.path.isdir(raiz):
            continue
        for pasta, _sub, arquivos in os.walk(raiz):
            if "chromedriver.exe" in arquivos:
                candidatos.append(os.path.join(pasta, "chromedriver.exe"))

    for caminho in candidatos:
        if caminho and os.path.isfile(caminho):
            return caminho
    return None


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


def criar_driver_chrome(options):
    """Cria o webdriver.Chrome priorizando o chromedriver local.

    Só tenta baixar da internet se não houver nenhum chromedriver na máquina.
    """
    achado = _achar_chromedriver()
    if achado:
        log_msg(f"🚗 chromedriver local: {achado}")
        return webdriver.Chrome(service=Service(executable_path=achado), options=options)

    if ChromeDriverManager is None:
        raise RuntimeError(
            "nenhum chromedriver.exe encontrado e o pacote webdriver-manager não está instalado"
        )
    log_msg("🚗 Nenhum chromedriver local — tentando baixar (webdriver_manager)...")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def iniciar_selenium():
    global driver, CHROME_HWND, _SELENIUM_RODANDO
    if _SELENIUM_RODANDO:
        log_msg("🔁 O monitoramento já está rodando — ignorando novo pedido de início.")
        return
    _SELENIUM_RODANDO = True
    try:
        log_msg("Iniciando WebDriver no 'limbo' (fora da tela)...")

        perfil_chrome = _pasta_perfil_chrome()
        if perfil_chrome:
            # perfil fixo + Chrome órfão de execução anterior = pasta travada
            _encerrar_instancias_antigas(perfil_chrome)

        def montar_options(com_perfil=True):
            opcoes = webdriver.ChromeOptions()
            opcoes.add_experimental_option(
                "prefs", {"profile.default_content_setting_values.notifications": 2})
            for argumento in ("--disable-gpu", "--no-sandbox", "--log-level=3",
                              "--window-position=-10000,-10000"):
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
                log_msg("   Baixe o chromedriver da sua versão do Chrome em "
                        "https://googlechromelabs.github.io/chrome-for-testing/ e salve como "
                        f"'chromedriver.exe' nesta pasta: {PASTA_SCRIPT}")
                log_msg("   O monitoramento NÃO foi iniciado. Feche e abra de novo depois de colocar o arquivo.")
                return

        time.sleep(2)
        CHROME_HWND = capturar_hwnd_pelo_pid()

        if CHROME_HWND:
            log_msg(f"Navegador interceptado com sucesso pelo PID. HWND: {CHROME_HWND}")
            gerenciar_janelas("ocultar")
        else:
            log_msg("ERRO: Não foi possível rastrear a janela do Chrome gerada.")

        try:
            if not fazer_login():
                log_msg("🛑 Login não concluído — monitoramento não iniciado. Ajuste o credenciais.json e reinicie.")
                return
            verificar_botao_chamar_senha()
        except Exception as e:
            log_msg(f"Erro Crítico no loop do Selenium: {e}")
    finally:
        _SELENIUM_RODANDO = False

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

    espera = 5.0
    for tentativa in range(1, 21):
        for nome_rota, funcao_login, url in rotas:
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
                solicitar_credenciais("recusadas")
                return False
        time.sleep(espera)
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


def verificar_botao_chamar_senha():
    cred = carregar_credenciais()
    driver.get(cred["url_lista_espera"])
    while True:
        try:
            cfg = carregar_filtros()
            fechar_modal()
            botao = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@class='btn btn-xs btn-default callTicketBtn']"))
            )
            botao.click()
            verificar_botao_explicacao()
            time.sleep(cfg["intervalo_busca_segundos"])
        except Exception:
            time.sleep(5)

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

def on_tray_exit(icon, item):
    log_msg("Desligando sistema. Limpando memória e rastros...")
    icon.stop()
    
    if driver:
        try: driver.quit()
        except: pass
        
    try:
        if os.path.exists(ARQUIVO_OFICIAL_ALERTA):
            os.remove(ARQUIVO_OFICIAL_ALERTA)
    except:
        pass
        
    os._exit(0)

def iniciar_tray():
    global tray_icon
    menu = pystray.Menu(
        item('Mostrar / Ocultar Painel', on_tray_click, default=True),
        item('Mostrar Painel', on_tray_mostrar),
        item('Ocultar Painel', on_tray_ocultar),
        pystray.Menu.SEPARATOR,
        item('Encerrar Sistema', on_tray_exit),
    )
    tray_icon = pystray.Icon("Monitor_Feegow", criar_icone_imagem("blue"), "Monitor Feegow (MC)", menu)
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
        indice = {"senhas": 0, "login": 1, "alertas": 2}.get(str(aba).lower(), 0)
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
root.title("Painel de Depuração - Monitor Feegow")
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
tk.Button(frame_estudio, text="Testar Texto", bg="#ff9900", fg="black", font=("Consolas", 10, "bold"), command=lambda: threading.Thread(target=gerar_e_tocar_alerta_teste, args=(entry_voz.get(), "todos"), daemon=True).start()).pack(side=tk.RIGHT, padx=10)

frame_audio = tk.LabelFrame(root, text="Testes de Hardware de Áudio", bg="#1e1e1e", fg="white", font=("Consolas", 10))
frame_audio.pack(fill=tk.X, padx=10, pady=5)

for disp in DISPOSITIVOS_AUDIO:
    frame_linha = tk.Frame(frame_audio, bg="#1e1e1e")
    frame_linha.pack(fill=tk.X, padx=5, pady=2)
    tk.Label(frame_linha, text=f"🔊 {disp}", bg="#1e1e1e", fg="#cccccc").pack(side=tk.LEFT)
    tk.Button(frame_linha, text="Testar", bg="#444444", fg="white", command=lambda d=disp: threading.Thread(target=gerar_e_tocar_alerta_teste, args=("Teste.", "unico", d), daemon=True).start()).pack(side=tk.RIGHT)

if __name__ == "__main__":
    carregar_filtros(forcar=True)
    carregar_credenciais()
    preparar_audio_inicial()

    if credenciais_incompletas():
        log_msg("🔐 credenciais.json sem login/senha válidos — aguardando preenchimento na janela de configuração.")
        root.after(600, lambda: solicitar_credenciais("primeira_vez"))
    else:
        threading.Thread(target=iniciar_selenium, daemon=True).start()

    threading.Thread(target=iniciar_tray, daemon=True).start()
    root.mainloop()
