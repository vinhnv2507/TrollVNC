# Patch TrollVNC vòng 6: reset dữ liệu app (wipeapp / snapshot / restore)

Thêm ba lệnh vào control socket để **quản lý dữ liệu app từ xa** cho cả farm:

```
wipeapp  <bundle id>   -> xoá dữ liệu app (Documents/Library/tmp/SystemData) như cài lại
snapshot <bundle id>   -> lưu bản sao dữ liệu app hiện tại NGAY TRÊN MÁY
restore  <bundle id>   -> thay dữ liệu hiện tại bằng bản snapshot đã lưu
```

Chạy được trên **máy chỉ có TrollStore, KHÔNG cần jailbreak** — vì mọi thứ nằm ở
`/var` (phân vùng Data), không đụng phân vùng hệ thống bị SSV niêm phong. Daemon
control của TrollVNC đã sẵn entitlement (`platform-application`,
`com.apple.private.security.storage.AppDataContainers`, `no-container`) để đọc/ghi
container của app khác, và chạy bằng **root**.

Chỉ sửa `src/trollvncserver.mm`. Không thêm framework/entitlement.

## Cơ chế

- **Container dữ liệu** của app ở `/var/mobile/Containers/Data/Application/<UUID>/`
  gồm `Documents`, `Library`, `tmp`, `SystemData`. Cố tình **không đụng**
  `.com.apple.mobile_container_manager.metadata.plist` ở gốc — iOS cần nó để nhận
  diện container.
- **wipeapp** = xoá sạch nội dung 4 thư mục trên, GIỮ container. App tự dựng lại
  khi mở → như vừa cài lại.
- **snapshot** = `copyItemAtPath` 4 thư mục sang `/var/mobile/controlios-snap/<bundle id>/`.
  Bản snapshot ở lại trên máy, mỗi máy giữ bản riêng → không phải truyền qua PC.
  iOS **không có `tar`** nên chép cây thư mục bằng `NSFileManager` chứ không nén.
- **restore** = xoá 4 thư mục hiện tại rồi copy từ snapshot về, sau đó **chown về
  mobile (uid/gid 501)**. Bắt buộc chown: root vừa chép nên file thuộc root, app
  chạy dưới mobile sẽ không đọc/ghi được nếu không trả quyền.

Phía Control IOS **đóng app trước** (`terminate`) mỗi thao tác để file được ghi/nhả,
và nên relaunch sau khi restore.

## Giới hạn (đọc kỹ trước khi kỳ vọng "máy chưa từng cài")

- **KHÔNG đụng keychain**. Token/khoá app lưu trong keychain (`/var/Keychains/`,
  DB mã hoá) vẫn còn qua wipe. Muốn xoá keychain phải jailbreak.
- **IDFV** không đổi (chỉ reset khi gỡ hẳn hết app cùng nhà phát hành).
- Tracking **server-side** (IP, đời máy, tài khoản) không bị đụng.
- Máy phải **đang mở khoá** (data protection) mới đọc/ghi container được — farm để
  máy không đặt passcode là hết vướng.

Nói cách khác: đây là **"clear data" ở mức file** (bắt đầu lại phiên sạch / gỡ kẹt
app), không phải xoá dấu vết thiết bị.

## Dùng trong Control IOS

- Bảng **Ứng dụng** → chuột phải một app → **Xoá dữ liệu / Lưu snapshot / Khôi phục**.
  Áp cho tất cả máy đang chọn ở lưới. Xoá và Khôi phục có hỏi xác nhận.
- Trong **kịch bản**:

  ```
  snapshot com.zing.zalo      # lưu trạng thái đăng nhập sạch một lần
  ...
  wipeapp com.zing.zalo       # xoá về trắng
  restore com.zing.zalo       # hoặc quay lại bản đã lưu
  ```

## Thao tác NGAY TRÊN máy (trong app TrollVNC)

Ngoài điều khiển từ PC, app TrollVNC trên máy có thêm nút **"App Data"** trên
thanh điều hướng (hiện cả ở màn hình *managed*). Bấm vào → liệt kê app người dùng
(app hỏi daemon `apps` qua control socket loopback `46752`, không cần auth) → chạm
một app → **Lưu snapshot / Xoá dữ liệu / Khôi phục** (Xoá và Khôi phục có xác
nhận). App tự đóng app đích trước, rồi gửi lệnh xuống daemon (root) làm việc thật.
Chỉ tác động **chính máy đó** — hàng loạt vẫn dùng Control IOS trên PC.

Thay đổi nằm ở `app/TrollVNC/TrollVNC/`:
- `TVNCClientListController.{h,m}`: thêm lớp `TVNCAppDataController` (tái dùng
  `TVNCConnect/TVNCSendLine/TVNCReadAll` sẵn có). Đặt chung file để khỏi sửa
  `project.pbxproj`.
- `TVNCRootListController.m`: nút "App Data" (đặt **trước** nhánh managed để hiện
  cả ở màn quản lý) + `showAppData` mở màn hình đó.

## Build và thử

Run workflow → cài lại. Thử một máy từ dòng lệnh:

```powershell
cd D:\ControlIOS
.\.venv\Scripts\python.exe -c "import asyncio; from controlios.config import Registry; from controlios.control_channel import ControlChannel; r=Registry.load(); c=ControlChannel('172.30.0.221', r.settings.control_port, r.settings.control_token); asyncio.run(c.snapshot_app('com.zing.zalo')); print('snapshot OK')"
```
