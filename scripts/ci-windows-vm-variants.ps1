param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$InputDirectory,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][ValidateSet('cuda', 'rocm')][string]$Backend
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$variants = if ($Backend -eq 'cuda') { @('cuda12', 'cuda13') } else { @('rocm') }
$failed = @()
foreach ($variant in $variants) {
    $runtime = "windows-x86_64-$variant"
    $product = Join-Path $Source "dist\products\$runtime"
    try {
        try {
            New-Item -ItemType Directory -Force $product | Out-Null
            Copy-Item "$InputDirectory\products\$runtime\*" $product -Recurse -Force
            & just.exe ci-smoke $runtime $product
            if ($LASTEXITCODE -ne 0) { throw "$runtime inference smoke failed" }
            if (-not (Test-Path "$product\evidence\smoke.json")) { throw "$runtime has no inference evidence" }
        } finally {
            if (Test-Path "$product\evidence") {
                Copy-Item "$product\evidence" "$OutputDirectory\$runtime" -Recurse -Force
            }
        }
    } catch {
        $failed += $runtime
        Write-Warning "$runtime failed: $_"
    }
}
if ($failed.Count -ne 0) { throw "Windows inference smoke failed: $($failed -join ', ')" }
