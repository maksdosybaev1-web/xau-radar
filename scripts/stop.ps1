$radarPidPath = Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime') 'server.pid'
if (Test-Path -LiteralPath $radarPidPath) {
    $radarIdentity = Get-Content -LiteralPath $radarPidPath -Raw | ConvertFrom-Json
    $radarProcess = Get-Process -Id ([int]$radarIdentity.id) -ErrorAction SilentlyContinue
    if ($radarProcess -and $radarProcess.ProcessName -match '^python' -and
        $radarProcess.StartTime.ToUniversalTime().Ticks -eq [long]$radarIdentity.started_utc_ticks) {
        Stop-Process -Id $radarProcess.Id
    }
}
