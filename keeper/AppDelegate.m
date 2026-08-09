#import "AppDelegate.h"
#import <BackgroundTasks/BackgroundTasks.h>
#import <arpa/inet.h>
#import <crt_externs.h>
#import <netinet/in.h>
#import <spawn.h>
#import <sys/socket.h>
#import <sys/stat.h>
#import <unistd.h>

// keeperd tự giữ cổng này; app dò để biết daemon đã chạy chưa.
static const int kSelfPort = 46753;
// BackgroundTasks: iOS tự khởi động lại app này (sau boot, khi có cơ hội) để chạy
// task -> app launch -> main() spawn keeperd -> hồi phục mà KHÔNG cần mở tay.
static NSString *const kBGReviveID = @"com.controlios.keeper.revive";

// Spawn keeperd làm tiến trình ROOT, tách session (POSIX_SPAWN_SETSID) -> sống độc
// lập với app. Khai báo WEAK để nếu máy thiếu symbol thì app KHÔNG fail launch
// (chỉ báo lỗi trên màn hình thay vì trắng xoá).
extern int posix_spawnattr_set_persona_np(posix_spawnattr_t *, uid_t, uint32_t)
    __attribute__((weak_import));
extern int posix_spawnattr_set_persona_uid_np(posix_spawnattr_t *, uid_t)
    __attribute__((weak_import));
extern int posix_spawnattr_set_persona_gid_np(posix_spawnattr_t *, uid_t)
    __attribute__((weak_import));
#ifndef POSIX_SPAWN_SETSID
#define POSIX_SPAWN_SETSID 0x0400
#endif
#define PERSONA_FLAGS_OVERRIDE 1

@implementation AppDelegate {
    UILabel *_status;
    NSTimer *_uiTimer;
}

- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    // Dựng UI TRƯỚC, đảm bảo luôn hiển thị (không dính việc spawn).
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    self.window.backgroundColor = [UIColor darkGrayColor];
    UIViewController *vc = [UIViewController new];
    vc.view.backgroundColor = [UIColor darkGrayColor];
    _status = [[UILabel alloc] initWithFrame:vc.view.bounds];
    _status.numberOfLines = 0;
    _status.textAlignment = NSTextAlignmentCenter;
    _status.textColor = [UIColor yellowColor];  // vàng: thấy trên mọi nền
    _status.font = [UIFont boldSystemFontOfSize:16];
    _status.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    _status.text = @"ControlIOS Keeper\nĐang khởi động…";
    [vc.view addSubview:_status];
    self.window.rootViewController = vc;
    [self.window makeKeyAndVisible];

    // keeperd đã được spawn trong main() (trước UI). Ở đây chỉ theo dõi trạng thái;
    // nếu daemon chết thì timer sẽ spawn lại.
    _uiTimer = [NSTimer scheduledTimerWithTimeInterval:3.0
                                                target:self
                                              selector:@selector(ensureKeeperd)
                                              userInfo:nil
                                               repeats:YES];

    // Đăng ký BackgroundTasks: iOS sẽ tự bật lại app này sau boot/khi rảnh để chạy
    // task (chỉ cần launch là main() đã spawn keeperd). Best-effort, thời điểm do
    // iOS quyết (hay chạy khi đang sạc). Vẫn cần MỞ KHOÁ máy 1 lần sau cold-boot.
    if (@available(iOS 13.0, *)) {
        [[BGTaskScheduler sharedScheduler]
            registerForTaskWithIdentifier:kBGReviveID
                               usingQueue:nil
                            launchHandler:^(BGTask *task) {
                                [self scheduleRevive];       // đặt lại cho lần sau
                                [self ensureKeeperd];        // chắc chắn keeperd chạy
                                [task setTaskCompletedWithSuccess:YES];
                            }];
        [self scheduleRevive];
    }
    return YES;
}

- (void)scheduleRevive {
    if (@available(iOS 13.0, *)) {
        BGProcessingTaskRequest *req =
            [[BGProcessingTaskRequest alloc] initWithIdentifier:kBGReviveID];
        req.requiresNetworkConnectivity = NO;
        req.requiresExternalPower = NO;   // cho phép chạy cả khi không sạc
        req.earliestBeginDate = [NSDate dateWithTimeIntervalSinceNow:60];
        [[BGTaskScheduler sharedScheduler] submitTaskRequest:req error:NULL];
    }
}

- (void)applicationDidBecomeActive:(UIApplication *)application {
    [self ensureKeeperd];
}

- (void)setStatus:(NSString *)text {
    _status.text = [NSString stringWithFormat:@"ControlIOS Keeper\n%@", text];
}

// Theo dõi + spawn lại nếu daemon chết. Kèm CHẨN ĐOÁN để dễ báo lỗi.
- (void)ensureKeeperd {
    BOOL bundled = [[NSBundle mainBundle] pathForResource:@"keeperd" ofType:@""] != nil;
    BOOL running = [self portOpen:kSelfPort];
    if (!running)
        [self spawnKeeperd];
    [self setStatus:[NSString stringWithFormat:
                                @"keeperd trong bundle: %@\nDaemon đang chạy: %@\n"
                                @"%@",
                                bundled ? @"CÓ ✓" : @"KHÔNG ✗ (lỗi build)",
                                running ? @"● CÓ ✓" : @"chưa (đang bật…)",
                                running ? @"Có thể TẮT app này, daemon vẫn sống." : @""]];
}

- (int)spawnKeeperd {
    NSString *path = [[NSBundle mainBundle] pathForResource:@"keeperd" ofType:@""];
    if (!path)
        return -1;
    chmod(path.fileSystemRepresentation, 0755);

    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    // Chạy ROOT qua persona nếu có symbol; thiếu thì spawn thường (vẫn tách session).
    if (posix_spawnattr_set_persona_np && posix_spawnattr_set_persona_uid_np &&
        posix_spawnattr_set_persona_gid_np) {
        posix_spawnattr_set_persona_np(&attr, 99, PERSONA_FLAGS_OVERRIDE);
        posix_spawnattr_set_persona_uid_np(&attr, 0);
        posix_spawnattr_set_persona_gid_np(&attr, 0);
    }
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSID);

    const char *cpath = path.fileSystemRepresentation;
    char *const argv[] = {(char *)cpath, NULL};
    pid_t pid = 0;
    int rc = posix_spawn(&pid, cpath, NULL, &attr, argv, *_NSGetEnviron());
    posix_spawnattr_destroy(&attr);
    NSLog(@"[Keeper] posix_spawn keeperd rc=%d pid=%d", rc, pid);
    return rc;
}

- (BOOL)portOpen:(int)port {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return NO;
    struct timeval tv = {1, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int r = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    close(fd);
    return r == 0;
}

@end
