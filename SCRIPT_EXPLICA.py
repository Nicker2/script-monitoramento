import os
import time
import threading
import datetime
import ctypes
import tkinter as tk
from tkinter import scrolledtext

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

BLACKLIST_SENHAS = [
    "CONSULTA", "EXAME", "PRIORITÁRIA", "AGENDAMENTOS", "RETORNO", "R2",
    "DILATAÇÃO", "CMC", "CMM", "FENIL", "RECEPÇÃO", "SUS", "ESPECIALIDADES",
    "CIRURGIA", "LENTES", "AUTO REFRATOR"
]

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
        pausa_ate = datetime.datetime.now() + datetime.timedelta(minutes=15)
        log_msg(f"✅ Alerta pausado manualmente. Busca ignorará MC até {pausa_ate.strftime('%H:%M:%S')}.")
    else:
        log_msg("🔄 A senha sumiu. Desarme remoto efetuado. Retomando busca imediata.")

# ==============================================================================
# FUNÇÕES DE ÁUDIO E LOOP
# ==============================================================================
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

def gerar_e_tocar_alerta(mensagem, modo="todos", dispositivo_especifico=None):
    try:
        audio_file = "alerta_temp.mp3"
        tts = gTTS(text=mensagem, lang='pt')
        tts.save(audio_file)
        if modo == "todos":
            for disp in DISPOSITIVOS_AUDIO:
                tocar_audio_em_dispositivo(disp, audio_file)
        elif modo == "unico" and dispositivo_especifico:
            tocar_audio_em_dispositivo(dispositivo_especifico, audio_file)
        pygame.mixer.quit()
        os.remove(audio_file)
    except Exception as e:
        log_msg(f"Erro de áudio: {str(e)}")

def loop_audio_alarme():
    global alarme_ativo
    texto_padrao = "Atenção. Senha de marcação cirúrgica."
    while alarme_ativo:
        gerar_e_tocar_alerta(texto_padrao, "todos")
        for _ in range(15):
            if not alarme_ativo:
                break
            time.sleep(1)

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
    usuario = 'exemplo@exemplo.com'
    senha = 'exemplo'
    
    while True:
        log_msg("Acessando a página de login...")
        driver.get("https://app.feegow.com/main/?P=Login")
        try:
            # Preenchimento padrão
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "User")))
            driver.execute_script(f"document.getElementById('User').value = '{usuario}';")
            driver.execute_script(f"document.getElementById('password').value = '{senha}';")
            driver.execute_script("document.getElementById('Entrar').click();")
            log_msg("Login preenchido. Aguardando validação do servidor...")

            try:
                # 1. Tenta identificar se bateu na tela de "usuário já conectado"
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Este usuário já está conectado em outra máquina.')]")))
                
                log_msg("⚠️ Sessão dupla detectada! Acionando metralhadora de Bypass Invisível...")
                
                # A mágica do JS Injetado no cérebro da página!
                js_bypass = f"""
                const user = '{usuario}';
                const pass = '{senha}';
                const actionUrl = window.location.href;

                // Transforma a página vermelha numa tela limpa de carregamento
                document.body.innerHTML = '<div style="display:flex; justify-content:center; align-items:center; height:100vh; background:#17a2b8; color:white; font-family:sans-serif; flex-direction:column;"><h2>Autenticando Acesso</h2><p>Bypass Invisível em andamento...</p></div>';

                // Loop silencioso via Fetch API
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
                            
                            // Se a resposta ainda for erro, espera só 50 milissegundos e atira de novo
                            if (html.includes('Este usuário já está conectado')) {{
                                await new Promise(resolve => setTimeout(resolve, 50));
                            }} else {{
                                // Liberou! Redireciona a tela real do Selenium pra dentro.
                                sucesso = true;
                                window.location.href = res.url || 'https://app.feegow.com/main/';
                            }}
                        }} catch (e) {{
                            await new Promise(resolve => setTimeout(resolve, 500));
                        }}
                    }}
                }})();
                """
                
                # Dispara a injeção
                driver.execute_script(js_bypass)
                
                # O Python agora só senta e espera o JS terminar de arrombar a porta e carregar a lista
                log_msg("JS injetado. Fuzilando requisições em background...")
                WebDriverWait(driver, 300).until(EC.presence_of_element_located((By.CLASS_NAME, "callTicketBtn")))
                log_msg("✔️ Bypass efetuado! Conexão estabelecida com sucesso.")
                break

            except TimeoutException:
                # 2. Se a mensagem NÃO apareceu, o login pode ter passado de primeira. Confirma se a home carregou:
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
    try:
        botao_mc = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.XPATH, "//button[contains(normalize-space(text()), 'MC')]"))
        )
        if botao_mc:
            texto_botao = botao_mc.text.strip().upper()
            if "MC" in texto_botao:
                falso_positivo = any(palavra in texto_botao for palavra in BLACKLIST_SENHAS)
                if not falso_positivo:
                    if datetime.datetime.now() > pausa_ate:
                        disparar_estado_alarme()
                    else:
                        log_msg("Ignorando chamada. Dentro do período de pausa de 15min.")
            fechar_modal()
    except TimeoutException:
        if alarme_ativo:
            root.after(0, lambda: desarmar_alarme(manual=False))
        fechar_modal()
    except Exception:
        fechar_modal()

def verificar_botao_chamar_senha():
    driver.get("https://app.feegow.com/pre-v8/?P=ListaEspera&Pers=1")
    while True:
        try:
            fechar_modal()
            botao = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@class='btn btn-xs btn-default callTicketBtn']"))
            )
            botao.click()
            verificar_botao_explicacao()
            time.sleep(10)
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
    log_msg("Desligando sistema. Fechando o navegador do robô...")
    icon.stop()
    if driver:
        try:
            driver.quit()
        except:
            pass
    os._exit(0)

def iniciar_tray():
    global tray_icon
    menu = pystray.Menu(item('Mostrar / Ocultar Painel', on_tray_click, default=True), item('Encerrar Sistema', on_tray_exit))
    tray_icon = pystray.Icon("Monitor_Feegow", criar_icone_imagem("blue"), "Monitor Feegow (MC)", menu)
    tray_icon.run()

# ==============================================================================
# INTERFACE GRÁFICA (TKINTER)
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
tk.Button(frame_estudio, text="Testar Texto", bg="#ff9900", fg="black", font=("Consolas", 10, "bold"), command=lambda: threading.Thread(target=gerar_e_tocar_alerta, args=(entry_voz.get(), "todos"), daemon=True).start()).pack(side=tk.RIGHT, padx=10)

frame_audio = tk.LabelFrame(root, text="Testes de Hardware de Áudio", bg="#1e1e1e", fg="white", font=("Consolas", 10))
frame_audio.pack(fill=tk.X, padx=10, pady=5)

for disp in DISPOSITIVOS_AUDIO:
    frame_linha = tk.Frame(frame_audio, bg="#1e1e1e")
    frame_linha.pack(fill=tk.X, padx=5, pady=2)
    tk.Label(frame_linha, text=f"🔊 {disp}", bg="#1e1e1e", fg="#cccccc").pack(side=tk.LEFT)
    tk.Button(frame_linha, text="Testar", bg="#444444", fg="white", command=lambda d=disp: threading.Thread(target=gerar_e_tocar_alerta, args=("Teste.", "unico", d), daemon=True).start()).pack(side=tk.RIGHT)

if __name__ == "__main__":
    threading.Thread(target=iniciar_selenium, daemon=True).start()
    threading.Thread(target=iniciar_tray, daemon=True).start()
    root.mainloop()
