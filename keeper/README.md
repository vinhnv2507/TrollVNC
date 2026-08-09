# ControlIOS Keeper

Giữ cho app ControlIOS luôn sống: cài **một lần**/máy, sau đó **tự mở lại
ControlIOS** khi nó chết (kể cả sau khi bạn cài đè bản mới) — **không cần mở tay**.

## Cách hoạt động (đã đổi sang DAEMON độc lập)
Gồm 2 phần:
- **keeperd** — một *daemon* nền (tiến trình root, tách session) **canh tiến trình
  `trollvncmanager` của ControlIOS bằng kqueue `NOTE_EXIT`**: nó *block chờ* sự
  kiện tiến trình chết → phản ứng **TỨC THÌ** và gần như **0% CPU** (không poll
  định kỳ, ngủ hẳn tới khi có sự kiện). Khi ControlIOS chết (cài đè/kill) →
  `SBSLaunch("com.controlios.app")` ngay. keeperd **sống độc lập với app**, không
  chết khi vuốt tắt app hay cài đè ControlIOS. Giống cách ControlIOS spawn
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

## Tự hồi phục sau reboot (best-effort)
Keeper đăng ký một **BGProcessingTask** (BackgroundTasks). Sau khi máy khởi động
lại **và được mở khoá một lần**, iOS sẽ **tự bật lại app Keeper** khi có cơ hội
(hay chạy lúc đang sạc) để chạy task → app launch → `main()` spawn keeperd → hồi
phục **không cần mở tay**. Thời điểm do iOS quyết (có thể vài phút–vài giờ), nên
đây là best-effort. Muốn chắc/nhanh hơn: thêm Automation Shortcuts **"Theo giờ"**
hoặc **"Khi cắm sạc"** → Mở app ControlIOS Keeper (hai trigger này hỗ trợ tắt
"Hỏi trước khi chạy"; trigger Wi-Fi thì không).

## Giới hạn
- **Trước lần MỞ KHOÁ đầu sau cold-boot (BFU)**: iOS mã hoá toàn bộ, KHÔNG tiến
  trình/automation nào chạy — kể cả Shortcuts. Bắt buộc nhập passcode 1 lần. Không
  cách nào lách trên máy chưa jailbreak.
- Tự chạy NGAY khi boot (không cần mở khoá) chỉ có nếu **jailbreak** (LaunchDaemon).
- Nếu iOS jetsam giết keeperd (hiếm, khi cực thiếu RAM), mở app Keeper lại để spawn.

## Chỉnh
- `keeper/daemon/main.m`: `kSleepSeconds` (nhịp), `kFailsBeforeLaunch`,
  `kLaunchSuspended` (false = mở foreground; true = thử mở nền).
- Yêu cầu entitlement: app cần `com.apple.private.persona-mgmt` để spawn root;
  keeperd cần `com.apple.springboard.launchapplications` để mở app.
