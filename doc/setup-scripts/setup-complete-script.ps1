New-Item "C:\ci\logs" -ItemType Directory -Force
New-Item "C:\Windows\Setup\Scripts" -ItemType Directory -Force
Copy-Item "C:\ci\SetupComplete.cmd" "C:\Windows\Setup\Scripts\"