"""
construir_indice.py — monta o índice local a partir da extração + vetores vindos do Colab.

  dados/indice.db        SQLite: chunks (texto) + fts (FTS5, busca por palavra) + arquivos
  dados/vetores_<versão>.f16 + _ids.npy   matriz N x DIM float16 (memmap, normalizada) e id de cada linha
  dados/vetores_atual.json               ponteiro p/ a versão vigente (o servidor recarrega quando muda)

Uso:
  python construir_indice.py texto                       # (re)cria o SQLite a partir de chunks.jsonl.gz (+ nomes)
  python construir_indice.py nomes                       # só o índice de nomes/caminhos (todos os arquivos)
  python construir_indice.py vetores <emb.npy> <ids.npy>  # acrescenta/atualiza vetores (corta p/ DIM e renormaliza)
"""
import json
import os
import sqlite3
import sys
import time

import numpy as np

from preparar_embed import DADOS, ler_chunks

DIM = 256
DB = os.path.join(DADOS, "indice.db")
PONTEIRO = os.path.join(DADOS, "vetores_atual.json")   # {"vet": nome.f16, "ids": nome.npy, "n": N}


def vetores_atuais():
    """(caminho .f16, caminho ids.npy) da versão vigente, ou (None, None)."""
    if not os.path.exists(PONTEIRO):
        return None, None
    p = json.load(open(PONTEIRO, encoding="utf-8"))
    return os.path.join(DADOS, p["vet"]), os.path.join(DADOS, p["ids"])


def gravar_vetores(ids, mat):
    """Grava nova versão (arquivos novos + ponteiro). Não sobrescreve a vigente: o servidor pode estar
    com ela mapeada (Windows não deixa trocar arquivo aberto). Versões antigas são apagadas quando possível."""
    tag = time.strftime("%Y%m%d_%H%M%S")
    vet, vids = f"vetores_{tag}.f16", f"vetores_{tag}_ids.npy"
    mm = np.memmap(os.path.join(DADOS, vet), dtype=np.float16, mode="w+", shape=(len(ids), DIM))
    mm[:] = mat; mm.flush(); del mm
    np.save(os.path.join(DADOS, vids), np.asarray(ids, dtype=np.int64))
    tmp = PONTEIRO + ".tmp"
    json.dump({"vet": vet, "ids": vids, "n": int(len(ids))}, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, PONTEIRO)
    for f in os.listdir(DADOS):                       # limpa versões velhas que ninguém está usando
        if f.startswith("vetores_") and f not in (vet, vids, "vetores_atual.json"):
            try:
                os.remove(os.path.join(DADOS, f))
            except OSError:
                pass
    return os.path.join(DADOS, vet)


def ler_vetores():
    vet, vids = vetores_atuais()
    if not vet:
        return np.empty(0, np.int64), np.empty((0, DIM), np.float16)
    ids = np.load(vids)
    return ids, np.asarray(np.memmap(vet, dtype=np.float16, mode="r", shape=(len(ids), DIM)))


def normalizar(emb):
    """Corta p/ DIM (Matryoshka) e renormaliza; float16."""
    out = np.empty((len(emb), DIM), np.float16)
    for i in range(0, len(emb), 50000):
        b = np.asarray(emb[i:i + 50000, :DIM], dtype=np.float32)
        b /= np.linalg.norm(b, axis=1, keepdims=True) + 1e-12
        out[i:i + 50000] = b.astype(np.float16)
    return out


def mesclar_vetores(novos_ids, novos, remover_ids=()):
    """Junta novos vetores (já normalizados, DIM) aos atuais; ids novos substituem; remove remover_ids."""
    ids, mat = ler_vetores()
    tirar = np.isin(ids, np.concatenate([np.asarray(novos_ids, np.int64), np.asarray(list(remover_ids), np.int64)]))
    todos_ids = np.concatenate([ids[~tirar], np.asarray(novos_ids, np.int64)])
    tudo = np.concatenate([mat[~tirar], novos])
    ordem = np.argsort(todos_ids, kind="stable")
    return gravar_vetores(todos_ids[ordem], tudo[ordem]), len(todos_ids)


def texto():
    novo = DB + ".novo"
    if os.path.exists(novo):
        os.remove(novo)
    con = sqlite3.connect(novo)
    con.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, arq TEXT, pi INT, pf INT, text TEXT);
        CREATE TABLE arquivos(arq TEXT PRIMARY KEY, ext TEXT, mtime INT, size INT, paginas INT,
                              pag_sem_texto INT, status TEXT, dup_de TEXT, n_chunks INT);
        CREATE VIRTUAL TABLE fts USING fts5(text, arq UNINDEXED, content='chunks', content_rowid='id',
                                            tokenize='unicode61 remove_diacritics 2');
    """)
    lote, n = [], 0
    for r in ler_chunks(os.path.join(DADOS, "chunks.jsonl.gz")):
        lote.append((r["id"], r["arq"], r["pi"], r["pf"], r["text"]))
        if len(lote) >= 20000:
            con.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?)", lote); n += len(lote); lote = []
            print(f"  {n} trechos...", flush=True)
    con.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?)", lote); n += len(lote)
    arqs = {}
    for l in open(os.path.join(DADOS, "arquivos.jsonl"), encoding="utf-8"):
        r = json.loads(l)
        arqs[r["arq"]] = (r["arq"], r["ext"], r["mtime"], r["size"], r.get("paginas"), r.get("pag_sem_texto"),
                          r["status"], r.get("dup_de"), r.get("n_chunks"))
    con.executemany("INSERT OR REPLACE INTO arquivos VALUES (?,?,?,?,?,?,?,?,?)", list(arqs.values()))
    print("  indexando FTS...", flush=True)
    con.execute("INSERT INTO fts(fts) VALUES('rebuild')")
    con.execute("CREATE INDEX ix_chunks_arq ON chunks(arq)")
    con.commit(); con.close()
    os.replace(novo, DB)
    print(f"texto OK: {n} trechos, {len(arqs)} arquivos -> {DB} ({os.path.getsize(DB)/1e9:.2f} GB)")


EXTS_NOMES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".csv", ".md", ".txt", ".rtf", ".odt", ".ods",
              ".pptx", ".ppt", ".msg", ".eml", ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".tif", ".tiff",
              ".html", ".htm", ".xml", ".mp3", ".mp4", ".m4a", ".ogg", ".opus"}


def nomes():
    """(Re)cria o índice de NOMES e CAMINHOS de todos os arquivos de documento/imagem da raiz — inclusive
    os que não têm texto (PDF escaneado, .doc, imagem, duplicata). Rápido (segundos): roda toda noite."""
    from extrair import PULAR_DIRS, RAIZ
    linhas = []
    for d, subdirs, files in os.walk(RAIZ):
        subdirs[:] = [s for s in subdirs if s not in PULAR_DIRS and not s.startswith(".")]
        for f in files:
            base, ext = os.path.splitext(f)
            if ext.lower() in EXTS_NOMES and not f.startswith("~$"):
                rel = os.path.relpath(os.path.join(d, f), RAIZ)
                linhas.append((base, os.path.dirname(rel), rel))
    con = sqlite3.connect(DB)
    con.executescript("""
        DROP TABLE IF EXISTS nomes;
        CREATE VIRTUAL TABLE nomes USING fts5(nome, caminho, arq UNINDEXED,
                                              tokenize='unicode61 remove_diacritics 2', prefix='2 3');
    """)
    con.executemany("INSERT INTO nomes(nome, caminho, arq) VALUES (?,?,?)", linhas)
    con.commit(); con.close()
    print(f"nomes OK: {len(linhas)} arquivos")


def vetores(emb_npy, ids_npy):
    emb = np.load(emb_npy, mmap_mode="r")
    ids = np.load(ids_npy)
    assert emb.shape[0] == len(ids), f"{emb.shape[0]} vetores != {len(ids)} ids"
    vet, n = mesclar_vetores(ids, normalizar(emb))
    print(f"vetores OK: {n} x {DIM} -> {vet} ({os.path.getsize(vet)/1e6:.0f} MB)")


if __name__ == "__main__":
    if sys.argv[1] == "texto":
        texto()
        nomes()
    elif sys.argv[1] == "nomes":
        nomes()
    elif sys.argv[1] == "vetores":
        vetores(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
