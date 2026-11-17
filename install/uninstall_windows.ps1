# Removes %LOCALAPPDATA%\FiberView and the shortcuts. Your recordings are not touched.
$root = Join-Path $env:LOCALAPPDATA 'FiberView'
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    foreach ($n in @('FiberView.lnk', 'FiberView (console).lnk')) {
        $p = Join-Path $dir $n
        if (Test-Path $p) { Remove-Item -Force $p }
    }
}
if (Test-Path $root) {
    Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
}
Write-Host 'FiberView removed.' -ForegroundColor Green
