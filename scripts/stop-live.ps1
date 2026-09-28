$radarPidPath = Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime') 'bridge.pid'
if (-not (Test-Path -LiteralPath $radarPidPath)) { exit }
$radarIdentity = Get-Content -LiteralPath $radarPidPath -Raw | ConvertFrom-Json
$radarProcess = Get-Process -Id ([int]$radarIdentity.id) -ErrorAction SilentlyContinue
if ($radarProcess -and $radarProcess.ProcessName -match '^python' -and
    $radarProcess.StartTime.ToUniversalTime().Ticks -eq [long]$radarIdentity.started_utc_ticks) {
    Stop-Process -Id $radarProcess.Id
}
