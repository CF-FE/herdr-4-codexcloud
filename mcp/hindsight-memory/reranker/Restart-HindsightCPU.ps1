[CmdletBinding()]
param([string]$Root = 'D:\Hindsight', [string]$StartupTask = 'Hindsight-Stack-Startup')

$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $StartupTask -ErrorAction Stop
if ($task.Principal.UserId -notin @('SYSTEM', 'S-1-5-18')) { throw 'The existing startup task must use SYSTEM.' }
$taskDefinition = Export-ScheduledTask -TaskName $StartupTask
$sha = [Security.Cryptography.SHA256]::Create()
try { $taskHash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskDefinition))).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
$pg = @(Get-NetTCPConnection -State Listen -LocalPort 15432 -ErrorAction Stop).OwningProcess
$bge = @(Get-NetTCPConnection -State Listen -LocalPort 18080 -ErrorAction Stop).OwningProcess
$listener = Get-NetTCPConnection -State Listen -LocalPort 8888 -ErrorAction Stop
$state = Get-Content (Join-Path $Root 'state\hindsight-process.json') -Raw | ConvertFrom-Json
$processes = @(Get-CimInstance Win32_Process)
$apiRoot = $processes | Where-Object { $_.ProcessId -eq $state.Pid }
if (-not $apiRoot -or $apiRoot.CommandLine -notlike ('*'+$Root+'\app\*')) { throw 'The tracked API root is not the current Hindsight process.' }
$stopIds = @([int]$apiRoot.ProcessId)
for ($i = 0; $i -lt $stopIds.Count; $i++) {
    $stopIds += @($processes | Where-Object { $_.ParentProcessId -eq $stopIds[$i] } | ForEach-Object { [int]$_.ProcessId })
}
if ([int]$listener.OwningProcess -notin $stopIds) { throw 'API listener is outside the tracked process tree.' }
for ($i = $stopIds.Count - 1; $i -ge 0; $i--) {
    Stop-Process -Id $stopIds[$i] -Force -ErrorAction SilentlyContinue
}
$wrapperPath = Join-Path $Root 'state\hindsight-wrapper.json'
if (Test-Path $wrapperPath) {
    $wrapper = Get-Content $wrapperPath -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId='+$wrapper.WrapperPid) -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -like ('*'+$Root+'\scripts\Run-Hindsight.ps1*')) {
        Stop-Process -Id $process.ProcessId -Force
    }
}
$deadline = (Get-Date).AddSeconds(15)
while ((Get-NetTCPConnection -State Listen -LocalPort 8888 -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
if (Get-NetTCPConnection -State Listen -LocalPort 8888 -ErrorAction SilentlyContinue) { throw 'The old API listener did not close; no new start requested.' }
# The existing idempotent stack task keeps PG/BGE running and launches only the
# missing API, with its established SYSTEM identity. Do not register/modify a task.
Start-ScheduledTask -TaskName $StartupTask
$receipt = [ordered]@{
    state = 'SYSTEM_API_RESTART_REQUESTED'; requested_utc = [DateTime]::UtcNow.ToString('o')
    old_api_listener_pid = $listener.OwningProcess; stopped_api_tree = $stopIds
    pg_pid_before = $pg; bge_pid_before = $bge
    existing_startup_task = $StartupTask; task_definition_sha256 = $taskHash
}
$receipt | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $Root 'tmp\rerank-evaluation-20261105\restart-receipt.json') -Encoding UTF8
$receipt | ConvertTo-Json -Depth 5 -Compress
