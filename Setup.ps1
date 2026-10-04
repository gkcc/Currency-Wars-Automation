param([string]$Python = 'python', [switch]$BuildGui)
$ErrorActionPreference = 'Stop'
$currencyProject = $PSScriptRoot
$currencyVenvPython = Join-Path $currencyProject '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $currencyVenvPython -PathType Leaf)) {
    & $Python -B -c 'import sys; assert (3,11) <= sys.version_info[:2] <= (3,13), "Python 3.11-3.13 required"'
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11-3.13 or select it with -Python.' }
    & $Python -B -m venv (Join-Path $currencyProject '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the project Python environment.' }
}
& $currencyVenvPython -B -m pip install -r (Join-Path $currencyProject 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
New-Item -ItemType Directory -Path (Join-Path $currencyProject 'docs') -Force | Out-Null
& $currencyVenvPython -B -X utf8 (Join-Path $currencyProject 'tools\currency_wars_update.py') --record-install
if ($LASTEXITCODE -ne 0) { throw 'Cannot attest the installed Python dependencies.' }
if ($BuildGui) { & (Join-Path $currencyProject 'gui\Build-GUI.ps1') }
& $currencyVenvPython -B -X utf8 (Join-Path $currencyProject 'tools\check_install.py')
if ($LASTEXITCODE -ne 0) { throw 'Installation check failed; see the result above.' }
Write-Host 'Setup complete. Start.ps1 opens the GUI. Setup does not start or control the game.'
