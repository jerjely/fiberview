# FiberView - Windows installer.
# Installs into %LOCALAPPDATA%\FiberView (private Python environment, no admin rights needed)
# and creates Desktop + Start menu shortcuts. Run via install_windows.bat.
$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'FiberView - installer'
$src   = Split-Path -Parent $PSScriptRoot
$root  = Join-Path $env:LOCALAPPDATA 'FiberView'
$app   = Join-Path $root 'app'
$venv  = Join-Path $root 'venv'

Write-Host '=== FiberView - fiber photometry acquisition and analysis ===' -ForegroundColor Cyan
Write-Host "Installing to $root"
Write-Host ''

# 1. find a Python 3.9+ (python.org launcher, PATH, or Anaconda/Miniconda/Miniforge base)
function Test-Py($cmd) {
    try {
        $exe = $cmd[0]; $a = @(); if ($cmd.Count -gt 1) { $a = $cmd[1..($cmd.Count - 1)] }
        $v = & $exe @a -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $p = $v.Trim().Split('.')
            if ([int]$p[0] -eq 3 -and [int]$p[1] -ge 9) { return $true }
        }
    } catch {}
    return $false
}
$candidates = @(@('py', '-3'), @('python'))
foreach ($d in @("$env:USERPROFILE\anaconda3", "$env:USERPROFILE\miniconda3", "$env:USERPROFILE\miniforge3",
                 "$env:LOCALAPPDATA\anaconda3", "$env:LOCALAPPDATA\miniconda3", "$env:ProgramData\anaconda3",
                 "$env:ProgramData\miniconda3")) {
    $candidates += ,@((Join-Path $d 'python.exe'))
}
$py = $null
foreach ($c in $candidates) {
    if (($c[0] -like '*\python.exe') -and -not (Test-Path $c[0])) { continue }
    if (Test-Py $c) { $py = $c; break }
}
if (-not $py) {
    Write-Host 'Python 3.9 or newer was not found.' -ForegroundColor Red
    Write-Host 'Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),'
    Write-Host 'then double-click install_windows.bat again.'
    exit 1
}
$pyExe = $py[0]
$pyArgs = @()
if ($py.Count -gt 1) { $pyArgs = $py[1..($py.Count - 1)] }
Write-Host "Using Python: $($py -join ' ')"

try {
    # 2. copy the app
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    foreach ($f in @('fiberview.py', 'fv_core.py', 'fv_sources.py', 'fv_selftest.py', 'fv_version.py',
                     'requirements.txt', 'README.md')) {
        Copy-Item -Force (Join-Path $src $f) $app
    }
    foreach ($d in @('assets', 'arduino')) {
        Copy-Item -Recurse -Force (Join-Path $src $d) $app
    }
    Copy-Item -Force (Join-Path $PSScriptRoot 'uninstall_windows.ps1') $root

    # 3. private environment + packages
    $vpy = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $vpy)) {
        Write-Host 'Creating the Python environment...'
        & $pyExe @pyArgs -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    Write-Host 'Installing packages (first time: a few minutes, ~300 MB)...'
    & $vpy -m pip install --disable-pip-version-check --upgrade pip | Out-Null
    & $vpy -m pip install --disable-pip-version-check -r (Join-Path $app 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Host 'Package installation failed.' -ForegroundColor Red
        Write-Host 'If the error mentions SSL and you use Anaconda, install Python from python.org and run'
        Write-Host "the installer again (delete $root first)."
        exit 1
    }
    & $vpy -c "import PySide6, pyqtgraph, numpy, scipy, serial, nidaqmx; print('All packages OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Package check failed.' }

    # 4. shortcuts
    $ws = New-Object -ComObject WScript.Shell
    $targets = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
    foreach ($dir in $targets) {
        $lnk = $ws.CreateShortcut((Join-Path $dir 'FiberView.lnk'))
        $lnk.TargetPath       = Join-Path $venv 'Scripts\pythonw.exe'
        $lnk.Arguments        = '"' + (Join-Path $app 'fiberview.py') + '"'
        $lnk.WorkingDirectory = $app
        $lnk.IconLocation     = Join-Path $app 'assets\fiberview.ico'
        $lnk.Description      = 'Fiber photometry acquisition and analysis'
        $lnk.Save()
    }
    # console variant that shows error messages, for troubleshooting
    $lnk = $ws.CreateShortcut((Join-Path $targets[1] 'FiberView (console).lnk'))
    $lnk.TargetPath       = $vpy
    $lnk.Arguments        = '"' + (Join-Path $app 'fiberview.py') + '"'
    $lnk.WorkingDirectory = $app
    $lnk.IconLocation     = Join-Path $app 'assets\fiberview.ico'
    $lnk.Save()
} catch {
    Write-Host ''
    Write-Host "Installation failed: $_" -ForegroundColor Red
    exit 1
}

# NI-DAQmx driver check (only needed for NI devices)
& $vpy -c "import nidaqmx.system; nidaqmx.system.System.local().driver_version" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'Note: the NI-DAQmx driver was not found. That is fine for the Arduino/ADS1115;' -ForegroundColor Yellow
    Write-Host 'for NI devices install NI-DAQmx from ni.com (it comes with NI MAX).' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Done. Start "FiberView" from the Desktop or the Start menu.' -ForegroundColor Green
Write-Host 'To update later: unzip the new version and run install_windows.bat again.'
