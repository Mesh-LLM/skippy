$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $scratch | Out-Null
$global:MeshSmokeTestAttempts = @()
function global:just.exe {
    param($Command, $Runtime, $Product)
    $global:MeshSmokeTestAttempts += $Runtime
    New-Item -ItemType Directory -Force "$Product\evidence" | Out-Null
    Set-Content "$Product\evidence\diagnostic.txt" $Runtime
    if ($Runtime -eq 'windows-x86_64-cuda12') {
        $global:LASTEXITCODE = 1
    } else {
        Set-Content "$Product\evidence\smoke.json" '{}'
        $global:LASTEXITCODE = 0
    }
}
try {
    foreach ($runtime in @('windows-x86_64-cuda12', 'windows-x86_64-cuda13')) {
        New-Item -ItemType Directory -Force "$scratch\input\products\$runtime" | Out-Null
        Set-Content "$scratch\input\products\$runtime\archive.zip" 'fixture'
    }
    New-Item -ItemType Directory "$scratch\output" | Out-Null
    $failed = $false
    try {
        & "$PSScriptRoot\..\ci-windows-vm-variants.ps1" -Source "$scratch\source" `
            -InputDirectory "$scratch\input" -OutputDirectory "$scratch\output" -Backend cuda
    } catch {
        if ($_ -notmatch 'Windows inference smoke failed: windows-x86_64-cuda12') { throw }
        $failed = $true
    }
    if (-not $failed) { throw 'A failed CUDA variant passed the aggregate' }
    if (($global:MeshSmokeTestAttempts -join ',') -ne 'windows-x86_64-cuda12,windows-x86_64-cuda13') {
        throw 'CUDA 12 failure prevented CUDA 13 execution'
    }
    foreach ($runtime in $global:MeshSmokeTestAttempts) {
        if (-not (Test-Path "$scratch\output\$runtime\diagnostic.txt")) { throw "Missing $runtime diagnostics" }
    }
    if (-not (Test-Path "$scratch\output\windows-x86_64-cuda13\smoke.json")) { throw 'CUDA 13 evidence was lost' }
    if (Test-Path "$scratch\output\windows-x86_64-cuda12\smoke.json") { throw 'Failure acquired passing evidence' }
    Write-Output 'Both CUDA variants attempted; failure and separate diagnostics preserved'
} finally {
    Remove-Item Function:\just.exe
    Remove-Variable -Name MeshSmokeTestAttempts -Scope Global
    Remove-Item $scratch -Recurse -Force
}
