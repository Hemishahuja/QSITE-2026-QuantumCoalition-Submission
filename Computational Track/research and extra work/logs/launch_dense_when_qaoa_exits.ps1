$ErrorActionPreference = "Continue"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $root
$waitLog = Join-Path $root "solution\logs\dense_random_wait.log"
$runLog = Join-Path $root "solution\logs\dense_random.log"
function Stamp($msg) {
    $line = "{0} {1}" -f (Get-Date -Format o), $msg
    Add-Content -LiteralPath $waitLog -Value $line
}
Stamp "cwd=$(Get-Location) waiting until qaoa_random PIDs 12520 and 22828 are both gone"
while ($true) {
    $alive = @(Get-CimInstance Win32_Process -Filter "ProcessId=12520 OR ProcessId=22828" -ErrorAction SilentlyContinue)
    if ($alive.Count -eq 0) { break }
    Start-Sleep -Seconds 20
}
Stamp "qaoa_random processes exited"
$other = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "cpsat_joint" })
if ($other.Count -gt 0) {
    Stamp "another cpsat_joint process is still alive; dense_random not started"
    exit 1
}
$freeMb = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
Stamp "free_mb=$freeMb"
if ($freeMb -lt 2500) {
    Stamp "free memory below 2500 MB; dense_random not started"
    exit 2
}
Stamp "launching dense_random workers=2 log=$runLog"
$py = "C:\Users\hemis\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
& $py -u -m solution.cpsat_joint --benchmark dense_random --workers 2 > $runLog 2>&1
Stamp "dense_random exit=$LASTEXITCODE"
