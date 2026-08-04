# Patch TrollVNC vòng 5: setscale (đổi scale khung hình lúc đang chạy)

Thêm một lệnh vào control socket:

```
setscale <0..1>   -> đổi hệ số scale khung hình NGAY, để giảm tải máy đời cũ
```

TrollVNC **vốn đã có** scale (`gScale`, đặt qua CLI `-s` hoặc pref `Scale`), nhưng
chỉ đọc lúc khởi động. Lệnh này cho **chỉnh runtime từ Control IOS** (hộp *Chất
lượng* → *Scale khung máy gửi*), không phải build lại hay đổi pref.

Vì sao đáng làm: iPhone đời cũ (6s...) khi kéo vuốt phải nén cả màn hình mỗi
khung → trần ~12-15fps dù đặt fps cao. Giảm scale (máy gửi khung nhỏ hơn) → nén
nhẹ hơn nhiều → **mượt hơn thật sự**. Khác với *Độ nét* ở PC (chỉ thu nhỏ sau khi
máy đã nén full — không giảm tải máy).

Chỉ sửa `src/trollvncserver.mm`. Không cần framework/entitlement.

> An toàn về luồng: đổi scale runtime chỉ cần **gán `gScale`**. Luồng xử lý khung
> gọi `maybeResizeFramebufferForRotation()` mỗi khung và tự đọc `gScale`, nên nó
> đổi kích thước framebuffer ở lượt kế **trên đúng luồng đó** — không race. Đổi
> kích thước làm client nối lại một nhịp (client này không đăng ký DesktopSize),
> y như lúc xoay máy.

---

## 1/2 — Hàm mới

`Ctrl+F` tìm `#pragma mark - File transfer`. Đặt con trỏ **ngay trên** dòng đó,
nhấn Enter, rồi dán:

```objc
#pragma mark - Scale

// `setscale <0..1>` — đổi hệ số scale khung hình LÚC ĐANG CHẠY. Chỉ cần gán
// gScale; luồng xử lý khung sẽ tự đổi kích thước framebuffer ở lượt kế.
static NSData *tvCtlSetScale(NSString *arg) {
    double v = [[arg stringByTrimmingCharactersInSet:
                    [NSCharacterSet whitespaceAndNewlineCharacterSet]] doubleValue];
    if (!(v > 0.0 && v <= 1.0))
        return [@"ERR BadScale (can 0 < s <= 1)\n" dataUsingEncoding:NSUTF8StringEncoding];
    gScale = v;
    TVLog(@"Control socket: setscale %.3f", v);
    NSString *ok = [NSString stringWithFormat:@"OK %.3f\n", v];
    return [ok dataUsingEncoding:NSUTF8StringEncoding];
}
```

> `gScale` là biến toàn cục sẵn có ở đầu file (`static double gScale = 1.0;`).

---

## 2/2 — Nối vào bộ điều phối

`Ctrl+F` tìm nhánh `respring` (vòng 4):

```objc
    } else if ([cmd isEqualToString:@"respring"]) {
        resp = tvCtlRespring();
```

Ngay **phía dưới**, chèn:

```objc
    } else if ([cmd hasPrefix:@"setscale "]) {
        resp = tvCtlSetScale([cmd substringFromIndex:9]);
```

---

## Build và thử

Run workflow → ☑ Managed.plist → cài lại.

Trong Control IOS: **Chất lượng → Scale khung máy gửi** → chọn mức (Gốc / 0.75× /
0.5×...) → Áp dụng. Máy đang xem sẽ nối lại một nhịp rồi chạy ở khung nhỏ hơn.

Từ dòng lệnh (thử một máy):

```powershell
cd D:\ControlIOS
.\.venv\Scripts\python.exe -c "import asyncio; from controlios.config import Registry; from controlios.control_channel import ControlChannel; r=Registry.load(); c=ControlChannel('127.0.0.1', 6001, r.settings.control_token) if False else ControlChannel('172.30.0.221', r.settings.control_port, r.settings.control_token); asyncio.run(c.set_scale(0.5)); print('OK scale 0.5')"
```

## Ghi chú

- Giá trị **không lưu qua lần khởi động lại daemon** (gScale reset về pref/mặc
  định). Control IOS nhớ lựa chọn trong `settings.device_scale` và phát lại khi
  bạn Áp dụng; muốn cố định trên máy thì đặt pref `Scale`.
- Đổi scale = đổi kích thước framebuffer → **phiên VNC nối lại ~1 giây**. Đừng đổi
  liên tục.
