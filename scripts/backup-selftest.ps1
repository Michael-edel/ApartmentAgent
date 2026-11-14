[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($script in @('backup.ps1', 'restore.ps1', 'backup-run-scheduled.ps1', 'backup-install-schedule.ps1')) {
    $tokens = $null; $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $script), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "Ошибка синтаксиса $script" }
}
$testDir = Join-Path ([IO.Path]::GetTempPath()) ("apartmentagent-backup-test-" + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testDir)
$dump = Join-Path $testDir 'fixture.dump'
$metaPath = "$dump.meta.json"
$previousPath = $env:PATH
try {
    # Prevent any Docker invocation even if a fail-closed gate regresses.
    $env:PATH = ''
    [IO.File]::WriteAllBytes($dump, [byte[]](1,2,3))
    $meta = @{version=1; source_project='source'; source_container=('a'*64); source_volume='source_volume'; database='apartment'; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $dump).Hash.ToLowerInvariant()}
    [IO.File]::WriteAllText($metaPath, ($meta | ConvertTo-Json))
    $sameRejected = $false
    try { & (Join-Path $PSScriptRoot 'restore.ps1') -Project source -BackupPath $dump } catch { $sameRejected = $_.Exception.Message -like '*исходный проект запрещено*' }
    if (-not $sameRejected) { throw 'Проверка запрета исходного проекта не пройдена.' }
    $meta.sha256 = '0'*64
    [IO.File]::WriteAllText($metaPath, ($meta | ConvertTo-Json))
    $hashRejected = $false
    try { & (Join-Path $PSScriptRoot 'restore.ps1') -Project isolated -BackupPath $dump } catch { $hashRejected = $_.Exception.Message -like '*SHA-256*' }
    if (-not $hashRejected) { throw 'Проверка ошибочного хэша не пройдена.' }
    $missingRejected = $false
    try { & (Join-Path $PSScriptRoot 'restore.ps1') -Project isolated -BackupPath (Join-Path $testDir 'missing.dump') } catch { $missingRejected = $_.Exception.Message -like '*исходное описание*' }
    if (-not $missingRejected) { throw 'Проверка отсутствия архива не пройдена.' }
    function global:Get-ScheduledTask { param($TaskName) return [pscustomobject]@{TaskName=$TaskName} }
    function global:Register-ScheduledTask { throw 'Тест запрещает регистрацию задач.' }
    $existingRejected = $false
    try { & (Join-Path $PSScriptRoot 'backup-install-schedule.ps1') -Project source -DestinationDir $testDir } catch { $existingRejected = $_.Exception.Message -like '*уже существует*' }
    if (-not $existingRejected) { throw 'Проверка запрета замены существующей задачи не пройдена.' }
    Write-Output 'Синтаксис и проверки отказа без Docker пройдены.'
} finally {
    $env:PATH = $previousPath
    Remove-Item -LiteralPath Function:\Get-ScheduledTask -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath Function:\Register-ScheduledTask -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $dump) { Remove-Item -LiteralPath $dump -Force }
    if (Test-Path -LiteralPath $metaPath) { Remove-Item -LiteralPath $metaPath -Force }
    if (Test-Path -LiteralPath $testDir -PathType Container) { Remove-Item -LiteralPath $testDir -Force }
}
