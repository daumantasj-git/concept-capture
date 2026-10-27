# Vienkartinis paruošimas naujame kompiuteryje. Paleisti PAPRASTAME (ne admin) PowerShell lange:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
#
# Ką daro (jau esamų dalykų nediegia iš naujo, todėl galima leisti pakartotinai):
#   1. Python 3.13, ffmpeg, Node.js – per winget, jei jų nėra.
#   2. .venv\ ir Python paketai iš requirements.txt (+ NVIDIA bibliotekos, jei yra NVIDIA GPU).
#   3. src\machine.py: įvertina kompiuterį (CPU, RAM, GPU, CUDA, GPU dekodavimą), parsiunčia whisper modelį
#      (~1,6 GB) ir įrašo tinkamiausius parametrus į config.local.yaml.
# Serveris telefonui diegiamas atskirai: serveris\idiegti.ps1 (admin langas), žr. README.md.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Update-SessionPath {
    # winget įrašo naujus kelius į registrą, bet šio lango PATH jų nemato, kol neperskaitomas iš naujo.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Install-WingetPackage([string]$id, [string]$what) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Nėra winget. Įdiek '$what' ranka ir paleisk setup.ps1 iš naujo."
    }
    Write-Host "Diegiama: $what (winget $id)..." -ForegroundColor Cyan
    winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "winget nepavyko įdiegti $id (kodas $LASTEXITCODE)." }
    Update-SessionPath
}

function Find-Python313 {
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:ProgramFiles\Python313\python.exe")) {
        if (Test-Path $c) { return $c }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        # PowerShell 5.1: native komandos stderr su 'Stop' tampa klaida, todėl čia – Continue.
        $ErrorActionPreference = 'Continue'
        $exe = & py -3.13 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { return "$exe".Trim() }
    }
    return $null
}

function Test-Ffmpeg {
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { return $true }
    return [bool](Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue)
}

# 1. Programos
$py = Find-Python313
if (-not $py) {
    Install-WingetPackage 'Python.Python.3.13' 'Python 3.13'
    $py = Find-Python313
    if (-not $py) { throw 'Python 3.13 įdiegtas, bet nerastas. Atidaryk naują PowerShell langą ir paleisk setup.ps1 iš naujo.' }
}
Write-Host "Python: $py"

if (-not (Test-Ffmpeg)) { Install-WingetPackage 'Gyan.FFmpeg' 'ffmpeg' }
Write-Host 'ffmpeg: yra'

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Install-WingetPackage 'OpenJS.NodeJS.LTS' 'Node.js (serveriui telefonui)' }
Write-Host 'Node.js: yra'

# 2. Python aplinka
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Host 'Kuriama .venv...' -ForegroundColor Cyan
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Nepavyko sukurti .venv.' }
}
Write-Host 'Diegiami Python paketai (pirmą kartą ~1-3 min)...' -ForegroundColor Cyan
& $venvPy -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'pip install nepavyko.' }

$nvidia = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' }
if ($nvidia) {
    # ctranslate2 (whisper) su CUDA reikia cuBLAS ir cuDNN 9. pip paketai – be atskiro CUDA Toolkit diegimo.
    Write-Host "Rasta NVIDIA ($($nvidia[0].Name)): diegiamos CUDA bibliotekos whisper'iui (~1 GB)..." -ForegroundColor Cyan
    & $venvPy -m pip install --disable-pip-version-check -q nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'
    if ($LASTEXITCODE -ne 0) { Write-Warning 'CUDA bibliotekų įdiegti nepavyko – whisper veiks per CPU.' }
}

# 3. Kompiuterio įvertinimas → config.local.yaml
& $venvPy src\machine.py
if ($LASTEXITCODE -ne 0) { throw 'src\machine.py nepavyko.' }

Write-Host ''
Write-Host 'Paruošta.' -ForegroundColor Green
Write-Host 'Toliau (žr. README.md):'
Write-Host '  1. Serveris telefonui – ADMIN PowerShell lange:  powershell -ExecutionPolicy Bypass -File .\serveris\idiegti.ps1'
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host '  2. Claude Code dar neįdiegtas: https://claude.com/claude-code' -ForegroundColor Yellow
} else {
    Write-Host '  2. Šiame aplanke paleisk: claude'
}
