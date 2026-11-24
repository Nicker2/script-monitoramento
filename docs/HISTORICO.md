# 📜 Histórico de alterações

> Voltar para o [README](../README.md).

### v2.3.4 — sessão guardada: reinício sem login

- **A sessão do modo requisição fica guardada** em `%LOCALAPPDATA%\MonitorFeegow\sessao.dat`, cifrada com a DPAPI do Windows (só o usuário atual daquele PC abre), com a validade (`exp` do JWT). A API só precisa do `x-access-token` (nenhum cookie), então só o token é guardado. Nunca vai para o log, para a pasta do programa nem para o repositório.
- **Ao abrir/reiniciar:** sessão guardada com mais de 10 min de validade é testada com **uma** requisição; vale = usa direto, **sem abrir o Chrome e sem login** (o reinício programado de 60 min passa a custar ~1 s e não derruba outra sessão do mesmo usuário no SSO). Não vale (401/403/`SESSION_EXPIRED`/HTML/formato inválido), perto de vencer ou arquivo corrompido = apaga e faz o login normal, que grava a sessão nova. Todo token novo (relogin, renovação, painel) regrava o arquivo.
- Escada de recuperação, cronômetro de login, intervalo mínimo de 30 s e adiamento durante alarme: sem mudança.
- Testes novos (`tests/test_sessao_guardada.py`): sessão válida reaproveitada sem Chrome, expirada/recusada → apaga e loga, arquivo corrompido → apaga e loga, regravação após relogin, erro de rede não apaga, nada do token no log, arquivo cifrado sem o token em texto.
- Publicada só como `.py` (sem `.exe`).

### v2.3.3 — cronômetro de login e reinício sem teto por hora

- **Login demorou mais de 2 min = reinicia o programa.** Um cronômetro começa em qualquer tentativa de login (login ao abrir, relogin do modo requisição por sessão expirada ou renovação do token, relogin do modo Chrome, SSO com sessão dupla, bypass e retentativas) e para no login OK: token obtido no modo requisição, página logada no modo Chrome, ou qualquer ciclo OK. Retentativas e reaberturas do Chrome contam no mesmo cronômetro. Quem confere é a thread do watchdog (a cada 15 s), de fora da thread que está logando, então vale também com o Chrome travado. Passou de `login_timeout_seg` (padrão `120`, chave nova no `filtros.json`), o log mostra `⏱️ Login demorou mais de 2 min: reiniciando o programa` e o programa reinicia pelo mesmo caminho do menu *Reiniciar monitor* (`reiniciar_processo`).
- **Exceções do cronômetro:** com o alarme ou a voz em andamento, espera terminar; com `credenciais.json` vazio/de exemplo ou senha recusada, não reinicia (só avisa, porque reiniciar não adianta); sem internet, não reinicia e o tempo offline não conta (o login nem começa sem internet: `executar_sessao_chrome` espera a conexão antes de abrir o Chrome). Fora do horário, parado e aguardando login zeram o cronômetro.
- **Sem teto de reinícios por hora** (decisão do dono), em toda a escada de recuperação e no supervisor: saíram o limite de 5 reinícios por hora e a espera de 10 min depois de estourar (`recuperacao_max_reinicios_por_hora` e `recuperacao_espera_limite_min` deixaram de existir; se estiverem no `filtros.json`, são ignoradas). O programa reinicia sempre que a regra mandar. Ficou só um **intervalo mínimo de 30 s** entre um reinício e o próximo (gravado em disco, vale entre processos), para um defeito permanente não virar um laço apertado. O supervisor (`iniciar_monitor.pyw`) segue a mesma regra nos relançamentos forçados. O teto de reaberturas do Chrome (N2, `watchdog_max_reinicios_por_hora`) continua, porque não é reinício do programa e não segura o N3.
- Testes novos (`tests/test_cronometro_login.py`): login travado por mais de 120 s reinicia (conferido de outra thread), login que termina em 100 s não reinicia, credenciais de exemplo não reiniciam, alarme e voz adiam, sem internet não reinicia, modo requisição só para o cronômetro com o token, sem teto por hora e com intervalo mínimo de 30 s (monitor e supervisor).
- Publicada só como `.py` (sem `.exe`).

### v2.3.2 — log das requisições e Chrome fechado depois do login

- **Chrome fechado logo depois do login e da leitura do token**, mesmo com o painel visível (`driver.quit` + restos desse perfil e desse chromedriver, pelo PID), com a linha `🌐 Chrome fechado (modo requisição).` no log. Ele só abre de novo para relogar ou quando você clica em **Mostrar** na bandeja.
- **Log de cada requisição** (console e `logs\monitor.log`): `→ GET <url> (ciclo N)` e `← <status> em <ms> ms, <bytes> B, <qtd> senha(s): [<Sigla><Senha> <NomeTipo> <HH:MM> (id N)], ...`. Quando a resposta muda (ou em erro/formato inesperado), também vai o corpo em JSON compacto, truncado em ~2000 caracteres. O token nunca é logado; nome de paciente, CPF, telefone, e-mail etc. são mascarados (iniciais ou `***`).
- Chave nova `log_requisicoes` (padrão `true`) no `filtros.json`, acrescentada pela migração. A rotação do log continua a mesma (1 MB x 5).
- Testes novos: linhas de log por requisição, corpo só quando muda, 401 logado sem token, mascaramento, log desligado, Chrome fechado com o painel visível e o **Mostrar** da bandeja.
- Publicada só como `.py` (sem `.exe`).

### v2.3.1 — modo requisição reloga sempre

- **Sem limite de relogins por hora no modo requisição:** sessão expirada = reloga sempre na hora. Saiu o limite de 3 relogins por hora e a pausa de 60 min ("outro PC com o mesmo usuário?") que a 2.3.0 herdou do modo Chrome. A única proteção é a escada: 2 relogins seguidos sem token novo = reinicia o programa (até 5 por hora, nunca durante alarme ou voz). No modo Chrome o `max_relogins_por_hora` continua valendo.
- Teste novo: 5 sessões expiradas seguidas = 5 relogins, sem parar e sem reiniciar.
- Publicada só como `.py` (sem `.exe`): os PCs que usam o `.exe` continuam na 2.2.0.

### v2.3.0 — modo requisição: a fila vem direto da API do Feegow

- **Novo padrão `"modo_busca": "http"`.** O login continua pelo Chrome (Selenium); depois o monitor lê o token do login (`sessionStorage["tk"]`), **fecha o Chrome** e pede a fila a `core.feegow.com/totem-queue/admin/get-queue-by-filter` com o header `x-access-token`, uma requisição a cada `intervalo_requisicao_seg` (padrão 10 s), sempre uma de cada vez. Menos RAM e CPU, e nenhuma dependência da página, do modal ou do clique.
- **O alerta é idêntico ao do modo Chrome:** o `NomeTipo` de cada ticket passa pelos mesmos `termos_gatilho`, checagem de senha, whitelist > blacklist, pausa do CIENTE, fala/voz por filtro, janela vermelha e anti-repetição. Cada id novo das listas vai para o log (só o tipo da senha).
- **Recuperação:** sessão expirada (401/403/`SESSION_EXPIRED`/HTML/token ausente) = reloga na hora e repete; 2 relogins seguidos sem token novo = reinicia o programa (escada da 2.2.2: até 5 por hora, nunca durante alarme ou fala); timeout/5xx/429 = espera 10 → 20 → 40 → 60 s e volta a 10 no primeiro sucesso; 3 min sem resposta boa = reinicia o programa; token renovado quando faltam 10 min para vencer.
- **Reserva automática:** se a API deixar de mandar a lista ou o `NomeTipo` por 3 ciclos seguidos (`http_ciclos_formato_invalido`), o monitor volta sozinho para o modo Chrome (clique no *Chamar senha*), avisa no log e na bandeja e tenta o modo requisição de novo no próximo reinício. `"modo_busca": "chrome"` força o modo antigo.
- **Painel pela bandeja:** com o painel visível o Chrome abre maximizado na Lista de Espera, só para ver; ao ocultar, fecha.
- **Migração:** o `filtros.json` de cada PC só ganha as chaves novas (`modo_busca`, `intervalo_requisicao_seg`, `http_ciclos_formato_invalido`); nada existente é trocado.
- `status.json` ganhou `modo_busca`. O heartbeat e o supervisor funcionam igual (batimento só em ciclo OK).
- Testes novos em `tests/test_modo_requisicao.py`, com uma API falsa local: senha nova → alerta idêntico ao modo Chrome, blacklist, 401/403/HTML → relogin, 2 relogins falhos → reinício, 5xx/429 → espera progressiva, 3 min sem OK → reinício, formato quebrado → modo Chrome, migração.
- Publicada só como `.py` (sem `.exe`): os PCs que usam o `.exe` continuam na 2.2.0.

### v2.2.2 — escada de recuperação: Chrome sumiu, o monitor se recupera sozinho

- **Corrigido o "batendo cabeça" sem Chrome.** Quando o Chrome/chromedriver morria, o monitor reabria o Chrome (N2) com espera crescente (5 s, 15 s, 30 s… até 10 min, e 15 min depois de 6 por hora), mas **nunca subia para o reinício do programa**: o N3 só olhava o tempo *sem batimento*, e toda tentativa e toda espera registravam batimento (`registrar_batimento(ok=False)` / `_dormir(..., progresso=True)`). O supervisor também não agia, porque o heartbeat é gravado pela thread do watchdog, que continuava viva. Além disso, `_visivel`, `_senhas_na_tela`, `_confirmar_troca_sessao` e os laços do bypass do login engoliam o erro de conexão e ficavam até 20 s / 90 s / 5 min chamando a porta fechada do chromedriver (as linhas `Retrying ... Failed to establish a new connection` no console).
- **Escada nova:** (N2) o watchdog confere a cada 15 s se o chromedriver que o monitor abriu (pelo PID) ainda existe e ainda tem Chrome; se não, fecha os restos **desse perfil e desse chromedriver** (nunca os outros Chromes do PC), recria o driver e entra de novo. (N3) **3 reaberturas seguidas sem nenhum ciclo OK em 10 min**, ou **3 min monitorando sem ciclo OK**, ou **10 min em qualquer etapa (abrir/login) sem ciclo OK** = reinicia o programa inteiro pelo mesmo caminho do menu *Reiniciar monitor*. (Supervisor) heartbeat parado há mais de **5 min** (era 10) = mata e relança; heartbeat andando mas monitor ativo **30 min sem ciclo OK** = relança (reserva).
- **Proteção contra laço:** no máximo **5 reinícios por hora** (era 4); passou disso segura **10 min** e tenta de novo, com aviso no log, no status (vermelho) e na bandeja. O supervisor tem o mesmo limite para os relançamentos forçados.
- **Nunca no meio de um alarme:** com a janela vermelha aberta ou a voz falando, o reinício por falha é adiado até terminar (`[N3] Reinício do programa adiado`). Sem internet, etapa fora do horário ou aguardando login, não reinicia.
- Erros reconhecidos como "Chrome morto" ampliados: `MaxRetryError`/`NewConnectionError`/`ProtocolError` do urllib3 (inclusive dentro de outra exceção), `ConnectionRefusedError`/`ConnectionResetError`, `read timed out`, `WinError 10061/10054`, `not connected to DevTools`.
- Opções novas no `filtros.json`: `recuperacao_falhas_para_reiniciar` (3), `recuperacao_janela_falhas_min` (10), `recuperacao_min_sem_ok` (3), `recuperacao_min_sem_ok_total` (10), `recuperacao_max_reinicios_por_hora` (5), `recuperacao_espera_limite_min` (10). O `status.json` ganhou `reaberturas_sem_ok` e `reinicio_bloqueado_ate`; o `heartbeat.json` ganhou `ativo`, `ok_ref_ts` e `alarme`.
- Testes novos em `tests/test_recuperacao.py` (Chrome morto → recria, falha repetida → reinicia, limite de reinícios, nada durante alarme, supervisor relança).
- Publicada só como `.py` (sem `.exe`): os PCs que usam o `.exe` continuam na 2.2.0.

### v2.2.1 — Chrome maximizado quando visível

- **Janela do Chrome visível = maximizada** (ao mostrar pela bandeja, ao abrir com o painel visível e depois de reiniciar o Chrome ou o programa). Antes ela aparecia com 1024x768 fixo.
- **Escondida = do tamanho da tela real** (1920x1080, 1280x720 etc., já descontando a escala do Windows), com mínimo de 1280x720, para a página do Feegow renderizar igual à visível. O modo escondido continua o mesmo (fora da tela e fora da barra de tarefas).
- Testes novos em `tests/test_janela_chrome.py` (maximizar ao mostrar, tamanho = resolução quando escondido, mínimo, escala 125%).
- Detalhes em [AVANCADO.md](AVANCADO.md#janela-do-chrome-maximizada-ou-do-tamanho-da-tela-221).

### v2.2.0 — fala própria em cada filtro

- **Cada filtro tem a sua fala e o seu liga/desliga da voz** (`falas_filtros` no `filtros.json`). Padrões: **Dilatação** fala sempre *"Atenção! Senha de dilatação"* (qualquer senha de dilatação, inclusive `AUTO REFRATOR (SEM DILATAR)`, sem ler a senha); **Lentes** fala *"Nova Senha"*; **Marcação MC** fica **sem voz** (a janela vermelha continua seguindo `alerta_tela`); **R2** continua lendo a senha.
- **Aba nova ⚙️ Configurações → Falas:** para cada filtro, as senhas/termos, o campo **Fala**, a caixa **Falar em voz alta** e um botão **▶ Testar**. O **💾 Salvar** grava no `filtros.json` e vale na hora, sem reiniciar.
- **Migração automática nos PCs que já tinham o monitor:** na primeira leitura do `filtros.json` pela 2.2.0, se não existir `falas_filtros`, os padrões acima são gravados no arquivo (o resto fica como estava). Se já existir, só os campos que faltam são completados; fala, voz e termos personalizados não são trocados, e um filtro tirado de propósito não volta. O log mostra `🗣️ filtros.json: falas por filtro adicionadas`.
- A chave `alerta_voz` (aba Alertas) continua sendo o liga/desliga geral: desligada, nenhum filtro fala.
- Testes novos em `tests/test_falas.py` (fala por filtro, MC sem voz, `CMC` não cai no MC, migração e compatibilidade).

### v2.1.2 — reinício programado a cada 60 min

- **O programa inteiro se reinicia sozinho a cada 60 min** (`reiniciar_a_cada_min` no `filtros.json`; `0` desliga, valores de 1 a 9 viram 10). Usa o mesmo caminho do nível 3 do watchdog: fecha o Chrome e o chromedriver, deixa o perfil limpo e relança (código 75 para o supervisor, ou uma instância nova quando não há supervisor). Continua um processo só, com o ícone na bandeja.
- **Nunca no meio de um alerta:** com a janela vermelha aberta, a voz falando ou a pausa do CIENTE valendo, o reinício é adiado até terminar (o log mostra `reinicio programado (60 min) adiado: ...` uma vez por motivo).
- **A pausa do CIENTE sobrevive a qualquer reinício** (programado, watchdog, atualização): é gravada em `logs/pausa.json` e retomada no processo novo.
- Log: `reinicio programado (60 min)`. O `status.json` ganhou `reinicio_programado_min` e `reinicio_adiado`.
- Não conta no limite de 4 reinícios por hora do nível 3, porque é planejado.

### v2.1.1 — login SSO com outra máquina logada

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
