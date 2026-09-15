# Tối ưu độ mượt view/control (nét là phụ)

Mục tiêu: điều khiển bám tay nhất, độ nét vừa đủ không mờ quá.

## PC 0.2.25: canh EarnApp không còn fail vì disconnected during capture

OCR EarnApp đọc framebuffer trên máy. Bản trước **bắt chụp một khung VNC full**
trước khi OCR. Khi đang mở khung lớn (LIVE), socket nửa sống hoặc FBUR ma của
pipeline=2 làm phiên VNC rớt ngay: log `LỖI kiểm tra màn hình: ... disconnected
during capture`, chưa kịp OCR.

PC 0.2.25 (không cần cài lại iOS):

- Máy LIVE/GRID vừa có khung (<2.5s): OCR luôn, không gửi thêm capture full.
- Capture lỗi: nối lại tối đa 3 lần; nếu VNC vẫn online thì OCR trên khung đang có.
- Capture không còn chờ stall_timeout 20s rồi giết phiên LIVE vì inflight ma.

Đóng bản PC đang chạy rồi mở 0.2.25.

## PC 0.2.24: captcha TikTok/Shopee không còn đứng hình đến lúc thả chuột

0.2.16–0.2.23 dồn MouseMove bằng `call_soon` (một điểm mỗi vòng asyncio) và
quên `_inflight` **mỗi nhịp** lúc kéo. Qt có thể xếp hết các điểm kéo trước
khi vòng lặp chạy, nên iOS chỉ nhận down rồi một move lúc mouse_up: mũi tên
và mảnh puzzle đứng yên, nhảy khi thả, captcha fail. Kéo tay trên máy thì
bình thường vì không đi qua cầu PC.

PC 0.2.24 (không cần cài lại iOS):

- Lúc **giữ chuột**: gửi từng PointerEvent ngay, không dồn thành 1 điểm cuối.
- Quên FBUR ma **một lần** khi bắt đầu kéo (vẫn sửa đóng băng 0.2.15), không
  gửi chồng request lúc encode Q=1 đang chạy.
- LIVE lúc xem vẫn pipeline=2 + Tight JPEG; lúc kéo vẫn depth=1.

Đóng bản PC đang chạy rồi mở 0.2.24.

## PC 0.2.16: hết đóng băng hình khi kéo (lỗi 0.2.15)

0.2.15 tắt pipeline lúc kéo captcha nhưng **chờ khung** của request pipeline
cũ. Máy Q=1 (ưu tiên độ trễ thấp) nuốt encode thừa nên client ngồi chờ tới
stall_timeout (~20s) mới có 1 khung; iOS vẫn nhận vuốt vì socket pointer
còn sống.

PC 0.2.16 (không cần cài lại iOS 4.11):

- Không chờ khung nếu không còn request thật. Quên FBUR ma khi chuyển sang
  kéo/chạm, rồi xin khung mới ngay.
- LIVE lúc **đang xem**: vẫn 2 FBUR chồng, Tight JPEG, sàn 30fps.
- LIVE lúc **đang kéo**: vẫn 1 FBUR, request ngay khi khung về.

Đóng 0.2.15 đang chạy rồi mở 0.2.16. Ping farm ~180ms vẫn là trần vật lý.

## PC 0.2.15: lúc kéo captcha thì 1 request, không xếp khung cũ
0.2.14 xin Tight JPEG + tạm dừng lưới (giữ nguyên) nhưng **pipeline 2 khung
LIVE lúc đang kéo** làm ảnh trễ thêm ~1 RTT. Ping farm ~180ms → user nhìn
thanh captcha cũ ~360ms, kéo Shopee rất khó / không kéo được.

PC 0.2.15 (không cần cài lại iOS 4.11):

- Tight JPEG + luôn tạm dừng lưới khi mở máy: như 0.2.14.
- LIVE khi **đang xem**: vẫn 2 FBUR chồng, sàn 30fps, giấu RTT.
- LIVE khi **đang kéo/chạm**: chỉ 1 FBUR, request ngay khi khung về. Ảnh tươi
  trong ~1 RTT (~180ms WiFi), không xếp thêm khung cũ. Trần fps lúc kéo = 1/RTT
  (khoảng 5 khung/giây trên farm) nhưng bám tay hơn 11 khung/giây ảnh cũ.

Đóng bản 0.2.13/0.2.14 đang chạy rồi mở 0.2.15. Ping ~180ms vẫn là trần vật lý
của LAN farm; không có cách nào kéo captcha nhanh hơn 1 RTT nếu không đổi mạng.

## PC 0.2.14: Tight JPEG + 30fps + tạm dừng lưới khi mở máy
Giật khi kéo captcha Shopee trên farm WiFi **không phải vì hết RAM**. Đo trên
máy nội bộ: ping ~180ms, client cũ chỉ xin ZLib-raw, LIVE 12fps kiểu gửi-rồi-chờ
→ trần khoảng 5 khung/giây nên thanh captcha kéo rất khó.

PC 0.2.14 (không cần cài lại iOS 4.11):

- Xin Tight JPEG (encoding 7, QualityLevel 6). Khung Shopee đầy màu nhẹ hơn nhiều
  so với zlib-raw 32bpp. Daemon TrollVNC đã link turbojpeg sẵn.
- LIVE luôn sàn 30fps và gửi **2 FramebufferUpdateRequest chồng nhau** để giấu
  một RTT (~180ms) thay vì request-then-wait. (0.2.15 tắt pipeline này lúc kéo.)
- Khi mở/điều khiển 1 máy, **luôn tạm dừng stream lưới** — không phụ thuộc
  checkbox cũ, vì bản lưu trước đó thường đang TẮT nên farm vẫn tranh băng thông.

Đóng bản 0.2.13 đang chạy rồi mở bản mới. WiFi farm đông máy vẫn là trần vật lý;
tắt lưới khi mở máy là đòn bẩy lớn nhất phía PC.

## Trễ ~1s trên farm đông máy? → lưới phải dừng khi đang điều khiển
Nguyên nhân số 1 gây trễ trên farm WiFi: mở 1 máy điều khiển nhưng **các ô lưới
vẫn đang stream** → 249 máy kia tranh băng thông của máy bạn đang bấm.

Từ 0.2.14 PC **luôn** tạm NGƯNG stream toàn bộ lưới khi có máy đang mở/điều
khiển → dồn trọn băng thông cho máy đó. Lưới đứng hình lúc đang điều khiển;
đóng máy ra thì lưới chạy lại. Checkbox "Chỉ truyền máy đang xem" vẫn còn
trên dialog nhưng hành vi này không còn bị bản config cũ (mặc định TẮT) làm hỏng.

## Chỉnh NGAY trên PC (không cần cài lại) — **Chất lượng**
Menu **Chất lượng** trên thanh công cụ, nhóm **"Độ mượt (áp thẳng lên máy)"**:

- **Ưu tiên độ trễ THẤP (Q=1)** — BẬT. Máy bỏ khung cũ khi chưa gửi kịp → điều
  khiển bám tay, hợp auto/bấm nhanh. (Tắt = giữ 2 khung, mượt hơn khi mạng ổn
  nhưng trễ hơn.) Áp `setinflight` + `setdefer` xuống máy, **không nối lại**.
- **Đồng bộ xoay màn hình** — TẮT (khuyên cho farm). Bỏ qua xoay → **không resize
  khi app xoay** → hết cảnh **chớp đen/nối lại giữa chừng**. (`setorient off`)
- **Scale khung máy gửi** — `0.35×`–`0.5×`. Đây là đòn bẩy băng thông lớn nhất.
  `0.35×` bạn thấy mượt là hợp; muốn nét hơn chút thì `0.5×`.

> Ba cái trên áp qua control socket, **không đổi kích thước framebuffer** nên
> KHÔNG nối lại. Chỉ **đổi Scale** mới làm nối lại một nhịp (đổi cỡ khung) — nên
> chọn scale một lần rồi để yên, đừng đổi tới lui.

Bên "Máy đang mở": **Tốc độ** 20–30 hình/giây là đủ mượt; **Độ nét** 640–900px
(khung điều khiển chỉ rộng ~500px nên nhận lớn hơn là phí).

## Vì sao hay bị "đen màn xong có lại"
Framebuffer **đổi cỡ** giữa chừng thì client phải bắt tay lại (một nhịp đen). Có
2 nguồn: (1) **xoay màn** khi bật đồng bộ xoay — tắt bằng ô ở trên; (2) **đổi
scale** — chủ động, chỉ 1 nhịp khi bạn bấm. Tắt đồng bộ xoay là hết phần lớn.

## Chỉnh sâu khi BUILD (Managed.plist) — cho cả farm, cần build lại
Muốn cố định tối ưu ngay từ đầu, commit `prefs/TrollVNCPrefs/Resources/Managed.plist`
(theo README) với các khoá:

- `MaxInflight` = `1` (Q=1, trễ thấp)
- `DeferWindowSec` = `0.008` (trễ thấp) — hoặc `0.02` nếu muốn nhẹ băng thông
- `Scale` = `0.4`
- `FrameRateSpec` = `"60"` (hoặc `"30:60:60"`)
- `OrientationSync` = `false` (hết chớp đen do xoay)
- **Dò vùng bẩn (tiết kiệm băng thông WiFi cho farm)**: `FullscreenThresholdPercent`
  = `35`, `TileSize` = `48`. Chỉ gửi vùng THAY ĐỔI → nhẹ mạng khi màn tĩnh, đổi
  lại tốn CPU máy hơn. Máy quá cũ (6s) thì cân nhắc để `0` (tắt) + hạ `Scale`.
- `MaxRects` = `256`

> Các khoá này daemon đọc **lúc khởi động**. Q/defer/xoay còn chỉnh được LIVE từ
> PC (mục trên); riêng dò-vùng-bẩn và frame rate hiện chỉ đặt lúc build.

## Máy đầy RAM / remote giật — nút **RAM** / lệnh `freeram`
Khi nhiều app nặng (Safari, Facebook, EarnApp, Golike…) cùng mở, VNC dễ giật.
Bấm **RAM** trên PC (cạnh Home/App/Khoá), menu **Giải phóng RAM** trong app
ControlIOS, hoặc lệnh kịch bản `freeram` / JS `freeRAM()`.

Lệnh đóng hết app đang chạy trừ ControlIOS, TrollStore và tiến trình hệ thống.
**EarnApp và Golike cũng bị đóng** — chỉ bấm khi muốn nhường RAM cho remote.
Không tự chạy lúc kết nối VNC. `closeall 5` chỉ hất thẻ switcher, không thay `freeram`.

## Ghi chú
- Tham số Q/defer/xoay áp live sẽ **giữ tới khi daemon khởi động lại** (máy reboot
  / TrollVNC chạy lại). Sau reboot mở lại **Chất lượng → Áp dụng** để nạp lại,
  hoặc cố định bằng Managed.plist.
