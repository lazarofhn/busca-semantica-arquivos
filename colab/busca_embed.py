#!/usr/bin/env python3
"""
busca_embed.py — vetoriza os trechos com EmbeddingGemma 2 (só texto) numa GPU do Colab, AUTOMATIZADO pela CLI do Colab.
Retomável: cada fatia de 10k vai para o Drive assim que fica pronta; uma queda da VM perde no máximo uma fatia.
Entrada: jsonl.gz com {"id", "t" (título = nome do arquivo), "text"} — gerado por preparar_embed.py.
Saída: .npy float16 (N x 768), na ordem da entrada. Corte p/ 256d e renormalização: construir_indice.py vetores.
Uso (numa máquina Linux com a CLI do Colab e o rclone configurados):
    python3 busca_embed.py <entrada.jsonl.gz> <saida.npy> [GPU] [TAG] [BATCH]
Variáveis: COLAB_BIN (CLI do Colab), RCLONE_BIN, RCLONE_CONF, DRIVE_REMOTE (ex.: gdrive:busca).
ATENÇÃO: o rclone.conf (com o token do SEU Google Drive) é embutido no job enviado à SUA VM do Colab,
para ela ler/gravar no Drive. Não compartilhe o job gerado (/tmp/<tag>_job.py) nem o log.
Sem CLI? Use o notebook colab/embed_colab.ipynb (manual, pelo navegador) ou embed_local.py (GPU local).
"""
import base64
import os
import subprocess
import sys
import time

COLAB = os.environ.get("COLAB_BIN", "colab")
RCLONE = os.environ.get("RCLONE_BIN", "rclone")
RCLONE_CONF = os.environ.get("RCLONE_CONF", os.path.expanduser("~/.config/rclone/rclone.conf"))
DRIVE = os.environ.get("DRIVE_REMOTE", "gdrive:busca-embed")
SESSAO = "busca-emb"
RUN_TIMEOUT = int(os.environ.get("EMBED_RUN_TIMEOUT", "18000"))  # s por tentativa (vigia de inatividade)


def gerar_job(tag, b64, batch):
    return f'''import base64, json, gzip, os, subprocess, sys, time
os.environ["PYTORCH_CUDA_ALLOC_CONF"]="expandable_segments:True"
os.makedirs("/content/in", exist_ok=True); os.makedirs("/content/parts", exist_ok=True)
open("/content/rclone.conf","w").write(base64.b64decode({b64!r}).decode())
subprocess.run([sys.executable,"-m","pip","install","-q","-U","sentence-transformers>=6.1.0","transformers>=5.19.0"], check=True)
import numpy as np
if not os.path.exists("/usr/local/bin/rclone"):
    subprocess.run("curl -sL -o /tmp/rc.zip https://downloads.rclone.org/rclone-current-linux-amd64.zip && cd /tmp && python -c \\"import zipfile; zipfile.ZipFile('/tmp/rc.zip').extractall('/tmp')\\" && cp /tmp/rclone-*-linux-amd64/rclone /usr/local/bin/ && chmod +x /usr/local/bin/rclone", shell=True, check=True)
RC="rclone --config /content/rclone.conf"
PARTS="{DRIVE}/parts/{tag}"
subprocess.run(f"{{RC}} copy {DRIVE}/in/{tag}.jsonl.gz /content/in/", shell=True, check=True)
textos=[]
with gzip.open("/content/in/{tag}.jsonl.gz","rt",encoding="utf-8") as f:
    for line in f:
        r=json.loads(line)
        textos.append("title: "+(r.get("t") or "none")+" | text: "+r["text"])
N=len(textos); SLICE=10000; nsl=(N+SLICE-1)//SLICE
print(f"embedando {{N}} textos em {{nsl}} fatias...", flush=True)
r=subprocess.run(f"{{RC}} lsf {{PARTS}}/", shell=True, capture_output=True, text=True)
feitas=set(x.strip() for x in r.stdout.split() if x.strip())
print(f"parts ja no Drive: {{len(feitas)}}/{{nsl}} (resume)", flush=True)
need=[k for k in range(nsl) if f"part_{{k:03d}}.npy" not in feitas]
if need:
    import torch
    from sentence_transformers import SentenceTransformer
    dev="cuda" if torch.cuda.is_available() else "cpu"
    dt=torch.bfloat16 if (dev=="cuda" and torch.cuda.is_bf16_supported()) else torch.float32   # NUNCA float16 (NaN)
    m=None
    for att in range(5):
        try:
            m=SentenceTransformer("google/embeddinggemma-2", device=dev, model_kwargs={{"torch_dtype": dt}},
                                  config_kwargs={{"vision_config": None, "audio_config": None}}); break
        except Exception as e:
            print(f"[load {{att+1}}/5] falhou: {{repr(e)[:300]}}", flush=True); time.sleep(20)
    if m is None:
        print("MODEL_LOAD_FAIL", flush=True); sys.exit(1)
    m.max_seq_length=1024
    print(f"modelo carregado em {{dev}} ({{dt}}); {{len(need)}} fatias a fazer...", flush=True)
    for k in need:
        t0=time.time()
        sub=textos[k*SLICE:(k+1)*SLICE]
        emb=m.encode(sub, batch_size={batch}, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        emb=np.asarray(emb, dtype=np.float32)
        if not np.isfinite(emb).all():
            print("NAN_DETECTADO", flush=True); sys.exit(2)
        pf=f"/content/parts/part_{{k:03d}}.npy"; np.save(pf, emb.astype(np.float16))
        subprocess.run(f"{{RC}} copy {{pf}} {{PARTS}}/", shell=True, check=True)
        print(f"[fatia {{k+1}}/{{nsl}}] ok ({{min((k+1)*SLICE,N)}}/{{N}}) em {{time.time()-t0:.0f}}s", flush=True)
subprocess.run(f"{{RC}} copy {{PARTS}}/ /content/parts/", shell=True, check=True)
emb=np.concatenate([np.load(f"/content/parts/part_{{k:03d}}.npy") for k in range(nsl)], axis=0)
assert emb.shape[0]==N, f"concat {{emb.shape[0]}} != {{N}}"
np.save("/content/{tag}.npy", emb)
subprocess.run(f"{{RC}} copy /content/{tag}.npy {DRIVE}/out/", shell=True, check=True)
print("EMBED_OK", emb.shape, flush=True)
'''


def _lsf(caminho, nome):
    r = subprocess.run(f"{RCLONE} lsf {caminho}", shell=True, capture_output=True, text=True)
    return nome in r.stdout


def embed(entrada_gz, saida_npy, gpu="L4", batch=64, tag="buscav1"):
    b64 = base64.b64encode(open(RCLONE_CONF, "rb").read()).decode()
    if not _lsf(f"{DRIVE}/in/{tag}.jsonl.gz", f"{tag}.jsonl.gz"):
        print(f"[drive] subindo {entrada_gz}...", file=sys.stderr, flush=True)
        subprocess.run(f"{RCLONE} copyto -P {entrada_gz} {DRIVE}/in/{tag}.jsonl.gz", shell=True, check=True)
    jobf = f"/tmp/{tag}_job.py"
    with open(jobf, "w") as f:
        f.write(gerar_job(tag, b64, batch))

    gpus = [gpu] * 10 + ["A100"] * 2   # muitas tentativas: cada uma retoma das fatias salvas
    ok = False
    for tent in range(len(gpus)):
        if _lsf(f"{DRIVE}/out/{tag}.npy", f"{tag}.npy"):
            ok = True
            break
        g = gpus[min(tent, len(gpus) - 1)]
        print(f"[embed] colab run --gpu {g} (tentativa {tent+1}/{len(gpus)}, teto {RUN_TIMEOUT//60}min)...",
              file=sys.stderr, flush=True)
        try:
            # --timeout do colab = dead-man's switch do lado da VM; RUN_TIMEOUT = do lado do servidor
            subprocess.run(f"{COLAB} --auth=adc run -s {SESSAO} --gpu {g} --timeout {RUN_TIMEOUT} {jobf}",
                           shell=True, timeout=RUN_TIMEOUT + 300)
        except subprocess.TimeoutExpired:
            print(f"[embed] tentativa {tent+1} estourou — parando; fatias salvas retomam.", file=sys.stderr, flush=True)
            subprocess.run(f"{COLAB} --auth=adc stop -s {SESSAO} > /dev/null 2>&1", shell=True)
            time.sleep(120)  # esfriar: VM reciclada rápido vem envenenada
        if _lsf(f"{DRIVE}/out/{tag}.npy", f"{tag}.npy"):
            ok = True
            break
        time.sleep(60)
    subprocess.run(f"{COLAB} --auth=adc stop -s {SESSAO} > /dev/null 2>&1", shell=True)
    if not ok:
        raise RuntimeError("busca_embed: falhou apos todas as tentativas")
    subprocess.run(f"{RCLONE} copyto {DRIVE}/out/{tag}.npy {saida_npy}", shell=True, check=True)
    for alvo in (f"delete {DRIVE}/in/{tag}.jsonl.gz", f"delete {DRIVE}/out/{tag}.npy", f"purge {DRIVE}/parts/{tag}"):
        subprocess.run(f"{RCLONE} {alvo} > /dev/null 2>&1", shell=True)
    os.remove(jobf)
    print(f"busca_embed OK -> {saida_npy}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("uso: busca_embed.py <entrada.jsonl.gz> <saida.npy> [GPU] [TAG]")
    embed(sys.argv[1], sys.argv[2],
          gpu=sys.argv[3] if len(sys.argv) > 3 else "L4",
          tag=sys.argv[4] if len(sys.argv) > 4 else "buscav1",
          batch=int(sys.argv[5]) if len(sys.argv) > 5 else 64)
