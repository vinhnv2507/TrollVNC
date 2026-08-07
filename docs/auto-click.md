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
> **Lấy màu trên PC:** rê chuột trên khung điều khiển lớn — thanh dưới hiện
> `x=.. y=..  ·  0.500 0.900  ·  #RRGGBB` (tỉ lệ + mã màu tại điểm đó).
>
> **Chèn thẳng vào kịch bản:** trong dialog **Auto-click JS** bấm **🎨 Lấy màu**
> rồi bấm 1 điểm trên màn hình lớn → lệnh **tự chèn vào ô soạn + chép clipboard**.
> Menu ▾ chọn kiểu: **nếu khớp màu → chạm** `if (matchColor) tap` / **chờ ra màu
> → chạm** `if (waitColor) tap` / `matchColor` / `waitColor` / `getColor` /
> `tap(x,y)` / chỉ chép `#RRGGBB`. (Bấm thẳng nút = `matchColor`.)
>
> "Nếu khớp màu → chạm" là mẫu hay dùng nhất: **chỉ bấm khi điểm đó đúng màu**.
> ```js
> if (matchColor(0.806, 0.250, "35F7EF", 15)) tap(0.806, 0.250);
> ```
>
> Khi lấy màu, PC **hỏi thẳng máy** (lệnh control `color rx ry`) để lấy **màu
> THẬT** — daemon đọc pixel gốc trên framebuffer, đúng cái `getColor`/`matchColor`
> auto-click dùng, nên **khớp tuyệt đối** (không lệ thuộc khung PC bị nén). Status
> ghi rõ “màu THẬT từ máy”. Máy chạy bản TrollVNC cũ (chưa có lệnh này) thì tự lùi
> về màu đọc ở PC (gần đúng) và báo rõ.

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

**Tìm CHỮ trên màn (OCR + toạ độ)** — mạnh cho tự động theo giao diện
```js
let q = findText("Đăng nhập");     // {x,y} tỉ lệ tâm, không phân biệt hoa/thường
if (q) tap(q.x, q.y);
tapText("Đăng nhập");              // tìm chữ rồi chạm (1 dòng)
waitText("Trang chủ", 10);         // chờ tới khi chữ hiện (tối đa 10s)
```
**Tiện ích dựng sẵn (prelude)**
```js
swipeUp(); swipeDown(); swipeLeft(); swipeRight();   // vuốt theo hướng
tapImage("/var/mobile/Media/tpl.png");               // tìm ảnh rồi chạm
waitImage("/var/mobile/Media/tpl.png", 10);          // chờ ảnh hiện
tapIfColor(0.5, 0.9, "FF3B30", 15);                  // chạm nếu đúng màu
repeat(5, function(i){ tap(0.5, 0.9); });            // lặp N lần
retry(3, function(){ return tapText("OK"); });       // thử lại tới khi thành công
```
**Biến BỀN / clipboard / ảnh / thời gian / phím cứng**
```js
setVar("dem", getVar("dem", 0) + 1);   // lưu qua CÁC LẦN chạy (file vars.json)
let n = getVar("dem", 0);              // đọc, kèm mặc định
let c = getClipboard(); setClipboard("abc");  // bảng tạm iOS
saveScreenshot("/var/mobile/Media/shot.png"); // chụp màn ra PNG
let t = now();                          // mốc mili-giây (đo thời lượng)
volumeUp(); volumeDown(); mute(); lockScreen();  // phím cứng
```

**App / URL / Thông báo**
```js
launchApp("com.zing.zalo");           // mở app theo bundle id
killApp("com.zing.zalo");             // đóng app
openURL("https://x.com");             // mở URL (app mặc định)
openURLIn("com.zing.zalo", "zalo://"); // mở URL bằng app chỉ định
toast("Xong!");                       // ghi vào NHẬT KÝ (xem trên PC), alert cũng vậy
log("bước 1 xong");                    // ghi nhật ký (xem trên PC hoặc log daemon)
```
**Tệp / HTTP / JSON**
```js
let s = readFile("/var/mobile/x.txt");        // đọc chuỗi
writeFile("/var/mobile/x.txt", "nội dung");   // ghi
if (fileExists("/var/mobile/x.json")) { ... }
let o = JSON.parse(readFile("/var/mobile/x.json")); // JSON có SẴN của JS
let r = httpGet("https://api.example.com/data");    // GET (đồng bộ) -> chuỗi
let r2 = httpPost("https://api.example.com", "a=1&b=2"); // POST
```

> Trong trình soạn, bấm **“＋ Chèn”** để chọn lệnh chèn sẵn: cử chỉ, chờ-lặp,
> màu-ảnh, app-web, tệp, và **cấu trúc** — hàm, vòng lặp có **nhãn**
> (`break/continue label`), **khung máy-trạng-thái** (thay `goto`/label kiểu
> AutoTouch), `try/catch`, `switch`, mảng, JSON. Kịch bản **lưu tự động** trên
> máy (bấm Bắt đầu là lưu).
>
> JS **không có `goto`**, nhưng dùng **hàm** hoặc **máy trạng thái** thay được:
> đặt `let buoc = "..."`, mỗi `case` là một nhãn, gán `buoc = "tên"` chính là
> "nhảy tới". Vòng lặp lồng thì dùng nhãn: `ngoai: for(...){ break ngoai; }`.

### Ví dụ: chỉ bấm khi nút đỏ hiện, tối đa 100 lần
```js
for (let i = 0; i < 100 && !false; i++) {
  if (matchColor(0.5, 0.9, "FF3B30", 15)) tap(0.5, 0.9);
  sleep(random(1, 2));
}
```

## Cập nhật KHÔNG cần cài lại app
- **Kịch bản & hàm JS**: đẩy qua PC tức thì — muốn thêm hàm mới cứ để đầu script,
  hoặc bấm **“⇪ Đẩy làm thư viện hàm”** để nạp nội dung ô soạn thành **thư viện**
  (nạp trước MỌI kịch bản trên máy) → thêm hàm dùng chung cho cả farm, **không
  cài lại**. (Lệnh control `setprelude`/`getprelude`, lưu `…/controlios/prelude.js`.)
- **Chỉ khi thêm primitive NATIVE mới** (HID, Vision/OCR, đọc framebuffer…) mới
  phải build lại + cài đè — vì máy chỉ-TrollStore không hot-swap được binary đã ký.

## Lệnh control (PC cũng điều khiển được)
```
autoset <base64 kịch bản JS>   # lưu
autostart / autostop / autostatus
autoget                        # OK <base64 kịch bản>
```
Kịch bản lưu ở `/var/mobile/Library/controlios/autoscript.txt`.

## Theo dõi tiến trình (nhật ký)
`log("...")` và `toast("...")` ghi vào **nhật ký** trên máy. Trên **PC**: mở
**Kịch bản ▾ → Auto-click JS** — có ô **“Nhật ký tiến trình”** tự kéo về từ máy
đầu tiên đang chọn (~1.2s/lần), hiện cả `▶ bắt đầu`, `■ dừng`, `⚠ lỗi`. Đây là
cách theo dõi đáng tin nhất.

### Tự ghi tiến trình (trace) — MẶC ĐỊNH BẬT
Không cần chèn `log()`: daemon **tự ghi mỗi lệnh + kết quả** vào nhật ký, ví dụ:
```
16:40:01  matchColor 0.806,0.250 "35F7EF" = true
16:40:01  tap 0.806, 0.250
16:40:02  sleep 1.50s
16:40:03  waitColor 0.500,0.900 "34C759" = false (hết 10s)
16:40:03  findImage nut.png = 0.512,0.744
```
Có với: `tap/tapRegion/doubleTap/two-threeFingerTap/longPress/swipe/home/key/
typeText/sleep/getColor/matchColor/waitColor/findImage/launchApp/killApp/openURL`.
Vòng lặp dày thì nhật ký giữ **250 dòng gần nhất**. Muốn tắt cho gọn: gọi
`setTrace(false);` đầu kịch bản (bật lại `setTrace(true);`).

> `toast`/`alert` **chỉ ghi vào nhật ký** (xem trên PC). Máy chỉ-TrollStore
> (không jailbreak) không vẽ được chữ nổi đè lên app khác — muốn HUD nổi kiểu
> AutoTouch cần tweak inject vào SpringBoard (chỉ có khi jailbreak).

## Ghi chú
- Chạy trong **daemon**, luồng riêng, tách khỏi luồng VNC. Lỗi JS **không sập
  daemon** (bắt exception). **Dừng** ăn ngay ở lệnh kế (tap/sleep tự kiểm cờ dừng)
  — nên vòng lặp phải có `tap`/`sleep` (auto-click luôn có).
- Toạ độ theo màn **dọc**. Dò màu/tìm ảnh chuẩn nhất khi **không xoay**.
- `findImage` khớp theo ~25 điểm mẫu nên **nhanh nhưng gần đúng**; dùng ảnh nhỏ
  (biểu tượng/nút) + giới hạn vùng cho chính xác và nhẹ.
