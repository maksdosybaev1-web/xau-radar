$ErrorActionPreference = 'Stop'
$radarRoot = Split-Path -Parent $PSScriptRoot
$radarRuntime = Join-Path $radarRoot 'runtime'
New-Item -ItemType Directory -Path $radarRuntime -Force | Out-Null
function Start-RadarBridge {
    try { & (Join-Path $PSScriptRoot 'start-live.ps1') }
    catch { Write-Warning ('Мост MT5 пока недоступен: ' + $_.Exception.Message) }
}
if (-not $env:RADAR_OLLAMA_MODEL) {
    $radarOllama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    for ($radarAttempt = 0; $radarAttempt -lt 5; $radarAttempt++) {
        try {
            $radarModels = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2
            if ($radarModels.models.name -contains 'qwen2.5:7b') { $env:RADAR_OLLAMA_MODEL = 'qwen2.5:7b' }
            break
        } catch {
            if ($radarAttempt -eq 0 -and (Test-Path -LiteralPath $radarOllama)) {
                Start-Process -FilePath $radarOllama -ArgumentList 'serve' -WindowStyle Hidden
            }
            Start-Sleep -Seconds 2
        }
    }
}
try {
    $radarHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8767/api/health' -TimeoutSec 2
    if ($radarHealth.ok) { Start-RadarBridge; Write-Output 'Радар уже запущен: http://127.0.0.1:8767'; exit }
} catch {}
$radarPython = (Get-Command python.exe).Source
$radarProcess = Start-Process -FilePath $radarPython -ArgumentList @('-X','utf8','-m','app.server') -WorkingDirectory $radarRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $radarRuntime 'server.log') -RedirectStandardError (Join-Path $radarRuntime 'server-error.log')
@{ id = $radarProcess.Id; started_utc_ticks = $radarProcess.StartTime.ToUniversalTime().Ticks } |
    ConvertTo-Json -Compress |
    Set-Content -LiteralPath (Join-Path $radarRuntime 'server.pid')
Start-Sleep -Seconds 2
if ($radarProcess.HasExited) { throw 'Сервер завершился. См. runtime/server-error.log.' }
Start-RadarBridge
Write-Output 'Радар запущен: http://127.0.0.1:8767'
