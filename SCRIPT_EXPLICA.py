import os
import time
from gtts import gTTS
import pygame
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, ElementNotInteractableException, TimeoutException

# Inicializar o pygame mixer
pygame.mixer.init()

# Função para tocar alerta usando gTTS e pygame
def tocar_alerta_explicacao(mensagem):
    print("Entrando na função tocar_alerta_explicacao...")
    try:
        # Gerar o arquivo de áudio com gTTS
        tts = gTTS(text=mensagem, lang='pt')
        audio_file = "alerta.mp3"
        tts.save(audio_file)
        print("Arquivo de áudio gerado com sucesso.")

        # Carregar e reproduzir o áudio com pygame
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        print("Comando de reprodução de áudio enviado.")
        
        # Aguardar a reprodução terminar
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
        
        # Remover o arquivo temporário
        pygame.mixer.music.unload()
        os.remove(audio_file)
        print("Arquivo de áudio removido.")
    except Exception as e:
        print(f"Erro ao tocar o áudio: {str(e)}")

# Configurações do WebDriver
options = webdriver.ChromeOptions()
prefs = {
    "profile.default_content_setting_values.notifications": 2,
}
options.add_experimental_option("prefs", prefs)
options.add_argument("--headless")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--log-level=3")
options.add_argument("--disable-extensions")
options.add_argument("--disable-notifications")
options.add_argument("--disable-component-extensions-with-background-pages")
options.add_argument("--window-size=1920,1080")

print("Inicializando o WebDriver em modo headless...")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
print("WebDriver inicializado com sucesso em modo headless.")

# Função para fazer login
def fazer_login():
    while True:
        print("Acessando a página de login...")
        driver.get("https://app.feegow.com/main/?P=Login")
        print("Página de login acessada. Preenchendo credenciais...")
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "User"))
            )
            driver.execute_script("document.getElementById('User').value = 'exemplo@exemplo.com';")
            driver.execute_script("document.getElementById('password').value = 'exemplo';")
            driver.execute_script("document.getElementById('Entrar').click();")
            print("Login enviado automaticamente.")

            # Verificar se a mensagem de erro aparece
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Este usuário já está conectado em outra máquina.')]"))
                )
                print("Mensagem 'Este usuário já está conectado em outra máquina.' detectada.")
                cancel_button = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[@type='button' and @class='btn btn-secondary' and @onclick='window.history.back();']"))
                )
                cancel_button.click()
                print("Botão 'Cancelar' clicado. Tentando login novamente...")
                time.sleep(2)
                continue
            except TimeoutException:
                print("Mensagem de erro não encontrada. Verificando se o login foi bem-sucedido...")

            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "callTicketBtn"))
                )
                print(f"Login bem-sucedido. Botão 'callTicketBtn' encontrado na página pós-login.")
                break
            except TimeoutException:
                print("Erro: Login pode ter falhado ou página de lista de espera não carregada.")
                print(f"URL atual: {driver.current_url}")
                print(f"Conteúdo da página: {driver.page_source[:500]}...")
                time.sleep(2)
                continue
        except Exception as e:
            print(f"Erro ao tentar fazer login: {str(e)}")
            time.sleep(2)

# Função para fechar o modal
def fechar_modal():
    try:
        print("Tentando fechar o modal com a tecla ESC...")
        actions = webdriver.ActionChains(driver)
        actions.send_keys('\ue00c').perform()
        print("Modal fechado com sucesso.")
    except (NoSuchElementException, ElementNotInteractableException, TimeoutException) as e:
        print("Nenhum modal encontrado ou não foi possível fechá-lo:", e)

# Função para verificar botão "MC"
def verificar_botao_explicacao():
    try:
        print("Verificando a presença do botão com 'MC' no modal...")
        botao_mc = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located(
                (By.XPATH, "//button[normalize-space(text()) = 'MC']")
            )
        )
        if botao_mc:
            texto_botao = botao_mc.text.strip()
            print(f"Botão encontrado com texto: '{texto_botao}'")
            if texto_botao == "CMC":
                print("Botão com 'CMC' encontrado. Ignorando por ser outro tipo.")
                fechar_modal()
                return
            if "RECEPÇÃO" in texto_botao or "ESPECIALIDADES" in texto_botao:
                print(f"Botão com 'MC' encontrado, mas contém '{texto_botao}'. Ignorando o som.")
                fechar_modal()
            else:
                print("Botão com 'MC' encontrado e 'RECEPÇÃO' ou 'ESPECIALIDADES' não presente. Tocando alerta.")
                fechar_modal()
                tocar_alerta_explicacao("Atenção, Senha de marcação cirúrgica.")
                time.sleep(5)
    except TimeoutException:
        print("Botão com 'MC' não encontrado no momento.")
        fechar_modal()
    except Exception as e:
        print(f"Ocorreu um erro ao tentar verificar o botão com 'MC': {str(e)}")
        fechar_modal()

# Função para monitorar botão de chamada
def verificar_botao_chamar_senha():
    print("Acessando a página de lista de espera...")
    driver.get("https://app.feegow.com/pre-v8/?P=ListaEspera&Pers=1")
    print(f"URL atual após acessar a página: {driver.current_url}")
    try:
        WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CLASS_NAME, "callTicketBtn"))
        )
        print("Página carregada. Iniciando monitoramento do botão 'Chamar senha'.")
    except TimeoutException:
        print("Erro: Botão 'callTicketBtn' não encontrado. Tentando seletor alternativo...")
        try:
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@class='btn btn-xs btn-default callTicketBtn']"))
            )
            print("Botão encontrado com seletor alternativo.")
        except TimeoutException:
            print("Erro: Botão 'callTicketBtn' não encontrado com seletor alternativo. Verifique a página.")
            print(f"Conteúdo da página: {driver.page_source[:500]}...")
            return

    while True:
        try:
            print("Procurando o botão 'Chamar senha'...")
            fechar_modal()
            botao = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@class='btn btn-xs btn-default callTicketBtn']"))
            )
            botao.click()
            print("Botão 'Chamar senha' encontrado e clicado.")
            verificar_botao_explicacao()
            print("Esperando 10 segundos antes de verificar novamente...")
            time.sleep(10)
        except ElementNotInteractableException:
            print("Botão 'Chamar senha' não interativo, tentando novamente em 5 segundos.")
            time.sleep(5)
        except TimeoutException:
            print("Tempo esgotado: Botão 'Chamar senha' não encontrado.")
            time.sleep(5)
        except Exception as e:
            print("Erro ao tentar clicar no botão 'Chamar senha':", e)
            time.sleep(5)

# Executa login e monitoramento
try:
    fazer_login()
    verificar_botao_chamar_senha()
except KeyboardInterrupt:
    print("\nInterrompido pelo usuário. Fechando o navegador...")
finally:
    pygame.mixer.quit()
    driver.quit()
    print("Recursos liberados e o script foi encerrado.")
