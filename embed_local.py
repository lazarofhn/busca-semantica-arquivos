"""
embed_local.py — vetoriza a entrada de preparar_embed.py NA PRÓPRIA MÁQUINA (GPU NVIDIA se houver; senão CPU).
Retomável: salva cada fatia em dados/partes_<nome>/ e pula as prontas se for interrompido.

Ritmo de referência (trechos de ~450 tokens):
  GPU A100 (Colab) ≈ 78/s · GPU L4 ≈ 35/s · CPU i3 de 4 núcleos ≈ 0,1/s (≈ 9,5 s por trecho!)
Ou seja: em CPU só compensa para bases pequenas (alguns milhares de trechos). Para dezenas/centenas
de milhares, use uma GPU — local ou a do Colab (colab/embed_colab.ipynb).

Uso:  python embed_local.py dados/embed_in            (lê dados/embed_in.jsonl.gz → grava dados/embed_in.npy)
Depois: python construir_indice.py vetores dados/embed_in.npy dados/embed_in.ids.npy
"""
import gzip
import json
import os
import sys
import time

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

import config

FATIA = 2000


def main():
    base = sys.argv[1]
    textos = []
    with gzip.open(base + ".jsonl.gz", "rt", encoding="utf-8") as f:
        for l in f:
            r = json.loads(l)
            textos.append("title: " + (r.get("t") or "none") + " | text: " + r["text"])
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    # NUNCA float16: o EmbeddingGemma 2 estoura a faixa e gera NaN. bf16 na GPU, fp32 na CPU.
    dt = torch.bfloat16 if (dev == "cuda" and torch.cuda.is_bf16_supported()) else torch.float32
    origem = config.MODELO_LOCAL if os.path.isdir(config.MODELO_LOCAL) else config.MODELO_HF
    kw = {} if origem == config.MODELO_LOCAL else {"config_kwargs": {"vision_config": None, "audio_config": None}}
    m = SentenceTransformer(origem, device=dev, model_kwargs={"torch_dtype": dt}, **kw)
    m.max_seq_length = 1024
    pasta = os.path.join(os.path.dirname(base) or ".", "partes_" + os.path.basename(base))
    os.makedirs(pasta, exist_ok=True)
    n = len(textos)
    nf = (n + FATIA - 1) // FATIA
    print(f"{n} trechos em {nf} fatias · {dev} ({dt})", flush=True)
    t0 = time.time()
    for k in range(nf):
        arq = os.path.join(pasta, f"parte_{k:04d}.npy")
        if os.path.exists(arq):
            continue
        e = m.encode(textos[k * FATIA:(k + 1) * FATIA], batch_size=64 if dev == "cuda" else 4,
                     normalize_embeddings=True, convert_to_numpy=True)
        e = np.asarray(e, dtype=np.float32)
        if not np.isfinite(e).all():
            raise SystemExit("NaN nos vetores — confira se não está em float16")
        np.save(arq, e.astype(np.float16))
        feitos = min((k + 1) * FATIA, n)
        print(f"[{k + 1}/{nf}] {feitos}/{n} · {(time.time() - t0) / 60:.1f} min", flush=True)
    emb = np.concatenate([np.load(os.path.join(pasta, f"parte_{k:04d}.npy")) for k in range(nf)])
    np.save(base + ".npy", emb)
    print(f"OK: {emb.shape} → {base}.npy", flush=True)


if __name__ == "__main__":
    main()
