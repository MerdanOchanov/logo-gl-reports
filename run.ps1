# Запуск сервера: создаёт .venv при отсутствии, ставит зависимости (при изменении requirements.txt), при отсутствии frontend\dist
# собирает фронтенд (если есть Node/npm), запускает uvicorn (порт 8790; раздаёт frontend\dist).
#   .\run.ps1               обычный запуск
#   .\run.ps1 -CheckOnly    только проверить окружение (venv, зависимости, фронт, Logo) и выйти, сервер не запускать
#   .\run.ps1 -Build        принудительно пересобрать фронтенд перед запуском
param([switch]$CheckOnly, [switch]$Build)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv'
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Создаю виртуальное окружение...'
    python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать .venv: установите Python 3.11+ и добавьте его в PATH' }
}
$req = Join-Path $root 'backend\requirements.txt'
$stamp = Join-Path $venv '.req-stamp'
if (-not (Test-Path $stamp) -or (Get-Item $req).LastWriteTime -gt (Get-Item $stamp).LastWriteTime) {
    Write-Host 'Устанавливаю зависимости...'
    & $py -m pip install -q -r $req
    if ($LASTEXITCODE -ne 0) { throw 'pip install завершился с ошибкой' }
    New-Item -ItemType File -Force $stamp | Out-Null
}

# фронтенд: сборка только если нет dist (или указан -Build) и доступен npm; готовый dist не трогаем
$front = Join-Path $root 'frontend'
$dist = Join-Path $front 'dist\index.html'
if ((-not (Test-Path $dist)) -or $Build) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm -and (Test-Path (Join-Path $front 'package.json'))) {
        Write-Host 'Собираю фронтенд (npm)...'
        Push-Location $front
        try {
            if (-not (Test-Path 'node_modules')) { npm ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'npm ci завершился с ошибкой' } }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw 'npm run build завершился с ошибкой' }
        } finally { Pop-Location }
    } else {
        Write-Host 'frontend\dist не найден и npm недоступен - будет работать только API (для разработки: npm run dev в frontend).'
    }
}

$port = if ($env:PORT) { $env:PORT } else { '8790' }
if (-not $env:PORT) {   # порт из config.ini ([app] port), если файл есть; переменная окружения PORT главнее
    $ini = Join-Path $root 'config.ini'
    if (Test-Path $ini) {
        $m = Select-String -Path $ini -Pattern '^\s*port\s*=\s*(\d+)' | Select-Object -First 1
        if ($m) { $port = $m.Matches[0].Groups[1].Value }
    }
}
$env:PORT = $port
if ($CheckOnly) {
    Set-Location (Join-Path $root 'backend')
    & $py -c "import fastapi, uvicorn, pyodbc, openpyxl, multipart; from app import config, logo; print('Зависимости: OK'); print('Данные:', config.DATA_DIR); print('Logo доступен:', logo.ping())"
    Write-Host ('Фронтенд собран: ' + (Test-Path $dist))
    exit 0
}
Write-Host "Сервер: http://localhost:$port"
Set-Location (Join-Path $root 'backend')
& $py -m uvicorn app.main:app --host 0.0.0.0 --port $port
