[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$Project,
    [Parameter(Mandatory=$true)][string]$DestinationDir,
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')][string]$DailyAt = '03:00',
    [switch]$ReplaceExisting
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$folder = [IO.Path]::GetFullPath($DestinationDir)
if (-not (Test-Path -LiteralPath $folder -PathType Container)) { throw 'Папка резервных копий не существует.' }
if ($folder.Contains('"')) { throw 'Недопустимый путь к папке.' }
$runner = Join-Path $PSScriptRoot 'backup-run-scheduled.ps1'
$pwsh = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf) -or -not (Test-Path -LiteralPath $pwsh -PathType Leaf)) { throw 'Нужен PowerShell 7 и комплект скриптов резервирования.' }
$taskName = "ApartmentAgent-Backup-$Project"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and -not $ReplaceExisting) { throw 'Задача уже существует. Для явной замены используйте -ReplaceExisting.' }
$arguments = '-NoProfile -NonInteractive -File "{0}" -Project "{1}" -DestinationDir "{2}"' -f $runner, $Project, $folder
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments
$time = [DateTime]::Today.AddHours([int]$DailyAt.Substring(0,2)).AddMinutes([int]$DailyAt.Substring(3,2))
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$parameters = @{TaskName=$taskName; Action=$action; Trigger=$trigger; Settings=$settings; Principal=$principal; Description='Ежедневная локальная копия базы ApartmentAgent.'}
if ($ReplaceExisting) { $parameters.Force = $true }
Register-ScheduledTask @parameters | Out-Null
Write-Output "Ежедневная задача $taskName создана на $DailyAt. Она запускается только при входе текущего пользователя в Windows; Docker и проект должны работать."
