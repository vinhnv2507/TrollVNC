#import "AppDelegate.h"
#import <arpa/inet.h>
#import <crt_externs.h>
#import <netinet/in.h>
#import <spawn.h>
#import <sys/socket.h>
#import <sys/stat.h>
#import <unistd.h>

// keeperd tự giữ cổng này; app dò để biết daemon đã chạy chưa.
static const int kSelfPort = 46753;

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
    self.window.backgroundColor = [UIColor blackColor];
    UIViewController *vc = [UIViewController new];
    vc.view.backgroundColor = [UIColor blackColor];
    _status = [[UILabel alloc] initWithFrame:vc.view.bounds];
    _status.numberOfLines = 0;
    _status.textAlignment = NSTextAlignmentCenter;
    _status.textColor = [UIColor whiteColor];
    _status.font = [UIFont systemFontOfSize:15];
    _status.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    _status.text = @"ControlIOS Keeper\nĐang khởi động daemon…";
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
    return YES;
}

- (void)applicationDidBecomeActive:(UIApplication *)application {
    [self ensureKeeperd];
}

- (void)setStatus:(NSString *)text {
    _status.text = [NSString stringWithFormat:@"ControlIOS Keeper\n%@", text];
}

// Nếu daemon chưa chạy (cổng 46753 đóng) thì spawn.
- (void)ensureKeeperd {
    if ([self portOpen:kSelfPort]) {
        [self setStatus:@"● Daemon đang chạy nền ✓\n(có thể tắt app này, daemon vẫn sống)"];
        return;
    }
    int rc = [self spawnKeeperd];
    if (rc == 0)
        [self setStatus:@"Đã bật daemon — chờ vài giây…"];
    else
        [self setStatus:[NSString stringWithFormat:@"Chưa bật được daemon (mã %d)\nthử lại…", rc]];
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
