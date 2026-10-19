[CmdletBinding()]
param([Parameter(Mandatory)][string]$FilePath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'windows-signing.psm1') -Force
$root = Split-Path $PSScriptRoot -Parent
Assert-TipTuneSigningConfiguration
foreach ($name in @('RUNNER_TEMP', 'TIPTUNE_SIGNING_REPORT')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Missing release setting: $name." }
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
New-Item -ItemType Directory -Path $unsigned | Out-Null
$artifactName = if ($file.Extension -ieq '.tmp') { 'TipTune-uninstaller.exe' } else { $file.Name }
$inputPath = Join-Path $unsigned $artifactName
Copy-Item -LiteralPath $file.FullName -Destination $inputPath
Invoke-TipTuneAzureSigning -Path $inputPath -Root $root
$signedPath = $inputPath
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
