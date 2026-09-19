# Shopee Cookie Checker — bảng 1 trang

## Cột

`Cookie | Mã Vận đơn | Trạng thái đơn | Người nhận | Số điện thoại nhận | Địa chỉ | Sản phẩm | Link sản phẩm | Voucher hiện có`

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

- **SPX**: gọi dữ liệu tra cứu công khai của SPX để lấy trạng thái mới nhất, thời gian cập nhật, người nhận, số điện thoại và địa chỉ nếu SPX trả về. Cột trạng thái hiển thị dạng `Đã giao hàng thành công | SPX | dd/MM/yyyy HH:mm`. Di chuột vào ô trạng thái để xem tối đa 5 mốc hành trình gần nhất.
- **GHN**: gọi endpoint tra cứu công khai của trang GHN. Một số mã sẽ yêu cầu xác minh số điện thoại (`PHONE_VERIFY_REQUIRED`); khi đó cột trạng thái vẫn giữ trạng thái Shopee và ghi kèm link GHN để mở kiểm tra.
- **Viettel Post**: trang tra cứu có cơ chế chống bot/captcha nên Apps Script không tự vượt qua. Cột trạng thái sẽ giữ trạng thái từ thông báo Shopee và ghi link Viettel Post để mở tra cứu thủ công.

Việc tra cứu nhà vận chuyển chỉ bổ sung **trạng thái vận chuyển**. Nó không mở khóa API chi tiết đơn Shopee. Nếu Shopee trả `90309999`, trạng thái cột C vẫn chỉ hiển thị trạng thái vận chuyển sạch (ví dụ `Delivered`); cảnh báo chi tiết đơn bị chặn được đặt trong ghi chú của ô trạng thái, không làm bẩn nội dung trạng thái.

## Sản phẩm và link sản phẩm

Công cụ thử lần lượt: chi tiết đơn Shopee, thẻ sản phẩm trong thông báo, rồi API sản phẩm công khai nếu tìm được `shop_id` và `item_id`. Khi lấy được hai ID, link được tạo theo dạng `https://shopee.vn/product/<shop_id>/<item_id>`.

Với cookie đã kiểm tra ngày 19/09/2026, Shopee trả `90309999` cho API danh sách/chi tiết đơn; thông báo giao hàng chỉ có mã đơn và mã vận đơn, không có `item_id`, `shop_id` của sản phẩm hay tên sản phẩm. Vì vậy riêng cookie này chưa thể lấy chính xác Sản phẩm/Link chỉ từ cookie. Dữ liệu cache trong bản sao lưu iOS có thể có tên mặt hàng, nhưng đó không phải dữ liệu cookie và Google Apps Script không truy cập được thư mục backup trên PC.

## Voucher hiện có

Mỗi lần kiểm tra cookie, Sheet gọi endpoint ví voucher của chính tài khoản với trạng thái đang còn hiệu lực. Cột **Voucher hiện có** chứa danh sách nhiều dòng trong cùng một ô; mỗi dòng gồm mã voucher, loại/nhãn, mức giảm, giá trị đơn tối thiểu, giới hạn giảm và thời hạn nếu Shopee trả các trường đó.

Danh sách được lấy theo cookie của từng dòng, không dùng chung giữa các tài khoản. Nếu Shopee trả mã lỗi hoặc chặn endpoint voucher, ô này sẽ hiển thị cảnh báo thay vì coi như tài khoản không có voucher.

## ??a ch? m?c ??nh

Sheet lu?n g?i danh s?ch ??a ch? c?a t?i kho?n qua cookie. ??a ch? ???c ??nh d?u `is_delivery_address` ho?c `is_default` s? ???c ?u ti?n; n?u kh?ng c? ??n/th?ng b?o ho?c chi ti?t ??n b? Shopee ch?n, t?n ng??i nh?n, s? ?i?n tho?i v? ??a ch? m?c ??nh v?n ???c ?i?n l?m d? li?u d? ph?ng.

## IP v? proxy

C?c d?ng cookie hi?n ???c ki?m tra b?ng `UrlFetchApp` c?a Google Apps Script. T?t c? request ?i qua h? t?ng v? nh?m IP c?a Google; m?i d?ng kh?ng t? c? m?t IP ri?ng. Apps Script kh?ng c? t?y ch?n proxy HTTP/SOCKS theo t?ng l?n g?i, v? v?y th?m m?t ? ch?a proxy d?ng `host:port:user:pass` s? kh?ng l?m request ??i IP.

Mu?n d?ng proxy th?t cho t?ng d?ng c?n m?t HTTPS relay do b?n ki?m so?t: Sheet g?i request t?i relay, relay d?ng proxy t??ng ?ng r?i chuy?n ph?n h?i v? Sheet. Kh?ng n?n g?i cookie qua relay c?ng c?ng. Khi c? ??a ch? relay v? format x?c th?c c? th?, c? th? n?i c?t Proxy v?o relay m? kh?ng ??a cookie v?o log.
