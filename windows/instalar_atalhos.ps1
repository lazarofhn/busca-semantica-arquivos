# Integra a busca ao Windows:
#   1. atalho "Busca nos Arquivos" na Área de Trabalho e no Iniciar (tecla Ctrl+Alt+B) → tela completa no navegador,
#      e "Barra de busca (Alt+Espaço)" no Iniciar, para religar a barra à mão
#   2. barra estilo Spotlight (Alt+Espaço) iniciando junto com o Windows (pasta Inicializar)
#   3. tarefa agendada que atualiza o índice toda madrugada (03:00; acorda o PC; roda ao ligar se perdeu o horário)
# Uso (PowerShell, na pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File windows\instalar_atalhos.ps1 [-SemTarefa] [-SemInicializar] [-Hora 03:00]
# Para desfazer: apague os atalhos e rode  Unregister-ScheduledTask -TaskName "Busca nos Arquivos - atualizacao noturna"
param([switch]$SemTarefa, [switch]$SemInicializar, [string]$Hora = "03:00")

$projeto = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $projeto ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { throw "Não achei $pyw — crie o venv do projeto antes (veja o README)." }
$ws = New-Object -ComObject WScript.Shell
$icone = "$env:WINDIR\System32\imageres.dll,168"

function Atalho($destino, $script, $tecla) {
    $s = $ws.CreateShortcut($destino)
    $s.TargetPath = $pyw
    $s.Arguments = "`"$(Join-Path $projeto $script)`""
    $s.WorkingDirectory = $projeto
    $s.IconLocation = $icone
    if ($tecla) { $s.Hotkey = $tecla }
    $s.Save()
    Write-Host "atalho: $destino"
}

Atalho (Join-Path ([Environment]::GetFolderPath("Desktop")) "Busca nos Arquivos.lnk") "abrir_busca.py" $null
Atalho (Join-Path ([Environment]::GetFolderPath("Programs")) "Busca nos Arquivos.lnk") "abrir_busca.py" "CTRL+ALT+B"
# para religar a barra à mão, se um dia ela parar (procure "Barra de busca" no Iniciar)
Atalho (Join-Path ([Environment]::GetFolderPath("Programs")) "Barra de busca (Alt+Espaço).lnk") "barra.py" $null
if (-not $SemInicializar) {
    $ini = Join-Path ([Environment]::GetFolderPath("Startup")) "Busca nos Arquivos - barra.lnk"
    Atalho $ini "barra.py" $null
    Start-Process $ini        # já deixa a barra rodando
}
if (-not $SemTarefa) {
    $acao = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$(Join-Path $projeto 'atualizar_noturno.vbs')`"" -WorkingDirectory $projeto
    $gatilho = New-ScheduledTaskTrigger -Daily -At $Hora
    $cfg = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
           -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $quem = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName "Busca nos Arquivos - atualizacao noturna" -Action $acao -Trigger $gatilho `
        -Settings $cfg -Principal $quem -Description "Indexa arquivos novos/alterados ($projeto\atualizar.py)" -Force | Out-Null
    Write-Host "tarefa agendada: todo dia às $Hora (log em dados\atualizar.log)"
}
