param([string]$ChatId)
$ErrorActionPreference = 'Stop'
$currencyGuiDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$currencyGuiProject = Split-Path -Parent $currencyGuiDirectory
$currencyGuiPython = Join-Path $currencyGuiProject '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $currencyGuiPython -PathType Leaf)) { throw 'Run Setup.ps1 to install the project Python environment.' }
& $currencyGuiPython -B -X utf8 (Join-Path $currencyGuiProject 'tools\currency_wars_update.py')
if ($LASTEXITCODE -ne 0) { Write-Warning 'Update checking failed; verifying the existing local installation.' }
& $currencyGuiPython -B -X utf8 (Join-Path $currencyGuiProject 'tools\check_install.py') --check-launch
if ($LASTEXITCODE -ne 0) { throw 'Run Setup.ps1 -BuildGui; dependencies or the compiled GUI need updating.' }
$currencyGuiLifecycle = Join-Path $currencyGuiDirectory 'launch.py'
$currencyGuiArguments = @('-B','-X','utf8',('"' + $currencyGuiLifecycle + '"'),'--skip-update')
if ($ChatId) { $currencyGuiArguments += @('--chat-id', ('"' + $ChatId + '"')) }
Start-Process -FilePath $currencyGuiPython -ArgumentList $currencyGuiArguments -WindowStyle Hidden
