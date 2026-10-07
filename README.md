# Busca semântica nos seus arquivos (Windows + Claude)

Busca local nos seus documentos — PDF, Word, Excel, Markdown e texto — **por assunto**, **por nome/caminho** e
**por palavra exata**, ao mesmo tempo. Você descreve o que procura ("parecer sobre imunidade de empresa portuária",
"contrato com cláusula de não concorrência") e ela acha o documento mesmo que ele use outras palavras.

São três formas de usar a mesma base:

| | O que é | Como abre |
|---|---|---|
| **Barra estilo Spotlight** | caixa flutuante, resultados enquanto digita, Enter abre o arquivo | **Alt+Espaço** de qualquer lugar do Windows |
| **Tela completa** | página local com filtros, leitura de trechos e busca de pastas | atalho **Ctrl+Alt+B** (abre no navegador) |
| **Conector MCP para o Claude** | o Claude (Desktop ou Code) pesquisa e lê seus arquivos durante a conversa | ferramentas `buscar_arquivos`, `buscar_pastas`, `ler_arquivo`, `status_busca` |

Tudo roda **no seu computador**: o índice, o modelo que transforma a consulta em vetor e o servidor (que só escuta
em `127.0.0.1`). A única etapa que pode sair da máquina é a geração inicial dos vetores, se você optar pelo Google
Colab (veja [Privacidade](#privacidade-e-segurança)).

O modelo de embeddings é o **[EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2)** (Google, Apache
2.0), usando só o módulo de texto (270M de parâmetros), com vetores cortados para 256 dimensões (Matryoshka).

---

## Como funciona

```
 sua pasta de documentos
        │  extrair.py        (PDF/DOCX/XLSX/MD/TXT → trechos de ~1.500 caracteres; pula SPED/logs; detecta duplicatas)
        ▼
 dados/chunks.jsonl.gz ──► construir_indice.py texto ──► dados/indice.db (SQLite: texto + FTS5 + índice de NOMES)
        │
        │  preparar_embed.py → embed_in.jsonl.gz
        ▼
 vetores 768d ◄── GPU: Google Colab (notebook ou CLI)  ou  embed_local.py (GPU/CPU local)
        │
        └─► construir_indice.py vetores ──► dados/vetores_*.f16 (256d, float16, lido do disco sob demanda)

 servidor.py (127.0.0.1:8765) ◄── barra.py (Alt+Espaço) · ui.html (navegador) · mcp_busca.py (Claude)
 atualizar.py (toda madrugada): arquivos novos/alterados/apagados → texto na hora, vetores na CPU com limite de tempo
```

**Como a busca ordena os resultados** (`busca.py`): três listas são combinadas por *Reciprocal Rank Fusion*,
arquivo a arquivo:

1. **Nome e caminho** (FTS5, estilo *Everything*): todas as palavras, aceitando palavra incompleta; o nome do
   arquivo pesa 3× o nome das pastas. Acha também arquivos **sem texto** (PDF escaneado, `.doc`, imagens).
2. **Conteúdo por palavra** (FTS5/bm25): primeiro os trechos com *todas* as palavras, depois os com alguma.
   Números de processo, CNPJ e termos entre aspas funcionam como frase exata.
3. **Assunto** (vetores): a consulta vira vetor com o prompt `task: search result | query: …` e é comparada com
   todos os trechos (força bruta em blocos — 466 mil trechos levam ~1 s num i3).

Depois: o mesmo documento em formatos diferentes na mesma pasta (`peça.docx` + `peça.pdf`) vira uma linha só, e
pastas de trabalho intermediário (configuráveis, ex.: `_apoio`, `tmp`) vão para o fim da lista.

No **modo Pastas**, o resultado são pastas: pontuam pelo **nome da própria pasta** e pela **concentração** de
arquivos sobre o assunto dentro dela (soma dos acertos ÷ log do tamanho da pasta).

---

## Requisitos

- Windows 10/11 (a barra e os atalhos usam APIs do Windows; o motor e o MCP são Python puro)
- Python 3.11+ (testado no 3.14)
- Espaço em disco: ~0,6 GB do modelo só-texto + o índice. Referência: 28,7 mil arquivos (31 GB) → 466 mil trechos
  → `indice.db` de 1,3 GB + vetores de 240 MB.
- RAM: o modelo ocupa ~1,1 GB **só enquanto há buscas** (sai da memória após 15 min parado); a barra, ~40 MB.
- Para gerar os vetores da base inteira: uma GPU (a do Google Colab serve — foi o que eu usei).

---

## Instalação

```powershell
git clone https://github.com/lazarofhn/busca-semantica-arquivos.git
cd busca-semantica-arquivos
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.exemplo.json config.json
notepad config.json        # ponha em "raiz" a pasta que quer indexar
```

`config.json` (fica só na sua máquina; está no `.gitignore`):

| chave | o que é |
|---|---|
| `raiz` | pasta a indexar (obrigatório) |
| `nome` / `descricao_mcp` | nome exibido e a descrição que o Claude lê sobre o seu acervo |
| `porta` | porta local do serviço (padrão 8765) |
| `pastas_rebaixadas` | pastas cujos arquivos vão para o fim da lista (cópias de trabalho, rascunhos) |
| `pastas_ignoradas` | pastas que nunca são varridas (além de `.venv`, `node_modules`, `.git`…) |
| `orcamento_cpu_min` | minutos de CPU por noite para vetorizar arquivos novos |

### 1. Extrair o texto (local)

```powershell
.\.venv\Scripts\python.exe extrair.py              # retomável; ~15 min para 28 mil arquivos num i3
.\.venv\Scripts\python.exe construir_indice.py texto   # SQLite + busca por palavra + índice de nomes
```

A partir daqui a busca **por palavra e por nome já funciona**. Falta a busca por assunto (vetores).

### 2. Gerar os vetores — escolha uma opção

```powershell
.\.venv\Scripts\python.exe preparar_embed.py dados/embed_in   # → dados/embed_in.jsonl.gz + dados/embed_in.ids.npy
```

**A) Google Colab pelo navegador (o que eu usei)** — abra [`colab/embed_colab.ipynb`](colab/embed_colab.ipynb) no
Colab, suba `embed_in.jsonl.gz` para `MyDrive/busca-embed/`, escolha GPU L4 ou A100 e execute tudo. Baixe o
`embed_in.npy` gerado para a pasta `dados/`. Referência real: **466 mil trechos em ~1h35 numa A100** (78 trechos/s);
numa L4, ~35 trechos/s (~3h45). O notebook salva cada fatia no Drive: se a sessão cair, rode de novo e ele continua.

**B) Google Colab automatizado** — [`colab/busca_embed.py`](colab/busca_embed.py) faz o mesmo sem abrir o navegador,
usando a [CLI do Colab](https://github.com/googlecolab/google-colab-cli) + `rclone` numa máquina Linux (eu rodo de um
servidor caseiro). Mais prático para refazer a base de tempos em tempos; leia o aviso sobre o token do Drive no topo
do arquivo.

**C) Na sua máquina** — `.\.venv\Scripts\python.exe embed_local.py dados/embed_in`. Com GPU NVIDIA (instale o torch
com CUDA) é o caminho mais simples e mais privado. **Em CPU funciona, mas é lento**: ~9,5 s por trecho num i3 de 4
núcleos — só vale para bases pequenas (alguns milhares de trechos).

Depois de qualquer opção:

```powershell
.\.venv\Scripts\python.exe construir_indice.py vetores dados/embed_in.npy dados/embed_in.ids.npy
.\.venv\Scripts\python.exe salvar_modelo_texto.py   # cópia só-texto do modelo (0,57 GB): carrega em ~14 s em vez de ~53 s
```

### 3. Integrar ao Windows

```powershell
powershell -ExecutionPolicy Bypass -File windows\instalar_atalhos.ps1
```

Cria o atalho da tela completa (Área de Trabalho + Iniciar, **Ctrl+Alt+B**), coloca a **barra (Alt+Espaço)** para
iniciar com o Windows e agenda a **atualização diária às 03:00** (acorda o PC; se ele estiver desligado, roda ao
ligar). Opções: `-SemTarefa`, `-SemInicializar`, `-Hora 02:30`.

O serviço de busca **não** fica residente: a barra, a tela e o MCP o sobem quando precisam.

**Se o Alt+Espaço parar de responder**, a barra foi encerrada: procure **"Barra de busca"** no Iniciar para religá-la.
O registro dela fica em `dados/barra.log` (início, atalho obtido e erros). Se outro programa já usar Alt+Espaço, ela
fica com **Ctrl+Alt+Espaço** — o log diz qual. Armadilha: se você iniciar a barra **de dentro de um terminal ou de um
agente** (Claude Code, por exemplo), ela morre quando esse programa fecha; inicie pelo atalho, pela pasta Inicializar
ou de forma desvinculada (`Invoke-CimMethod Win32_Process -MethodName Create`).

### 4. Conectar ao Claude

**Claude Code**

```powershell
claude mcp add busca-arquivos -s user -- "$PWD\.venv\Scripts\python.exe" -X utf8 "$PWD\mcp_busca.py"
```

**Claude Desktop**

```powershell
powershell -ExecutionPolicy Bypass -File windows\registrar_claude_desktop.ps1
```

e feche o Claude Desktop pela bandeja (botão direito → Sair) — o script grava a configuração e reabre o app.
Confira em *Configurações → Desenvolvedor*.

> **Por que um script e não editar o JSON?** O Claude Desktop guarda o `claude_desktop_config.json` na memória e o
> **regrava ao sair**: uma entrada adicionada com o app aberto some quando ele fecha. Além disso, na versão da
> Microsoft Store o arquivo não fica em `%APPDATA%\Claude`, e sim em
> `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\`. O script cuida das duas coisas. Se preferir
> fazer à mão: feche o app, adicione em `mcpServers`
> `"busca-arquivos": {"command": "<projeto>\\.venv\\Scripts\\python.exe", "args": ["-X", "utf8", "<projeto>\\mcp_busca.py"]}`
> e abra de novo.

O conector não carrega o modelo: ele repassa as chamadas ao serviço local e o sobe se estiver fora do ar.

---

## Uso

**Barra (Alt+Espaço)** — ↑↓ escolher · **Enter** abrir · **Ctrl+Enter** mostrar na pasta · **Tab** alterna
Arquivos/Pastas · **Ctrl+O** leva a busca para a tela completa · **Esc** fecha. Filtros dentro da consulta:
`tipo:pdf`, `tipo:docx,xlsx`, `pasta:Contratos`, `pasta:"Cliente X"`. Os resultados por nome/palavra aparecem quase na
hora; os por assunto completam a lista um instante depois.

**Tela completa** — modos Híbrido / Por assunto / Palavra exata, filtros por tipo e pasta, "Ler trecho" (abre as
páginas em volta do trecho), "Buscar dentro" de uma pasta.

**Claude** — "ache o parecer que fiz sobre X e use de base", "onde está a pasta do cliente Y?", "leia as páginas
do comprovante de protocolo". Em PDFs grandes o `ler_arquivo` lê por página.

**Atualização** — `atualizar.py` (o que a tarefa agendada roda): reprocessa arquivos novos e alterados, remove os
apagados, refaz o índice de nomes e vetoriza os trechos novos na CPU, dos mais recentes para os mais antigos, até o
limite de tempo. Texto novo fica pesquisável por palavra na hora; o que não couber no tempo vetoriza nas noites
seguintes (ou rode a opção A/B de novo para um lote grande). Log em `dados/atualizar.log`.

---

## Aprendizados e armadilhas

**EmbeddingGemma 2**
- **Nunca use float16** — estoura a faixa numérica e gera NaN (ou degrada calado). bf16 na GPU, fp32 na CPU. A T4
  não tem bf16 nativo: use fp32 nela.
- É **mais lento do que o tamanho sugere** na GPU: a camada de *per-layer embeddings* (herdada do Gemma 4) roda em
  fp32 e consome muita memória. Numa L4, lote de 128 trechos estoura os 22 GB; lote 64 dá ~35 trechos/s. Trocar
  `eager` por `sdpa` não muda nada. A A100 rende o dobro.
- Carregue **só o texto**: `config_kwargs={"vision_config": None, "audio_config": None}`. Salvar essa cópia
  (`salvar_modelo_texto.py`) derruba o tempo de carga na CPU de ~53 s para ~14 s.
- Na CPU, **fp32 é 4× mais rápido que bf16** em processadores sem bf16 nativo (0,17 s × 0,69 s por consulta num i3).
- Prompts: consulta com `prompt_name="SearchQuery"`; documento como `title: <nome do arquivo> | text: <trecho>`.
  Corte para 256d e **renormalize** depois de cortar.

**Windows**
- O `http.server` do Python liga `SO_REUSEADDR`, que **no Windows deixa vários processos na mesma porta** — eu
  cheguei a ter 11 servidores (cada um podendo carregar o modelo). O servidor aqui usa `SO_EXCLUSIVEADDRUSE` e
  ocupa a porta *antes* de carregar o modelo; os clientes testam se a porta está ocupada antes de subir outro.
- Scripts `.ps1` com acentos precisam ser salvos em **UTF-8 com BOM** — o PowerShell 5.1 lê arquivo sem BOM como
  ANSI e corrompe caminhos como `C:\Users\JOÃO`.
- O `openpyxl` quebra em muitas planilhas reais (estilos de borda); o `python-calamine` lê sem problema e mais rápido.
- Arquivos SPED/ECF e logs (`.txt` cheios de `|campo|campo|`) geram milhares de trechos inúteis: o extrator os
  detecta e pula.

---

## Privacidade e segurança

- Nada sai do computador no uso normal: índice, vetores, modelo e servidor são locais; o servidor só escuta em
  `127.0.0.1`, exige um cabeçalho próprio nas chamadas (um site aberto no navegador não consegue usá-lo) e confere o
  `Host` (contra *DNS rebinding*).
- **Se usar o Colab**, o texto dos seus documentos passa pelo seu Google Drive e pela sua VM do Colab durante a
  geração dos vetores. Para material sensível, use `embed_local.py`.
- `config.json`, `dados/` (índice, vetores, textos extraídos) e `modelo_texto/` estão no `.gitignore` — **não os
  publique**: o índice contém o texto dos seus documentos.
- O conector dá ao Claude acesso de **leitura** aos arquivos da pasta indexada. Indexe só o que você quer que ele
  possa ler.

## Limitações

- PDFs escaneados sem camada de texto são achados **só pelo nome** (falta OCR).
- `.doc` (Word antigo) e imagens: só pelo nome.
- A barra e os atalhos são específicos do Windows; o motor (`busca.py`), o servidor e o MCP rodam em qualquer SO.

## Arquivos

| arquivo | papel |
|---|---|
| `config.py`, `config.exemplo.json` | configuração |
| `extrair.py` | varre a pasta, extrai texto e corta em trechos (retomável) |
| `preparar_embed.py` | monta a entrada para vetorização |
| `colab/embed_colab.ipynb`, `colab/busca_embed.py`, `embed_local.py` | geração dos vetores (Colab manual, Colab automatizado, local) |
| `construir_indice.py` | SQLite (texto, FTS5, nomes) e vetores versionados |
| `salvar_modelo_texto.py` | cópia só-texto do modelo para consultas rápidas na CPU |
| `busca.py` | motor: fusão nome + palavra + assunto, modo Pastas, leitura |
| `servidor.py`, `ui.html` | serviço local e tela completa |
| `barra.py`, `abrir_busca.py` | barra Alt+Espaço e atalho da tela completa |
| `mcp_busca.py` | conector MCP para o Claude |
| `atualizar.py`, `atualizar_noturno.cmd/.vbs` | atualização incremental (tarefa agendada) |
| `windows/instalar_atalhos.ps1`, `windows/registrar_claude_desktop.ps1` | integração com o Windows e o Claude Desktop |
| `teste_api.py`, `teste_mcp.py` | testes rápidos do serviço e do conector |

Construído com o [Claude Code](https://claude.com/claude-code). Licença MIT.
