# Enable the MSVC developer environment for subsequent job steps by running
# VsDevCmd for the installed VC tools and exporting the environment delta to
# GITHUB_ENV. First-party replacement for a third-party action: the repository
# only admits GitHub-owned and pattern-allowlisted actions.
#
# rustc locates Visual Studio itself. The native CMake/Ninja stage, nvcc,
# and the HIP compilers need cl.exe, INCLUDE, and LIB on PATH.

$ErrorActionPreference = 'Stop'

$arm64 = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq 'Arm64'
$arch = if ($arm64) { 'arm64' } else { 'amd64' }
$component = if ($arm64) { 'Microsoft.VisualStudio.Component.VC.Tools.ARM64' } else { 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) {
    throw "vswhere.exe was not found at $vswhere"
}

$installPath = (& $vswhere -latest -products * `
    -requires $component `
    -property installationPath) | Select-Object -First 1
if (-not $installPath) {
    throw 'no Visual Studio installation with VC tools was found'
}

$devCmd = Join-Path $installPath 'Common7\Tools\VsDevCmd.bat'
if (-not (Test-Path $devCmd)) {
    throw "VsDevCmd.bat was not found at $devCmd"
}

$work = Join-Path $env:RUNNER_TEMP 'msvc-dev-env'
New-Item -ItemType Directory -Path $work -Force | Out-Null
$batch = Join-Path $work 'dump-env.bat'
$dump = Join-Path $work 'env.txt'
@(
    '@echo off',
    "call `"$devCmd`" -arch=$arch -host_arch=$arch -no_logo || exit /b 1",
    "set > `"$dump`""
) | Set-Content -Path $batch -Encoding ascii

cmd /c "`"$batch`""
if ($LASTEXITCODE -ne 0) {
    throw "VsDevCmd failed with exit code $LASTEXITCODE"
}

$after = @{}
foreach ($line in Get-Content $dump) {
    $separator = $line.IndexOf('=')
    if ($separator -le 0) { continue }
    $name = $line.Substring(0, $separator)
    $after[$name] = $line.Substring($separator + 1)
}
if (-not $after.ContainsKey('INCLUDE') -or -not $after.ContainsKey('LIB')) {
    throw 'VsDevCmd completed but did not export INCLUDE and LIB'
}

$exported = 0
foreach ($name in $after.Keys) {
    $current = [Environment]::GetEnvironmentVariable($name)
    if ($null -ne $current -and $current -eq $after[$name]) { continue }
    if ($env:GITHUB_ENV) {
        "${name}=$($after[$name])" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
    } else {
        Set-Item -Path "env:$name" -Value $after[$name]
    }
    $exported++
}
if ($env:GITHUB_PATH) {
    # Prepend the developer PATH so cl.exe wins over any preinstalled tools.
    $machinePath = [Environment]::GetEnvironmentVariable('PATH', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
    $devPath = $after['PATH']
    foreach ($segment in @($machinePath, $userPath)) {
        if (-not $segment) { continue }
        $devPath = ($devPath -split ';' | Where-Object { $_ -and ($segment -split ';') -notcontains $_ }) -join ';'
    }
    if ($devPath) {
        $devPath | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
    }
}
Write-Host "MSVC developer environment exported ($exported variables) from $devCmd"
