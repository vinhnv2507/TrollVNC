# Patch TrollVNC vòng 4: respring

Thêm một lệnh vào control socket:

```
respring   -> khởi động lại SpringBoard (gỡ giao diện treo, KHÔNG mất jailbreak)
```

Daemon `trollvncserver` chạy **root** nên làm được ngay, không cần thêm framework
hay entitlement. Chỉ sửa `src/trollvncserver.mm`.

> **Vì sao không có `reboot`?** Đã thử và iOS chặn kernel-reboot từ tiến trình
> này: `reboot(2)` bị AMFI chặn (EPERM) dù root, `reboot3()` bị gate (trả cùng một
> mã bất kể cờ), còn `SBSRelaunchAction` chỉ respring. Ngoài ra với farm Dopamine
> reboot thật làm **mất jailbreak toàn bộ máy** nên gần như không nên dùng —
> respring gỡ treo là đủ.

---

## 1/2 — Hàm mới

`Ctrl+F` tìm `#pragma mark - File transfer`. Đặt con trỏ **ngay trên** dòng đó,
nhấn Enter, rồi dán:

```objc
#pragma mark - Power

// `respring` — kill SpringBoard; backboardd tự bật lại. Gỡ giao diện treo mà
// KHÔNG reboot, nên giữ nguyên jailbreak trên máy semi-untethered (Dopamine).
static NSData *tvCtlRespring(void) {
    pid_t pid = 0;
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsPid)(CFStringRef, pid_t *) =
            (int (*)(CFStringRef, pid_t *))dlsym(h, "SBSProcessIDForDisplayIdentifier");
        if (sbsPid)
            sbsPid((__bridge CFStringRef)@"com.apple.springboard", &pid);
    }
    if (pid <= 0)
        return [@"ERR NoSpringBoard\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = (kill(pid, SIGKILL) == 0);
    TVLog(@"Control socket: respring (SpringBoard pid %d) -> %@", pid, ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR RespringFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}
```

---

## 2/2 — Nối vào bộ điều phối

`Ctrl+F` tìm nhánh `savephoto` (vòng 3):

```objc
    } else if ([cmd hasPrefix:@"savephoto "]) {
        resp = tvCtlSavePhoto([cmd substringFromIndex:10]);
```

Ngay **phía dưới**, chèn:

```objc
    } else if ([cmd isEqualToString:@"respring"]) {
        resp = tvCtlRespring();
```

---

## Build và thử

Run workflow → ☑ Managed.plist → cài lại.

Trong Control IOS: nút **Respring** trên thanh công cụ (hỏi xác nhận, hiện bảng
kết quả từng máy).

Từ dòng lệnh:

```powershell
cd D:\ControlIOS
.\.venv\Scripts\python.exe -c "import asyncio; from controlios.config import Registry; from controlios.control_channel import ControlChannel; r=Registry.load(); c=ControlChannel('172.30.0.221', r.settings.control_port, r.settings.control_token); asyncio.run(c.respring()); print('OK respring')"
```

SpringBoard phải chớp tắt rồi hiện lại.
