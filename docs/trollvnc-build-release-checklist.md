# Checklist build/phát hành TrollVNC đã vá cho ControlIOS

Tài liệu này chốt quy trình build/phát hành bản `_fork/TrollVNC` đang mang các
vá của ControlIOS, đặc biệt là các lệnh control socket như `apps`, `put`,
`openurl`, `clipset`, `savephoto`, `respring`, `assistivetouch`, `keeper`.

Mục tiêu: mỗi lần cần ra bản mới, chỉ việc đi theo checklist này thay vì phải
đọc lại workflow, script đóng gói và các ghi chú vá rải rác.

## 1. Hiểu đầu ra cần lấy

Workflow `_fork/TrollVNC/.github/workflows/build.yml` luôn build 4 scheme:

- `default` -> gói `.deb`
- `rootless` -> gói `.deb`
- `roothide` -> gói `.deb`
- `bootstrap` -> gói `.tipa`

Ý nghĩa thực tế:

- Máy chỉ có TrollStore, không cài `.deb`: lấy artifact `packages-bootstrap`,
  file `TrollVNC_<version>.tipa`
- Máy jailbreak rootless: lấy artifact `packages-rootless`
- Máy jailbreak roothide: lấy artifact `packages-roothide`
- `default` chủ yếu để tương thích kiểu đóng gói cũ

Với farm ControlIOS kiểu "cài app bằng TrollStore rồi điều khiển từ PC", bản
quan trọng nhất thường là `bootstrap` vì nó đóng ra `.tipa`.

## 2. Trước khi build

Trong `_fork/TrollVNC`, kiểm tra các chỗ sau đã có đủ patch bạn cần:

- `src/trollvncserver.mm`
- `include-spi/IOKitSPI.h`
- `src/STHIDEventGenerator.mm`
- `devkit/gen-managed-plist.sh`
- `.github/workflows/build.yml`
- `app/TrollVNC/TrollVNC/TrollVNC.entitlements`

Riêng với vòng tự hồi phục Keeper, cần xác nhận:

- `src/trollvncserver.mm` có `tvCtlKeeper(...)` và dispatcher có nhánh `keeper `
- `app/TrollVNC/TrollVNC/AppDelegate.m` có `TVEnsureKeeperRunning()`, gọi ở cả
  `didFinishLaunchingWithOptions:` và `applicationDidBecomeActive:`
- `keeper/daemon/main.m` canh `trollvncmanager` bằng `kqueue NOTE_EXIT`

Keeper là bundle RIÊNG (`com.controlios.keeper`), build bằng workflow
**Build ControlIOS Keeper**, không đi kèm gói TrollVNC. Máy nào muốn tự hồi phục
thì phải cài CẢ HAI.

## 3. Secrets cần có trên GitHub

Vào GitHub repo fork:

`Settings -> Secrets and variables -> Actions`

Tạo hoặc kiểm tra các secret:

- `TVNC_CTL_TOKEN`
- `TVNC_FULL_PASSWORD` nếu muốn nhúng mật khẩu full-control vào build managed
- `TVNC_VIEWONLY_PASSWORD` nếu muốn nhúng mật khẩu view-only

`TVNC_CTL_TOKEN` là cái rất quan trọng với ControlIOS. Nếu thiếu secret này,
build vẫn có thể xong nhưng cổng control ngoài LAN sẽ không hoạt động như mong
đợi.

## 4. Cách build chuẩn cho ControlIOS

Vào:

`Actions -> Build TrollVNC -> Run workflow`

Chọn đúng branch chứa patch. Nếu đang chuẩn bị phát hành chính thức, nên build
từ branch bạn thực sự sẽ giữ lại, và sau khi ổn thì mới merge/push sang
`release`.

Với ControlIOS, nên bật:

- `is_managed = true`

Điền tối thiểu:

- `desktop_name`: tên máy hiển thị cho VNC client
- `port`: thường là `5901`
- `scale`: theo cấu hình farm của bạn
- `frame_rate_spec`: nếu muốn cố định fps ngay từ lúc build
- `modifier_map`: thường để `std`
- `view_only`: `false`

Reverse mode thường để:

- `reverse_mode = none`

## 5. Vì sao phải tick `is_managed`

Khi `is_managed = true`, workflow sẽ chạy:

- `_fork/TrollVNC/devkit/gen-managed-plist.sh`

Script này sinh:

- `prefs/TrollVNCPrefs/Resources/Managed.plist`

và nhúng các giá trị từ workflow/secrets vào build, gồm cả:

- `CtlToken`
- `Port`
- `DesktopName`
- `Scale`
- `FrameRateSpec`

Nếu không tick `is_managed`, `CtlToken` có thể không đi vào app và ControlIOS sẽ
khó nói chuyện với control socket qua LAN.

## 6. Artifact nào cần tải

Sau khi workflow chạy xong, vào trang run -> `Artifacts`.

Tải ít nhất:

- `packages-bootstrap`
- `dsym-bootstrap`

Nếu bạn còn duy trì máy jailbreak cài `.deb`, tải thêm:

- `packages-rootless`
- `packages-roothide`

Trong `packages-bootstrap`, file cần dùng là:

- `TrollVNC_<version>.tipa`

Đây là file cài bằng TrollStore trên iPhone/iPad.

## 7. Cách workflow đóng ra `.tipa`

Luồng đóng gói hiện tại là:

1. `devkit/bootstrap.sh` đặt `THEBOOTSTRAP=1`
2. `gmake clean package`
3. `devkit/before-package.sh`
   - chép `trollvncserver`
   - chép `trollvncmanager`
   - chép `TrollVNCPrefs.bundle`
   - chép webclients
   - ký lại app bằng `TrollVNC.entitlements`
4. `devkit/after-package.sh`
   - đổi `Applications` thành `Payload`
   - zip thành `TrollVNC.tipa`
   - đặt vào `packages/TrollVNC_<version>.tipa`

Nghĩa là bản `bootstrap` mới là bản đã gom đủ daemon + app + prefs để TrollStore
cài trực tiếp.

## 8. Cài lên máy

Với máy TrollStore:

1. gỡ hoặc cài đè bản `TrollVNC.tipa` cũ
2. cài `TrollVNC_<version>.tipa` mới bằng TrollStore
3. mở app một lần nếu cần để nó áp prefs/bật daemon
4. kiểm tra máy đã quảng bá đúng cổng hoặc nhận control token mới

Với máy jailbreak dùng `.deb`:

1. chọn đúng gói `rootless` hoặc `roothide`
2. cài bằng trình quản lý gói tương ứng
3. respring/restart daemon nếu cần

## 9. Kiểm tra sau phát hành

Từ PC, kiểm tra theo thứ tự:

1. Mở ControlIOS và kết nối được VNC tới máy
2. Thử lệnh control cơ bản:
   - `apps`
   - `clipget`
   - `respring`
3. Thử vòng hồi phục Keeper:
   - `🛡 Keeper -> Kiểm tra + bật lại nếu chết` báo `keeperd đang chạy`
   - buộc ControlIOS chết (vuốt tắt app): keeperd phải mở lại gần như tức thì
   - giết keeperd rồi bấm kiểm tra lại: PC phải báo `đã bật lại Keeper`, và
     màn hình máy KHÔNG bị app Keeper chiếm (daemon spawn thẳng keeperd)

## 10. Cách ra release chính thức

Workflow có nhánh release tự động:

- push vào branch `release`

Khi đó job `release` sẽ:

- đọc `PACKAGE_VERSION` từ `Makefile`
- tạo tag `v<version>`
- tạo GitHub Release
- đính kèm toàn bộ package artifacts

Vậy quy trình an toàn nên là:

1. build thủ công bằng `Run workflow` trên branch đang làm
2. tải `packages-bootstrap` về cài thử trên 1-2 máy
3. xác minh `keeper` và các lệnh control chính
4. khi ổn, push cùng nội dung đó lên branch `release`
5. để GitHub tự tạo release/tag

## 11. Lỗi hay gặp

- Build xong nhưng ControlIOS không gọi được control socket qua LAN:
  thiếu `TVNC_CTL_TOKEN`, hoặc build không bật `is_managed`

- Cài bản mới rồi nhưng máy vẫn hành xử như bản cũ:
  chưa cài đè đúng `.tipa`, hoặc daemon cũ chưa được thay bằng bundle mới

- `keeper start` trả `ERR KeeperNotInstalled`:
  máy chưa cài bundle `com.controlios.keeper`, hoặc bản Keeper build ra thiếu
  `keeperd` trong bundle (xem lại workflow Build ControlIOS Keeper)

- `keeper start` trả `ERR NotRoot`:
  `trollvncserver` không chạy dưới uid 0 — thường do daemon được bật bằng đường
  khác chứ không qua `trollvncmanager`

- Máy vẫn chết cứng sau khi khởi động lại:
  đúng như thiết kế. TrollStore không có launchd nên phải mở khoá máy và bật app
  Keeper một lần bằng tay; từ đó về sau mới tự hồi phục

## 12. Bản phát hành khuyến nghị cho farm hiện tại

Nếu mục tiêu là farm ControlIOS điều khiển máy từ PC và tự hồi phục khi app
chết, bộ phát hành khuyến nghị là:

- artifact: `packages-bootstrap`
- file cài: `TrollVNC_<version>.tipa`
- workflow: `Build TrollVNC`
- `is_managed = true`
- secret bắt buộc: `TVNC_CTL_TOKEN`
- cài kèm: `ControlIOSKeeper.tipa` từ workflow `Build ControlIOS Keeper`

Đây là đường phát hành ít sai nhất cho đội máy dùng TrollStore.
