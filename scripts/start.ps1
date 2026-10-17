param([int]$Port = 0, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'runtime\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first or use the self-contained release.' }
$launchArgs = @((Join-Path $PSScriptRoot 'launch.py'))
if ($Port) { $launchArgs += @('--port', "$Port") }
if ($NoBrowser) { $launchArgs += '--no-browser' }
& $pythonPath @launchArgs
if ($LASTEXITCODE) { throw 'Flux did not start successfully; see diagnostics above.' }
