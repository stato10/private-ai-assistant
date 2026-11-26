param([switch]$UseSystemPython)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$python = if ($UseSystemPython) { 'python' } else { '.venv/Scripts/python.exe' }
if (-not $env:GLASSOS_TOKEN) {
    $secret = Read-Host 'Local Assistant access token' -AsSecureString
    $env:GLASSOS_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
}
try {
    & $python -m uvicorn glassos.app:app --host 127.0.0.1 --port 8765 --no-access-log --no-proxy-headers
    if ($LASTEXITCODE -ne 0) { throw "Uvicorn exited with code $LASTEXITCODE" }
} finally {
    Remove-Item Env:GLASSOS_TOKEN -ErrorAction SilentlyContinue
}
