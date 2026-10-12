param([int]$Port = 8765)
$taskProject = $PSScriptRoot
Set-Location -LiteralPath $taskProject
python -m maimai_team serve --port $Port
