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
Kịch bản chạy **MỘT lượt**; muốn lặp thì bọc trong `loop`.

**Chạm/cử chỉ**
```
tap 0.5 0.9                 # chạm 1 điểm
tap 0.4 0.8 0.6 0.95        # chạm NGẪU NHIÊN trong vùng (x1 y1 x2 y2) — nhân bản hoá
doubletap 0.5 0.5
twofinger 0.5 0.5           # chạm 2 ngón
threefinger 0.5 0.5
longpress 0.5 0.5 1.0       # giữ 1 giây
swipe 0.5 0.8 0.5 0.3 0.4   # vuốt (x1 y1 -> x2 y2) trong 0.4 giây
home                        # nút Home
key a                       # gõ 1 phím
text hello world            # gõ chuỗi
```
**Chờ**
```
wait 2                      # 2 giây
wait 1-3                    # NGẪU NHIÊN 1–3 giây
```
**Vòng lặp** (lồng nhau được)
```
loop 100        # lặp 100 lần (0 hoặc bỏ trống = vô hạn)
  tap 0.5 0.9
  wait 1-2
end
```
**Theo MÀU điểm ảnh** (dò trên màn thật)
```
ifcolor 0.5 0.5 FF3B30 15   # nếu điểm (0.5,0.5) ~ đỏ (sai số 15) thì...
  tap 0.5 0.5
end
ifnotcolor 0.9 0.1 FFFFFF   # nếu KHÔNG phải trắng thì...
  home
end
waitcolor 0.5 0.5 34C759 10 # chờ tới khi điểm thành xanh (tối đa 10s)
stopifcolor 0.5 0.9 000000  # gặp màu này thì DỪNG kịch bản
stop                        # dừng ngay
# dòng bắt đầu bằng # là chú thích
```
> Màu RRGGBB (hex), tham số `[sai]` = dung sai 0–255 (mặc định 12). Dò màu chuẩn
> nhất khi máy để **dọc, không xoay**.

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
