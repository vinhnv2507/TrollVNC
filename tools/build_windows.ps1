$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
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

if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

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
    & $python -m PyInstaller --noconfirm --distpath $distRoot $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Output "Build complete: $outputRoot"
Write-Output "User data kept outside build: $userConfigRoot"
