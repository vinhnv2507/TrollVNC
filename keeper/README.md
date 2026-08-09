# ControlIOS Keeper

Giữ cho app ControlIOS luôn sống: cài **một lần**/máy, sau đó **tự mở lại
ControlIOS** khi nó chết (kể cả sau khi bạn cài đè bản mới) — **không cần mở tay**.

## Cách hoạt động (đã đổi sang DAEMON độc lập)
Gồm 2 phần:
- **keeperd** — một *daemon* nền (tiến trình root, tách session) canh cổng **46751**
  (ControlIOS mở cổng này khi còn sống). Chết ~60s → gọi SpringBoard
  `SBSLaunch("com.controlios.app")`. keeperd **sống độc lập với app**, không chết
  khi bạn vuốt tắt app hay khi cài đè ControlIOS. Giống hệt cách ControlIOS spawn
  `trollvncmanager`.
- **App ControlIOS Keeper** — chỉ là *bệ phóng*: mở app một lần, nó **spawn keeperd**
  (root, `POSIX_SPAWN_SETSID`, dùng persona như TrollVNC). Xong có thể **tắt app**,
  keeperd vẫn chạy.

> Vì sao phải là daemon: iOS treo/giết app nền — audio keep-alive không đủ và chết
> khi vuốt tắt. Chỉ tiến trình tách khỏi vòng đời app mới bất tử qua force-quit.

## Build .tipa
### GitHub Actions
Actions → **Build ControlIOS Keeper** → Run workflow → tải artifact
`ControlIOSKeeper-tipa`.

### Máy Mac có theos
```sh
cd keeper
make tipa FINALPACKAGE=1
# -> keeper/packages/ControlIOSKeeper.tipa
```

## Cài & dùng
1. TrollStore cài `ControlIOSKeeper.tipa` (một lần/máy).
2. **Mở app "ControlIOS Keeper" một lần** → nó spawn keeperd. Màn hình hiện
   "● Daemon đang chạy nền ✓". Từ đó **tắt app thoải mái**, keeperd vẫn sống.
3. Xong. Cài đè ControlIOS → keeperd tự mở lại trong ~1 phút. PC bấm **"Nối lại
   ngay"** nếu muốn bám tức thì.

## Giới hạn
- **Sau REBOOT**: keeperd chưa chạy (TrollStore không có launchd) → mở app Keeper
  **một lần** sau reboot để spawn lại keeperd. Rồi keeperd lo phần còn lại. Tự chạy
  sau reboot cần máy jailbreak (LaunchDaemon).
- Nếu iOS jetsam giết keeperd (hiếm, khi cực thiếu RAM), mở app Keeper lại để spawn.

## Chỉnh
- `keeper/keeperd/main.m`: `kSleepSeconds` (nhịp), `kFailsBeforeLaunch`,
  `kLaunchSuspended` (false = mở foreground; true = thử mở nền).
- Yêu cầu entitlement: app cần `com.apple.private.persona-mgmt` để spawn root;
  keeperd cần `com.apple.springboard.launchapplications` để mở app.
