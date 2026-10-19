[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'windows-signing.psm1') -Force
$root = Split-Path $PSScriptRoot -Parent
Assert-TipTuneSigningConfiguration
$version = Get-TipTuneReleaseVersion $root
if ($env:GITHUB_REF_TYPE -ne 'tag' -or $env:GITHUB_REF_NAME -cne "v$version") {
    throw "Signed releases require the tag v$version."
}
# Never overwrite a published release during a retry.
$release = @(& gh api "repos/$env:GITHUB_REPOSITORY/releases/tags/v$version" 2>&1)
if ($LASTEXITCODE -eq 0) {
    if (-not (($release -join "`n") | ConvertFrom-Json).draft) {
        throw "v$version is already published. Bump the version and create a new tag."
    }
} elseif (($release -join "`n") -notmatch 'HTTP 404') {
    throw 'Unable to check the existing release. Check the GitHub token and connection.'
}
$tools = Join-Path $root '.signing-tools'
New-Item -ItemType Directory -Path $tools -Force | Out-Null
$signCommand = @{
    cmd = 'pwsh'
    args = @('-NoLogo', '-NoProfile', '-NonInteractive', '-File',
        (Join-Path $root 'scripts/windows-code-sign.ps1'), '-FilePath', '%1')
}
@{ build = @{ beforeBundleCommand = '' }; bundle = @{ windows = @{ signCommand = $signCommand } } } |
    ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $tools 'windows.conf.json') -Encoding utf8
"TIPTUNE_SIGNING_REPORT=$env:RUNNER_TEMP/tiptune-windows-signatures.jsonl" >> $env:GITHUB_ENV
Write-Host "Azure Artifact Signing configured for TipTune $version."
# The expected `gh api` 404 for a new tag must not fail the runner's pwsh wrapper.
exit 0
