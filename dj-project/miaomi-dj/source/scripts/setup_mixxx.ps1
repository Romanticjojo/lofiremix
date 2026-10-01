param(
    [switch]$Install,
    [switch]$Launch,
    [switch]$Diagnose
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$downloadDir = Join-Path $project 'tools\mixxx-download'
$installDir = Join-Path $project 'tools\mixxx'
$settingsDir = Join-Path $project 'tools\mixxx-settings'
$exe = Join-Path $installDir 'Mixxx\mixxx.exe'
$msi = Join-Path $downloadDir 'mixxx-2.5.6-win64.msi'
$sha = '0d1f01a1f5c2e4d4180cd462e60365d0230b405808e7bd2b6625b62a53a29c72'
$url = 'https://downloads.mixxx.org/releases/2.5.6/mixxx-2.5.6-win64.msi'

if ($Install) {
    New-Item -ItemType Directory -Force $downloadDir, $installDir | Out-Null
    if (-not (Test-Path -LiteralPath $msi)) {
        Invoke-WebRequest -Uri $url -OutFile $msi
    }
    $actual = (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $sha) { throw "Mixxx MSI SHA256 mismatch: $actual" }
    if (-not (Test-Path -LiteralPath $exe)) {
        $arguments = @('/a', ('"' + $msi + '"'), '/qn', ('TARGETDIR="' + $installDir + '"'))
        $process = Start-Process msiexec.exe -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Mixxx administrative extraction failed: $($process.ExitCode)" }
    }
    if (-not (Test-Path -LiteralPath $exe)) { throw 'Mixxx executable was not extracted' }
    Write-Output "Verified Mixxx 2.5.6: $exe"
}

if ($Install -or $Launch) {
    New-Item -ItemType Directory -Force (Join-Path $settingsDir 'controllers') | Out-Null
    Copy-Item -LiteralPath (Join-Path $project 'integrations\mixxx\DJ Agent.midi.xml') -Destination (Join-Path $settingsDir 'controllers\DJ Agent.midi.xml') -Force
    Copy-Item -LiteralPath (Join-Path $project 'integrations\mixxx\dj-agent-bridge.js') -Destination (Join-Path $settingsDir 'controllers\dj-agent-bridge.js') -Force
}

if ($Launch) {
    if (-not (Test-Path -LiteralPath $exe)) { throw 'Run -Install first' }
    $process = Start-Process -FilePath $exe -ArgumentList @('--settingsPath', ('"' + $settingsDir + '"')) -WorkingDirectory (Split-Path -Parent $exe) -WindowStyle Normal -PassThru
    Write-Output "Mixxx launched with isolated settings; PID=$($process.Id)"
}

if ($Diagnose -or (-not $Install -and -not $Launch)) {
    [pscustomobject]@{
        Executable = $exe
        Installed = Test-Path -LiteralPath $exe
        SettingsDirectory = $settingsDir
        MappingCopied = Test-Path -LiteralPath (Join-Path $settingsDir 'controllers\DJ Agent.midi.xml')
        MixxxProcess = @((Get-Process mixxx -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id))
        DDJ200Present = [bool]@(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.FriendlyName -match 'DDJ.?200' }).Count
    } | Format-List
}
