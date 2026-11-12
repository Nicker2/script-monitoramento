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


def normalizar_texto(texto):
    """Maiúsculas, sem acento, sem hífen/barra/pontuação e com espaços simples.

    A tela do Feegow varia entre 'DILATAÇÃO - CCC', 'DILATAÇÃO-CCC' e
    'RECEPÇÃO 1° ANDAR' (ou '1º'), então tudo vira a mesma forma canônica:
    'DILATACAO CCC', 'RECEPCAO 1 ANDAR'.
    """
    base = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(c for c in base if not unicodedata.combining(c))
    for simbolo in ("°", "º", "ª", "-", "–", "—", ".", ",", "/", "\\", "(", ")", "[", "]", ":", ";", "|", "_"):
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
    global _CRED_USUARIO_LOGADO
    if not os.path.exists(ARQ_CREDENCIAIS):
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

def gerenciar_janelas(acao):
    global is_visible, CHROME_HWND
    if acao == "ocultar":
        root.withdraw()
        if CHROME_HWND:
            ctypes.windll.user32.ShowWindow(CHROME_HWND, 0)
        is_visible = False
        log_msg("Painel e Navegador ocultados da barra de tarefas.")
    elif acao == "mostrar":
        root.deiconify()
        if CHROME_HWND:
            ctypes.windll.user32.ShowWindow(CHROME_HWND, 5)
            ctypes.windll.user32.SetWindowPos(CHROME_HWND, 0, 100, 100, 1024, 768, 0x0040)
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
    global alarme_ativo, janela_alerta, pausa_ate, canvas_bg
    alarme_ativo = False
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

def tocar_audio_em_dispositivo(nome_dispositivo, arquivo_audio):
    try:
        pygame.mixer.quit()
        pygame.mixer.init(devicename=nome_dispositivo)
        pygame.mixer.music.load(arquivo_audio)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
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

    try:
        while alarme_ativo:
            if os.path.exists(ARQUIVO_OFICIAL_ALERTA):
                for disp in DISPOSITIVOS_AUDIO:
                    if not alarme_ativo: break
                    tocar_audio_em_dispositivo(disp, ARQUIVO_OFICIAL_ALERTA)
            
            for _ in range(15):
                if not alarme_ativo: break
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
    global driver, CHROME_HWND
    log_msg("Iniciando WebDriver no 'limbo' (fora da tela)...")
    options = webdriver.ChromeOptions()
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--log-level=3")
    options.add_argument("--window-position=-10000,-10000") 

    try:
        driver = criar_driver_chrome(options)
    except Exception as e:
        log_msg(f"🛑 O WebDriver não iniciou: {type(e).__name__}: {e}")
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


def _localizar_botao_senha(cfg, timeout=8.0):
    """Acha o botão da senha no modal com WebDriverWait + XPath.

    Retorna None quando não há senha no modal — é assim que se detecta que a
    senha saiu da fila e o alarme pode ser desarmado.
    """
    termos = [str(t).strip().upper() for t in (cfg.get("termos_gatilho") or []) if str(t).strip()]
    if not termos:
        return None
    try:
        return WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.XPATH, _xpath_botao_senha(termos)))
        )
    except TimeoutException:
        return None
    except Exception:
        return None


def verificar_botao_explicacao():
    global alarme_ativo
    cfg = carregar_filtros()

    botao_senha = _localizar_botao_senha(cfg, timeout=8.0)

    if botao_senha is None:
        # Nenhuma senha no modal: se o alarme estava tocando, a senha saiu da fila
        if alarme_ativo:
            root.after(0, lambda: desarmar_alarme(manual=False))
        fechar_modal()
        return

    try:
        texto_botao = botao_senha.text.strip()
        disparar, motivo = avaliar_chamada(texto_botao)
        log_msg(f"🔎 Senha '{texto_botao}' -> {motivo}")
        if disparar:
            if datetime.datetime.now() > pausa_ate:
                disparar_estado_alarme()
            else:
                log_msg(f"Ignorando chamada. Dentro do período de pausa até {pausa_ate.strftime('%H:%M:%S')}.")
    except Exception as e:
        log_msg(f"⚠️ Falha ao avaliar a senha: {e}")
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

def on_tray_click(icon, item):
    if is_visible:
        gerenciar_janelas("ocultar")
    else:
        gerenciar_janelas("mostrar")

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
    menu = pystray.Menu(item('Mostrar / Ocultar Painel', on_tray_click, default=True), item('Encerrar Sistema', on_tray_exit))
    tray_icon = pystray.Icon("Monitor_Feegow", criar_icone_imagem("blue"), "Monitor Feegow (MC)", menu)
    tray_icon.run()

# ==============================================================================
# INTERFACE GRÁFICA (TKINTER) E INICIALIZAÇÃO
# ==============================================================================
root = tk.Tk()
root.title("Painel de Depuração - Monitor Feegow")
root.geometry("800x650")
root.configure(bg="#1e1e1e")
root.withdraw()
root.protocol("WM_DELETE_WINDOW", ao_fechar_janela)

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
    
    threading.Thread(target=iniciar_selenium, daemon=True).start()
    threading.Thread(target=iniciar_tray, daemon=True).start()
    root.mainloop()
