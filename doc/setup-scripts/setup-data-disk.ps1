$ErrorActionPreference="SilentlyContinue"
Stop-Transcript | out-null
$ErrorActionPreference = "Continue"
Start-Transcript -path C:\ci\logs\disk-setup.log -append

# rename the CD-ROM to R: to have D: available for the data disk
$cd = gwmi win32_volume -Filter "DriveLetter = 'd:'"
Set-WmiInstance -input $cd -Arguments @{DriveLetter="R:"}

Get-Disk |
  Where partitionstyle -eq "raw" |
  Initialize-Disk -PartitionStyle MBR -PassThru |
  New-Partition -AssignDriveLetter -UseMaximumSize |
  Format-Volume -FileSystem NTFS -NewFileSystemLabel "data" -Confirm:$false

Stop-Transcript
