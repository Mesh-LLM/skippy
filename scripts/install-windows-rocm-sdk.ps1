# Install the pinned AMD HIP SDK on a hosted Windows runner and verify the
# compiler, import libraries, and rocBLAS/hipBLASLt kernels that Skippy
# redistributes. Ported from the Mesh CI installer that shipped the Windows
# ROCm archives; Skippy pins exactly one installer and its SHA-256.
#
# Exports for later steps:
#   ROCM_PATH, HIP_PATH, HIPCC, HIPCXX  (GITHUB_ENV)
#   %ROCM_PATH%\bin, %ROCM_PATH%\llvm\bin  (GITHUB_PATH)

param(
    [string]$HipSdkFilename = 'AMD-Software-PRO-Edition-26.Q3-Win11-For-HIP.exe',
    [string]$ExpectedSha256 = '24bc203a10d731c35d3130a652ee2e83097d5d90b3d296810e5bce6745bd8b57',
    [string]$ExpectedVersion = '7.2.0',
    [string]$CacheDir = ''
)

$ErrorActionPreference = 'Stop'

$installerUrl = "https://download.amd.com/developer/eula/rocm-hub/$HipSdkFilename"

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

# The installer writes ROCM_PATH/HIP_PATH machine environment variables; the
# runner refreshes machine environment between steps, but resolve the install
# root from the filesystem so this script also works inside one step.
function Resolve-RocmInstallRoot {
    $candidates = @()
    if ($env:ROCM_PATH -and (Test-Path $env:ROCM_PATH)) {
        $candidates += $env:ROCM_PATH
    }
    if ($env:ProgramFiles) {
        foreach ($base in @((Join-Path $env:ProgramFiles 'AMD\ROCm'), (Join-Path $env:ProgramFiles 'AMD\HIP'))) {
            if (Test-Path $base) { $candidates += $base }
        }
    }

    foreach ($base in $candidates) {
        if (Test-Path (Join-Path $base 'lib\cmake\hip\hip-config.cmake')) {
            return $base
        }
        $versioned = Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName 'lib\cmake\hip\hip-config.cmake') } |
            Sort-Object Name -Descending |
            Select-Object -ExpandProperty FullName -First 1
        if ($versioned) { return $versioned }
    }
    return $null
}

function Test-RocmVersion([string]$Found, [string]$Expected) {
    if (-not $Found) { return $false }
    $found = $Found.Trim().Split('-')[0]
    $expected = $Expected.Trim()
    if ($found -eq $expected) { return $true }
    # The Windows HIP SDK installs into a two-segment versioned root (7.2)
    # for a three-segment pin (7.2.0): a zero patch is implied. A pin with a
    # nonzero patch never matches the short form.
    $expectedParts = $expected.Split('.')
    $foundParts = $found.Split('.')
    return ($foundParts.Count -eq 2 -and $expectedParts.Count -eq 3 -and
        $expectedParts[2] -eq '0' -and
        $foundParts[0] -eq $expectedParts[0] -and $foundParts[1] -eq $expectedParts[1])
}

function Resolve-RocmVersion([string]$Root) {
    $info = Join-Path $Root '.info\version'
    if (Test-Path $info) {
        return (Get-Content $info -Raw).Trim().Split('-')[0]
    }
    if ($Root -match '\\ROCm\\(\d+\.\d+(?:\.\d+)?)$' -or $Root -match '\\HIP\\(\d+\.\d+(?:\.\d+)?)$') {
        return $Matches[1]
    }
    return $null
}

if (-not $CacheDir) {
    $CacheDir = Join-Path $env:RUNNER_TEMP 'skippy-rocm-sdk'
}
New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null

$installer = Join-Path $CacheDir $HipSdkFilename
if (Test-ExeHeader $installer) {
    Write-Host "Using cached HIP SDK installer $HipSdkFilename"
} else {
    Write-Host "Downloading $installerUrl"
    Invoke-WebRequest -Uri $installerUrl -OutFile $installer
}

if (-not (Test-ExeHeader $installer)) {
    throw "HIP SDK installer payload is not a Windows executable: $installer"
}
$actualSha256 = (Get-FileHash -Path $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
    throw "HIP SDK installer SHA-256 mismatch: expected $ExpectedSha256, found $actualSha256"
}

$existing = Resolve-RocmInstallRoot
if ($existing -and (Test-RocmVersion (Resolve-RocmVersion $existing) $ExpectedVersion)) {
    Write-Host "HIP SDK $ExpectedVersion is already installed at $existing"
} else {
    $installLog = Join-Path $env:RUNNER_TEMP 'hip-sdk-install.log'
    $process = Start-Process $installer -ArgumentList '-install', '-log', $installLog -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "HIP SDK installer exited with code $($process.ExitCode). See $installLog"
    }
}

$rocmRoot = Resolve-RocmInstallRoot
if (-not $rocmRoot) {
    throw 'HIP SDK install completed, but no ROCm root with lib\cmake\hip\hip-config.cmake was found'
}

$version = Resolve-RocmVersion $rocmRoot
if (-not (Test-RocmVersion $version $ExpectedVersion)) {
    throw "HIP SDK at $rocmRoot reports version '$version', expected $ExpectedVersion"
}

foreach ($library in @('amdhip64.lib', 'rocblas.lib', 'hipblas.lib')) {
    $found = Get-ChildItem -Path $rocmRoot -Recurse -Filter $library -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $found) {
        throw "Expected HIP SDK import library was not found under ${rocmRoot}: $library"
    }
    Write-Host "Found $library at $($found.FullName)"
}

foreach ($kernels in @('rocblas', 'hipblaslt')) {
    $kernelDir = Join-Path $rocmRoot "bin\$kernels\library"
    if (-not (Test-Path $kernelDir)) {
        throw "HIP SDK $kernels kernels were not found at $kernelDir"
    }
}

$compilerRoots = @()
$hipConfigTool = Get-Command hipconfig -ErrorAction SilentlyContinue
if ($hipConfigTool) {
    try {
        $hipConfigRoot = (& $hipConfigTool.Source -l | Select-Object -First 1)
        if ($hipConfigRoot) { $compilerRoots += $hipConfigRoot }
    } catch {
    }
}
$compilerRoots += @(
    (Join-Path $rocmRoot 'llvm\bin'),
    (Join-Path $rocmRoot 'bin')
)
$compilerRoots = $compilerRoots | Where-Object { $_ } | Select-Object -Unique

$hipcc = $null
$hipcxx = $null
foreach ($root in $compilerRoots) {
    if (-not $hipcc -and (Test-Path (Join-Path $root 'clang.exe'))) {
        $hipcc = Join-Path $root 'clang.exe'
    }
    if (-not $hipcxx -and (Test-Path (Join-Path $root 'clang++.exe'))) {
        $hipcxx = Join-Path $root 'clang++.exe'
    }
}

if (-not $hipcc -or -not $hipcxx) {
    Write-Host 'Compiler search found no clang pair in:'
    foreach ($root in $compilerRoots) {
        Write-Host "  $root"
        Get-ChildItem $root -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match 'clang|hipcc|hipconfig' } |
            ForEach-Object { Write-Host "    $($_.Name)" }
    }
    Write-Host 'clang/hipcc-like files anywhere under the SDK root (depth 3):'
    Get-ChildItem $rocmRoot -Recurse -Depth 3 -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^clang(\+\+)?(\.exe)?$|^hipcc(\.bat|\.exe)?$' } |
        ForEach-Object { Write-Host "  $($_.FullName)" }
    throw "HIP compiler was not found under $rocmRoot"
}
& $hipcc --version

Add-StepPath (Join-Path $rocmRoot 'bin')
Add-StepPath (Join-Path $rocmRoot 'llvm\bin')
Set-StepEnv 'ROCM_PATH' $rocmRoot
Set-StepEnv 'HIP_PATH' $rocmRoot
Set-StepEnv 'HIPCC' $hipcc
Set-StepEnv 'HIPCXX' $hipcxx
Write-Host "HIP SDK $version ready at $rocmRoot"
