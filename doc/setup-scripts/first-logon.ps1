Copy-Item "C:\ci\_vimrc" $home
New-Item $home"\Documents\WindowsPowerShell" -Force -ItemType Directory
Copy-Item "C:\ci\Microsoft.PowerShell_profile.ps1" $home"\Documents\WindowsPowerShell"
# without that powershell doesn't even load the profile
Set-ExecutionPolicy unrestricted
# logging off is faster, but rebooting also works if automatic login is 
# enabled, and the system requires a logged in user
#logoff
shutdown -r -t 0
