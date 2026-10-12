$taskRoot = Split-Path -Parent $PSScriptRoot
$taskGh = Join-Path $taskRoot '.private\tools\gh\bin\gh.exe'
if (-not (Test-Path -LiteralPath $taskGh)) {
    $taskGh = (Get-Command gh -ErrorAction Stop).Source
}
& $taskGh auth login --hostname github.com --git-protocol https --web
