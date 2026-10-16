$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$databasePath = Join-Path $projectRoot 'data\processed\semas_store_20260630.sqlite'
if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
    throw "Validated snapshot database is missing: $databasePath"
}

$expectedPython = (Get-Content -LiteralPath (Join-Path $projectRoot '.python-version') -Raw).Trim()
$pythonPrefixArgs = @()
$python = $null
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $python = [pscustomobject]@{ Source = $venvPython }
} else {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython -PathType Leaf) {
        $python = [pscustomobject]@{ Source = $bundledPython }
    }
    if (-not $python) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            try {
                $candidateVersion = & $launcher.Source -3.12 --version 2>$null
                $launcherExitCode = $LASTEXITCODE
            } catch {
                $candidateVersion = ''
                $launcherExitCode = 1
            }
            if ($launcherExitCode -eq 0 -and $candidateVersion -eq "Python $expectedPython") {
                $python = $launcher
                $pythonPrefixArgs = @('-3.12')
            }
        }
    }
}
if (-not $python) {
    throw "Python $expectedPython is required. Create .venv from the pinned interpreter or install that exact version."
}

$pythonVersion = (& $python.Source @pythonPrefixArgs --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne "Python $expectedPython") {
    throw "Python version mismatch: expected $expectedPython, detected $pythonVersion."
}

$secretDirectory = Join-Path $env:LOCALAPPDATA 'GapAnalysis'
$secretPath = Join-Path $secretDirectory 'analyzer-token.txt'
New-Item -ItemType Directory -Force -Path $secretDirectory | Out-Null
$newSecret = -not (Test-Path -LiteralPath $secretPath -PathType Leaf)
if ($newSecret) {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    [System.IO.File]::WriteAllText($secretPath, $token, [System.Text.Encoding]::ASCII)
} else {
    $token = [System.IO.File]::ReadAllText($secretPath, [System.Text.Encoding]::ASCII).Trim()
}

if ($token.Length -lt 32) {
    throw "The local analyzer token is invalid. Remove and regenerate: $secretPath"
}

$env:GAP_ANALYZER_TOKEN = $token
Write-Host "Starting local gap analyzer on 127.0.0.1:8765 using $pythonVersion."
if ($newSecret) {
    Write-Host 'A local token was generated. It was not printed to this terminal or stored in the project folder.'
    Write-Host 'Open the local token file yourself and copy its contents directly into an n8n HTTP Header Auth credential:'
    Write-Host 'Header name: Authorization'
    Write-Host "Header value format: Bearer <token from $secretPath>"
    Write-Host 'Do not paste the token into chat or a workflow export.'
}
Write-Host 'Keep this PowerShell window open while running the n8n workflow. Press Ctrl+C to stop.'

$servicePath = Join-Path $PSScriptRoot 'analyzer_service.py'
& $python.Source @pythonPrefixArgs $servicePath --db $databasePath --host 127.0.0.1 --port 8765
if ($LASTEXITCODE -ne 0) {
    throw "Analyzer service exited with code $LASTEXITCODE."
}
