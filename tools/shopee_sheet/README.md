# Shopee Cookie Exporter cho Google Sheets

Công cụ chạy dưới dạng Apps Script gắn trực tiếp vào Google Sheet. Cookie được nhập bằng hộp thoại và lưu trong `UserProperties`; không ghi cookie vào ô hoặc nhật ký.

## Cách tự động tìm đơn chỉ bằng cookie

1. Gọi API thông báo đơn hàng (`action_cate=4`).
2. Giải mã nội dung hex trong thông báo.
3. Lấy Order ID từ URL chuyển hướng.
4. Lấy mã đơn Shopee và mã SPX từ nội dung thông báo.
5. Dùng Order ID lấy ePOD và dùng mã SPX lấy timeline vận chuyển.

Hướng này không phụ thuộc vào API danh sách đơn, vì API danh sách có thể bị Shopee chặn `90309999`.

## Dữ liệu xuất

- Account nhận diện được từ cookie.
- Tên người nhận, số điện thoại, địa chỉ và tọa độ.
- Bộ đếm trạng thái đơn.
- Order ID, mã đơn, trạng thái thông báo, mã vận đơn SPX và ePOD.
- Timeline SPX: trạng thái, thời gian, trạm và địa chỉ trạm.
- Nhật ký HTTP/mã lỗi, không chứa cookie/token.

## Cài vào Google Sheet

1. Tạo Google Sheet trống.
2. Chọn **Tiện ích mở rộng → Apps Script**.
3. Xóa code mặc định, dán toàn bộ `Code.gs`.
4. Thay manifest bằng `appsscript.json`, lưu và tải lại Sheet.
5. Chọn **Shopee Export → Tạo/cập nhật mẫu Sheet**.
6. Chọn **Nhập/cập nhật cookie**, dán `cookie-header.txt`.
7. Chọn **Lấy dữ liệu**.

## Cấu hình và giới hạn

- `B2`: số thông báo/đơn tối đa muốn thử lấy.
- Không cần nhập Order ID hay mã SPX.
- Chỉ những đơn còn thông báo trong lịch sử tài khoản mới tự tìm được theo hướng này. Thông báo quá cũ bị Shopee xóa có thể không xuất hiện.
- Thông báo chưa có mã SPX sẽ chưa lấy được timeline vận chuyển.

## Bảo mật

- Cookie `SPC_ST` tương đương phiên đăng nhập.
- Không chia sẻ cookie hoặc quyền sửa Apps Script.
- Dùng xong chọn **Shopee Export → Xóa cookie đã lưu**.
