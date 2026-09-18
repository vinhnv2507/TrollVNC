# Shopee Cookie Exporter cho Google Sheets

Công cụ này chạy dưới dạng **Apps Script gắn trực tiếp vào Google Sheet**. Cookie được
nhập bằng hộp thoại và lưu trong `UserProperties` của tài khoản Google đang chạy;
không ghi cookie vào ô, kết quả hoặc nhật ký API.

## Dữ liệu xuất

- Thông tin account nhận diện được từ cookie.
- Danh sách địa chỉ: tên người nhận, số điện thoại, địa chỉ đầy đủ, tọa độ.
- Bộ đếm trạng thái đơn hàng.
- Danh sách/chi tiết đơn nếu Shopee cho phép API.
- ePOD nếu có `order_id`.
- Timeline SPX: mã vận đơn, mã SLS, trạng thái, thời gian, trạm và địa chỉ trạm.
- Nhật ký HTTP/mã lỗi nhưng không ghi cookie hoặc token.

## Cài vào Google Sheet

1. Tạo một Google Sheet trống.
2. Chọn **Tiện ích mở rộng → Apps Script**.
3. Mở `Code.gs`, xóa nội dung mặc định và dán toàn bộ file `Code.gs` trong thư mục này.
4. Trong Apps Script, bật hiển thị file manifest nếu cần, rồi thay `appsscript.json`
   bằng nội dung file cùng tên trong thư mục này.
5. Lưu project và tải lại Google Sheet.
6. Chọn menu **Shopee Export → Tạo/cập nhật mẫu Sheet**.
7. Chọn **Shopee Export → Nhập/cập nhật cookie**, dán nội dung `cookie-header.txt`
   hoặc nguyên dòng `Cookie: ...`.
8. Chọn **Shopee Export → Lấy dữ liệu** và cấp quyền ở lần chạy đầu.

## Ô cấu hình

Trong tab `Cấu hình`:

- `B2`: `order_id` — không bắt buộc nhưng cần khi API danh sách đơn bị chặn.
- `B3`: mã vận đơn SPX — không bắt buộc; nhập để lấy timeline trực tiếp.
- `B4`: số đơn tối đa muốn thử lấy.

## Giới hạn thực tế của Shopee

Ngày 18/09/2026, thử nghiệm với cookie Shopee VN cho thấy:

- API địa chỉ và ePOD hoạt động.
- API đếm đơn hoạt động.
- API danh sách/chi tiết đơn có thể trả `403 / 90309999` do cơ chế chống bot.
- Vì vậy không thể cam kết **mọi tài khoản chỉ dán cookie là tự tìm toàn bộ đơn**.
  Khi bị `90309999`, nhập thêm `order_id` hoặc mã SPX ở tab `Cấu hình`.
- Google Apps Script chạy từ máy chủ Google, nên một số cookie có thể bị Shopee từ
  chối do thay đổi IP/thiết bị. Trường hợp đó nên chạy bản Python cục bộ trên chính PC.

## Bảo mật

Cookie `SPC_ST` tương đương phiên đăng nhập:

- Không gửi cookie cho người khác.
- Không chia sẻ quyền chỉnh sửa Apps Script khi cookie còn được lưu.
- Dùng xong chọn **Shopee Export → Xóa cookie đã lưu**.
- Nếu cookie từng bị lộ, đăng xuất mọi thiết bị hoặc đổi mật khẩu để thu hồi phiên.
