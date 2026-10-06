"""
servidor.py — serviço local da busca (http://127.0.0.1:8765). Um processo só segura o modelo;
a interface web e o MCP conversam com ele. Só escuta em localhost.

  GET /                      interface
  GET /api/buscar?q=&k=&pasta=&tipo=&modo=
  GET /api/ler?arq=&pi=&pf=
  GET /api/abrir?arq=        abre o arquivo no programa padrão
  GET /api/pasta?arq=        abre o Explorer com o arquivo selecionado
  GET /api/status
"""
import json
import os
import subprocess
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from busca import Motor  # noqa: E402
from construir_indice import EXTS_NOMES as EXTS  # noqa: E402  (tipos que a busca pode abrir)

from config import PORTA  # noqa: E402
AQUI = os.path.dirname(os.path.abspath(__file__))
MOTOR = None      # criado só depois de garantir a porta (cópia duplicada sai antes de carregar o modelo)


class ServidorExclusivo(ThreadingHTTPServer):
    # No Windows, SO_REUSEADDR (padrão do http.server) deixa VÁRIOS processos na mesma porta.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        # proteção: só localhost (contra DNS rebinding) e API só com cabeçalho próprio
        # (site de fora não consegue mandar cabeçalho custom sem preflight CORS, que não respondemos)
        if self.headers.get("Host", "") not in (f"127.0.0.1:{PORTA}", f"localhost:{PORTA}"):
            return self._json({"erro": "host"}, 403)
        if u.path.startswith("/api/") and self.headers.get("X-Busca") != "1":
            return self._json({"erro": "cabecalho"}, 403)
        if u.path in ("/api/abrir", "/api/pasta") and os.path.splitext(q.get("arq", ""))[1].lower() not in EXTS:
            return self._json({"erro": "tipo"}, 403)
        if MOTOR is None:
            return self._json({"erro": "iniciando"}, 503)
        try:
            if u.path == "/":
                b = open(os.path.join(AQUI, "ui.html"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            elif u.path == "/api/buscar":
                self._json(MOTOR.buscar(q.get("q", ""), k=int(q.get("k", 15)), pasta=q.get("pasta") or None,
                                        tipo=q.get("tipo") or None, modo=q.get("modo", "hibrido")))
            elif u.path == "/api/pastas":
                self._json(MOTOR.buscar_pastas(q.get("q", ""), k=int(q.get("k", 10)), pasta=q.get("pasta") or None,
                                               com_conteudo=q.get("conteudo", "1") != "0"))
            elif u.path == "/api/abrir_pasta":
                p = MOTOR.caminho(q["pasta"] + os.sep + "x")[:-2]      # valida que está dentro da raiz
                if not os.path.isdir(p):
                    raise ValueError("pasta inexistente")
                os.startfile(p)
                self._json({"ok": True})
            elif u.path == "/api/ler":
                self._json(MOTOR.ler(q["arq"], int(q["pi"]) if q.get("pi") else None,
                                     int(q["pf"]) if q.get("pf") else None,
                                     int(q.get("max", 60000))))
            elif u.path == "/api/abrir":
                os.startfile(MOTOR.caminho(q["arq"]))
                self._json({"ok": True})
            elif u.path == "/api/pasta":
                subprocess.Popen(["explorer", "/select,", MOTOR.caminho(q["arq"])])
                self._json({"ok": True})
            elif u.path == "/api/status":
                self._json(MOTOR.status())
            else:
                self._json({"erro": "rota inexistente"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._json({"erro": repr(e)}, 500)


if __name__ == "__main__":
    try:
        srv = ServidorExclusivo(("127.0.0.1", PORTA), H)
    except OSError:
        print(f"porta {PORTA} já em uso: outro servidor de busca está no ar — saindo", flush=True)
        sys.exit(0)
    MOTOR = Motor()
    print(f"busca-arquivos em http://127.0.0.1:{PORTA}", flush=True)
    srv.serve_forever()
