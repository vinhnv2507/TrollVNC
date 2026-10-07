# Khóa bảo vệ — thử nghiệm trên ControlIOS 4.28

1. Cập nhật ControlIOS trên iPhone lên 4.28. PC dùng nút **Khóa cảm ứng** hiện có.
2. Khi bật khóa, bộ lọc chặn cảm ứng, Home và nút nguồn vật lý. Lệnh điều khiển
   gửi từ ControlIOS PC vẫn được phép; phím âm lượng hoạt động như trước.
3. Bấm nguồn nhanh khi đang khóa: màn hình phải vẫn sáng. **Giữ nguồn liên tục
   3 giây**: khóa bảo vệ tắt, cảm ứng và Home hoạt động lại.
4. Thả nút sau khi thoát khóa. Lượt thả này được lọc để tránh tắt màn hình ngay;
   lần bấm nguồn tiếp theo hoạt động bình thường.
5. Nếu thả sớm, khóa vẫn bật. Đổi trạng thái khóa trên PC hủy bộ đếm cũ; phải
   thả rồi bấm giữ lại để bắt đầu một lượt thoát khóa mới.

Trong **Chẩn đoán kết nối**, mục **Khóa bảo vệ** hiển thị số sự kiện nguồn nhận
được, số sự kiện đã lọc và số lượt thoát khóa. Control PC có thể đọc cùng thông
tin qua lệnh `touchlock details`; `touchlock status` trả `OK on` hoặc `OK off`.
Số sự kiện lọc chỉ cho biết callback đã xử lý; cần bấm nút thật để kiểm chứng
màn hình không tắt và thao tác giữ mở khóa trên từng máy/iOS.

Đây là bộ lọc sự kiện trong iOS, không bảo đảm chặn tổ hợp cưỡng bức khởi động
lại của phần cứng. Nếu không nhận được nút nguồn hoặc thao tác giữ chưa hoạt
động, tắt **Khóa cảm ứng** từ PC và gửi lại `touchlock details` để kiểm tra.
