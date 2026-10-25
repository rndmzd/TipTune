[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$destination = Join-Path $root '.signing-tools/native'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$packages = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'windows-signing-tools.json') -Raw | ConvertFrom-Json
foreach ($package in $packages) {
    $id = $package.id.ToLowerInvariant()
    $version = $package.version
    $archive = Join-Path $destination "$id.$version.zip"
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/$id/$version/$id.$version.nupkg" -OutFile $archive
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -cne $package.sha256) {
        throw "Unexpected package hash for $id $version."
    }
    $packageRoot = Join-Path $destination "$($package.id).$version"
    Expand-Archive -LiteralPath $archive -DestinationPath $packageRoot -Force
    $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $packageRoot $package.path)
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -cne 'Microsoft Corporation') {
        throw "The signing tool in $id is not validly Microsoft signed."
    }
    Write-Host "Verified Microsoft signing tool: $($package.id) $version"
}
