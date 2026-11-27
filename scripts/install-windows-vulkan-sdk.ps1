# Install the pinned LunarG Vulkan SDK on a hosted Windows runner and verify
# the shader compiler and import library the Vulkan backend builds against.
# Mesh shipped its Windows Vulkan archives with SDK 1.4.328.1; Skippy pins
# exactly that installer and its SHA-256, as LunarG publishes it.
#
# Exports for later steps:
#   VULKAN_SDK        (GITHUB_ENV)
#   %VULKAN_SDK%\Bin  (GITHUB_PATH)

param(
    [string]$ExpectedVersion = '1.4.328.1',
    [string]$ExpectedSha256 = 'a8675df6d538079c2a719a9373994948091db785b48f142e024254e76348d16c',
    [string]$CacheDir = '',
    [string]$InstallRoot = ''
)

$ErrorActionPreference = 'Stop'

$installerName = "vulkansdk-windows-X64-$ExpectedVersion.exe"
$installerUrl = "https://sdk.lunarg.com/sdk/download/$ExpectedVersion/windows/$installerName"

function Set-StepEnv([string]$Name, [string]$Value) {
    if (-not $Name) { return }
    if ($env:GITHUB_ENV) {
        "${Name}=${Value}" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
    } else {
        Set-Item -Path "env:$Name" -Value $Value
    }
}

function Add-StepPath([string]$PathEntry) {
    if (-not $PathEntry) { return }
    if ($env:GITHUB_PATH) {
        $PathEntry | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
    } else {
        $env:PATH = "$PathEntry;$env:PATH"
    }
}

function Test-ExeHeader([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    $header = Get-Content -Path $Path -AsByteStream -TotalCount 2
    return $header.Length -eq 2 -and $header[0] -eq 0x4D -and $header[1] -eq 0x5A
}

function Test-VulkanSdk([string]$Root) {
    return (Test-Path (Join-Path $Root 'Bin\glslc.exe')) -and
        (Test-Path (Join-Path $Root 'Lib\vulkan-1.lib')) -and
        (Test-Path (Join-Path $Root 'Include\vulkan\vulkan.h'))
}

if (-not $CacheDir) {
    $CacheDir = Join-Path $env:RUNNER_TEMP 'skippy-vulkan-sdk'
}
if (-not $InstallRoot) {
    $InstallRoot = "C:\VulkanSDK\$ExpectedVersion"
}
New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null

if (Test-VulkanSdk $InstallRoot) {
    Write-Host "Vulkan SDK $ExpectedVersion is already installed at $InstallRoot"
} else {
    $installer = Join-Path $CacheDir $installerName
    if (Test-ExeHeader $installer) {
        Write-Host "Using cached Vulkan SDK installer $installerName"
    } else {
        Write-Host "Downloading $installerUrl"
        Invoke-WebRequest -Uri $installerUrl -OutFile $installer
    }
    if (-not (Test-ExeHeader $installer)) {
        throw "Vulkan SDK installer payload is not a Windows executable: $installer"
    }
    $actualSha256 = (Get-FileHash -Path $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Vulkan SDK installer SHA-256 mismatch: expected $ExpectedSha256, found $actualSha256"
    }
    # The LunarG installer is a Qt installer: these arguments run it
    # unattended with its default components, which include glslc.
    $arguments = @('--root', $InstallRoot, '--accept-licenses', '--default-answer',
        '--confirm-command', 'install')
    $process = Start-Process $installer -ArgumentList $arguments -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Vulkan SDK installer exited with code $($process.ExitCode)"
    }
}

if (-not (Test-VulkanSdk $InstallRoot)) {
    throw "Vulkan SDK at $InstallRoot lacks glslc.exe, vulkan-1.lib or vulkan.h"
}
$glslc = Join-Path $InstallRoot 'Bin\glslc.exe'
& $glslc --version
if ($LASTEXITCODE -ne 0) {
    throw "glslc at $glslc did not run"
}

Add-StepPath (Join-Path $InstallRoot 'Bin')
Set-StepEnv 'VULKAN_SDK' $InstallRoot
Write-Host "Vulkan SDK $ExpectedVersion ready at $InstallRoot"
