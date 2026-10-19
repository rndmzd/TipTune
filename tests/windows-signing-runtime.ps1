# Verify nested upload-artifact from a JavaScript action's real runner context.
# This uploads only a small text fixture; it never calls a signing provider.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Import-Module (Join-Path $root 'scripts/windows-signing.psm1') -Force
foreach ($name in @('ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RESULTS_URL', 'RUNNER_TEMP')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Missing inherited runtime setting: $name" }
}
$fixture = Join-Path $env:RUNNER_TEMP 'tiptune-signing-runtime.txt'
'TipTune signing runtime fixture; not a release artifact.' | Set-Content -LiteralPath $fixture
$output = Join-Path $env:RUNNER_TEMP 'tiptune-signing-runtime-output.txt'
Invoke-TipTuneSigningAction (Join-Path $root '.signing-tools/upload-artifact/dist/upload/index.js') @{
    name = 'signing-runtime-fixture'
    path = $fixture
    'if-no-files-found' = 'error'
    'retention-days' = '1'
    'compression-level' = '0'
    'overwrite' = 'false'
    'include-hidden-files' = 'false'
} $output
if ((Get-Content -LiteralPath $output -Raw) -notmatch '(?m)^artifact-id=\d+') {
    throw 'Nested upload did not return a GitHub artifact ID.'
}
Write-Host 'Verified the nested artifact upload in the real GitHub Actions runtime.'
