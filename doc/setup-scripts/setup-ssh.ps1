Expand-Archive "C:\ci\OpenSSH-Win64.zip" "C:\Program Files\" -Force
New-Item "C:\Program Files\OpenSSH" -ItemType Directory -Force
Copy-Item "C:\Program Files\OpenSSH-Win64\*" "C:\Program Files\OpenSSH"
Copy-Item "C:\ci\sshd_config" "C:\Program Files\OpenSSH\sshd_config_default"

powershell.exe -ExecutionPolicy Bypass -File "C:\Program Files\OpenSSH\install-sshd.ps1"

# windows adds duplicate rules -> kill rules first just to be safe
netsh advfirewall firewall del rule name=sshd
netsh advfirewall firewall add rule name=sshd dir=in action=allow protocol=TCP localport=22

net start sshd
Set-Service sshd -StartupType Automatic

# set default shell to powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShellCommandOption -Value "/c" -PropertyType String -Force
