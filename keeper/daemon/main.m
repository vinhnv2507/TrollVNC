// keeperd — tiến trình nền ĐỘC LẬP (không thuộc vòng đời app Keeper). Được app
// Keeper spawn ra như một daemon (root, tách session) nên sống sót cả khi app bị
// vuốt tắt. Nhiệm vụ: canh cổng "còn sống" 46751 của ControlIOS; chết một lúc thì
// nhờ SpringBoard mở lại com.controlios.app. Giống cách trollvncmanager giữ server.
#import <Foundation/Foundation.h>
#import <arpa/inet.h>
#import <dlfcn.h>
#import <netinet/in.h>
#import <sys/socket.h>
#import <unistd.h>

static const int kAlivePort = 46751;              // ControlIOS còn sống
static const int kSelfPort = 46753;               // keeperd tự giữ (một-thể-hiện + để app dò)
static NSString *const kTargetBundleID = @"com.controlios.app";
static const int kSleepSeconds = 2;               // nhịp kiểm tra NHANH (~real-time)
static const int kFailsBeforeLaunch = 2;          // chết ~4s là mở lại
static const int kLaunchCooldown = 10;            // sau khi mở, chờ ControlIOS lên rồi mới soi tiếp
static const Boolean kLaunchSuspended = false;    // false = foreground; true = thử mở nền

static BOOL tvPortOpen(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return NO;
    struct timeval tv = {2, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int r = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    close(fd);
    return r == 0;
}

// Giữ cổng tự-nhận diện: bind thành công = mình là thể hiện duy nhất; thất bại =
// đã có keeperd khác chạy -> thoát.
static int tvBindSelf(int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        close(fd);
        return -1;
    }
    listen(fd, 4);
    return fd;
}

static int tvLaunchTarget(void) {
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (!h)
        return -100;
    int err = -1;
    int (*launchOpts)(CFStringRef, CFDictionaryRef, Boolean) =
        (int (*)(CFStringRef, CFDictionaryRef, Boolean))dlsym(
            h, "SBSLaunchApplicationWithIdentifierAndLaunchOptions");
    if (launchOpts)
        err = launchOpts((__bridge CFStringRef)kTargetBundleID, NULL, kLaunchSuspended);
    if (err != 0) {
        int (*launch)(CFStringRef, Boolean) =
            (int (*)(CFStringRef, Boolean))dlsym(h, "SBSLaunchApplicationWithIdentifier");
        if (launch)
            err = launch((__bridge CFStringRef)kTargetBundleID, kLaunchSuspended);
    }
    NSLog(@"[keeperd] launch %@ -> %d", kTargetBundleID, err);
    return err;
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        int self_fd = tvBindSelf(kSelfPort);
        if (self_fd < 0) {
            NSLog(@"[keeperd] đã có thể hiện khác — thoát");
            return 0;
        }
        NSLog(@"[keeperd] bắt đầu canh ControlIOS");
        int fails = 0;
        for (;;) {
            if (tvPortOpen(kAlivePort)) {
                fails = 0;
                sleep((unsigned)kSleepSeconds);
            } else if (++fails >= kFailsBeforeLaunch) {
                fails = 0;
                tvLaunchTarget();
                sleep((unsigned)kLaunchCooldown); // chờ ControlIOS khởi động xong
            } else {
                sleep((unsigned)kSleepSeconds);    // fail lần đầu -> soi lại nhanh
            }
        }
    }
    return 0;
}
