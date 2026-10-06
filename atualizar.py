"""
atualizar.py — atualização incremental do índice (arquivos novos, alterados e apagados).
Texto entra na hora (busca por palavra); vetores na CPU com orçamento de tempo, mais recentes primeiro.
Backlog grande (processo novo de milhares de páginas): o que não couber fica p/ a noite seguinte, ou use o Colab.
Pode rodar com o servidor no ar: SQLite aceita leitor + escritor, e os vetores saem em versão nova.
Uso: python atualizar.py
"""
import json
import os
import sqlite3
import sys
import time

import numpy as np

import config
import extrair
from construir_indice import DB, DIM, ler_vetores, mesclar_vetores, normalizar
from preparar_embed import DADOS, ler_chunks

ARQS = os.path.join(DADOS, "arquivos.jsonl")
ORCAMENTO_MIN = config.ORCAMENTO_MIN   # tempo máx. de CPU p/ vetores por rodada (config.json)
LOTE_SALVAR = 100          # salva a cada ~16 min de CPU


def main():
    t0 = time.time()
    linhas_antes = sum(1 for _ in open(ARQS, encoding="utf-8"))
    id_antes = 0
    for l in open(ARQS, encoding="utf-8"):
        id_antes = max(id_antes, json.loads(l).get("id_fim", -1) + 1)

    sys.argv = [sys.argv[0]]
    extrair.main()

    novos_regs = [json.loads(l) for i, l in enumerate(open(ARQS, encoding="utf-8")) if i >= linhas_antes]
    con = sqlite3.connect(DB)
    no_indice = {r[0] for r in con.execute("SELECT arq FROM arquivos")}
    apagados = {a for a in no_indice if not os.path.exists(os.path.join(extrair.RAIZ, a))}
    mexer = {r["arq"] for r in novos_regs} | apagados
    print(f"{len(novos_regs)} arquivos novos/alterados, {len(apagados)} apagados", flush=True)
    if not mexer:
        con.close()
        return

    # 1) tira as versões antigas desses arquivos (texto + FTS)
    velhos = []
    for a in mexer:
        for cid, txt in con.execute("SELECT id, text FROM chunks WHERE arq=? AND id<?", (a, id_antes)).fetchall():
            con.execute("INSERT INTO fts(fts, rowid, text, arq) VALUES('delete', ?, ?, ?)", (cid, txt, a))
            velhos.append(cid)
        con.execute("DELETE FROM chunks WHERE arq=? AND id<?", (a, id_antes))
        con.execute("DELETE FROM arquivos WHERE arq=?", (a,))

    # 2) entra o novo
    novos = [c for c in ler_chunks(os.path.join(DADOS, "chunks.jsonl.gz")) if c["id"] >= id_antes]
    con.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?)",
                    [(c["id"], c["arq"], c["pi"], c["pf"], c["text"]) for c in novos])
    con.executemany("INSERT INTO fts(rowid, text, arq) VALUES (?,?,?)",
                    [(c["id"], c["text"], c["arq"]) for c in novos])
    con.executemany("INSERT OR REPLACE INTO arquivos VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r["arq"], r["ext"], r["mtime"], r["size"], r.get("paginas"), r.get("pag_sem_texto"),
                      r["status"], r.get("dup_de"), r.get("n_chunks")) for r in novos_regs])
    con.commit(); con.close()
    print(f"texto: -{len(velhos)} +{len(novos)} trechos", flush=True)

    if velhos:
        vet, n = mesclar_vetores([], np.empty((0, DIM), np.float16), remover_ids=velhos)
        print(f"vetores: -{len(velhos)} antigos ({n} no total)", flush=True)
    print(f"texto atualizado em {time.time()-t0:.0f}s", flush=True)


def vetorizar_pendentes(orcamento_min=ORCAMENTO_MIN):
    """Vetoriza na CPU os trechos que estão no SQLite mas sem vetor, dos arquivos mais recentes p/ os
    mais antigos, até estourar o orçamento de tempo. Salva a cada lote (progresso não se perde).
    i3-8100T: ~9,5 s por trecho (~450 tokens) — ~950 trechos em 2h30. Backlog grande: usar o Colab."""
    t0 = time.time()
    ids_vet, _ = ler_vetores()
    con = sqlite3.connect(DB)
    todos = [r[0] for r in con.execute("SELECT c.id FROM chunks c LEFT JOIN arquivos a ON a.arq=c.arq "
                                       "ORDER BY a.mtime DESC, c.id")]
    tem = set(ids_vet.tolist())
    pend_ids = [i for i in todos if i not in tem]
    print(f"{len(pend_ids)} trechos sem vetor", flush=True)
    if not pend_ids:
        con.close()
        return
    from sentence_transformers import SentenceTransformer
    from busca import MODELO, _LOCAL
    kw = {} if MODELO == _LOCAL else {"config_kwargs": {"vision_config": None, "audio_config": None}}
    m = SentenceTransformer(MODELO, device="cpu", **kw)
    feitos = 0
    for i in range(0, len(pend_ids), LOTE_SALVAR):
        if time.time() - t0 > orcamento_min * 60:
            break
        ids_lote = pend_ids[i:i + LOTE_SALVAR]
        lote = con.execute(f"SELECT id, arq, text FROM chunks WHERE id IN ({','.join('?' * len(ids_lote))})",
                           ids_lote).fetchall()
        textos = ["title: " + os.path.splitext(os.path.basename(a))[0] + " | text: " + t for _, a, t in lote]
        vecs = m.encode(textos, batch_size=4, normalize_embeddings=True, convert_to_numpy=True)
        mesclar_vetores([r[0] for r in lote], normalizar(vecs))
        feitos += len(lote)
        print(f"  {feitos}/{len(pend_ids)} vetorizados ({(time.time()-t0)/60:.0f} min)", flush=True)
    con.close()
    resto = len(pend_ids) - feitos
    print(f"vetores: +{feitos} em {(time.time()-t0)/60:.0f} min" +
          (f"; {resto} ficam p/ a próxima noite (ou rode o Colab)" if resto else ""), flush=True)


if __name__ == "__main__":
    main()
    from construir_indice import nomes
    nomes()                      # nomes/caminhos de TODOS os arquivos (inclui escaneados, .doc, imagens)
    vetorizar_pendentes()
    print("atualização OK", flush=True)
