# Bảng Shopee trong ControlIOS PC

Mở **Shopee** trên thanh Thao tác. Cookie lấy từ app Shopee trên iPhone tự được thêm vào bảng; cookie Shopee đã lưu từ các bản cũ được nhập một lần khi mở bảng lần đầu. Danh sách được lưu trong dữ liệu của ControlIOS PC và giữ lại khi thay bản EXE, không cần Google Sheet.

- **Thêm cookie / Sửa**: đặt tên, cookie đầy đủ hoặc SPC_ST, proxy riêng của tài khoản.
- **Dán danh sách**: mỗi dòng một cookie; hoặc ba cột phân cách bằng TAB: tên, cookie, proxy. Cookie có cùng SPC_ST được cập nhật, không thêm trùng.
- **Gán proxy**: chọn áp dụng cho toàn bảng hoặc các dòng đã chọn, luôn theo thứ tự từ trên xuống dưới. Dán mỗi dòng một proxy và chọn cách gán. Hộp thoại xem trước số cookie được gán và số cookie để trống trước khi áp dụng; proxy cũ trong phạm vi này được thay thế.
  - **Gán lần lượt; cookie dư để trống proxy**: 100 proxy cho 150 cookie sẽ gán 100 cookie đầu, xóa proxy của 50 cookie còn lại.
  - **Gán lần lượt và lặp lại danh sách proxy**: 100 proxy cho 150 cookie sẽ gán 100 cookie đầu rồi dùng lại 50 proxy đầu cho 50 cookie cuối.
  - **Dùng một proxy cho tất cả cookie**: nhập đúng một proxy để dùng chung cho phạm vi đã chọn.
- Proxy hỗ trợ `host:port`, `host:port:user:pass`, `http://user:pass@host:port`, `https://…`, `socks5://…`. SOCKS5 phân giải tên miền qua proxy.
- **Check đã chọn / Check tất cả**: lấy **Username**, kiểm tra đơn và voucher, tối đa ba tài khoản cùng lúc; mỗi tài khoản qua proxy đã gán. Username là tên tài khoản đang đăng nhập, tự lưu cùng kết quả và không thay tên/ghi chú tự đặt. Thiếu hoặc lỗi proxy được báo trên dòng đó. Tool không chuyển sang kết nối trực tiếp.
- Chọn một dòng để xem **Đơn gần đây**, **Voucher hiện có**, **Kết quả / cảnh báo**. Chọn dòng đơn/voucher rồi Ctrl+C để copy. **Copy cookie** sao chép cookie đầy đủ của các tài khoản đã chọn.
- Bấm ô **Voucher ▾** của một cookie để xổ danh sách voucher riêng của cookie đó. Danh sách cuộn được, hiển thị mã, tên, shop, mức giảm, giảm tối đa, đơn tối thiểu và hạn dùng; chọn voucher rồi Ctrl+C hoặc **Copy đã chọn**. Bấm ra ngoài hoặc Esc để đóng. Chưa check, không có voucher và API lỗi được hiển thị riêng.
- Cửa sổ Shopee có nút **thu nhỏ xuống thanh tác vụ**, **phóng to / khôi phục** và **đóng**. Nút Shopee trên tool mở lại cửa sổ đã ẩn/thu nhỏ; kết quả và việc kiểm tra đang chạy được giữ lại. Khi thoát tool, cửa sổ Shopee cũng đóng sau khi dừng kiểm tra.
- **Dừng** ngừng bắt đầu tài khoản mới và chờ yêu cầu đang chạy kết thúc. Đóng bảng vẫn cho kiểm tra đang chạy hoàn tất; đóng tool sẽ dừng kiểm tra trước khi thoát.

Danh sách đơn giới hạn 100 đơn gần đây và bổ sung chi tiết tối đa 10 đơn mỗi lượt. Khi API đơn bị chặn, tool thử lấy thông tin có trong 100 thông báo gần đây và ghi rõ kết quả chưa đầy đủ. Voucher lấy tối đa 20 trang, mỗi trang 50 mục. Đây là voucher đã có trong ví, không phải cam kết tài khoản đủ điều kiện áp dụng voucher cho mọi sản phẩm.

Nếu Shopee trả lỗi 90309999, yêu cầu xác minh, cookie hết phiên hoặc thay cấu trúc API, bảng hiển thị cảnh báo riêng cho username, đơn và voucher, giữ kết quả từng phần của lượt kiểm tra. Không coi lỗi API là tài khoản không có đơn/voucher. Đổi cookie hoặc proxy sẽ xóa kết quả cũ; bấm Check để lấy lại. Không đặt hàng, nhận voucher hay tự vượt bước xác minh.

EXE lưu dữ liệu tại `%APPDATA%\ControlIOS PC\config\shopee_accounts.json`; cookie/proxy chỉ hiển thị đầy đủ khi sửa hoặc copy. Chạy từ source dùng `config/shopee_accounts.json` trong thư mục dự án.
