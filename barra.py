"""
barra.py — barra de busca estilo Spotlight para os seus arquivos (Alt+Espaço).
Processo leve e residente: registra o atalho global e mostra/esconde a janela. A busca em si é do
servidor.py (sobe sozinho na primeira busca; o modelo sai da RAM após 15 min sem uso).

Teclas: ↑↓ escolher · Enter abrir · Ctrl+Enter mostrar na pasta · Ctrl+O tela completa · Esc fechar
Filtros dentro da consulta:  tipo:pdf   pasta:Contratos   pasta:"Cliente X"
Tab (ou clique) alterna Arquivos | Pastas — em Pastas, Enter abre a pasta no Explorer.
Teste sem atalho: pythonw barra.py --teste "consulta" [--pastas]  (grava dados/barra_teste.png e sai)
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
import winreg

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
from extrair import RAIZ  # noqa: E402

import config  # noqa: E402

BASE = f"http://127.0.0.1:{config.PORTA}"
MAX_RES = 8
user32 = ctypes.windll.user32
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_SPACE, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x20, 0x0312

CORES = {
    False: dict(bg="#ffffff", borda="#d6d3cc", fg="#1d1d1b", muted="#6b6a65", sel="#f3e9de",
                acento="#8a5a2b", selo="#efede8"),
    True: dict(bg="#1f1f1c", borda="#3a3934", fg="#ecebe6", muted="#9a998f", sel="#2d241b",
               acento="#d9a16a", selo="#2a2926"),
}


# ---------------------------------------------------------------- utilidades
def instancia_unica():
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\BuscaArquivosBarra")
    return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


def tema_escuro():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def api(rota, timeout=180, **params):
    url = BASE + rota + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
    req = urllib.request.Request(url, headers={"X-Busca": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def servidor_no_ar():
    try:
        api("/api/status", timeout=2)
        return True
    except Exception:
        return False


_subindo = threading.Lock()


def porta_ocupada():
    """Conexão TCP aceita = servidor existe (mesmo ocupado carregando o modelo e lento p/ responder)."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(BASE.rsplit(":", 1)[1])), timeout=1):
            return True
    except OSError:
        return False


def subir_servidor():
    with _subindo:
        if servidor_no_ar():
            return True
        if porta_ocupada():                      # está subindo/ocupado: espera, não duplica
            for _ in range(120):
                time.sleep(0.5)
                if servidor_no_ar():
                    return True
            return False
        subprocess.Popen([os.path.join(AQUI, ".venv", "Scripts", "pythonw.exe"), os.path.join(AQUI, "servidor.py")],
                         cwd=AQUI, creationflags=0x00000008 | 0x00000200)
        for _ in range(90):
            time.sleep(0.5)
            if servidor_no_ar():
                return True
        return False


def separar_filtros(q):
    filtros = {}

    def tira(m):
        filtros[m.group(1).lower()] = m.group(2).strip('"')
        return " "
    q = re.sub(r'\b(tipo|pasta):("[^"]+"|\S+)', tira, q, flags=re.I)
    return " ".join(q.split()), filtros.get("tipo"), filtros.get("pasta")


def trecho_em_volta(texto, consulta, largura=150):
    """Pedaço do trecho em volta da primeira palavra da consulta (ou o começo)."""
    plano = " ".join(texto.split())
    base = sem_acento(plano)
    pos = -1
    for t in sorted(re.findall(r"\w{4,}", sem_acento(consulta)), key=len, reverse=True):
        pos = base.find(t)
        if pos >= 0:
            break
    ini = max(0, pos - largura // 3) if pos >= 0 else 0
    s = plano[ini:ini + largura]
    return ("…" if ini > 0 else "") + s + ("…" if ini + largura < len(plano) else "")


def ouvir_atalho(fila):
    """Registra o atalho global numa thread com laço de mensagens próprio."""
    for mod, nome in ((MOD_ALT, "Alt+Espaço"), (MOD_CONTROL | MOD_ALT, "Ctrl+Alt+Espaço")):
        if user32.RegisterHotKey(None, 1, mod | MOD_NOREPEAT, VK_SPACE):
            fila.put(("atalho", nome))
            break
    else:
        fila.put(("atalho", None))
        return
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY:
            fila.put(("mostrar", None))


# ---------------------------------------------------------------- janela
class Barra:
    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.c = CORES[tema_escuro()]
        c = self.c
        self.root = root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=c["borda"])
        esc = root.winfo_fpixels("1i") / 96
        self.larg = int(760 * esc)
        self.chars_trecho = 118          # cabe numa linha na largura da barra
        fonte = "Segoe UI Variable Text" if "Segoe UI Variable Text" in root.tk.call("font", "families") else "Segoe UI"
        self.f_entrada = (fonte, 16)
        self.f_nome = (fonte, 11, "bold")
        self.f_peq = (fonte, 9)
        self.f_selo = (fonte, 8, "bold")

        self.caixa = tk.Frame(root, bg=c["bg"])
        self.caixa.pack(padx=1, pady=1, fill="both", expand=True)
        topo = tk.Frame(self.caixa, bg=c["bg"])
        topo.pack(fill="x", padx=16, pady=(14, 10))
        tk.Label(topo, text="⌕", font=(fonte, 18), bg=c["bg"], fg=c["acento"]).pack(side="left", padx=(0, 10))
        # seletor Arquivos | Pastas (clique ou Tab)
        self.alvo, self.alvo_res = "arquivos", "arquivos"
        seletor = tk.Frame(topo, bg=c["bg"])
        seletor.pack(side="right", padx=(10, 0))
        self.chips = {}
        for chave, rot in (("arquivos", "Arquivos"), ("pastas", "Pastas")):
            ch = tk.Label(seletor, text=rot, font=(fonte, 9), padx=10, pady=2, cursor="hand2")
            ch.pack(side="left", padx=(4, 0))
            ch.bind("<Button-1>", lambda ev, k=chave: self._trocar_alvo(k))
            self.chips[chave] = ch
        self.entrada = tk.Entry(topo, font=self.f_entrada, relief="flat", bd=0, bg=c["bg"], fg=c["fg"],
                                insertbackground=c["fg"], highlightthickness=0)
        self.entrada.pack(side="left", fill="x", expand=True)
        self.dica = tk.Label(topo, text="", font=self.f_entrada, bg=c["bg"], fg=c["muted"])
        self.dica.place(in_=self.entrada, x=0, rely=0.5, anchor="w")
        self.dica.bind("<Button-1>", lambda e: self.entrada.focus_set())
        self.sep = tk.Frame(self.caixa, bg=c["borda"], height=1)
        self.lista = tk.Frame(self.caixa, bg=c["bg"])
        self.rodape = tk.Label(self.caixa, font=self.f_peq, bg=c["bg"], fg=c["muted"], anchor="w")
        self.rodape.pack(fill="x", padx=16, pady=(0, 8), side="bottom")
        self.lista.pack(fill="x", side="top")

        self.resultados, self.sel, self.seq, self._deb, self.linhas = [], 0, 0, None, []
        self.ultimo_texto, self.modo_txt, self.atalho = "", "", None
        self.auto_esconder = True
        self.fila = queue.Queue()
        e = self.entrada
        e.bind("<KeyRelease>", self._digitou)
        e.bind("<Down>", lambda ev: self._mover(1))
        e.bind("<Up>", lambda ev: self._mover(-1))
        e.bind("<Return>", lambda ev: self._abrir(pasta=False))
        e.bind("<Control-Return>", lambda ev: self._abrir(pasta=True))
        e.bind("<Control-o>", lambda ev: self._tela_completa())
        e.bind("<Escape>", lambda ev: self.esconder())
        e.bind("<Tab>", lambda ev: self._trocar_alvo("pastas" if self.alvo == "arquivos" else "arquivos"))
        root.bind("<FocusOut>", lambda ev: root.after(200, self._talvez_esconder))
        self._pintar_chips()
        self._rodape_padrao()
        root.after(40, self._consumir_fila)

    # ---------- Arquivos | Pastas
    def _pintar_chips(self):
        c = self.c
        for k, ch in self.chips.items():
            on = k == self.alvo
            ch.configure(bg=c["sel"] if on else c["selo"], fg=c["fg"] if on else c["muted"])
        self.dica.configure(text="Buscar nos arquivos…   (tipo:pdf  pasta:cliente)" if self.alvo == "arquivos"
                            else "Buscar pastas pelo nome ou assunto…")

    def _trocar_alvo(self, alvo):
        if alvo != self.alvo:
            self.alvo = alvo
            self._pintar_chips()
            if separar_filtros(self.entrada.get())[0]:
                self._buscar()
        self.entrada.focus_set()
        return "break"

    # ---------- mostrar / esconder
    def _hwnd(self):
        return user32.GetParent(self.root.winfo_id())

    def mostrar(self):
        r = self.root
        self.x = (r.winfo_screenwidth() - self.larg) // 2
        self.y = int(r.winfo_screenheight() * 0.12)
        self._ajustar_altura()
        r.deiconify()
        r.lift()
        try:   # cantos arredondados do Windows 11
            v = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(self._hwnd(), 33, ctypes.byref(v), 4)
        except Exception:
            pass
        user32.SetForegroundWindow(self._hwnd())
        r.focus_force()
        self.entrada.focus_set()
        self.entrada.select_range(0, "end")
        threading.Thread(target=subir_servidor, daemon=True).start()   # já vai aquecendo

    def esconder(self):
        self.root.withdraw()

    def _talvez_esconder(self):
        if self.auto_esconder and self.root.state() != "withdrawn" and self.root.focus_displayof() is None:
            self.esconder()

    def _ajustar_altura(self):
        self.root.update_idletasks()
        limite = self.root.winfo_screenheight() - self.y - 60       # não passar da barra de tarefas
        while self.caixa.winfo_reqheight() + 2 > limite and len(self.linhas) > 1:
            self.linhas.pop().destroy()                              # tela baixa: mostra menos resultados
            self.resultados = self.resultados[:len(self.linhas)]
            self.root.update_idletasks()
        h = self.caixa.winfo_reqheight() + 2
        self.root.geometry(f"{self.larg}x{h}+{self.x}+{self.y}")

    # ---------- fila de eventos (threads → tk)
    def _consumir_fila(self):
        try:
            while True:
                tipo, dado = self.fila.get_nowait()
                if tipo == "mostrar":
                    if self.root.state() == "withdrawn":
                        self.mostrar()
                    else:
                        self.esconder()
                elif tipo == "atalho":
                    self.atalho = dado
                    log(f"atalho registrado: {dado}" if dado else
                        "ATENÇÃO: nenhum atalho disponível (Alt+Espaço e Ctrl+Alt+Espaço ocupados por outro programa)")
                    self._rodape_padrao()
                elif tipo == "res":
                    seq, r, modo, alvo = dado
                    if seq == self.seq:
                        rot = {("arquivos", "palavra"): "só palavra", ("arquivos", "hibrido"): "por assunto + palavra",
                               ("pastas", "palavra"): "pastas pelo nome",
                               ("pastas", "hibrido"): "pastas pelo nome + conteúdo"}[(alvo, modo)]
                        self.modo_txt = f"{rot} · {r['ms']/1000:.1f} s"
                        self._mostrar_resultados(r["resultados"], alvo)
                elif tipo == "rodape":
                    seq, txt = dado
                    if seq == self.seq:
                        self.rodape.configure(text=txt)
        except queue.Empty:
            pass
        self.root.after(40, self._consumir_fila)

    # ---------- busca
    def _digitou(self, ev):
        txt = self.entrada.get()
        (self.dica.place_forget() if txt else self.dica.place(in_=self.entrada, x=0, rely=0.5, anchor="w"))
        if txt == self.ultimo_texto:
            return
        self.ultimo_texto = txt
        if self._deb:
            self.root.after_cancel(self._deb)
        self._deb = self.root.after(300, self._buscar)

    def _buscar(self):
        self.seq += 1
        seq = self.seq
        texto, tipo, pasta = separar_filtros(self.entrada.get())
        if len(texto) < 2:
            self.modo_txt = ""
            self._mostrar_resultados([])
            return
        self.rodape.configure(text="Buscando…")
        alvo = self.alvo

        def trabalho():
            if not subir_servidor():
                self.fila.put(("rodape", (seq, "Não consegui subir o serviço de busca (servidor.py).")))
                return
            try:
                # 1º passo rápido (palavra / nome da pasta), 2º completo (com o modelo)
                if alvo == "pastas":
                    r = api("/api/pastas", q=texto, k=MAX_RES, pasta=pasta, conteudo="0", timeout=30)
                else:
                    r = api("/api/buscar", q=texto, k=MAX_RES, tipo=tipo, pasta=pasta, modo="palavra", timeout=30)
                self.fila.put(("res", (seq, r, "palavra", alvo)))
                if seq != self.seq:
                    return
                if not api("/api/status", timeout=5).get("modelo_carregado"):
                    self.fila.put(("rodape", (seq, "Resultado rápido · carregando o modelo para a busca por assunto…")))
                if alvo == "pastas":
                    r = api("/api/pastas", q=texto, k=MAX_RES, pasta=pasta)
                else:
                    r = api("/api/buscar", q=texto, k=MAX_RES, tipo=tipo, pasta=pasta, modo="hibrido")
                self.fila.put(("res", (seq, r, "hibrido", alvo)))
            except Exception as e:
                self.fila.put(("rodape", (seq, f"Erro na busca: {e}")))
        threading.Thread(target=trabalho, daemon=True).start()

    # ---------- lista de resultados
    def _mostrar_resultados(self, res, alvo=None):
        tk, c = self.tk, self.c
        for w in self.lista.winfo_children():
            w.destroy()
        self.resultados, self.sel, self.linhas = res, 0, []
        self.alvo_res = alvo or self.alvo
        consulta = separar_filtros(self.entrada.get())[0]
        if res:
            self.sep.pack(fill="x", before=self.lista)
        else:
            self.sep.pack_forget()
        for i, g in enumerate(res):
            eh_pasta = self.alvo_res == "pastas"
            partes = (g["pasta"] if eh_pasta else g["arq"]).split("\\")
            nome = partes.pop()
            caminho = " › ".join(partes if len(partes) <= 4 else partes[:2] + ["…"] + partes[-2:]) or "(raiz)"
            data = time.strftime("%d/%m/%Y", time.localtime(g["mtime"])) if g.get("mtime") else ""
            lin = tk.Frame(self.lista, bg=c["bg"], padx=16, pady=7)
            lin.pack(fill="x")
            l1 = tk.Frame(lin, bg=c["bg"])
            l1.pack(fill="x")
            rot_selo = "PASTA" if eh_pasta else "+".join(e.lstrip(".").upper() for e in [g["ext"]] + g.get("tambem", []))
            selo = tk.Label(l1, text=rot_selo, font=self.f_selo,
                            bg=c["selo"], fg=c["acento"] if eh_pasta else c["muted"], padx=5)
            selo.pack(side="left", padx=(0, 8))
            tk.Label(l1, text=nome if len(nome) <= 68 else nome[:65] + "…", font=self.f_nome, bg=c["bg"],
                     fg=c["fg"], anchor="w").pack(side="left")
            tk.Label(l1, text=data, font=self.f_peq, bg=c["bg"], fg=c["muted"]).pack(side="right")
            tk.Label(lin, text=caminho[:120], font=self.f_peq, bg=c["bg"], fg=c["muted"], anchor="w").pack(fill="x")
            if eh_pasta:
                info = f"{g['n_arquivos']} arquivos indexados"
                if g.get("exemplos"):
                    info += " · contém: " + ", ".join(g["exemplos"])
                elif g.get("pelo_nome"):
                    info += " · casou pelo nome"
                linha3 = info if len(info) <= self.chars_trecho else info[:self.chars_trecho - 1] + "…"
            elif g["trechos"]:
                t = g["trechos"][0]
                pg = f"p. {t['pi']} · " if g["ext"] == ".pdf" else ""
                linha3 = pg + trecho_em_volta(t["text"], consulta, self.chars_trecho)
            else:
                linha3 = "achado pelo nome · sem texto indexado (escaneado ou tipo sem leitura)"
            tk.Label(lin, text=linha3, font=self.f_peq, bg=c["bg"], fg=c["fg"], anchor="w").pack(fill="x")
            for w in [lin, l1] + lin.winfo_children() + l1.winfo_children():
                w.bind("<Button-1>", lambda ev, k=i: (self._marcar(k), self._abrir(False)))
                w.bind("<Enter>", lambda ev, k=i: self._marcar(k))
            self.linhas.append(lin)
        self._marcar(0)
        self._rodape_padrao()
        self._ajustar_altura()

    def _marcar(self, k):
        if not self.resultados:
            return
        self.sel = max(0, min(k, len(self.resultados) - 1))
        for i, lin in enumerate(self.linhas):
            cor = self.c["sel"] if i == self.sel else self.c["bg"]
            for w in [lin] + lin.winfo_children():
                if w.cget("bg") in (self.c["bg"], self.c["sel"]):
                    w.configure(bg=cor)
                for ww in w.winfo_children():
                    if ww.cget("bg") in (self.c["bg"], self.c["sel"]):
                        ww.configure(bg=cor)

    def _mover(self, d):
        self._marcar(self.sel + d)
        return "break"

    def _abrir(self, pasta):
        if not self.resultados:
            return "break"
        g = self.resultados[self.sel]
        p = os.path.join(RAIZ, g["pasta"] if self.alvo_res == "pastas" else g["arq"])
        if pasta:                                   # Ctrl+Enter: abre a pasta-mãe com o item selecionado
            subprocess.Popen(["explorer", "/select,", p])
        else:
            os.startfile(p)
        self.esconder()
        return "break"

    def _tela_completa(self):
        webbrowser.open(BASE + "/?" + urllib.parse.urlencode({"q": self.entrada.get(), "alvo": self.alvo}))
        self.esconder()
        return "break"

    def _rodape_padrao(self):
        teclas = "↑↓ · Enter abrir · Ctrl+Enter mostrar na pasta · Tab arquivos/pastas · Ctrl+O tela cheia · Esc"
        extra = self.modo_txt if self.resultados or self.modo_txt else (f"atalho: {self.atalho}" if self.atalho else "")
        if self.modo_txt and not self.resultados:
            extra = "nada encontrado · " + self.modo_txt
        self.rodape.configure(text=teclas + (f"      {extra}" if extra else ""))


LOG = os.path.join(config.DADOS, "barra.log")


def log(msg):
    """Registro curto em dados/barra.log (pythonw não tem console: sem isso uma queda não deixa rastro)."""
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%d/%m %H:%M:%S')} [{os.getpid()}] {msg}\n")
    except OSError:
        pass


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    teste = sys.argv[sys.argv.index("--teste") + 1] if "--teste" in sys.argv else None
    if not teste and not instancia_unica():
        return
    if not teste:
        import atexit
        import traceback
        log("barra iniciada")
        atexit.register(lambda: log("barra encerrada normalmente"))
        sys.excepthook = lambda t, v, tb: log("ERRO fatal: " + "".join(traceback.format_exception(t, v, tb))[-1500:])
    b = Barra()
    if not teste:
        b.root.report_callback_exception = lambda t, v, tb: log(
            "erro na interface: " + "".join(traceback.format_exception(t, v, tb))[-1500:])
    if teste:
        b.auto_esconder = False
        if "--pastas" in sys.argv:
            b.alvo = "pastas"
            b._pintar_chips()
        b.mostrar()
        b.entrada.insert(0, teste)
        b._digitou(None)

        def foto():
            from PIL import ImageGrab
            b.root.update()
            x, y = b.root.winfo_rootx(), b.root.winfo_rooty()
            img = ImageGrab.grab((x, y, x + b.root.winfo_width(), y + b.root.winfo_height()), all_screens=True)
            img.save(os.path.join(AQUI, "dados", "barra_teste.png"))
            b.root.destroy()
        b.root.after(int(float(os.environ.get("BARRA_ESPERA", "12")) * 1000), foto)
    else:
        threading.Thread(target=ouvir_atalho, args=(b.fila,), daemon=True).start()
    b.root.mainloop()


if __name__ == "__main__":
    main()
