$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectRoot 'data\server.pid'
if (!(Test-Path -LiteralPath $pidPath)) { Write-Host 'No background server PID recorded. Foreground server: use Ctrl+C.'; exit 0 }
$serverPid = [int](Get-Content -LiteralPath $pidPath)
$pythonPaths = @((Join-Path $projectRoot '.venv\Scripts\python.exe'),(Join-Path $projectRoot 'runtime\python.exe'))
$serverInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid"
if (!$serverInfo) { Write-Host 'Recorded server is no longer running.'; exit 0 }
if ($serverInfo.ExecutablePath -notin $pythonPaths -or ($serverInfo.CommandLine -notlike '*pe_workflow.api:app*' -and $serverInfo.CommandLine -notlike '*scripts*launch.py*')) { throw 'PID identity mismatch. No process stopped.' }
# Windows venv Python can be a redirector with an actual Python child.
$serverChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId=$serverPid"
foreach ($child in $serverChildren) {
    if ($child.CommandLine -like '*pe_workflow.api:app*' -or $child.CommandLine -like '*scripts*launch.py*') { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
}
Stop-Process -Id $serverPid -ErrorAction SilentlyContinue
Write-Host "Stopped PE-Workflow background service PID $serverPid."
