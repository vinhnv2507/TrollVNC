# Kích hoạt bản quyền ControlIOS (offline, ký số, buộc UDID, thuê bao)

Daemon TrollVNC/ControlIOS chỉ phục vụ khi có **license hợp lệ**: chữ ký ECDSA
P-256 đúng, **đúng UDID máy**, **chưa hết hạn**. Thiếu/sai → daemon **từ chối mọi
client VNC** (newClientHook) và control socket trả `ERR NotActivated`.

**"Khoá có ích"**: control token lấy TỪ license (`tok`). Kẻ khác patch phần kiểm
cũng không có token đúng → PC không điều khiển được. (Vẫn là bảo vệ *offline* —
người rất giỏi trên máy jailbreak vẫn có thể can thiệp; muốn tuyệt đối phải online.)

## Thành phần
- `tools/controlios_keygen.py` — tool **VENDOR** tạo/ký license. Giữ RIÊNG.
- `tools/controlios_private.pem` — **KHOÁ RIÊNG**, đã `.gitignore`, **không bao giờ commit / chia sẻ**.
- Daemon (`src/trollvncserver.mm`): nhúng **khoá công khai** `kLicensePubKey`, đọc
  license ở `/var/mobile/Library/controlios/license.dat`, kiểm lúc khởi động.
- App: nút **"Kích hoạt"** → hiện UDID + trạng thái, dán license → ghi file + `relicense`.

## Thiết lập LẦN ĐẦU (một lần)
```powershell
cd D:\ControlIOS
.\.venv\Scripts\python.exe tools\controlios_keygen.py genkeys
```
→ In ra **mảng C khoá công khai**. Dán đè `static const uint8_t kLicensePubKey[65]`
trong `src/trollvncserver.mm`. (Khoá công khai sinh sẵn hiện tại khớp với
`controlios_private.pem` đang có trên máy bạn — dùng luôn được; muốn khoá mới thì
`genkeys` rồi dán lại.)

## Cấp key cho MỖI khách
1. Khách mở app ControlIOS → **Kích hoạt** → chép **UDID** gửi cho bạn.
2. Bạn tạo license (vd 30 ngày):
   ```powershell
   .\.venv\Scripts\python.exe tools\controlios_keygen.py issue --udid <UDID> --days 30
   ```
   → in ra **chuỗi license** (và `token` — đặt vào `control_token` ở PC nếu khách dùng WiFi).
   `--days 0` = vĩnh viễn.
3. Gửi chuỗi license cho khách. Khách: **Kích hoạt → Dán license & kích hoạt**.
   App ghi file + gọi `relicense`; nếu hợp lệ → daemon phục vụ ngay (respring cho chắc).

## Kiểm thử (mô phỏng daemon) trên PC
```powershell
.\.venv\Scripts\python.exe tools\controlios_keygen.py verify "<license>" --pub <hex65>
```

## Định dạng license
`b64url(payload_json).b64url(chữ ký DER)` — payload
`{"v":1,"udid":"..","exp":<epoch,0=vĩnh viễn>,"tok":"<b64>"}`, ký ECDSA-P256-SHA256.

## Giới hạn (trung thực)
- **Offline** nên người rất giỏi trên máy jailbreak vẫn có thể patch daemon. Buộc
  UDID + ký số chặn: chia sẻ key sang máy khác, tự chế key mới. Muốn thu hồi /
  chống patch triệt để → cần **kích hoạt online** (làm sau nếu cần).
- **GPL**: bán/phát tán bản khoá key cho người ngoài vướng GPL-2.0 của TrollVNC.
  Dùng nội bộ farm thì không sao.
- Mất `controlios_private.pem` = mất khả năng cấp key. **Sao lưu nơi an toàn.**
