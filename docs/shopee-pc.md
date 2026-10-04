# Bảng Shopee trong ControlIOS PC

Mở **Shopee** trên thanh Thao tác. Cookie lấy từ app Shopee trên iPhone tự được thêm vào bảng; cookie Shopee đã lưu từ các bản cũ được nhập một lần khi mở bảng lần đầu. Danh sách được lưu trong dữ liệu của ControlIOS PC và giữ lại khi thay bản EXE, không cần Google Sheet.

- **Thêm cookie / Sửa**: đặt tên, cookie đầy đủ hoặc SPC_ST, proxy riêng của tài khoản.
- **Dán danh sách**: mỗi dòng một cookie; hoặc ba cột phân cách bằng TAB: tên, cookie, proxy. Cookie có cùng SPC_ST được cập nhật, không thêm trùng.
- **Gán proxy**: chọn các dòng rồi dán một proxy dùng chung, hoặc số proxy bằng số dòng đã chọn, theo thứ tự trên bảng.
- Proxy hỗ trợ `host:port`, `host:port:user:pass`, `http://user:pass@host:port`, `https://…`, `socks5://…`. SOCKS5 phân giải tên miền qua proxy.
- **Check đã chọn / Check tất cả**: kiểm tra tối đa ba tài khoản cùng lúc, mỗi tài khoản qua proxy đã gán. Thiếu hoặc lỗi proxy được báo trên dòng đó. Tool không chuyển sang kết nối trực tiếp.
- Chọn một dòng để xem **Đơn gần đây**, **Voucher hiện có**, **Kết quả / cảnh báo**. Chọn dòng đơn/voucher rồi Ctrl+C để copy. **Copy cookie** sao chép cookie đầy đủ của các tài khoản đã chọn.
- **Dừng** ngừng bắt đầu tài khoản mới và chờ yêu cầu đang chạy kết thúc. Đóng bảng vẫn cho kiểm tra đang chạy hoàn tất; đóng tool sẽ dừng kiểm tra trước khi thoát.

Danh sách đơn giới hạn 100 đơn gần đây và bổ sung chi tiết tối đa 10 đơn mỗi lượt. Khi API đơn bị chặn, tool thử lấy thông tin có trong 100 thông báo gần đây và ghi rõ kết quả chưa đầy đủ. Voucher lấy tối đa 20 trang, mỗi trang 50 mục. Đây là voucher đã có trong ví, không phải cam kết tài khoản đủ điều kiện áp dụng voucher cho mọi sản phẩm.

Nếu Shopee trả lỗi 90309999, yêu cầu xác minh, cookie hết phiên hoặc thay cấu trúc API, bảng hiển thị cảnh báo riêng cho đơn và voucher, giữ kết quả từng phần của lượt kiểm tra. Không coi lỗi API là tài khoản không có đơn/voucher. Không đặt hàng, nhận voucher hay tự vượt bước xác minh.

EXE lưu dữ liệu tại `%APPDATA%\ControlIOS PC\config\shopee_accounts.json`; cookie/proxy chỉ hiển thị đầy đủ khi sửa hoặc copy. Chạy từ source dùng `config/shopee_accounts.json` trong thư mục dự án.
