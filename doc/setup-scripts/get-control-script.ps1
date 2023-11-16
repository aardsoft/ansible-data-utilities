$ErrorActionPreference="SilentlyContinue"
Stop-Transcript | out-null
$ErrorActionPreference = "Continue"
Start-Transcript -path C:\ci\logs\get-control-script.log -append

$control_script_local_path = "c:\ci\control_script.ps1"
$control_script_remote_path = "/control_script.ps1"
$control_servers = @("https://mirror",
                     "http://mirror",
                     "https://control-server",
                     "http://control-server")

$wc = New-Object System.Net.WebClient
$ErrorMessage = $Error[0].Exception.ErrorRecord.Exception.Message

foreach ($server in $control_servers) {
    $url = "$($server)/$($control_script_remote_path)"
    try {
        $wc.DownloadFile($url, $control_script_local_path)
    } catch {
        Write-Host "Not downloaded from $url"
        Write-Host -Object $ErrorMessage
    }
}

powershell.exe -ExecutionPolicy Bypass -File $control_script_local_path

Stop-Transcript
