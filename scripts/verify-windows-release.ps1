[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'windows-signing.psm1') -Force
$root = Split-Path $PSScriptRoot -Parent
$version = Get-TipTuneReleaseVersion $root
if (-not $env:WINDOWS_SIGNING_CERT_SUBJECT) { throw 'WINDOWS_SIGNING_CERT_SUBJECT is required.' }
$sevenZipCommand = Get-Command 7z -ErrorAction SilentlyContinue
$sevenZip = if ($sevenZipCommand) { $sevenZipCommand.Source } else { 'C:/Program Files/7-Zip/7z.exe' }
if (-not (Test-Path -LiteralPath $sevenZip)) { throw '7-Zip is required to verify the actual installer payloads.' }
foreach ($kind in @('msi', 'nsis')) {
    $name = if ($kind -eq 'msi') { "TipTune_${version}_x64_en-US.msi" } else { "TipTune_${version}_x64-setup.exe" }
    $installer = Join-Path $root "src-tauri/target/release/bundle/$kind/$name"
    Assert-TipTuneFileMetadata $installer $version
    Assert-TipTuneAuthenticode $installer $env:WINDOWS_SIGNING_CERT_SUBJECT | Out-Null
    $destination = Join-Path $env:RUNNER_TEMP ('tiptune-verify-' + [guid]::NewGuid().ToString('N'))
    & $sevenZip x $installer "-o$destination" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Cannot extract $name." }
    # 7-Zip extracts MSI cabinet entries under their File-table identifiers,
    # including names without .exe. Inspect PE headers, not filename extensions.
    $ownFiles = @(Get-ChildItem -LiteralPath $destination -Recurse -File | Where-Object {
        $stream = [IO.File]::OpenRead($_.FullName)
        try { $isPe = ($stream.ReadByte() -eq 0x4D -and $stream.ReadByte() -eq 0x5A) }
        finally { $stream.Dispose() }
        $isPe -and $_.VersionInfo.ProductName -ceq 'TipTune'
    })
    if ($ownFiles.Count -lt 2 -or
        @($ownFiles | Where-Object { $_.VersionInfo.OriginalFilename -ceq 'TipTune.exe' }).Count -ne 1) {
        throw "$name must contain a signed desktop and exactly one signed backend."
    }
    $canonical = Join-Path $env:RUNNER_TEMP ('tiptune-pe-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $canonical | Out-Null
    foreach ($file in $ownFiles) {
        $verifyPath = Join-Path $canonical ($file.Name + '.exe')
        Copy-Item -LiteralPath $file.FullName -Destination $verifyPath
        Assert-TipTuneFileMetadata $verifyPath $version
        Assert-TipTuneAuthenticode $verifyPath $env:WINDOWS_SIGNING_CERT_SUBJECT | Out-Null
    }
    & python (Join-Path $PSScriptRoot 'check-bundle-metadata.py') --installer $installer --expected $kind --seven-zip $sevenZip
    if ($LASTEXITCODE -ne 0) { throw "Invalid updater bundle type in $name." }
    if (-not (Test-Path -LiteralPath ($installer + '.sig'))) { throw "Missing Tauri updater signature for $name." }
    Write-Host "Verified signed installer and TipTune payloads: $name"
}
