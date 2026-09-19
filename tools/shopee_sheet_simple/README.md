# Shopee Cookie Checker — bảng 1 trang

## Cột

`Cookie | Mã Vận Đơn | Trạng thái Đơn | Người nhận | Số điện thoại nhận | Địa chỉ | Sản phẩm | Link sản phẩm`

Mỗi dòng nhập **một cookie Shopee** vào cột A. Sau khi bật trigger, dán cookie vào cột A là dòng đó tự kiểm tra.

## Cài đặt một lần

1. Tạo Google Sheet trống → **Tiện ích mở rộng → Apps Script**.
2. Dán toàn bộ `Code.gs` vào Apps Script.
3. Mở `appsscript.json` trong Project Settings và dán manifest nếu cần.
4. Chạy `setupSimpleSheet` một lần, cấp quyền.
5. Quay lại Sheet, tải lại trang.
6. Chọn **Shopee → Bật tự động kiểm tra khi dán cookie**, cấp quyền lần đầu.
7. Dán mỗi cookie vào một dòng ở cột A.

## Cách lấy dữ liệu

- Tự đọc thông báo Shopee để tìm mã vận đơn SPX.
- Gọi SPX để lấy trạng thái mới nhất.
- Gọi danh sách địa chỉ để điền người nhận, số điện thoại và địa chỉ dự phòng.
- Gọi chi tiết đơn để lấy sản phẩm/link khi Shopee cho phép. Nếu Shopee trả `90309999`, các cột chi tiết sản phẩm có thể trống và trạng thái sẽ ghi rõ bị chặn.

Một cookie có thể có nhiều đơn; bảng một dòng/cookie lấy thông báo đơn mới nhất tìm thấy. Muốn xuất tất cả đơn thì dùng bản exporter đầy đủ.

## Bảo mật

Cookie có thể tương đương phiên đăng nhập. Chỉ dùng Sheet riêng, không chia sẻ quyền chỉnh sửa và xóa cookie khỏi cột A sau khi kiểm tra xong.

## Tra cứu theo đơn vị vận chuyển

Bản đơn giản nhận diện đơn vị vận chuyển từ thông báo Shopee và xử lý như sau:

- **SPX**: gọi dữ liệu tra cứu công khai của SPX để lấy trạng thái mới nhất, người nhận, số điện thoại và địa chỉ nếu SPX trả về.
- **GHN**: gọi endpoint tra cứu công khai của trang GHN. Một số mã sẽ yêu cầu xác minh số điện thoại (`PHONE_VERIFY_REQUIRED`); khi đó cột trạng thái vẫn giữ trạng thái Shopee và ghi kèm link GHN để mở kiểm tra.
- **Viettel Post**: trang tra cứu có cơ chế chống bot/captcha nên Apps Script không tự vượt qua. Cột trạng thái sẽ giữ trạng thái từ thông báo Shopee và ghi link Viettel Post để mở tra cứu thủ công.

Việc tra cứu nhà vận chuyển chỉ bổ sung **trạng thái vận chuyển**. Nó không mở khóa API chi tiết đơn Shopee. Vì vậy `Chi tiết Shopee bị chặn 90309999` vẫn có thể xuất hiện; đây là giới hạn riêng của API chi tiết đơn, không phải lỗi mã vận đơn.