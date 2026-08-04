# Patch TrollVNC vòng 3: clipboard UTF-8 và nạp ảnh vào Thư viện

Thêm ba lệnh nữa vào control socket:

```
clipset <số byte>   -> đặt clipboard của máy (UTF-8) — dán khối chữ dài, đủ dấu
clipget             -> đọc clipboard hiện tại của máy
savephoto <đường dẫn> -> nạp một ảnh đã có trên máy vào Thư viện Ảnh
```

Vì sao đáng làm:

- **Clipboard** đi vòng qua giới hạn client VNC: `asyncvnc` chỉ có `ClientCutText`
  latin-1 nên đường clipboard cũ không gửi được tiếng Việt. Đặt thẳng vào
  `UIPasteboard` thì **dán được khối chữ dài, đủ dấu và cả emoji** cho hàng loạt
  máy — nhanh hơn hẳn gõ từng ký tự qua keysym.
- **`savephoto`** giải quyết giới hạn lớn nhất còn lại: chép ảnh vào thư mục
  **không** làm nó hiện trong app Ảnh vì iOS quản ảnh bằng cơ sở dữ liệu riêng.
  Phải nạp qua `PHPhotoLibrary` thì ảnh mới dùng được trong Shopee/TikTok.

Chỉ sửa **một file**: `src/trollvncserver.mm`, cộng thêm một framework khi build.

> Làm sau [trollvnc-patch.md](trollvnc-patch.md) (vòng 1) và
> [trollvnc-patch-2.md](trollvnc-patch-2.md) (vòng 2). Lệnh `clipset` dùng lại
> đúng cơ chế tách dòng lệnh khỏi dữ liệu (`pending`/`pendingLength`) mà vòng 2
> đã dựng cho `put` — **nếu chưa làm vòng 2 thì làm trước**.

---

## ⚠️ Đọc trước khi làm `savephoto`

`PHPhotoLibrary` là dữ liệu nhạy cảm nên iOS chặn bằng **TCC** (bảng quyền riêng
tư). `trollvncserver` là **tiến trình nền, không có UI**, nên khi nó xin quyền
Thư viện Ảnh thì **không có hộp thoại nào bật lên để bấm Đồng ý** — lời xin có
thể bị từ chối lặng lẽ và `savephoto` trả `ERR Denied`.

Có mấy đường xử lý, thử theo thứ tự:

1. **Cấp quyền sẵn bằng TrollStore**: TrollStore ký app với entitlement mạnh,
   và nhiều bản để cấp full disk / photos access. Thử thêm
   `com.apple.private.tcc.allow` với giá trị `kTCCServicePhotos` vào entitlements
   lúc build (mục dưới). Đây là cách sạch nhất nếu chạy được.
2. **Ghi thẳng bản ghi TCC**: trên máy jailbreak, chèn dòng cho phép vào
   `/var/mobile/Library/TCC/TCC.db` (cần root — đi qua **kênh SSH** có sẵn). Cách
   này chỉ hợp máy đã jailbreak.
3. **Chuyển việc nạp sang chính app TrollVNC** (có UI) thay vì daemon: app xin
   quyền một lần, người dùng bấm Đồng ý, từ đó nạp ảnh được. Nặng hơn nhưng chắc
   ăn nhất cho máy chỉ có TrollStore.

Khuyến nghị: **làm `clipset`/`clipget` trước** (rủi ro thấp, giá trị cao ngay),
build và thử; rồi mới thử `savephoto` trên **một** máy, đọc `ERR` trả về để biết
đường nào hợp với bản của bạn. Phía Control IOS đã sẵn sàng cho cả ba lệnh nên
không phải sửa gì thêm ở PC.

---

## 1/5 — Thêm framework khi build (chỉ cho `savephoto`)

Đọc `Makefile` thật của TrollVNC thì thấy:

- **`UIKit` đã có sẵn** (dòng `trollvncserver_FRAMEWORKS += UIKit`) vì
  `ClipboardManager.mm` vốn dùng `UIPasteboard`. Nên **`clipset`/`clipget` không
  cần thêm framework nào**.
- **`Photos` thì chưa có** → chỉ `savephoto` mới cần.

Nếu bạn **chỉ làm clipboard**, bỏ qua hẳn bước này. Nếu làm `savephoto`, thêm một
dòng ngay **sau dòng `trollvncserver_FRAMEWORKS += UserNotifications`**:

```make
trollvncserver_FRAMEWORKS += Photos
```

---

## 2/5 — Import (chỉ cho `savephoto`)

`ClipboardManager.h` **đã được import sẵn** ở đầu `src/trollvncserver.mm` (cùng
`UIKit`), nên clipboard không cần thêm import. Chỉ `savephoto` cần — thêm vào cuối
cụm `#import`:

```objc
#import <Photos/Photos.h>
```

---

## 3/5 — Ba hàm mới

`Ctrl+F` tìm `#pragma mark - File transfer` (đã thêm ở vòng 2). Đặt con trỏ
**ngay trên** dòng đó, nhấn Enter tạo dòng trống, rồi dán:

```objc
#pragma mark - Clipboard

// Đọc đúng `size` byte payload (đã gồm phần bị vòng đọc dòng lệnh nuốt trước)
// rồi đặt làm clipboard. Tái dùng ClipboardManager sẵn có — chính lớp mà đường
// clipboard VNC dùng — thay vì gọi thẳng UIPasteboard, cho nhất quán và tránh
// vòng lặp echo (setStringFromRemote bỏ qua callback + thông báo hệ thống 1 lần).
static NSData *tvCtlSetClipboard(int cfd, NSString *spec, const uint8_t *pending,
                                 size_t pendingLength) {
    long long size = [[spec stringByTrimmingCharactersInSet:
                          [NSCharacterSet whitespaceAndNewlineCharacterSet]] longLongValue];
    if (size < 0 || size > 16 * 1024 * 1024) // 16 MiB dư sức cho chữ; lớn hơn là gõ nhầm
        return [@"ERR BadSize\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSMutableData *buffer = [NSMutableData dataWithCapacity:(NSUInteger)size];

    // Phần đã bị nuốt trước phải ghi vào trước tiên.
    if (pendingLength > 0) {
        size_t take = (size_t)MIN((uint64_t)pendingLength, (uint64_t)size);
        [buffer appendBytes:pending length:take];
    }

    struct timeval tv;
    tv.tv_sec = 15;
    tv.tv_usec = 0;
    setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint8_t chunk[65536];
    while ((long long)buffer.length < size) {
        size_t want = (size_t)MIN((uint64_t)sizeof(chunk), (uint64_t)(size - buffer.length));
        ssize_t n = recv(cfd, chunk, want, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (n == 0)
            break;
        [buffer appendBytes:chunk length:(NSUInteger)n];
    }

    if ((long long)buffer.length != size)
        return [@"ERR Incomplete\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSString *text = [[NSString alloc] initWithData:buffer encoding:NSUTF8StringEncoding];
    if (!text)
        text = @"";
    [[ClipboardManager sharedManager] setStringFromRemote:text];

    NSString *ok = [NSString stringWithFormat:@"OK %lld\n", size];
    return [ok dataUsingEncoding:NSUTF8StringEncoding];
}

// Trả về `OK <n>\n` rồi đúng n byte nội dung clipboard.
static NSData *tvCtlGetClipboard(void) {
    NSString *text = [[ClipboardManager sharedManager] currentString] ?: @"";
    NSData *body = [text dataUsingEncoding:NSUTF8StringEncoding] ?: [NSData data];
    NSMutableData *resp = [NSMutableData data];
    NSString *header = [NSString stringWithFormat:@"OK %lu\n", (unsigned long)body.length];
    [resp appendData:[header dataUsingEncoding:NSUTF8StringEncoding]];
    [resp appendData:body];
    return resp;
}

#pragma mark - Photo library

// Nạp một file ảnh đã có trên máy vào Thư viện Ảnh. Chép vào thư mục thường
// không đủ — iOS quản ảnh bằng CSDL riêng, phải qua PHPhotoLibrary.
static NSData *tvCtlSavePhoto(NSString *path) {
    path = [path stringByTrimmingCharactersInSet:
               [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (path.length == 0 || ![path hasPrefix:@"/"] ||
        [path rangeOfString:@".."].location != NSNotFound)
        return [@"ERR BadPath\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (![[NSFileManager defaultManager] fileExistsAtPath:path])
        return [@"ERR NotFound\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSURL *fileURL = [NSURL fileURLWithPath:path];
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block BOOL ok = NO;
    __block NSError *err = nil;

    [[PHPhotoLibrary sharedPhotoLibrary] performChanges:^{
        [PHAssetCreationRequest creationRequestForAssetFromImageAtFileURL:fileURL];
    } completionHandler:^(BOOL success, NSError *error) {
        ok = success;
        err = error;
        dispatch_semaphore_signal(done);
    }];

    // Chờ nạp xong (giới hạn 30s) để trả lời đúng trạng thái cho PC.
    if (dispatch_semaphore_wait(done,
            dispatch_time(DISPATCH_TIME_NOW, (int64_t)(30 * NSEC_PER_SEC))) != 0)
        return [@"ERR Timeout\n" dataUsingEncoding:NSUTF8StringEncoding];

    if (ok) {
        TVLog(@"Control socket: savephoto %@ -> OK", path);
        return [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
    }

    // TCC từ chối thường rơi vào đây. Xem phần cảnh báo đầu tài liệu.
    TVLog(@"Control socket: savephoto %@ -> FAIL %@", path, err);
    NSString *msg = err.localizedDescription.length
        ? [NSString stringWithFormat:@"ERR %@\n", err.localizedDescription]
        : @"ERR Denied\n";
    return [msg dataUsingEncoding:NSUTF8StringEncoding];
}

```

---

## 4/5 — Nối ba lệnh vào bộ điều phối

`Ctrl+F` tìm (nhánh đã thêm ở vòng 2):

```objc
    } else if ([cmd hasPrefix:@"put "]) {
        resp = tvCtlReceiveFile(cfd, [cmd substringFromIndex:4], pending, pendingLength);
```

Ngay **phía dưới** hai dòng đó, chèn:

```objc
    } else if ([cmd hasPrefix:@"clipset "]) {
        resp = tvCtlSetClipboard(cfd, [cmd substringFromIndex:8], pending, pendingLength);
    } else if ([cmd isEqualToString:@"clipget"]) {
        resp = tvCtlGetClipboard();
    } else if ([cmd hasPrefix:@"savephoto "]) {
        resp = tvCtlSavePhoto([cmd substringFromIndex:10]);
```

Lưu ý: `clipset` phải nằm ở nhánh có `pending`/`pendingLength` (giống `put`) vì
payload đi liền sau dòng lệnh. `clipget` và `savephoto` chỉ đọc dòng lệnh nên đặt
đâu trong chuỗi `else if` cũng được.

---

> **Cập nhật quan trọng (đã áp vào fork):** gọi thẳng `performChanges` khi tiến
> trình nền **chưa được cấp quyền** Thư viện có thể làm framework Photos *abort*
> — daemon chết, socket đóng, PC nhận **reply rỗng** (không phải `ERR`). Bản đã
> áp vì thế **xin quyền trước** (`requestAuthorizationForAccessLevel:` addOnly),
> `@try` quanh `performChanges`, và phân biệt **ảnh/video** theo đuôi file
> (`.mov`/`.mp4`/`.m4v` → video). Nhờ vậy trường hợp bị chặn trả về `ERR Denied
> status=<n>` đọc được thay vì crash.

## 5/5 — (Chỉ cho `savephoto`) entitlement Thư viện Ảnh

**Đã áp vào fork:** thêm `com.apple.private.tcc.allow` →
`kTCCServicePhotos` + `kTCCServicePhotosAdd` vào
`app/TrollVNC/TrollVNC/TrollVNC.entitlements`, để `requestAuthorization` được cấp
quyền **không cần hộp thoại** (tiến trình nền không bật được hộp thoại).

Ngoài ra `src/trollvncserver.entitlements` (trỏ tới file trên) **vốn đã có sẵn**:

- `com.apple.private.security.storage.Photos`
- `com.apple.private.security.storage.PhotosLibraries`
- `com.apple.private.security.storage.TCC`
- `platform-application` (được đối xử như binary hệ thống)

Với bộ quyền này, `savephoto` **có khả năng chạy được ngay** mà không phải thêm gì.
Nên **cứ build và thử trước đã**.

Nếu vẫn trả `ERR Denied`, thử theo thứ tự:

1. Thêm vào `TrollVNC.entitlements` (trước `</dict>`):

   ```xml
   <key>com.apple.private.tcc.allow</key>
   <array>
       <string>kTCCServicePhotos</string>
       <string>kTCCServicePhotosAdd</string>
   </array>
   ```

2. Máy jailbreak: ghi thẳng bản ghi cho phép vào `/var/mobile/Library/TCC/TCC.db`
   qua **kênh SSH** có sẵn (cần root).
3. Chuyển việc nạp ảnh sang chính **app TrollVNC** (có UI, xin quyền một lần).

`clipset`/`clipget` **không cần** entitlement gì thêm — `com.apple.Pasteboard.background-access`
đã bật sẵn nên clipboard chạy tốt từ tiến trình nền.

---

## Build và thử

`Ctrl+S` → Commit & Push → Actions → Run workflow → ☑ **Managed.plist** → cài lại.

Clipboard:

```powershell
cd D:\ControlIOS
# Đặt rồi đọc lại — phải khớp đúng chữ có dấu
.\.venv\Scripts\python.exe -c "import asyncio; from controlios.control_channel import ControlChannel; c=ControlChannel('172.30.0.221', token='TOKEN'); asyncio.run(c.set_clipboard('Xin chào bạn')); print(asyncio.run(c.get_clipboard()))"
```

Trên iPhone, mở một ô nhập chữ, giữ để hiện **Dán** — nội dung phải là "Xin chào
bạn" đủ dấu. Trong Control IOS: nút **Gõ chữ…** → tick *Đặt vào clipboard máy*.

Nạp ảnh (thử trên một máy trước):

```powershell
.\.venv\Scripts\python.exe -c "import asyncio; from controlios.control_channel import ControlChannel; c=ControlChannel('172.30.0.221', token='TOKEN'); asyncio.run(c.push_photo(r'D:\anh.jpg'))"
```

Mở app Ảnh trên iPhone — ảnh phải xuất hiện. Trong Control IOS: nút **Nạp ảnh…**
trên thanh công cụ.

## Nếu hỏng

| Trả lời | Nghĩa là |
|---|---|
| `ERR Unknown` | Máy chưa cài bản vá vòng 3 (hoặc build chưa gồm lệnh mới) |
| `ERR BadSize` | `clipset` nhận cỡ âm hoặc quá 16 MiB — gần như luôn là gõ nhầm |
| `ERR Incomplete` | Mất kết nối giữa lúc gửi clipboard; cứ gửi lại |
| `ERR BadPath` / `ERR NotFound` | `savephoto` cần đường dẫn tuyệt đối tới file **đã có** trên máy (đẩy bằng `put` trước, hoặc dùng `push_photo`) |
| `ERR Denied` / `ERR Timeout` | TCC từ chối quyền Thư viện Ảnh — xem phần cảnh báo đầu tài liệu |
