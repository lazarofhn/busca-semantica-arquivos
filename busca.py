"""
busca.py — motor de busca híbrido (assunto + nome/caminho + palavra exata) sobre os seus arquivos.

Semântico: EmbeddingGemma 2 (só texto, CPU) gera o vetor da consulta; comparação por força bruta
           contra dados/vetores.f16 (memmap, 256d) — sem servidor, o Windows pagina sob demanda.
Palavra:   SQLite FTS5 (bm25), sem acento/caixa. Pega número de processo, nomes, termos raros.
Fusão:     Reciprocal Rank Fusion, depois agrupa por arquivo (melhor trecho de cada).
"""
import math
import os
import re
import sqlite3
import threading
import time

import numpy as np

from construir_indice import DB, DIM, PONTEIRO, vetores_atuais
from extrair import LEITORES, RAIZ, limpar

import config

_LOCAL = config.MODELO_LOCAL          # cópia só-texto (salvar_modelo_texto.py): carrega bem mais rápido
MODELO = _LOCAL if os.path.isdir(_LOCAL) else config.MODELO_HF
RRF_K = 60
CAND = 300           # candidatos de cada lado antes da fusão
# pastas de trabalho intermediário (cópias, revisões, apoio de ferramentas): aparecem, mas no fim da lista
REBAIXAR = config.PASTAS_REBAIXADAS
FATOR_REBAIXO = 0.3
PALAVRAS_VAZIAS = {"de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "em", "no", "na", "nos", "nas",
                   "para", "por", "com", "sobre", "um", "uma", "que", "pasta", "pastas"}


def _sem_acento(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def rebaixada(caminho_rel):
    return any(seg.lower() in REBAIXAR for seg in caminho_rel.split(os.sep)[:-1])


class Motor:
    def __init__(self, ocioso_min=15):
        self._modelo = None
        self._lock = threading.Lock()
        self._ultimo_uso = 0.0
        self.ocioso = ocioso_min * 60
        self._abrir_indice()
        threading.Thread(target=self._vigia, daemon=True).start()
        threading.Thread(target=self._get_modelo, daemon=True).start()   # pré-carrega (descarrega se ocioso)
        threading.Thread(target=self._pastas, daemon=True).start()       # lista de pastas p/ o modo Pastas

    # ---------- índice ----------
    def _abrir_indice(self):
        self._mtime_idx = os.path.getmtime(PONTEIRO) if os.path.exists(PONTEIRO) else 0
        vet, vids = vetores_atuais()
        self.ids = np.load(vids) if vids else np.empty(0, np.int64)
        self.vet = (np.memmap(vet, dtype=np.float16, mode="r", shape=(len(self.ids), DIM))
                    if len(self.ids) else None)

    def _recarregar_se_mudou(self):
        m = os.path.getmtime(PONTEIRO) if os.path.exists(PONTEIRO) else 0
        if m != self._mtime_idx:
            self._abrir_indice()

    def _db(self):
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    # ---------- modelo (carrega sob demanda, descarrega ocioso) ----------
    def _get_modelo(self):
        with self._lock:
            if self._modelo is None:
                import torch
                from sentence_transformers import SentenceTransformer
                torch.set_num_threads(max(1, (os.cpu_count() or 4)))
                kw = {} if MODELO == _LOCAL else {"config_kwargs": {"vision_config": None, "audio_config": None}}
                self._modelo = SentenceTransformer(MODELO, device="cpu", **kw)   # fp32: 0,17 s/consulta no i3
            self._ultimo_uso = time.time()
            return self._modelo

    def _vigia(self):
        while True:
            time.sleep(60)
            with self._lock:
                if self._modelo is not None and time.time() - self._ultimo_uso > self.ocioso:
                    self._modelo = None
                    import gc; gc.collect()

    def modelo_carregado(self):
        return self._modelo is not None

    def vetor_consulta(self, q):
        m = self._get_modelo()
        v = m.encode(q, prompt_name="SearchQuery", normalize_embeddings=True,
                     truncate_dim=DIM, convert_to_numpy=True)
        return np.asarray(v, dtype=np.float32)

    # ---------- buscas ----------
    def _semantica(self, q, n):
        if self.vet is None:
            return []
        qv = self.vetor_consulta(q)
        N, bloco = len(self.ids), 200_000
        melhores_s, melhores_i = np.empty(0, np.float32), np.empty(0, np.int64)
        for i in range(0, N, bloco):
            s = np.asarray(self.vet[i:i + bloco], dtype=np.float32) @ qv
            k = min(n, len(s))
            top = np.argpartition(-s, k - 1)[:k]
            melhores_s = np.concatenate([melhores_s, s[top]])
            melhores_i = np.concatenate([melhores_i, top + i])
        ordem = np.argsort(-melhores_s)[:n]
        return [(int(self.ids[melhores_i[j]]), float(melhores_s[j])) for j in ordem]

    @staticmethod
    def _termos_fts(q, prefixo=False):
        """Termos para o FTS5: frases entre aspas ficam juntas; "0801235-55.2019..." vira frase;
        palavras vazias (de, da, com…) saem. prefixo=True: palavra incompleta casa (protoc → protocolo)."""
        frases = re.findall(r'"([^"]+)"', q)
        resto = re.sub(r'"[^"]+"', " ", q)
        termos = []
        for f in frases:
            partes = re.findall(r"\w+", f)
            if partes:
                termos.append('"' + " ".join(partes) + '"')
        for tok in resto.split():
            partes = re.findall(r"\w+", tok)
            if not partes:
                continue
            if len(partes) > 1:                           # ex.: 0801235-55.2019.4.05.8300
                termos.append('"' + " ".join(partes) + '"')
            elif len(partes[0]) > 1 and _sem_acento(partes[0]) not in PALAVRAS_VAZIAS:
                termos.append('"' + partes[0] + '"' + ("*" if prefixo and len(partes[0]) > 2 else ""))
        return termos

    def _palavra(self, con, q, n):
        """Conteúdo: primeiro os trechos com TODAS as palavras; depois completa com os que têm alguma."""
        termos = self._termos_fts(q)
        if not termos:
            return []
        vistos, out = set(), []
        for expr in ([" AND ".join(termos)] + ([" OR ".join(termos)] if len(termos) > 1 else [])):
            try:
                rows = con.execute("SELECT rowid, bm25(fts) AS s FROM fts WHERE fts MATCH ? ORDER BY s LIMIT ?",
                                   (expr, n)).fetchall()
            except sqlite3.OperationalError:
                continue
            for r in rows:
                if r[0] not in vistos and len(out) < n:
                    vistos.add(r[0]); out.append((r[0], -r[1]))
        return out

    def _nome(self, con, q, n):
        """Nome e caminho do arquivo (estilo Everything): TODAS as palavras, aceitando palavra incompleta;
        nome vale 3x o caminho. Pega também arquivos sem texto (PDF escaneado, .doc, imagem)."""
        termos = self._termos_fts(q, prefixo=True)
        if not termos:
            return []
        try:
            rows = con.execute("SELECT arq, bm25(nomes, 3.0, 1.0, 0.0) AS s FROM nomes WHERE nomes MATCH ? "
                               "ORDER BY s LIMIT ?", (" AND ".join(termos), n)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r[0] for r in rows]

    def buscar(self, q, k=15, pasta=None, tipo=None, modo="hibrido", trechos_por_arq=2):
        """Devolve lista de arquivos: {arq, ext, score, trechos:[{id, pi, pf, text}]}.
        Fusão por ARQUIVO (RRF) de três listas: nome/caminho, conteúdo por palavra e (no híbrido) por assunto."""
        t0 = time.time()
        self._recarregar_se_mudou()
        con = self._db()
        sem = self._semantica(q, CAND) if modo in ("hibrido", "semantico") else []
        pal = self._palavra(con, q, CAND) if modo in ("hibrido", "palavra") else []
        nom = self._nome(con, q, CAND) if modo in ("hibrido", "palavra") else []
        ids = list({cid for cid, _ in sem} | {cid for cid, _ in pal})
        meta = {}
        for i in range(0, len(ids), 900):
            parte = ids[i:i + 900]
            for r in con.execute(f"SELECT id, arq, pi, pf, text FROM chunks WHERE id IN ({','.join('?' * len(parte))})", parte):
                meta[r["id"]] = r
        pasta_n = pasta.strip("\\/").lower() if pasta else None
        tipos = {("." + t.lower().lstrip(".")) for t in tipo.split(",")} if tipo else None

        def passa(arq):
            return ((not pasta_n or pasta_n in arq.lower()) and
                    (not tipos or os.path.splitext(arq)[1].lower() in tipos))

        por_arq = {}

        def grupo(arq):
            return por_arq.setdefault(arq, {"arq": arq, "ext": os.path.splitext(arq)[1].lower(),
                                            "score": 0.0, "trechos": [], "por": []})
        # listas de trechos → ordem de arquivos (cada arquivo entra na posição do seu melhor trecho)
        for nome_lista, lista, peso in (("assunto", sem, 1.0), ("palavra", pal, 1.0)):
            pos = 0
            for cid, _ in lista:
                r = meta.get(cid)
                if r is None or not passa(r["arq"]):
                    continue
                g = grupo(r["arq"])
                if nome_lista not in g["por"]:
                    g["por"].append(nome_lista)
                    g["score"] += peso / (RRF_K + pos + 1)
                    pos += 1
                if len(g["trechos"]) < trechos_por_arq and all(t["id"] != cid for t in g["trechos"]):
                    g["trechos"].append({"id": cid, "pi": r["pi"], "pf": r["pf"], "text": r["text"]})
        pos = 0
        for arq in nom:                                    # nome/caminho com todas as palavras: peso maior
            if not passa(arq):
                continue
            g = grupo(arq)
            g["por"].append("nome")
            g["score"] += 1.5 / (RRF_K + pos + 1)
            pos += 1
        # quem entrou só pelo nome ganha o 1º trecho do arquivo (ou fica sem trecho: PDF escaneado, .doc…)
        sem_trecho = [a for a, g in por_arq.items() if not g["trechos"]]
        for a in sem_trecho:
            r = con.execute("SELECT id, pi, pf, text FROM chunks WHERE arq=? ORDER BY id LIMIT 1", (a,)).fetchone()
            if r:
                por_arq[a]["trechos"].append({"id": r["id"], "pi": r["pi"], "pf": r["pf"], "text": r["text"]})
        for g in por_arq.values():
            if rebaixada(g["arq"]):
                g["score"] *= FATOR_REBAIXO
                g["rebaixado"] = True
        # mesmo documento em formatos diferentes (peça.docx + peça.pdf, mesma pasta): uma linha só
        res, vistos = [], {}
        for g in sorted(por_arq.values(), key=lambda g: -g["score"]):
            chave = os.path.splitext(g["arq"])[0].lower()
            if chave in vistos:
                vistos[chave].setdefault("tambem", []).append(g["ext"])
                continue
            vistos[chave] = g
            res.append(g)
        res = res[:k]
        for g in res:
            try:
                st = os.stat(os.path.join(RAIZ, g["arq"]))
                g["mtime"], g["size"] = int(st.st_mtime), st.st_size
            except OSError:
                g["mtime"], g["size"] = None, None
        con.close()
        return {"resultados": res, "ms": int((time.time() - t0) * 1000)}

    # ---------- busca de PASTAS ----------
    def _pastas(self):
        """Todas as pastas da raiz (cache de 10 min): [(rel, rel_sem_acento, nome_sem_acento)]."""
        agora = time.time()
        if getattr(self, "_cache_pastas", None) and agora - self._cache_pastas[0] < 600:
            return self._cache_pastas[1]
        from extrair import PULAR_DIRS
        out = []
        for d, subdirs, _ in os.walk(RAIZ):
            subdirs[:] = [s for s in subdirs if s not in PULAR_DIRS and not s.startswith(".")]
            rel = os.path.relpath(d, RAIZ)
            if rel != ".":
                out.append((rel, _sem_acento(rel), _sem_acento(os.path.basename(rel))))
        self._cache_pastas = (agora, out)
        return out

    def _tamanhos(self):
        """nº de arquivos indexados em cada subárvore (cache de 10 min)."""
        agora = time.time()
        if getattr(self, "_cache_tam", None) and agora - self._cache_tam[0] < 600:
            return self._cache_tam[1]
        tam = {}
        con = self._db()
        for (arq,) in con.execute("SELECT arq FROM arquivos"):
            d = os.path.dirname(arq)
            while d:
                tam[d] = tam.get(d, 0) + 1
                d = os.path.dirname(d)
        con.close()
        self._cache_tam = (agora, tam)
        return tam

    def buscar_pastas(self, q, k=10, pasta=None, com_conteudo=True):
        """Pastas que casam pelo NOME (peso maior) ou pelo CONTEÚDO dos arquivos (busca híbrida agregada).
        com_conteudo=False: só pelo nome (instantâneo, não precisa do modelo)."""
        t0 = time.time()
        termos = [t for t in re.findall(r"\w+", _sem_acento(q)) if t not in PALAVRAS_VAZIAS and len(t) > 1]
        filtro = _sem_acento(pasta.strip("\\/")) if pasta else None
        nome, conteudo, exemplos = {}, {}, {}
        # 1) nome: só pontua se algum termo casa com o NOME da própria pasta (2 por termo, começo de palavra);
        #    termos que só aparecem no caminho acima valem 0,5 — desempatam ("Parecer" de qual cliente),
        #    mas não fazem todas as subpastas de um cliente casarem pelo nome dele.
        if termos:
            for rel, rel_n, base_n in self._pastas():
                if filtro and filtro not in rel_n:
                    continue
                pal_base = set(re.findall(r"\w+", base_n))
                pal_cam = set(re.findall(r"\w+", rel_n))
                proprio = sum(2 for t in termos if any(p.startswith(t) for p in pal_base))
                if not proprio:
                    continue
                acima = sum(0.5 for t in termos if not any(p.startswith(t) for p in pal_base)
                            and any(p.startswith(t) for p in pal_cam))
                nome[rel] = (proprio + acima) / (2 * len(termos)) - 0.002 * rel.count(os.sep)  # empate: a mais rasa
        # 2) conteúdo = CONCENTRAÇÃO: soma dos acertos na subárvore ÷ (1 + log10 do nº de arquivos dela).
        #    O cliente que concentra os acertos ganha da subpasta pequena com 2-3 acertos, e a pasta gigante
        #    não ganha só por ser grande. Pastas de 1º nível (ex.: "Clientes", "Arquivo") ficam de fora.
        r = self.buscar(q, k=80, pasta=pasta, trechos_por_arq=1) if com_conteudo else {"resultados": []}
        tam = self._tamanhos()
        for g in r["resultados"]:
            d = os.path.dirname(g["arq"])
            direto = True
            while os.sep in d:
                conteudo[d] = conteudo.get(d, 0.0) + g["score"]
                if direto or len(exemplos.get(d, [])) < 3:
                    exemplos.setdefault(d, []).append(os.path.basename(g["arq"]))
                d, direto = os.path.dirname(d), False
        for d in conteudo:
            conteudo[d] /= 1 + math.log10(1 + tam.get(d, 1))
        mx_c = max(conteudo.values(), default=0) or 1
        final = {}
        for d in set(nome) | set(conteudo):
            s = nome.get(d, 0) + 0.8 * conteudo.get(d, 0) / mx_c
            if rebaixada(d + os.sep + "x"):
                s *= FATOR_REBAIXO
            final[d] = s
        res = []
        con = self._db()
        for d in sorted(final, key=final.get, reverse=True)[:k]:
            pref = d + os.sep        # substr exato (no LIKE, o "_" de "_apoio" seria curinga)
            n = con.execute("SELECT count(*) FROM arquivos WHERE substr(arq, 1, ?) = ?",
                            (len(pref), pref)).fetchone()[0]
            try:
                mtime = int(os.path.getmtime(os.path.join(RAIZ, d)))
            except OSError:
                mtime = None
            res.append({"pasta": d, "score": final[d], "n_arquivos": n, "mtime": mtime,
                        "pelo_nome": d in nome, "exemplos": exemplos.get(d, [])[:3]})
        con.close()
        return {"resultados": res, "ms": int((time.time() - t0) * 1000)}

    # ---------- leitura ----------
    @staticmethod
    def caminho(arq):
        p = os.path.normpath(os.path.join(RAIZ, arq))
        if not p.startswith(os.path.normpath(RAIZ) + os.sep):
            raise ValueError("caminho fora da pasta indexada")
        return p

    def ler(self, arq, pagina_ini=None, pagina_fim=None, max_chars=60_000):
        """Lê o texto direto do arquivo (sempre atualizado). Páginas só fazem sentido em PDF."""
        p = self.caminho(arq)
        ext = os.path.splitext(p)[1].lower()
        if ext not in LEITORES:
            raise ValueError(f"arquivo {ext} sem texto extraível — só dá para abrir no programa")
        pags = [limpar(t) for t in LEITORES[ext](p)]
        total = len(pags)
        a = max(1, pagina_ini or 1)
        b = min(total, pagina_fim or total)
        partes, tam = [], 0
        for n in range(a, b + 1):
            t = pags[n - 1]
            cab = f"\n\n--- página {n} ---\n" if ext == ".pdf" else ""
            if tam + len(t) > max_chars:
                dica = ("use pagina_ini/pagina_fim para continuar" if ext == ".pdf"
                        else "aumente max_caracteres para ler mais")
                partes.append(cab + t[:max_chars - tam] + f"\n[... cortado: {dica}]")
                b = n
                break
            partes.append(cab + t); tam += len(t)
        return {"arq": arq, "paginas_total": total, "pagina_ini": a, "pagina_fim": b, "texto": "".join(partes).strip()}

    def status(self):
        con = self._db()
        n_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
        n_arq = con.execute("SELECT count(*) FROM arquivos").fetchone()[0]
        escaneados = con.execute("SELECT count(*) FROM arquivos WHERE ext='.pdf' AND paginas>0 "
                                 "AND pag_sem_texto*2 > paginas").fetchone()[0]
        con.close()
        return {"arquivos": n_arq, "trechos": n_chunks, "vetores": int(len(self.ids)),
                "pdfs_escaneados_sem_ocr": escaneados, "raiz": RAIZ, "pasta": os.path.basename(RAIZ.rstrip("\\/")),
                "nome": config.NOME, "modelo_carregado": self.modelo_carregado()}
