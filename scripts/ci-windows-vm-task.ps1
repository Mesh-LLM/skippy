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
$env:SKIPPY_CI_PROFILE = 'full'
$env:SKIPPY_CI_MODEL = "$inputDirectory\model.gguf"
& "$source\scripts\ci-windows-vm-variants.ps1" -Source $source -InputDirectory $inputDirectory `
    -OutputDirectory $outputDirectory -Backend $task.backend
exit 0
