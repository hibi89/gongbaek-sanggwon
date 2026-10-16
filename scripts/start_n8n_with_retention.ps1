[CmdletBinding()]
param(
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

# Keep finished n8n executions long enough for a 30-day-after-delivery cleanup.
# The delivery date can be several calendar days after the workflow execution.
$env:EXECUTIONS_DATA_PRUNE = 'true'
$env:EXECUTIONS_DATA_MAX_AGE = '900' # 37.5 days from execution finish
$env:EXECUTIONS_DATA_PRUNE_MAX_COUNT = '0' # avoid count-based early deletion

$launcher = Join-Path $env:APPDATA 'npm\n8n.cmd'
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedN8n = (Get-Content -LiteralPath (Join-Path $projectRoot 'n8n-version.txt') -Raw).Trim()
$expectedNode = 'v' + (Get-Content -LiteralPath (Join-Path $projectRoot '.nvmrc') -Raw).Trim()
$port = 5678
$nodeCommand = Get-Command node -ErrorAction Stop
$actualNode = (& $nodeCommand.Source --version | Out-String).Trim()
$portOpen = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue
$actualN8n = if (Test-Path -LiteralPath $launcher) { (& $launcher --version | Out-String).Trim() } else { '' }

if (-not (Test-Path -LiteralPath $launcher)) {
    throw "The installed n8n launcher was not found at $launcher."
}
if ($actualN8n -ne $expectedN8n) {
    throw "n8n version mismatch: expected $expectedN8n, found $actualN8n. Use the project-pinned n8n version."
}
if ($actualNode -ne $expectedNode) {
    throw "Node.js version mismatch: expected $expectedNode, found $actualNode. Use the project-pinned Node.js version."
}

$status = [ordered]@{
    launcher_exists = Test-Path -LiteralPath $launcher
    listen_port = $port
    port_already_open = [bool]$portOpen
    pruning_enabled_for_next_start = $env:EXECUTIONS_DATA_PRUNE
    max_age_hours_for_next_start = [int]$env:EXECUTIONS_DATA_MAX_AGE
    count_limit_for_next_start = [int]$env:EXECUTIONS_DATA_PRUNE_MAX_COUNT
    n8n_version = $actualN8n
    node_version = $actualNode
    check_only = [bool]$CheckOnly
}

if ($CheckOnly) {
    $status['result'] = 'checked_only; running instance was not changed'
    $status | ConvertTo-Json -Compress
    exit 0
}

if ($portOpen) {
    throw "Port $port is already open. Stop n8n gracefully, then run this script so the retention settings reach the next process."
}

$status['result'] = 'launching n8n with 37.5-day automatic-pruning fallback'
$status | ConvertTo-Json -Compress
& $launcher start
