# 🛠️ Detalhes técnicos e manutenção

> Voltar para o [README](../README.md). Aqui fica o que interessa a quem mantém o monitor: como ele se recupera sozinho, como se atualiza, como publicar uma versão, testes, compilação e limpeza de disco.

## Sumário

- [Modo requisição (2.3.0)](#modo-requisição-230)
- [Watchdog (auto-recuperação)](#watchdog-auto-recuperação)
- [Atualização automática](#atualização-automática)
- [Áudio: só a saída padrão](#áudio-só-a-saída-padrão)
- [Fala por filtro (2.2.0)](#fala-por-filtro-220)
- [Janela do Chrome: maximizada ou do tamanho da tela (2.2.1)](#janela-do-chrome-maximizada-ou-do-tamanho-da-tela-221)
- [Opções da 2.1.0](#opções-novas-da-210-filtrosjson--aba-sistema)
- [Senha protegida (DPAPI)](#senha-protegida-dpapi)
- [Assinatura das versões (como publicar)](#assinatura-das-versões-como-publicar)
- [Heartbeat remoto (opcional)](#heartbeat-remoto-opcional)
- [Testes](#testes)
- [Compilando um .exe (Nuitka)](#compilando-um-exe-nuitka)
- [Limpeza de disco (PCs antigos)](#limpeza-de-disco-pcs-antigos)
- [Arquivos do repositório](#arquivos-do-repositório)

---

## Modo requisição (2.3.0)

Padrão a partir da 2.3.0 (`"modo_busca": "http"`). O Selenium continua fazendo o login como antes; depois disso o monitor lê o token do login (`sessionStorage["tk"]`, um JWT que vale ~12 h), **fecha o Chrome** e passa a pedir a fila direto à API que o próprio modal *Chamar senha* usa:

```
GET https://core.feegow.com/totem-queue/admin/get-queue-by-filter?filter=
x-access-token: <token do login>
```

- **Uma requisição a cada `intervalo_requisicao_seg`** (padrão 10 s, mínimo 5), sempre uma depois da outra, nunca em paralelo (~250 ms cada). Sem página aberta, o PC economiza a RAM e a CPU do Chrome (~300 a 400 MB).
- **O alerta é o mesmo do modo Chrome:** cada ticket traz `NomeTipo` (o texto do botão "Tipo" do modal). Os `NomeTipo` passam pelos mesmos `termos_gatilho`, pela mesma checagem de "parece senha", pela regra whitelist > blacklist > `padrao_sem_match`, pela pausa do CIENTE, pela fala/voz de cada filtro, pela janela vermelha e pelo anti-repetição. O alarme fica ligado enquanto a senha estiver na fila e desarma sozinho quando ela sai, como antes. Cada **id novo** das listas aparece no log (`🆕 Senha nova na fila (id 123): DILATAÇÃO - CCC`); só o tipo da senha vai para o log, nunca dado de paciente.
- **Painel pela bandeja:** com o painel visível, o Chrome abre **maximizado na Lista de Espera** (regra da 2.2.1), só para você ver. A busca continua por requisição. Ao ocultar o painel, o Chrome fecha.
- O `status.json` (e o **Copiar diagnóstico**) ganhou `modo_busca`: configurado, em uso, reserva, ciclos OK, último tempo (ms), falhas seguidas e validade do token. O token em si nunca é gravado nem vai para o log.

### Recuperação no modo requisição

| Situação | O que o monitor faz |
|---|---|
| Sessão expirada: HTTP 401/403, `{"code":"SESSION_EXPIRED"}`, HTML ou tela de login no lugar do JSON, token ausente | **Reloga na hora** (abre o Chrome escondido, entra, lê o token novo, fecha) e repete a requisição, sem esperar. **Sempre**, sem limite por hora (desde a 2.3.1; o `max_relogins_por_hora` só vale no modo Chrome). A única proteção é a linha de baixo. |
| Relogin falhou **2 vezes seguidas** | **Reinicia o programa inteiro** pelo caminho da escada 2.2.2 (`reiniciar_processo`): no máximo 5 por hora (depois segura 10 min), nunca com a janela vermelha aberta ou a voz falando, nunca sem internet. |
| Rede/servidor: timeout, HTTP 5xx, 429 | Espera **10 → 20 → 40 → 60 s** (teto) e volta a 10 s no primeiro sucesso. Sem internet, espera a conexão voltar (não conta como falha). |
| **3 min sem nenhuma resposta boa** | Reinicia o programa inteiro (mesmo caminho e mesmos limites acima). O watchdog também confere isso (`recuperacao_min_sem_ok`). |
| Token a menos de **10 min** de vencer | Renova antes: reaproveita a sessão do Chrome se o token dela ainda valer, senão faz o login. Se não der, tenta de novo em 2 min e, se vencer, cai no relogin. |
| A API deixou de mandar a lista ou o `NomeTipo` por `http_ciclos_formato_invalido` (3) ciclos seguidos | **Volta sozinho para o modo Chrome** (clique no *Chamar senha* e leitura do modal), avisa no log e na bandeja e fica nele até o próximo reinício do programa (o reinício programado de 60 min tenta o modo requisição de novo). |

O batimento (`heartbeat.json`, supervisor) só anda nos ciclos OK, como na 2.2.2. As esperas de propósito (backoff) registram progresso, mas não "ciclo OK", então a escada continua subindo se o Feegow não responder. O watchdog não trata o Chrome fechado como falha no modo requisição.

### Chaves no `filtros.json`

| Chave | Padrão | O que faz |
|---|---|---|
| `modo_busca` | `"http"` | `"http"` = modo requisição; `"chrome"` = o método antigo (clique no *Chamar senha*). Vale na hora: trocar para `"chrome"` com o monitor rodando volta para o modo antigo no próximo ciclo. |
| `intervalo_requisicao_seg` | `10` | Segundos entre uma requisição e a próxima (5 a 300). No modo Chrome continua valendo o `intervalo_busca_segundos`. |
| `http_ciclos_formato_invalido` | `3` | Respostas fora do formato esperado, seguidas, antes de voltar para o modo Chrome. |

**Migração:** na primeira leitura pela 2.3.0, as três chaves são **acrescentadas** ao `filtros.json` de cada PC se não existirem. Nada que já está no arquivo é trocado; quem já escreveu `"modo_busca": "chrome"` continua no modo antigo. O log mostra `🌐 filtros.json: modo requisição adicionado`.

## Watchdog (auto-recuperação)

Antes, o laço principal engolia **qualquer** erro e tentava de novo para sempre, sem log: se a sessão do Feegow caía, o Chrome travava ou a página ficava em branco, o monitor parecia ligado mas não avisava mais nada. Agora existe um **batimento** e uma recuperação em níveis.

**Batimento:** a cada ciclo em que a Lista de Espera respondeu (botão "Chamar senha" clicado e modal avaliado, ou a página confirmada viva por JavaScript) o horário é registrado. Uma thread separada (watchdog) olha esse horário a cada 15 s.

| Nível | Quando | O que faz |
| :--- | :--- | :--- |
| N1 | Uma falha no ciclo | Diagnostica a página: **login** → faz o login de novo; **branca / erro / outra página** → recarrega a Lista de Espera (até 4 vezes seguidas); **sem o botão mas viva** → recarrega no máximo a cada 5 min. |
| N2 | 5 falhas seguidas, Chrome morto, **Chrome/chromedriver sumiu** (conferido a cada 15 s pelo PID), ou **180 s sem batimento** | Fecha o Chrome **do perfil do monitor** e o chromedriver que ele abriu (pelo PID; nunca os outros Chromes do PC), recria o driver e entra de novo, com espera crescente: 5 s, 15 s, 30 s… |
| N3 | **3 reaberturas do N2 seguidas sem ciclo OK em 10 min**, **3 min monitorando sem ciclo OK**, **10 min em qualquer etapa sem ciclo OK**, ou nem fechando o Chrome o monitor voltou em 5 min | Reinicia o programa inteiro (mesmo caminho do menu *Reiniciar monitor*). No máximo **5 vezes por hora** (contador gravado em disco, vale entre reinícios); passou disso segura **10 min** e tenta de novo, com aviso no status e na bandeja. Com alarme tocando ou voz falando, **adia até terminar**. Sem internet, fora do horário ou aguardando login, não reinicia. |
| Supervisor | Heartbeat parado há mais de **5 min**, ou monitor ativo **30 min sem ciclo OK** | Mata o processo pelo PID (com os filhos) e relança. Também no máximo 5 por hora, depois 10 min de espera. |

**Escada de recuperação (2.2.2).** Antes, com o Chrome morto o monitor ficava "batendo cabeça": reabria o Chrome com espera cada vez maior, mas o N3 só olhava o tempo *sem batimento*, e toda tentativa/espera contava como batimento — o programa nunca reiniciava. E o heartbeat que o supervisor confere é gravado pela thread do watchdog, que continuava viva. Agora o N3 olha o tempo **sem ciclo OK** (página respondeu certo) e o número de reaberturas seguidas, e os auxiliares do login e da leitura do modal param na hora quando o erro é de Chrome morto (`MaxRetryError`/`NewConnectionError` do urllib3, `ConnectionRefusedError`, `invalid session id`, `no such window`, `chrome not reachable`, `disconnected`, `read timed out`…). Log de cada passo: `🩺 [N2] Watchdog: o chromedriver (PID …) não existe mais`, `🪜 Reabertura 2/3 sem ciclo OK`, `🪜 [N3] Escada de recuperação: …`, `⏳ [N3] Reinício do programa adiado: … esperando o alarme terminar`, `🚧 [N3] … Próxima tentativa de reinício às HH:MM`.

| Opção (`filtros.json`) | Padrão | O que faz |
| :--- | :--- | :--- |
| `recuperacao_falhas_para_reiniciar` | `3` | Reaberturas do Chrome seguidas, sem ciclo OK, para reiniciar o programa. |
| `recuperacao_janela_falhas_min` | `10` | Janela em que essas reaberturas contam. |
| `recuperacao_min_sem_ok` | `3` | Monitorando sem nenhum ciclo OK há X min = reinicia. |
| `recuperacao_min_sem_ok_total` | `10` | Em qualquer etapa (abrir o Chrome, login…) sem ciclo OK há X min = reinicia. |
| `recuperacao_max_reinicios_por_hora` | `5` | Teto de reinícios do programa por hora (os programados não contam). |
| `recuperacao_espera_limite_min` | `10` | Passou do teto: espera isto e tenta de novo. |

Também:

- **Sem internet:** o monitor espera a conexão voltar (ícone ⚪) e não reinicia nada à toa.
- **PC suspenso/hibernado:** ao voltar, o watchdog percebe o "pulo" do relógio, dá uma folga e recarrega a página.
- **Aba congelada pelo Chrome:** o Chrome é aberto com `--disable-background-timer-throttling`, `--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding` e sem detecção de oclusão/economia de memória — a janela fica fora da tela e, sem isso, o Chrome tratava a aba como em segundo plano (timers lentos, Page Lifecycle *freeze*, descarte da aba).
- **Prevenção:** a Lista de Espera é recarregada a cada 30 min (sem alarme tocando). Dá para reabrir o Chrome a cada N horas (`watchdog_reiniciar_chrome_horas`, desligado por padrão).
- **Reinício programado (desde a 2.1.2):** o programa inteiro se reinicia a cada `reiniciar_a_cada_min` minutos (padrão `60`). O watchdog confere a cada 15 s e, na hora, usa o mesmo caminho do N3: fecha o Chrome e o chromedriver, limpa o perfil e relança (código 75 para o supervisor, ou uma instância nova sem supervisor). Com a janela vermelha aberta, a voz falando ou a pausa do CIENTE valendo, ele **adia** até terminar. A pausa do CIENTE é gravada em `logs/pausa.json` antes de qualquer reinício e retomada no processo novo. O log mostra `reinicio programado (60 min)`. Esse reinício não conta no limite de 5 por hora do N3.
- **Tempo limite** em toda chamada do navegador (`page_load_timeout` 60 s, `script_timeout` 30 s): nada fica pendurado para sempre.
- **Instância única:** um segundo monitor aberto no mesmo PC sai sozinho (antes os dois brigavam pelo perfil do Chrome e a fala saía duplicada).

Configuração no `filtros.json` (ou na aba **Sistema**):

| Chave | Padrão | O que faz |
| :--- | :--- | :--- |
| `watchdog_limite_sem_batimento_segundos` | `180` | Tempo sem batimento para o watchdog reabrir o Chrome (mínimo 60). |
| `watchdog_recarregar_pagina_minutos` | `30` | Recarga preventiva da Lista de Espera (`0` = nunca). |
| `watchdog_reiniciar_chrome_horas` | `0` | Reabre o Chrome a cada N horas (`0` = nunca). |
| `watchdog_max_reinicios_por_hora` | `6` | Teto de reinícios do Chrome por hora. |
| `reiniciar_a_cada_min` | `60` | Reinicia o programa inteiro (Chrome + processo) a cada N minutos, adiando se houver alerta ou pausa do CIENTE. `0` desliga; de 1 a 9 vira 10. |

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

## Fala por filtro (2.2.0)

`falas_filtros` no `filtros.json` (ou aba **⚙️ Configurações → Falas**) define o que a voz fala em cada filtro. Quando uma senha alerta, o texto dela é comparado com os `termos` de cada filtro, **na ordem do arquivo**; o primeiro que casar decide a fala. Senha que não cai em nenhum filtro fala como antes (*"Atenção! Senha ..."*).

```json
"falas_filtros": {
  "dilatacao": {"nome": "Dilatação", "termos": ["DILATAÇÃO", "DILATACAO", "AUTO REFRATOR"],
                "comparacao": "contem", "fala": "Atenção! Senha de dilatação", "voz": true},
  "lentes": {"nome": "Lentes", "termos": ["LENTES"], "comparacao": "contem", "fala": "Nova Senha", "voz": true},
  "mc":     {"nome": "Marcação MC", "termos": ["MC"], "comparacao": "exata", "fala": "", "voz": false},
  "r2":     {"nome": "R2", "termos": ["R2"], "comparacao": "contem", "fala": "", "voz": true}
}
```

| Campo | O que faz |
| :--- | :--- |
| `termos` | Textos comparados com a senha (sem acento, maiúsculas, como nas listas). |
| `comparacao` | `contem` ou `exata`. O MC usa `exata` para `DILATAÇÃO - CMC` não cair nele. |
| `fala` | Frase falada. Vazia = fala a senha da tela. |
| `voz` | `false` = não fala nesse filtro. A janela vermelha continua seguindo `alerta_tela`. **Com `alerta_tela` desligado e `voz` desligada, a chamada não gera alerta nenhum** (o log avisa). |
| `nome` | Nome mostrado na aba Falas e no log. |

`alerta_voz` continua sendo o liga/desliga geral. O anti-repetição, o intervalo entre repetições e o cache de áudio funcionam igual; as frases novas são pré-geradas no cache como as outras.

**Migração (PCs que já tinham o monitor):** a atualização automática troca só o programa (`SCRIPT_EXPLICA.py` / `.exe` e o `iniciar_monitor.pyw`); o `filtros.json` de cada PC é preservado. Na primeira leitura pela 2.2.0, se o arquivo não tiver `falas_filtros`, os padrões acima são gravados nele (gravação atômica; o resto do arquivo não muda). Se a chave já existir, só os campos que faltam nos filtros conhecidos (`dilatacao`, `lentes`, `mc`, `r2`) são completados: nada personalizado é trocado e um filtro removido não volta. Filtro novo sem `fala`/`voz` lê a senha e fala, como antes. Se a 2.2.0 voltar para a 2.1.x (rollback), a chave nova é simplesmente ignorada.

---

## Janela do Chrome: maximizada ou do tamanho da tela (2.2.1)

A regra é: **visível = maximizada; escondida = do tamanho da tela real**. Nunca fica uma janela pequena, nem mostrando nem escondida, então a Lista de Espera do Feegow renderiza igual nos dois casos e os seletores funcionam em 1080p e em 720p.

- **Escondida** (padrão, painel na bandeja): o Chrome abre fora da tela (`--window-position=-10000,-10000`, o "limbo" de sempre) com `--window-size` igual à resolução da tela principal, lida pelo `GetSystemMetrics` e dividida pela escala do Windows (o `--window-size` é em px CSS). Exemplos: 1920x1080 a 100% vira `1920,1080`; 1920x1080 a 125% vira `1536,864`; 1280x720 vira `1280,720`. **Mínimo de 1280x720**: acima do breakpoint `lg` (1200 px) do Bootstrap do Feegow o layout é sempre o de desktop. Se a tela não puder ser lida, usa o mínimo.
- **Mostrar** (bandeja → Mostrar, clique no ícone, painel visível ao abrir ou depois de um reinício do Chrome/programa): a janela sai do limbo para a tela principal e é **maximizada** (`ShowWindow(SW_SHOWMAXIMIZED)`; reserva: `driver.maximize_window()`). Antes ela aparecia em 100,100 com 1024x768.
- **Ocultar**: `ShowWindow(SW_HIDE)`, como antes. Se a janela foi restaurada e diminuída na mão, ela volta ao limbo com o tamanho da tela; maximizada, só é escondida (já tem o tamanho da tela).
- O relogin não recria a janela, então o tamanho continua o mesmo. Reinício do Chrome e do programa passam pela abertura, que aplica a regra de novo.
- O log de abertura mostra `🖥️ Janela do Chrome: escondida (1920x1080 px quando fora da tela).`

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

**Supervisor (`iniciar_monitor.pyw`):** além de relançar no código 75, confere o `logs/heartbeat.json` a cada 30 s. Se o heartbeat do monitor ficar parado por mais de 5 min (era 10 até a 2.2.1; fora dos 5 min de inicialização e da volta da suspensão do PC), ele encerra o processo pelo PID (com os filhos) e relança. Desde a 2.2.2 também relança quando o heartbeat anda mas o monitor está ativo há mais de 30 min sem nenhum ciclo OK (reserva da escada; nunca com alarme tocando), com no máximo 5 relançamentos forçados por hora (depois espera 10 min). Versões antigas do monitor, que não gravam heartbeat, nunca são mortas por isso.

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

- `python -m pytest tests` — unidade (filtros, fala por filtro e migração do `filtros.json`, horário, reinício diário, textos do alarme, assinatura com vetores do RFC 8032, DPAPI, heartbeat, reinício que não sai com código 0, escada de recuperação com driver falso), conferência do `versao.json` e o supervisor de verdade com um monitor falso (código 75, queda, heartbeat parado).
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


---

## Arquivos do repositório

| Arquivo | Para que serve |
| :--- | :--- |
| `SCRIPT_EXPLICA.py` | Programa principal: login no Feegow (Selenium), ciclo de "Chamar senha", filtros, alarme (Tkinter + voz), bandeja, painel de logs, watchdog e atualização. A versão fica no `VERSAO` do topo (fonte única). |
| `iniciar_monitor.pyw` | **Ponto de entrada** e supervisor sem janela: abre o monitor (`.py` ou `.exe`), relança no código 75 e quando ele cai (5 s → 5 min), mata e relança se o heartbeat parar, impede duas instâncias. Log em `logs/supervisor.log`. |
| `filtros.json` | Listas de senhas e tempos. Recarregado sozinho ao salvar. |
| `credenciais.exemplo.json` | Modelo do `credenciais.json` (que **não** é versionado). |
| `exemplos/` | Filtros prontos (dilatação, lentes de contato, MC, R2), só como referência — os mesmos presets existem dentro do programa. |
| `instalar_inicio_automatico.bat` / `remover_inicio_automatico.bat` | Criam/removem o atalho na pasta Inicializar (via `ferramentas/inicio_automatico.ps1`, sem administrador). Se o `pythonw.exe` não estiver no PATH: `instalar_inicio_automatico.bat -Python "C:\caminho\pythonw.exe"`. |
| `DEBUG_EXECUTAR_EXPLICA.BAT` | Roda com console visível e `pause` no fim, para ver os erros. |
| `LIMPAR_LIXO.py` | Auditor/limpador do lixo do Chrome/ChromeDriver (ver [Limpeza de disco](#limpeza-de-disco-pcs-antigos)). |
| `versao.json` | Versão publicada, com sha256 e assinatura Ed25519 (lido pela atualização automática). |
| `ferramentas/gerar_versao.py` | Monta, assina e verifica o `versao.json`. |
| `ferramentas/versao_atual.py` | Lê o `VERSAO` do script (usado pelo `compilar.bat`). |
| `compilar.bat` | Gera `dist\MonitorFeegow.exe` com Nuitka. |
| `tests/` | pytest + harness com Feegow simulado local. |
| `executar_iniciar_explica.BAT`, `iniciar_explica.py`, `executar_invisivel_explica.py` | **Legado**: só redirecionam para o `iniciar_monitor.pyw`, para atalhos antigos continuarem funcionando. |

### Pasta `logs/` (não vai para o git)

`monitor.log` (tudo o que aparece no painel, 5 × 1 MB em rotação, e-mails mascarados), `supervisor.log`, `watchdog_estado.json`, `heartbeat.json` (gravado a cada 15 s), `status.json` (etapa, último OK, relogins, RAM do Chrome, áudio, reinício programado…), `pausa.json` (pausa do CIENTE passada de um processo para o outro no reinício). Criar `logs/reiniciar.pedido` reinicia o monitor e `logs/encerrar.pedido` encerra (conferido a cada 15 s).
