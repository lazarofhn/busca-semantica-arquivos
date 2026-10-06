# Registra o conector MCP "busca-arquivos" no Claude Desktop (Windows).
#
# Por que este script existe: o Claude Desktop guarda o claude_desktop_config.json na memória e o REGRAVA AO
# SAIR — uma entrada adicionada com o app aberto some quando ele fecha. Então o script espera o app fechar,
# grava a entrada (com backup) e reabre o Claude.
#
# Uso (PowerShell, na pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File windows\registrar_claude_desktop.ps1
# Depois feche o Claude Desktop pela bandeja (botão direito → Sair). Ele reabre sozinho já com o conector.
param([int]$MinutosEspera = 15)

$projeto = Split-Path -Parent $PSScriptRoot
$python  = Join-Path $projeto ".venv\Scripts\python.exe"
$mcp     = Join-Path $projeto "mcp_busca.py"
if (-not (Test-Path $python)) { throw "Não achei $python — crie o venv do projeto antes (veja o README)." }

# Instalação pela Microsoft Store (MSIX) guarda a config num Roaming virtualizado; a instalação comum, em %APPDATA%.
$pacote = Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pacote) {
    $cfg = Join-Path $pacote.FullName "LocalCache\Roaming\Claude\claude_desktop_config.json"
    $appId = "$($pacote.Name)!Claude"
} else {
    $cfg = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
    $appId = $null
}
Write-Host "config do Claude Desktop: $cfg"

Write-Host "Agora feche o Claude Desktop pela bandeja (botão direito no ícone → Sair). Aguardando até $MinutosEspera min..."
$limite = (Get-Date).AddMinutes($MinutosEspera)
do {
    $abertos = @(Get-CimInstance Win32_Process -Filter "Name='claude.exe'" |
                 Where-Object { $_.ExecutablePath -like "*\WindowsApps\Claude_*" -or $_.ExecutablePath -like "*\AnthropicClaude\*" })
    if ($abertos.Count -eq 0) { break }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $limite)
if ($abertos.Count -gt 0) { throw "O Claude Desktop não foi fechado a tempo. Nada foi alterado." }
Start-Sleep -Seconds 3   # deixa o app terminar de gravar a config

$py = @"
import json, os, shutil, sys
p, python, mcp = sys.argv[1], sys.argv[2], sys.argv[3]
d = {}
if os.path.exists(p):
    shutil.copy2(p, p + ".bak_busca_arquivos")
    d = json.load(open(p, encoding="utf-8"))
else:
    os.makedirs(os.path.dirname(p), exist_ok=True)
d.setdefault("mcpServers", {})["busca-arquivos"] = {"command": python, "args": ["-X", "utf8", mcp]}
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("conectores:", ", ".join(sorted(d["mcpServers"])))
"@
$tmp = Join-Path $env:TEMP "registrar_busca_desktop.py"
[IO.File]::WriteAllText($tmp, $py, (New-Object Text.UTF8Encoding $false))
& $python -X utf8 $tmp $cfg $python $mcp
Remove-Item $tmp -ErrorAction SilentlyContinue

if ($appId) { Start-Process "shell:AppsFolder\$appId" }
else { $exe = Join-Path $env:LOCALAPPDATA "AnthropicClaude\claude.exe"; if (Test-Path $exe) { Start-Process $exe } }
Write-Host "Pronto. Confira em Configurações → Desenvolvedor do Claude Desktop."
