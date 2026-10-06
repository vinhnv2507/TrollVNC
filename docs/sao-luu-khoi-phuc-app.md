# Spec sao lưu / khôi phục dữ liệu app — Control IOS

Tài liệu độc lập, gửi được sang dự án khác để tái hiện cùng cơ chế.
Không phải Apple Backup, không phải `idevicebackup2`, iTunes, MDM, tar IPA.

Cập nhật theo mã nguồn Control IOS (daemon iOS CTRIOS + kênh control PC).

---

## 1. Bản chất

Đây là **sao chép cây thư mục dữ liệu (file-level container copy)** ngay trên
máy iOS, do **daemon root** của TrollStore thực hiện.

| Là | Không phải |
|---|---|
| Copy 4 thư mục `Documents`, `Library`, `tmp`, `SystemData` trong data container của app | Apple Backup / iTunes / Finder |
| Lưu bản snapshot trên phân vùng Data: `/var/mobile/controlios-snap/` | `idevicebackup2` / MDM backup |
| Chạy trên máy **chỉ có TrollStore, không cần jailbreak** | tar/zip IPA hay đụng App Store binary |
| "Clear data" mức file, rồi khôi phục lại đúng cây đó | Reset identity thiết bị |

Data container của app nằm ở:

```
/var/mobile/Containers/Data/Application/<UUID>/
```

UUID lấy qua `LSApplicationProxy.dataContainerURL` theo bundle id. Cố tình
**không đụng** file metadata ở gốc container:

```
.com.apple.mobile_container_manager.metadata.plist
```

iOS dùng file này để nhận diện container. Xoá đi thì app có thể không mở lại được.

iOS không có `tar` sẵn trong môi trường daemon này, nên chép cây bằng
`NSFileManager` (đệ quy, chịu lỗi từng file), không nén.

---

## 2. Điều kiện để chạy được

### 2.1. Môi trường máy

- App/daemon cài bằng **TrollStore**, chạy **root**.
- Máy **đang mở khoá** (data protection). Farm nên không đặt passcode.
- Không cần jailbreak, không ghi phân vùng hệ thống (SSV). Mọi thứ nằm ở `/var`.

### 2.2. Entitlement tối thiểu (bắt buộc)

Daemon phải có ít nhất:

```
platform-application
com.apple.private.security.storage.AppDataContainers
com.apple.private.security.no-container
```

Không có `AppDataContainers` thì root vẫn không đọc/ghi data container của app khác.

Các entitlement hữu ích kèm theo (Control IOS đang dùng):

```
com.apple.private.MobileContainerManager.allowed
com.apple.private.security.container-manager
com.apple.frontboard.launchapplications
com.apple.springboard.launchapplications
```

(để tìm container qua LaunchServices và `terminate`/`launch` app trước/sau thao tác).

### 2.3. Kênh lệnh

TCP **control socket**, mặc định cổng **46752**.

- Mỗi kết nối = **đúng một dòng lệnh UTF-8 kết thúc `\n`**, rồi đóng (trừ `subscribe`).
- Kết nối từ ngoài loopback phải có tiền tố: `auth <token> `.
- Loopback (app CTRIOS trên chính máy) không cần token.
- Timeout PC cho snapshot / restore / wipe: **180 giây**.
- Timeout PC cho `snapclear`: 60 giây.
- `put` / `getfile`: chờ nhận/ghi file, timeout đọc tối thiểu 60 giây.

---

## 3. Cấu trúc dữ liệu trên máy

### 3.1. Data container (nguồn / đích)

```
<dataContainer>/Documents
<dataContainer>/Library
<dataContainer>/tmp
<dataContainer>/SystemData
```

Chỉ 4 thư mục này. Không copy file rời ở gốc container.

### 3.2. Kho snapshot

```
/var/mobile/controlios-snap/<bundle id>/<tên snapshot>/
    Documents/
    Library/
    tmp/
    SystemData/
```

- Mỗi app nhiều bản, mỗi tên một bản.
- Trùng tên → **ghi đè** (xoá thư mục cũ rồi copy lại).
- Snapshot nằm **ngay trên máy**, không bắt buộc đi qua PC.

### 3.3. Quy tắc tên snapshot

`tvValidSnapName`:

- Không rỗng, tối đa **64** ký tự.
- Không chứa `/`.
- Không chứa `..`.
- Không bắt đầu bằng `.`.

Tên trống khi gọi `snapshot` → tự sinh `yyyyMMdd-HHmmss` (locale `en_US_POSIX`).

PC khi import thêm cấm `\` và khoảng trắng trong bundle id.

---

## 4. Thuật toán trên iOS

Toàn bộ nằm trong daemon: `trollvncserver.mm`.

Hằng số:

```
uid/gid mobile = 501 / 501
snapshot root  = /var/mobile/controlios-snap
subdirs        = Documents, Library, tmp, SystemData
```

### 4.1. Tìm container

```
tvDataContainerPath(bundleId):
    duyệt LSApplicationWorkspace.allApplications
    nếu applicationIdentifier == bundleId
        return app.dataContainerURL.path
    không thấy → nil  (lệnh trả NOT_FOUND)
```

### 4.2. Copy chịu lỗi — `tvCopyTree(src, dst)`

Không dùng một lần `copyItemAtPath` cho cả cây (một file socket/cache khoá sẽ
làm hỏng cả bản, hay gặp ở Shopee `Library/Caches` và `tmp`).

```
nếu src là thư mục:
    tạo dst (kèm intermediate)
    đệ quy từng con
    trả YES nếu tạo được dst
nếu src là file thường hoặc symlink:
    copyItemAtPath; lỗi thì LOG và bỏ qua, vẫn trả YES
nếu socket / fifo / device:
    bỏ qua, trả YES
```

### 4.3. Trả quyền — `tvChownTree(path)`

Sau restore, file do **root** chép nên thuộc root. App chạy uid 501 sẽ không
đọc/ghi được.

```
lchown(path, 501, 501)     # lchown để không đi theo symlink
nếu là thư mục: đệ quy từng con
```

### 4.4. Xoá nội dung, giữ thư mục — `tvEmptyDir(dir)`

Xoá từng item bên trong, **giữ** chính thư mục (giữ ownership mobile của nó).
Thư mục không tồn tại → coi như OK.

### 4.5. wipeapp `<bundle id>`

```
data = tvDataContainerPath(bundleId)     # nil → NOT_FOUND
với mỗi sub trong [Documents, Library, tmp, SystemData]:
    tvEmptyDir(data/sub)
trả OK hoặc ERR Partial
```

Giữ nguyên container. App tự dựng lại 4 thư mục khi mở → như vừa cài lại.
**Không đụng keychain.**

Nên `terminate` app trước.

### 4.6. snapshot `<bundle id> [tên]`

```
nếu tên trống → yyyyMMdd-HHmmss
nếu tên không hợp lệ → ERR BadName
data = container                         # nil → NOT_FOUND
snap = /var/mobile/controlios-snap/<bundle>/<tên>
xoá snap nếu đã có (ghi đè)
tạo thư mục snap
với mỗi sub tồn tại trong data:
    tvCopyTree(data/sub, snap/sub)
trả "OK <tên>\n"
```

Không chown kho snapshot (root giữ cũng được; chỉ chown khi restore vào container).

### 4.7. snaplist `<bundle id>`

TSV, mỗi dòng:

```
<tên>\t<epoch giây>\t<tổng byte file thường>
```

- Chưa có kho → trả rỗng (không phải lỗi).
- Bỏ qua 4 tên reserved `Documents|Library|tmp|SystemData` ngay dưới
  `<bundle>/` (rác của format snapshot 1-bản cũ).
- Chỉ liệt kê thư mục con.
- Tab trong tên bị thay bằng space.
- PC sort mới nhất lên đầu theo epoch.

### 4.8. restore `<bundle id> <tên>`

```
tên bắt buộc, phải hợp lệ          # thiếu → ERR BadArg / ERR BadName
data = container                   # nil → NOT_FOUND
snap = kho/<tên>                   # không phải thư mục → ERR NoSnapshot
với mỗi sub có trong snap:
    xoá data/sub
    tvCopyTree(snap/sub, data/sub)
    tvChownTree(data/sub)          # BẮT BUỘC
trả OK hoặc ERR Partial
```

Nên `terminate` trước, `launch` lại sau.

### 4.9. snapdel `<bundle id> <tên>`

Xoá đúng một thư mục snapshot. Không có → `NOT_FOUND`.

### 4.10. snapclear `<bundle id>`

Xoá cả `/var/mobile/controlios-snap/<bundle id>/` (mọi bản + rác format cũ).
Không có kho → vẫn `OK`.

---

## 5. Giao thức control socket

Cổng: `46752`.

Gửi:

```
[auth <token> ]<lệnh>\n
```

Trả lời text UTF-8, thường kết thúc `\n`. Lỗi bắt đầu bằng `ERR ` hoặc `NOT_FOUND`.

### 5.1. Lệnh dữ liệu app

| Lệnh | Trả lời thành công | Lỗi thường gặp |
|---|---|---|
| `wipeapp <bundle>` | `OK` | `ERR BadArg`, `NOT_FOUND`, `ERR Partial` |
| `snapshot <bundle> [tên]` | `OK <tên>` | `ERR BadArg`, `ERR BadName`, `NOT_FOUND` |
| `snaplist <bundle>` | TSV hoặc rỗng | `ERR BadArg` |
| `restore <bundle> <tên>` | `OK` | `ERR BadArg`, `ERR BadName`, `NOT_FOUND`, `ERR NoSnapshot`, `ERR Partial` |
| `snapdel <bundle> <tên>` | `OK` | `ERR BadArg`, `ERR BadName`, `NOT_FOUND` |
| `snapclear <bundle>` | `OK` | `ERR BadArg` |
| `terminate <bundle>` | `OK` / `NOT_RUNNING` | — |
| `launch <bundle>` | `OK` | — |
| `container <bundle>` | `<dataPath>\t<bundlePath>` | `NOT_FOUND`, `ERR Unavailable` |

### 5.2. Truyền file (xuất/nạp snapshot qua PC)

**`ls <path tuyệt đối>`** — TSV:

```
<tên>\t<cỡ byte>\t<1 nếu thư mục, 0 nếu file>
```

**`getfile <path>`** — chỉ đọc trong `/var/mobile/` (sau `stringByStandardizingPath`
+ resolve symlink). Không cho `/var/root` hay file hệ thống.

```
OK <size>\n
<size byte nhị phân>
```

Lỗi: `ERR BadPath`, `ERR CannotRead`, `ERR NotFile`.

**`put <size> <path tuyệt đối>`** rồi ngay sau đó **đúng `size` byte nhị phân**.

- `size` từ 0 đến **1 GiB**.
- Path phải bắt đầu `/`, không chứa `..`.
- Daemon tự `mkdir -p` thư mục cha.
- Timeout recv 30 giây/chunk phía iOS.
- Thiếu byte → xoá file dở, trả `ERR Incomplete <written>/<size>`.
- Thành công: `OK <written>`.

Dùng `put` để nạp cây snapshot vào
`/var/mobile/controlios-snap/<bundle>/<tên>/...` mà không cần lệnh mkdir riêng.

---

## 6. Luồng phía PC (Control IOS)

File: `controlios/control_channel.py`, `controlios/vnc/pool.py`.

### 6.1. Nguyên tắc

**Luôn `terminate` app trước** snapshot / restore / wipe, để file được ghi/nhả,
tránh copy cache đang khoá.

Hàng loạt máy: mỗi máy giữ snapshot **riêng trên máy đó**. Muốn khôi phục hàng
loạt thì đặt **cùng một tên**.

### 6.2. Sao lưu tại chỗ (không qua PC)

```
terminate <bundle>
snapshot <bundle> [tên]     → nhận tên thật (máy có thể tự sinh)
```

### 6.3. Khôi phục tại chỗ

```
terminate <bundle>
restore <bundle> <tên>
launch <bundle>             # nên làm, PC có thể để người dùng tự mở
```

### 6.4. Xoá dữ liệu

```
terminate <bundle>
wipeapp <bundle>
```

### 6.5. Xuất snapshot về PC (`backup_app_to_pc`)

Không cần SSH.

```
terminate <bundle>
saved = snapshot <bundle> <tên>
download_tree /var/mobile/controlios-snap/<bundle>/<saved>  → thư mục PC
```

`download_tree` = `ls` đệ quy + `getfile` từng file thường.
Tên file Windows được encode (`<>:"/\|?*%`, tên reserved CON/PRN/...).

Cấu trúc thư mục PC khi backup hàng loạt:

```
<destination>/<slug(tên máy)_slug(key)>/<slug(bundle)>/<slug(tên snapshot)>/
    Documents/
    Library/
    ...
```

Giới hạn song song truyền file: semaphore 4 máy.

### 6.6. Nạp snapshot từ PC lên máy (`import_snapshot`)

```
kiểm tra cây local có ít nhất một trong: Documents, Library, tmp, SystemData
snapdel <bundle> <tên>          # ghi đè an toàn; bỏ qua nếu chưa có
với mỗi file:
    put <size> /var/mobile/controlios-snap/<bundle>/<tên>/<rel posix>
```

Sau khi nạp xong, gọi `restore` như bản nội bộ.

Bỏ qua symlink. Không follow link khi walk.

---

## 7. Giới hạn — đọc trước khi kỳ vọng sai

- **Không đụng keychain** (`/var/Keychains/`). Token/khoá app trong keychain
  vẫn còn sau wipe. Xoá keychain cần jailbreak (và rủi ro cao).
- **IDFV không đổi** (chỉ reset khi gỡ hết app cùng vendor).
- Tracking **server-side** (IP, fingerprint máy, tài khoản) không bị đụng.
- File đang khoá / socket / fifo bị **bỏ qua** khi copy → snapshot có thể thiếu
  cache; với hầu hết app điều này chấp nhận được.
- Máy khoá màn hình / data protection → đọc/ghi container thất bại.
- Đây là "bắt đầu lại phiên sạch / gỡ kẹt app", **không** phải "máy chưa từng cài".

---

## 8. Việc tối thiểu dự án khác cần làm

1. Daemon/root helper trên iOS, entitlement mục 2.2.
2. Resolve bundle id → `dataContainerURL`.
3. Implement `tvCopyTree` chịu lỗi + `lchown` 501 sau restore.
4. Chỉ copy 4 subdir; không xoá metadata plist.
5. Kho snapshot dưới `/var/mobile/...` (hoặc path tương đương trên Data).
6. Đóng app trước mọi thao tác ghi.
7. (Tuỳ chọn) kênh TCP một-lệnh như bảng mục 5 để PC/farm điều khiển.
8. (Tuỳ chọn) `getfile`/`put` nếu muốn chuyển snapshot giữa máy và PC không SSH.

Không cần:

- Backup Apple / lockdownd
- Jailbreak
- Nén tar/zip trên iOS
- Đụng keychain, IDFV, Keychain access groups của app đích

---

## 9. Ví dụ lệnh thô

Auth từ PC (thay TOKEN):

```
auth TOKEN snapshot com.shopee.Application cleanlogin
auth TOKEN snaplist com.shopee.Application
auth TOKEN restore com.shopee.Application cleanlogin
auth TOKEN wipeapp com.shopee.Application
auth TOKEN snapdel com.shopee.Application cleanlogin
auth TOKEN snapclear com.shopee.Application
```

`snaplist` mẫu:

```
cleanlogin	1725520000	184320000
20260912-153045	1725530000	190001000
```

---

## 10. Con trỏ mã nguồn gốc (repo Control IOS)

| Thành phần | File |
|---|---|
| Thuật toán iOS | `.worktrees/ios-release-44/src/trollvncserver.mm` (`tvDataContainerPath` ~6717, `tvCopyTree` ~6771, `tvCtlWipeApp` ~6807, `tvCtlSnapshotApp` ~6865, `tvCtlRestoreApp` ~6942) |
| `getfile` / `put` / `ls` | cùng file, `tvCtlSendMobileFile` ~5671, `tvCtlReceiveFile` ~5544, `tvCtlListDirectory` ~5643 |
| Dispatch lệnh | cùng file ~7170–7267 |
| Entitlements | `.worktrees/ios-release-44/app/TrollVNC/TrollVNC/TrollVNC.entitlements` |
| Client PC | `controlios/control_channel.py` |
| Hàng loạt + backup về PC | `controlios/vnc/pool.py` (`terminate` trước mọi thao tác; `backup_app_to_pc`) |
| Ghi chú patch gốc | `docs/trollvnc-patch-6.md` |

UI trên máy: app CTRIOS, nút **App Data** (gọi cùng control socket loopback 46752).
UI PC: bảng Ứng dụng → chuột phải → Snapshot & khôi phục / Xoá dữ liệu; áp cho mọi máy đang chọn.
