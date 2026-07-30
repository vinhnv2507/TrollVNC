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
| `IDLE` | Vẫn kết nối, vẫn gửi lệnh được, **không hỏi pixel** | ~0 |
| `GRID` | Ô đang nhìn thấy, refresh `grid_fps` (mặc định 1 fps), ảnh thu nhỏ | thấp |
| `LIVE` | Máy đang mở ở khung bên phải, full độ phân giải, `live_fps` | cao |

Nên bạn **kết nối cả 250 máy cùng lúc**, nhưng chỉ những ô đang lọt trong
khung nhìn mới thực sự tải hình. Cuộn tới đâu, tier đổi tới đó. Ảnh thu nhỏ
được scale ngay trong luồng mạng nên UI không bao giờ ôm 250 khung hình gốc.

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

```powershell
# Nhanh nhất: lấy IP từ bảng ARP rồi dò cổng VNC
python main.py --scan-arp

# Hoặc quét dải cụ thể
python main.py --scan 172.30.4.0/24
python main.py --scan 172.30.4.10-90 172.30.5.0/24
```

Kết quả ghi vào `config/devices.json`. Trong giao diện cũng có nút **Quét
mạng** và **Nạp danh sách…** (file txt, mỗi dòng một IP hoặc `ip:port`).

Việc dò chỉ nhận máy trả lời đúng banner `RFB `, nên không nhầm với dịch vụ khác.

### 2. Dùng giao diện

- **Lưới bên trái**: mỗi ô một máy, viền màu = trạng thái (xanh online, vàng
  đang kết nối, đỏ lỗi, xám tắt).
- **Double-click** một ô → mở khung điều khiển bên phải ở full độ phân giải.
  Kéo/thả chuột và gõ phím trên khung đó sẽ đi thẳng tới máy. Xem mục
  [Chuột và bàn phím](#chuột-và-bàn-phím) cho chi tiết.
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

| Thao tác | Gửi tới máy |
|---|---|
| Bấm/kéo/nhả chuột trái | nhấn – di – nhả nút 1 |
| Chuột **phải** / **giữa** | nút 3 / nút 2 (trước đây mọi nút đều thành trái) |
| **Bánh xe** lên/xuống | nút 4 / nút 5 — RFB không có sự kiện lăn riêng |
| Bánh xe ngang | nút 6 / nút 7 |
| Bấm ra ngoài vùng ảnh | không gửi gì |

Bánh xe nhích ít hơn một nấc vẫn cuộn một bước, nếu không cảm giác là kẹt.

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
- **Phím đặc biệt**: Enter, BackSpace, Delete, Esc, Tab, 4 mũi tên, Home, End,
  PageUp/Down, Insert, Space, F1–F12.
- Giữ phím thì máy nhận nhiều lần (không chặn auto-repeat).
- Nút **⏎ ⌫ Esc** trên thanh công cụ bấm nhanh cho các máy đang chọn.

### Gõ chữ cho nhiều máy cùng lúc

Nút **Gõ chữ…** mở hộp soạn nhiều dòng, dán được từ clipboard của PC, tuỳ chọn
nhấn Enter sau khi gõ. Tiện hơn hẳn gõ tay khi cần nhập cùng một nội dung cho
hàng loạt máy.

> Clipboard của RFB theo chuẩn chỉ nhận latin-1, nên **không** dùng đường
> clipboard để đưa tiếng Việt sang máy được. Vì vậy phần mềm gõ từng ký tự qua
> keysym — chậm hơn nhưng đúng dấu.

### Phát thao tác cho nhiều máy

Bật **Gửi thao tác tới các máy đã chọn** thì mọi thứ làm trên khung điều khiển
được phát cho toàn bộ máy đang chọn, ở **toạ độ tỉ lệ** nên máy khác cỡ màn
hình vẫn trúng chỗ:

- Bấm nhả tại chỗ → **chạm** hàng loạt.
- Kéo quá 12 px → **vuốt** hàng loạt, giữ đúng thời gian bạn kéo. (Trước đây
  mọi cú kéo bị co lại thành một cú chạm ở điểm nhả, nên không vuốt hàng loạt
  được.)
- Bánh xe → **cuộn** hàng loạt.

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
home                  # về màn hình chính
switcher              # mở trình chuyển app
spotlight             # mở ô tìm kiếm
openapp Zalo          # tìm trong Spotlight rồi Enter mở kết quả đầu
closeapp              # vào switcher, hất thẻ đầu lên, về home
closeall 5            # hất 5 thẻ liên tiếp
applibrary            # sang trang App Library
```

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

Toạ độ mặc định nhắm iPhone **Face ID** (không nút Home), màn hình dọc. Máy
khác đời hoặc iOS khác có thể lệch — nên toàn bộ cử chỉ nằm trong
`config/gestures.json`, sửa được mà không đụng code (mẫu:
`config/gestures.example.json`):

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

```
# Dòng trống và dòng bắt đầu bằng # bị bỏ qua

tap 0.5 0.85                      # chạm giữa màn hình, 85% chiều cao
swipe 0.5 0.8 0.5 0.2 0.4         # vuốt lên trong 0.4 giây
swipe 0.5 0.99 0.5 0.45 0.35 0.7  # ... rồi GIỮ 0.7s trước khi nhả
text Xin chào                     # gõ chữ
key Return                        # nhấn phím (tên keysym X11)
wait 1.5                          # chờ 1.5 giây
shot ket-qua                      # chụp màn hình, file có hậu tố ket-qua
repeat 3                          # lặp khối thụt lề bên dưới 3 lần
    swipe 0.5 0.75 0.5 0.25 0.3
    wait 1
openapp Zalo                      # và mọi cử chỉ ở mục trên
```

Tham số **giữ** của `swipe` không phải chi tiết vụn: vuốt lên từ mép dưới rồi
nhả ngay thì iOS về màn hình chính, phải *giữ* lại mới ra trình chuyển app.

- **Kiểm tra** — dò cú pháp và in lại kịch bản bằng tiếng Việt để soát trước khi
  chạy. Lỗi báo kèm **số dòng**. Toạ độ ngoài khoảng 0..1 bị từ chối ngay, vì đó
  gần như luôn là nhầm pixel với tỉ lệ.
- **Dừng** — huỷ giữa chừng trên tất cả máy.
- **Mở… / Lưu…** — kịch bản là file `.txt` thường.
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
    "thumb_long_edge": 320,     // cạnh dài ảnh thu nhỏ, px
    "connect_concurrency": 24,  // số máy bắt tay RFB cùng lúc
    "max_connected": 0,         // 0 = không giới hạn
    "reconnect_delay": 3.0,     // giây, tăng gấp đôi tới reconnect_max
    "reconnect_max": 60.0,
    "stall_timeout": 20.0       // không có frame quá lâu -> coi như treo, nối lại
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
  scan.py                dò cổng VNC theo CIDR / dải / bảng ARP
  script.py              ngôn ngữ kịch bản: parse, describe, runner
  gestures.py            cử chỉ iOS dựng sẵn, nạp đè từ config/gestures.json
  util/png.py            ghi PNG bằng thư viện chuẩn (không cần Pillow/Qt)
  vnc/session.py         một kết nối RFB: tier, vòng đọc, vòng nhịp, input, chụp
  vnc/pool.py            toàn bộ session trên 1 event loop ở thread riêng;
                         chụp hàng loạt, ghi hình, chạy kịch bản
  ui/tile.py             một ô trong lưới
  ui/grid.py             lưới cuộn + ảo hoá (chỉ ô nhìn thấy mới lên tier GRID)
  ui/detail.py           khung điều khiển full độ phân giải
  ui/app.py              cửa sổ chính, toolbar, phân trang, broadcast, kịch bản
tests/fake_vnc.py        server RFB 3.8 giả để test không cần iPhone
tools/bench_scale.py     đo tải với N máy giả
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

75 test, gồm: tier IDLE thật sự im lặng · tier GRID stream và ảnh đúng kích
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
bấm tại chỗ ra chạm, không lẫn nhau.

Đo tải:

```powershell
.\.venv\Scripts\python.exe tools\bench_scale.py --devices 250 --visible 40 --seconds 20
```

## Giới hạn đã biết

- **Phím đặc biệt tới iOS** (Home, khoá máy…) phụ thuộc TrollVNC map keysym thế
  nào ở phía máy. Các phím thường, Enter, Backspace, mũi tên thì đi qua bình
  thường. Nếu TrollVNC của bạn có bảng map riêng, sửa `SPECIAL_KEYS` trong
  `controlios/ui/detail.py`.
- **Lăn chuột** gửi đúng nút 4–7 theo chuẩn RFB, nhưng iOS có nhận thành cuộn
  hay không thì tuỳ TrollVNC. Nếu không cuộn, dùng `swipe` thay thế — cử chỉ
  vuốt luôn hoạt động vì nó chỉ là nhấn–di–nhả.
- Client chỉ đăng ký encoding **ZLib**; nếu TrollVNC không hỗ trợ, nó rơi về
  **Raw** (vẫn chạy, chỉ tốn băng thông hơn).
- Xoay màn hình làm đổi kích thước framebuffer sẽ khiến phiên đó ngắt rồi tự
  nối lại — không mất máy, chỉ chớp một nhịp.
- Ghi hình ra **chuỗi PNG**, chưa mã hoá thẳng thành video (dùng lệnh ffmpeg ở
  trên để ghép). Đổi lại là không cần cài thêm gì và không mất khung hình nào.
- Kịch bản chạy **mở vòng**: nó gửi thao tác theo đúng thời gian đã ghi, chứ
  không đọc màn hình để chờ một nút hiện ra. Nếu máy phản ứng chậm, tăng `wait`.
  Muốn kiểm chứng thì chèn `shot` ở các mốc rồi xem lại ảnh.
- **Không liệt kê được app đã cài dạng text**, và không mở/đóng app theo bundle
  id — VNC không có kênh cho việc đó (xem mục "Thao tác app"). Nếu các iPhone
  này có **SSH** (máy jailbreak thường có Dropbear/OpenSSH cổng 22), thì làm
  được thật: `uicache -l` liệt kê bundle id, `open <bundle>` mở app,
  `killall <tên>` đóng app. Đó sẽ là một kênh điều khiển thứ hai bên cạnh VNC,
  chưa cài trong bản này.
- Toạ độ cử chỉ mặc định nhắm iPhone Face ID dọc màn hình. Máy có nút Home vật
  lý, hoặc iOS khác đời, cần chỉnh `config/gestures.json`.
