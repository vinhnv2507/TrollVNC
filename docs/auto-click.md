# Tự động chạm (auto-click chạy TRÊN MÁY, ngôn ngữ JavaScript)

Kịch bản **JavaScript** chạy **ngay trong daemon trên iPhone** (engine
JavaScriptCore — framework có sẵn của iOS), **không cần PC nối**. Bơm sự kiện qua
`STHIDEventGenerator` (đúng bộ VNC dùng để điều khiển) nên tác động lên **app đang
mở** trên máy. Mạnh như AutoTouch: có biến/hàm/vòng lặp/điều kiện của JS + dò
màu + tìm ảnh mẫu.

## Dùng
App **ControlIOS → ••• Công cụ → Tự động chạm**: soạn JS, **▶ Bắt đầu** (lưu +
chạy), **■ Dừng**. Chạy nền, tiếp tục dù thoát app / khoá màn. Cần đã **kích hoạt
bản quyền** khi bật gác cổng.

## API (toạ độ TỈ LỆ 0..1 trên màn dọc)
**Chạm/cử chỉ**
```js
tap(0.5, 0.9);                 // chạm
tapRegion(0.4, 0.8, 0.6, 0.95);// chạm NGẪU NHIÊN trong vùng (nhân bản hoá)
doubleTap(0.5, 0.5);
twoFingerTap(0.5, 0.5); threeFingerTap(0.5, 0.5);
longPress(0.5, 0.5, 1.0);      // giữ 1 giây
swipe(0.5, 0.8, 0.5, 0.3, 0.4);// vuốt trong 0.4 giây
home();                        // nút Home
key('a'); typeText('hello');   // gõ
assistiveTouch(true);          // bật/tắt AssistiveTouch iOS
```
**Thời gian & logic** — dùng thẳng JS
```js
sleep(2); sleep(random(1, 3)); // chờ (giây)
while (true) { ... }           // lặp mãi
for (let i = 0; i < 10; i++) { ... }
if (cond) { ... } else { ... }
let x = 0.5; function foo(){ ... }   // biến, hàm — JS đầy đủ
stop();                        // dừng kịch bản
log('thông báo');              // ghi log daemon
```
**Dò MÀU** (đọc màn thật)
```js
getColor(0.5, 0.5);                    // -> "RRGGBB"
matchColor(0.5, 0.5, "FF3B30", 15);    // -> true/false (sai số 15)
waitColor(0.5, 0.5, "34C759", 10, 12); // chờ tới khi xanh (tối đa 10s) -> bool
```
**Tìm ẢNH mẫu** (template matching theo lưới điểm — nhanh, hợp biểu tượng/nút)
```js
// đẩy ảnh mẫu .png xuống máy trước (nút "Đẩy file" ở PC), rồi:
let p = findImage("/var/mobile/Media/tpl.png");        // cả màn
let q = findImage("/var/mobile/Media/tpl.png", 0,0.5,1,1, 24); // vùng + sai số
if (p) tap(p.x, p.y);   // p = {x, y} tỉ lệ tâm, hoặc null nếu không thấy
```

### Ví dụ: chỉ bấm khi nút đỏ hiện, tối đa 100 lần
```js
for (let i = 0; i < 100 && !false; i++) {
  if (matchColor(0.5, 0.9, "FF3B30", 15)) tap(0.5, 0.9);
  sleep(random(1, 2));
}
```

## Lệnh control (PC cũng điều khiển được)
```
autoset <base64 kịch bản JS>   # lưu
autostart / autostop / autostatus
autoget                        # OK <base64 kịch bản>
```
Kịch bản lưu ở `/var/mobile/Library/controlios/autoscript.txt`.

## Ghi chú
- Chạy trong **daemon**, luồng riêng, tách khỏi luồng VNC. Lỗi JS **không sập
  daemon** (bắt exception). **Dừng** ăn ngay ở lệnh kế (tap/sleep tự kiểm cờ dừng)
  — nên vòng lặp phải có `tap`/`sleep` (auto-click luôn có).
- Toạ độ theo màn **dọc**. Dò màu/tìm ảnh chuẩn nhất khi **không xoay**.
- `findImage` khớp theo ~25 điểm mẫu nên **nhanh nhưng gần đúng**; dùng ảnh nhỏ
  (biểu tượng/nút) + giới hạn vùng cho chính xác và nhẹ.
