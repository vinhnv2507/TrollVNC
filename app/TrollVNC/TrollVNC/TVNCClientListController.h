/*
 This file is part of TrollVNC
 Copyright (c) 2025 82Flex <82flex@gmail.com> and contributors

 This program is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License version 2
 as published by the Free Software Foundation.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with this program. If not, see <https://www.gnu.org/licenses/>.
*/

#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

@interface TVNCClientListController : UITableViewController

@property(nonatomic, strong) NSBundle *bundle;
@property(nonatomic, strong) UIColor *primaryColor;
@property(nonatomic, strong) UINotificationFeedbackGenerator *notificationGenerator;

@end

/// Reset dữ liệu app ngay trên máy: liệt kê app đã cài (hỏi daemon qua control
/// socket loopback) rồi Snapshot / Wipe / Restore từng app. Mọi việc nặng do
/// daemon (root) làm; controller này chỉ gửi lệnh và hiện kết quả.
@interface TVNCAppDataController : UITableViewController

@property(nonatomic, strong) NSBundle *bundle;
@property(nonatomic, strong) UIColor *primaryColor;
@property(nonatomic, strong) UINotificationFeedbackGenerator *notificationGenerator;

@end

/// Kích hoạt bản quyền: hiện UDID máy (gửi cho người bán) + trạng thái, dán key
/// license vào để kích hoạt. App ghi file license rồi bảo daemon nạp lại.
@interface TVNCActivationController : UITableViewController

@property(nonatomic, strong) UIColor *primaryColor;

@end

/// Tự động chạm: soạn kịch bản (tap/swipe/wait/home…), gửi xuống daemon và
/// Bật/Tắt. Vòng lặp chạy TRONG DAEMON nên tiếp tục dù thoát app.
/// On-device health check for manager/server, VNC, control socket and Keeper.
@interface TVNCDiagnosticsController : UITableViewController

@property(nonatomic, strong) UIColor *primaryColor;

@end

@interface TVNCAutoClickController : UIViewController

@property(nonatomic, strong) UIColor *primaryColor;

@end

NS_ASSUME_NONNULL_END
