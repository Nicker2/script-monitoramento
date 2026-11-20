"""Ponto de entrada ÚNICO do Monitor de Senha do Feegow.

Duplo clique (roda com pythonw.exe, sem janela de console). Ele:
  * abre o SCRIPT_EXPLICA.py (ou o MonitorFeegow.exe, se não houver o .py);
  * relança na hora quando o monitor pede reinício (watchdog / atualização: código 75);
  * relança com espera crescente se o monitor cair (5 s, 10 s, 20 s ... até 5 min);
  * para quando o usuário escolhe "Encerrar Sistema" (código 0);
  * grava tudo em logs/supervisor.log (com rotação) e não roda duas vezes.

A atualização pelo GitHub, o watchdog do navegador e o log do monitor ficam
dentro do próprio SCRIPT_EXPLICA.py. Uso com console (para depurar):
    python iniciar_monitor.pyw --console
"""
import ctypes
import datetime
import os
import subprocess
import sys
import time

PASTA = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(PASTA, "SCRIPT_EXPLICA.py")
EXE = os.path.join(PASTA, "MonitorFeegow.exe")
PASTA_LOGS = os.path.join(PASTA, "logs")
LOG = os.path.join(PASTA_LOGS, "supervisor.log")

CODIGO_REINICIAR = 75
CODIGO_OUTRA_INSTANCIA = 3
NOME_MUTEX = "Local\\MonitorFeegowSupervisor"
ESPERA_MAXIMA = 300
_MUTEX = None


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
            codigo = processo.wait()
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
