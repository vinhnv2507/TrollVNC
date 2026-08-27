# Quy trình bàn giao bản cập nhật

Với mọi yêu cầu cập nhật mã nguồn của người dùng, mặc định phải hoàn thành từ
đầu đến cuối; không dừng ở việc sửa source nếu vẫn có thể tiếp tục an toàn:

1. Giữ nguyên thay đổi không liên quan và chỉ commit đúng file thuộc yêu cầu.
2. Chạy test/build phù hợp, sửa lỗi phát sinh trong phạm vi cập nhật.
3. Đồng bộ tất cả vị trí khai báo version khi phát hành phiên bản mới.
4. Commit và push lên đúng nhánh GitHub của thành phần đã cập nhật.
5. Theo dõi CI đến khi thành công; nếu CI lỗi thì đọc log, sửa, push và chạy lại.
6. Tải artifact/release cuối cùng về `releases/v<version>/`, rồi kiểm tra version
   bên trong gói trước khi bàn giao đường dẫn tải cho người dùng.

Nguồn iOS nằm ở `.worktrees/ios-release-44`, phát hành từ nhánh `main`; workflow
`Build ControlIOS` tự build và tạo GitHub Release sau mỗi push lên nhánh này.
Không tự đóng bản Control IOS PC người dùng đang chạy chỉ để ghi đè build; nếu
file bị khóa, build sang thư mục kế bên và báo rõ đường dẫn.
