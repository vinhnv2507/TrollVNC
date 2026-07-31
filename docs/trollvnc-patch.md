# Patch TrollVNC: thêm lệnh quản lý app

Thêm ba lệnh vào **control socket** đã có sẵn của TrollVNC:

```
apps                      -> danh sách app đã cài (TSV: bundleId, tên, loại, phiên bản)
launch <bundleId>         -> mở app
terminate <bundleId>      -> đóng app
```

Kèm khả năng mở cổng điều khiển ra LAN **có token**. Không có token thì cổng
vẫn chỉ nghe 127.0.0.1 y như bản gốc.

> Token **không** nằm trong mã nguồn. Nó đi qua GitHub Secret →
> `Managed.plist` → prefs, đúng cách TrollVNC đang làm với mật khẩu VNC.
> Fork của bạn là public nên đây là điều bắt buộc.

Sửa 3 file. Tất cả đều sửa được thẳng trên web GitHub.

---

## File 1/3 — `devkit/gen-managed-plist.sh`

Tìm khối `# Auth passwords`:

```bash
# Auth passwords
add_str FullPassword           "${TVNC_FULL_PASSWORD:-}"
add_str ViewOnlyPassword       "${TVNC_VIEWONLY_PASSWORD:-}"
```

Thêm **ngay bên dưới** hai dòng đó:

```bash
# Control socket token (bật điều khiển từ xa qua LAN; rỗng = chỉ localhost)
add_str CtlToken               "${TVNC_CTL_TOKEN:-}"
```

---

## File 2/3 — `.github/workflows/build.yml`

Tìm hai dòng secret:

```yaml
          TVNC_FULL_PASSWORD: ${{ secrets.TVNC_FULL_PASSWORD }}
          TVNC_VIEWONLY_PASSWORD: ${{ secrets.TVNC_VIEWONLY_PASSWORD }}
```

Thêm **ngay bên dưới**, thụt lề y hệt:

```yaml
          TVNC_CTL_TOKEN: ${{ secrets.TVNC_CTL_TOKEN }}
```

Rồi tạo secret: **Settings → Secrets and variables → Actions →
New repository secret**, tên `TVNC_CTL_TOKEN`, giá trị là token của bạn.

---

## File 3/3 — `src/trollvncserver.mm`

Năm chỗ sửa. Số dòng theo bản gốc; nếu lệch thì tìm theo đoạn mã trích dẫn.

### 3.0 — Thêm hai header (bắt buộc)

Mình đã kiểm tra: file gốc **chưa** include `dlfcn.h` và `signal.h`, mà patch
cần cả hai (`dlopen`/`dlsym` và `kill`). Tìm dòng:

```objc
#import <arpa/inet.h>
```

Thêm **ngay bên dưới**:

```objc
#import <dlfcn.h>
#import <signal.h>
```

### 3.1 — Khai báo biến và SPI (khoảng dòng 69)

Tìm:

```objc
static int gTvCtlPort = 0;        // port for control connections (0 = disabled)
```

Thêm **ngay bên dưới**:

```objc
static NSString *gTvCtlToken = nil; // token bắt buộc cho kết nối không phải loopback
static BOOL gTvCtlBindAll = NO;     // YES khi có token: nghe trên mọi giao diện mạng

// SPI để liệt kê/mở app. Dùng NSClassFromString khi gọi nên không phải link
// thêm framework nào, và cũng không phải sửa Makefile.
@interface LSApplicationProxy : NSObject
@property(nonatomic, readonly) NSString *applicationIdentifier;
@property(nonatomic, readonly) NSString *localizedName;
@property(nonatomic, readonly) NSString *applicationType;
@property(nonatomic, readonly) NSString *shortVersionString;
@end

@interface LSApplicationWorkspace : NSObject
+ (instancetype)defaultWorkspace;
- (NSArray<LSApplicationProxy *> *)allApplications;
- (BOOL)openApplicationWithBundleID:(NSString *)bundleID;
@end
```

### 3.2 — Đọc token từ prefs (khoảng dòng 890)

Tìm đoạn đọc `ViewOnlyPassword`:

```objc
    NSString *viewPwd = [prefs objectForKey:@"ViewOnlyPassword"];
```

Cuộn xuống hết khối `if` của nó (kết thúc bằng `}`), rồi thêm:

```objc
    // Token cho control socket. Có token thì mới mở ra LAN; không có thì giữ
    // nguyên hành vi gốc là chỉ nghe 127.0.0.1.
    NSString *ctlToken = [prefs objectForKey:@"CtlToken"];
    if ([ctlToken isKindOfClass:[NSString class]] && ctlToken.length > 0) {
        gTvCtlToken = [ctlToken copy];
        gTvCtlBindAll = YES;
        TVLog(@"-daemon: control token set, control socket will listen on all interfaces");
    }
```

### 3.3 — Cho socket nghe ra LAN (khoảng dòng 3571)

Tìm trong `tvStartControlSocketIfNeeded`:

```objc
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK); // 127.0.0.1
```

Thay bằng:

```objc
    addr.sin_addr.s_addr = htonl(gTvCtlBindAll ? INADDR_ANY : INADDR_LOOPBACK);
```

### 3.4 — Ba lệnh mới

Tìm hàm `tvCtlHandleConnection` (khoảng dòng 3860). **Ngay phía trên** dòng
`void tvCtlHandleConnection(int cfd, struct sockaddr_in caddr) {`, dán:

```objc
#pragma mark - App management

static LSApplicationWorkspace *tvAppWorkspace(void) {
    Class cls = NSClassFromString(@"LSApplicationWorkspace");
    if (!cls) {
        TVLog(@"LSApplicationWorkspace unavailable");
        return nil;
    }
    return [cls defaultWorkspace];
}

/// TSV: bundleId \t tên hiển thị \t loại (User/System) \t phiên bản
static NSData *tvCtlTSVForApps(void) {
    LSApplicationWorkspace *ws = tvAppWorkspace();
    if (!ws)
        return [@"ERR Unavailable\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSMutableString *out = [NSMutableString string];
    NSArray<LSApplicationProxy *> *apps = [ws allApplications];
    for (LSApplicationProxy *app in apps) {
        NSString *bid = app.applicationIdentifier ?: @"";
        if (bid.length == 0)
            continue;
        NSString *name = app.localizedName ?: @"";
        NSString *type = app.applicationType ?: @"";
        NSString *ver = app.shortVersionString ?: @"";
        // Ký tự tab trong tên sẽ phá vỡ định dạng TSV.
        name = [name stringByReplacingOccurrencesOfString:@"\t" withString:@" "];
        [out appendFormat:@"%@\t%@\t%@\t%@\n", bid, name, type, ver];
    }
    TVLog(@"Control socket: listed %lu applications", (unsigned long)apps.count);
    return [out dataUsingEncoding:NSUTF8StringEncoding];
}

static NSData *tvCtlLaunchApp(NSString *bundleId) {
    if (bundleId.length == 0)
        return [@"ERR MissingBundleID\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = NO;
    LSApplicationWorkspace *ws = tvAppWorkspace();
    if ([ws respondsToSelector:@selector(openApplicationWithBundleID:)])
        ok = [ws openApplicationWithBundleID:bundleId];

    if (!ok) {
        // Dự phòng: SpringBoardServices. Entitlement
        // com.apple.springboard.launchapplications đã có sẵn.
        void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                         "SpringBoardServices",
                         RTLD_LAZY);
        if (h) {
            int (*sbsLaunch)(CFStringRef, Boolean) =
                (int (*)(CFStringRef, Boolean))dlsym(h, "SBSLaunchApplicationWithIdentifier");
            if (sbsLaunch)
                ok = (sbsLaunch((__bridge CFStringRef)bundleId, false) == 0);
        }
    }

    TVLog(@"Control socket: launch %@ -> %@", bundleId, ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR LaunchFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

static NSData *tvCtlTerminateApp(NSString *bundleId) {
    if (bundleId.length == 0)
        return [@"ERR MissingBundleID\n" dataUsingEncoding:NSUTF8StringEncoding];

    pid_t pid = 0;
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsPid)(CFStringRef, pid_t *) =
            (int (*)(CFStringRef, pid_t *))dlsym(h, "SBSProcessIDForDisplayIdentifier");
        if (sbsPid)
            sbsPid((__bridge CFStringRef)bundleId, &pid);
    }

    if (pid <= 0) {
        TVLog(@"Control socket: terminate %@ -> not running", bundleId);
        return [@"NOT_RUNNING\n" dataUsingEncoding:NSUTF8StringEncoding];
    }

    BOOL ok = (kill(pid, SIGKILL) == 0);
    TVLog(@"Control socket: terminate %@ (pid %d) -> %@", bundleId, pid, ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR KillFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}
```

Sau đó, **bên trong** `tvCtlHandleConnection`, tìm:

```objc
    NSData *resp = nil;
    BOOL keepOpen = NO;
    if (cmd.length == 0) {
```

Chèn **ngay phía trên** `NSData *resp = nil;`:

```objc
    // Cổng này cho phép ngắt toàn bộ client và mở app, nên kết nối từ ngoài
    // máy bắt buộc phải có token. Localhost giữ nguyên như cũ để app TrollVNC
    // trên máy vẫn dùng được.
    BOOL isLoopback = (caddr.sin_addr.s_addr == htonl(INADDR_LOOPBACK));
    if (!isLoopback) {
        NSString *prefix =
            gTvCtlToken.length > 0 ? [NSString stringWithFormat:@"auth %@ ", gTvCtlToken] : nil;
        if (prefix && [cmd hasPrefix:prefix]) {
            cmd = [[cmd substringFromIndex:prefix.length]
                stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        } else {
            TVLog(@"Control socket: unauthorized command from %s", ip ? ip : "?");
            const char *deny = "ERR Unauthorized\n";
            tvCtlWriteAll(cfd, deny, strlen(deny));
            close(cfd);
            return;
        }
    }

```

Cuối cùng, tìm nhánh cuối của chuỗi `else if`:

```objc
    } else {
        resp = [@"ERR Unknown\n" dataUsingEncoding:NSUTF8StringEncoding];
    }
```

Thay bằng:

```objc
    } else if ([cmd isEqualToString:@"apps"]) {
        resp = tvCtlTSVForApps();
    } else if ([cmd hasPrefix:@"launch "]) {
        resp = tvCtlLaunchApp([[cmd substringFromIndex:7]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else if ([cmd hasPrefix:@"terminate "]) {
        resp = tvCtlTerminateApp([[cmd substringFromIndex:10]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else {
        resp = [@"ERR Unknown\n" dataUsingEncoding:NSUTF8StringEncoding];
    }
```

---

## Build

Chạy workflow như lần trước, **nhưng lần này phải tick**:

> ☑ **Build with a bundled Managed.plist (managed configuration)**

Không tick thì `Managed.plist` không được sinh ra, token không tới được máy, và
cổng điều khiển vẫn chỉ nghe localhost.

---

## Thử nghiệm từ Windows

Cổng điều khiển là **46752**. Chạy trong PowerShell (thay IP và token):

```powershell
$ip = "172.30.3.152"; $token = "TOKEN_CUA_BAN"
$c = New-Object Net.Sockets.TcpClient($ip, 46752)
$s = $c.GetStream(); $w = New-Object IO.StreamWriter($s); $r = New-Object IO.StreamReader($s)
$w.WriteLine("auth $token apps"); $w.Flush()
$r.ReadToEnd()
$c.Close()
```

Kết quả mong đợi: nhiều dòng, mỗi dòng một app:

```
com.zing.zalo	Zalo	User	24.10.1
com.apple.Preferences	Cài đặt	System	1.0
```

Thử mở app:

```powershell
$w.WriteLine("auth $token launch com.zing.zalo")
```

Mỗi kết nối chỉ nhận **một lệnh** rồi đóng — đúng thiết kế sẵn có của TrollVNC.

---

## Nếu hỏng

| Triệu chứng | Nguyên nhân thường gặp |
|---|---|
| Không nối được cổng 46752 | Quên tick Managed.plist, hoặc secret `TVNC_CTL_TOKEN` chưa tạo |
| `ERR Unauthorized` | Token gõ sai, hoặc thiếu chữ `auth ` phía trước |
| `ERR Unavailable` khi gọi `apps` | `LSApplicationWorkspace` không nạp được trên iOS của bạn |
| `ERR LaunchFailed` | Sai bundle id, hoặc cả hai đường mở app đều bị chặn |
| `NOT_RUNNING` khi terminate | App vốn không chạy — không phải lỗi |

Bản vá này **chưa được biên dịch thử**. Lỗi build lần đầu là chuyện bình
thường; chép đoạn lỗi trong log GitHub Actions ra là sửa được.
