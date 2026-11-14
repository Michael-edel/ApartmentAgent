[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9_-]*$')][string]$Project,
    [Parameter(Mandatory=$true)][string]$BackupPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $BackupPath.EndsWith('.dump', [StringComparison]::OrdinalIgnoreCase)) { throw 'Укажите новый файл с расширением .dump.' }
$fullPath = [IO.Path]::GetFullPath($BackupPath)
$manifestPath = "$fullPath.meta.json"
$parent = [IO.Path]::GetDirectoryName($fullPath)
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Папка для копии не существует.' }
if ((Test-Path -LiteralPath $fullPath) -or (Test-Path -LiteralPath $manifestPath)) { throw 'Файл копии или её описание уже существуют.' }
$docker = (Get-Command docker -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$composeFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'docker-compose.yml'
$ids = @(& $docker compose -f $composeFile -p $Project ps --status running -q db)
if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1 -or $ids[0] -notmatch '^[0-9a-f]{12,64}$') { throw 'Нужен один работающий контейнер db указанного проекта.' }
$id = $ids[0]
$details = (& $docker inspect $id | ConvertFrom-Json)[0]
if ($LASTEXITCODE -ne 0 -or $details.Config.Labels.'com.docker.compose.project' -ne $Project) { throw 'Контейнер не принадлежит указанному проекту.' }
$mount = @($details.Mounts | Where-Object { $_.Destination -eq '/var/lib/postgresql/data' -and $_.Type -eq 'volume' })
if ($mount.Count -ne 1 -or -not $mount[0].Name) { throw 'Не удалось проверить отдельный том базы PostgreSQL.' }
$token = [guid]::NewGuid().ToString('N')
$remote = "/tmp/apartmentagent-backup-$token.dump"
$staged = "$fullPath.part-$token"
try {
    & $docker exec $id pg_dump -U apartment -d apartment --format=custom --file=$remote
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать архив PostgreSQL.' }
    & $docker exec $id pg_restore --list $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Архив PostgreSQL не прошёл проверку чтения.' }
    & $docker cp "${id}:$remote" $staged
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $staged -PathType Leaf)) { throw 'Не удалось скопировать архив.' }
    if ((Get-Item -LiteralPath $staged).Length -le 0) { throw 'Архив пуст.' }
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $staged).Hash.ToLowerInvariant()
    Move-Item -LiteralPath $staged -Destination $fullPath
    $manifest = [ordered]@{ version=1; source_project=$Project; source_container=$id; source_volume=[string]$mount[0].Name; database='apartment'; sha256=$hash; created_utc=[DateTime]::UtcNow.ToString('o') }
    $json = $manifest | ConvertTo-Json
    [IO.File]::WriteAllText($manifestPath, $json, [Text.UTF8Encoding]::new($false))
    Write-Output "Резервная копия создана: $fullPath"
    Write-Output "Описание и SHA-256: $manifestPath"
} finally {
    & $docker exec $id rm -f -- $remote 2>$null | Out-Null
    if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged -Force }
}
