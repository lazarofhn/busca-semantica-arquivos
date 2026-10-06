"""
preparar_embed.py — monta a entrada do Colab a partir de dados/chunks.jsonl.gz.
Gera <saida>.jsonl.gz com {id, t, text} (t = nome do arquivo sem extensão) e <saida>.ids.npy (ordem dos vetores).
Tolera o .gz ainda sendo escrito (para no último registro íntegro) e ids repetidos (fica o último).
Uso: python preparar_embed.py <saida_sem_ext> [--max N] [--desde ID]
"""
import gzip
import json
import os
import sys
import zlib

import numpy as np

from config import DADOS


def ler_chunks(caminho):
    try:
        with gzip.open(caminho, "rt", encoding="utf-8") as f:
            for l in f:
                try:
                    yield json.loads(l)
                except json.JSONDecodeError:
                    return
    except (EOFError, zlib.error, OSError):
        return


def main():
    saida = sys.argv[1]
    mx = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else None
    desde = int(sys.argv[sys.argv.index("--desde") + 1]) if "--desde" in sys.argv else 0
    # em fluxo (o arquivo tem ids crescentes; repetido = fica o primeiro) — não carrega tudo na RAM
    ids, vistos = [], set()
    with gzip.open(saida + ".jsonl.gz", "wt", encoding="utf-8", compresslevel=6) as f:
        for r in ler_chunks(os.path.join(DADOS, "chunks.jsonl.gz")):
            i = r["id"]
            if i < desde or i in vistos:
                continue
            vistos.add(i); ids.append(i)
            t = os.path.splitext(os.path.basename(r["arq"]))[0]
            f.write(json.dumps({"id": i, "t": t, "text": r["text"]}, ensure_ascii=False) + "\n")
            if mx and len(ids) >= mx:
                break
    np.save(saida + ".ids.npy", np.array(ids, dtype=np.int64))
    print(f"{len(ids)} trechos -> {saida}.jsonl.gz ({os.path.getsize(saida + '.jsonl.gz')/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
