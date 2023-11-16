powershell.exe -ExecutionPolicy Bypass -File C:\ci\setup-ssh-keys.ps1
REM for whatever reason USERS only get dropped on second run
REM instead of spending more time on debugging this just call it twice for now
powershell.exe -ExecutionPolicy Bypass -File C:\ci\setup-ssh-keys.ps1
powershell.exe -ExecutionPolicy Bypass -File C:\ci\setup-data-disk.ps1
powershell.exe -ExecutionPolicy Bypass -File C:\ci\get-control-script.ps1
