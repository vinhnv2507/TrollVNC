# Cập nhật ControlIOS từ PC qua LAN

Trong ControlIOS PC, chọn các máy Wi-Fi rồi mở **File… → Cập nhật ControlIOS qua LAN…**.
Gói iOS đi kèm trong thư mục `ios` được chọn sẵn. Có thể chọn một gói `.tipa`/`.ipa`
khác hoặc lấy bản có phiên bản cao nhất trong một thư mục local. PC nhớ thư mục này
và kiểm tra gói mới mỗi lần mở cửa sổ cập nhật. Không tìm hoặc tải gói từ GitHub.

Bấm **Cập nhật máy đã chọn**. PC phục vụ bản sao đã kiểm tra trên server HTTP tạm,
tối đa ba máy cập nhật cùng lúc. Máy có phiên bản bằng hoặc cao hơn gói được bỏ qua.
Gói phải đúng bundle `com.controlios.app`, đủ chương trình và vượt kiểm tra ZIP/CRC.
Thư mục chứa gói gốc có thể thay đổi; bản sao đang phục vụ không bị thay đổi theo.

ControlIOS iOS **4.25 trở lên** chuyển lệnh qua socket nội bộ đến manager có quyền
root. Manager chỉ nhận từ tiến trình `trollvncserver` của đúng bundle ControlIOS;
tiến trình nhận lệnh LAN và truyền màn hình vẫn chạy bằng mobile. Manager khởi
chạy bộ cập nhật độc lập, bộ này tạo bản sao ngoài bundle bằng root rồi tải
gói từ địa chỉ LAN số IPv4 của PC, không theo chuyển hướng, kiểm tra SHA256 rồi gọi
`trollstorehelper install custom` để cài đè. Không xóa dữ liệu app, token hoặc giấy
phép; không sửa tùy chọn xác nhận cài ứng dụng khác của TrollStore. Nó bật lại
manager và ControlIOS sau khi thay bundle, rồi PC kiểm tra phiên bản daemon mới và
trao đổi RFB ban đầu. Việc tải xong/gửi lệnh cài chưa được tính là thành công.

**Máy đang dùng bản cũ hoặc bộ cập nhật báo chưa sẵn sàng:** PC gửi URL gói local
cho TrollStore. PC 0.2.41 xử lý cả lỗi `LocalUpdateUnavailable` ở bản 4.18/4.19;
thông báo cũ “TrollStore/helper missing” có thể do thiếu quyền root dù TrollStore
vẫn được cài đầy đủ. Các bản thử nghiệm 4.20–4.24 có thể báo `updater root check
exit 10`; PC cũng chuyển sang TrollStore. Chọn gói 4.25 hoặc mới hơn để nâng cấp
sang cơ chế manager khởi chạy bộ cài bằng root.
Nếu TrollStore hỏi thì bấm Install qua CTRIOS; nếu không có Keeper tự bật lại app,
cần mở ControlIOS một lần sau khi cài. Đây là bước nâng cấp ban đầu để có tiến trình
cập nhật độc lập; những lần cập nhật tiếp theo dùng cơ chế tự động trên.

Giữ PC và mạng LAN hoạt động đến khi kết thúc. Có thể ẩn cửa sổ tiến trình; khi đang
cập nhật, đóng cửa sổ chính sẽ mở lại tiến trình và chờ cập nhật xong. Cổng HTTP tạm
có thể cần cho phép trong Windows Firewall. Máy chỉ kết nối qua USB cần dùng địa
chỉ Wi-Fi để cập nhật LAN. Gói mới cho những lần sau được chép vào thư mục local
đã chọn hoặc thư mục `ios` cạnh EXE.

Lỗi tải/hash/TrollStore được báo từng máy; hết thời gian hoặc chưa xác nhận được
dịch vụ mới cũng được báo lỗi, không báo cài thành công. Không tự gửi lại lệnh nếu
phản hồi ban đầu bị mất giữa lúc iPhone đang thay app.

PC 0.2.39 hiển thị bước xác nhận hiện tại và thời gian chờ còn lại. Nếu app đã cài
bản mới nhưng dịch vụ vẫn là bản cũ, bảng ghi rõ hai phiên bản và yêu cầu mở
ControlIOS trên iPhone. Nếu dịch vụ đã lên bản mới nhưng CTRIOS chưa phản hồi, bảng
ghi đang chờ CTRIOS. Các lần kiểm tra đều có giới hạn thời gian; lỗi token được báo
ngay. Máy đã có bản mới được kiểm tra CTRIOS và không gửi lại gói cài.
