# Shopee Cookie Exporter cho Google Sheets

Công cụ chạy dưới dạng Apps Script gắn trực tiếp vào Google Sheet. Cookie được nhập bằng hộp thoại và lưu trong `UserProperties`; không ghi cookie vào ô hoặc nhật ký.

## Dữ liệu xuất

- Account nhận diện được từ cookie.
- Tên người nhận, số điện thoại, địa chỉ và tọa độ.
- Bộ đếm trạng thái đơn.
- Nếu API danh sách/chi tiết đơn hoạt động: tự tìm Order ID, ePOD và mã vận đơn có trong phản hồi để lấy timeline SPX.
- Nhật ký HTTP/mã lỗi, không chứa cookie/token.

## Cài vào Google Sheet

1. Tạo Google Sheet trống.
2. Chọn **Tiện ích mở rộng → Apps Script**.
3. Xóa code mặc định, dán toàn bộ `Code.gs`.
4. Thay manifest bằng `appsscript.json`, lưu và tải lại Sheet.
5. Chọn **Shopee Export → Tạo/cập nhật mẫu Sheet**.
6. Chọn **Nhập/cập nhật cookie**, dán `cookie-header.txt`.
7. Chọn **Lấy dữ liệu**.

## Cấu hình

- `B2`: số đơn tối đa muốn thử lấy.
- Không có ô nhập Order ID hay mã SPX. Công cụ sẽ tự tìm nếu API phản hồi đủ dữ liệu.

## Giới hạn

Shopee có thể trả `403 / 90309999` cho API danh sách/chi tiết đơn. Cookie không chứa Order ID hoặc mã vận đơn, nên khi API này bị chặn, Google Sheet không thể tự suy ra chỉ từ cookie. Khi đó công cụ chỉ xuất account, địa chỉ và bộ đếm lấy được.

Luồng tự động: danh sách đơn → Order ID → chi tiết/ePOD. Chỉ khi phản hồi chi tiết có mã vận đơn thì công cụ mới tiếp tục lấy SPX tracking; công cụ không OCR ảnh ePOD.

## Bảo mật

- Cookie `SPC_ST` tương đương phiên đăng nhập.
- Không chia sẻ cookie hoặc quyền sửa Apps Script.
- Dùng xong chọn **Shopee Export → Xóa cookie đã lưu**.
