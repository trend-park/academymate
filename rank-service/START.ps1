$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
$pythonArgs = @('-3')
if (-not $pythonCommand) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) {
        $pythonCommand = @{ Source = $bundled }
    } else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    $pythonArgs = @()
}
if (-not $pythonCommand) { throw 'Python 3.12 이상을 설치한 뒤 다시 실행해 주세요.' }
$env:PYTHONPATH = Join-Path $PSScriptRoot '.deps'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot 'data\browsers'
& $pythonCommand.Source @pythonArgs server.py --open
