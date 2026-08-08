#import <UIKit/UIKit.h>

// Keeper: giữ cho app ControlIOS luôn sống. Cứ ít giây kiểm tra cổng "còn sống"
// (46751) của ControlIOS; nếu chết một lúc thì gọi SpringBoard mở lại app. Vì
// Keeper KHÔNG bị cài đè khi bạn update ControlIOS nên nó tự hồi phục ControlIOS
// sau mỗi lần cài đè, khỏi phải mở tay.
@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@end
