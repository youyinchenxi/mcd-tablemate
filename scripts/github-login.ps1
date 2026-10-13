$taskRoot = Split-Path -Parent $PSScriptRoot
$taskGh = Join-Path $taskRoot '.private\tools\gh\bin\gh.exe'
if (-not (Test-Path -LiteralPath $taskGh)) {
    $taskGh = (Get-Command gh -ErrorAction Stop).Source
}
# Go-based gh does not automatically use the Windows system proxy.
# Reuse an enabled loopback proxy for this process; never change system settings.
if (-not $env:HTTPS_PROXY) {
    $taskProxySettings = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($taskProxySettings.ProxyEnable) {
        $taskProxyParts = [string]$taskProxySettings.ProxyServer -split ';'
        $taskProxyEntry = $taskProxyParts | Where-Object { $_ -match '^https=' } | Select-Object -First 1
        if (-not $taskProxyEntry) { $taskProxyEntry = $taskProxyParts | Select-Object -First 1 }
        if ($taskProxyEntry -match '^(?:(?:https?|socks)=)?(127\.0\.0\.1|localhost):(\d{1,5})$') {
            $env:HTTPS_PROXY = 'http://' + $Matches[1] + ':' + $Matches[2]
        }
    }
}
& $taskGh auth login --hostname github.com --git-protocol https --web
