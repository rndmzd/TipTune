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
    $required = @('SIGNPATH_API_TOKEN', 'SIGNPATH_ORGANIZATION_ID', 'SIGNPATH_PROJECT_SLUG',
        'SIGNPATH_SIGNING_POLICY_SLUG', 'SIGNPATH_ARTIFACT_CONFIGURATION_SLUG',
        'WINDOWS_SIGNING_CERT_SUBJECT', 'TAURI_SIGNING_PRIVATE_KEY')
    $missing = @($required | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
    if ($missing.Count) { throw "Windows release signing is not configured. Missing: $($missing -join ', '). See docs/CODE_SIGNING.md." }
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

function Invoke-TipTuneSigningAction {
    param([string]$EntryPoint, [hashtable]$Inputs, [string]$OutputFile)
    # Run the pinned, bundled official JavaScript actions inside tauri-action's
    # inherited Actions runtime. Do not export its runtime token to GITHUB_ENV.
    $saved = @{}
    $overrides = @{ GITHUB_OUTPUT = $OutputFile }
    foreach ($key in $Inputs.Keys) { $overrides['INPUT_' + $key.ToUpperInvariant()] = [string]$Inputs[$key] }
    try {
        # @actions/core file commands require an existing output file.
        '' | Set-Content -LiteralPath $OutputFile -Encoding utf8
        foreach ($key in $overrides.Keys) {
            $saved[$key] = [Environment]::GetEnvironmentVariable($key)
            [Environment]::SetEnvironmentVariable($key, $overrides[$key])
        }
        & node $EntryPoint
        if ($LASTEXITCODE -ne 0) { throw "Signing action failed: $(Split-Path $EntryPoint -Leaf)." }
    } finally {
        foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key]) }
    }
}

function Get-TipTuneUploadedArtifactId {
    param([Parameter(Mandatory)][string]$OutputFile)
    $content = Get-Content -LiteralPath $OutputFile -Raw
    # @actions/core emits the multiline file-command form, even for numeric IDs.
    $pattern = '(?m)^artifact-id(?:=(?<id>\d+)[ \t]*\r?$|<<(?<delimiter>[^\r\n]+)\r?\n(?<id>\d+)\r?\n\k<delimiter>[ \t]*\r?$)'
    $id = [regex]::Match($content, $pattern).Groups['id'].Value
    if (-not $id) { throw 'The unsigned artifact upload did not return a numeric artifact ID.' }
    return $id
}

Export-ModuleMember -Function Get-TipTuneReleaseVersion, Assert-TipTuneFileMetadata,
    Assert-TipTuneAuthenticode, Assert-TipTuneSigningConfiguration, Invoke-TipTuneSigningAction,
    Test-TipTuneUpstreamFile, Get-TipTuneUploadedArtifactId
