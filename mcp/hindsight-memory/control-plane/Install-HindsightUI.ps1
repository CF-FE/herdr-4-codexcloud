[CmdletBinding()]
param([string]$Root = 'D:\Hindsight')

$ErrorActionPreference = 'Stop'
$version = '1.2.0'
$integrity = 'sha512-4y3Z68M3qSeWxNvCjVcaZpO4TQ1gOYHpu47mRYvHw1kyYgwFZ8QL2VXRBK2RfAspWaYJzI9uC1h7lv47W6q1hA=='
$destination = Join-Path $Root 'control-plane'
if (Test-Path $destination) {
    throw "Inspect the existing directory before installing: $destination"
}
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$tar = (Get-Command tar.exe -ErrorAction Stop).Source
New-Item $destination -ItemType Directory | Out-Null
# Official prebuilt standalone bundle; no API, database, model or npm dependency install.
# https://github.com/vectorize-io/hindsight/tree/v1.2.0/hindsight-control-plane
$metadata = & $npm pack "@vectorize-io/hindsight-control-plane@$version" --pack-destination $destination --json --silent
if ($LASTEXITCODE -ne 0) { throw 'npm pack failed; inspect the installation directory before retrying.' }
$package = @($metadata | ConvertFrom-Json)[0]
$archive = Join-Path $destination $package.filename
$sha512 = [Security.Cryptography.SHA512]::Create()
try {
    $actual = 'sha512-' + [Convert]::ToBase64String($sha512.ComputeHash([IO.File]::ReadAllBytes($archive)))
} finally { $sha512.Dispose() }
if ($actual -cne $integrity) { throw 'The downloaded package does not match the pinned integrity.' }
& $tar -xzf $archive -C $destination
if ($LASTEXITCODE -ne 0) { throw 'Package extraction failed.' }
$manifest = Get-Content (Join-Path $destination 'package\package.json') -Raw | ConvertFrom-Json
if ($manifest.version -ne $version -or -not (Test-Path (Join-Path $destination 'package\standalone\server.js'))) {
    throw 'The installed package is incomplete or has an unexpected version.'
}
$scripts = Join-Path $Root 'scripts'
New-Item $scripts -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'Start-HindsightUI.ps1') $scripts
Copy-Item (Join-Path $PSScriptRoot 'Start-HindsightUI.cmd') $scripts
[ordered]@{
    package = $manifest.name; version = $version; integrity = $actual
    source_commit = '34a138684a899b6ec34175f7a2b633860466d289'
    installed_at = [DateTime]::UtcNow.ToString('o')
    startup = 'manual foreground only'
} | ConvertTo-Json | Set-Content (Join-Path $destination 'install-receipt.json') -Encoding UTF8
Write-Host "Installed Control Plane $version. Start: $scripts\Start-HindsightUI.cmd"
