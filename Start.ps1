param([string]$ChatId, [switch]$Run)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'gui\Start-GUI.ps1') -ChatId $ChatId -Run:$Run
