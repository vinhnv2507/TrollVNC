# Tối ưu độ mượt view/control (nét là phụ)

Mục tiêu: điều khiển bám tay nhất, độ nét vừa đủ không mờ quá.

## Trễ ~1s trên farm đông máy? → BẬT "Chỉ truyền máy đang xem"
Nguyên nhân số 1 gây trễ trên farm WiFi: mở 1 máy điều khiển nhưng **các ô lưới
vẫn đang stream** → 249 máy kia tranh băng thông của máy bạn đang bấm.

**Chất lượng → "Chỉ truyền máy đang xem (tắt lưới khi điều khiển)"** (mặc định
BẬT): khi mở 1 máy, tạm NGƯNG stream toàn bộ lưới → dồn trọn băng thông cho máy
đó → trễ giảm mạnh. Lưới đứng hình lúc đang điều khiển; đóng máy ra thì lưới chạy
lại. Đây là đòn bẩy trễ lớn nhất, **có ngay trên PC không cần cài lại máy**.

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

## Ghi chú
- Tham số Q/defer/xoay áp live sẽ **giữ tới khi daemon khởi động lại** (máy reboot
  / TrollVNC chạy lại). Sau reboot mở lại **Chất lượng → Áp dụng** để nạp lại,
  hoặc cố định bằng Managed.plist.
