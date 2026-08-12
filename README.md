# Control IOS

Quản lý và điều khiển nhiều iPhone chạy **TrollVNC** từ một cửa sổ duy nhất —
thay cho việc mở hàng trăm cửa sổ TightVNC rời rạc. Thiết kế cho quy mô **250
máy** trong dải `172.30.x.x:5901`.

Phần mềm nói chuyện thẳng bằng giao thức RFB (VNC) nên nó vừa *xem* được màn
hình vừa *bơm* được thao tác chuột/phím — không cần TightVNC viewer nữa.

```powershell
D:\ControlIOS\.venv\Scripts\python.exe main.py
```

## Vì sao 250 máy vẫn nhẹ

Điểm mấu chốt: RFB chỉ gửi pixel khi client **hỏi**. Mỗi phiên có một *tier*:

| Tier   | Ý nghĩa                                   | Chi phí |
|--------|-------------------------------------------|---------|
| `IDLE` | Vẫn kết nối, vẫn gửi lệnh được, **không hỏi pixel** | ~0 ở PC, nhưng **máy vẫn chụp** |
| `GRID` | Ô đang nhìn thấy, refresh `grid_fps` (mặc định 1 fps), ảnh thu nhỏ | thấp |
| `LIVE` | Máy đang mở ở khung bên phải, full độ phân giải, `live_fps` | cao |

Nên bạn **kết nối cả 250 máy cùng lúc**, nhưng chỉ những ô đang lọt trong
khung nhìn mới thực sự tải hình. Cuộn tới đâu, tier đổi tới đó. Ảnh thu nhỏ
được scale ngay trong luồng mạng nên UI không bao giờ ôm 250 khung hình gốc.

### Chất lượng và tốc độ khung hình

Nút **Chất lượng** nằm ngay dưới màn hình xem máy lớn và chỉ áp dụng cho đúng
máy đang mở trong khung đó. Đổi là **có hiệu lực ngay**, không làm thay đổi các
máy khác.

| | Tốc độ | Độ nét |
|---|---|---|
| Máy đang mở (khung điều khiển) | `live_fps` | `live_long_edge` |
| Ô trong lưới | `grid_fps` | `thumb_long_edge` |

Ba mẫu sẵn: **Mượt** (8 fps / 640px), **Cân bằng** (12 fps / 900px), **Nét**
(20 fps / độ phân giải gốc).

Mặc định `live_long_edge` là **0** — giữ nguyên độ phân giải máy gửi sang. Toạ
độ chuột luôn tính theo khung hình gốc nên thu nhỏ không làm chạm sai chỗ.

**Vì sao mặc định lại là "không thu nhỏ":** việc thu nhỏ ở đây dùng bước nguyên
(lấy mẫu theo stride) cho nhanh, nên từ màn 1338 px chỉ nhảy được xuống 669.
Mà khung điều khiển cao chừng 890 px — tức là thu nhỏ rồi **phóng ngược lên**,
ảnh mờ đi mà chẳng nhanh hơn bao nhiêu. Đặt khác 0 chỉ đáng khi mạng yếu và bạn
chấp nhận mờ để đổi lấy nhẹ.

### Vì sao trước đây thấy giật

Hai chỗ, cùng một nguyên nhân: **thu phóng ảnh nằm ngay trong `paintEvent`**.

Rê chuột trên khung điều khiển gọi `update()` ở mỗi lần chuột nhúc nhích, và
mỗi lần vẽ lại là một lần thu phóng cả khung 752×1338 bằng `SmoothTransformation`.
Ô trong lưới cũng vậy — chọn, bỏ chọn, di chuột qua đều vẽ lại.

Giờ ảnh đã thu phóng được **nhớ lại**, chỉ tính lại khi có khung hình mới hoặc
khi đổi cỡ khung. Đo bằng `tools/bench_paint.py`:

| | 200 lần vẽ lại giữa hai khung hình |
|---|---|
| Cách cũ (thu phóng mỗi lần vẽ) | **384 ms** |
| Cách mới (nhớ ảnh đã thu phóng) | **2 ms** |

Cũng bỏ luôn một lần sao chép cả khung hình mỗi frame: `QPixmap.fromImage()`
vốn đã sao chép điểm ảnh, nên `image.copy()` trước đó là thừa.

Và bỏ việc cắt kênh màu. Máy gửi về bộ đệm **BGRA 4 kênh**; trước đây ta cắt
lấy 3 kênh cho `Format_RGB888` — mà cắt như vậy là **đọc nhảy cách**, chậm hơn
hẳn chép thẳng cả khối. Qt đọc được BGRA qua `Format_RGB32` nên giờ đưa nguyên
bộ đệm sang:

| | Mỗi khung 752×1338 |
|---|---|
| Cắt 3 kênh rồi `tobytes()` | 8,44 ms · 3,02 MB |
| `tobytes()` thẳng 4 kênh | **1,82 ms** · 4,02 MB |

Tốn thêm 1 MB bộ nhớ mỗi khung nhưng nhanh hơn **4,6 lần**. Ảnh thu nhỏ trong
lưới vẫn giữ 3 kênh vì đã ít điểm ảnh, giữ 4 kênh chỉ phí bộ nhớ giao diện.

### Giảm tải cho chính iPhone

Cơ chế tier ở trên tiết kiệm cho **phía PC và mạng**. Nhưng đọc mã nguồn
TrollVNC thì thấy nó **bật/tắt ScreenCapturer theo số client đang nối**
(`trollvncserver.mm`: `startCapture` khi `gClientCount > 0`, `endCapture` khi về
0). Nghĩa là chừng nào Control IOS còn giữ kết nối, **iPhone vẫn render và mã
hoá khung hình** dù bạn không nhìn ô đó.

Nên có thêm `idle_disconnect_after`: máy nằm ngoài khung nhìn quá bấy nhiêu
giây thì **ngắt hẳn kết nối** → `gClientCount = 0` → máy ngừng chụp, trả CPU và
bộ nhớ lại cho app đang chạy. Cuộn tới hoặc mở máy đó thì tự nối lại (~1 giây).

Ô của máy đang ngủ có **viền xanh dương** và giữ ảnh cuối — khác với viền xám
là mất kết nối ngoài ý muốn. Thanh trạng thái đếm riêng mục *ngủ*.

Chụp ảnh, ghi hình và kịch bản **tự đánh thức** máy đang ngủ, nên chọn cả 250
máy rồi chạy kịch bản vẫn đủ, không bỏ sót máy nào.

Đặt `0` để tắt hẳn chính sách này và giữ nguyên hành vi cũ.

> Một cách nữa, không cần code: build TrollVNC với ô **`frame_rate_spec`** đặt
> thấp (ví dụ `5`). Chỉ theo dõi và điều khiển thì 5 hình/giây là quá đủ, mà
> CPU trên máy giảm hẳn. Một lần build dùng cho cả 250 máy.

### Số đo thực tế

Đo bằng `tools/bench_scale.py`, giả lập 250 máy TrollVNC **chạy chung một tiến
trình với client** (nên số CPU dưới đây đã bao gồm cả phần nén zlib mà ngoài
đời là việc của iPhone — thực tế sẽ nhẹ hơn):

| Kịch bản | Kết quả |
|---|---|
| Kết nối 250 máy | **3.5 giây**, tất cả online |
| 40 ô hiển thị @1 fps (dùng thật) | 40 fps tổng, CPU **38%**, RAM 198 MB |
| 250 ô cùng stream @1 fps | chỉ đạt 99 fps tổng — **CPU bão hoà 92%** |
| 250 ô cùng stream @0.33 fps | 78 fps tổng, CPU 69%, RAM 410 MB |

Kết luận: kết nối cả 250 thì thoải mái; ép cả 250 ô cùng vẽ 1 fps thì nghẽn
CPU. Nếu bạn muốn thấy hết 250 ô nhúc nhích cùng lúc, hạ `grid_fps` xuống
`0.3` trong `config/devices.json`. Chế độ mặc định (chỉ vẽ ô đang nhìn) không
cần chỉnh gì.

## Bắt đầu

### 1. Tìm máy

TrollVNC **tự quảng bá dịch vụ `_rfb._tcp` qua Bonjour/mDNS**, nên cách tốt
nhất là hỏi thẳng mạng thay vì dò từng địa chỉ:

```powershell
# Khuyên dùng: không cần biết subnet, không dò 254 địa chỉ
python main.py --bonjour

# Lấy IP từ bảng ARP rồi dò cổng VNC
python main.py --scan-arp

# Hoặc quét dải cụ thể
python main.py --scan 172.30.3.0/24
python main.py --scan 172.30.3.10-90 172.30.4.0/24
```

Bonjour hơn hẳn ở hai điểm: thấy được cả máy **chưa từng liên lạc** với PC này
(bảng ARP thì không), và lấy **đúng cổng** mà từng máy đang mở thay vì đoán
5901. Cần gói `zeroconf`; thiếu thì phần Bonjour tự tắt, quét thường vẫn chạy.

Dải mặc định cho chế độ quét là `172.30.2.0/24` và `172.30.3.0/24` (đổi ở `DEFAULT_SCAN_RANGE`
trong [config.py](controlios/config.py)). Trong giao diện, hộp **Quét mạng** có
sẵn cả ba cách; Bonjour bật mặc định.

Kết quả ghi vào `config/devices.json`. Trong giao diện cũng có nút **Quét
mạng** và **Nạp danh sách…** (file txt, mỗi dòng một IP hoặc `ip:port`).

Việc dò chỉ nhận máy trả lời đúng banner `RFB `, nên không nhầm với dịch vụ khác.

### 1b. Qua USB (không cần WiFi)

Cả ba kênh đều là TCP nên **forward được qua dây USB** bằng usbmuxd của Apple
(đi kèm iTunes / Apple Mobile Device Support). Nút **Quét USB** trên thanh công
cụ: tìm iPhone đang cắm, tự dựng relay (`tidevice`) cho cả VNC lẫn control socket
lẫn SSH, rồi nạp vào lưới ở nhóm `usb`. Máy USB nhớ **cổng riêng từng kênh**
(`127.0.0.1:<cổng>`) trong `DeviceSpec` nên **mọi tính năng chạy y như qua mạng**
— xem/điều khiển, app, clipboard, nạp ảnh, reset dữ liệu app, respring, scale, SSH.

Ưu điểm: không phụ thuộc WiFi, độ trễ thấp hơn, hết nghẽn router. Cần `tidevice`
(đi kèm khi cài project) và Apple Mobile Device Support đang chạy. Relay tự tắt
khi đóng app và tự dựng lại khi mở lại (máy USB đã lưu trong `devices.json`).

> Giới hạn phần cứng: một PC/USB host chỉ cắm được số máy có hạn (hub có nguồn,
> có thể phải nhiều card USB) — đây là trần vật lý, không phải phần mềm.

### 2. Dùng giao diện

- **Lưới bên trái**: mỗi ô một máy, viền màu = trạng thái (xanh online, vàng
  đang kết nối, đỏ lỗi, xám tắt). Ô **tự giãn chia hết bề rộng** nên không bao
  giờ bị cắt hay phải cuộn ngang; chiều cao ô theo **tỉ lệ màn hình thật** của
  máy, biết được sau khung hình đầu tiên. Ô chọn **Cột** trên thanh công cụ đặt
  số cột cố định (4/6/8/10/12) hoặc để tự động theo bề rộng cửa sổ.
- **Khung một máy bên phải** chỉ rộng đúng bằng một chiếc iPhone ở chiều cao
  hiện tại, không chiếm nửa cửa sổ để hiện hai dải đen. Chưa mở máy nào thì nó
  thu về mức tối thiểu, nhường chỗ cho lưới. Máy xoay ngang thì khung tự nới ra.
- **Double-click** một ô → mở khung điều khiển bên phải ở full độ phân giải.
  Kéo/thả chuột và gõ phím trên khung đó sẽ đi thẳng tới máy. Xem mục
  [Chuột và bàn phím](#chuột-và-bàn-phím) cho chi tiết.
- **Điều khiển thẳng trên lưới**: bật ô này trên thanh công cụ thì **bấm và kéo
  thẳng vào ô nhỏ** là điều khiển máy đó luôn, khỏi phải mở khung riêng. Tiện
  khi cần chạm nhanh vài chục máy. `Ctrl`/`Shift`+bấm **vẫn để chọn máy** —
  không thì bật chế độ này lên là hết chọn được gì. Double-click vẫn mở khung
  riêng.
  - Ô lưới chỉ làm mới 1 hình/giây nên bấm vào mà chờ một giây mới thấy phản
    hồi thì vô dụng. Vì vậy ô vừa thao tác được **tạm nâng lên nhịp cao trong
    4 giây**, rồi tự trả về bình thường.
- **Chọn nhiều máy**: click, `Ctrl`+click, `Shift`+click, hoặc **Chọn tất cả**.
- **Gửi thao tác tới các máy đã chọn**: bật ô này thì mỗi cú click trên khung
  điều khiển được phát cho toàn bộ máy đang chọn. Toạ độ gửi đi là **tỉ lệ**
  (0..1) chứ không phải pixel, nên máy khác kích thước màn hình vẫn chạm đúng
  chỗ.
- **Trang**: mặc định 100 máy/trang. Chọn "Tất cả" để nạp hết 250. Máy ngoài
  trang hiện tại không được kết nối — đây là cách "mở lần lượt" nếu muốn.

## Chuột và bàn phím

Khung điều khiển bên phải là nơi thao tác trực tiếp. Mọi thứ gửi ở **toạ độ
framebuffer** của đúng máy đó.

### Chuột

Theo [mã nguồn TrollVNC](https://github.com/OwnGoalStudio/TrollVNC) (GPLv2), nút
chuột được map thành **nút cứng của iPhone**:

| Thao tác | iPhone nhận được |
|---|---|
| Chuột trái, bấm/kéo/nhả | một ngón chạm; giữ để kéo |
| **Chuột phải** | **nút Home/Menu** |
| **Chuột giữa** | **nút Power** |
| **Bánh xe** | vuốt ngắn (TrollVNC quy đổi, mặc định 48 px mỗi nấc, đổi bằng `-W`) |
| Bấm ra ngoài vùng ảnh | không gửi gì |

Nên **chuột phải là về màn hình chính, chuột giữa là khoá máy** — nhớ điều này
trước khi bấm bừa. Bánh xe nhích ít hơn một nấc vẫn cuộn một bước, nếu không
cảm giác là kẹt.

**Con trỏ có vòng ngắm**: TrollVNC không gửi hình con trỏ về, nên không vẽ thì
bạn không biết mình vừa chạm vào đâu. Vòng xanh là đang di, đỏ là đang giữ nút.

**Thanh trạng thái hiện toạ độ** dạng `x=188 y=901 · 0.500 0.675`. Hai số sau
là **tỉ lệ, dùng dán trực tiếp vào lệnh `tap`/`swipe`** của kịch bản — đây là
cách nhanh nhất để hiệu chỉnh toạ độ cử chỉ cho đúng đời máy của bạn: trỏ vào
nút cần chạm rồi đọc số.

### Bàn phím

- Ký tự thường gõ trực tiếp, **có tiếng Việt đầy đủ dấu** (`ạ` đi qua keysym
  Unicode `0x1001ea1`). Emoji thì không có keysym — ký tự nào không gửi được sẽ
  bị **bỏ qua và ghi vào nhật ký**, chứ không làm chết cả kịch bản như trước.
- **Tổ hợp bổ trợ**: Ctrl / Alt / Shift / Cmd (Super) + phím. `Ctrl+C` gửi đúng
  tổ hợp thay vì ký tự điều khiển thô, và nhả theo thứ tự ngược đúng quy ước.
  Phía TrollVNC map `Alt → Option`, `Meta/Super → Command` (đổi được bằng `-M`).
- **Phím đặc biệt**: Enter, BackSpace, Delete, Esc, Tab, 4 mũi tên, Home, End,
  PageUp/Down, Insert, Space, F1–F12. TrollVNC nhận tới F24 nếu bạn cần thêm.
- Giữ phím thì máy nhận nhiều lần (không chặn auto-repeat).
- Nút **⏎ ⌫ Esc** trên thanh công cụ bấm nhanh cho các máy đang chọn.

### Gõ chữ cho nhiều máy cùng lúc

Nút **Gõ chữ…** mở hộp soạn nhiều dòng, dán được từ clipboard của PC, tuỳ chọn
nhấn Enter sau khi gõ. Tiện hơn hẳn gõ tay khi cần nhập cùng một nội dung cho
hàng loạt máy.

> Phần mềm gõ **từng ký tự qua keysym** — chậm hơn nhưng đúng dấu. Thư viện
> client `asyncvnc` chỉ hiện thực `ClientCutText` chuẩn (latin-1) nên đường
> clipboard **của VNC** không dùng được cho tiếng Việt.
>
> **Đường vòng: đặt clipboard qua kênh điều khiển.** Với máy chạy TrollVNC đã vá
> (vòng 3), tick **Đặt vào clipboard máy (UTF-8, nhanh)** trong hộp *Gõ chữ…*:
> chữ đi thẳng vào `UIPasteboard`, giữ đúng dấu lẫn emoji và **nhanh hơn hẳn** gõ
> từng ký tự — hợp khi cần dán cùng một khối chữ dài (caption, bình luận) cho
> hàng loạt máy. Tick thêm **Dán ngay (Cmd+V)** để dán luôn vào ô đang chọn.
> Trong kịch bản: `clipboard <nội dung>`. Xem [docs/trollvnc-patch-3.md](docs/trollvnc-patch-3.md).

### Phát thao tác cho nhiều máy

Bật **Gửi thao tác tới các máy đã chọn** thì mọi thứ làm trên khung điều khiển
được phát cho toàn bộ máy đang chọn, ở **toạ độ tỉ lệ** nên máy khác cỡ màn
hình vẫn trúng chỗ:

- Bấm nhả tại chỗ → **chạm** hàng loạt.
- Kéo quá 12 px → **vuốt** hàng loạt, giữ đúng thời gian bạn kéo. (Trước đây
  mọi cú kéo bị co lại thành một cú chạm ở điểm nhả, nên không vuốt hàng loạt
  được.)
- Bánh xe → **cuộn** hàng loạt.

Kết hợp được với **Điều khiển thẳng trên lưới**: bấm vào một ô bất kỳ là thao
tác đó phát cho toàn bộ máy đang chọn.

## Bảng ứng dụng (cần TrollVNC đã vá)

Nút **Ứng dụng** mở bảng bên phải — đây là nơi gom **mọi thao tác với app và
máy**:

- Hàng trên cùng: **⌂ Home**, **⇄ Chuyển app**, **⏻ Khoá máy**. Đây là thao tác
  mức thiết bị, đi bằng nút cứng (chuột phải/giữa theo map của TrollVNC).
- Hàng **Độ sáng**: `▁ Tối đa` hạ xuống đáy, `− +` từng nấc, `▔ Sáng` lên cao
  nhất. Điều khiển qua VNC nên **máy chưa vá cũng dùng được**. Xem mục dưới về
  chuyện tắt hẳn màn hình.
- Danh sách app đã cài, mỗi app một biểu tượng màu: **bấm để mở**, **chuột phải
  để đóng**. Có ô lọc theo tên hoặc bundle id, mặc định ẩn app hệ thống.

Thao tác áp cho **tất cả máy đang chọn** ở lưới — mở một app trên 50 máy là một
cú bấm.

Menu "Thao tác app" cũ trên thanh công cụ đã bỏ: mở/đóng app qua bundle id ở
bảng này chính xác hơn hẳn cử chỉ Spotlight. Các cử chỉ `openapp <tên>`,
`closeapp`, `applibrary` vẫn dùng được **trong kịch bản**, làm phương án dự
phòng cho máy chưa cài bản TrollVNC đã vá.

Trong kịch bản thì dùng bundle id:

```
launchapp com.zing.zalo     # mở theo bundle id, không qua Spotlight
killapp com.golike.app      # đóng ngay, không phải mò App Switcher
```

Khác với `openapp <tên>` (cử chỉ Spotlight), hai lệnh này **không phụ thuộc tên
hiển thị, ngôn ngữ máy, hay bàn phím** — với 250 máy đây là khác biệt giữa
"chạy được" và "chạy được 90%".

### Cài .ipa hàng loạt

Nút **⤓ Cài .ipa…** trong bảng Ứng dụng. Cách hoạt động:

1. Control IOS mở một **web server tạm** trên PC, phục vụ đúng file `.ipa` đó
2. Gửi cho từng máy: `openurl apple-magnifier://install?url=http://<ip-pc>:<cổng>/<file>`
3. **TrollStore** trên từng máy tự tải về và cài

Cố ý không tự cài bằng `installd`: việc đó cần bộ quyền TrollVNC không có, còn
TrollStore vốn làm đúng. Phần code trên máy vì thế chỉ là một lệnh mở URL.

Web server sống thêm 5 phút sau khi gửi lệnh (máy còn phải tải), tự tắt sớm khi
đủ số máy đã tải xong. TrollStore có thể hỏi xác nhận trên máy — lúc đó bấm OK
qua màn hình VNC, hoặc phát thao tác cho nhiều máy cùng lúc.

Từ dòng lệnh:

```powershell
.\.venv\Scripts\python.exe -m controlios.filepush 172.30.0.221 app.ipa --install
```

### Đẩy file lên máy

Nút **⬆ Đẩy file…**, hoặc:

```powershell
.\.venv\Scripts\python.exe -m controlios.filepush 172.30.0.221 anh.jpg /var/mobile/Documents/anh.jpg
```

File đi thẳng qua kênh điều khiển (`put <size> <path>`), không mã hoá base64
nên không phình dung lượng và không tốn RAM với file lớn.

> **Chép ảnh vào `/var/mobile/Media/DCIM/` KHÔNG làm ảnh hiện trong app Ảnh.**
> iOS không quét thư mục, nó quản ảnh bằng cơ sở dữ liệu riêng. Muốn ảnh dùng
> được trong Shopee/TikTok thì phải nạp qua `PHPhotoLibrary` — chưa làm, xem
> "Giới hạn đã biết".

### Điều kiện

Tính năng này đi qua **kênh điều khiển thứ hai**, song song với VNC. Cần:

1. Máy chạy **bản TrollVNC đã vá**:
   - [docs/trollvnc-patch.md](docs/trollvnc-patch.md) — vòng 1: `apps`, `launch`, `terminate`
   - [docs/trollvnc-patch-2.md](docs/trollvnc-patch-2.md) — vòng 2: `put`, `openurl`
2. Khai `control_token` trong `config/devices.json`, đúng token đã dùng lúc build

Thiếu một trong hai thì bảng báo lỗi rõ ràng chứ không treo. Máy chạy bản gốc
sẽ báo "chưa cài bản đã vá".

**Quan trọng khi mới cài được vài máy:** mở/đóng app chỉ chạy trên **máy đã vá**.
Sau mỗi thao tác hàng loạt, bảng Ứng dụng hiện dòng tổng kết **ở lại trên màn
hình**, ví dụ:

> `Mở com.zing.zalo: xong 1/12 máy — 11 máy chưa cài bản TrollVNC đã vá`

Các lý do giống nhau được gộp lại, chứ không liệt kê từng máy một. Nếu bạn thấy
"chỉ máy đang mở mới ăn lệnh" thì gần như chắc chắn là các máy còn lại chưa cài
bản `.tipa` đã vá.

Kiểm tra nhanh một máy bất kỳ:

```powershell
.\tools\tvnc-ctl.ps1 -Device 172.30.3.152 -Command apps
```

Báo *"No connection could be made"* ở cổng 46752 nghĩa là máy đó chưa có bản vá.

```json
{
  "settings": {
    "control_port": 46752,
    "control_token": "…token của bạn…"
  }
}
```

Kênh này chỉ mở một socket ngắn cho mỗi lệnh, không giữ kết nối, nên không ảnh
hưởng gì tới luồng hình VNC.

## SSH — máy đã jailbreak

Kênh thứ ba, và là kênh mạnh nhất. Nó **chấm dứt vòng lặp** "vá TrollVNC →
build trên GitHub → cài lại từng máy": mọi tính năng mới sau này chỉ còn là một
câu lệnh shell.

Ba kênh bổ nhau chứ không thay thế nhau:

| Kênh | Làm được gì | Điều kiện |
|---|---|---|
| **VNC** | hình ảnh, chuột/phím | mọi máy |
| **Control socket** | app, truyền file, mở URL | TrollVNC đã vá |
| **SSH** | lệnh tuỳ ý, SFTP | máy đã jailbreak |

Nút **SSH…** mở bảng chạy lệnh: gõ lệnh, chạy song song trên các máy đang chọn,
xem kết quả **từng máy** trong bảng — mã trả về và output cạnh nhau để so sánh.

Có sẵn vài lệnh mẫu trong danh sách thả xuống. Đây cũng là công cụ để **dò xem
lệnh nào thật sự chạy được trên iOS**: máy jailbreak không có đủ bộ lệnh như
Linux, và mỗi bản jailbreak lại khác nhau — chạy thử rồi đọc kết quả, đừng đoán.

```json
{
  "settings": {
    "ssh_port": 22,
    "ssh_user": "root",
    "ssh_password": "…"
  }
}
```

> Mật khẩu mặc định của OpenSSH trên máy jailbreak là `alpine`. **Đổi ngay sau
> khi cài** — ai trong mạng LAN cũng biết mật khẩu đó, và SSH root là quyền cao
> nhất trên máy.

Máy chưa jailbreak báo lỗi rõ ràng ("máy chưa jailbreak, chưa cài OpenSSH, hoặc
đã khởi động lại và mất jailbreak") chứ không treo, và phân biệt được với lỗi
sai mật khẩu.

`pool.ssh_available()` trả về danh sách máy còn SSH — tức **máy nào còn jailbreak
sau lần khởi động lại gần nhất**. Dopamine là semi-untethered nên đây là thông
tin phải theo dõi thường xuyên.

## Chụp ảnh, ghi hình, kịch bản

Ba nút này làm việc trên **các máy đang chọn** ở lưới (không chọn gì thì lấy máy
đang mở ở khung bên phải). Mọi thứ ghi vào `captures/`.

### Chụp ảnh

Chụp **full độ phân giải** tất cả máy đang chọn cùng lúc, ra `captures/anh/`,
tên file `<tên máy>_<ngày-giờ>.png`. Chụp được cả máy đang ở tier `IDLE` —
yêu cầu chụp được xếp vào cùng một kết nối chứ không mở thêm phiên mới, nên
chọn 250 máy rồi bấm Chụp ảnh vẫn an toàn. Nhiều lời gọi chụp cùng lúc trên
một máy được gộp thành một lượt hỏi framebuffer.

### Ghi hình

Ghi thành **chuỗi ảnh PNG** (mặc định 2 fps) vào
`captures/ghihinh/rec-<timestamp>/<tên máy>/<tên máy>_000001.png`. Bấm lại nút
để dừng. Ghi quá 8 máy cùng lúc sẽ hỏi xác nhận vì mỗi máy là một luồng ảnh
full độ phân giải — nặng CPU và ổ đĩa.

Muốn ra video thì ghép bằng ffmpeg:

```powershell
ffmpeg -framerate 2 -i "captures\ghihinh\rec-123\iPhone-01\iPhone-01_%06d.png" -c:v libx264 -pix_fmt yuv420p iphone01.mp4
```

### Thao tác app (home / mở app / đóng app)

Nút **Thao tác app ▾** chạy các cử chỉ iOS dựng sẵn trên các máy đang chọn:
về màn hình chính, mở app theo tên, đóng app đang mở, đóng 5 app gần đây, mở
App Library. Dùng trong kịch bản thì viết thẳng tên lệnh:

```
home                  # nhấn nút Home (= chuột phải)
switcher              # nhấn Home hai lần nhanh
lock                  # nhấn nút Power (= chuột giữa)
spotlight             # về home rồi vuốt xuống mở ô tìm kiếm
openapp Zalo          # tìm trong Spotlight rồi Enter mở kết quả đầu
closeapp              # vào switcher, hất thẻ đầu lên, về home
closeall 5            # hất 5 thẻ liên tiếp
applibrary            # sang trang App Library
home_swipe            # dự phòng: về home bằng cử chỉ vuốt
switcher_swipe        # dự phòng: mở switcher bằng vuốt-và-giữ
```

**`home`, `switcher`, `lock` không phụ thuộc toạ độ** — chúng bấm nút cứng qua
map nút chuột của TrollVNC, nên chạy đúng trên mọi đời máy mà không cần hiệu
chỉnh gì. Chỉ các cử chỉ còn lại (`spotlight`, `closeapp`, `applibrary`) mới
dùng toạ độ và có thể cần chỉnh. Máy nào bấm nút không ăn thì còn `home_swipe`
và `switcher_swipe`.

Lệnh nguyên thuỷ đằng sau là `button`:

```
button home           # = chuột phải ở giữa màn hình
button power          # = chuột giữa
button left 0.5 0.9   # chuột trái tại toạ độ chỉ định
```

### Keeper — tự hồi phục khi ControlIOS chết

Farm có ba vòng canh lồng nhau, mỗi vòng vực dậy vòng trong:

1. **App ControlIOS tự canh daemon**: `TVNCServiceCoordinator` bật lại
   `trollvncmanager` mỗi 3 giây nếu nó chết.
2. **keeperd canh app ControlIOS**: một daemon root độc lập (cổng 46753 ở
   loopback), chờ bằng `kqueue NOTE_EXIT` nên phản ứng tức thì và ~0% CPU. App
   ControlIOS chết hay bị cài đè thì nó gọi SpringBoard mở lại. Nó sống ngoài
   vòng đời app nên vuốt tắt app Keeper cũng không ảnh hưởng.
3. **PC canh keeperd**: nút **🛡 Keeper** trên thanh công cụ bên ngoài khung điều khiển máy.

Vòng thứ ba cần thiết vì keeperd bind loopback — PC không dò cổng 46753 từ xa
được, phải hỏi qua control socket của ControlIOS. Menu có 2 mục:

- `Kiểm tra + bật lại nếu chết`: soát các máy đang chọn ngay lập tức.
- `Tự động canh mọi máy`: cứ 5 phút soát mọi máy **đang kết nối**, chỉ báo khi
  thật sự phải bật lại nên không làm ngập nhật ký.

Khi keeperd chết, PC nhờ daemon `posix_spawn` thẳng `keeperd` từ bundle Keeper
với quyền root — **không mở giao diện app Keeper**, nên máy đang chạy việc của
farm không bị chiếm màn hình.

Còn một khoảng trống không thể bịt từ xa: sau khi **khởi động lại máy**,
TrollStore không có launchd nên phải mở khoá máy và bật app Keeper một lần bằng
tay. Sau đó BackgroundTasks giúp iOS tự bật lại khi có cơ hội.

#### Vì sao không có lệnh "liệt kê app đã cài"

VNC **chỉ có màn hình và chuột/phím**. Không có kênh nào để hỏi iOS "máy này
cài app gì" hay "mở bundle id `com.example.app`" — TrollVNC không mở cổng đó.
Nên mọi lệnh app ở đây đều là **cử chỉ**, đúng như bạn tự thao tác tay:

- `openapp` = mở Spotlight, gõ tên, nhấn Enter. Cần **tên hiển thị** trên máy
  (gõ đủ dấu), không phải bundle id.
- `applibrary` = sang trang App Library, nơi thấy hết app đã cài. Ghép với
  `shot` thì được **ảnh chụp** các trang app — không phải danh sách chữ.

Muốn danh sách app dạng text đúng nghĩa (và mở/đóng theo bundle id) thì phải
có kênh khác ngoài VNC, ví dụ SSH trên máy. Xem "Giới hạn đã biết" bên dưới.

#### Toạ độ cử chỉ chỉnh được

Các cử chỉ còn dùng toạ độ nhắm màn hình **dọc**. Máy khác đời hoặc iOS khác có
thể lệch — nên toàn bộ cử chỉ nằm trong `config/gestures.json`, sửa được mà
không đụng code (mẫu: `config/gestures.example.json`):

```json
{
  "home": "key Home\nwait 0.5",
  "closeapp": "switcher\nswipe 0.5 0.5 0.5 0.03 0.35\nwait 0.8\nhome"
}
```

Mỗi macro chính là một đoạn kịch bản, gọi được macro khác, và `{name}` là tham
số truyền vào. Cách dò toạ độ đúng cho máy của bạn: chạy cử chỉ trên **một**
máy, chèn `shot` sau mỗi bước, xem ảnh trong `captures/` rồi chỉnh số.

### Kịch bản tự động

Bấm **Kịch bản…**, soạn rồi **Chạy** — kịch bản chạy **song song** trên mọi máy
đang chọn. Toạ độ là **tỉ lệ 0..1**, không phải pixel, nên cùng một kịch bản
chạy đúng trên các iPhone khác cỡ màn hình.

Không cần nhớ bundle ID: trong hộp thoại, bấm **Lấy danh sách app**. Chương
trình hỏi tất cả máy đang chọn, hợp nhất danh sách theo bundle ID và hiển thị
độ phủ như `Zalo — com.zing.zalo — 48/50 máy`. Chọn app rồi bấm **Chèn mở**,
**Chèn đóng** hoặc **Chèn mở lại** để đưa đúng lệnh vào vị trí con trỏ.

```
# Dòng trống và dòng bắt đầu bằng # bị bỏ qua

tap 0.5 0.85                      # chạm giữa màn hình, 85% chiều cao
swipe 0.5 0.8 0.5 0.2 0.4         # vuốt lên trong 0.4 giây
swipe 0.5 0.99 0.5 0.45 0.35 0.7  # ... rồi GIỮ 0.7s trước khi nhả
text Xin chào                     # gõ chữ
key Return                        # nhấn phím (tên keysym X11)
wait 1.5                          # chờ 1.5 giây
wait 5-10                         # chờ NGẪU NHIÊN 5–10 giây
brightness min                    # hạ độ sáng xuống đáy (16 nấc)
brightness down 3                 # giảm 3 nấc
volume mute                       # tắt tiếng
launchapp com.zing.zalo           # mở app theo bundle id (kênh điều khiển)
killapp com.zing.zalo             # đóng app
restartapp com.zing.zalo 2        # đóng, chờ 2 giây rồi mở lại
openurl https://example.com       # mở URL bằng app mặc định
openurlin com.zing.zalo zalo://home # mở URL bằng đúng app chỉ định
clipboard Xin chào bạn            # đặt clipboard máy (UTF-8, kênh điều khiển)
savephoto /var/mobile/Media/x.jpg # nạp ảnh đã có trên máy vào Thư viện Ảnh
snapshot com.zing.zalo            # lưu bản dữ liệu app hiện tại (trên máy)
wipeapp com.zing.zalo             # xoá dữ liệu app như cài lại (giữ keychain)
restore com.zing.zalo             # khôi phục dữ liệu app về bản snapshot
shot ket-qua                      # chụp màn hình, file có hậu tố ket-qua
repeat 3                          # lặp khối thụt lề bên dưới 3 lần
    swipe 0.5 0.75 0.5 0.25 0.3
    wait 1
retry 3 1                         # lỗi thì thử lại riêng trên từng máy
    restartapp com.zing.zalo 2
openapp Zalo                      # và mọi cử chỉ ở mục trên
```

Tham số **giữ** của `swipe` không phải chi tiết vụn: vuốt lên từ mép dưới rồi
nhả ngay thì iOS về màn hình chính, phải *giữ* lại mới ra trình chuyển app.

**`wait 5-10` bốc số riêng cho từng máy**, không phải một số dùng chung. Nên khi
chạy trên 50 máy, chúng không thao tác đồng loạt cùng một nhịp.

Block **`retry <số lần> [giây nghỉ]`** cũng chạy độc lập trên từng máy. Máy A
mất mạng có thể thử lại mà không bắt máy B/C chạy lại hoặc dừng cả mẻ. Nếu hết
số lần thử, chỉ máy đó báo lỗi; các máy còn lại tiếp tục tới cuối kịch bản.

Ví dụ đóng app rồi mở lại sau một khoảng ngẫu nhiên:

```
killapp com.zing.zalo
wait 5-10
launchapp com.zing.zalo
```

- **Kiểm tra** — dò cú pháp và in lại kịch bản bằng tiếng Việt để soát trước khi
  chạy. Lỗi báo kèm **số dòng**. Toạ độ ngoài khoảng 0..1 bị từ chối ngay, vì đó
  gần như luôn là nhầm pixel với tỉ lệ.
- **Dừng** — huỷ giữa chừng trên tất cả máy.
- **Thư viện kịch bản** — lưu thẳng trong app, không cần file rời. Hàng **Kịch
  bản đã lưu** có ô chọn tên, nút **Lưu…** (đặt tên cho kịch bản đang soạn) và
  **Xoá**. Chọn một tên là nạp lại vào ô soạn. Lưu ở `config/scripts.json`.
- Ảnh từ lệnh `shot` nằm ở `captures/kichban/`.
- Máy nào chưa kết nối thì bị bỏ qua (có ghi trong nhật ký), không làm hỏng
  cả mẻ; máy nào lỗi giữa chừng cũng chỉ dừng riêng máy đó.

## Cấu hình

`config/devices.json` (xem `config/devices.example.json`):

```json
{
  "settings": {
    "grid_fps": 1.0,            // fps của ô thu nhỏ đang nhìn thấy
    "live_fps": 12.0,           // fps của máy đang mở full
    "thumb_long_edge": 320,     // cạnh dài ảnh thu nhỏ trong lưới
    "live_long_edge": 0,        // 0 = giữ độ phân giải gốc (mặc định, nét nhất)
    "connect_concurrency": 24,  // số máy bắt tay RFB cùng lúc
    "max_connected": 0,         // 0 = không giới hạn
    "reconnect_delay": 3.0,     // giây, tăng gấp đôi tới reconnect_max
    "reconnect_max": 60.0,
    "stall_timeout": 20.0,      // không có frame quá lâu -> coi như treo, nối lại
    "idle_disconnect_after": 60 // giây; máy ngoài khung nhìn bấy lâu thì ngắt hẳn
  },
  "devices": [
    { "host": "172.30.4.101", "port": 5901, "name": "iPhone-01", "group": "tang1" }
  ]
}
```

`name` và `group` chỉ để bạn dễ nhận diện; sửa tay trong file được.

## Kiến trúc

```
main.py                  CLI + mở giao diện
controlios/
  config.py              DeviceSpec, Settings, đọc/ghi registry
  scan.py                tìm máy qua Bonjour _rfb._tcp, bảng ARP, hoặc CIDR
  script.py              ngôn ngữ kịch bản: parse, describe, runner
  gestures.py            cử chỉ iOS dựng sẵn, nạp đè từ config/gestures.json
  control_channel.py     kênh thứ hai: app, truyền file, mở URL (TrollVNC đã vá)
  fileserver.py          web server tí hon để iPhone tải .ipa từ PC
  filepush.py            đẩy file / cài .ipa từ dòng lệnh
  util/png.py            ghi PNG bằng thư viện chuẩn (không cần Pillow/Qt)
  vnc/session.py         một kết nối RFB: tier, vòng đọc, vòng nhịp, input, chụp
  vnc/pool.py            toàn bộ session trên 1 event loop ở thread riêng;
                         chụp hàng loạt, ghi hình, chạy kịch bản
  ui/tile.py             một ô trong lưới
  ui/grid.py             lưới cuộn + ảo hoá (chỉ ô nhìn thấy mới lên tier GRID)
  ui/detail.py           khung điều khiển full độ phân giải
  ui/apps_panel.py       bảng ứng dụng: danh sách, lọc, bấm mở, chuột phải đóng
  ui/app.py              cửa sổ chính, toolbar, phân trang, broadcast, kịch bản
tests/fake_vnc.py        server RFB 3.8 giả để test không cần iPhone
tools/bench_scale.py     đo tải với N máy giả
tools/ui_preview.py      chụp ảnh giao diện với máy giả để soi bố cục
captures/                ảnh chụp, ghi hình, ảnh từ kịch bản (không vào git)
```

Yêu cầu chụp ảnh không mở kết nối mới: nó xếp vào **cùng vòng nhịp** của phiên
đang chạy, nên chụp một máy đang `IDLE` cũng chỉ tốn đúng một lượt round trip,
và không giẫm chân lên luồng hình đang chạy ở tier `GRID`/`LIVE`. Nén PNG chạy
trong thread riêng (`asyncio.to_thread`) để không nghẽn event loop mạng.

Toàn bộ mạng chạy asyncio trên **một thread nền**; Qt ở thread chính. Khung
hình và trạng thái đi qua `Bridge` (Qt signal) nên không có race giữa hai bên.

Mỗi phiên có hai vòng lặp song song:
- *reader* — đọc liên tục từ socket, giải nén, phát frame.
- *pacer* — gửi `FramebufferUpdateRequest` theo nhịp của tier, và **chờ frame
  trả lời trước khi hỏi tiếp**, để một máy treo không làm dồn hàng đợi yêu cầu.

Mất kết nối thì tự nối lại với backoff 3s → 60s, không cần thao tác gì.

## Kiểm thử

Không cần iPhone thật — `tests/fake_vnc.py` là một server RFB 3.8 thu nhỏ
(security None, pixel format BGRA, mã hoá ZLib) có ghi lại các sự kiện chuột/phím
nhận được.

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
```

316 test, gồm: tier IDLE thật sự im lặng · tier GRID stream và ảnh đúng kích
thước · tier LIVE trả full res · thăng tier thì stream lại · chuột/phím tới
được server · tự nối lại khi server chết rồi sống lại · pool kết nối nhiều máy
· lưới chỉ thăng tier những ô nhìn thấy · PNG viết ra giải nén lại đúng từng
dòng · chụp full res ngay cả khi máy đang IDLE · nhiều lời gọi chụp gộp thành
một round trip · chụp báo lỗi rõ khi máy rớt · ghi hình ra đúng chuỗi ảnh và
dừng hẳn khi bấm dừng · kịch bản chạy đủ trên mọi máy đã chọn và huỷ được giữa
chừng · cú pháp sai báo đúng số dòng · hộp thoại kịch bản không gửi gì khi chưa
chọn máy · mọi cử chỉ mặc định đều phân tích được · `switcher` thật sự gửi
nhấn→kéo→giữ→nhả đúng thứ tự tới server · `openapp Zalo` gõ đúng chữ "Zalo" và
phím Return tới máy · cử chỉ tự chỉnh đè được cử chỉ mặc định · macro gọi vòng
tròn bị chặn thay vì treo · lăn lên/xuống/ngang dùng đúng nút RFB khác nhau ·
chuột phải và giữa không bị gửi thành chuột trái · gõ "Xin chào bạn" tới máy ra
đúng từng ký tự có dấu · emoji bị bỏ qua mà phiên vẫn sống · `Ctrl+C` nhấn giữ
rồi nhả theo thứ tự ngược · keysym không tồn tại bị từ chối chứ không làm chết
phiên · bấm ra ngoài vùng ảnh không gửi gì · phát thao tác: kéo dài ra vuốt,
bấm tại chỗ ra chạm, không lẫn nhau · ô lưới không bao giờ tràn khỏi khung ở
mọi bề rộng · ô giãn chia hết chỗ thay vì để trống dải bên phải · số cột cố
định thắng chế độ tự động và quay lại được · chiều cao ô đi theo tỉ lệ màn hình
thật · khung một máy rộng đúng một máy, thu nhỏ khi chưa mở máy nào, nới ra khi
máy xoay ngang · dải quét mặc định là 172.30.2.0/24 và 172.30.3.0/24 · `home` bấm nút cứng chứ
không vuốt và tới server đúng nút chuột phải · `switcher` là hai lần Home sát
nhau · `lock` dùng nút Power · Bonjour lấy đúng cổng máy quảng bá, bỏ qua IPv6,
lọc theo subnet, và thiếu thư viện `zeroconf` thì không làm sập gì · kênh điều
khiển đọc đúng bốn cột của danh sách app, bỏ qua dòng hỏng thay vì làm hỏng cả
mẻ, phân biệt được ba loại lỗi (sai token / máy chưa vá / máy không phản hồi) ·
`launchapp` từ chối tên hiển thị và chỉ thẳng sang `openapp` · bảng ứng dụng
lọc theo tên lẫn bundle id, không gọi ra mạng khi chưa cấu hình · màu biểu
tượng cố định theo bundle id giữa các lần chạy · file 512 KB chứa đủ 256 giá
trị byte đi qua nguyên vẹn không lệch một byte · file rỗng vẫn gửi được · đường
dẫn tương đối và `..` bị từ chối · web server chỉ phục vụ file đã đăng ký, mọi
đường dẫn khác trả 404 · `HEAD` không tính là một lượt tải · URL cài app mã hoá
dấu cách nhưng giữ nguyên `://` · cài `.ipa` phải qua hộp xác nhận mới chạy ·
phím độ sáng gửi đúng keysym XF86 tới server và lặp đúng số nấc · `wait 5-10`
chạy 30 lượt đều nằm trong khoảng và không ra cùng một số · `wait 3` thì không
bị ngẫu nhiên hoá · máy ngoài khung nhìn tự ngắt kết nối còn máy đang nhìn thì
không · cuộn tới thì máy tỉnh lại và mở kết nối mới · chụp ảnh và kịch bản đánh
thức được máy đang ngủ thay vì bỏ qua · đặt `idle_disconnect_after` bằng 0 thì
giữ nguyên hành vi cũ · tier LIVE thu nhỏ đúng theo giới hạn (bước chia làm
tròn **lên**, nếu làm tròn xuống thì giới hạn bị bỏ qua trong im lặng) · đặt 0
thì giữ độ phân giải gốc · toạ độ chuột vẫn theo khung hình gốc sau khi thu
nhỏ · huỷ hộp thoại chất lượng thì không đổi gì · ảnh đã thu phóng được dùng lại giữa các lần vẽ, chỉ tính lại khi có khung mới hoặc đổi cỡ · rê chuột không làm thu phóng lại · khung LIVE đi đường 4 kênh còn ảnh thu nhỏ giữ 3 kênh · điểm ảnh đỏ dạng BGRA đọc ra vẫn là đỏ chứ không bị đảo thành xanh · bấm vào ô nhỏ ra đúng toạ độ giữa màn hình máy dù ô chỉ rộng 150 px · Ctrl+bấm vẫn chọn máy khi đang bật điều khiển trên lưới · bấm vào dải nhãn dưới ô thì không gửi gì · ô vừa thao tác được nâng nhịp rồi tự trả về · thao tác hàng loạt tổng kết lại số máy được và số máy hỏng, gộp theo lý do · nhãn số máy trong bảng Ứng dụng cập nhật theo lựa chọn chứ không đứng yên · SSH chạy với server SSH thật dựng trong tiến trình, không giả lập nửa vời: lệnh chạy được, mã trả về giữ nguyên, nhiều lệnh dùng chung một kết nối, tải lên rồi tải về khớp từng byte · phân biệt được sai mật khẩu với máy chưa jailbreak · biết máy nào còn jailbreak sau reboot.

Soi bố cục bằng ảnh (không cần iPhone):

```powershell
.\.venv\Scripts\python.exe tools\ui_preview.py preview.png --devices 100 --columns 8 --focus
```

Đo tải:

```powershell
.\.venv\Scripts\python.exe tools\bench_scale.py --devices 250 --visible 40 --seconds 20
```

## Giới hạn đã biết

- Nếu TrollVNC bản của bạn có bảng map phím khác, sửa `SPECIAL_KEYS` trong
  `controlios/ui/detail.py` và `config/gestures.json`.
- **Clipboard UTF-8** của TrollVNC chưa dùng được vì client `asyncvnc` chỉ có
  `ClientCutText` latin-1 (xem mục Gõ chữ).
- Client chỉ đăng ký encoding **ZLib**; nếu TrollVNC không hỗ trợ, nó rơi về
  **Raw** (vẫn chạy, chỉ tốn băng thông hơn). Client chưa yêu cầu Tight/ZRLE
  dù TrollVNC có thể hỗ trợ — chỗ này còn dư địa tối ưu băng thông cho 250 máy.
- TrollVNC tự **xoay framebuffer** theo hướng máy (0/90/180/270°). Khi kích
  thước đổi (dọc ↔ ngang), client này không đăng ký pseudo-encoding DesktopSize
  nên phiên đó ngắt rồi tự nối lại — không mất máy, chỉ chớp một nhịp, và khung
  điều khiển tự nới ra theo tỉ lệ mới.
- Ghi hình ra **chuỗi PNG**, chưa mã hoá thẳng thành video (dùng lệnh ffmpeg ở
  trên để ghép). Đổi lại là không cần cài thêm gì và không mất khung hình nào.
- Kịch bản chạy **mở vòng**: nó gửi thao tác theo đúng thời gian đã ghi, chứ
  không đọc màn hình để chờ một nút hiện ra. Nếu máy phản ứng chậm, tăng `wait`.
  Muốn kiểm chứng thì chèn `shot` ở các mốc rồi xem lại ảnh.
- VNC thuần không liệt kê/mở/đóng app theo bundle id. Bản hiện tại giải quyết
  bằng control socket của TrollVNC đã vá, hoặc bằng kênh SSH trên máy jailbreak
  (xem mục "Bảng ứng dụng" và "SSH — máy đã jailbreak").
- Toạ độ cử chỉ mặc định nhắm iPhone Face ID dọc màn hình. Máy có nút Home vật
  lý, hoặc iOS khác đời, cần chỉnh `config/gestures.json`.
- **Nạp ảnh và video vào Thư viện Ảnh** đã chạy: nút **Nạp ảnh/video…** (đẩy file
  rồi gọi `PHPhotoLibrary`), lệnh kịch bản `savephoto`, và bản vá TrollVNC vòng 3
  ([docs/trollvnc-patch-3.md](docs/trollvnc-patch-3.md) — đã kèm entitlement TCC
  nên quyền được cấp sẵn, không cần hộp thoại). **Video tự chuẩn hoá:** iOS chỉ
  nhận H.264 (≤1080p) / HEVC, `yuv420p` — một `.mp4` mở tốt trên PC (ví dụ 4K
  H.264 level 5.1) vẫn bị từ chối. `push_photo` tự soi bằng `ffprobe`, video nào
  chưa đạt chuẩn thì `ffmpeg` re-encode **một lần** rồi mới phát cho cả mẻ (cache
  ở `captures/_media_tmp/`). Cần **ffmpeg trên PATH**; thiếu thì video được đẩy
  nguyên bản và máy tự báo lỗi nếu không nạp được.
- Cài `.ipa` phụ thuộc TrollStore trên máy nhận URL `apple-magnifier://`. Nếu
  TrollStore hỏi xác nhận thì phải bấm OK qua VNC — chưa tự động hoá bước đó.
- **Không tắt hẳn được màn hình mà vẫn giữ VNC.** TrollVNC chụp hình bằng
  IOSurface + CoreAnimation render server, **theo nhịp `CADisplayLink`** — mà
  `CADisplayLink` chạy theo nhịp quét của màn hình. Màn tắt thì nhịp đó dừng,
  luồng hình VNC đứng theo. Nên cách dùng được là **hạ độ sáng xuống đáy**:
  màn vẫn bật (VNC vẫn chạy), panel tối, tiết kiệm pin — nhất là máy OLED.
  Muốn kiểm chứng thì bấm **⏻ Khoá máy** trên một máy rồi xem ô của nó ở lưới
  còn cập nhật không.
