<#
.SYNOPSIS
    Gửi lệnh tới control socket của TrollVNC (bản đã vá).

.DESCRIPTION
    TrollVNC mở một control socket dạng text, mỗi kết nối nhận đúng một lệnh
    rồi đóng. Bản vá của chúng ta thêm: apps, launch <bundleId>, terminate
    <bundleId>, và bắt buộc tiền tố "auth <token>" cho kết nối từ ngoài máy.

.EXAMPLE
    .\tvnc-ctl.ps1 -Device 172.30.3.152 -Command apps

.EXAMPLE
    .\tvnc-ctl.ps1 -Device 172.30.3.152 -Command "launch com.zing.zalo"

.EXAMPLE
    # Liệt kê app do người dùng cài, bỏ qua app hệ thống
    .\tvnc-ctl.ps1 -Device 172.30.3.152 -Command apps -UserOnly
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Device,

    [Parameter(Mandatory = $true)]
    [string]$Command,

    [string]$Token = "Congavinh1",

    [int]$Port = 46752,

    [int]$TimeoutMs = 5000,

    # Chỉ dùng với lệnh "apps": lọc bỏ app hệ thống cho dễ đọc.
    [switch]$UserOnly
)

$client = New-Object Net.Sockets.TcpClient
try {
    $connect = $client.BeginConnect($Device, $Port, $null, $null)
    if (-not $connect.AsyncWaitHandle.WaitOne($TimeoutMs)) {
        throw "Không nối được $Device`:$Port trong $TimeoutMs ms. Kiểm tra: TrollVNC đang chạy? Bản build có tick Managed.plist? Secret TVNC_CTL_TOKEN đã tạo?"
    }
    $client.EndConnect($connect)

    $client.ReceiveTimeout = $TimeoutMs
    $client.SendTimeout = $TimeoutMs

    $stream = $client.GetStream()
    $writer = New-Object IO.StreamWriter($stream)
    $reader = New-Object IO.StreamReader($stream)

    # Token đi kèm mọi lệnh vì mỗi kết nối chỉ phục vụ một dòng.
    $writer.WriteLine("auth $Token $Command")
    $writer.Flush()

    $response = $reader.ReadToEnd()
} finally {
    $client.Close()
}

if ($response -match '^ERR Unauthorized') {
    Write-Error "Token sai. Kiểm tra secret TVNC_CTL_TOKEN trên GitHub có đúng '$Token' không."
    return
}

if ($Command -eq "apps" -and $response -match "`t") {
    $apps = $response -split "`r?`n" | Where-Object { $_ -match "`t" } | ForEach-Object {
        $f = $_ -split "`t"
        [pscustomobject]@{
            BundleId = $f[0]
            Name     = $f[1]
            Type     = $f[2]
            Version  = $f[3]
        }
    }
    if ($UserOnly) {
        $apps = $apps | Where-Object { $_.Type -eq "User" }
    }
    $apps | Sort-Object Type, Name
} else {
    $response.TrimEnd()
}
