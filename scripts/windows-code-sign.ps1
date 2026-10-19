[CmdletBinding()]
param([Parameter(Mandatory)][string]$FilePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'windows-signing.psm1') -Force
$root = Split-Path $PSScriptRoot -Parent
Assert-TipTuneSigningConfiguration
foreach ($name in @('ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RESULTS_URL', 'GITHUB_TOKEN', 'RUNNER_TEMP', 'TIPTUNE_SIGNING_REPORT')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Missing GitHub Actions runtime setting: $name. Run signing inside the release JavaScript action." }
}
$version = Get-TipTuneReleaseVersion $root
$file = Get-Item -LiteralPath $FilePath
if (Test-TipTuneUpstreamFile $file.FullName $root) {
    [pscustomobject]@{
        file = $file.Name
        status = 'upstream-unmodified'
        sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        signature_status = (Get-AuthenticodeSignature -LiteralPath $file.FullName).Status.ToString()
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:TIPTUNE_SIGNING_REPORT -Encoding utf8
    Write-Host "Preserving upstream binary without re-signing: $($file.Name)"
    exit 0
}
Assert-TipTuneFileMetadata $file.FullName $version
$existing = Get-AuthenticodeSignature -LiteralPath $file.FullName
if ($existing.Status -eq 'Valid') {
    Assert-TipTuneAuthenticode $file.FullName $env:WINDOWS_SIGNING_CERT_SUBJECT | Out-Null
    Write-Host "Already Windows-signed: $($file.Name)"
    exit 0
}
$originalHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
$requestRoot = Join-Path $env:RUNNER_TEMP ('tiptune-sign-' + [guid]::NewGuid().ToString('N'))
$unsigned = Join-Path $requestRoot 'unsigned'
$signed = Join-Path $requestRoot 'signed'
New-Item -ItemType Directory -Path $unsigned, $signed | Out-Null
$artifactName = if ($file.Extension -ieq '.tmp') { 'TipTune-uninstaller.exe' } else { $file.Name }
$inputPath = Join-Path $unsigned $artifactName
Copy-Item -LiteralPath $file.FullName -Destination $inputPath
$uploadOutput = Join-Path $requestRoot 'upload-output.txt'
Invoke-TipTuneSigningAction (Join-Path $root '.signing-tools/upload-artifact/dist/upload/index.js') @{
    name = ('sign-' + [guid]::NewGuid().ToString('N'))
    path = $inputPath
    'if-no-files-found' = 'error'
    'retention-days' = '1'
    'compression-level' = '0'
    'overwrite' = 'false'
    'include-hidden-files' = 'false'
} $uploadOutput
$artifactId = Get-TipTuneUploadedArtifactId $uploadOutput
Invoke-TipTuneSigningAction (Join-Path $root '.signing-tools/signpath/index.js') @{
    'connector-url' = 'https://pipelineconnector.connectors.signpath.io/GitHubActions/GitHubCom'
    'api-token' = $env:SIGNPATH_API_TOKEN
    'organization-id' = $env:SIGNPATH_ORGANIZATION_ID
    'project-slug' = $env:SIGNPATH_PROJECT_SLUG
    'signing-policy-slug' = $env:SIGNPATH_SIGNING_POLICY_SLUG
    'artifact-configuration-slug' = $env:SIGNPATH_ARTIFACT_CONFIGURATION_SLUG
    'github-artifact-id' = $artifactId
    'github-token' = $env:GITHUB_TOKEN
    'parameters' = ('version: "{0}"' -f $version)
    'wait-for-completion' = 'true'
    'wait-for-completion-timeout-in-seconds' = '1800'
    'service-unavailable-timeout-in-seconds' = '600'
    'download-signed-artifact-timeout-in-seconds' = '300'
    'output-artifact-directory' = $signed
    'skip-decompress' = 'false'
} (Join-Path $requestRoot 'signpath-output.txt')
$signedPath = Join-Path $signed $artifactName
Assert-TipTuneFileMetadata $signedPath $version
$evidence = Assert-TipTuneAuthenticode $signedPath $env:WINDOWS_SIGNING_CERT_SUBJECT
$evidence.file = $file.Name
if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -cne $originalHash) {
    throw "The input changed while signing $($file.Name); refusing to replace it."
}
Copy-Item -LiteralPath $signedPath -Destination $file.FullName -Force
Assert-TipTuneAuthenticode $file.FullName $env:WINDOWS_SIGNING_CERT_SUBJECT | Out-Null
$evidence | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:TIPTUNE_SIGNING_REPORT -Encoding utf8
Write-Host "Verified timestamped Windows signature: $($file.Name)"
