$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& '.venv\Scripts\python.exe' -m pytest backend/tests -q
if ($LASTEXITCODE) { throw 'Backend checks failed.' }
Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
npm run build
if ($LASTEXITCODE) { throw 'Frontend checks failed.' }
