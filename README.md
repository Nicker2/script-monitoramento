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

Contribuições são bem-vindas! Sinta-se à vontade para abrir issues para relatar bugs, solicitar novas funcionalidades ou enviar pull requests com melhorias.

1. Fork o repositório
2. Crie uma nova branch (`git checkout -b feature-nova-funcionalidade`)
3. Commit suas mudanças (`git commit -m 'Adiciona nova funcionalidade'`)
4. Push para a branch (`git push origin feature-nova-funcionalidade`)
5. Abra um Pull Request

---

## Licença

Este projeto é licenciado sob a Licença MIT. Consulte o arquivo [LICENSE](LICENSE) para obter mais detalhes.

---

## Autor

Desenvolvido por Nicolas Bonza Cavalari Borges.
