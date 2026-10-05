param([string]$PackagePath)
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$packageRecord = Get-Content -LiteralPath (Join-Path $project 'docs\INPUT_BRIDGE_PACKAGE.json') -Raw | ConvertFrom-Json
if (-not $PackagePath) {
    $PackagePath = $packageRecord.package
}
$packagePathResolved = (Resolve-Path -LiteralPath $PackagePath -ErrorAction Stop).ProviderPath
if ($packagePathResolved -ne [IO.Path]::GetFullPath($packageRecord.package)) { throw 'Installer accepts only this deployment reviewed package.' }
$manifestPath = Join-Path $packagePathResolved 'package.json'
$manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
$manifestSha = [Security.Cryptography.SHA256]::Create()
try { $manifestHash = ([BitConverter]::ToString($manifestSha.ComputeHash($manifestBytes))).Replace('-','') } finally { $manifestSha.Dispose() }
if ($manifestHash -ne $packageRecord.manifest_sha256 -or $packageRecord.state -ne 'reviewed_not_installed') { throw 'Complete independent package review before installing the input component.' }
$manifest = [Text.UTF8Encoding]::new($false,$true).GetString($manifestBytes) | ConvertFrom-Json
$installer = Join-Path $packagePathResolved 'install.ps1'
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $manifest.installer_sha256) { throw 'Installer hash mismatch.' }
$systemPowerShell = Join-Path ([System.Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
if ($packagePathResolved -notmatch '^D:\\Codex\\Downloads\\CurrencyWarsInputBridge-[0-9a-f]{32}(-repair-[0-9a-f]{32})?$' -or $manifestHash -notmatch '^[0-9A-F]{64}$' -or $manifest.installer_sha256 -notmatch '^[0-9A-F]{64}$') { throw 'Invalid reviewed package location or digest.' }
# Elevation receives these fixed reviewed digests as literal code. It reads the
# installer once, verifies those exact bytes, then executes that same snapshot.
# A low-writable -File entry would execute before it could verify itself.
$bootstrap = @'
$ErrorActionPreference='Stop'
$env:PSModulePath=$PSHOME+'\Modules'
$package='PACKAGE_LITERAL'
$expectedManifest='MANIFEST_LITERAL'
$expectedInstaller='INSTALLER_LITERAL'
try {
    if ((Get-FileHash -LiteralPath (Join-Path $package 'package.json') -Algorithm SHA256).Hash -cne $expectedManifest) { throw 'Reviewed manifest changed after consent.' }
    $bytes=[IO.File]::ReadAllBytes((Join-Path $package 'install.ps1'))
    $sha=[Security.Cryptography.SHA256]::Create()
    try { $actual=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','') } finally { $sha.Dispose() }
    if ($actual -cne $expectedInstaller) { throw 'Reviewed installer changed after consent.' }
    $source=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)
    & ([ScriptBlock]::Create($source)) -PackageDir $package -ExpectedManifestSha256 $expectedManifest -ValidatedInstallerSha256 $expectedInstaller
    exit 1
} catch { exit 1 }
'@
$bootstrap = $bootstrap.Replace('PACKAGE_LITERAL',$packagePathResolved).Replace('MANIFEST_LITERAL',$manifestHash).Replace('INSTALLER_LITERAL',$manifest.installer_sha256)
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$arguments = '-NoProfile -NonInteractive -EncodedCommand ' + $encoded
# This is the single, ordinary Windows installation consent. Runtime workers
# never invoke RunAs and cannot change the registered protected action.
$installationProcess = Start-Process -FilePath $systemPowerShell -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
$installationProcess.WaitForExit()
if ($installationProcess.ExitCode -ne 0) { throw "Input component installation exited $($installationProcess.ExitCode). See D:\CurrencyWarsInputBridge\installation-status.json if present." }
Get-Content -LiteralPath 'D:\CurrencyWarsInputBridge\installation-status.json' -Raw
