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
$names = @('AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_ARTIFACT_SIGNING_ENDPOINT',
    'AZURE_ARTIFACT_SIGNING_ACCOUNT_NAME', 'AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE_NAME',
    'WINDOWS_SIGNING_CERT_SUBJECT', 'TAURI_SIGNING_PRIVATE_KEY', 'GITHUB_REF_TYPE',
    'GITHUB_REF_NAME', 'GITHUB_REPOSITORY', 'RUNNER_TEMP', 'GITHUB_ENV',
    'ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'ACTIONS_ID_TOKEN_REQUEST_URL', 'AZURE_FEDERATED_TOKEN_FILE',
    'AZURE_AUTHORITY_HOST', 'TIPTUNE_TEST_NATIVE_CAPTURE', 'TIPTUNE_TEST_SIGN_RESULT')
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
    Assert (Test-TipTuneUpstreamFile (Join-Path $scratch 'wix/x64/wix/WixUIExtension.dll') $root) 'Upstream WiX UI extension must not be re-signed.'
    Assert (Test-TipTuneUpstreamFile (Join-Path $scratch 'wix/x64/wix/WixUtilExtension.dll') $root) 'Upstream WiX utility extension must not be re-signed.'
    Assert (-not (Test-TipTuneUpstreamFile (Join-Path $scratch 'wix/x64/wix/unexpected.dll') $root)) 'Unexpected WiX code must not be silently excluded.'

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
    $env:AZURE_TENANT_ID = ''
    Expect-Failure { Assert-TipTuneSigningConfiguration } 'Missing: AZURE_TENANT_ID'
    $env:AZURE_TENANT_ID = '11111111-1111-1111-1111-111111111111'
    $env:AZURE_CLIENT_ID = 'not-an-Azure-id'
    Expect-Failure { Assert-TipTuneSigningConfiguration } 'AZURE_CLIENT_ID must be'
    $env:AZURE_CLIENT_ID = '22222222-2222-2222-2222-222222222222'
    foreach ($endpoint in @('http://eus.codesigning.azure.net', 'https://evil.example', 'https://eus.codesigning.azure.net.evil.example', 'https://eus.codesigning.azure.net/unexpected')) {
        $env:AZURE_ARTIFACT_SIGNING_ENDPOINT = $endpoint
        Expect-Failure { Assert-TipTuneSigningConfiguration } 'regional endpoint'
    }
    $env:AZURE_ARTIFACT_SIGNING_ENDPOINT = 'https://eus.codesigning.azure.net'
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

    $fakeTools = Join-Path $scratch '.signing-tools/native/Fixture.1.0'
    New-Item -ItemType Directory -Path $fakeTools, (Join-Path $scratch 'scripts') -Force | Out-Null
    @(
        @{ tool='SignTool'; id='Fixture'; version='1.0'; path='sign.ps1' },
        @{ tool='Dlib'; id='Fixture'; version='1.0'; path='client.dll' }
    ) | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $scratch 'scripts/windows-signing-tools.json')
    '' | Set-Content -LiteralPath (Join-Path $fakeTools 'client.dll')
    @'
param([Parameter(ValueFromRemainingArguments)][string[]]$NativeArguments)
$metadataIndex = [Array]::IndexOf($NativeArguments, '/dmdf') + 1
@{
    arguments = $NativeArguments
    metadata = Get-Content -LiteralPath $NativeArguments[$metadataIndex] -Raw | ConvertFrom-Json
    token_file = $env:AZURE_FEDERATED_TOKEN_FILE
    assertion = Get-Content -LiteralPath $env:AZURE_FEDERATED_TOKEN_FILE -Raw
    authority = $env:AZURE_AUTHORITY_HOST
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $env:TIPTUNE_TEST_NATIVE_CAPTURE
$global:LASTEXITCODE = [int]$env:TIPTUNE_TEST_SIGN_RESULT
'@ | Set-Content -LiteralPath (Join-Path $fakeTools 'sign.ps1')
    # Preflight reloads the module. Mock the instance that owns the public signer.
    $module = (Get-Command Invoke-TipTuneAzureSigning).Module
    & $module {
        $script:requestCount = 0
        $script:emptyAssertion = $false
        function script:Invoke-RestMethod {
            param([string]$Uri, [hashtable]$Headers, [string]$Method, [int]$MaximumRedirection, [int]$TimeoutSec)
            $script:requestCount++
            $script:lastRequest = @{ uri=$Uri; authorization=$Headers.Authorization; redirects=$MaximumRedirection }
            return [pscustomobject]@{ value = $(if ($script:emptyAssertion) { '' } else { "fixture-assertion-$script:requestCount" }) }
        }
    }
    $env:ACTIONS_ID_TOKEN_REQUEST_TOKEN = ''
    Expect-Failure { Invoke-TipTuneAzureSigning $invalidExe $scratch } 'Missing GitHub OIDC setting'
    $env:ACTIONS_ID_TOKEN_REQUEST_TOKEN = 'fixture-request-credential'
    $env:ACTIONS_ID_TOKEN_REQUEST_URL = 'https://evil.example/token'
    Expect-Failure { Invoke-TipTuneAzureSigning $invalidExe $scratch } 'unexpected endpoint'
    $env:ACTIONS_ID_TOKEN_REQUEST_URL = 'https://vstoken.actions.githubusercontent.com/fixture?api-version=2.0'
    $env:AZURE_FEDERATED_TOKEN_FILE = 'prior-token-file'
    $env:AZURE_AUTHORITY_HOST = 'prior-authority'
    $env:TIPTUNE_TEST_NATIVE_CAPTURE = Join-Path $scratch 'native-call.json'
    $env:TIPTUNE_TEST_SIGN_RESULT = '0'
    Invoke-TipTuneAzureSigning $invalidExe $scratch
    $capture = Get-Content -LiteralPath $env:TIPTUNE_TEST_NATIVE_CAPTURE -Raw | ConvertFrom-Json
    Assert ($capture.arguments[-1] -ceq $invalidExe) 'The signer did not receive the exact staged file.'
    Assert ($capture.arguments -contains 'SHA256' -and $capture.arguments -contains 'http://timestamp.acs.microsoft.com') 'SHA256 and RFC3161 timestamp are required.'
    Assert ($capture.metadata.ExcludeCredentials -notcontains 'WorkloadIdentityCredential') 'Workload identity must remain enabled.'
    Assert ($capture.metadata.ExcludeCredentials.Count -eq 9) 'Unexpected credential fallback was enabled.'
    Assert ($capture.metadata.ExcludeCredentials -contains 'AzureCliCredential') 'Signing must not depend on a cached CLI login.'
    Assert ($capture.authority -ceq 'https://login.microsoftonline.com/') 'Unexpected token authority.'
    Assert (-not (Test-Path -LiteralPath $capture.token_file)) 'The OIDC assertion file was not deleted.'
    Assert ($env:AZURE_FEDERATED_TOKEN_FILE -ceq 'prior-token-file' -and $env:AZURE_AUTHORITY_HOST -ceq 'prior-authority') 'Credential environment was not restored.'
    $request = & $module { $script:lastRequest }
    Assert ($request.uri -match 'audience=api%3A%2F%2FAzureADTokenExchange') 'Incorrect OIDC token audience.'
    Assert ($request.authorization -ceq 'bearer fixture-request-credential' -and $request.redirects -eq 0) 'OIDC authorization or redirect protection changed.'
    Invoke-TipTuneAzureSigning $invalidExe $scratch
    $second = Get-Content -LiteralPath $env:TIPTUNE_TEST_NATIVE_CAPTURE -Raw | ConvertFrom-Json
    Assert ($second.assertion -cne $capture.assertion) 'A fresh assertion is required after long builds.'
    $env:TIPTUNE_TEST_SIGN_RESULT = '2'
    Expect-Failure { Invoke-TipTuneAzureSigning $invalidExe $scratch } 'SignTool failed'
    $failed = Get-Content -LiteralPath $env:TIPTUNE_TEST_NATIVE_CAPTURE -Raw | ConvertFrom-Json
    Assert (-not (Test-Path -LiteralPath $failed.token_file)) 'A failed signing call leaked its assertion file.'
    Assert ($env:AZURE_FEDERATED_TOKEN_FILE -ceq 'prior-token-file') 'A failed signing call changed the credential environment.'
    & $module { $script:emptyAssertion = $true }
    Expect-Failure { Invoke-TipTuneAzureSigning $invalidExe $scratch } 'returned no OIDC assertion'
    $federation = Get-Content -LiteralPath (Join-Path $root '.github/signing/azure-federation.json') -Raw | ConvertFrom-Json
    Assert ($federation.subject -ceq 'repo:rndmzd/TipTune:environment:windows-signing') 'Federation must be limited to the release environment.'
    Assert ($federation.issuer -ceq 'https://token.actions.githubusercontent.com' -and $federation.audiences[0] -ceq 'api://AzureADTokenExchange') 'Unexpected federation issuer or audience.'
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
