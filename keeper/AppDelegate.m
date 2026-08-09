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
// lập với app, không chết khi app bị vuốt tắt. Dùng persona giống TrollVNC.
extern int posix_spawnattr_set_persona_np(posix_spawnattr_t *, uid_t, uint32_t);
extern int posix_spawnattr_set_persona_uid_np(posix_spawnattr_t *, uid_t);
extern int posix_spawnattr_set_persona_gid_np(posix_spawnattr_t *, uid_t);
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
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
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

    [self ensureKeeperd];

    // Cập nhật trạng thái mỗi 3s cho dễ theo dõi; và spawn lại nếu daemon chết.
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

// Nếu daemon chưa chạy (cổng 46753 đóng) thì spawn.
- (void)ensureKeeperd {
    if ([self portOpen:kSelfPort]) {
        _status.text = @"ControlIOS Keeper\n● Daemon đang chạy nền ✓\n(có thể tắt app này, daemon vẫn sống)";
        return;
    }
    int rc = [self spawnKeeperd];
    _status.text = [NSString stringWithFormat:@"ControlIOS Keeper\nĐã spawn daemon (mã %d)\n"
                                              @"Chờ vài giây để nó nhận cổng…", rc];
}

- (int)spawnKeeperd {
    NSString *path = [[NSBundle mainBundle] pathForResource:@"keeperd" ofType:@""];
    if (!path) {
        NSLog(@"[Keeper] thiếu keeperd trong bundle");
        return -1;
    }
    // Cho chắc có quyền chạy.
    chmod(path.fileSystemRepresentation, 0755);

    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    // Chạy như ROOT qua persona (cần entitlement com.apple.private.persona-mgmt).
    posix_spawnattr_set_persona_np(&attr, 99, PERSONA_FLAGS_OVERRIDE);
    posix_spawnattr_set_persona_uid_np(&attr, 0);
    posix_spawnattr_set_persona_gid_np(&attr, 0);
    // Tách session -> daemon sống độc lập khi app bị tắt.
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
