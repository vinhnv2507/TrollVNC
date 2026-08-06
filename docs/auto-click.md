# Tự động chạm (auto-click chạy TRÊN MÁY)

Kịch bản chạm/vuốt chạy **ngay trong daemon trên iPhone**, theo vòng lặp, **không
cần PC nối**. Bơm sự kiện qua `STHIDEventGenerator` (đúng bộ VNC dùng để điều
khiển) nên tác động lên **app đang mở** trên máy.

## Dùng
App **ControlIOS → nút ••• (Công cụ) → Tự động chạm**:
- Soạn kịch bản, bấm **▶ Bắt đầu** (lưu + chạy), **■ Dừng** để ngừng.
- Vòng lặp chạy trong nền, tiếp tục dù **thoát app / khoá màn**. Cần đã **kích
  hoạt bản quyền** (daemon gác cổng).

## Cú pháp (toạ độ TỈ LỆ 0..1 trên màn dọc)
```
tap 0.5 0.9              # chạm giữa, 90% chiều cao
doubletap 0.5 0.5        # chạm đúp
longpress 0.5 0.5        # giữ lâu
swipe 0.5 0.8 0.5 0.3 0.4  # vuốt (x1 y1 -> x2 y2) trong 0.4 giây
wait 2                   # chờ 2 giây
wait 1-3                 # chờ NGẪU NHIÊN 1–3 giây
home                     # nút Home
key a                    # gõ phím
# dòng bắt đầu bằng # là chú thích
```
**Cả kịch bản LẶP** tới khi bấm Dừng.

**Mẹo tìm toạ độ:** mở khung VNC máy đó trên PC — góc dưới hiện **tỉ lệ x y** khi
rê chuột; lấy số đó điền vào kịch bản.

## Lệnh control (để PC cũng điều khiển được)
```
autoset <base64 kịch bản>   # lưu kịch bản
autostart                   # bắt đầu
autostop                    # dừng
autostatus                  # OK running / OK stopped
autoget                     # OK <base64 kịch bản>
```
Đều **cần license hợp lệ** (gác cổng bản quyền). Kịch bản lưu ở
`/var/mobile/Library/controlios/autoscript.txt`.

## Ghi chú
- Vòng lặp trong **daemon** (không phải app) nên chạy nền được; app chỉ soạn +
  Bật/Tắt.
- Toạ độ theo màn **dọc** (portrait). Máy xoay ngang thì hình dung theo dọc.
- Có thể mở rộng sau: bật/tắt hàng loạt từ PC (lệnh đã có sẵn).
