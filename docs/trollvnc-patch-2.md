# Patch TrollVNC vòng 2: truyền file và mở URL

Thêm hai lệnh nữa vào control socket:

```
put <số byte> <đường dẫn>    -> nhận file từ PC, ghi vào máy
openurl <url>                -> mở một URL (dùng để nhờ TrollStore cài .ipa)
```

Chỉ sửa **một file**: `src/trollvncserver.mm`. Bốn chỗ.

> Làm sau khi đã xong [trollvnc-patch.md](trollvnc-patch.md). Nếu chưa có
> `apps`/`launch`/`terminate` thì làm vòng 1 trước.

---

## Vì sao `openurl` lại là cách cài .ipa

Cài app đúng cách cần nói chuyện với `installd` bằng đúng bộ quyền — TrollStore
mới là thứ làm việc đó chuẩn. Thay vì tự cài, ta **nhờ TrollStore cài**:

1. Control IOS mở một web server nhỏ trên PC, phục vụ file `.ipa`
2. Gửi cho từng máy: `openurl apple-magnifier://install?url=http://<ip-pc>:8080/app.ipa`
3. TrollStore trên máy tự tải về và cài

Phần code trên máy vì thế rất nhỏ, ít chỗ hỏng. Entitlement
`com.apple.springboard.opensensitiveurl` đã có sẵn trong TrollVNC.

---

## 1/4 — Tách dòng lệnh khỏi phần dữ liệu

Đây là chỗ **bắt buộc** và dễ bỏ sót. Vòng đọc hiện tại đọc tối đa 1024 byte và
dừng khi thấy ký tự xuống dòng — nghĩa là nó **đã nuốt sẵn phần đầu của file**.
Nếu đọc lại từ socket thì mất đoạn đó, file sẽ hỏng.

`Ctrl+F` tìm:

```objc
    NSString *cmd = [[NSString alloc] initWithBytes:buf length:off encoding:NSUTF8StringEncoding];
```

Bôi đen **dòng đó và 3 dòng dưới** (tới hết dòng `stringByTrimming…`):

```objc
    NSString *cmd = [[NSString alloc] initWithBytes:buf length:off encoding:NSUTF8StringEncoding];
    if (!cmd)
        cmd = @"";
    cmd = [cmd stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
```

Thay bằng:

```objc
    // Split at the first newline. For `put` everything after it is already the
    // start of the payload, so it must not be parsed as text — and it must not
    // be dropped either, or the received file loses its first bytes.
    uint8_t *newline = (uint8_t *)memchr(buf, '\n', off);
    size_t lineLength = newline ? (size_t)(newline - buf) : off;
    const uint8_t *pending = newline ? newline + 1 : buf + off;
    size_t pendingLength = newline ? off - lineLength - 1 : 0;

    NSString *cmd = [[NSString alloc] initWithBytes:buf
                                             length:lineLength
                                           encoding:NSUTF8StringEncoding];
    if (!cmd)
        cmd = @"";
    cmd = [cmd stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
```

---

## 2/4 — Hai hàm mới

`Ctrl+F` tìm `struct sockaddr_in caddr) {` để tới định nghĩa hàm
`tvCtlHandleConnection`. Đặt con trỏ **đầu dòng đó**, nhấn Enter, rồi dán vào
dòng trống vừa tạo:

```objc
#pragma mark - File transfer

// Giới hạn cho chắc: file lớn hơn mức này gần như luôn là gõ nhầm.
static const uint64_t kTvMaxPutBytes = 1024ull * 1024ull * 1024ull; // 1 GiB

/// `put <size> <path>` — đọc đúng `size` byte từ socket rồi ghi ra `path`.
static NSData *tvCtlReceiveFile(int cfd, NSString *spec, const uint8_t *pending,
                                size_t pendingLength) {
    NSRange space = [spec rangeOfString:@" "];
    if (space.location == NSNotFound)
        return [@"ERR Usage put <size> <path>\n" dataUsingEncoding:NSUTF8StringEncoding];

    long long size = [[spec substringToIndex:space.location] longLongValue];
    NSString *path = [[spec substringFromIndex:space.location + 1]
        stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];

    if (size < 0 || (uint64_t)size > kTvMaxPutBytes)
        return [@"ERR BadSize\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (path.length == 0 || ![path hasPrefix:@"/"] ||
        [path rangeOfString:@".."].location != NSNotFound)
        return [@"ERR BadPath\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    [fm createDirectoryAtPath:path.stringByDeletingLastPathComponent
        withIntermediateDirectories:YES
                         attributes:nil
                              error:NULL];

    FILE *fp = fopen(path.fileSystemRepresentation, "wb");
    if (!fp) {
        TVLog(@"Control socket: cannot open %@ for writing: %s", path, strerror(errno));
        return [@"ERR CannotWrite\n" dataUsingEncoding:NSUTF8StringEncoding];
    }

    // Nhận file có thể lâu hơn nhiều so với một dòng lệnh.
    struct timeval tv;
    tv.tv_sec = 30;
    tv.tv_usec = 0;
    setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint64_t written = 0;

    // Phần đã bị vòng đọc dòng lệnh nuốt trước phải ghi ra trước tiên.
    if (pendingLength > 0) {
        size_t take = (size_t)MIN((uint64_t)pendingLength, (uint64_t)size);
        if (take > 0 && fwrite(pending, 1, take, fp) != take) {
            fclose(fp);
            return [@"ERR WriteFailed\n" dataUsingEncoding:NSUTF8StringEncoding];
        }
        written += take;
    }

    uint8_t chunk[65536];
    while (written < (uint64_t)size) {
        size_t want = (size_t)MIN((uint64_t)sizeof(chunk), (uint64_t)size - written);
        ssize_t n = recv(cfd, chunk, want, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (n == 0)
            break;
        if (fwrite(chunk, 1, (size_t)n, fp) != (size_t)n)
            break;
        written += (uint64_t)n;
    }

    fclose(fp);

    if (written != (uint64_t)size) {
        TVLog(@"Control socket: put %@ incomplete (%llu/%lld)", path, written, size);
        unlink(path.fileSystemRepresentation);
        NSString *err = [NSString stringWithFormat:@"ERR Incomplete %llu/%lld\n", written, size];
        return [err dataUsingEncoding:NSUTF8StringEncoding];
    }

    TVLog(@"Control socket: put %@ (%llu bytes)", path, written);
    NSString *ok = [NSString stringWithFormat:@"OK %llu\n", written];
    return [ok dataUsingEncoding:NSUTF8StringEncoding];
}

/// `openurl <url>` — dùng để nhờ TrollStore cài .ipa qua apple-magnifier://
static NSData *tvCtlOpenURL(NSString *urlString) {
    NSURL *url = [NSURL URLWithString:urlString];
    if (!url)
        return [@"ERR BadURL\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = NO;
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsOpen)(CFURLRef, Boolean) =
            (int (*)(CFURLRef, Boolean))dlsym(h, "SBSOpenSensitiveURLAndUnlock");
        if (sbsOpen)
            ok = (sbsOpen((__bridge CFURLRef)url, true) == 0);
    }

    if (!ok) {
        LSApplicationWorkspace *ws = tvAppWorkspace();
        if ([ws respondsToSelector:@selector(openSensitiveURL:withOptions:)])
            ok = [ws openSensitiveURL:url withOptions:nil];
    }

    TVLog(@"Control socket: openurl %@ -> %@", urlString, ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR OpenFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

```

---

## 3/4 — Khai báo thêm một selector

`Ctrl+F` tìm:

```objc
- (BOOL)openApplicationWithBundleID:(NSString *)bundleID;
```

Thêm **ngay bên dưới**:

```objc
- (BOOL)openSensitiveURL:(NSURL *)url withOptions:(NSDictionary *)options;
```

---

## 4/4 — Nối hai lệnh vào

`Ctrl+F` tìm:

```objc
    } else if ([cmd hasPrefix:@"terminate "]) {
```

Ngay **phía trên** dòng đó, chèn:

```objc
    } else if ([cmd hasPrefix:@"put "]) {
        resp = tvCtlReceiveFile(cfd, [cmd substringFromIndex:4], pending, pendingLength);
    } else if ([cmd hasPrefix:@"openurl "]) {
        resp = tvCtlOpenURL([[cmd substringFromIndex:8]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
```

---

## Build và thử

`Ctrl+S` → Commit & Push → Actions → Run workflow → ☑ **Managed.plist** → cài lại.

Thử truyền file bằng script có sẵn:

```powershell
cd D:\ControlIOS
.\.venv\Scripts\python.exe -m controlios.filepush 172.30.0.221 README.md /var/mobile/Documents/thu.txt
```

Thử mở URL:

```powershell
.\tools\tvnc-ctl.ps1 -Device 172.30.0.221 -Command "openurl https://example.com"
```

Safari phải mở lên trên iPhone.

## Nếu hỏng

| Trả lời | Nghĩa là |
|---|---|
| `ERR BadPath` | Đường dẫn phải tuyệt đối (bắt đầu bằng `/`) và không chứa `..` |
| `ERR CannotWrite` | Không có quyền ghi vào thư mục đó — thử `/var/mobile/Documents/` |
| `ERR Incomplete a/b` | Mất kết nối giữa chừng; file hỏng đã bị xoá, cứ gửi lại |
| `ERR OpenFailed` | Cả hai đường mở URL đều bị từ chối |
