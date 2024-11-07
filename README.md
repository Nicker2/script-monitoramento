Aqui está o seu `README.md` atualizado com as descrições que você solicitou e incluindo sua autoria no final:

---

# Monitoramento de Senha no Feegow

Script em Python que usa Selenium para monitorar e clicar automaticamente no botão "Chamar senha" no sistema Feegow. Ele verifica se há uma senha específica relacionada a explicações médicas ("EC") e, caso detectado, emite um alerta sonoro com voz sintetizada.

## Índice

- [Descrição](#descrição)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Uso](#uso)
- [Contribuição](#contribuição)
- [Licença](#licença)
- [Autor](#autor)

---

## Descrição

Este script automatiza o monitoramento da página de lista de espera no sistema Feegow, clicando no botão "Chamar senha" e verificando se existe um botão com a sigla "EC" no modal. Quando encontrado, o script emite um alerta sonoro com a mensagem "Atenção, Senha de Explicação". Esse alerta utiliza voz sintetizada em português. O script foi configurado para operar em modo headless, facilitando a execução em segundo plano.

---

### **SCRIPT_EXPLICA.py**
**Descrição**:  
Este é o script principal que contém o código responsável por realizar a verificação e execução de tarefas automáticas. Ele lida com a interação com os elementos da página, como encontrar o botão "EC" e verificar se o texto "RECEPÇÃO" está presente, além de realizar o acionamento do alerta de voz quando necessário.

---

### **iniciar_explica.py**
**Descrição**:  
Este script é responsável por iniciar o processo de execução do script Python invisível. Ele verifica se o executável do Python e o script invisível existem nos caminhos definidos. Caso ambos sejam encontrados, ele executa o script `executar_invisivel_explica.py` de maneira oculta, sem abrir a janela do terminal. Isso é feito utilizando a função `subprocess.Popen`, com o argumento `CREATE_NO_WINDOW`, que impede que a janela do terminal seja exibida.

---

### **executar_invisivel_explica.py**
**Descrição**:  
Este script faz parte do processo de execução invisível do código. Ele verifica se os caminhos para o executável do Python e o script Python principal (`SCRIPT_EXPLICA.py`) estão corretos e existem. Caso tudo esteja certo, o script executa `SCRIPT_EXPLICA.py` de forma oculta, utilizando a função `subprocess.Popen` com a flag `CREATE_NO_WINDOW`. Se ocorrer algum erro durante a execução do script, ele é registrado no arquivo de log `error_log.txt`, e o script é reiniciado a cada 5 segundos. Esse processo garante que o script principal seja reiniciado automaticamente em caso de falha.

---

### **executar_iniciar_explica.BAT**
**Descrição**:  
Este é um arquivo de script batch responsável por iniciar o processo de execução do Python, rodando o script `iniciar_explica.py`. Ele invoca o Python de maneira silenciosa (`start /b`) e, caso ocorra algum erro na execução do script, o erro é verificado e, se necessário, o script é reiniciado automaticamente, aguardando 3 segundos antes de reiniciar o processo.

---

### **error_log.txt**
**Descrição**:  
Este arquivo de log é utilizado para armazenar os erros que ocorrem durante a execução do script invisível. Caso haja algum erro ao tentar rodar o script, o traceback do erro é registrado neste arquivo para facilitar o diagnóstico e a resolução de problemas. Isso ajuda a monitorar e depurar o funcionamento dos scripts sem precisar observar a execução diretamente.

---

### **DEBUG_EXECUTAR_EXPLICA.BAT**
**Descrição**:  
Este arquivo batch é semelhante ao `executar_iniciar_explica.BAT`, mas é utilizado para fins de depuração. Ele também executa o script `SCRIPT_EXPLICA.py` de maneira silenciosa e reinicia o processo em caso de erro, mas com uma função extra de depuração, como exibir mensagens de erro diretamente no console.

---

## Pré-requisitos

Antes de executar o script, você precisa ter o seguinte instalado no seu sistema:

- [Python 3.x](https://www.python.org/downloads/)
- [ChromeDriver](https://sites.google.com/chromium.org/driver/) (pode ser instalado automaticamente com o `webdriver_manager`)
- Bibliotecas Python necessárias:
  - `selenium`
  - `webdriver_manager`
  - `pyttsx3`

---

## Instalação

1. Clone este repositório:

   ```bash
   git clone https://github.com/seuusuario/MonitoramentoSenhaFeegow.git
   cd MonitoramentoSenhaFeegow
   ```

2. Instale as dependências do Python:

   ```bash
   pip install -r requirements.txt
   ```

3. Verifique se o ChromeDriver está instalado e atualizado para a versão do Chrome em seu sistema. O script usa o `webdriver_manager` para instalar automaticamente a versão correta do ChromeDriver.

---

## Uso

1. Configure as credenciais de login no script, substituindo os valores `exemplo@dominio.com` e `senha_exemplo` pelas suas credenciais do Feegow:

   ```python
   driver.execute_script("document.getElementById('User').value = 'exemplo@dominio.com';")
   driver.execute_script("document.getElementById('password').value = 'senha_exemplo';")
   ```

2. Execute o script:

   ```bash
   python nome_do_script.py
   ```

3. O script acessará automaticamente a página de login do Feegow, fará o login, navegará para a página de lista de espera e monitorará o botão "Chamar senha".

4. Quando o botão com "EC" for encontrado, o alerta de voz será ativado. Se o modal aparecer, o script tentará fechá-lo automaticamente.

---

## Contribuição

Contribuições são sempre bem-vindas! Se você quiser ajudar a melhorar este projeto, siga as etapas abaixo. Vamos explicar o que você precisa fazer para enviar suas melhorias, corrigir bugs ou sugerir novas funcionalidades.

### Como Contribuir:

1. **Faça um Fork do Repositório**
   
   O primeiro passo para contribuir é fazer um "fork" deste repositório. Um fork cria uma cópia do repositório no seu GitHub, permitindo que você faça alterações sem afetar o projeto original.
   
   Para fazer o fork:
   - Vá até a página do repositório no GitHub.
   - No canto superior direito, clique no botão **Fork**.
   - Isso criará uma cópia do projeto na sua conta do GitHub.

2. **Crie uma Nova Branch**

   Depois de fazer o fork e clonar o repositório para o seu computador, crie uma nova "branch" (ramificação) onde você poderá trabalhar nas suas alterações. Isso é importante porque, ao trabalhar em uma branch separada, você evita fazer alterações no código principal (a **master** ou **main branch**).
   
   Para criar uma nova branch:
   - Abra o terminal no diretório do projeto clonado.
   - Digite o comando para criar e mudar para uma nova branch:

     ```bash
     git checkout -b nome-da-nova-branch
     ```

   Substitua **nome-da-nova-branch** por um nome que faça sentido para o que você está implementando. Exemplo: `feature-corrigir-bug` ou `feature-adicionar-funcionalidade`.

3. **Faça as Suas Alterações**

   Agora que você está na sua nova branch, faça as alterações necessárias no código. Isso pode incluir:
   - Corrigir um bug.
   - Adicionar uma nova funcionalidade.
   - Melhorar a documentação (README, por exemplo).
   
   Após fazer as alterações, você pode testar o código para garantir que tudo esteja funcionando corretamente.

4. **Commit Suas Mudanças**

   Quando terminar suas alterações, você precisa **salvar** essas mudanças localmente no seu repositório (isso é chamado de "commit"). O commit é uma forma de registrar suas alterações.

   Para fazer isso:
   - Adicione os arquivos que você alterou para o commit:

     ```bash
     git add .
     ```

   - Agora, faça o commit com uma mensagem explicando o que você fez:

     ```bash
     git commit -m "Mensagem descrevendo a alteração"
     ```

   A mensagem deve ser clara e descritiva, para que qualquer pessoa possa entender o que foi alterado sem olhar o código.

5. **Faça o Push para o seu Repositório no GitHub**

   Após o commit, você precisa enviar as mudanças para o repositório do GitHub. Esse processo é chamado de **push**. Ele envia seus commits da sua máquina para o repositório remoto no GitHub.

   Para fazer isso, use o seguinte comando:

   ```bash
   git push origin nome-da-nova-branch
   ```

   Isso vai enviar a nova branch com as alterações para o seu repositório no GitHub.

6. **Abra um Pull Request**

   Agora que suas alterações estão no seu repositório no GitHub, você pode **propor** essas mudanças ao repositório original. Para isso, você abre um **Pull Request**.

   - No seu repositório no GitHub, você verá um botão **Compare & Pull Request**. Clique nele.
   - No formulário que aparecer, explique o que você fez e por que acredita que suas mudanças são importantes.
   - Clique em **Create Pull Request**.



Após isso, a equipe do projeto irá revisar suas mudanças e, se tudo estiver certo, elas serão adicionadas ao código principal do repositório.

---

## Licença

Este projeto é licenciado sob a Licença MIT - veja o arquivo [LICENSE.md](LICENSE.md) para detalhes.

---

## Autor

Criado por Nicolas Bonza Cavalari Borges.

---
