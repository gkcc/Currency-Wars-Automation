param([string]$ChatId)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'gui\Start-GUI.ps1') -ChatId $ChatId
