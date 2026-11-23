"""versao.json x SCRIPT_EXPLICA.py: sha256 (em LF), tamanho, versão, extras e assinatura."""
import json
import os
import re
import subprocess
import sys

from conftest import RAIZ


def test_gerar_versao_verificar():
    r = subprocess.run([sys.executable, os.path.join(RAIZ, "ferramentas", "gerar_versao.py"), "--verificar"],
                       capture_output=True, text=True, errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr


def test_versao_em_fonte_unica():
    with open(os.path.join(RAIZ, "SCRIPT_EXPLICA.py"), encoding="utf-8") as arquivo:
        versao = re.search(r'^VERSAO\s*=\s*"([^"]+)"', arquivo.read(), re.M).group(1)
    with open(os.path.join(RAIZ, "versao.json"), encoding="utf-8") as arquivo:
        assert json.load(arquivo)["versao"] == versao
    saida = subprocess.run([sys.executable, os.path.join(RAIZ, "ferramentas", "versao_atual.py"), "--nuitka"],
                           capture_output=True, text=True).stdout.strip()
    assert saida.startswith(versao) and saida.count(".") == 3
    with open(os.path.join(RAIZ, "compilar.bat"), encoding="utf-8") as arquivo:
        bat = arquivo.read()
    assert "versao_atual.py" in bat and "--file-version=%VERSAO_EXE%" in bat


def test_sem_caminho_fixo_d():
    for nome in ("DEBUG_EXECUTAR_EXPLICA.BAT", "executar_iniciar_explica.BAT",
                 os.path.join("ferramentas", "inicio_automatico.ps1")):
        with open(os.path.join(RAIZ, nome), encoding="utf-8") as arquivo:
            assert "D:\\" not in arquivo.read(), nome


def test_chave_privada_fora_do_repo():
    marca_pem = "PRIVATE" + " KEY-----"
    for pasta, _subs, arquivos in os.walk(RAIZ):
        partes = pasta.split(os.sep)
        if ".git" in partes or "dist" in partes:
            continue
        for arquivo in arquivos:
            assert not arquivo.endswith((".pem", ".key")), os.path.join(pasta, arquivo)
            if arquivo.endswith((".py", ".json", ".md", ".yml", ".bat", ".ps1", ".pyw")):
                with open(os.path.join(pasta, arquivo), encoding="utf-8", errors="ignore") as f:
                    assert marca_pem not in f.read(), os.path.join(pasta, arquivo)
