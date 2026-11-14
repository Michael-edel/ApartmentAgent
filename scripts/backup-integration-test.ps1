[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$docker = (Get-Command docker -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$compose = Join-Path (Split-Path -Parent $PSScriptRoot) 'docker-compose.yml'
$token = [guid]::NewGuid().ToString('N').Substring(0, 12)
$projects = @("aabackupsrc$token", "aabackupdst$token")
$testDir = Join-Path ([IO.Path]::GetTempPath()) ("apartmentagent-isolated-$token")
[void][IO.Directory]::CreateDirectory($testDir)
$dump = Join-Path $testDir 'fixture.dump'
$ownedProjects = [System.Collections.Generic.List[string]]::new()
try {
    foreach ($project in $projects) {
        $existing = @(& $docker compose -f $compose -p $project ps -aq)
        if ($LASTEXITCODE -ne 0 -or $existing.Count -ne 0) { throw 'Тестовый проект уже существует или недоступен.' }
        & $docker volume inspect "${project}_postgres_data" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { throw 'Тестовый том уже существует.' }
        $ownedProjects.Add($project)
        & $docker compose -f $compose -p $project up -d --no-deps db | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось запустить тестовую базу.' }
        $id = @(& $docker compose -f $compose -p $project ps --status running -q db)[0]
        $ready = $false
        for ($attempt=0; $attempt -lt 30; $attempt++) {
            & $docker exec $id pg_isready -U apartment -d apartment 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
            Start-Sleep -Seconds 1
        }
        if (-not $ready) { throw 'Тестовая база не запустилась.' }
    }
    $sourceId = @(& $docker compose -f $compose -p $projects[0] ps -q db)[0]
    & $docker exec $sourceId psql -U apartment -d apartment -v ON_ERROR_STOP=1 -c "CREATE TABLE aa_synthetic_backup_check (value text NOT NULL); INSERT INTO aa_synthetic_backup_check VALUES ('synthetic-check-42');" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать синтетическую запись.' }
    & (Join-Path $PSScriptRoot 'backup.ps1') -Project $projects[0] -BackupPath $dump
    $sameRejected = $false
    try { & (Join-Path $PSScriptRoot 'restore.ps1') -Project $projects[0] -BackupPath $dump } catch { $sameRejected = $_.Exception.Message -like '*исходный проект запрещено*' }
    if (-not $sameRejected) { throw 'Восстановление в исходный проект не заблокировано.' }
    & (Join-Path $PSScriptRoot 'restore.ps1') -Project $projects[1] -BackupPath $dump
    $targetId = @(& $docker compose -f $compose -p $projects[1] ps -q db)[0]
    $value = @(& $docker exec $targetId psql -U apartment -d apartment --no-psqlrc -A -t -c 'SELECT value FROM aa_synthetic_backup_check;')
    if ($LASTEXITCODE -ne 0 -or $value.Count -ne 1 -or $value[0].Trim() -ne 'synthetic-check-42') { throw 'Синтетические данные не восстановились.' }
    $nonemptyRejected = $false
    try { & (Join-Path $PSScriptRoot 'restore.ps1') -Project $projects[1] -BackupPath $dump } catch { $nonemptyRejected = $_.Exception.Message -like '*быть пустой*' }
    if (-not $nonemptyRejected) { throw 'Повторное восстановление в непустую базу не заблокировано.' }
    Write-Output 'Изолированный тест: архив создан, синтетическая запись восстановлена, защитные отказы проверены.'
} finally {
    foreach ($project in $ownedProjects) {
        $ids = @(& $docker compose -f $compose -p $project ps -aq)
        $safe = $LASTEXITCODE -eq 0
        foreach ($id in $ids) {
            $object = (& $docker inspect $id | ConvertFrom-Json)[0]
            if ($object.Config.Labels.'com.docker.compose.project' -ne $project) { $safe = $false }
            foreach ($mount in @($object.Mounts | Where-Object { $_.Destination -eq '/var/lib/postgresql/data' })) {
                if ($mount.Type -ne 'volume' -or $mount.Name -ne "${project}_postgres_data") { $safe = $false }
            }
        }
        if ($safe) { & $docker compose -f $compose -p $project down --volumes --remove-orphans | Out-Null }
        else { Write-Warning "Проверьте и удалите вручную тестовый проект $project" }
    }
    foreach ($path in @($dump, "$dump.meta.json")) { if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force } }
    if (Test-Path -LiteralPath $testDir -PathType Container) { Remove-Item -LiteralPath $testDir -Force }
}
