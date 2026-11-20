"""Gera/atualiza o versao.json que as máquinas consultam para se atualizar.

Fluxo para publicar uma versão nova:
  1. Suba o VERSAO = "x.y.z" no topo do SCRIPT_EXPLICA.py.
  2. python ferramentas/gerar_versao.py --notas "o que mudou"
     (com o .exe: python ferramentas/gerar_versao.py --exe dist/MonitorFeegow.exe)
  3. Commit + push do SCRIPT_EXPLICA.py e do versao.json JUNTOS na branch do canal
     (main). Para o .exe, anexe o MonitorFeegow.exe na release antes do push.

O sha256 do script é calculado com fim de linha LF, que é como o GitHub serve
o arquivo (raw.githubusercontent.com), mesmo que o Windows tenha CRLF no disco.
"""
import argparse
import hashlib
import json
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(RAIZ, "SCRIPT_EXPLICA.py")
SAIDA = os.path.join(RAIZ, "versao.json")
URL_EXE = "https://github.com/Nicker2/script-monitoramento/releases/latest/download/MonitorFeegow.exe"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notas", default=None)
    parser.add_argument("--exe", default="", help="MonitorFeegow.exe compilado (preenche sha256_exe)")
    args = parser.parse_args()

    with open(SCRIPT, "rb") as arquivo:
        bruto = arquivo.read().replace(b"\r\n", b"\n")
    versao = re.search(rb'^VERSAO\s*=\s*"([^"]+)"', bruto, re.M).group(1).decode()

    anterior = {}
    if os.path.isfile(SAIDA):
        with open(SAIDA, "r", encoding="utf-8") as arquivo:
            anterior = json.load(arquivo)

    dados = {
        "versao": versao,
        "arquivo": "SCRIPT_EXPLICA.py",
        "sha256": hashlib.sha256(bruto).hexdigest(),
        "tamanho": len(bruto),
        "url_exe": anterior.get("url_exe") or URL_EXE,
        "sha256_exe": anterior.get("sha256_exe", "") if anterior.get("versao") == versao else "",
        "notas": args.notas if args.notas is not None else anterior.get("notas", ""),
    }
    if args.exe:
        with open(args.exe, "rb") as arquivo:
            dados["sha256_exe"] = hashlib.sha256(arquivo.read()).hexdigest()

    with open(SAIDA, "w", encoding="utf-8", newline="\n") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")
    print(f"versao.json -> {versao} (sha256 {dados['sha256'][:12]}..., exe: "
          f"{'sim' if dados['sha256_exe'] else 'nao'})")


if __name__ == "__main__":
    main()
