param(
    [string]$MusicFolder = (Join-Path $PSScriptRoot '..\weeknd'),
    [double]$Minutes = 25,
    [ValidateSet('auto','librosa','beat-this')][string]$Backend = 'auto'
)
$ErrorActionPreference = 'Stop'
$djRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$djPython = Join-Path $djRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $djPython)) { throw 'Project Python environment is missing. Follow README setup.' }
$env:PYTHONUTF8 = '1'
Set-Location -LiteralPath $djRoot
& $djPython -m dj_agent mix $MusicFolder --minutes $Minutes --backend $Backend --open
exit $LASTEXITCODE
