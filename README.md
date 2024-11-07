# Monitoramento de Senha no Feegow

Script em Python que usa Selenium para monitorar e clicar automaticamente no botão "Chamar senha" no sistema Feegow. Ele verifica se há uma senha específica relacionada a explicações médicas ("EC") e, caso detectado, emite um alerta sonoro com voz sintetizada.

## Índice

- [Descrição](#descrição)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Uso](#uso)
- [Contribuição](#contribuição)
- [Licença](#licença)

---

## Descrição

Este script automatiza o monitoramento da página de lista de espera no sistema Feegow, clicando no botão "Chamar senha" e verificando se existe um botão com a sigla "EC" no modal. Quando encontrado, o script emite um alerta sonoro com a mensagem "Atenção, Senha de Explicação". Esse alerta utiliza voz sintetizada em português. O script foi configurado para operar em modo headless, facilitando a execução em segundo plano.

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

   O Pull Request será analisado por quem mantém o repositório. Se tudo estiver correto, suas alterações serão incorporadas ao código principal do projeto.

---

### Como Funciona?

Essas etapas são chamadas de **fluxo de trabalho com Git** e são uma prática comum em projetos de código aberto. Ao seguir esse processo, você garante que as alterações sejam feitas de forma organizada e controlada, e que todos possam colaborar no projeto sem sobrecarregar o código original.

---

## Licença

Este projeto é licenciado sob a Licença MIT. Consulte o arquivo [LICENSE](LICENSE) para obter mais detalhes.

---

## Autor

Desenvolvido por Nicolas Bonza Cavalari Borges.
