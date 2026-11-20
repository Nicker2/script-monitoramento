"""Imprime a versão do monitor lida do SCRIPT_EXPLICA.py (fonte ÚNICA da versão).

    python ferramentas/versao_atual.py           -> 2.1.0
    python ferramentas/versao_atual.py --nuitka  -> 2.1.0.0  (formato x.y.z.w do --file-version)

Usado pelo compilar.bat; o gerar_versao.py lê do mesmo lugar.
"""
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def versao():
    with open(os.path.join(RAIZ, "SCRIPT_EXPLICA.py"), "r", encoding="utf-8") as arquivo:
        achado = re.search(r'^VERSAO\s*=\s*"([^"]+)"', arquivo.read(), re.M)
    if not achado:
        raise SystemExit("VERSAO não encontrada no SCRIPT_EXPLICA.py")
    return achado.group(1)


if __name__ == "__main__":
    texto = versao()
    if "--nuitka" in sys.argv:
        partes = (re.findall(r"\d+", texto) + ["0", "0", "0", "0"])[:4]
        texto = ".".join(partes)
    print(texto)
