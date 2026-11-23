"""Ponto de entrada ÚNICO do Monitor de Senha do Feegow.

Duplo clique (roda com pythonw.exe, sem janela de console). Ele:
  * abre o SCRIPT_EXPLICA.py (ou o MonitorFeegow.exe, se não houver o .py);
  * relança na hora quando o monitor pede reinício (watchdog / atualização: código 75);
  * relança com espera crescente se o monitor cair (5 s, 10 s, 20 s ... até 5 min);
  * para quando o usuário escolhe "Encerrar Sistema" (código 0);
  * mata e relança o monitor se ele congelar: o monitor grava logs/heartbeat.json
    a cada 15 s; parado há mais de 5 min (fora da inicialização) = travado;
  * reserva da escada de recuperação (2.2.2): heartbeat andando, mas o monitor
    "ativo" sem nenhum ciclo OK há mais de 30 min (e sem alarme tocando) = relança;
  * sem teto de relançamentos forçados por hora (2.3.3): só um intervalo mínimo de
    30 s entre um e o próximo, para não virar laço apertado;
  * grava tudo em logs/supervisor.log (com rotação) e não roda duas vezes.

A atualização pelo GitHub, o watchdog do navegador e o log do monitor ficam
dentro do próprio SCRIPT_EXPLICA.py. Uso com console (para depurar):
    python iniciar_monitor.pyw --console
"""
import ctypes
import datetime
import json
import os
import subprocess
import sys
import time

PASTA = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(PASTA, "SCRIPT_EXPLICA.py")
EXE = os.path.join(PASTA, "MonitorFeegow.exe")
PASTA_LOGS = os.path.join(PASTA, "logs")
LOG = os.path.join(PASTA_LOGS, "supervisor.log")

HEARTBEAT = os.path.join(PASTA_LOGS, "heartbeat.json")

CODIGO_REINICIAR = 75
CODIGO_OUTRA_INSTANCIA = 3
CODIGO_MORTO_POR_HEARTBEAT = -9
NOME_MUTEX = "Local\\MonitorFeegowSupervisor"
ESPERA_MAXIMA = 300
# (as variáveis MONITOR_SUP_* existem só para os testes automáticos encurtarem os tempos)
LIMITE_HEARTBEAT = int(os.environ.get("MONITOR_SUP_LIMITE_HEARTBEAT") or 300)   # s sem heartbeat = travado
LIMITE_SEM_OK = int(os.environ.get("MONITOR_SUP_LIMITE_SEM_OK") or 1800)   # s ativo sem ciclo OK = relança
INTERVALO_MINIMO_MORTES = int(os.environ.get("MONITOR_SUP_INTERVALO_MINIMO") or 30)   # s entre relançamentos forçados
FOLGA_INICIALIZACAO = int(os.environ.get("MONITOR_SUP_FOLGA") or 300)   # s após abrir/voltar da suspensão
INTERVALO_CONFERENCIA = int(os.environ.get("MONITOR_SUP_INTERVALO") or 30)   # s entre conferências
_MUTEX = None
_ULTIMA_MORTE = {"ts": 0.0}   # último relançamento forçado (heartbeat parado / sem ciclo OK)


def log(mensagem):
    linha = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} | {mensagem}\n"
    try:
        os.makedirs(PASTA_LOGS, exist_ok=True)
        if os.path.isfile(LOG) and os.path.getsize(LOG) > 1_000_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as arquivo:
            arquivo.write(linha)
    except Exception:
        pass
    try:
        if sys.stdout:
            sys.stdout.write(linha)
            sys.stdout.flush()
    except Exception:
        pass


def instancia_unica():
    """True se este é o único supervisor rodando no PC."""
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    global _MUTEX
    _MUTEX = kernel32.CreateMutexW(None, False, NOME_MUTEX)
    return bool(_MUTEX) and ctypes.get_last_error() != 183


def comando_monitor(com_console):
    if os.path.isfile(SCRIPT):
        interpretador = sys.executable
        pasta_py = os.path.dirname(interpretador)
        preferido = "python.exe" if com_console else "pythonw.exe"
        if os.path.isfile(os.path.join(pasta_py, preferido)):
            interpretador = os.path.join(pasta_py, preferido)
        return [interpretador, SCRIPT]
    if os.path.isfile(EXE):
        return [EXE]
    return None


def heartbeat_parado(pid, folga_ate, agora=None):
    """True quando o heartbeat DESTE processo está parado há mais de LIMITE_HEARTBEAT.

    Sem heartbeat deste PID (versão antiga do monitor, que não grava, ou ainda
    abrindo) nunca conta como travado."""
    agora = agora or time.time()
    if agora < folga_ate:
        return False
    try:
        with open(HEARTBEAT, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        if int(dados.get("pid") or 0) != int(pid):
            return False
        return agora - float(dados.get("ts") or 0) > LIMITE_HEARTBEAT
    except Exception:
        return False


def sem_ciclo_ok_demais(pid, folga_ate, agora=None):
    """Reserva do N3: heartbeat DESTE processo andando, monitor ativo (não está
    sem internet / fora do horário / aguardando login), sem alarme tocando e sem
    nenhum ciclo OK há mais de LIMITE_SEM_OK. Monitor antigo (sem esses campos) = False."""
    agora = agora or time.time()
    if agora < folga_ate:
        return False
    try:
        with open(HEARTBEAT, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        if int(dados.get("pid") or 0) != int(pid) or "ok_ref_ts" not in dados:
            return False
        if not dados.get("ativo") or dados.get("alarme"):
            return False
        return agora - float(dados.get("ok_ref_ts") or agora) > LIMITE_SEM_OK
    except Exception:
        return False


def pode_forcar_reinicio(agora=None):
    """Desde a 2.3.3 não há teto por hora (decisão do dono): relança sempre que a regra
    mandar. Só segura se o último relançamento forçado foi há menos de
    INTERVALO_MINIMO_MORTES segundos (proteção contra laço apertado)."""
    agora = agora or time.time()
    if 0 <= agora - _ULTIMA_MORTE["ts"] < INTERVALO_MINIMO_MORTES:
        return False
    _ULTIMA_MORTE["ts"] = agora
    return True


def matar_arvore(pid):
    """Mata o monitor travado e os filhos dele (chromedriver/Chrome) pelo PID."""
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       timeout=30, creationflags=0x08000000)
    except Exception as e:
        log(f"taskkill falhou: {e}")


def esperar_monitor(processo, inicio):
    """Espera o monitor sair, conferindo o heartbeat. Devolve o código de saída."""
    folga_ate = inicio + FOLGA_INICIALIZACAO
    ultima_volta = time.time()
    while True:
        try:
            return processo.wait(timeout=INTERVALO_CONFERENCIA)
        except subprocess.TimeoutExpired:
            pass
        agora = time.time()
        if agora - ultima_volta > INTERVALO_CONFERENCIA * 4:
            # o relógio pulou: PC suspenso/hibernado. Dá a folga de novo.
            folga_ate = agora + FOLGA_INICIALIZACAO
        ultima_volta = agora
        motivo = ""
        if heartbeat_parado(processo.pid, folga_ate, agora):
            motivo = (f"O monitor (PID {processo.pid}) está sem heartbeat há mais de {LIMITE_HEARTBEAT}s "
                      "— parece travado.")
        elif sem_ciclo_ok_demais(processo.pid, folga_ate, agora):
            motivo = (f"O monitor (PID {processo.pid}) está ativo mas sem nenhum ciclo OK há mais de "
                      f"{LIMITE_SEM_OK // 60} min — a recuperação interna não resolveu.")
        if motivo and pode_forcar_reinicio(agora):
            log(motivo + " Encerrando o processo e relançando.")
            matar_arvore(processo.pid)
            try:
                processo.wait(timeout=30)
            except Exception:
                pass
            return CODIGO_MORTO_POR_HEARTBEAT


def main():
    com_console = "--console" in sys.argv
    if not instancia_unica():
        log("Outro iniciar_monitor.pyw já está rodando — saindo.")
        return 0

    ambiente = dict(os.environ, MONITOR_SUPERVISOR="1")
    quedas_rapidas = 0
    while True:
        comando = comando_monitor(com_console)
        if not comando:
            log(f"Nem SCRIPT_EXPLICA.py nem MonitorFeegow.exe encontrados em {PASTA}.")
            return 1
        inicio = time.time()
        log(f"Iniciando: {' '.join(os.path.basename(c) for c in comando)}")
        try:
            flags = 0 if com_console else 0x08000000  # CREATE_NO_WINDOW
            processo = subprocess.Popen(comando, cwd=PASTA, env=ambiente, creationflags=flags)
            codigo = esperar_monitor(processo, inicio)
        except KeyboardInterrupt:
            log("Ctrl+C — encerrando o supervisor.")
            return 0
        except Exception as e:
            log(f"Falha ao iniciar o monitor: {e}")
            codigo = -1
        duracao = time.time() - inicio

        if codigo == 0:
            log("Monitor encerrado pelo usuário. Supervisor saindo.")
            return 0
        if codigo == CODIGO_OUTRA_INSTANCIA:
            log("Já existe outro monitor rodando neste PC (aberto fora do supervisor). Saindo.")
            return 0
        if codigo == CODIGO_REINICIAR:
            log("O monitor pediu reinício (watchdog/atualização). Relançando.")
            quedas_rapidas = 0
            time.sleep(1)
            continue

        quedas_rapidas = quedas_rapidas + 1 if duracao < 120 else 0
        espera = min(5 * (2 ** max(0, quedas_rapidas - 1)), ESPERA_MAXIMA)
        log(f"O monitor caiu (código {codigo}, rodou {int(duracao)}s). Relançando em {espera}s.")
        time.sleep(espera)


if __name__ == "__main__":
    sys.exit(main())
