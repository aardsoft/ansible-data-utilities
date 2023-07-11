# this script helps to create custom unattended install images. See the
# main documentation for details

Write-Output $args

# todo: check if it ends in .ps1 - windows is stupid about that
if (($args[0]) -and
    (Test-Path -PathType Leaf -Path $args[0]))
{
    $parameter_file=$args[0]
}
elseif (($Env:CI_ISO_PARAMETERS) -and
        (Test-Path -PathType Leaf -Path $Env:CI_ISO_PARAMETERS)){
            Write-Output "Using parameter file"
            $parameter_file=$Env:CI_ISO_PARAMETERS
        }

$current_principal = (New-Object Security.Principal.WindowsPrincipal(
                          [Security.Principal.WindowsIdentity]::GetCurrent()))

if (!$current_principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)){
            if ($parameter_file){
                $script_arguments='-File', $MyInvocation.MyCommand.Source, $parameter_file | %{ $_ }
            } else {
                $script_arguments='-File', $MyInvocation.MyCommand.Source, $parameter_file | %{ $_ }
            }
            Start-Process -FilePath 'powershell' -Verb RunAs -ArgumentList $script_arguments
            exit
        }


if ($parameter_file){
    . $parameter_file
}

if (-not (Test-Path variable:iso_path)){
    $iso_path = Read-Host "Path to source ISO"
}

if (-not (Test-Path variable:image_path)){
    $image_path = Read-Host "Path to extracted image"
}

if(!(get-DiskImage -ImagePath $iso_path).Attached){
    Mount-DiskImage -ImagePath $iso_path
}

# todo, error handling about mounting

$cd_drive = (get-DiskImage -ImagePath $iso_path | Get-Volume).DriveLetter

Write-Output $cd_drive

$copy_data=$true

if (Test-Path $image_path){
    $dirinfo = Get-ChildItem $image_path | Measure-Object

    If ($dirinfo.count -ne 0){
        Write-Warning "Target directory not empty, assuming data has been copied"
        Write-Warning "To start from scratch delete ${image_path}"
        $copy_data=$false
    }
} else {
    New-Item -Path $image_path -ItemType Directory
}

if ($copy_data){
    New-Item -Path $image_path -ItemType Directory
    New-Item -Path $image_path\data -ItemType Directory
    New-Item -Path $image_path\boot -ItemType Directory
    New-Item -Path $image_path\install -ItemType Directory
    Write-Output "Copying image files, this will take a while..."
    Copy-Item "${cd_drive}:\*" -Destination $image_path\data\ -Force -Recurse
}

$install_esd="${image_path}\data\sources\install.esd"
$install_wim="${image_path}\data\sources\install.wim"
$boot_wim="${image_path}\data\sources\boot.wim"
$boot_mnt="${image_path}\boot\"
$install_mnt="${image_path}\install\"

if (-not (Test-Path -PathType Leaf -Path $install_wim)){
    if (-not (Test-Path variable:os_index)){
        dism /Get-WimInfo /WimFile:${install_esd}
        $os_index=Read-Host "Select OS image to use (number)"
    }

    Write-Output "Extracting image at index ${os_index}, this will take a while..."
    dism /Export-Image /SourceImageFile:${install_esd} /DestinationImageFile:${install_wim} /SourceIndex:${os_index} /CheckIntegrity /Compress:Max
    Remove-Item -Force $install_esd
}

if ((Get-ChildItem -Path ${install_wim}).IsReadOnly){
    Write-Warning "install.wim is read-only, changing"
    Set-ItemProperty -Path ${install_wim} -Name IsReadOnly -Value $false
}
if ((Get-ChildItem -Path ${boot_wim}).IsReadOnly){
    Write-Warning "boot.wim is read-only, changing"
    Set-ItemProperty -Path ${boot_wim} -Name IsReadOnly -Value $false
}
Get-WindowsImage -ImagePath ${install_wim}

Mount-WindowsImage -ImagePath ${boot_wim} -Index 2 -Path ${boot_mnt}
Mount-WindowsImage -ImagePath ${install_wim} -Index 1 -Path ${install_mnt}

New-Item -Path ${install_mnt}\${ci_target_dir} -ItemType Directory

Get-WindowsImage -Mounted
if ($wait_for_manual){
    $foo = Read-Host "You can inject data into above mount locations now. Press any key to continue."
}

Dismount-WindowsImage -Path ${boot_mnt} -save -checkintegrity
Dismount-WindowsImage -Path ${install_mnt} -save -checkintegrity
