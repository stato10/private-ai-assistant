param([ValidateSet('serve', 'pull', 'ps')][string]$Action = 'serve')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$portable = Join-Path $root '.runtime/ollama/ollama.exe'
$binary = if (Test-Path -LiteralPath $portable) { $portable } else { (Get-Command ollama.exe).Source }
$port = if ($env:GLASSOS_OLLAMA_PORT) { $env:GLASSOS_OLLAMA_PORT } else { '11434' }
$env:OLLAMA_HOST = "127.0.0.1:$port"
$env:OLLAMA_MODELS = Join-Path $root '.runtime/models'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
if ($Action -eq 'pull') {
    & $binary pull qwen3.5:4b
} else {
    & $binary $Action
}
exit $LASTEXITCODE
