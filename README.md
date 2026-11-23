# Monitoramento de Senha no Feegow

Script em Python que usa Selenium para monitorar e clicar automaticamente no botão "Chamar senha" do sistema Feegow. Quando aparece uma senha de **dilatação** (ou `AUTO REFRATOR (SEM DILATAR)`) — e ela não está na lista de ignoradas — o script dispara um alarme sonoro com voz sintetizada e/ou uma janela piscante na tela.

As regras de filtro e as credenciais ficam em arquivos **JSON separados** (`filtros.json` e `credenciais.json`), então dá para mudar o comportamento sem tocar no código.

> ## ⬇️ Baixar pronto para usar (sem instalar Python)
>
> **[MonitorFeegow.exe — última versão](https://github.com/Nicker2/script-monitoramento/releases/latest/download/MonitorFeegow.exe)**
>
> Baixe, salve numa pasta própria (ex.: `C:\MonitorFeegow\`) e dê duplo clique. Na primeira execução ele cria `filtros.json` e `credenciais.json` ao lado do executável e pede o login.
>
> Requisitos: Windows 10/11 64 bits e Google Chrome instalado. O ChromeDriver é baixado sozinho conforme a versão do seu Chrome.
>
> Todas as versões: <https://github.com/Nicker2/script-monitoramento/releases>

## Como executar

| Situação | O que fazer |
| :--- | :--- |
| Dia a dia (com Python) | Duplo clique em **`iniciar_monitor.pyw`**. Não abre janela: o ícone aparece na bandeja. |
| Dia a dia (sem Python) | Duplo clique no **`MonitorFeegow.exe`**. |
| Ligar junto com o Windows | Duplo clique em **`instalar_inicio_automatico.bat`** (cria um atalho em `shell:startup`). Para desfazer: `remover_inicio_automatico.bat`. |
| Ver os erros na tela | `DEBUG_EXECUTAR_EXPLICA.BAT` (ou `python iniciar_monitor.pyw --console`). |

O `iniciar_monitor.pyw` é o **único** ponto de entrada: abre o monitor, relança se ele cair, aplica o reinício pedido pelo watchdog/atualização, grava `logs/supervisor.log` e não deixa abrir duas vezes. Os arquivos antigos (`executar_iniciar_explica.BAT`, `iniciar_explica.py`, `executar_invisivel_explica.py`) continuam aí só para não quebrar atalhos antigos — todos apontam para o `iniciar_monitor.pyw`.

---

## Índice

- [Como executar](#como-executar)
- [Descrição](#descrição)
- [Como Funciona](#como-funciona)
- [Arquivos e Descrições](#arquivos-e-descricoes)
- [Arquivos de Configuração](#arquivos-de-configuração)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Uso](#uso)
- [Watchdog (auto-recuperação)](#watchdog-auto-recuperação)
- [Atualização automática](#atualização-automática)
- [Áudio: só a saída padrão](#áudio-só-a-saída-padrão)
- [Compilando um .exe (Nuitka)](#compilando-um-exe-nuitka)
- [Solução de Problemas](#solução-de-problemas)
- [Limpeza de Disco](#limpeza-de-disco-pcs-antigos)
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

### **LIMPAR_LIXO.py**
**Descrição**:
Auditor e limpador do lixo que o Chrome/ChromeDriver deixam no PC. Roda só relatório por padrão (`--limpar` para apagar). Acha perfis temporários `scoped_dir*`, sobras do atualizador do Chrome no TEMP, extrações `_MEI*` e — o que mais enche o HD — **versões antigas do Chrome** em `Program Files\Google\Chrome\Application`. Nunca apaga pasta de processo vivo nem a versão do Chrome em uso.

---

### **iniciar_monitor.pyw** (ponto de entrada)
**Descrição**:
Supervisor sem janela. Abre o `SCRIPT_EXPLICA.py` (ou o `MonitorFeegow.exe`), relança na hora quando o monitor sai com o código 75 (reinício pedido pelo watchdog ou pela atualização), relança com espera crescente (5 s → 5 min) se ele cair e para quando o usuário escolhe **Encerrar Sistema**. Log em `logs/supervisor.log`.

---

### **instalar_inicio_automatico.bat** / **remover_inicio_automatico.bat**
**Descrição**:
Criam/removem o atalho "Monitor de Senha Feegow" na pasta Inicializar do usuário (sem precisar de administrador). O trabalho é feito por `ferramentas/inicio_automatico.ps1`, que também avisa se ainda existe um atalho antigo (para não abrir duas vezes). Se o `pythonw.exe` não estiver no PATH: `instalar_inicio_automatico.bat -Python "C:\caminho\pythonw.exe"`.

---

### **versao.json** e **ferramentas/gerar_versao.py**
**Descrição**:
`versao.json` diz qual é a versão publicada (com o sha256 do script, do `iniciar_monitor.pyw` e, opcionalmente, do `.exe`) e vem **assinado com Ed25519**. As máquinas consultam esse arquivo para se atualizar e recusam qualquer `versao.json` sem assinatura válida. O `gerar_versao.py` monta e assina o arquivo a partir do `VERSAO` do script (fonte única da versão; o `compilar.bat` lê do mesmo lugar via `ferramentas/versao_atual.py`) — ver [Assinatura das versões](#assinatura-das-versões-como-publicar).

---

### **logs/**
**Descrição**:
`monitor.log` (tudo o que aparece no painel, com data e hora; 5 arquivos de 1 MB em rotação; e-mails aparecem mascarados, ex. `fu***@clinica.com.br`), `supervisor.log`, `watchdog_estado.json`, `heartbeat.json` (o monitor grava a cada 15 s; o supervisor usa para saber se ele travou) e `status.json` (estado legível: etapa, último OK, relogins, RAM do Chrome, áudio...). Criar `logs/reiniciar.pedido` reinicia o monitor e `logs/encerrar.pedido` encerra (conferido a cada 15 s). Não vai para o git.

---

### **Legado: executar_iniciar_explica.BAT, iniciar_explica.py, executar_invisivel_explica.py**
**Descrição**:
Antes a cadeia era `executar_iniciar_explica.BAT` → `iniciar_explica.py` → `executar_invisivel_explica.py` (laço que relançava em 5 s) → `SCRIPT_EXPLICA.py`, com caminhos fixos do WinPython em `D:\`. Agora os três só chamam o `iniciar_monitor.pyw`, para os atalhos antigos continuarem funcionando. Podem ser apagados depois que as máquinas usarem o atalho novo.

---

### **DEBUG_EXECUTAR_EXPLICA.BAT**
**Descrição**:
Roda o `SCRIPT_EXPLICA.py` com console visível e `pause` no fim, para ver as mensagens e erros na tela.

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
- `pygame` — toca o áudio (por padrão só na saída padrão do Windows)
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

2. (Opcional) Instale as dependências — **o script faz isso sozinho** se faltar alguma:

   ```bash
   pip install -r requirements.txt
   ```

   Ao iniciar, ele confere `psutil`, `gTTS`, `pygame`, `pystray`, `Pillow`, `selenium` (e `webdriver-manager`, opcional) e roda `pip install` do que estiver faltando **antes** de abrir a interface. Se o pip não conseguir instalar, ele mostra o comando exato para você rodar à mão e não abre o programa pela metade.

3. Coloque o **chromedriver** na mesma pasta do `SCRIPT_EXPLICA.py`:

   - Abra <https://googlechromelabs.github.io/chrome-for-testing/>
   - Escolha a versão que bate com o seu Chrome (os três primeiros números, ex.: `157.0.8103.51`)
   - Baixe `chromedriver-win64.zip`, extraia e copie `chromedriver.exe` para a pasta do script

   **Isso é opcional:** o script confere a versão do Chrome instalado e, se não houver um driver compatível, baixa sozinho para `%LOCALAPPDATA%\MonitorFeegow\drivers`. A cópia manual só serve para PC sem internet ou com o download bloqueado.

4. Rode o script uma vez para ele criar os arquivos de configuração:

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

Duplo clique em **`iniciar_monitor.pyw`** (ou no `.exe`). Para testar com console: `python SCRIPT_EXPLICA.py`. Detalhes em [Como executar](#como-executar).

O Chrome roda fora da tela e o programa fica no ícone da bandeja (perto do relógio). A **cor do ícone** mostra o estado: 🟢 conectado, 🟠 recuperando, ⚪ sem internet, 🔴 falhas repetidas. O painel mostra a mesma informação no topo (`● Conectado | último OK há 8s | v2.1.1`).

### 4. Janela de configurações (⚙️ no painel)

No painel (ícone da bandeja → Mostrar), o botão **`⚙️ Configurações`** abre uma janela com três abas:

- **Senhas** — duas listas: `✅ ALERTAR (whitelist)` e `🚫 IGNORAR (blacklist)`. Digite a senha como aparece na tela do Feegow e clique em **Adicionar** (ou Enter); para remover, selecione e clique em **Excluir selecionado**. Se você adicionar uma senha de uma família nova, o `termos_gatilho` é atualizado sozinho para o XPath conseguir achá-la no modal.
- **⚡ Padrões prontos** (botões no topo da aba Senhas) — carrega, salva e já aplica o filtro escolhido:

  | Botão | O que faz |
  | :--- | :--- |
  | 🩺 **Dilatação** | Alerta as 7 senhas de dilatação e ignora as outras 13 (padrão da clínica). |
  | 👓 **Lentes** | Alerta **só** `LENTES DE CONTATO`. |
  | 🔔 **MC** | Alerta **só** `MC`, com comparação `exata` (não pega `DILATAÇÃO - CMC`). |
  | 🎫 **R2** | Alerta **só** `R2`. |

  O filtro anterior fica salvo em `filtros.backup.json` antes de cada troca.
- **Login** — e-mail/usuário, senha (com opção "mostrar senha") e a rota (`auto` / `sso` / `legado`). Salvar um login **diferente** reabre o navegador sozinho com ele. A senha é gravada protegida pelo Windows (DPAPI) — ver [Senha protegida](#senha-protegida-dpapi).
- **Alertas** — liga/desliga a janela piscando e a voz, `pausa_minutos`, `intervalo_busca_segundos`, a **saída da voz** (`padrao`/`todos`/um dispositivo) e o anti-repetição.
- **Sistema** — versão instalada, atualização automática (liga/desliga, intervalo, canal), limites do watchdog, reinício diário e limite de RAM do Chrome, limite de relogins, heartbeat remoto (URL, intervalo, nome da máquina e token) e os botões **Procurar atualização agora**, **Abrir pasta de logs** e **📋 Copiar diagnóstico** (copia versão, estado, configuração sem segredos e as últimas linhas do log, com e-mails mascarados, para colar no suporte).

O botão **💾 Salvar** grava direto em `filtros.json` e `credenciais.json` — **mesmo formato de antes** (`_comentario` e as chaves de URL são preservadas). Senhas e alertas passam a valer na hora.

**Primeira execução / senha errada:** se o `credenciais.json` não existir, o script cria o arquivo e abre uma caixa pedindo o login — já com a janela de configuração na aba **Login**. Se o Feegow recusar a senha, a mesma caixa aparece com o aviso de senha recusada. Assim que você salvar os dados certos, o monitoramento **começa sozinho**, sem precisar reiniciar.

### 5. Bandeja do Windows

- **Clique no ícone** → mostra/oculta o painel e o navegador.
- **Botão direito → Reiniciar monitor** → reinicia o programa (o supervisor relança na hora).
- **Botão direito → Encerrar Sistema** → fecha o Chrome e sai (o supervisor para junto).
- Passe o mouse no ícone para ver o estado; quando não há como falar aparece **"Sem áudio"**.

### 6. Logs

Tudo vai para `logs/monitor.log` com data e hora (cada reinício do watchdog aparece com o motivo, ex.: `[N1] Página com problema (branca...) — recarregando`). O supervisor grava em `logs/supervisor.log`.

---

## Watchdog (auto-recuperação)

Antes, o laço principal engolia **qualquer** erro e tentava de novo para sempre, sem log: se a sessão do Feegow caía, o Chrome travava ou a página ficava em branco, o monitor parecia ligado mas não avisava mais nada. Agora existe um **batimento** e uma recuperação em níveis.

**Batimento:** a cada ciclo em que a Lista de Espera respondeu (botão "Chamar senha" clicado e modal avaliado, ou a página confirmada viva por JavaScript) o horário é registrado. Uma thread separada (watchdog) olha esse horário a cada 15 s.

| Nível | Quando | O que faz |
| :--- | :--- | :--- |
| N1 | Uma falha no ciclo | Diagnostica a página: **login** → faz o login de novo; **branca / erro / outra página** → recarrega a Lista de Espera (até 4 vezes seguidas); **sem o botão mas viva** → recarrega no máximo a cada 5 min. |
| N2 | 5 falhas seguidas, Chrome morto, ou **180 s sem batimento** | Fecha o Chrome do monitor (destrava qualquer chamada pendurada) e abre de novo, com espera crescente: 5 s, 15 s, 30 s, 1, 2, 4 min… No máximo 6 reinícios por hora; passou disso, espera 15 min e avisa na bandeja. |
| N3 | Nem fechando o Chrome o monitor voltou em 5 min | Reinicia o programa inteiro. No máximo 4 vezes por hora (contador gravado em disco, vale entre reinícios). |

Também:

- **Sem internet:** o monitor espera a conexão voltar (ícone ⚪) e não reinicia nada à toa.
- **PC suspenso/hibernado:** ao voltar, o watchdog percebe o "pulo" do relógio, dá uma folga e recarrega a página.
- **Aba congelada pelo Chrome:** o Chrome é aberto com `--disable-background-timer-throttling`, `--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding` e sem detecção de oclusão/economia de memória — a janela fica fora da tela e, sem isso, o Chrome tratava a aba como em segundo plano (timers lentos, Page Lifecycle *freeze*, descarte da aba).
- **Prevenção:** a Lista de Espera é recarregada a cada 30 min (sem alarme tocando). Dá para reabrir o Chrome a cada N horas (`watchdog_reiniciar_chrome_horas`, desligado por padrão).
- **Tempo limite** em toda chamada do navegador (`page_load_timeout` 60 s, `script_timeout` 30 s): nada fica pendurado para sempre.
- **Instância única:** um segundo monitor aberto no mesmo PC sai sozinho (antes os dois brigavam pelo perfil do Chrome e a fala saía duplicada).

Configuração no `filtros.json` (ou na aba **Sistema**):

| Chave | Padrão | O que faz |
| :--- | :--- | :--- |
| `watchdog_limite_sem_batimento_segundos` | `180` | Tempo sem batimento para o watchdog reabrir o Chrome (mínimo 60). |
| `watchdog_recarregar_pagina_minutos` | `30` | Recarga preventiva da Lista de Espera (`0` = nunca). |
| `watchdog_reiniciar_chrome_horas` | `0` | Reabre o Chrome a cada N horas (`0` = nunca). |
| `watchdog_max_reinicios_por_hora` | `6` | Teto de reinícios do Chrome por hora. |

---

## Atualização automática

O programa consulta `https://raw.githubusercontent.com/Nicker2/script-monitoramento/<canal>/versao.json` a cada **60 min** (primeira consulta 90 s depois de abrir). Se a versão de lá for maior que a instalada:

1. Espera um **momento seguro**: sem alarme tocando, sem recuperação em andamento e com a página respondendo.
2. Baixa e **valida**: com o `.py`, confere o tamanho mínimo, a primeira linha (`# MONITOR-FEEGOW-SCRIPT`), se o `VERSAO` do arquivo é o mesmo do `versao.json`, o `sha256` e se o código compila. Com o `.exe`, exige `sha256_exe` e cabeçalho `MZ`.
3. Guarda a versão atual como `SCRIPT_EXPLICA.py.anterior` (ou `MonitorFeegow.exe.anterior`), coloca a nova no lugar e reinicia.
4. A versão nova fica **em teste** (`atualizacao_estado.json`) até o primeiro ciclo de monitoramento OK. Se ela reiniciar 3 vezes sem chegar lá, ou passar 20 min online sem conseguir monitorar, **volta sozinha para a anterior** e essa versão fica bloqueada naquela máquina.

Pastas que são um clone do git **não** são atualizadas (use `git pull`). Também dá para procurar na hora: bandeja → **Procurar atualização**, ou o botão na aba **Sistema**.

| Chave | Padrão | O que faz |
| :--- | :--- | :--- |
| `auto_atualizar` | `true` | Liga/desliga. |
| `atualizacao_intervalo_minutos` | `60` | Intervalo entre consultas (mínimo 10). |
| `atualizacao_canal` | `"main"` | Branch consultada. Para testar uma branch numa máquina só, coloque o nome dela aqui. |

Desde a 2.1.0 o cliente também exige: **assinatura Ed25519 válida** no `versao.json`, **sha256 obrigatório**, e só aceita os canais `main` e `beta` (outro nome no `filtros.json` cai na `main`, com aviso no log). O `iniciar_monitor.pyw` também é atualizado (conferido pelo sha256 do `versao.json` assinado) e passa a valer no próximo início do supervisor (ao entrar no Windows). Versão publicada sem `.exe` (`sha256_exe` vazio) simplesmente não mexe nos `.exe`, sem erro.

**Para publicar uma versão nova:** ver [Assinatura das versões](#assinatura-das-versões-como-publicar).

---

## Áudio: só a saída padrão

Antes, a fala tocava em **todos** os dispositivos de som, um depois do outro (alto-falante, monitor HDMI, fone…): a mesma frase saía 2–3 vezes seguidas. Agora:

- `"saida_audio": "padrao"` (padrão) — fala **uma vez**, na saída padrão do Windows. `"todos"` volta ao comportamento antigo; o nome exato de um dispositivo fala só nele.
- `"anti_repeticao_segundos": 60` — a mesma senha não é anunciada de novo dentro dessa janela (por exemplo, quando o alarme é desarmado pelo tempo máximo e a senha continua na tela). `0` desliga. As repetições do **mesmo** alarme (`intervalo_repeticao_segundos`) continuam normais.
- Uma fala por vez (trava interna) e **um monitor por PC** (instância única): duas cópias abertas não falam ao mesmo tempo.

---

## Opções novas da 2.1.0 (`filtros.json` / aba Sistema)

| Chave | Padrão | O que faz |
| :--- | :--- | :--- |
| `ciclo_leve` | `true` | Cada ciclo é **um** JavaScript que clica em "Chamar senha", espera o modal (MutationObserver, no máx. ~3 s) e devolve os textos — ~0,3 s por ciclo em vez de até 8 s de polling. Se falhar 3 vezes seguidas, volta sozinho para o método antigo até reabrir o Chrome. `false` usa sempre o antigo. |
| `chrome_reinicio_diario_hora` | `"03:00"` | Reabre o Chrome uma vez por dia nesse horário (só sem alarme e se o Chrome tiver mais de 1 h aberto). `""` desliga. |
| `chrome_limite_ram_mb` | `1500` | Mede a memória da árvore chromedriver → Chrome a cada 5 min (psutil) e reabre se passar. `0` desliga. |
| `chrome_bloquear_imagens` | `false` | Não carrega imagens (economiza memória). **Teste antes de ligar.** |
| `horario_funcionamento` | desligado | `{"ativo": true, "inicio": "06:00", "fim": "20:00", "dias": ["seg","ter","qua","qui","sex","sab"]}`. Fora do horário o Chrome fica fechado e o monitor só espera. Aceita virada da meia-noite (ex. `22:00` → `06:00`). |
| `max_relogins_por_hora` | `3` | Mais quedas de sessão que isso numa hora = para de relogar por 60 min (outro PC com o mesmo usuário?). |
| `hosts_verificacao_internet` | `["app.feegow.com", "www.google.com"]` | Hosts usados para saber se há internet (`host` ou `host:porta`). |
| `heartbeat_remoto_url` | `""` | Heartbeat para a VPS (desligado enquanto vazio; só `https://`). Ver [Heartbeat remoto](#heartbeat-remoto-opcional). |
| `heartbeat_remoto_intervalo_minutos` | `5` | Intervalo do heartbeat remoto. |
| `nome_maquina` | `""` | Nome enviado no heartbeat (vazio = nome do PC). |

O Chrome agora abre **com a sandbox ligada** (sem `--no-sandbox`; só se não abrir de jeito nenhum ele tenta sem, como último recurso) e com `--disable-extensions`, `--disable-background-networking`, `--disable-component-update`, `--disable-sync`, `--mute-audio` e cache em disco de 50 MB.

**Supervisor (`iniciar_monitor.pyw`):** além de relançar no código 75, confere o `logs/heartbeat.json` a cada 30 s. Se o heartbeat do monitor ficar parado por mais de 10 min (fora dos 5 min de inicialização e da volta da suspensão do PC), ele encerra o processo pelo PID (com os filhos) e relança. Versões antigas do monitor, que não gravam heartbeat, nunca são mortas por isso.

**Janela do alarme:** o texto vem da senha real e da pausa configurada (nada fixo), **Enter / Espaço / Esc = CIENTE**, a janela puxa o foco e o tamanho acompanha o DPI e cabe na tela.

## Senha protegida (DPAPI)

A senha do Feegow não fica mais em texto no `credenciais.json`: ela é gravada em `senha_protegida` (`dpapi:...`), cifrada pelo Windows para **o usuário atual daquele PC**. Máquinas que tinham `"senha"` em texto são migradas sozinhas no primeiro login depois da atualização (a migração espera a versão nova ser confirmada, para que um rollback automático ainda consiga entrar). Se copiar o `credenciais.json` para outro PC/usuário, a senha precisa ser digitada de novo. O token do heartbeat remoto também fica protegido assim.

## Assinatura das versões (como publicar)

O `versao.json` é assinado com uma chave **Ed25519**. A chave **pública** está embutida no `SCRIPT_EXPLICA.py` (`CHAVE_PUBLICA_RELEASE`); a chave **privada** fica **fora do repositório**, em `..\chaves\monitor_release_ed25519.pem` (pasta ao lado do clone; `*.pem` e `chaves/` estão no `.gitignore`). **Guarde uma cópia de segurança da chave privada**: sem ela não dá para publicar versões que as máquinas aceitem.

1. Suba o `VERSAO = "x.y.z"` no topo do `SCRIPT_EXPLICA.py` (é a única fonte da versão).
2. `python ferramentas/gerar_versao.py --notas "o que mudou"` — calcula os sha256 (em LF, como o GitHub serve), inclui o `iniciar_monitor.pyw` e assina. Precisa do pacote `cryptography` na máquina de quem publica (`pip install cryptography`). Outra chave: `--chave caminho.pem` ou variável `MONITOR_CHAVE_RELEASE`. Canal beta: `--canal beta`.
3. Commit do `SCRIPT_EXPLICA.py` + `versao.json` (+ `iniciar_monitor.pyw`) **juntos**.
4. `python ferramentas/gerar_versao.py --verificar --git-ref HEAD` — confere sha256, versão e assinatura contra o que foi commitado (com `core.autocrlf=true` o disco tem CRLF, mas o blob é LF). Depois, push.
5. Para quem usa o `.exe`: compile (`compilar.bat`, que lê a versão do script), anexe o `MonitorFeegow.exe` na release e rode o passo 2 com `--exe dist\MonitorFeegow.exe`.

As máquinas conferem a assinatura com o pacote `cryptography` se ele existir e, senão (ex.: `.exe`), com uma verificação Ed25519 em Python puro embutida no script — nunca ficam sem verificar.

## Heartbeat remoto (opcional)

Desligado por padrão. Com `heartbeat_remoto_url` preenchida (só `https://`), cada monitor faz `POST` JSON a cada `heartbeat_remoto_intervalo_minutos` com: `maquina`, `versao`, `status`, `texto`, `etapa`, `ultimo_ok_ha_s`, `rodando_ha_s`, `alarme_ativo`, `sem_audio`, `relogins_ultima_hora`, `relogin_bloqueado`, `reinicios_chrome_ultima_hora`, `chrome_ram_mb` etc. (sem senha e sem e-mail). O token vai no cabeçalho `Authorization: Bearer <token>` e é salvo pela aba Sistema (protegido por DPAPI).

**O que falta no servidor (não incluído aqui):** um endpoint HTTPS que receba esse POST, confira o token, guarde o último heartbeat por `maquina` e uma tarefa periódica que avise no Telegram quando uma máquina ficar sem heartbeat por X minutos (ou chegar com `status` `falha`/`sem_audio`/`relogin_bloqueado`), com aviso de volta ao normal.

## Testes

- `python -m pytest tests` — unidade (filtros, horário, reinício diário, textos do alarme, assinatura com vetores do RFC 8032, DPAPI, heartbeat, reinício que não sai com código 0), conferência do `versao.json` e o supervisor de verdade com um monitor falso (código 75, queda, heartbeat parado).
- `MONITOR_TESTES_CHROME=1 python -m pytest tests -k harness -s` (ou `python tests/harness/teste_monitor.py`) — bateria completa com Chrome headless e um **Feegow simulado local** (127.0.0.1, nunca o Feegow real), incluindo supervisor + monitor reais reiniciando e voltando a monitorar.
- O GitHub Actions (`.github/workflows/ci.yml`, `windows-latest`) roda `py_compile`, `gerar_versao.py --verificar` e o `pytest` a cada push.

## Compilando um `.exe` (Nuitka)

O executável é gerado com [Nuitka](https://nuitka.net/), que compila o Python para código C nativo. Vantagem para PC de clínica: **não precisa instalar Python nem os pacotes** na máquina de destino — o `.exe` já leva tudo embutido.

### Jeito rápido (1 clique)

Dê **duplo clique em `compilar.bat`**. Ele confere o Nuitka, compila e abre a pasta `dist` no final.

### Jeito manual

```bat
python -m pip install --upgrade nuitka
python -m nuitka --onefile --assume-yes-for-downloads --mingw64 ^
  --enable-plugin=tk-inter --windows-disable-console ^
  --include-package=selenium ^
  --include-package=pygame --include-package=pystray ^
  --include-package=gtts --include-package-data=gtts ^
  --nofollow-import-to=pygame.tests ^
  --company-name="Monitor Feegow" --product-name="Monitor de Senha Feegow" ^
  --file-version=1.0.0.0 --product-version=1.0.0.0 ^
  --output-filename="MonitorFeegow.exe" --output-dir=dist ^
  SCRIPT_EXPLICA.py
```

Saída: **`dist\MonitorFeegow.exe`** (arquivo único, ~49 MB).

### O que cada opção faz

| Opção | Motivo |
| :--- | :--- |
| `--onefile` | Um único `.exe`, fácil de copiar para os outros PCs. |
| `--mingw64` + `--assume-yes-for-downloads` | Baixa o compilador C (MinGW) na primeira compilação — só precisa do Python, não do Visual Studio. |
| `--enable-plugin=tk-inter` | Inclui o Tcl/Tk que o Tkinter usa (sem isso a janela não abre no PC de destino). |
| `--windows-disable-console` | Igual ao `pythonw`: sem janela preta de console. |
| `--include-package=pygame/pystray/gtts` | Garante as DLLs e pacotes que o Nuitka não deduz sozinho. |
| `--include-package=selenium` | **Obrigatório**: o Selenium importa `selenium.webdriver.chrome.options` dinamicamente e o Nuitka não enxerga — sem isso o `.exe` abre, mas o WebDriver não inicia (`ModuleNotFoundError`). |
| `--include-package-data=gtts` | Leva os arquivos internos do `gTTS`. |
| `--nofollow-import-to=pygame.tests` | Pula o pacote de testes do pygame — sozinho ele mais que dobra o tempo de compilação. |
| `--company-name`, `--file-version` … | Aparecem em Propriedades do arquivo no Windows. |

### Onde ficam os JSONs no modo compilado

O `SCRIPT_EXPLICA.py` detecta que está compilado (`__compiled__`) e grava/ler **`filtros.json` e `credenciais.json` na mesma pasta do `.exe`** — não na pasta temporária de extração do `--onefile` (que é apagada ao fechar). Então:

```
📁 Pasta do monitor
   ├── MonitorFeegow.exe      ← o compilado
   ├── filtros.json           ← criado no primeiro uso
   └── credenciais.json       ← criado no primeiro uso
```

Na primeira execução ele cria os dois sozinhos e abre a caixa pedindo login/senha.

### Detalhes e problemas comuns

- **A primeira compilação demora** (5 a 20 min): baixa o MinGW e compila tudo. As seguintes levam ~1 min.
- **`print()` sem console**: com `--windows-disable-console` o `sys.stdout` chega como `None`, e o primeiro `print()` derrubava a thread de inicialização em silêncio (o `.exe` abria, criava os JSONs e não fazia mais nada). O script substitui `stdout`/`stderr` por um fluxo nulo no topo do arquivo — o mesmo vale para rodar com `pythonw`. Se você compilar com console habilitado, isso é inofensivo.
- **Primeira abertura do `.exe` é mais lenta** (2–5s): o `--onefile` extrai para o TEMP antes de rodar.
- **Antivírus/SmartScreen** pode avisar que o `.exe` é desconhecido (não é assinado). Isso é normal em binário compilado por você mesmo — libere a pasta.
- **`--standalone` em vez de `--onefile`**: gera uma pasta `dist\SCRIPT_EXPLICA.dist\` com vários arquivos. Abre mais rápido, mas precisa copiar a pasta inteira.
- **ChromeDriver**: continua igual — o `.exe` lê a versão do Chrome instalado e baixa o driver certo em `%LOCALAPPDATA%\MonitorFeegow\drivers`.
- **Não versione o `dist\`** (está no `.gitignore`): binário de dezenas de MB não pertence ao repositório.

---

## Solução de Problemas

| Sintoma | Causa provável / solução |
| :--- | :--- |
| Para de avisar no meio do dia | Corrigido pelo [watchdog](#watchdog-auto-recuperação). Veja em `logs/monitor.log` as linhas `[N1]`/`[N2]`/`[N3]` com o motivo de cada recuperação. |
| A voz sai duas ou três vezes | Era a fala tocando em cada dispositivo. Agora `saida_audio` = `padrao`. Se ainda duplicar, confira se não há dois monitores (o segundo agora fecha sozinho) ou se o Windows está com "Ouvir este dispositivo" ligado. |
| Não atualizou sozinho | Veja `logs/monitor.log` (`Atualização:`). Pasta com `.git` não se atualiza; `.exe` só atualiza se o `versao.json` tiver `sha256_exe`. |
| Atualizou e voltou para a versão antiga | A versão nova não conseguiu monitorar e foi revertida (ver `atualizacao_estado.json`, campo `motivo`). |
| O alarme não toca | Desde a 2.1.0 as falas ficam em cache permanente (`%LOCALAPPDATA%\MonitorFeegow\audio`) e, sem internet, a voz do Windows (SAPI pt-BR) gera a fala; sem nada disso, toca bipe. O log mostra uma linha `🔈 Áudio: ...` por tipo de falha e a bandeja mostra "Sem áudio". |
| Parou de relogar com "outro PC usando o mesmo usuário?" | A sessão caiu mais de 3 vezes em uma hora (`max_relogins_por_hora`). O monitor para de relogar por 60 min para não ficar derrubando a outra máquina. Salvar o login na janela ⚙️ tenta de novo na hora. |
| "Não consegui abrir a senha protegida" | O `credenciais.json` veio de outro usuário/PC (a proteção DPAPI só abre no mesmo usuário do Windows). Digite a senha de novo na aba Login. |
| Toca em um dispositivo só | Confira em "Testes de Hardware de Áudio" no painel se todos os dispositivos listados respondem ao "Testar". |
| Uma chamada real está sendo ignorada | Alguma palavra da `blacklist` está no texto do botão. Veja no log a linha `🔎 Botão '...' -> blacklist ('...')` e ajuste o `filtros.json`. |
| Uma chamada de outro tipo está disparando | Acrescente a palavra à `blacklist` (ou use `"comparacao": "exata"`). |
| O login não passa | Confira `usuario`/`senha` no `credenciais.json`. O log avisa se ainda está com os dados de exemplo. |
| "Usuário já conectado em outra máquina" | É esperado quando o mesmo usuário está logado em outro PC. No SSO o script reenvia o login a cada **1,5 s** (até 40 tentativas, ~60 s) até assumir a sessão; na rota legada são 120 tentativas com espera de 1 s crescendo até 10 s. Se ficar repetindo, veja `max_relogins_por_hora` (há outro PC usando o mesmo usuário). |
| O programa não acha o Chrome | Feche as janelas do Chrome abertas pelo Selenium e reinicie (o rastreamento é por PID). |
| `Chrome failed to start: crashed (DevToolsActivePort file doesn't exist)` | Um Chrome órfão de execução anterior estava travando o perfil fixo. O script agora encerra esses processos no arranque (`🧹 Encerrei N processo(s) de Chrome antigos`) e, se ainda falhar, abre com perfil temporário. |
| Clique no ícone da bandeja não responde / pisca | Corrigido: o clique agora é agendado para a thread do Tk (antes mexia na janela direto da thread do pystray, o que travava), o estado vem do `root.state()` em vez de um flag, há trava de 400 ms contra clique duplo e o menu ganhou **Mostrar Painel** / **Ocultar Painel** separados. |
| Pastas `scoped_dir...` enchendo o TEMP | São perfis temporários do ChromeDriver (60–90 MB cada). O script agora usa um **perfil fixo** em `%LOCALAPPDATA%\MonitorFeegow\chrome_profile` e limpa os órfãos no arranque. Se quiser apagar na mão, feche o monitoramento antes. |
| Pastas `chromecrx_chrome_url_fetcher_...` / `chrome_chrome_BITS_...` | Não são do script: vêm do **instalador/atualizador do próprio Chrome** (BITS + url_fetcher). Somam poucos KB e o Windows limpa sozinho. |
| Alarme não para depois que a senha sai | Corrigido: o script agora só considera "senha" um botão cujo texto case com as listas de `filtros.json` (antes qualquer botão da página que contivesse um termo de busca — ex. um filtro "RECEPÇÃO" — era confundido com a senha e o alarme nunca desarmava). Se ainda acontecer, veja no log se aparece `🔇 A senha saiu da tela`. |
| Uma senha que existe na fila não é identificada | O modal do Feegow mostra **vários tickets ao mesmo tempo**. O script agora avalia **todos** (antes pegava só o primeiro: se fosse `AGENDAMENTOS`, a `DILATAÇÃO - CCC` logo abaixo nunca era lida). No log deve aparecer uma linha `🔎 Senha '...' -> ...` para cada ticket do modal. |
| `Could not reach host` / `Connection reset` ao iniciar | O `webdriver_manager` não conseguiu baixar o chromedriver (rede/firewall/proxy). Baixe manualmente em <https://googlechromelabs.github.io/chrome-for-testing/> e salve como `chromedriver.exe` na pasta do script — aí ele não precisa de internet para isso. |
| `Unable to obtain driver for chrome` | Mesma causa acima: chromedriver ausente. A mensagem nova do script já mostra a pasta onde salvar o arquivo. |
| Ctrl+C no console mostrava traceback e deixava o Chrome aberto | Corrigido: `KeyboardInterrupt` é tratado, o Chrome é fechado e o áudio temporário apagado (`⌨️ Ctrl+C detectado — encerrando...`). |
| Chrome abrindo com **"Restaurar páginas?"** | O script apaga os arquivos de sessão do perfil (`Last Session`/`Current Session`/`Sessions`), marca `exit_type = Normal` no `Preferences` e passa `--hide-crash-restore-bubble` / `--disable-session-crashed-bubble`. |

---

## Limpeza de Disco (PCs antigos)

PCs que rodaram versões antigas do script costumam ficar com o disco cheio. O `LIMPAR_LIXO.py` mostra exatamente onde está o peso e limpa o que é seguro:

```bash
python LIMPAR_LIXO.py                          # só relatório
python LIMPAR_LIXO.py --limpar                 # apaga (pede confirmação)
python LIMPAR_LIXO.py --limpar --sim           # apaga sem perguntar
```

O que ele procura:

| Item | Onde | Tamanho típico |
| :--- | :--- | :--- |
| `scoped_dir<pid>_*` | `%TEMP%` | 60–90 MB cada (perfil temporário do ChromeDriver) |
| `chrome_*`, `chromecrx_*`, `chrome_chrome_BITS_*` | `%TEMP%` | KB (atualizador do Chrome) |
| `_MEI*` | `%TEMP%` | dezenas de MB (extração do PyInstaller) |
| Versões antigas do Chrome | `Program Files\Google\Chrome\Application\<versão>` | **~400 MB cada** — é o que costuma encher o HD |
| `scoped_dir*`, `chrome_debug.log` | pasta do script | KB |

Regras de segurança:

- Só mexe no TEMP em pastas com **mais de 1 hora** (`--idade` ajusta).
- Nunca apaga `scoped_dir` cujo **PID ainda está vivo**.
- Em `Program Files` mantém **a versão mais nova** e a que está **em uso**; apaga só as antigas — e precisa rodar **como Administrador** para conseguir.

> Para impedir que o lixo volte: a versão atual do script usa perfil de Chrome fixo (`%LOCALAPPDATA%\MonitorFeegow\chrome_profile`) e já limpa os órfãos do TEMP no arranque.

---

## Histórico de Alterações

### v2.1.1 — login SSO assume a sessão em segundos

- **O login pela rota nova (`/main/sso/`) voltou a passar quando outra máquina está logada.** O Feegow devolve o formulário com `input[name=disconnect]`, **apaga a senha** e espera você confirmar "Deslogar usuário e entrar". O código antigo clicava em Enviar com o campo vazio — o próprio `sso-login.js` do site ignora esse clique (`if (!password.value) return`) — e a espera ia até 90 s sem nada acontecer.
- **Reenvio rápido:** agora o login é reenviado por `fetch` a cada **1,5 s** (até 40 tentativas, ~60 s) até a resposta deixar de ser a tela de conflito. Antes era o handshake nativo do site: `check` de 5 em 5 s, teto de 60 s.
- **Senha reposta na tela de conflito** antes de qualquer clique, e o fallback (handshake do site) continua existindo se o JavaScript não puder rodar.
- **Senha errada aborta na hora** (`E-mail ou senha incorretos` / `não conferem`): não entra no laço de reenvio nem derruba a outra máquina por causa de um erro de digitação.
- A rota legada (`?P=Login`) mantém o comportamento anterior: 120 tentativas com espera de 1 s crescendo até 10 s.

### v2.1.0 — mais leve, mais seguro e mais fácil de diagnosticar

- **Ciclo leve:** clique + leitura do modal num único `execute_async_script` com MutationObserver (~0,3 s por ciclo); o método antigo continua como reserva.
- **Chrome:** sandbox ligada (sem `--no-sandbox`), flags para PC fraco, reinício diário (03:00) e limite de RAM; chromedriver guardado por PID e morto por PID como reserva; horário de funcionamento opcional.
- **Supervisor:** mata e relança o monitor congelado (heartbeat parado > 10 min); o próprio `iniciar_monitor.pyw` passa a ser atualizado (conferido pela assinatura).
- **Login:** limite de relogins por hora (avisa "outro PC usando o mesmo usuário?"), bypass com limite de tentativas e espera crescente, usuário/senha passados como argumentos do JavaScript, troca de login reabre o Chrome sozinha.
- **Segurança:** `versao.json` assinado (Ed25519) e sha256 obrigatório; canais só `main`/`beta`; senha e token protegidos por DPAPI (migração automática); e-mail mascarado no log.
- **Áudio:** cache permanente das falas, voz do Windows (SAPI pt-BR) sem internet, bipe como último recurso, gTTS com tempo limite e fora da thread principal, `pygame.mixer` em vez de `pygame.init`, falha registrada uma vez por tipo e "Sem áudio" na bandeja.
- **Alarme:** textos da janela e da voz vêm da senha real e da pausa configurada; Enter/Espaço/Esc = CIENTE; foco, DPI e tamanho ajustados à tela.
- **Robustez:** JSON gravado de forma atômica; estado do alarme com trava; a interface só é mexida pela thread do Tk (fila); log das senhas só quando o conjunto muda; versão sem `.exe` não gera erro.
- **Diagnóstico:** `logs/status.json`, botão **Copiar diagnóstico**, heartbeat remoto opcional (desligado).
- **Scripts:** `.bat`/`.ps1` sem caminho fixo `D:\...` (WinPython na pasta/acima, `py` launcher ou PATH); versão em fonte única (`compilar.bat` lê do script).
- **Testes:** `tests/` com pytest + CI no GitHub Actions.

### v2.0.1 — correção urgente do reinício

- **Corrigido:** o reinício do programa (watchdog N3, reinício depois de atualizar e rollback) **fechava o monitor de vez**. A limpeza antes de reiniciar terminava com `os._exit(0)`, então o processo saía com código 0 antes de relançar e o `iniciar_monitor.pyw` entendia "o usuário encerrou" e parava também. Agora a limpeza só limpa; o reinício sai com o código 75 (o supervisor relança) e só o **Encerrar Sistema** sai com 0.
- **Atenção (máquinas na 2.0.0):** a própria 2.0.0 ainda tem o defeito ao se atualizar: depois de instalar a versão nova ela fecha e **não volta sozinha**. Abra o monitor uma vez (duplo clique em `iniciar_monitor.pyw`, ou saia e entre no Windows). Da 2.0.1 em diante o reinício volta sozinho.
- Bandeja → **Reiniciar monitor**; e pedidos por arquivo: criar `logs/reiniciar.pedido` reinicia, `logs/encerrar.pedido` encerra (lidos a cada 15 s).
- `hosts_verificacao_internet` no `filtros.json` (padrão `["app.feegow.com", "www.google.com"]`, aceita `host:porta`).
- Testes: o teste de atualização não troca mais o `reiniciar_processo` por um mock (era isso que escondia o defeito) e há um teste de integração real (supervisor + monitor + Chrome contra um Feegow simulado local).

### v2.0.0 — watchdog, atualização automática e inicialização simples

- **Watchdog** com batimento e recuperação em 3 níveis (recarregar/relogar → reabrir o Chrome → reiniciar o programa), com backoff e limites por hora; trata sessão caída, tela branca, página travada, internet fora e PC suspenso.
- **Flags anti-congelamento** no Chrome (a aba fora da tela era tratada como segundo plano) e tempo limite em toda chamada do navegador.
- **Atualização automática** pelo `versao.json` do GitHub, com validação, momento seguro e rollback.
- **Fala só na saída padrão** (antes saía em todos os dispositivos) + anti-repetição da mesma senha + instância única.
- **Um ponto de entrada:** `iniciar_monitor.pyw` (supervisor) + instalador do início automático.
- **Log em arquivo** com rotação (`logs/monitor.log`); o painel não é mais atualizado direto pela thread do Selenium (Tk não é thread-safe) e guarda só as últimas 3000 linhas.
- **Indicador de estado** na bandeja (cor) e no painel; aba **Sistema** na janela de configurações.
- Corrigido: o botão **CIENTE** estourava `KeyError: 'termo_gatilho'`.


### Configuração externa em JSON (atual)

- **`filtros.json`**: blacklist, whitelist (com prioridade sobre a blacklist), termo gatilho, modo de comparação, tempo de pausa e intervalo de busca — tudo fora do código e com recarga automática ao salvar.
- **`credenciais.json`**: usuário, senha e URLs fora do código, ignorado pelo git (só o modelo `credenciais.exemplo.json` é versionado).
- Os dois arquivos são **criados automaticamente** se não existirem; JSON inválido não derruba o script.
- A comparação de filtros passou a **ignorar acentos**, então `PRIORITARIA` agora casa com `PRIORITÁRIA`.
- O login passou a montar o JavaScript com escape correto: **senha com apóstrofo ou aspas** não quebra mais o preenchimento do formulário.
- Adicionado `requirements.txt` e README atualizado.

### Versão compilada (.exe com Nuitka)

- Release **v1.0.0** publicada com o `MonitorFeegow.exe` anexado — link direto e fixo: `https://github.com/Nicker2/script-monitoramento/releases/latest/download/MonitorFeegow.exe`.
- `compilar.bat` gera `dist\MonitorFeegow.exe` com um duplo clique — sem precisar de Visual Studio (o MinGW é baixado automaticamente com `--mingw64`).
- `_pasta_do_programa()` detecta o modo compilado (`__compiled__` / `NUITKA_ONEFILE_BINARY`) e usa a pasta do **executável**, não a pasta temporária de extração do `--onefile` — antes os JSONs seriam criados no TEMP e apagados ao fechar.
- `garantir_dependencias()` sai na hora quando compilado (as dependências já estão embutidas).
- Passo a passo completo, tabela de cada flag e problemas comuns no README, seção **“Compilando um `.exe` (Nuitka)”**.

### Padrões prontos de filtro (botões)

- `PADROES_SENHA` no código guarda quatro presets: **Dilatação** (7 alertar / 13 ignorar), **Lentes**, **MC** (comparação `exata`) e **R2** (senha única em cada um dos três últimos).
- Botões **⚡ Padrões prontos** no topo da aba **Senhas** da janela de configuração: um clique substitui `whitelist`, `blacklist`, `comparacao` e `termos_gatilho`, grava no `filtros.json`, recarrega em memória e atualiza as duas listas na tela.
- Antes de aplicar, o filtro atual é salvo em `filtros.backup.json` — dá para voltar atrás sem perder a configuração.
- Os presets ficam **no código**, então funcionam mesmo sem a pasta `exemplos/` (que continua existindo só como documentação).

### ChromeDriver se atualiza sozinho

- O script lê a **versão do Chrome instalado** (registro `HKCU\Software\Google\Chrome\BLBeacon`, com a pasta `Application\<versão>` como reserva) e confere se o driver que está na máquina combina com ela. Se não combinar, **baixa o certo** sozinho.
- Drivers ficam em `%LOCALAPPDATA%\MonitorFeegow\drivers\chromedriver-<versão>.exe` — um por versão, não sobrescreve nada (evita o arquivo travado no Windows) e dispensa permissão de administrador.
- Downloads usados (o catálogo do `webdriver_manager` é bloqueado nessa rede): `storage.googleapis.com/chrome-for-testing-public/<versão>/win64/chromedriver-win64.zip` e `edgedl.me.gvt1.com/...`.
- **Regra de compatibilidade:** compara `major.minor.build` (ex.: `157.0.8103`). Patch diferente continua valendo; build novo (Chrome atualizou) força o download.
- O `--version` do driver é executado antes de aceitar o arquivo, então um download corrompido nunca entra em uso.
- Ordem de busca: `%LOCALAPPDATA%\MonitorFeegow\drivers` → pasta do script → PATH → caches do Selenium/`webdriver_manager`. Se nada combinar e o download falhar, usa o que existe e avisa no log.

### Sem o balão "Restaurar páginas?"

- Depois de uma execução morta na força, o Chrome abria o balão **"Restaurar páginas?"** por cima da lista de espera (e ele rouba cliques).
- Três medidas, no arranque **e** no encerramento:
  1. `_limpar_sessao_antiga()` apaga `Default\Current Session`, `Current Tabs`, `Last Session`, `Last Tabs` e a pasta `Sessions` — é deles que o Chrome tira a restauração.
  2. `_marcar_perfil_limpo()` escreve `profile.exit_type = "Normal"` e `exited_cleanly = true` em `Default\Preferences`.
  3. Flags: `--no-first-run`, `--no-default-browser-check`, `--hide-crash-restore-bubble`, `--disable-session-crashed-bubble`.
- Observação: o `exit_type` volta a "Crashed" quando o processo é morto na força — normal. Como os arquivos de sessão são apagados em todo arranque, o balão não volta.

### Encerramento limpo (Ctrl+C e menu da bandeja)

- `KeyboardInterrupt` dentro do `root.mainloop()` (Ctrl+C no console) estourava um traceback e **deixava o Chrome aberto**. Agora o `mainloop` está dentro de `try/except KeyboardInterrupt` e chama `encerrar_sistema()`.
- `encerrar_sistema()` concentra a saída limpa: para o ícone da bandeja, fecha o driver (com timeout de 5s numa thread, para não travar se o Selenium estiver no meio de uma chamada), encerra Chrome/chromedriver que ainda estejam segurando o nosso perfil, apaga o mp3 temporário e sai com código 0.
- O menu **Encerrar Sistema** usa exatamente a mesma função — antes cada caminho fazia a limpeza por conta própria.

### Todas as senhas do modal são avaliadas

- O modal do Feegow lista **uma linha por ticket** (`AGENDAMENTOS`, `DILATAÇÃO - CCC`, ...), cada uma com um `<button title="Chamar a senha">`. O script lia apenas o **primeiro** botão que casava com o XPath: com a fila `['AGENDAMENTOS', 'DILATAÇÃO - CCC']` ele registrava `AGENDAMENTOS -> IGNORAR` e nunca olhava a CCC da linha de baixo — a senha "existia mas não era identificada".
- Agora `_senhas_na_tela()` usa `find_elements` e devolve **todas** as senhas visíveis (filtradas por `_texto_parece_senha`, sem duplicar), e `verificar_botao_explicacao()` avalia uma por uma, disparando o alarme se **qualquer** for de ALERTAR.
- Validado contra o DOM real do modal (Selenium em `file://`): lê as duas senhas, `CCC` alerta, `AGENDAMENTOS` ignora, e os botões `Fechar`/refresh/anular não entram.

### Bandeja do Windows e perfil do Chrome estáveis

- **Clique no ícone bugando:** `on_tray_click` mexia em `root.withdraw()/deiconify()` direto da **thread do pystray** — Tkinter não é thread-safe e o app travava/dessincronizava. Agora tudo passa por `root.after(0, ...)`.
- Estado real da janela via `_janela_visivel()` (`root.state()`), não mais pelo flag `is_visible`; trava de 400 ms contra o clique duplo que o pystray às vezes dispara; menu com **Mostrar Painel** / **Ocultar Painel** explícitos.
- `_hwnd_chrome()` valida o handle com `IsWindow` e reencontra o Chrome se ele foi reaberto (antes usava um handle morto).
- **Perfil fixo travado:** se uma execução anterior morreu na força, um Chrome órfão segura `%LOCALAPPDATA%\MonitorFeegow\chrome_profile` e o próximo início falha com `Chrome failed to start: crashed (DevToolsActivePort file doesn't exist)`. `_encerrar_instancias_antigas()` mata só os processos cujo cmdline contém o nosso perfil, e existe fallback para perfil temporário.

### Alarme desarmando de verdade + novos ajustes

- **Bug corrigido:** o alarme continuava tocando depois que a senha saía da tela. Causa: `_localizar_botao_senha()` procurava qualquer `<button>` que **contivesse** um `termos_gatilho`; um botão da própria página (ex. filtro "RECEPÇÃO") casava, o script entendia que a senha continuava lá e nunca desarmava. A versão antiga não tinha o problema porque só procurava o texto `MC`.
- Agora existe `_texto_parece_senha()`: o texto do botão tem de **ser** uma senha das listas (igual ou começando por ela). Botão de página com termo no meio é descartado.
- Desarme extra: se o ticket na tela **mudar** em relação ao que armou o alarme, ou virar uma senha de IGNORAR, o alarme é desarmado na hora.
- `tocar_audio_em_dispositivo()` para o mp3 imediatamente quando desarma (antes terminava a frase inteira).
- Novas configs: `alarme_max_segundos` (padrão 120, `0` sem limite), `intervalo_repeticao_segundos` (15) e `volume_alarme` (100) — também na aba **Alertas** da janela de configuração.
- Correção no normalizador: `º`/`ª` viram `o`/`a` no NFKD, então `RECEPÇÃO 2º ANDAR` virava `RECEPCAO 2O ANDAR` e não casava com `RECEPÇÃO 2° ANDAR`.

### Perfil do Chrome fixo (fim das pastas no TEMP)

- Sem `--user-data-dir`, o ChromeDriver cria uma pasta `scoped_dir<pid>_*` no TEMP por sessão (60–90 MB com o cache do Feegow) e **só apaga se a sessão terminar normalmente**. Execução morta na força (taskkill, crash, desligar o PC) deixa o lixo para trás.
- Agora o script passa `--user-data-dir=%LOCALAPPDATA%\MonitorFeegow\chrome_profile`: pasta única e reaproveitada, com o bônus de manter os cookies da sessão.
- `_limpar_scoped_dirs_orfas()` roda no arranque e apaga `scoped_dir<pid>_*` do TEMP cujo PID dono já morreu (nunca toca em pasta de sessão viva).
- `--incognito` **não** resolve: testado, continua criando as pastas — e ainda forçaria login novo a cada início.

### Primeira execução / senha recusada (msgbox)

- `credenciais_incompletas()` detecta arquivo recém-criado, usuário/senha vazios ou ainda nos valores de exemplo. Nesse caso o `__main__` **não inicia o Selenium** e chama `solicitar_credenciais("primeira_vez")`.
- `fazer_login()` chama `solicitar_credenciais("recusadas")` quando o Feegow recusa e-mail/senha (não em conflito de sessão nem em erro de rede).
- `solicitar_credenciais()` agenda no `root.after(0, ...)` (é chamada de dentro da thread do Selenium), mostra a msgbox e abre `abrir_configuracoes(aba="login")` — a janela já vem na aba certa e não duplica (`_PEDIDO_CRED_PENDENTE` / `_JANELA_CONFIG`).
- Ao salvar na janela, se o Selenium não estiver rodando e as credenciais estiverem completas, `salvar()` **inicia o monitoramento sozinho** (guarda `_SELENIUM_RODANDO` evita dois laços).

### Janela de configurações (⚙️)

- Botão **⚙️ Configurações** no topo do painel abre um `Toplevel` com `ttk.Notebook`: aba **Senhas** (duas `Listbox` — alertar/ignorar — com adicionar/excluir), aba **Login** (usuário, senha, rota) e aba **Alertas** (tela/voz/pausa/intervalo).
- Salvar **relê o JSON, altera só as chaves que a tela gerencia e regrava** — `_comentario` e `url_login`/`url_login_sso`/`url_lista_espera` sobrevivem.
- Ao salvar, `_sincronizar_termos_filtro()` garante que toda senha das listas tenha cobertura em `termos_gatilho` (senão o XPath nunca acharia a senha nova); `carregar_filtros(forcar=True)` recarrega na hora e `_CRED_USUARIO_LOGADO = None` força reler as credenciais no próximo login.
- Teste sem abrir janela: `exec` do módulo com `__name__` diferente de `__main__` (não entra no `mainloop`), `abrir_configuracoes()`, localizar os widgets com `winfo_children()` recursivo e `button.invoke()`, com `messagebox.showinfo/showerror` stubados — e rodar numa **cópia temporária** da pasta para não tocar nos JSONs reais.

### Auto-instalação das dependências

- Antes de qualquer `import` de terceiros, o script roda `garantir_dependencias()`: confere `psutil`, `gTTS`, `pygame`, `pystray`, `Pillow`, `selenium` e `webdriver-manager` com `importlib.util.find_spec` e, se faltar algum, chama `pip install` (primeiro normal, depois `--user`) na própria versão do Python que está rodando.
- Se não conseguir instalar, imprime o comando manual e encerra — evita abrir interface pela metade com `ModuleNotFoundError`.
- `webdriver_manager` é opcional: se não instalar, o script segue (o chromedriver local é a via principal).

### chromedriver local (não depende de download)

- O script passou a procurar o **chromedriver.exe** na própria pasta (depois `drivers\`, PATH, cache do Selenium Manager e do `webdriver_manager`) antes de tentar baixar. Sem isso, uma rede que bloqueia o `googlechromelabs.github.io` derrubava o script no arranque com `ConnectionResetError` / `Could not reach host`.
- Se o WebDriver não iniciar, o erro agora aparece no log do painel com a pasta exata onde salvar o `chromedriver.exe` — antes a thread morria com traceback e o programa ficava "vivo" sem monitorar nada.
- `webdriver_manager` virou dependência opcional (só entra no fallback).

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
