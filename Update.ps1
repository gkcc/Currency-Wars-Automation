param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$currencyPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $currencyPython -PathType Leaf)) {
    $currencyPython = (Get-Command python -ErrorAction Stop).Source
}
$currencyArguments = @('-B','-X','utf8',(Join-Path $PSScriptRoot 'tools\currency_wars_update.py'))
if ($CheckOnly) { $currencyArguments += '--check-only' }
& $currencyPython @currencyArguments
if ($LASTEXITCODE -ne 0) { throw 'Update check failed; no source reset or game control was requested.' }
