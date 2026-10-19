Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-TipTuneReleaseVersion {
    param([Parameter(Mandatory)][string]$Root)
    $package = Get-Content -LiteralPath (Join-Path $Root 'package.json') -Raw | ConvertFrom-Json
    $lock = Get-Content -LiteralPath (Join-Path $Root 'package-lock.json') -Raw | ConvertFrom-Json -AsHashtable
    $config = Get-Content -LiteralPath (Join-Path $Root 'src-tauri/tauri.conf.json') -Raw | ConvertFrom-Json
    $cargo = Get-Content -LiteralPath (Join-Path $Root 'src-tauri/Cargo.toml') -Raw
    $cargoLock = Get-Content -LiteralPath (Join-Path $Root 'src-tauri/Cargo.lock') -Raw
    $versions = @(
        $package.version, $lock['version'], $lock['packages']['']['version'],
        $config.version, [regex]::Match($cargo, '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value,
        [regex]::Match($cargoLock, '(?ms)\[\[package\]\]\s*name = "tiptune-tauri"\s*version = "([^"]+)"').Groups[1].Value
    )
    if ($package.version -notmatch '^\d+\.\d+\.\d+$' -or @($versions | Where-Object { $_ -cne $package.version }).Count) {
        throw 'The six release version fields must contain the same stable x.y.z version.'
    }
    return [string]$package.version
}

function Get-TipTuneMsiProperty {
    param([string]$Path, [ValidateSet('ProductName', 'ProductVersion')][string]$Property)
    $installer = $database = $view = $record = $null
    try {
        $installer = New-Object -ComObject WindowsInstaller.Installer
        $database = $installer.OpenDatabase($Path, 0)
        $view = $database.OpenView(('SELECT `Value` FROM `Property` WHERE `Property` = ''{0}''' -f $Property))
        $view.Execute()
        $record = $view.Fetch()
        return [string]$record.StringData(1)
    } finally {
        foreach ($item in @($record, $view, $database, $installer)) {
            if ($null -ne $item) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($item) }
        }
    }
}

function Assert-TipTuneFileMetadata {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Version)
    $file = Get-Item -LiteralPath $Path
    if ($file.Extension -ieq '.msi') {
        if ((Get-TipTuneMsiProperty $file.FullName 'ProductName') -cne 'TipTune' -or
            (Get-TipTuneMsiProperty $file.FullName 'ProductVersion') -cne $Version) {
            throw "Refusing to sign $($file.Name): MSI product/version does not match TipTune $Version."
        }
        return
    }
    # NSIS passes a PE uninstaller with a .tmp name to !uninstfinalize.
    $stream = [IO.File]::OpenRead($file.FullName)
    try { $isPe = ($stream.ReadByte() -eq 0x4D -and $stream.ReadByte() -eq 0x5A) }
    finally { $stream.Dispose() }
    if (-not $isPe) { throw "Refusing to sign $($file.Name): not a PE executable or MSI installer." }
    $info = $file.VersionInfo
    if ($info.ProductName -cne 'TipTune' -or $info.ProductVersion -cne $Version -or $info.FileVersion -cne $Version) {
        throw "Refusing to sign $($file.Name): expected TipTune product and file version $Version."
    }
}

function Assert-TipTuneAuthenticode {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$ExpectedSubject)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
        throw "Invalid Windows signature on $(Split-Path $Path -Leaf): $($signature.Status)."
    }
    if ($signature.SignerCertificate.Subject -cne $ExpectedSubject) {
        throw "Unexpected signing identity on $(Split-Path $Path -Leaf)."
    }
    if ($null -eq $signature.TimeStamperCertificate) {
        throw "Missing timestamp on $(Split-Path $Path -Leaf)."
    }
    return [pscustomobject]@{
        file = (Split-Path $Path -Leaf)
        subject = $signature.SignerCertificate.Subject
        thumbprint = $signature.SignerCertificate.Thumbprint
        timestamp_subject = $signature.TimeStamperCertificate.Subject
        sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    }
}

function Assert-TipTuneSigningConfiguration {
    $required = @('AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_ARTIFACT_SIGNING_ENDPOINT',
        'AZURE_ARTIFACT_SIGNING_ACCOUNT_NAME', 'AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE_NAME',
        'WINDOWS_SIGNING_CERT_SUBJECT', 'TAURI_SIGNING_PRIVATE_KEY')
    $missing = @($required | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
    if ($missing.Count) { throw "Windows release signing is not configured. Missing: $($missing -join ', '). See docs/CODE_SIGNING.md." }
    foreach ($name in @('AZURE_TENANT_ID', 'AZURE_CLIENT_ID')) {
        $id = [guid]::Empty
        if (-not [guid]::TryParse([Environment]::GetEnvironmentVariable($name), [ref]$id) -or $id -eq [guid]::Empty) {
            throw "$name must be a nonempty Azure GUID."
        }
    }
    if ($env:AZURE_ARTIFACT_SIGNING_ENDPOINT -cnotmatch '^https://[a-z0-9]+\.codesigning\.azure\.net/?$') {
        throw 'The Azure signing endpoint must be an HTTPS codesigning.azure.net regional endpoint.'
    }
}

function Test-TipTuneUpstreamFile {
    param([string]$Path, [string]$Root)
    $full = [IO.Path]::GetFullPath($Path).Replace('\', '/')
    $mediaRoot = [IO.Path]::GetFullPath((Join-Path $Root 'src-tauri/resources/bin/windows')).Replace('\', '/') + '/'
    $leaf = [IO.Path]::GetFileName($full)
    if ($full.StartsWith($mediaRoot, [StringComparison]::OrdinalIgnoreCase) -and
        $leaf -in @('ffmpeg.exe', 'ffprobe.exe', 'yt-dlp.exe', 'deno.exe')) { return $true }
    # Tauri copies these upstream NSIS plugins before invoking the signing hook.
    if ($full -match '/x86-unicode/(additional/)?(NSISdl|StartMenu|System|nsDialogs|nsis_tauri_utils)\.dll$') { return $true }
    return $full -match '/wix/(x64|x86|arm64)/wix/(WixUIExtension|WixUtilExtension)\.dll$'
}

function Get-TipTuneSigningToolPaths {
    param([Parameter(Mandatory)][string]$Root)
    $packages = Get-Content -LiteralPath (Join-Path $Root 'scripts/windows-signing-tools.json') -Raw | ConvertFrom-Json
    $paths = @{}
    foreach ($package in $packages) {
        $paths[$package.tool] = Join-Path $Root ".signing-tools/native/$($package.id).$($package.version)/$($package.path)"
    }
    return $paths
}

function Invoke-TipTuneAzureSigning {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Root)
    Assert-TipTuneSigningConfiguration
    foreach ($name in @('ACTIONS_ID_TOKEN_REQUEST_TOKEN', 'ACTIONS_ID_TOKEN_REQUEST_URL', 'RUNNER_TEMP')) {
        if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Missing GitHub OIDC setting: $name. The release job needs id-token: write." }
    }
    $requestUri = [uri]$env:ACTIONS_ID_TOKEN_REQUEST_URL
    if ($requestUri.Scheme -cne 'https' -or $requestUri.Host -notlike '*.actions.githubusercontent.com' -or
        -not $requestUri.IsDefaultPort -or $requestUri.UserInfo) {
        throw 'Refusing to send the GitHub OIDC request credential to an unexpected endpoint.'
    }
    $paths = Get-TipTuneSigningToolPaths $Root
    foreach ($tool in $paths.Values) {
        if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw 'Run scripts/setup-windows-signing-tools.ps1 before signing.' }
    }
    $requestRoot = Join-Path $env:RUNNER_TEMP ('tiptune-azure-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $requestRoot | Out-Null
    $tokenPath = Join-Path $requestRoot 'oidc-token'
    $metadataPath = Join-Path $requestRoot 'metadata.json'
    $savedTokenPath = $env:AZURE_FEDERATED_TOKEN_FILE
    $savedAuthority = $env:AZURE_AUTHORITY_HOST
    try {
        # Obtain a fresh assertion for every callback, including after a long Rust
        # build. The Azure client exchanges it through WorkloadIdentityCredential.
        $separator = if ($requestUri.Query) { '&' } else { '?' }
        $audience = [uri]::EscapeDataString('api://AzureADTokenExchange')
        $response = Invoke-RestMethod -Uri ($requestUri.AbsoluteUri + $separator + 'audience=' + $audience) -Method Get `
            -Headers @{ Authorization = "bearer $env:ACTIONS_ID_TOKEN_REQUEST_TOKEN" } -MaximumRedirection 0 -TimeoutSec 30
        if ($response.value -isnot [string] -or [string]::IsNullOrWhiteSpace($response.value)) { throw 'GitHub returned no OIDC assertion.' }
        [IO.File]::WriteAllText($tokenPath, $response.value)
        $env:AZURE_FEDERATED_TOKEN_FILE = $tokenPath
        $env:AZURE_AUTHORITY_HOST = 'https://login.microsoftonline.com/'
        @{
            Endpoint = $env:AZURE_ARTIFACT_SIGNING_ENDPOINT
            CodeSigningAccountName = $env:AZURE_ARTIFACT_SIGNING_ACCOUNT_NAME
            CertificateProfileName = $env:AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE_NAME
            ExcludeCredentials = @('EnvironmentCredential', 'ManagedIdentityCredential', 'SharedTokenCacheCredential',
                'VisualStudioCredential', 'VisualStudioCodeCredential', 'AzureCliCredential', 'AzurePowerShellCredential',
                'AzureDeveloperCliCredential', 'InteractiveBrowserCredential')
        } | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding utf8
        & $paths.SignTool sign /fd SHA256 /tr 'http://timestamp.acs.microsoft.com' /td SHA256 `
            /dlib $paths.Dlib /dmdf $metadataPath $Path
        if ($LASTEXITCODE -ne 0) { throw "Azure SignTool failed with exit code $LASTEXITCODE." }
    } finally {
        $env:AZURE_FEDERATED_TOKEN_FILE = $savedTokenPath
        $env:AZURE_AUTHORITY_HOST = $savedAuthority
        if (Test-Path -LiteralPath $tokenPath) { Remove-Item -LiteralPath $tokenPath -Force }
    }
}

Export-ModuleMember -Function Get-TipTuneReleaseVersion, Assert-TipTuneFileMetadata,
    Assert-TipTuneAuthenticode, Assert-TipTuneSigningConfiguration, Get-TipTuneSigningToolPaths,
    Test-TipTuneUpstreamFile, Invoke-TipTuneAzureSigning
