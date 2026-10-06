' Roda atualizar_noturno.cmd sem abrir janela (chamado pelo Agendador de Tarefas)
Set fso = CreateObject("Scripting.FileSystemObject")
pasta = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & pasta & "\atualizar_noturno.cmd""", 0, True
