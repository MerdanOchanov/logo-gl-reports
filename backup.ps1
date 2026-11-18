# Резервная копия настроек: data\settings.db (классификаторы, отчёты, правила, пользователи, ручная корреспонденция), data\registry.db (подключения и наборы), data\ds\*\settings.db (наборы этапа B)
# и data\templates (Excel-шаблоны бланков) -> backups\settings-ГГГГ-ММ-ДД_ЧЧММСС\. Кэш (data\cache.db) не копируется: он пересоздаётся из Logo.
#   .\backup.ps1                 копия в backups\ рядом со скриптом
#   .\backup.ps1 -Keep 30        после копирования оставить только 30 последних копий (по умолчанию не удалять)
#   .\backup.ps1 -Dest D:\bk     другая папка для копий
param([string]$Dest, [int]$Keep = 0)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$data = if ($env:DATA_DIR) { $env:DATA_DIR } else { Join-Path $root 'data' }
if (-not $Dest) { $Dest = Join-Path $root 'backups' }
$db = Join-Path $data 'settings.db'
if (-not (Test-Path $db)) { throw "Не найден $db - нечего копировать" }
$target = Join-Path $Dest ('settings-' + (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
New-Item -ItemType Directory -Force $target | Out-Null

# settings.db открыт сервером в режиме WAL: консистентная копия делается средствами SQLite (VACUUM INTO), а не копированием файла.
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$out = Join-Path $target 'settings.db'
$code = "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('VACUUM INTO ?', (sys.argv[2],)); c.close()"
& $py -c $code $db $out
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $out)) { throw 'Не удалось создать копию settings.db' }

# реестр подключений и наборов (универсальность, этап A): data\registry.db (пароль подключения хранится зашифрованным средствами Windows)
$reg = Join-Path $data 'registry.db'
if (Test-Path $reg) {
    $rout = Join-Path $target 'registry.db'
    & $py -c $code $reg $rout
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $rout)) { throw 'Не удалось создать копию registry.db' }
}
$ds = Join-Path $data 'ds'     # наборы данных этапа B (data\ds\<id>\settings.db)
if (Test-Path $ds) {
    Get-ChildItem $ds -Directory | Where-Object { $_.Name -notlike '_deleted*' } | ForEach-Object {
        $dst = Join-Path $target ('ds\' + $_.Name)
        New-Item -ItemType Directory -Force $dst | Out-Null
        $sdb = Join-Path $_.FullName 'settings.db'
        if (Test-Path $sdb) { & $py -c $code $sdb (Join-Path $dst 'settings.db') }
        if (Test-Path (Join-Path $_.FullName 'templates')) { Copy-Item (Join-Path $_.FullName 'templates') (Join-Path $dst 'templates') -Recurse -Force }
    }
}
$tpl = Join-Path $data 'templates'
if (Test-Path $tpl) { Copy-Item $tpl (Join-Path $target 'templates') -Recurse -Force }
# при включённом AUTH=1 ключ подписи сессий лежит в data\.secret - без него после восстановления все войдут заново (не критично), копируем
$secret = Join-Path $data '.secret'
if (Test-Path $secret) { Copy-Item $secret (Join-Path $target '.secret') -Force }

if ($Keep -gt 0) {
    Get-ChildItem $Dest -Directory -Filter 'settings-*' | Sort-Object Name -Descending | Select-Object -Skip $Keep |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
}
Write-Host "Копия создана: $target"
Write-Host 'Восстановление: остановить сервер, заменить data\settings.db, data\registry.db, data\ds\<номер>\settings.db (и data\templates) файлами из копии, запустить сервер.'
