"""Teste rápido da API: python teste_api.py pastas|arquivos "consulta" ..."""
import json, sys, time, urllib.parse, urllib.request

def api(r, **p):
    req = urllib.request.Request("http://127.0.0.1:8765" + r + "?" + urllib.parse.urlencode(p), headers={"X-Busca": "1"})
    return json.loads(urllib.request.urlopen(req, timeout=240).read())

while not api("/api/status")["modelo_carregado"]:
    time.sleep(2)
alvo = sys.argv[1]
for q in sys.argv[2:]:
    if alvo == "pastas":
        r = api("/api/pastas", q=q, k=5)
        print(f"\n### {q}  ({r['ms']} ms)")
        for g in r["resultados"]:
            print(f"  {g['score']:.2f} {'nome' if g['pelo_nome'] else 'cont'} | {g['pasta'][-75:]}")
    else:
        r = api("/api/buscar", q=q, k=6)
        print(f"\n### {q}  ({r['ms']} ms)")
        for g in r["resultados"]:
            print(f"  {g['score']:.4f} | {g['arq'][-95:]}")
