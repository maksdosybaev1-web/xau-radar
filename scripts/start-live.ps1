$ErrorActionPreference = 'Stop'
$radarRoot = Split-Path -Parent $PSScriptRoot
$radarRuntime = Join-Path $radarRoot 'runtime'
New-Item -ItemType Directory -Path $radarRuntime -Force | Out-Null
$radarPidPath = Join-Path $radarRuntime 'bridge.pid'
if (Test-Path -LiteralPath $radarPidPath) {
    try {
        $radarIdentity = Get-Content -LiteralPath $radarPidPath -Raw | ConvertFrom-Json
        $radarExisting = Get-Process -Id ([int]$radarIdentity.id) -ErrorAction SilentlyContinue
        if ($radarExisting -and $radarExisting.ProcessName -match '^python' -and
            $radarExisting.StartTime.ToUniversalTime().Ticks -eq [long]$radarIdentity.started_utc_ticks) {
            Write-Output "MT5-мост уже запущен (PID $($radarExisting.Id))."
            exit
        }
    } catch {}
}
$radarTerminal = Join-Path $env:ProgramFiles 'MetaTrader 5\terminal64.exe'
if (-not (Test-Path -LiteralPath $radarTerminal)) { throw 'Терминал MetaTrader 5 не найден.' }
$radarTerminalProcesses = @(Get-Process terminal64 -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $radarTerminal })
if ($radarTerminalProcesses.Count -eq 0) {
    # The Python package can launch MT5 without a usable history window.
    # Start the interactive terminal first; the bridge then attaches to it.
    Start-Process -FilePath $radarTerminal -WindowStyle Normal | Out-Null
    Start-Sleep -Seconds 3
}
$radarPython = (Get-Command python.exe).Source
$radarProcess = Start-Process -FilePath $radarPython -ArgumentList @('-X','utf8','-m','app.mt5_bridge','--symbol','XAUUSD','--terminal',('"' + $radarTerminal + '"'),'--watch') -WorkingDirectory $radarRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $radarRuntime 'bridge.log') -RedirectStandardError (Join-Path $radarRuntime 'bridge-error.log')
@{ id = $radarProcess.Id; started_utc_ticks = $radarProcess.StartTime.ToUniversalTime().Ticks } |
    ConvertTo-Json -Compress |
    Set-Content -LiteralPath $radarPidPath
Start-Sleep -Seconds 2
if ($radarProcess.HasExited) { throw "MT5-мост завершился. См. runtime/bridge-error.log." }
Write-Output "MT5-мост запущен (PID $($radarProcess.Id))."
