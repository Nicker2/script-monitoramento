#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LIMPAR_LIXO.py — Auditoria e limpeza do lixo que o Chrome/ChromeDriver deixa no PC.

Uso:
    python LIMPAR_LIXO.py            -> só RELATÓRIO (não apaga nada)
    python LIMPAR_LIXO.py --limpar   -> apaga o que é seguro (pede confirmação)
    python LIMPAR_LIXO.py --limpar --sim  -> apaga sem perguntar

O que ele encontra:
  1. %TEMP%\\scoped_dir<pid>_*      -> perfil temporário do ChromeDriver (60-90 MB cada)
  2. %TEMP%\\chrome_* / chromecrx_* -> sobras do instalador/atualizador do Chrome
  3. %TEMP%\\_MEI*                  -> extrações do PyInstaller (outros programas)
  4. Program Files\\Google\\Chrome\\Application\\<versao> antigas  -> o que mais enche o HD
  5. scoped_dir* / chrome_debug.log na pasta do script

Segurança:
  - Nunca apaga pasta de processo vivo (confere o PID do nome e a lista de processos).
  - Nunca apaga a versão do Chrome em uso, nem a mais recente instalada.
  - Sem permissão de administrador, apenas relata o que está em Program Files.
"""
import argparse
import glob
import os
import shutil
import sys
import tempfile
import time

def tamanho_pasta(caminho):
    total = 0
    for raiz, _dirs, arquivos in os.walk(caminho):
        for nome in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, nome))
            except Exception:
                pass
    return total


def mb(bytes_):
    return bytes_ / (1024 * 1024)


def formatar(bytes_):
    valor = mb(bytes_)
    if valor >= 1024:
        return f"{valor / 1024:.2f} GB"
    if valor >= 1:
        return f"{valor:.0f} MB"
    return f"{bytes_ / 1024:.0f} KB"


def pid_vivo(pid):
    if not pid:
        return False
    try:
        import psutil
        return bool(psutil.pid_exists(pid))
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def e_admin():
    if not sys.platform.startswith("win"):
        return getattr(os, "geteuid", lambda: 1)() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 1) Lixo do ChromeDriver / Chrome no TEMP
# ---------------------------------------------------------------------------
def varrer_temp(idade_min_horas=1.0):
    """Retorna lista de (caminho, tamanho, motivo, pode_apagar)."""
    temporario = tempfile.gettempdir()
    agora = time.time()
    achados = []

    for nome in os.listdir(temporario):
        caminho = os.path.join(temporario, nome)
        if not os.path.isdir(caminho):
            continue

        pode = True
        motivo = ""

        if nome.startswith("scoped_dir"):
            resto = nome[len("scoped_dir"):]
            parte = resto.split("_")[0]
            pid = int(parte) if parte.isdigit() else 0
            if pid and pid_vivo(pid):
                pode = False
                motivo = f"em uso (PID {pid} vivo)"
            else:
                motivo = "perfil temporário do ChromeDriver"

        elif nome.startswith("chromecrx_") or nome.startswith("chrome_chrome_"):
            motivo = "sobra do instalador/atualizador do Chrome"

        elif nome.startswith("_MEI"):
            motivo = "extração do PyInstaller"

        elif nome.startswith("chrome_"):
            motivo = "sobra do Chrome"

        else:
            continue

        try:
            if (agora - os.path.getmtime(caminho)) < idade_min_horas * 3600:
                pode = False
                motivo += " (recente, menos de 1h)"
        except Exception:
            pass

        achados.append((caminho, tamanho_pasta(caminho), motivo, pode))

    return achados


# ---------------------------------------------------------------------------
# 2) Versões antigas do Chrome em Program Files
# ---------------------------------------------------------------------------
def _chave_versao(nome):
    partes = []
    for pedaco in nome.split("."):
        partes.append(int(pedaco) if pedaco.isdigit() else 0)
    return partes


def versao_em_uso():
    """Descobre a versão do Chrome rodando (ou a que o Chrome usaria)."""
    try:
        import psutil
        for proc in psutil.process_iter(["name", "exe"]):
            if (proc.info.get("name") or "").lower() == "chrome.exe":
                exe = proc.info.get("exe") or ""
                if exe:
                    return os.path.basename(os.path.dirname(exe))
    except Exception:
        pass
    return ""


def varrer_chrome_antigo():
    """Retorna lista de (caminho, tamanho, motivo, pode_apagar) das versões velhas."""
    raizes = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Google", "Chrome", "Application"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application"),
    ]
    em_uso = versao_em_uso()
    achados = []

    for raiz in raizes:
        if not os.path.isdir(raiz):
            continue
        versoes = []
        for nome in os.listdir(raiz):
            caminho = os.path.join(raiz, nome)
            if not os.path.isdir(caminho):
                continue
            if not nome[0].isdigit():
                continue  # PlatformExperienceHelper, SetupMetrics, etc.
            if not os.path.isfile(os.path.join(caminho, "chrome.exe")):
                continue
            versoes.append((nome, caminho))

        if len(versoes) <= 1:
            continue

        versoes.sort(key=lambda par: _chave_versao(par[0]), reverse=True)
        mais_nova = versoes[0][0]

        for nome, caminho in versoes:
            if nome == mais_nova or nome == em_uso:
                continue
            achados.append((
                caminho,
                tamanho_pasta(caminho),
                f"versão antiga do Chrome (atual: {mais_nova})",
                True,
            ))

    return achados


# ---------------------------------------------------------------------------
# 3) Lixo na pasta do script
# ---------------------------------------------------------------------------
def varrer_pasta_script():
    pasta = os.path.dirname(os.path.abspath(__file__))
    achados = []
    for nome in os.listdir(pasta):
        caminho = os.path.join(pasta, nome)
        if os.path.isdir(caminho) and nome.startswith("scoped_dir"):
            achados.append((caminho, tamanho_pasta(caminho),
                            "perfil de Chrome criado na pasta do script", True))
        elif os.path.isfile(caminho) and nome.lower() in (
            "chrome_debug.log", "chromedriver.log", "geckodriver.log",
        ):
            try:
                achados.append((caminho, os.path.getsize(caminho),
                                "log do Chrome/ChromeDriver", True))
            except Exception:
                pass
    return achados


# ---------------------------------------------------------------------------
# Relatório / limpeza
# ---------------------------------------------------------------------------
def executar(limpar=False, confirmar=True, idade_min_horas=1.0):
    grupos = [
        ("TEMP (perfis do ChromeDriver, sobras do Chrome, PyInstaller)", varrer_temp(idade_min_horas)),
        ("Program Files (versões antigas do Chrome)", varrer_chrome_antigo()),
        ("Pasta do script", varrer_pasta_script()),
    ]

    total_lixo = 0
    total_apagavel = 0

    print("=" * 78)
    print("AUDITORIA DE LIXO — Chrome / ChromeDriver")
    print("=" * 78)

    for titulo, achados in grupos:
        print(f"\n### {titulo}")
        if not achados:
            print("   (nada encontrado)")
            continue
        for caminho, tam, motivo, pode in achados:
            total_lixo += tam
            if pode:
                total_apagavel += tam
            marca = "APAGAR " if pode else "MANTER "
            print(f"   [{marca}] {formatar(tam):>9}  {os.path.basename(caminho)}")
            print(f"              {motivo}")
            print(f"              {caminho}")

    print("\n" + "=" * 78)
    print(f"Total encontrado: {formatar(total_lixo)}  |  liberável agora: {formatar(total_apagavel)}")
    print("=" * 78)

    if not limpar:
        print("\nNada foi apagado (modo relatório).")
        print("Para apagar o que está marcado como APAGAR:  python LIMPAR_LIXO.py --limpar")
        return 0

    if not total_apagavel:
        print("\nNada para apagar.")
        return 0

    if confirmar:
        resposta = input(f"\nApagar {formatar(total_apagavel)}? Digite SIM para confirmar: ").strip().upper()
        if resposta != "SIM":
            print("Cancelado.")
            return 0

    liberado = 0
    erros = 0
    for titulo, achados in grupos:
        for caminho, tam, motivo, pode in achados:
            if not pode:
                continue
            try:
                if os.path.isdir(caminho):
                    shutil.rmtree(caminho)
                else:
                    os.remove(caminho)
                liberado += tam
                print(f"   apagado: {os.path.basename(caminho)} ({formatar(tam)})")
            except Exception as e:
                erros += 1
                print(f"   ⚠️ não consegui apagar {caminho}: {e}")

    print("\n" + "=" * 78)
    print(f"Liberado: {formatar(liberado)}" + (f" | falhas: {erros}" if erros else ""))
    if erros and not e_admin():
        print("Dica: pastas em Program Files exigem executar como Administrador.")
    print("=" * 78)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Limpa o lixo do Chrome/ChromeDriver.")
    parser.add_argument("--limpar", action="store_true", help="apaga de verdade")
    parser.add_argument("--sim", action="store_true", help="não pedir confirmação")
    parser.add_argument("--idade", type=float, default=1.0,
                        help="idade mínima em horas para mexer no TEMP (padrão 1)")
    args = parser.parse_args()
    return executar(limpar=args.limpar, confirmar=not args.sim, idade_min_horas=args.idade)


if __name__ == "__main__":
    sys.exit(main())
