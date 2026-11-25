$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$inputDirectory = '\\host.lan\Data\input'
$outputDirectory = '\\host.lan\Data\output'
$task = Get-Content "$inputDirectory\task.json" -Raw | ConvertFrom-Json
if ($task.source -cnotmatch '^[a-f0-9]{40}$') { throw 'Invalid immutable source identity' }
if ($task.backend -notin @('cuda', 'rocm')) { throw 'Invalid Windows smoke backend' }
$source = 'C:\MeshSmoke\source'
New-Item -ItemType Directory -Force C:\MeshSmoke | Out-Null
& git.exe -c core.autocrlf=false clone --no-checkout "$inputDirectory\source.bundle" $source
if ($LASTEXITCODE -ne 0) { throw 'Source restoration failed' }
Set-Location $source
& git.exe config core.autocrlf false
if ($LASTEXITCODE -ne 0) { throw 'Source Git configuration failed' }
& git.exe -c core.autocrlf=false checkout --detach $task.source
if ($LASTEXITCODE -ne 0) { throw 'Source checkout failed' }
$actual = & git.exe rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $actual.Trim() -ne $task.source) { throw 'Source identity mismatch' }
$env:SKIPPY_PYTHON = 'C:\MeshWindows\Python\python.exe'
$env:SKIPPY_CI_MODEL = "$inputDirectory\model.gguf"
$variants = if ($task.backend -eq 'cuda') { @('cuda12', 'cuda13') } else { @('rocm') }
foreach ($variant in $variants) {
    $runtime = "windows-x86_64-$variant"
    $product = Join-Path $source "dist\products\$runtime"
    New-Item -ItemType Directory -Force $product | Out-Null
    Copy-Item "$inputDirectory\products\$runtime\*" $product -Recurse -Force
    try {
        & just.exe ci-smoke $runtime $product
        if ($LASTEXITCODE -ne 0) { throw "$runtime inference smoke failed" }
        if (-not (Test-Path "$product\evidence\smoke.json")) { throw "$runtime has no inference evidence" }
    } finally {
        if (Test-Path "$product\evidence") {
            Copy-Item "$product\evidence" "$outputDirectory\$runtime" -Recurse -Force
        }
    }
}
exit 0
