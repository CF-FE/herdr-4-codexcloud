[CmdletBinding()]
param([ValidateRange(1024,65535)][int]$Port = 9999)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$server = Join-Path $root 'control-plane\package\standalone\server.js'
if (-not (Test-Path $server)) { throw "Install the official Control Plane first: $server" }
$node = (Get-Command node.exe -ErrorAction Stop).Source
$key = $null
foreach ($line in Get-Content (Join-Path $root 'config\shared.env')) {
    if ($line -match '^\s*HINDSIGHT_API_TENANT_API_KEY\s*=(.*)$') {
        $key = $Matches[1].Trim().Trim('"').Trim("'")
    }
}
if (-not $key) { throw 'The existing tenant API key is missing from config\shared.env.' }

# Server-only API credentials; localhost UI is reached remotely through existing SSH.
# https://github.com/vectorize-io/hindsight/blob/v1.2.0/hindsight-control-plane/src/lib/hindsight-client.ts
# Direct server.js avoids bin/cli.js's windowsHide child process wrapper.
$settings = @{
    PORT = "$Port"
    HOSTNAME = 'localhost'
    HINDSIGHT_CP_DATAPLANE_API_URL = 'http://127.0.0.1:8888'
    HINDSIGHT_CP_DATAPLANE_API_KEY = $key
    NODE_OPTIONS = (($env:NODE_OPTIONS + ' --dns-result-order=ipv4first').Trim())
    NEXT_TELEMETRY_DISABLED = '1'
}
$previous = @{}
foreach ($name in $settings.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
}
try {
    Write-Host "Hindsight Control Plane 1.2.0: http://localhost:$Port"
    Write-Host 'Foreground UI only. Press Ctrl+C to stop; the existing backend stays running.'
    & $node $server
    $uiExitCode = $LASTEXITCODE
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    $key = $null
    $settings.Clear()
}
exit $uiExitCode
