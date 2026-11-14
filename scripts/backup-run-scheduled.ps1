[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$Project,
    [Parameter(Mandatory=$true)][string]$DestinationDir
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$folder = [IO.Path]::GetFullPath($DestinationDir)
if (-not (Test-Path -LiteralPath $folder -PathType Container)) { throw 'Папка резервных копий не существует.' }
$name = 'ApartmentAgent-{0}-{1}-{2}.dump' -f $Project, (Get-Date -Format 'yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
& (Join-Path $PSScriptRoot 'backup.ps1') -Project $Project -BackupPath (Join-Path $folder $name)
