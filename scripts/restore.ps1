[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$Project,
    [Parameter(Mandatory=$true)][string]$BackupPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$fullPath = [IO.Path]::GetFullPath($BackupPath)
$manifestPath = "$fullPath.meta.json"
if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf) -or -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Нужны файл .dump и его исходное описание .meta.json.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.version -ne 1 -or $manifest.database -ne 'apartment' -or $manifest.source_project -notmatch '^[a-z0-9][a-z0-9_-]*$' -or $manifest.source_container -notmatch '^[0-9a-f]{12,64}$' -or -not $manifest.source_volume -or $manifest.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Неверное описание резервной копии.' }
if ($manifest.source_project -eq $Project) { throw 'Восстановление в исходный проект запрещено. Выберите отдельный проект.' }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $fullPath).Hash.ToLowerInvariant() -ne $manifest.sha256) { throw 'SHA-256 архива не совпадает с описанием.' }
$docker = (Get-Command docker -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$composeFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'docker-compose.yml'
$ids = @(& $docker compose -f $composeFile -p $Project ps --status running -q db)
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1 -or $ids[0] -notmatch '^[0-9a-f]{12,64}$') { throw 'Нужен один работающий контейнер db отдельного проекта.' }
$id = $ids[0]
if ($id -eq $manifest.source_container) { throw 'Исходный и целевой контейнеры совпадают.' }
$details = (& $docker inspect $id | ConvertFrom-Json)[0]
if ($LASTEXITCODE -ne 0 -or $details.Config.Labels.'com.docker.compose.project' -ne $Project) { throw 'Целевой контейнер не принадлежит указанному проекту.' }
$mount = @($details.Mounts | Where-Object { $_.Destination -eq '/var/lib/postgresql/data' -and $_.Type -eq 'volume' })
if ($mount.Count -ne 1 -or -not $mount[0].Name -or $mount[0].Name -eq $manifest.source_volume) { throw 'Целевой том не подтверждён как отдельный от исходной базы.' }
$query = "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f');"
$count = @(& $docker exec $id psql -U apartment -d apartment --no-psqlrc -A -t -c $query)
if ($LASTEXITCODE -ne 0 -or $count.Count -ne 1 -or $count[0].Trim() -ne '0') { throw 'Целевая база должна существовать и быть пустой; восстановление остановлено.' }
$remote = "/tmp/apartmentagent-restore-$([guid]::NewGuid().ToString('N')).dump"
try {
    & $docker cp $fullPath "${id}:$remote"
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось передать архив целевому контейнеру.' }
    & $docker exec $id pg_restore --list $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Архив PostgreSQL не прошёл проверку чтения.' }
    & $docker exec $id pg_restore -U apartment -d apartment --no-owner --no-privileges --exit-on-error --single-transaction $remote
    if ($LASTEXITCODE -ne 0) { throw 'Восстановление не выполнено: транзакция отменена.' }
    Write-Output "База восстановлена в отдельный проект $Project. Проверьте приложение этого проекта."
} finally {
    & $docker exec $id rm -f -- $remote 2>$null | Out-Null
}
