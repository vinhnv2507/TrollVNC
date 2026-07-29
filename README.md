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
  Kéo/thả chuột và gõ phím trên khung đó sẽ đi thẳng tới máy.
- **Chọn nhiều máy**: click, `Ctrl`+click, `Shift`+click, hoặc **Chọn tất cả**.
- **Gửi thao tác tới các máy đã chọn**: bật ô này thì mỗi cú click trên khung
  điều khiển được phát cho toàn bộ máy đang chọn. Toạ độ gửi đi là **tỉ lệ**
  (0..1) chứ không phải pixel, nên máy khác kích thước màn hình vẫn chạm đúng
  chỗ.
- **Trang**: mặc định 100 máy/trang. Chọn "Tất cả" để nạp hết 250. Máy ngoài
  trang hiện tại không được kết nối — đây là cách "mở lần lượt" nếu muốn.

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
  vnc/session.py         một kết nối RFB: tier, vòng đọc, vòng nhịp, input
  vnc/pool.py            chạy toàn bộ session trên 1 event loop ở thread riêng
  ui/tile.py             một ô trong lưới
  ui/grid.py             lưới cuộn + ảo hoá (chỉ ô nhìn thấy mới lên tier GRID)
  ui/detail.py           khung điều khiển full độ phân giải
  ui/app.py              cửa sổ chính, toolbar, phân trang, broadcast
tests/fake_vnc.py        server RFB 3.8 giả để test không cần iPhone
tools/bench_scale.py     đo tải với N máy giả
```

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

11 test: tier IDLE thật sự im lặng, tier GRID stream và ảnh đúng kích thước,
tier LIVE trả full res, thăng tier thì stream lại, chuột/phím tới được server,
tự nối lại khi server chết và sống lại, pool kết nối nhiều máy, và lưới chỉ
thăng tier những ô nhìn thấy.

Đo tải:

```powershell
.\.venv\Scripts\python.exe tools\bench_scale.py --devices 250 --visible 40 --seconds 20
```

## Giới hạn đã biết

- **Phím đặc biệt tới iOS** (Home, khoá máy…) phụ thuộc TrollVNC map keysym thế
  nào ở phía máy. Các phím thường, Enter, Backspace, mũi tên thì đi qua bình
  thường. Nếu TrollVNC của bạn có bảng map riêng, sửa `SPECIAL_KEYS` trong
  `controlios/ui/detail.py`.
- Client chỉ đăng ký encoding **ZLib**; nếu TrollVNC không hỗ trợ, nó rơi về
  **Raw** (vẫn chạy, chỉ tốn băng thông hơn).
- Xoay màn hình làm đổi kích thước framebuffer sẽ khiến phiên đó ngắt rồi tự
  nối lại — không mất máy, chỉ chớp một nhịp.
- Chưa có ghi hình / chụp ảnh hàng loạt / kịch bản tự động. Lớp `DevicePool`
  đã có sẵn `broadcast_tap`, `broadcast_swipe`, `type_text` nên phần automation
  cắm vào là được.
