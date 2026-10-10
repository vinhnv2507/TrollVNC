# CTLIOS Keygen 1.0.0

Tool riêng cho người cấp bản quyền. EXE không chứa khoá ký riêng và không nằm
trong bộ Manager gửi cho khách. Dùng lại `tools/controlios_private.pem` đã ký
cho các bản iOS hiện tại; tool kiểm tra khoá công khai trước khi tạo key.

1. Mở `CTLIOS Keygen.exe`. Tool tìm khoá ký trên máy và đọc token từ cấu hình
   Manager trong AppData. Có thể chọn file PEM và `config/devices.json` khác.
2. Dán UDID từ **Manager → Kích hoạt bản quyền qua LAN**, hoặc chọn thiết bị/IP
   rồi bấm **Lấy UDID qua LAN**. UDID khác với số serial. Mỗi dòng là một UDID
   hoặc `Tên máy | UDID`.
3. Chọn 7/30/90 ngày, số ngày tuỳ chọn, ngày giờ hết hạn hoặc vĩnh viễn. Thời
   hạn bắt đầu khi tạo key. Token phải trùng token kết nối đang dùng của máy.
4. Bấm **Tạo key**, chọn dòng và **Copy key dòng chọn**; dán vào Manager để
   kích hoạt. **Copy tất cả** có định dạng UDID + tab + key. **Lưu danh sách**
   chỉ ghi file khi bạn chủ động chọn nơi lưu.

Token, mật khẩu PEM và key đã tạo không được tự lưu vào cấu hình tool.
Thư mục phát hành công khai không chứa PEM. Giữ khoá ký riêng trên máy cấp key.

Build: `py -3.11 -m PyInstaller --noconfirm tools/CTLIOSKeygen.spec`.
