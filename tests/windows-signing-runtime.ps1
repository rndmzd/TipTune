# Exercise the actual pinned Microsoft tool packages without cloud credentials.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Import-Module (Join-Path $root 'scripts/windows-signing.psm1') -Force
$paths = Get-TipTuneSigningToolPaths $root
& $paths.SignTool /? | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'The installed Microsoft SignTool could not start.' }
$runtimes = @(& dotnet --list-runtimes)
if ($LASTEXITCODE -ne 0 -or -not ($runtimes -match '^Microsoft.NETCore.App 8\.')) { throw '.NET 8 is required by the Azure signing client.' }
# Detect missing native dependencies without invoking signing or contacting Azure.
$handle = [Runtime.InteropServices.NativeLibrary]::Load($paths.Dlib)
try {
    if ($handle -eq [IntPtr]::Zero) { throw 'The Azure signing plug-in could not load.' }
} finally { [Runtime.InteropServices.NativeLibrary]::Free($handle) }
Write-Host 'Verified the Microsoft SignTool, Azure signing plug-in, and .NET 8 runtime. No cloud signing request was made.'
exit 0
