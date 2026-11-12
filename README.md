# Monitoramento de Senha no Feegow

Script em Python que usa Selenium para monitorar e clicar automaticamente no botão "Chamar senha" do sistema Feegow. Quando aparece uma senha de **dilatação** (ou `AUTO REFRATOR (SEM DILATAR)`) — e ela não está na lista de ignoradas — o script dispara um alarme sonoro com voz sintetizada e/ou uma janela piscante na tela.

As regras de filtro e as credenciais ficam em arquivos **JSON separados** (`filtros.json` e `credenciais.json`), então dá para mudar o comportamento sem tocar no código.

## Índice

- [Descrição](#descrição)
- [Como Funciona](#como-funciona)
- [Arquivos e Descrições](#arquivos-e-descricoes)
- [Arquivos de Configuração](#arquivos-de-configuração)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Uso](#uso)
- [Solução de Problemas](#solução-de-problemas)
- [Histórico de Alterações](#histórico-de-alterações)
- [Contribuição](#contribuição)
- [Licença](#licença)
- [Autor](#autor)

---

## Descrição

Este script automatiza o monitoramento da página de lista de espera do Feegow: clica no botão "Chamar senha", lê o texto do botão que aparece no modal e decide se aquilo é uma marcação cirúrgica de verdade.

Quando é, dispara dois alertas ao mesmo tempo:

- **Sonoro** — mensagem falada em português ("Atenção! Senha de dilatação."), tocada em **todos os dispositivos de áudio** da máquina (caixa, TV, monitor com som).
- **Visual** — janela vermelha piscando no centro da tela com o botão **CIENTE**.

Clicando em **CIENTE**, a chamada é assumida e os alertas ficam silenciados pelo tempo definido em `pausa_minutos` (padrão 15). Se a senha desaparecer da fila sem ninguém clicar, o alarme se desarma sozinho.

O Chrome roda **fora da tela** (posição `-10000,-10000`) com a janela rastreada por PID: ele fica no "limbo", sem aparecer na barra de tarefas, e pode ser exibido a qualquer momento pelo ícone da bandeja do Windows.

---

## Como Funciona

```
1. Abre o Chrome fora da tela e faz login no Feegow
   (se cair em "usuário já conectado em outra máquina", roda o bypass)
             |
2. Entra na Lista de Espera e clica em "Chamar senha"
             |
3. Lê o texto do botão que aparece no modal
             |
4. Decide o que fazer:
      whitelist casou?  -> ALERTA
      blacklist casou?  -> IGNORA
      nada casou?       -> segue padrao_sem_match (padrão: ignora)
             |
5. Espera intervalo_busca_segundos e volta para o passo 2
```

A comparação **ignora acentos e maiúsculas/minúsculas**: `PRIORITARIA` no `filtros.json` casa com `PRIORITÁRIA` na tela.

---

## Arquivos e Descrições

### **SCRIPT_EXPLICA.py**
**Descrição**:
Script principal. Faz o login no Feegow pelo Selenium, percorre a lista de espera clicando em "Chamar senha", lê o texto do botão da senha e aplica as regras do `filtros.json` para decidir se dispara o alarme. Também cuida da janela de alerta (Tkinter), do áudio sintetizado (gTTS + pygame), do ícone na bandeja e do painel de logs.

---

### **filtros.json**
**Descrição**:
Blacklist, whitelist e tempos. É recarregado **sozinho** ao salvar — não precisa reiniciar o programa. Se o arquivo não existir, o script cria na primeira execução. Detalhes na seção [Arquivos de Configuração](#arquivos-de-configuração).

---

### **credenciais.json**
**Descrição**:
Usuário, senha e URLs do Feegow. **Não é versionado** (fica no `.gitignore`, porque o repositório é público). Criado automaticamente na primeira execução a partir do `credenciais.exemplo.json`.

---

### **credenciais.exemplo.json**
**Descrição**:
Modelo versionado do arquivo de credenciais, só com valores de exemplo (`exemplo@exemplo.com` / `exemplo`). Serve de referência para montar o seu `credenciais.json`.

---

### **requirements.txt**
**Descrição**:
Lista das bibliotecas Python usadas (selenium, webdriver-manager, gTTS, pygame, pystray, Pillow e psutil) para instalar tudo com um único comando.

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

## Arquivos de Configuração

As regras de filtro e as credenciais ficam **fora do código**, em arquivos JSON. Se eles não existirem, o script **cria automaticamente** na primeira execução (com os valores padrão) — é só abrir e editar.

### **filtros.json**
Controla o que dispara o alarme. É recarregado **sozinho** ao salvar o arquivo, sem reiniciar o programa.

```json
{
  "termos_gatilho": ["DILATAÇÃO", "MC", "AUTO REFRATOR", "CONSULTA", "..."],
  "comparacao": "contem",
  "padrao_sem_match": "ignorar",
  "whitelist": ["DILATAÇÃO - CCC", "DILATAÇÃO - MMM", "..."],
  "blacklist": ["CONSULTA - COMUM", "EXAME - COMUM", "MC", "..."],
  "pausa_minutos": 15,
  "intervalo_busca_segundos": 10,
  "alerta_tela": false,
  "alerta_voz": true
}
```

| Campo | O que faz |
| :--- | :--- |
| `termos_gatilho` | Textos usados no **XPath de busca do botão** da senha (`//button[contains(...)]`). Escreva como aparece na tela, **com acento**: `DILATAÇÃO` não acha `DILATACAO`. Basta um dos termos casar para o botão ser localizado. |
| `comparacao` | `contem` (padrão) ou `exata` — como as listas casam com o texto da senha. |
| `whitelist` | Senhas que **DEVEM alertar**. Tem prioridade: vence a blacklist. |
| `blacklist` | Senhas que devem ser **IGNORADAS**. |
| `padrao_sem_match` | `ignorar` (padrão) ou `alertar` — o que fazer quando a senha não está em nenhuma das listas. |
| `pausa_minutos` | Quanto tempo o botão "CIENTE" silencia os alertas (padrão 15). |
| `intervalo_busca_segundos` | Espera entre uma verificação e outra na lista de espera (padrão 10). |
| `alerta_tela` | `true`/`false` — liga ou desliga a **janela vermelha piscando** na tela (padrão `true`). |
| `alerta_voz` | `true`/`false` — liga ou desliga a **fala sintetizada** (padrão `true`). |

**Configuração atual da clínica:**

| ALERTAR (whitelist) | IGNORAR (blacklist) |
| :--- | :--- |
| DILATAÇÃO - CCC | CONSULTA - COMUM |
| DILATAÇÃO - MMM | EXAME - COMUM |
| DILATAÇÃO - FENIL | PRIORITÁRIA |
| DILATAÇÃO - CMC | AGENDAMENTOS |
| DILATAÇÃO - CMM | CIRURGIA |
| DILATAÇÃO COM PILOCARPINA | RETORNO |
| AUTO REFRATOR (SEM DILATAR) | LENTES DE CONTATO |
| | R2 |
| | MC |
| | RECEPÇÃO 1° ANDAR |
| | RECEPÇÃO 2° ANDAR |
| | RECEPÇÃO SUS |
| | OUTRAS ESPECIALIDADES |

> A comparação ignora **acentos, maiúsculas/minúsculas, hífens e pontuação**: `DILATAÇÃO-CCC`, `dilatacao ccc` e `DILATAÇÃO - CCC` caem todas na mesma regra. `RECEPÇÃO 1º ANDAR` também casa com `RECEPÇÃO 1° ANDAR`. Chaves começando com `_` (ex.: `_comentario`) são ignoradas.

**Modos de alerta (as duas chaves do fim):**

| Quero... | `alerta_tela` | `alerta_voz` |
| :--- | :--- | :--- |
| Fala + janela piscando (padrão) | `true` | `true` |
| **Só voz, sem nada na tela** | `false` | `true` |
| Só a janela, sem som | `true` | `false` |
| Nenhum (só registra no log) | `false` | `false` |

`true`/`false`, `1`/`0`, `"sim"`/`"não"` e `"on"`/`"off"` são todos aceitos.

**Ordem de decisão do filtro:** `whitelist` (alerta) > `blacklist` (ignora) > `padrao_sem_match`. A whitelist existe justamente para o caso em que a senha contém uma palavra da blacklist mas deve alertar de qualquer forma — é o caso de `DILATAÇÃO - CMC`, que contém `MC`.

### Exemplos prontos (pasta `exemplos/`)

Quatro `filtros.json` prontos para copiar. Para usar: copie o arquivo desejado **sobre** o `filtros.json` da pasta do script (ou copie o conteúdo) e reinicie.

| Arquivo | O que alerta |
| :--- | :--- |
| `exemplos/filtros.dilatacao.json` | As senhas de dilatação (`DILATAÇÃO - CCC/MMM/FENIL/CMC/CMM`, `DILATAÇÃO COM PILOCARPINA`, `AUTO REFRATOR (SEM DILATAR)`) e ignora o resto. É idêntico ao `filtros.json` do repositório. |
| `exemplos/filtros.lentes-de-contato.json` | **Só** `LENTES DE CONTATO`. |
| `exemplos/filtros.mc.json` | **Só** `MC`. Usa `comparacao: "exata"` de propósito, para `DILATAÇÃO - CMC` não disparar junto. |
| `exemplos/filtros.r2.json` | **Só** `R2`. |

> Nos três exemplos de senha única a `blacklist` está vazia e `padrao_sem_match` é `ignorar`: qualquer senha diferente daquela é silenciosa.

### **credenciais.json**
Usuário e senha do Feegow. **Não é versionado** (está no `.gitignore`) — o script cria na primeira execução a partir de `credenciais.exemplo.json` e avisa no log até você preencher.

```json
{
  "usuario": "seu.usuario@clinica.com.br",
  "senha": "sua-senha",
  "rota_login": "auto",
  "url_login": "https://app.feegow.com/main/?P=Login",
  "url_login_sso": "https://app.feegow.com/main/sso/",
  "url_lista_espera": "https://app.feegow.com/pre-v8/?P=ListaEspera&Pers=1"
}
```

| Campo | O que faz |
| :--- | :--- |
| `usuario` | E-mail de acesso ao Feegow. |
| `senha` | Senha do Feegow. |
| `rota_login` | `auto` (padrão: tenta o SSO e depois a antiga), `sso` (só a nova) ou `legado` (só a antiga). |
| `url_login` | Página de login antiga (`?P=Login`). |
| `url_login_sso` | Página de login nova (`/main/sso/`). |
| `url_lista_espera` | Página da lista de espera monitorada. |

**As duas rotas de login:**

- **SSO (`/main/sso/`)** — duas etapas (e-mail → senha). Quando outra máquina está logada, o Feegow pede confirmação e **derruba a outra máquina**: o script confirma sozinho e avisa no log.
- **Legada (`?P=Login`)** — formulário único. Se aparecer "usuário já conectado em outra máquina", o script usa o bypass (repete o POST até assumir a sessão).
- Com `rota_login: "auto"`, se uma rota falhar ele tenta a outra automaticamente.
- E-mail/senha errados são detectados pelas mensagens "E-mail ou senha incorretos" (SSO) e "E-mail de acesso ou senha não conferem" (legada) — nesses casos o script **para** em vez de ficar tentando e travar a conta.

> ⚠️ Nunca faça commit do `credenciais.json` com a senha real. O repositório é público.

---

## Pré-requisitos

Antes de executar o script, você precisa ter instalado:

- [Python 3.x](https://www.python.org/downloads/)
- Google Chrome atualizado
- [ChromeDriver](https://sites.google.com/chromium.org/driver/) — o `webdriver-manager` baixa a versão certa automaticamente
- Conexão com a internet (o áudio da voz é gerado na hora pelo Google TTS)

Bibliotecas Python (todas no `requirements.txt`):

- `selenium` — automação do navegador
- `webdriver-manager` — baixa e gerencia o ChromeDriver
- `gTTS` — gera o áudio da voz sintetizada
- `pygame` — toca o áudio em todos os dispositivos de saída
- `pystray` — ícone na bandeja do Windows
- `Pillow` — desenha o ícone da bandeja
- `psutil` — rastreia o processo do Chrome pela janela

---

## Instalação

1. Clone este repositório:

   ```bash
   git clone https://github.com/Nicker2/script-monitoramento.git
   cd script-monitoramento
   ```

2. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

3. Rode o script uma vez para ele criar os arquivos de configuração:

   ```bash
   python SCRIPT_EXPLICA.py
   ```

   Na primeira execução ele cria `filtros.json` e `credenciais.json` na pasta do script.

---

## Uso

### 1. Preencher as credenciais

Abra o **`credenciais.json`** criado na pasta do script e troque os valores de exemplo:

```json
{
  "usuario": "seu.usuario@clinica.com.br",
  "senha": "sua-senha"
}
```

Enquanto os dados de exemplo estiverem lá, o log avisa:

```
⚠️ ATENÇÃO: credenciais.json está com os dados de EXEMPLO. Preencha usuário e senha.
```

### 2. (Opcional) Ajustar os filtros

Abra o **`filtros.json`** e ajuste conforme a sua realidade:

- Quer **ignorar** mais tipos de chamada? Acrescente palavras na `blacklist`.
- Quer **priorizar** um caso que cairia na blacklist? Coloque na `whitelist`.
- Quer que o "CIENTE" silencie por mais tempo? Aumente `pausa_minutos`.

Basta salvar o arquivo — o script recarrega sozinho.

### 3. Executar

- **Modo normal (console visível)** — melhor para testar:

  ```bash
  python SCRIPT_EXPLICA.py
  ```

  O script faz login, vai para a lista de espera e começa a monitorar. Ao detectar uma marcação cirúrgica válida, dispara o alarme.

- **Modo invisível** — para o dia a dia, sem janela de terminal:

  ```bash
  executar_iniciar_explica.BAT
  ```

  O Chrome roda fora da tela e o programa fica no ícone da bandeja (perto do relógio).

### 4. Bandeja do Windows

- **Clique no ícone** → mostra/oculta o painel e o navegador.
- **Botão direito → Encerrar Sistema** → fecha o Chrome, apaga o áudio temporário e sai.

### 5. Verificação de erros

No modo invisível, qualquer falha vai para `error_log.txt`. Se precisar ver a mensagem no console, use `DEBUG_EXECUTAR_EXPLICA.BAT`.

### 6. Reinício automático

Se o script cair, o modo invisível reinicia sozinho em 5 segundos — o monitoramento não fica parado.

---

## Solução de Problemas

| Sintoma | Causa provável / solução |
| :--- | :--- |
| O alarme não toca | Falta internet (o gTTS gera o áudio na hora). O log mostra "Falha ao gerar áudio de emergência". O alarme visual continua piscando. |
| Toca em um dispositivo só | Confira em "Testes de Hardware de Áudio" no painel se todos os dispositivos listados respondem ao "Testar". |
| Uma chamada real está sendo ignorada | Alguma palavra da `blacklist` está no texto do botão. Veja no log a linha `🔎 Botão '...' -> blacklist ('...')` e ajuste o `filtros.json`. |
| Uma chamada de outro tipo está disparando | Acrescente a palavra à `blacklist` (ou use `"comparacao": "exata"`). |
| O login não passa | Confira `usuario`/`senha` no `credenciais.json`. O log avisa se ainda está com os dados de exemplo. |
| "Usuário já conectado em outra máquina" | É esperado: o script cai no bypass invisível automaticamente e assume a sessão. |
| O programa não acha o Chrome | Feche as janelas do Chrome abertas pelo Selenium e reinicie (o rastreamento é por PID). |

---

## Histórico de Alterações

### Configuração externa em JSON (atual)

- **`filtros.json`**: blacklist, whitelist (com prioridade sobre a blacklist), termo gatilho, modo de comparação, tempo de pausa e intervalo de busca — tudo fora do código e com recarga automática ao salvar.
- **`credenciais.json`**: usuário, senha e URLs fora do código, ignorado pelo git (só o modelo `credenciais.exemplo.json` é versionado).
- Os dois arquivos são **criados automaticamente** se não existirem; JSON inválido não derruba o script.
- A comparação de filtros passou a **ignorar acentos**, então `PRIORITARIA` agora casa com `PRIORITÁRIA`.
- O login passou a montar o JavaScript com escape correto: **senha com apóstrofo ou aspas** não quebra mais o preenchimento do formulário.
- Adicionado `requirements.txt` e README atualizado.

### Filtros por lista (alertar × ignorar)

- **`whitelist` = ALERTAR** e **`blacklist` = IGNORAR**, com a whitelist vencendo sempre. Antes a lógica era "MC obrigatório + blacklist de falsos positivos", o que ignorava justamente as senhas de dilatação.
- **`padrao_sem_match`**: decide o que fazer com senha fora das duas listas (padrão `ignorar`, para nunca alarmar em cima de senha desconhecida).
- **`termos_gatilho`** (lista) alimenta o **XPath de busca do botão** (`//button[contains(translate(normalize-space(.), 'abc...', 'ABC...'), 'TERMO')]`, o método tradicional com `WebDriverWait`). Mantenha o termo escrito como aparece na tela — o XPath ignora maiúscula/minúscula, mas **não ignora acento**.
- Normalização passou a remover **hífens, barras, parênteses e símbolos de ordinal** (`°`/`º`), então `DILATAÇÃO-CCC`, `dilatacao ccc` e `RECEPÇÃO 1º ANDAR` casam com as listas.

### Alertas ligáveis/desligáveis + login SSO

- **`alerta_tela`** e **`alerta_voz`** no `filtros.json`: dá para usar só voz (sem janela), só janela (sem som) ou desligar os dois.
- Login passou a aceitar a **rota nova do Feegow (`https://app.feegow.com/main/sso/`)**: fluxo de duas etapas (e-mail → senha), confirmação automática da troca de sessão quando outra máquina está logada, e queda para a rota antiga se o SSO falhar (`rota_login` no `credenciais.json`).
- Senha/e-mail errados agora param o login com mensagem clara, em vez de ficar tentando em loop.

### Versão anterior

- Blacklist fixa dentro do `SCRIPT_EXPLICA.py` (`BLACKLIST_SENHAS`), sem whitelist.
- Usuário e senha (`exemplo@exemplo.com` / `exemplo`) escritos direto no código.

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

Este projeto é licenciado sob a Licença MIT - veja o arquivo [LICENSE](LICENSE) para detalhes.

---

## Autor

Criado por Nicolas Bonza Cavalari Borges.

---
