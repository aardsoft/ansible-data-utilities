$ErrorActionPreference="SilentlyContinue"
Stop-Transcript | out-null
$ErrorActionPreference = "Continue"
Start-Transcript -path C:\ci\logs\ssh-key-setup.log -append

$key_dir = "C:\ProgramData\ssh_keys\"
$users = @("build", "management")

New-Item $key_dir"build\" -ItemType Directory -Force

$user_account = New-Object System.Security.Principal.NTAccount("BUILTIN\USERS")
$user_rights = [System.Security.AccessControl.FileSystemRights]"CreateFiles, AppendData"
$user_inheritance_flag = [System.Security.AccessControl.InheritanceFlags]::None
$user_propagation_flag = [System.Security.AccessControl.PropagationFlags]::None
$user_obj_type =[System.Security.AccessControl.AccessControlType]::Allow
$user_acl = New-Object System.Security.AccessControl.FileSystemAccessRule($user_account, $user_rights, $user_inheritance_flag, $user_propagation_flag, $user_obj_type)

foreach ($user in $users){
    "Setting up for $user"
    $key_file = $key_dir+$user+"\authorized_keys"
    New-Item $key_dir$user -ItemType Directory -Force

    Copy-Item "C:\ci\authorized_keys" $key_file

    $account = New-Object -TypeName System.Security.Principal.NTAccount -ArgumentList $user
    $acl = Get-ACL -Path $key_file
    "Original acl on ${key_file}:"+($acl | Format-List | Out-String)
    # disable inheritance
    $acl.setaccessruleprotection($true, $true)
    $acl.setowner($account)
    # remove access for USERS
    $acl.removeaccessruleall($user_acl)
    "Removing user from acl"+($user_acl | Format-List | Out-String)

    "Setting acl to ${key_file}:"+($acl | Format-List | Out-String)
    set-acl $key_file $acl
}

Stop-Transcript
