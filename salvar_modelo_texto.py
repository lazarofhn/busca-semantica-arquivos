"""Salva cópia local só-texto do EmbeddingGemma 2 (sem visão/áudio) em modelo_texto/ — carrega mais rápido."""
import os, time
import torch
from sentence_transformers import SentenceTransformer

import config

DEST = config.MODELO_LOCAL
m = SentenceTransformer(config.MODELO_HF, device="cpu",
                        config_kwargs={"vision_config": None, "audio_config": None})
print("params:", sum(p.numel() for p in m.parameters()) / 1e6, "M")
m.save(DEST, safe_serialization=True)
tam = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(DEST) for f in fs)
print(f"salvo em {DEST}: {tam/1e9:.2f} GB")
q = "exclusão do ISS da base de cálculo do PIS"
ref = m.encode(q, prompt_name="SearchQuery", normalize_embeddings=True)
del m
for dt in (torch.float32, torch.bfloat16):
    t = time.time()
    m2 = SentenceTransformer(DEST, device="cpu", model_kwargs={"torch_dtype": dt})
    carga = time.time() - t
    m2.encode(q, prompt_name="SearchQuery")
    t = time.time()
    for _ in range(3):
        v = m2.encode(q, prompt_name="SearchQuery", normalize_embeddings=True)
    print(f"{dt}: carga {carga:.1f}s | encode {(time.time()-t)/3:.2f}s | cos vs ref {float((v*ref).sum()):.4f}")
    del m2
