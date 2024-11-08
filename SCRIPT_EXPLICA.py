eimport os
import time
import pyttsx3  # Importando a biblioteca para síntese de voz
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, ElementNotInteractableException, TimeoutException

# Inicializando o PyTTSx3
engine = pyttsx3.init()
engine.setProperty('rate', 150)  # Definindo a taxa de fala
engine.setProperty('volume', 1)   # Definindo o volume (0.0 a 1.0)

# Listar as vozes disponíveis e selecionar a voz masculina em português
voices = engine.getProperty('voices')
for voice in voices:
    if 'brazil' in voice.name.lower() or 'portuguese' in voice.languages:
        engine.setProperty('voice', voice.id)
        print(f"Voz selecionada: {voice.name}")

# Função para tocar alerta com voz masculina
def tocar_alerta_explicacao(mensagem):
    print("Preparando para tocar o alerta de áudio.")
    engine.say(mensagem)
    engine.runAndWait()
    print("Alerta de áudio tocando: 'Atenção! Senha de explicação.'")

# Configurações do WebDriver
options = webdriver.ChromeOptions()
prefs = {
    "profile.default_content_setting_values.notifications": 2,  # Desativa notificações
}
options.add_experimental_option("prefs", prefs)
options.add_argument("--headless")  # Modo headless (sem janela)
options.add_argument("--disable-gpu")  # Desativa GPU para evitar erros
options.add_argument("--no-sandbox")  # Necessário para ambientes sem interface gráfica

print("Inicializando o WebDriver em modo headless...")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
print("WebDriver inicializado com sucesso em modo headless.")

# Função para fazer login no Feegow
def fazer_login():
    print("Acessando a página de login...")
    driver.get("https://app.feegow.com/main/?P=Login")
    print("Página de login acessada. Preenchendo credenciais...")

    # Preenchendo e enviando os dados de login
    driver.execute_script("document.getElementById('User').value = 'login@exemplo.com.br';")
    driver.execute_script("document.getElementById('password').value = 'senha_exemplo';")
    driver.execute_script("document.getElementById('Entrar').click();")
    print("Login enviado automaticamente.")


# Função para verificar se o botão com "EC" está presente no modal
def verificar_botao_explicacao():
    try:
        print("Verificando a presença do botão com 'EC' no modal...")

        # Espera o botão com "EC" aparecer
        botao_ec = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located(
                (By.XPATH, "//button[normalize-space(text()) = 'EC']")
            )
        )
        
        if botao_ec:
            texto_botao = botao_ec.text.strip()
            
            # Verifica se o texto 'RECEPÇÃO' ou 'ESPECIALIDADES' estão presentes dentro do botão
            if "RECEPÇÃO" in texto_botao or "ESPECIALIDADES" in texto_botao:
                print(f"Botão com 'EC' encontrado, mas contém '{texto_botao}'. Ignorando o som.")
            else:
                print("Botão com 'EC' encontrado e 'RECEPÇÃO' ou 'ESPECIALIDADES' não presente. Tocando alerta.")
                tocar_alerta_explicacao("Atenção, Senha de explicação.")
                time.sleep(10)
                fechar_modal()  # Tenta fechar o modal após tocar o alerta

    except TimeoutException:
        print("Botão com 'EC' não encontrado no momento.")
        fechar_modal()  # Fecha o modal se o botão não for encontrado
    except Exception as e:
        print(f"Ocorreu um erro ao tentar verificar o botão com 'EC': {str(e)}")
        fechar_modal()


# Função para fechar o modal com a tecla ESC
def fechar_modal():
    try:
        print("Tentando fechar o modal com a tecla ESC...")
        actions = webdriver.ActionChains(driver)
        actions.send_keys('\ue00c').perform()  # Código '\ue00c' é equivalente à tecla ESC
        print("Modal fechado com sucesso.")
    except (NoSuchElementException, ElementNotInteractableException, TimeoutException) as e:
        print("Nenhum modal encontrado ou não foi possível fechá-lo:", e)

# Função para monitorar o botão de chamada de senha
def verificar_botao_chamar_senha():
    print("Acessando a página de lista de espera...")
    driver.get("https://app.feegow.com/pre-v8/?P=ListaEspera&Pers=1")

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.CLASS_NAME, "callTicketBtn"))
    )
    print("Página carregada. Iniciando monitoramento do botão 'Chamar senha'.")

    while True:
        try:
            print("Procurando o botão 'Chamar senha'...")
            fechar_modal()  # Fecha o modal, se estiver visível

            botao = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CLASS_NAME, "callTicketBtn"))
            )
            botao.click()
            print("Botão 'Chamar senha' encontrado e clicado.")

            verificar_botao_explicacao()  # Verifica o botão "EC" após o clique
            
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
    driver.quit()
    print("Recursos liberados e o script foi encerrado.")
