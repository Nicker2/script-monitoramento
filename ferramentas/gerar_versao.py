"""Gera, ASSINA e confere o versao.json que as máquinas consultam para se atualizar.

Publicar uma versão nova:
  1. Suba o VERSAO = "x.y.z" no topo do SCRIPT_EXPLICA.py (fonte única da versão).
  2. python ferramentas/gerar_versao.py --notas "o que mudou"
     (com o .exe: ... --exe dist/MonitorFeegow.exe)
  3. Commit do SCRIPT_EXPLICA.py + versao.json (+ iniciar_monitor.pyw se mudou) JUNTOS e push.
  4. Depois do commit: python ferramentas/gerar_versao.py --verificar --git-ref HEAD
     (confere o sha256 contra o blob em LF que o GitHub serve e a assinatura).

Chave privada Ed25519: NUNCA vai para o repositório. Por padrão é lida de
  <pasta acima do repo>/chaves/monitor_release_ed25519.pem
ou do caminho em --chave / variável MONITOR_CHAVE_RELEASE. Assinar exige o
pacote `cryptography` (pip install cryptography); as máquinas clientes conferem
mesmo sem ele (verificação embutida em Python puro).

O sha256 é calculado com fim de linha LF, que é como o GitHub serve o arquivo
(raw.githubusercontent.com), mesmo que o Windows tenha CRLF no disco.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(RAIZ, "SCRIPT_EXPLICA.py")
SAIDA = os.path.join(RAIZ, "versao.json")
EXTRAS = ("iniciar_monitor.pyw",)
URL_EXE = "https://github.com/Nicker2/script-monitoramento/releases/latest/download/MonitorFeegow.exe"
CHAVE_PADRAO = os.path.join(os.path.dirname(RAIZ), "chaves", "monitor_release_ed25519.pem")
CANAIS = ("main", "beta")


def ler_lf(nome, git_ref=None):
    """Conteúdo do arquivo com LF (do disco ou de um commit do git)."""
    if git_ref:
        bruto = subprocess.run(["git", "-C", RAIZ, "show", f"{git_ref}:{nome}"],
                               capture_output=True, check=True).stdout
    else:
        with open(os.path.join(RAIZ, nome), "rb") as arquivo:
            bruto = arquivo.read()
    return bruto.replace(b"\r\n", b"\n")


def versao_do_script(bruto):
    achado = re.search(rb'^VERSAO\s*=\s*"([^"]+)"', bruto, re.M)
    if not achado:
        raise SystemExit("VERSAO não encontrada no SCRIPT_EXPLICA.py")
    return achado.group(1).decode()


def chave_publica_do_script(bruto):
    achado = re.search(rb'^CHAVE_PUBLICA_RELEASE\s*=\s*"([0-9a-f]{64})"', bruto, re.M)
    if not achado:
        raise SystemExit("CHAVE_PUBLICA_RELEASE não encontrada no SCRIPT_EXPLICA.py")
    return bytes.fromhex(achado.group(1).decode())


def mensagem_assinada(dados):
    """Igual a mensagem_assinada_versao() do SCRIPT_EXPLICA.py."""
    conteudo = {k: v for k, v in dados.items() if k != "assinatura"}
    return json.dumps(conteudo, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def verificar_assinatura(publica, dados):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        assinatura = base64.b64decode(dados.get("assinatura") or "", validate=True)
        Ed25519PublicKey.from_public_bytes(publica).verify(assinatura, mensagem_assinada(dados))
        return True
    except Exception:
        return False


def assinar(caminho_chave, dados):
    from cryptography.hazmat.primitives import serialization
    with open(caminho_chave, "rb") as arquivo:
        privada = serialization.load_pem_private_key(arquivo.read(), password=None)
    return base64.b64encode(privada.sign(mensagem_assinada(dados))).decode("ascii")


def verificar(git_ref=None):
    """Confere versao.json x script (sha/tamanho/versão), extras e assinatura. Devolve 1 se falhar."""
    problemas = []
    if git_ref:
        dados = json.loads(ler_lf("versao.json", git_ref).decode("utf-8"))
    else:
        with open(SAIDA, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    script = ler_lf("SCRIPT_EXPLICA.py", git_ref)
    if dados.get("versao") != versao_do_script(script):
        problemas.append(f"versao {dados.get('versao')} != VERSAO do script {versao_do_script(script)}")
    if dados.get("sha256") != hashlib.sha256(script).hexdigest():
        problemas.append("sha256 do script não confere")
    if dados.get("tamanho") != len(script):
        problemas.append("tamanho do script não confere")
    if dados.get("canal") not in CANAIS:
        problemas.append(f"canal inválido: {dados.get('canal')}")
    for nome, info in (dados.get("arquivos_extras") or {}).items():
        if hashlib.sha256(ler_lf(nome, git_ref)).hexdigest() != (info or {}).get("sha256"):
            problemas.append(f"sha256 de {nome} não confere")
    if not verificar_assinatura(chave_publica_do_script(script), dados):
        problemas.append("assinatura Ed25519 inválida para a chave pública embutida no script")
    origem = f"commit {git_ref}" if git_ref else "disco"
    if problemas:
        print(f"versao.json ({origem}) COM PROBLEMAS:\n  - " + "\n  - ".join(problemas))
        return 1
    print(f"versao.json ({origem}) OK: v{dados['versao']}, sha256 {dados['sha256'][:12]}..., "
          f"canal {dados['canal']}, assinatura válida, extras: {', '.join(dados.get('arquivos_extras') or {}) or '-'}")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--notas", default=None)
    parser.add_argument("--exe", default="", help="MonitorFeegow.exe compilado (preenche sha256_exe)")
    parser.add_argument("--canal", default="main", choices=CANAIS)
    parser.add_argument("--chave", default=os.environ.get("MONITOR_CHAVE_RELEASE") or CHAVE_PADRAO,
                        help="chave privada Ed25519 (PEM) — fica FORA do repositório")
    parser.add_argument("--verificar", action="store_true", help="só confere o versao.json existente")
    parser.add_argument("--git-ref", default=None, help="com --verificar: confere o conteúdo deste commit")
    args = parser.parse_args()

    if args.verificar:
        sys.exit(verificar(args.git_ref))

    if not os.path.isfile(args.chave):
        raise SystemExit(f"Chave privada não encontrada: {args.chave}\n"
                         "(passe --chave <arquivo.pem> ou defina MONITOR_CHAVE_RELEASE)")
    if os.path.commonpath([os.path.abspath(args.chave), RAIZ]) == RAIZ:
        raise SystemExit("A chave privada está DENTRO do repositório — mova para fora antes de assinar.")

    script = ler_lf("SCRIPT_EXPLICA.py")
    versao = versao_do_script(script)
    anterior = {}
    if os.path.isfile(SAIDA):
        with open(SAIDA, "r", encoding="utf-8") as arquivo:
            anterior = json.load(arquivo)

    dados = {
        "versao": versao,
        "canal": args.canal,
        "arquivo": "SCRIPT_EXPLICA.py",
        "sha256": hashlib.sha256(script).hexdigest(),
        "tamanho": len(script),
        "arquivos_extras": {},
        "url_exe": anterior.get("url_exe") or URL_EXE,
        "sha256_exe": anterior.get("sha256_exe", "") if anterior.get("versao") == versao else "",
        "notas": args.notas if args.notas is not None else anterior.get("notas", ""),
    }
    for nome in EXTRAS:
        bruto = ler_lf(nome)
        dados["arquivos_extras"][nome] = {"sha256": hashlib.sha256(bruto).hexdigest(), "tamanho": len(bruto)}
    if args.exe:
        with open(args.exe, "rb") as arquivo:
            dados["sha256_exe"] = hashlib.sha256(arquivo.read()).hexdigest()
    publica = chave_publica_do_script(script)
    dados["chave"] = hashlib.sha256(publica).hexdigest()[:16]
    dados["assinatura"] = assinar(args.chave, dados)
    if not verificar_assinatura(publica, dados):
        raise SystemExit("A chave privada não corresponde à CHAVE_PUBLICA_RELEASE do script. Nada foi gravado.")

    with open(SAIDA, "w", encoding="utf-8", newline="\n") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")
    print(f"versao.json -> {versao} (canal {args.canal}, sha256 {dados['sha256'][:12]}..., assinado, "
          f"exe: {'sim' if dados['sha256_exe'] else 'nao'})")


if __name__ == "__main__":
    main()
