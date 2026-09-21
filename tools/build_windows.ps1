$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
# Prefer the project virtualenv only when it can import the complete build
# stack. A partially upgraded Python can exist but miss pyexpat/PyInstaller;
# in that case fall back to the installed Python 3.11 used for releases.
$candidates = @()
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) { $candidates += $venvPython }
try {
    $py311 = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1).Trim()
    if ($py311) { $candidates += $py311 }
}
catch { }
$python = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    & $candidate -c "import xml.parsers.expat, PyInstaller, PySide6" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    throw "No working Python build environment found (need Python 3.11, PyInstaller and PySide6)"
}
$spec = Join-Path $projectRoot 'ControlIOS.spec'
$defaultDistRoot = Join-Path $projectRoot 'dist'
$runningBuild = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -like "$defaultDistRoot\ControlIOS PC\*"
} | Select-Object -First 1
$distRoot = if ($runningBuild) {
    # Không đóng app người dùng đang chạy; đóng gói sang thư mục kế bên.
    Join-Path $projectRoot 'dist-next'
}
else {
    $defaultDistRoot
}
$outputRoot = Join-Path $distRoot 'ControlIOS PC'
$resolvedOutput = [System.IO.Path]::GetFullPath($outputRoot)
$resolvedProject = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
if (-not $resolvedOutput.StartsWith($resolvedProject, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Build output escaped project: $resolvedOutput"
}
$userConfigRoot = Join-Path $env:APPDATA 'ControlIOS PC\config'
$legacyConfigRoot = Join-Path $defaultDistRoot 'ControlIOS PC\config'
$backupBase = Join-Path $projectRoot 'backups\ControlIOS PC'

# Dữ liệu máy/nhóm nằm ở AppData, ngoài dist, nên PyInstaller không thể xoá nó.
# Vẫn chụp thêm một bản theo thời gian trước mỗi build để có thể quay lại nếu
# file bị hỏng do mất điện hoặc app bị tắt giữa lúc ghi.
$sourceConfigRoot = if (Test-Path $userConfigRoot) {
    $userConfigRoot
}
elseif (Test-Path $legacyConfigRoot) {
    $legacyConfigRoot
}
else {
    $null
}

if ($sourceConfigRoot) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupRoot = Join-Path $backupBase $stamp
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    foreach ($name in @('devices.json', 'scripts.json', 'autoclick_js.json')) {
        $source = Join-Path $sourceConfigRoot $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $backupRoot $name)
        }
    }
    Write-Output "Backed up user data: $backupRoot"
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean --distpath $distRoot $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Output "Build complete: $outputRoot"
Write-Output "User data kept outside build: $userConfigRoot"
