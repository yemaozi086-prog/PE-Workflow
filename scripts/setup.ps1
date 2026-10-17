param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$chain = Get-Content toolchain.json -Raw | ConvertFrom-Json
if ((& $Python -c 'import platform; print(platform.python_version())') -ne $chain.python) { throw "Python $($chain.python) required. Pass -Python <path>." }
if ((& node -v) -ne ('v'+$chain.node)) { throw "Node $($chain.node) required." }
if ((& npm -v) -ne $chain.npm) { throw "npm $($chain.npm) required." }
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) { & $Python -m venv .venv; if ($LASTEXITCODE) { throw 'venv creation failed' } }
& '.venv\Scripts\python.exe' -m ensurepip
if ($LASTEXITCODE) { throw 'pip bootstrap failed' }
& '.venv\Scripts\python.exe' -m pip install --require-hashes --only-binary=:all: -r backend/requirements-win-py314.lock
if ($LASTEXITCODE) { throw 'Locked Python dependency install failed' }
& '.venv\Scripts\python.exe' -m pip check
if ($LASTEXITCODE) { throw 'Python dependency graph is inconsistent' }
npm --prefix frontend ci --no-audit --no-fund --cache .npm-cache
if ($LASTEXITCODE) { throw 'Locked frontend install failed' }
npm --prefix frontend run build
if ($LASTEXITCODE) { throw 'Frontend build failed' }
& '.venv\Scripts\python.exe' scripts/launch.py --check
if ($LASTEXITCODE) { throw 'Runtime check failed' }
Write-Host 'Ready. Double-click Start.cmd. No DSH is required for calculation.'
