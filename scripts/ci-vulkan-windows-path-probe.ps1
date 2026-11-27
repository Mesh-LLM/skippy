param([ValidateSet('short', 'long', 'long-short-build', 'ci-deep')][string]$Case)
$ErrorActionPreference = 'Stop'
$source = 'ab8cc0735b3c974a20e85303f2bfdd0c4cbd9225'
$controlRoot = (Get-Location).Path
$evidence = Join-Path $controlRoot "path-probe-evidence/$Case"
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$probeRoot = if ($Case -eq 'short') { 'C:\svk' } else { 'C:\Users\SkippyLab\dev\skippy-vulkan' }
$buildRoot = if ($Case -eq 'long-short-build') { 'C:\vkb' } else { Join-Path $probeRoot '.deps/llama-build' }
if ($Case -eq 'ci-deep') { $buildRoot = Join-Path $controlRoot '.deps/windows/vulkan/long-path-regression/llama-build' }
if (Test-Path $probeRoot) { throw "Probe source directory already exists: $probeRoot" }
if ($Case -in @('long-short-build', 'ci-deep') -and (Test-Path $buildRoot)) { throw 'Probe build directory already exists' }
& git worktree add --detach $probeRoot $source
if ($LASTEXITCODE -ne 0) { throw 'Could not create source-pinned worktree' }
Push-Location $probeRoot
try {
    $actual = (& git rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $actual -ne $source) { throw 'Probe source identity mismatch' }
    if ($Case -in @('long-short-build', 'ci-deep')) { $env:SKIPPY_LLAMA_BUILD_ROOT = $buildRoot.Replace('\', '/') }
    else { Remove-Item Env:SKIPPY_LLAMA_BUILD_ROOT -ErrorAction SilentlyContinue }
    $env:SKIPPY_PYTHON = 'python'
    $env:LLAMA_STAGE_BUILD_TESTS = 'OFF'
    $env:LLAMA_STAGE_FULL_REPLAY = 'OFF'
    $env:LLAMA_STAGE_UPSTREAM_TESTS = 'OFF'
    $started = [DateTime]::UtcNow
    $ErrorActionPreference = 'Continue'
    $PSNativeCommandUseErrorActionPreference = $false
    & just release-runtime-build vulkan x86_64-pc-windows-msvc 2>&1 | Tee-Object -FilePath (Join-Path $evidence 'build.log')
    $buildExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    $completed = [DateTime]::UtcNow
    $caches = @()
    if (Test-Path $buildRoot) {
        foreach ($cache in Get-ChildItem $buildRoot -Filter CMakeCache.txt -Recurse -File) {
            $relative = $cache.FullName.Substring($buildRoot.Length).TrimStart('\', '/')
            $destination = Join-Path $evidence (Join-Path 'cmake-caches' $relative)
            New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
            Copy-Item $cache.FullName $destination
            $limit = @(Get-Content $cache.FullName | Where-Object { $_ -match '^CMAKE_OBJECT_PATH_MAX:' })
            $caches += @{ path = $cache.FullName; object_path_limit = $limit }
        }
        foreach ($diagnostic in Get-ChildItem $buildRoot -Recurse -File | Where-Object { $_.Name -in @('CMakeConfigureLog.yaml', 'CMakeError.log', 'CMakeOutput.log') }) {
            $relative = $diagnostic.FullName.Substring($buildRoot.Length).TrimStart('\', '/')
            $destination = Join-Path $evidence (Join-Path 'configure-logs' $relative)
            New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
            Copy-Item $diagnostic.FullName $destination
        }
    }
    $objects = if (Test-Path $buildRoot) { @(Get-ChildItem $buildRoot -Recurse -File -Filter '*.obj' | Sort-Object { $_.FullName.Length } -Descending | Select-Object -First 5 | ForEach-Object { @{ path = $_.FullName; characters = $_.FullName.Length } }) } else { @() }
    $trackedChanges = (& git status --porcelain --untracked-files=no | Out-String).Trim()
    if ($trackedChanges) { throw 'Tracked probe source was changed' }
    $report = [ordered]@{
        case = $Case; source = $actual; harness = $env:GITHUB_SHA
        sdk_version = $env:SKIPPY_VULKAN_SDK_VERSION
        source_root = $probeRoot; source_root_characters = $probeRoot.Length; build_root = $buildRoot
        started_at = $started.ToString('o'); completed_at = $completed.ToString('o')
        build_exit_code = $buildExit; build_succeeded = ($buildExit -eq 0)
        cmake_caches = $caches; longest_existing_objects = $objects
        gpu_inference_run = $false
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $evidence 'result.json') -Encoding utf8
    $report | ConvertTo-Json -Depth 8 | Write-Output
    # This job collects both success and failure outcomes. The receipt's exit
    # code, not the collector job's conclusion, is the build result.
} finally { Pop-Location }
