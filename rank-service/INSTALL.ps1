$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
$pythonArgs = @('-3')
if (-not $pythonCommand) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { $pythonCommand = @{ Source = $bundled } }
    else { $pythonCommand = Get-Command python -ErrorAction SilentlyContinue }
    $pythonArgs = @()
}
if (-not $pythonCommand) { throw 'Python 3.12 이상을 설치해 주세요.' }
$env:PYTHONPATH = Join-Path $PSScriptRoot '.deps'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot 'data\browsers'
& $pythonCommand.Source @pythonArgs -m pip install --target .deps -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '모듈 설치에 실패했습니다.' }
& $pythonCommand.Source @pythonArgs -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw '브라우저 설치에 실패했습니다.' }
Write-Host '설치 완료. START.cmd를 실행하세요.'
