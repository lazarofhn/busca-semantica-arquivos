"""Atalho: sobe o serviço de busca (se não estiver no ar) e abre a interface no navegador."""
import os
import subprocess
import time
import urllib.request
import webbrowser

AQUI = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, AQUI)
import config  # noqa: E402

URL = f"http://127.0.0.1:{config.PORTA}/"


def no_ar():
    try:
        req = urllib.request.Request(URL + "api/status", headers={"X-Busca": "1"})
        urllib.request.urlopen(req, timeout=2)
        return True
    except Exception:
        return False


def porta_ocupada():
    import socket
    try:
        with socket.create_connection(("127.0.0.1", config.PORTA), timeout=1):
            return True
    except OSError:
        return False


if not no_ar() and not porta_ocupada():
    subprocess.Popen([os.path.join(AQUI, ".venv", "Scripts", "pythonw.exe"), os.path.join(AQUI, "servidor.py")],
                     cwd=AQUI, creationflags=0x00000008 | 0x00000200)
    for _ in range(60):
        time.sleep(0.5)
        if no_ar():
            break
webbrowser.open(URL)
