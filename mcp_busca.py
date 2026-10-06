"""
mcp_busca.py — conector MCP (stdio) da busca nos seus arquivos locais.
Não carrega modelo: repassa ao serviço local (servidor.py, porta 8765) e o sobe se estiver fora do ar.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import config  # noqa: E402

BASE = f"http://127.0.0.1:{config.PORTA}"
mcp = FastMCP("busca-arquivos", instructions=(
    config.DESCRICAO_MCP + " Use buscar_arquivos para localizar documentos por assunto ou termo exato, "
    "buscar_pastas para localizar pastas e ler_arquivo para ler o conteúdo. PDFs escaneados sem OCR "
    "só são achados pelo nome."))


def _get(rota, timeout=180, **params):
    url = BASE + rota + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"X-Busca": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _porta_ocupada():
    import socket
    try:
        with socket.create_connection(("127.0.0.1", int(BASE.rsplit(":", 1)[1])), timeout=1):
            return True
    except OSError:
        return False


def _garantir_servidor():
    try:
        _get("/api/status", timeout=5)
        return
    except Exception:
        pass
    if not _porta_ocupada():     # só sobe se ninguém estiver na porta (ocupado carregando ≠ fora do ar)
        pyw = os.path.join(AQUI, ".venv", "Scripts", "pythonw.exe")
        subprocess.Popen([pyw, os.path.join(AQUI, "servidor.py")], cwd=AQUI,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    for _ in range(90):
        time.sleep(1)
        try:
            _get("/api/status", timeout=3)
            return
        except Exception:
            pass
    raise RuntimeError("serviço de busca não subiu (rode servidor.py e veja o erro)")


@mcp.tool()
def buscar_arquivos(consulta: str, limite: int = 10, pasta: str | None = None, tipo: str | None = None,
                    modo: str = "hibrido") -> str:
    """Busca nos arquivos locais do usuário por ASSUNTO (semântica), NOME/CAMINHO e/ou TERMO EXATO.

    consulta: descreva o que procura em linguagem natural ("parecer sobre imunidade recíproca de porto"),
              ou termos exatos entre aspas ("0801235-55.2019.4.05.8300"). Número de processo, CNPJ e nomes
              funcionam bem no modo híbrido.
    limite: nº de arquivos (padrão 10, máx 30).
    pasta: filtra por trecho do caminho, ex.: "Clientes\\Empresa X", "Contratos", "2024".
    tipo: "pdf", "docx", "xlsx", "md", "txt" (vírgula para vários).
    modo: "hibrido" (padrão), "semantico" ou "palavra".
    Retorna, por arquivo: caminho relativo, data, páginas do trecho e o trecho que casou.
    """
    _garantir_servidor()
    r = _get("/api/buscar", q=consulta, k=min(max(1, limite), 30), pasta=pasta, tipo=tipo, modo=modo)
    if r.get("erro"):
        return "Erro: " + r["erro"]
    linhas = []
    for n, g in enumerate(r["resultados"], 1):
        data = time.strftime("%d/%m/%Y", time.localtime(g["mtime"])) if g.get("mtime") else "?"
        outros = f" — também em {', '.join(g['tambem'])} com o mesmo nome" if g.get("tambem") else ""
        linhas.append(f"{n}. {g['arq']}  ({data}){outros}")
        if not g["trechos"]:
            linhas.append("   [achado pelo nome — sem texto indexado: PDF escaneado ou tipo sem leitura]")
        for t in g["trechos"]:
            pg = f"[p. {t['pi']}{'-' + str(t['pf']) if t['pf'] != t['pi'] else ''}] " if g["ext"] == ".pdf" else ""
            linhas.append(f"   {pg}{t['text'][:500].replace(chr(10), ' ')}")
    return "\n".join(linhas) or "Nada encontrado."


@mcp.tool()
def buscar_pastas(consulta: str, limite: int = 10, dentro_de: str | None = None) -> str:
    """Localiza PASTAS (não arquivos) pelo nome ou pelo assunto dos arquivos que elas contêm.
    Use quando o usuário quer saber onde fica a pasta de um cliente, processo ou projeto
    ("pasta do parecer sobre ISS do cliente X"). dentro_de restringe a um trecho do caminho.
    Depois, para buscar arquivos só dentro de uma pasta, use buscar_arquivos com pasta=<caminho>."""
    _garantir_servidor()
    r = _get("/api/pastas", q=consulta, k=min(max(1, limite), 30), pasta=dentro_de)
    if r.get("erro"):
        return "Erro: " + r["erro"]
    linhas = []
    for n, g in enumerate(r["resultados"], 1):
        data = time.strftime("%d/%m/%Y", time.localtime(g["mtime"])) if g.get("mtime") else "?"
        motivo = "contém: " + ", ".join(g["exemplos"]) if g["exemplos"] else "casou pelo nome"
        linhas.append(f"{n}. {g['pasta']}  ({g['n_arquivos']} arquivos, modificada {data}) — {motivo}")
    return "\n".join(linhas) or "Nenhuma pasta encontrada."


@mcp.tool()
def ler_arquivo(arquivo: str, pagina_ini: int | None = None, pagina_fim: int | None = None,
                max_caracteres: int = 40000) -> str:
    """Lê o texto de um arquivo devolvido por buscar_arquivos (caminho relativo exatamente como veio).
    Em PDF, use pagina_ini/pagina_fim para ler só as páginas do trecho (e vizinhas) — processos têm milhares
    de páginas. DOCX/XLSX/MD/TXT vêm inteiros até max_caracteres."""
    _garantir_servidor()
    r = _get("/api/ler", arq=arquivo, pi=pagina_ini, pf=pagina_fim, max=max_caracteres)
    if r.get("erro"):
        return "Erro: " + r["erro"]
    return (f"{r['arq']} — páginas {r['pagina_ini']}-{r['pagina_fim']} de {r['paginas_total']}\n\n{r['texto']}")


@mcp.tool()
def status_busca() -> str:
    """Estado do índice: nº de arquivos, trechos, vetores e PDFs escaneados ainda sem OCR."""
    _garantir_servidor()
    return json.dumps(_get("/api/status"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    mcp.run()
