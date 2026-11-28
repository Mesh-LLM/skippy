param(
    [ValidateSet("", "cpu", "cuda", "rocm", "hip", "vulkan")][string]$Backend = "cpu",
    [string]$CudaArch = "",
    [string]$RocmArch = "",
    [ValidateSet("debug", "dev", "release")][string]$BuildProfile = "debug",
    [ValidateSet("", "x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc")][string]$Target = "",
    [ValidateSet("", "x64", "arm64")][string]$TargetArch = "",
    [switch]$RuntimeOnly
)
$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Resolve-CommandPath {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    return $null
}

function Add-ToPath {
    param([string]$Directory)

    if (-not $Directory -or -not (Test-Path $Directory)) {
        return
    }

    $pathEntries = @($env:PATH -split [System.IO.Path]::PathSeparator)
    if ($pathEntries -contains $Directory) {
        return
    }

    $env:PATH = "$Directory$([System.IO.Path]::PathSeparator)$env:PATH"
}

function Import-CmdEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandLine
    )

    $output = & cmd.exe /s /c "$CommandLine && set"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to initialize Windows build environment with command: $CommandLine"
    }

    foreach ($line in $output) {
        if ($line -match '^(?<name>[^=]+)=(?<value>.*)$') {
            Set-Item -Path "env:$($Matches.name)" -Value $Matches.value
        }
    }
}

function Get-MissingCommands {
    param([string[]]$Commands)

    $missing = @()
    foreach ($command in $Commands) {
        if (-not (Resolve-CommandPath $command)) {
            $missing += $command
        }
    }
    return $missing
}

function Resolve-Vcvars {
    param([string]$Component)
    $programFilesX86 = ${env:ProgramFiles(x86)}
    $vswhereCandidates = @()
    if ($programFilesX86) {
        $vswhereCandidates += (Join-Path $programFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe")
    }
    if ($env:ProgramFiles) {
        $vswhereCandidates += (Join-Path $env:ProgramFiles "Microsoft Visual Studio\Installer\vswhere.exe")
    }
    $vswhereFromPath = Resolve-CommandPath "vswhere"
    if ($vswhereFromPath) {
        $vswhereCandidates += $vswhereFromPath
    }

    $vswhere = $vswhereCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique -First 1
    if ($vswhere) {
        $installationPathOutput = & $vswhere -latest -products * -requires $Component -property installationPath | Select-Object -First 1
        $installationPath = if ($installationPathOutput) { $installationPathOutput.Trim() } else { "" }
        if ($installationPath) {
            $candidate = Join-Path $installationPath "VC\Auxiliary\Build\vcvarsall.bat"
            if (Test-Path $candidate) {
                return $candidate
            }
        }
    }

    $searchRoots = @($programFilesX86, $env:ProgramFiles) | Where-Object { $_ } | Select-Object -Unique
    foreach ($searchRoot in $searchRoots) {
        $candidate = Get-ChildItem -Path $searchRoot -Filter vcvarsall.bat -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like '*Microsoft Visual Studio*VC\Auxiliary\Build\vcvarsall.bat' } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }
    throw "Visual Studio Build Tools with $Component and vcvarsall.bat are required."
}

function Ensure-MsvcToolchain {
    param([string]$Architecture, [bool]$ArmHost)

    $requiredTools = @("cl", "link", "lib", "rc", "mt")
    $missingTools = @(Get-MissingCommands -Commands $requiredTools)
    if ($missingTools.Count -eq 0 -and $env:VSCMD_ARG_TGT_ARCH -eq $Architecture) {
        return
    }

    $component = if ($Architecture -eq "arm64") { "Microsoft.VisualStudio.Component.VC.Tools.ARM64" } else { "Microsoft.VisualStudio.Component.VC.Tools.x86.x64" }
    $vcvars = Resolve-Vcvars $component
    $argument = if ($Architecture -eq "arm64") {
        if ($ArmHost) { "arm64" } else { "amd64_arm64" }
    } else {
        "amd64"
    }
    Import-CmdEnvironment "`"$vcvars`" $argument >nul"

    $missingTools = @(Get-MissingCommands -Commands $requiredTools)
    if ($missingTools.Count -gt 0 -or $env:VSCMD_ARG_TGT_ARCH -ne $Architecture) {
        throw "MSVC must target $Architecture. Active target: '$env:VSCMD_ARG_TGT_ARCH'. Missing tools: $($missingTools -join ', ')."
    }
}

function Ensure-CudaToolchain {
    if (Resolve-CommandPath "nvcc") {
        return
    }

    $candidates = @()
    if ($env:CUDA_PATH) {
        $candidates += (Join-Path $env:CUDA_PATH "bin")
    }
    if ($env:ProgramFiles) {
        $toolkitRoot = Join-Path $env:ProgramFiles "NVIDIA GPU Computing Toolkit\CUDA"
        if (Test-Path $toolkitRoot) {
            $candidates += Get-ChildItem -Path $toolkitRoot -Directory | Sort-Object Name -Descending | ForEach-Object {
                Join-Path $_.FullName "bin"
            }
        }
    }

    foreach ($candidate in $candidates) {
        if (Test-Path (Join-Path $candidate "nvcc.exe")) {
            Add-ToPath $candidate
            return
        }
    }

    throw "nvcc not found. Install the CUDA toolkit and ensure nvcc.exe is in PATH."
}

function Resolve-PackagingBash {
    $kernel = try { (& bash -c "uname -s" 2>$null | Out-String).Trim() } catch { "" }
    if ($kernel -match '^(MINGW|MSYS|CYGWIN)') {
        return "bash"
    }
    $git = Resolve-CommandPath "git"
    if ($git) {
        # git.exe sits in <Git>\cmd or <Git>\bin, and Git Bash in <Git>\bin.
        $gitBash = Join-Path (Split-Path -Parent (Split-Path -Parent $git)) "bin\bash.exe"
        if (Test-Path $gitBash) {
            return $gitBash
        }
    }
    throw "Packaging the native runtime needs Git Bash, MSYS2 or Cygwin bash, but bash on PATH is not one (uname -s: '$kernel') and no Git Bash was found next to git. Put Git for Windows' bin directory first on PATH."
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [string[]]$Arguments = @()
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        $argString = if ($Arguments.Count -gt 0) { " " + ($Arguments -join " ") } else { "" }
        throw "Command failed with exit code ${LASTEXITCODE}: $Command$argString"
    }
}
$machineIsArm64 = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq "Arm64"
if ($TargetArch) {
    $archTarget = if ($TargetArch -eq "arm64") { "aarch64-pc-windows-msvc" } else { "x86_64-pc-windows-msvc" }
    if ($Target -and $Target -ne $archTarget) { throw "-Target and -TargetArch must select the same architecture." }
    $Target = $archTarget
}
if ($env:CARGO_BUILD_TARGET) {
    if ($env:CARGO_BUILD_TARGET -notin @("x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc")) {
        throw "Unsupported Windows Cargo target '$env:CARGO_BUILD_TARGET'."
    }
    if ($Target -and $Target -ne $env:CARGO_BUILD_TARGET) {
        throw "The requested target '$Target' conflicts with CARGO_BUILD_TARGET '$env:CARGO_BUILD_TARGET'."
    }
    $Target = $env:CARGO_BUILD_TARGET
}
if (-not $Target) {
    $Target = if ($machineIsArm64) { "aarch64-pc-windows-msvc" } else { "x86_64-pc-windows-msvc" }
}
$architecture = if ($Target -eq "aarch64-pc-windows-msvc") { "arm64" } else { "x64" }
if (-not $Backend) { $Backend = "cpu" }
if ($architecture -eq "arm64" -and $Backend -ne "cpu") { throw "Windows ARM64 supports the CPU runtime only." }

$savedEnvironment = @{}
Get-ChildItem Env: | ForEach-Object { $savedEnvironment[$_.Name] = $_.Value }
Push-Location $repoRoot
try {
    Ensure-MsvcToolchain $architecture $machineIsArm64
    if ($architecture -eq "arm64" -and -not (Resolve-CommandPath "clang")) {
        if ($env:VCINSTALLDIR) {
            Add-ToPath (Join-Path $env:VCINSTALLDIR "Tools\Llvm\ARM64\bin")
            Add-ToPath (Join-Path $env:VCINSTALLDIR "Tools\Llvm\x64\bin")
        }
        if (-not (Resolve-CommandPath "clang")) {
            throw "Install Microsoft.VisualStudio.Component.VC.Llvm.Clang and add clang to PATH."
        }
    }
    if ($Backend -eq "cuda") { Ensure-CudaToolchain }
    $bash = Resolve-PackagingBash
    if (-not $env:CC) { $env:CC = "cl" }
    if (-not $env:CXX) { $env:CXX = "cl" }
    $env:CMAKE_GENERATOR = "Ninja"
    if ($CudaArch) { $env:LLAMA_STAGE_CUDA_ARCHITECTURES = $CudaArch }
    if ($RocmArch) { $env:LLAMA_STAGE_AMDGPU_TARGETS = $RocmArch }
    $profile = if ($BuildProfile -eq "release") { "release" } else { "debug" }
    $runtimeOut = "dist/native-runtimes"
    if (-not $RuntimeOnly) {
        $cargoMetadata = & cargo metadata --locked --no-deps --format-version 1
        if ($LASTEXITCODE -ne 0) { throw "cargo metadata failed with exit code $LASTEXITCODE" }
        $targetDir = ($cargoMetadata | ConvertFrom-Json).target_directory
        # Use the same explicit triple for Cargo and the packaged runtime,
        # including when a Cargo configuration supplies a different default.
        $env:CARGO_BUILD_TARGET = $Target
        $targetDir = Join-Path $targetDir $Target
        $productDir = Join-Path $targetDir $profile
        $nativePath = Join-Path $productDir "native-runtimes"
        $runtimeOut = (& $bash -c 'cygpath -au "$1"' bash $nativePath | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $runtimeOut) { throw "Cannot convert $nativePath for the packaging shell." }
    }
    Invoke-NativeCommand $bash @("scripts/package-native-runtime.sh", "--build", "--backend", $Backend, "--target", $Target, "--out", $runtimeOut)
    if (-not $RuntimeOnly) {
        $recipe = if ($BuildProfile -eq "release") { "skippy-cli-release-build" } else { "skippy-cli-build" }
        Invoke-NativeCommand "just" @($recipe)
        Write-Host "Built Skippy: $(Join-Path $productDir 'skippy.exe')"
    }
} finally {
    Pop-Location
    Get-ChildItem Env: | Where-Object { -not $savedEnvironment.ContainsKey($_.Name) } |
        ForEach-Object { Remove-Item -LiteralPath "env:$($_.Name)" }
    foreach ($name in $savedEnvironment.Keys) {
        if ([Environment]::GetEnvironmentVariable($name) -cne $savedEnvironment[$name]) {
            Set-Item -LiteralPath "env:$name" -Value $savedEnvironment[$name]
        }
    }
}
