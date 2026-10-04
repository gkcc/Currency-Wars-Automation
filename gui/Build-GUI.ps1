$ErrorActionPreference = 'Stop'
$currencyGuiDirectory = $PSScriptRoot
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Install Rust MSVC and Visual Studio C++ build tools first.' }
& cargo build --locked --release --manifest-path (Join-Path $currencyGuiDirectory 'Cargo.toml') --target-dir (Join-Path $currencyGuiDirectory 'target')
if ($LASTEXITCODE -ne 0) { throw 'Rust/Tauri build failed.' }
$currencyBinaryDirectory = Join-Path $currencyGuiDirectory 'bin'
New-Item -ItemType Directory -Path $currencyBinaryDirectory -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $currencyGuiDirectory 'target\release\currency-wars-gui.exe') -Destination $currencyBinaryDirectory -Force
$currencyProject = Split-Path -Parent $currencyGuiDirectory
$currencyPython = Join-Path $currencyProject '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $currencyPython -PathType Leaf)) { throw 'Run Setup.ps1 before building the GUI.' }
& $currencyPython -B -X utf8 (Join-Path $currencyProject 'tools\currency_wars_update.py') --record-build
if ($LASTEXITCODE -ne 0) { throw 'Cannot attest the locally compiled GUI.' }
Write-Host 'GUI built. Reusable compiler cache is in gui\target.'
