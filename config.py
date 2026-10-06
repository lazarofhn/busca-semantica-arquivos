"""
config.py — configuração local da busca. Lê config.json (ao lado deste arquivo, ou o caminho em BUSCA_CONFIG);
variáveis de ambiente têm prioridade. Copie config.exemplo.json para config.json e ajuste.
"""
import json
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
_arq = os.environ.get("BUSCA_CONFIG", os.path.join(AQUI, "config.json"))
_cfg = json.load(open(_arq, encoding="utf-8")) if os.path.exists(_arq) else {}


def _get(chave, env, padrao=None):
    return os.environ.get(env) or _cfg.get(chave, padrao)


# pasta que será indexada (obrigatório)
RAIZ = _get("raiz", "BUSCA_RAIZ")
# onde ficam índice, vetores e logs
DADOS = _get("dados", "BUSCA_DADOS", os.path.join(AQUI, "dados"))
# porta do serviço local (só escuta em 127.0.0.1)
PORTA = int(_get("porta", "BUSCA_PORTA", 8765))
# nome exibido na interface e descrição que o Claude lê no conector MCP
NOME = _cfg.get("nome", "Busca nos Arquivos")
DESCRICAO_MCP = _cfg.get("descricao_mcp",
                         "Busca nos arquivos locais do usuário (documentos de trabalho: PDFs, Word, Excel, Markdown).")
# pastas de trabalho intermediário: aparecem, mas no fim da lista
PASTAS_REBAIXADAS = {p.lower() for p in _cfg.get("pastas_rebaixadas", ["_apoio", "_aux", "_equipe", "tmp"])}
# pastas nunca varridas
PASTAS_IGNORADAS = set(_cfg.get("pastas_ignoradas", [])) | {
    "venv", ".venv", "node_modules", "__pycache__", ".git", "site-packages", "_to_delete"}
# modelo de embeddings (Hugging Face) e cópia local só-texto (salvar_modelo_texto.py)
MODELO_HF = _cfg.get("modelo", "google/embeddinggemma-2")
MODELO_LOCAL = _cfg.get("modelo_local", os.path.join(AQUI, "modelo_texto"))
# tempo máximo de CPU por noite para vetorizar trechos novos (atualizar.py)
ORCAMENTO_MIN = int(_get("orcamento_cpu_min", "BUSCA_ORCAMENTO_MIN", 150))


def exigir_raiz():
    if not RAIZ or not os.path.isdir(RAIZ):
        raise SystemExit("Configure a pasta a indexar: copie config.exemplo.json para config.json e preencha "
                         "\"raiz\" (ou defina a variável de ambiente BUSCA_RAIZ).")
    return RAIZ
