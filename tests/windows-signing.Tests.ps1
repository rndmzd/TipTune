# Runs without a provider account or modifying the Windows certificate stores.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Import-Module (Join-Path $root 'scripts/windows-signing.psm1') -Force
$module = Get-Module windows-signing
$checks = 0
function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
}
function Expect-Failure([scriptblock]$Action, [string]$Pattern) {
    try { & $Action } catch {
        Assert ($_.Exception.Message -match $Pattern) "Unexpected failure: $($_.Exception.Message)"
        return
    }
    throw "Expected failure matching $Pattern"
}
$scratch = Join-Path $root ('.signing-tools/test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $scratch 'src-tauri') -Force | Out-Null
$savedEnvironment = @{}
$names = @('SIGNPATH_API_TOKEN', 'SIGNPATH_ORGANIZATION_ID', 'SIGNPATH_PROJECT_SLUG',
    'SIGNPATH_SIGNING_POLICY_SLUG', 'SIGNPATH_ARTIFACT_CONFIGURATION_SLUG',
    'WINDOWS_SIGNING_CERT_SUBJECT', 'TAURI_SIGNING_PRIVATE_KEY', 'GITHUB_REF_TYPE',
    'GITHUB_REF_NAME', 'GITHUB_REPOSITORY', 'RUNNER_TEMP', 'GITHUB_ENV', 'GITHUB_OUTPUT', 'INPUT_API-TOKEN')
foreach ($name in $names) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name) }
try {
    foreach ($relative in @('package.json', 'package-lock.json', 'src-tauri/tauri.conf.json', 'src-tauri/Cargo.toml', 'src-tauri/Cargo.lock')) {
        Copy-Item -LiteralPath (Join-Path $root $relative) -Destination (Join-Path $scratch $relative)
    }
    $version = Get-TipTuneReleaseVersion $root
    Assert ((Get-TipTuneReleaseVersion $scratch) -ceq $version) 'Matching release versions were rejected.'
    foreach ($relative in @('package.json', 'package-lock.json', 'src-tauri/tauri.conf.json', 'src-tauri/Cargo.toml', 'src-tauri/Cargo.lock')) {
        $path = Join-Path $scratch $relative
        $original = Get-Content -LiteralPath $path -Raw
        $original.Replace($version, '9.9.9') | Set-Content -LiteralPath $path
        Expect-Failure { Get-TipTuneReleaseVersion $scratch } 'six release version'
        $original | Set-Content -LiteralPath $path
    }
    $invalidExe = Join-Path $scratch 'untrusted.exe'
    'not a TipTune executable' | Set-Content -LiteralPath $invalidExe
    Expect-Failure { Assert-TipTuneFileMetadata $invalidExe $version } 'Refusing to sign'
    Expect-Failure { Assert-TipTuneAuthenticode $invalidExe 'CN=Expected' } 'Invalid Windows signature'
    foreach ($media in @('ffmpeg.exe', 'ffprobe.exe', 'yt-dlp.exe', 'deno.exe')) {
        Assert (Test-TipTuneUpstreamFile (Join-Path $root "src-tauri/resources/bin/windows/$media") $root) "Expected upstream media exclusion: $media"
        Assert (-not (Test-TipTuneUpstreamFile (Join-Path $root $media) $root)) 'Upstream exclusion escaped its resource directory.'
    }
    Assert (Test-TipTuneUpstreamFile (Join-Path $scratch 'x86-unicode/additional/nsis_tauri_utils.dll') $root) 'NSIS upstream plugin must not be re-signed.'
    Assert (-not (Test-TipTuneUpstreamFile (Join-Path $scratch 'x86-unicode/unexpected.dll') $root)) 'Unexpected plugin must not be silently excluded.'

    # Exercise trusted signature, wrong identity, and missing timestamp decisions
    # without adding a self-signed certificate to the host's trust store.
    & $module {
        function script:Get-AuthenticodeSignature {
            param([string]$LiteralPath)
            return $script:testSignature
        }
        $script:testSignature = [pscustomobject]@{
            Status = 'Valid'
            SignerCertificate = [pscustomobject]@{ Subject = 'CN=Expected'; Thumbprint = 'fixture' }
            TimeStamperCertificate = [pscustomobject]@{ Subject = 'CN=Timestamp' }
        }
    }
    Assert ((Assert-TipTuneAuthenticode $invalidExe 'CN=Expected').subject -ceq 'CN=Expected') 'Valid trusted signature decision failed.'
    Expect-Failure { Assert-TipTuneAuthenticode $invalidExe 'CN=Other' } 'Unexpected signing identity'
    & $module { $script:testSignature.TimeStamperCertificate = $null }
    Expect-Failure { Assert-TipTuneAuthenticode $invalidExe 'CN=Expected' } 'Missing timestamp'
    & $module { $script:testSignature.Status = 'HashMismatch' }
    Expect-Failure { Assert-TipTuneAuthenticode $invalidExe 'CN=Expected' } 'Invalid Windows signature'

    foreach ($name in $names[0..6]) { [Environment]::SetEnvironmentVariable($name, 'fixture-only') }
    $env:SIGNPATH_API_TOKEN = ''
    Expect-Failure { Assert-TipTuneSigningConfiguration } 'Missing: SIGNPATH_API_TOKEN'
    $env:SIGNPATH_API_TOKEN = 'fixture-only'
    $env:GITHUB_REF_TYPE = 'tag'
    $env:GITHUB_REF_NAME = "v$version"
    $env:GITHUB_REPOSITORY = 'rndmzd/TipTune'
    $env:RUNNER_TEMP = $scratch
    $env:GITHUB_ENV = Join-Path $scratch 'environment.txt'
    function global:gh { $global:LASTEXITCODE = 0; '{"draft":false}' }
    Expect-Failure { & (Join-Path $root 'scripts/prepare-windows-signing.ps1') } 'already published'
    function global:gh { $global:LASTEXITCODE = 0; '{"draft":true}' }
    & (Join-Path $root 'scripts/prepare-windows-signing.ps1')
    $overlay = Get-Content -LiteralPath (Join-Path $root '.signing-tools/windows.conf.json') -Raw | ConvertFrom-Json
    Assert ($overlay.build.beforeBundleCommand -ceq '') 'Release must not rebuild the signed sidecar.'
    Assert ([IO.Path]::IsPathRooted($overlay.bundle.windows.signCommand.args[4])) 'Uninstaller signer path must be absolute.'
    Assert ($overlay.bundle.windows.signCommand.args[-1] -ceq '%1') 'Tauri must pass the actual patched file to sign.'
    function global:gh { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
    & (Join-Path $root 'scripts/prepare-windows-signing.ps1')
    Assert ($LASTEXITCODE -eq 0) 'Expected 404 must return success to the Actions pwsh wrapper.'
    function global:gh { $global:LASTEXITCODE = 1; 'gh: server error (HTTP 500)' }
    Expect-Failure { & (Join-Path $root 'scripts/prepare-windows-signing.ps1') } 'Unable to check'
    $env:GITHUB_REF_NAME = 'v9.9.9'
    Expect-Failure { & (Join-Path $root 'scripts/prepare-windows-signing.ps1') } 'require the tag'

    $fakeAction = Join-Path $scratch 'action.cjs'
    @'
const fs = require('node:fs');
if (process.env['INPUT_API-TOKEN'] !== 'fixture-only') process.exit(2);
fs.appendFileSync(process.env.GITHUB_OUTPUT, 'artifact-id=12345\n');
'@ | Set-Content -LiteralPath $fakeAction
    [Environment]::SetEnvironmentVariable('INPUT_API-TOKEN', 'prior-value')
    $env:GITHUB_OUTPUT = 'prior-output'
    $output = Join-Path $scratch 'output.txt'
    Invoke-TipTuneSigningAction $fakeAction @{ 'api-token' = 'fixture-only' } $output
    Assert ((Get-Content -LiteralPath $output -Raw) -match 'artifact-id=12345') 'Action output was not isolated and captured.'
    Assert ([Environment]::GetEnvironmentVariable('INPUT_API-TOKEN') -ceq 'prior-value') 'Action token leaked into subsequent calls.'
    Assert ($env:GITHUB_OUTPUT -ceq 'prior-output') 'Action output path was not restored.'
    Expect-Failure { Invoke-TipTuneSigningAction $fakeAction @{ 'api-token' = 'wrong-fixture' } $output } 'Signing action failed'
    Assert ($env:GITHUB_OUTPUT -ceq 'prior-output') 'Failed action did not restore its environment.'
    Write-Host "Passed $checks Windows signing checks. No provider request or certificate-store change was made."
} finally {
    Remove-Item Function:\gh -ErrorAction SilentlyContinue
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name]) }
    Remove-Module windows-signing -ErrorAction SilentlyContinue
    # Only remove the test's own resolved directory inside this checkout.
    $resolvedScratch = [IO.Path]::GetFullPath($scratch)
    $allowedParent = [IO.Path]::GetFullPath((Join-Path $root '.signing-tools')) + [IO.Path]::DirectorySeparatorChar
    if ($resolvedScratch.StartsWith($allowedParent, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
    }
}
# Negative child-process tests intentionally set LASTEXITCODE. The Actions pwsh
# wrapper propagates it even after the assertion catches the expected failure.
exit 0
