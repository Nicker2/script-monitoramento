import os
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

import psutil
from gtts import gTTS
import pygame
from pygame._sdl2 import audio as sdl2_audio

import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw

from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
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
    "termo_gatilho": "MC",
    "comparacao": "contem",
    "blacklist": [
        "CONSULTA", "EXAME", "PRIORITÁRIA", "AGENDAMENTOS", "RETORNO", "R2",
        "DILATAÇÃO", "CMC", "CMM", "FENIL", "RECEPÇÃO", "SUS", "ESPECIALIDADES",
        "CIRURGIA", "LENTES", "AUTO REFRATOR"
    ],
    "whitelist": [],
    "pausa_minutos": 15,
    "intervalo_busca_segundos": 10,
}

CREDENCIAIS_PADRAO = {
    "usuario": "exemplo@exemplo.com",
    "senha": "exemplo",
    "url_login": "https://app.feegow.com/main/?P=Login",
    "url_lista_espera": "https://app.feegow.com/pre-v8/?P=ListaEspera&Pers=1",
}

_FILTROS = dict(FILTROS_PADRAO)
_FILTROS_MTIME = 0.0


def normalizar_texto(texto):
    """Maiúsculas, sem acento e com espaços simples — base das comparações."""
    base = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(c for c in base if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


def _ler_json(caminho):
    with open(caminho, "r", encoding="utf-8-sig") as arquivo:
        return json.load(arquivo)


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
        "blacklist = IGNORA a chamada; whitelist = DISPARA o alarme mesmo que também "
        "case com a blacklist; termo_gatilho = sigla do botão (ex.: MC)."
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
            "termo_gatilho": normalizar_texto(combinado.get("termo_gatilho")),
            "comparacao": str(combinado.get("comparacao") or "contem").strip().lower(),
            "blacklist": _lista_normalizada(combinado.get("blacklist")),
            "whitelist": _lista_normalizada(combinado.get("whitelist")),
            "pausa_minutos": max(1, int(combinado.get("pausa_minutos") or 15)),
            "intervalo_busca_segundos": max(1, int(combinado.get("intervalo_busca_segundos") or 10)),
        }
        _FILTROS_MTIME = mtime
        log_msg(
            "⚙️ filtros.json carregado: gatilho '"
            f"{_FILTROS['termo_gatilho']}', {len(_FILTROS['blacklist'])} na blacklist, "
            f"{len(_FILTROS['whitelist'])} na whitelist."
        )
    except Exception as e:
        _FILTROS_MTIME = mtime
        log_msg(f"⚠️ Erro ao ler filtros.json ({e}). Mantendo a configuração anterior.")
    return _FILTROS


def avaliar_chamada(texto_botao):
    """Decide se o texto do botão dispara o alarme.

    Prioridade:
      1. Whitelist -> dispara sempre que casar (vence a blacklist).
      2. Blacklist -> ignora a chamada (falso positivo).
      3. Nada casou -> dispara (é marcação cirúrgica de verdade).
    """
    cfg = carregar_filtros()
    texto = normalizar_texto(texto_botao)
    termo = cfg["termo_gatilho"]
    modo_exato = cfg["comparacao"] in ("exata", "exato", "igual")

    if termo and termo not in texto:
        return False, f"não contém o termo gatilho '{termo}'"

    def casou(item):
        return texto == item if modo_exato else item in texto

    for item in cfg["whitelist"]:
        if casou(item):
            return True, f"whitelist ('{item}')"

    for item in cfg["blacklist"]:
        if casou(item):
            return False, f"blacklist ('{item}')"

    return True, "marcação cirúrgica confirmada"


def carregar_credenciais():
    """Lê credenciais.json (cria a partir do exemplo na primeira execução)."""
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
        "url_login": str(combinado.get("url_login") or CREDENCIAIS_PADRAO["url_login"]),
        "url_lista_espera": str(combinado.get("url_lista_espera") or CREDENCIAIS_PADRAO["url_lista_espera"]),
    }

    if not credenciais["usuario"] or credenciais["usuario"].lower() == CREDENCIAIS_PADRAO["usuario"]:
        log_msg("⚠️ ATENÇÃO: credenciais.json está com os dados de EXEMPLO. Preencha usuário e senha.")
    else:
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
            pid_janela = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_janela))
            if pid_janela.value in pids_chrome:
                if ctypes.windll.user32.GetWindowTextLengthW(hwnd) > 0:
                    hwnds_encontrados.append(hwnd)
            return True
            
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
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
    texto_padrao = "Atenção. Senha de marcação cirúrgica."
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
            texto_padrao = "Atenção. Senha de marcação cirúrgica."
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
    if not alarme_ativo:
        log_msg("🚨 Iniciando Estado de Alarme!")
        alarme_ativo = True
        root.after(0, criar_janela_alarme)
        threading.Thread(target=loop_audio_alarme, daemon=True).start()

# ==============================================================================
# LÓGICA DO SELENIUM E BYPASS INVISÍVEL VIA JS
# ==============================================================================
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

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    time.sleep(2)
    CHROME_HWND = capturar_hwnd_pelo_pid()
    
    if CHROME_HWND:
        log_msg(f"Navegador interceptado com sucesso pelo PID. HWND: {CHROME_HWND}")
        gerenciar_janelas("ocultar")
    else:
        log_msg("ERRO: Não foi possível rastrear a janela do Chrome gerada.")
    
    try:
        fazer_login()
        verificar_botao_chamar_senha()
    except Exception as e:
        log_msg(f"Erro Crítico no loop do Selenium: {e}")

def fazer_login():
    cred = carregar_credenciais()
    usuario = cred["usuario"]
    senha = cred["senha"]
    url_login = cred["url_login"]
    # json.dumps escapa aspas/barras: a senha pode ter apóstrofo ou aspas sem quebrar o JS
    usuario_js = json.dumps(usuario)
    senha_js = json.dumps(senha)

    while True:
        log_msg("Acessando a página de login...")
        driver.get(url_login)
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "User")))
            driver.execute_script(f"document.getElementById('User').value = {usuario_js};")
            driver.execute_script(f"document.getElementById('password').value = {senha_js};")
            driver.execute_script("document.getElementById('Entrar').click();")
            log_msg("Login preenchido. Aguardando validação do servidor...")

            try:
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Este usuário já está conectado em outra máquina.')]")))

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
                WebDriverWait(driver, 300).until(EC.presence_of_element_located((By.CLASS_NAME, "callTicketBtn")))
                log_msg("✔️ Bypass efetuado! Conexão estabelecida com sucesso.")
                break

            except TimeoutException:
                try:
                    WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "callTicketBtn")))
                    log_msg("✔️ Login direto bem-sucedido. Nenhuma barreira encontrada.")
                    break
                except TimeoutException:
                    log_msg("Demora inesperada na resposta do Feegow. Reiniciando o processo...")
                    continue
        except Exception as e:
            log_msg("Falha ao injetar os dados de login. Retentando em 2 segundos...")
            time.sleep(2)

def fechar_modal():
    try:
        webdriver.ActionChains(driver).send_keys('\ue00c').perform() # ESC
    except:
        pass

def verificar_botao_explicacao():
    global alarme_ativo
    cfg = carregar_filtros()
    termo_botao = (cfg["termo_gatilho"] or "MC").replace('"', "")
    try:
        botao_mc = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.XPATH, f"//button[contains(normalize-space(text()), '{termo_botao}')]"))
        )
        if botao_mc:
            texto_botao = botao_mc.text.strip()
            disparar, motivo = avaliar_chamada(texto_botao)
            log_msg(f"🔎 Botão '{texto_botao}' -> {motivo}")
            if disparar:
                if datetime.datetime.now() > pausa_ate:
                    disparar_estado_alarme()
                else:
                    log_msg(f"Ignorando chamada. Dentro do período de pausa até {pausa_ate.strftime('%H:%M:%S')}.")
            fechar_modal()
    except TimeoutException:
        if alarme_ativo:
            root.after(0, lambda: desarmar_alarme(manual=False))
        fechar_modal()
    except Exception:
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
entry_voz.insert(0, "Atenção. Senha de marcação cirúrgica.")
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
