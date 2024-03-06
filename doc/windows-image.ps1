# this script helps to create custom unattended install images. See the
# main documentation for details

function Write-Status()
{
    $current_color = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = "green"
    Write-Output $args
    $host.UI.RawUI.ForegroundColor = $current_color
}

# todo: check if it ends in .ps1 - windows is stupid about that
if (($args[0]) -and
    (Test-Path -PathType Leaf -Path $args[0]))
{
    $parameter_file=(Resolve-Path -path $args[0]).Path
}
elseif (($Env:CI_ISO_PARAMETERS) -and
        (Test-Path -PathType Leaf -Path $Env:CI_ISO_PARAMETERS)){
            Write-Output "Using parameter file"
            $parameter_file=(ResolvePath -path $Env:CI_ISO_PARAMETERS).Path
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

if (-not (Test-Path variable:ci_target_dir)){
    $ci_target_dir = "ci"
}

if (-not (Test-Path variable:imgburn)){
    $imgburn = "${Env:ProgramFiles(x86)}\ImgBurn\ImgBurn.exe"
}

if(!(get-DiskImage -ImagePath $iso_path).Attached){
    Mount-DiskImage -ImagePath $iso_path
}

# todo, error handling about mounting
$cd_drive = (get-DiskImage -ImagePath $iso_path | Get-Volume).DriveLetter

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
    Write-Status "Copying image files, this will take a while..."
    Copy-Item "${cd_drive}:\*" -Destination $image_path\data\ -Force -Recurse
}

Dismount-DiskImage -ImagePath $iso_path

$install_esd="${image_path}\data\sources\install.esd"
$install_wim="${image_path}\data\sources\install.wim"
$boot_wim="${image_path}\data\sources\boot.wim"
$boot_mnt="${image_path}\boot\"
$install_mnt="${image_path}\install\"

# Newer Windows versions seem to have abandoned the esd file - so this step may
# get skipped. In this case the upstream wim file most likely has multiple
# editions - with the selection of the image happening either at install time
# or through the answer file.
if (-not (Test-Path -PathType Leaf -Path $install_wim)){
    if (-not (Test-Path variable:os_index)){
        dism /Get-WimInfo /WimFile:${install_esd}
        $os_index=Read-Host "Select OS image to use (number)"
    }

    Write-Status "Extracting image at index ${os_index}, this will take a while..."
    dism /Export-Image /SourceImageFile:${install_esd} /DestinationImageFile:${install_wim} /SourceIndex:${os_index} /CheckIntegrity /Compress:Max
    Remove-Item -Force $install_esd
} else {
    Write-Warning "install.wim already exists, skipping."
    Write-Warning "Remove ${install_wim} to repeat the extraction from esd."
}

if ((Get-ChildItem -Path ${install_wim}).IsReadOnly){
    Write-Status "install.wim is read-only, changing"
    Set-ItemProperty -Path ${install_wim} -Name IsReadOnly -Value $false
}
if ((Get-ChildItem -Path ${boot_wim}).IsReadOnly){
    Write-Status "boot.wim is read-only, changing"
    Set-ItemProperty -Path ${boot_wim} -Name IsReadOnly -Value $false
}

Write-Output "install.wim contains:"
Get-WindowsImage -ImagePath ${install_wim}

Mount-WindowsImage -ImagePath ${boot_wim} -Index 2 -Path ${boot_mnt}
Mount-WindowsImage -ImagePath ${install_wim} -Index 1 -Path ${install_mnt}

if (Test-Path variable:remove_apps){
    foreach ($app in $remove_apps){
        Write-Status "Trying to remove $app..."
        Get-AppXProvisionedPackage -path ${install_mnt} | where DisplayName -EQ $app | Remove-AppxProvisionedPackage
    }
}

New-Item -Path ${install_mnt}\${ci_target_dir} -ItemType Directory

if ((Test-Path variable:ci_source_path) -and
    (Test-Path -Path $ci_source_path))
{
    Write-Status "Copying CI scripts to target media..."
    Write-Status "(${ci_source_path} -> ${install_mnt}\${ci_target_dir})"
    Copy-Item "${ci_source_path}\*" -Destination ${install_mnt}\${ci_target_dir} -Force -Recurse
} else {
    Write-Warning "ci_source_path not set or pointing to invalid directory, skipping."
}

if ((Test-Path variable:autounattend_path) -and
    (Test-Path -PathType Leaf -Path $autounattend_path))
{
    Write-Status "Using autounattend data from autounattend_path variable"
    Copy-Item "$autounattend_path" -Destination "${image_path}\data\Autounattend.xml" -Force -Recurse
} elseif (Test-Path -PathType Leaf -Path "${install_mnt}\${ci_target_dir}\Autounattend.xml"){
    Write-Status "Using autounattend data from CI script directory"
    Copy-Item "${install_mnt}\${ci_target_dir}\Autounattend.xml" -Destination "${image_path}\data\Autounattend.xml" -Force -Recurse
} else {
    Write-Warning "No autounattend file in autounattend_path variable or CI script directory, skipping."
}

Write-Status "active image mounts (this may show mounts from other processes):"
Get-WindowsImage -Mounted
if ($wait_for_manual){
    $foo = Read-Host "You can inject data into above mount locations now. Press any key to continue."
}

Dismount-WindowsImage -Path ${boot_mnt} -save -checkintegrity
Dismount-WindowsImage -Path ${install_mnt} -save -checkintegrity

if ((Test-Path variable:new_iso_path) -and
    (Test-Path -Path $imgburn))
{
    & ${imgburn} /mode build /buildinputmode advanced /buildoutputmode imagefile /src ${image_path}\data /dest ${new_iso_path} /volumelabel WIN10 /filesystem UDF /udfrevision 1.02 /recursesubdirectories yes /includehiddenfiles yes /includesystemfiles yes /bootimage "${image_path}\data\boot\etfsboot.com" /bootemutype 0 /bootsectorstoload 8 /bootloadsegment 07C0 /start /closesuccess /rootfolder yes /portable /noimagedetails
} else {
    Write-Warning "Skipping media creation. If you don't want that set new_iso_path and install imgburn."
    Write-Warning "Also set imgburn if imgburn is not in default directories."
}
