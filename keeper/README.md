# ControlIOS Keeper

App phụ **rất nhỏ**, cài **một lần** cho mỗi máy, chạy nền và **tự mở lại app
ControlIOS** khi nó chết — nhờ vậy sau khi bạn cài đè bản ControlIOS mới, máy tự
hồi phục **không cần mở tay**.

## Vì sao cần
Trên TrollStore (chưa jailbreak), cài đè sẽ kill tiến trình cũ và iOS **không tự
chạy lại** app nền. Nhưng bản thân app ControlIOS đã có **watchdog 3 giây** tự
dựng lại server — chỉ thiếu khâu "làm app chạy lại". Keeper lấp đúng khâu đó:

- Keeper mở cổng kiểm tra `46751` (ControlIOS mở cổng này khi còn sống).
- Cứ 20s Keeper thử cổng đó. Chết liên tục ~60s (3 lần) → gọi SpringBoard
  `SBSLaunchApplication("com.controlios.app")` → ControlIOS chạy lại → watchdog
  của nó tự lo server.
- **Keeper KHÔNG bị cài đè** khi bạn update ControlIOS, nên nó sống sót và hồi
  phục ControlIOS sau mỗi lần cài đè.

## Build .tipa
### Cách 1 — GitHub Actions (khuyên dùng)
Actions → **Build ControlIOS Keeper** → Run workflow → tải artifact
`ControlIOSKeeper-tipa` → có `ControlIOSKeeper.tipa`.

### Cách 2 — máy Mac có theos
```sh
cd keeper
make tipa FINALPACKAGE=1
# -> keeper/packages/ControlIOSKeeper.tipa
```

## Cài & dùng
1. Mở `ControlIOSKeeper.tipa` bằng **TrollStore** → Install (một lần cho mỗi máy).
2. **Mở app "ControlIOS Keeper" một lần** để nó bắt đầu chạy nền (từ đó về sau tự
   chạy; chỉ mở lại nếu máy reboot hoặc iOS thu hồi bộ nhớ).
3. Xong. Từ giờ khi bạn cài đè ControlIOS, Keeper tự mở lại ControlIOS trong ~1
   phút. Trên PC bấm **"Nối lại ngay"** nếu muốn bám lại tức thì.

> Lưu ý reboot: sau khi máy khởi động lại, cả Keeper lẫn ControlIOS đều chưa chạy
> (TrollStore không có launchd nền). Cần mở **Keeper** một lần sau reboot; sau đó
> Keeper lo phần còn lại. Nếu muốn tự chạy cả sau reboot thì cần máy jailbreak
> (LaunchDaemon) — TrollStore thuần không làm được.

## Giữ nền bằng cách nào
Để không bị iOS treo khi ở nền, Keeper **phát một file âm thanh IM LẶNG lặp vô
hạn** (background mode `audio`, `MixWithOthers` nên không cắt âm app khác). Nhờ
vậy timer kiểm tra tiếp tục chạy trong nền. (Chỉ `network-authentication` không
đủ để giữ app sống.)

## Chỉnh
Trong `AppDelegate.m`:
- `kCheckInterval` (20s), `kFailsBeforeLaunch` (3) — nhịp kiểm tra và độ trễ trước
  khi mở lại.
- `kLaunchSuspended` — `false` mở ControlIOS foreground (chắc chắn chạy, hiện app
  một nhịp); đổi `true` để thử mở nền (không cướp app đang mở) nếu máy bạn hỗ trợ.
