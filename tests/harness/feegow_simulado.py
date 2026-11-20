"""Servidor HTTP local que imita o caminho do Feegow usado pelo monitor (SEM dados reais).

  /main/?P=Login            -> formulário antigo (User / password / Entrar)
  POST /main/               -> 302 para /main/?P=Home (sessão "ativa")
  /pre-v8/?P=ListaEspera... -> a Lista de Espera simulada (botão .callTicketBtn + modal)

Uso no teste:  srv = iniciar(); srv.url_base ... srv.shutdown()
Nenhuma requisição sai da máquina: tudo em 127.0.0.1.
"""
import http.server
import os
import threading
import urllib.parse

AQUI = os.path.dirname(os.path.abspath(__file__))

LOGIN_HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Login (simulado)</title></head>
<body><h2>Login simulado</h2><form method="post" action="/main/">
<input id="User" name="User"><input id="password" type="password" name="password">
<button type="submit" id="Entrar">Entrar</button></form></body></html>"""

HOME_HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Home (simulada)</title></head>
<body><h2>Feegow simulado: sessão ativa</h2><p>Página inicial simulada para testes do monitor.</p></body></html>"""


class _Manipulador(http.server.BaseHTTPRequestHandler):
    contador = {"get": 0, "post": 0}

    def log_message(self, *_a):
        pass

    def _responder(self, corpo, status=200, tipo="text/html; charset=utf-8", extra=None):
        dados = corpo.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        self.contador["get"] += 1
        partes = urllib.parse.urlsplit(self.path)
        consulta = {k.lower(): v for k, v in urllib.parse.parse_qs(partes.query).items()}
        pagina = (consulta.get("p") or [""])[0].lower()
        if pagina == "listaespera":
            with open(os.path.join(AQUI, "ListaEspera_simulada.html"), encoding="utf-8") as f:
                return self._responder(f.read())
        if pagina == "login":
            return self._responder(LOGIN_HTML)
        if partes.path.startswith("/main"):
            return self._responder(HOME_HTML)
        return self._responder("nao encontrado", status=404, tipo="text/plain")

    def do_POST(self):
        self.contador["post"] += 1
        tamanho = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(tamanho)
        self._responder("", status=302, extra={"Location": "/main/?P=Home"})


def iniciar(porta=0):
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", porta), _Manipulador)
    servidor.daemon_threads = True
    servidor.url_base = f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.porta = servidor.server_address[1]
    servidor.contador = _Manipulador.contador
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


if __name__ == "__main__":
    srv = iniciar(8765)
    print("Feegow simulado em", srv.url_base)
    threading.Event().wait()
