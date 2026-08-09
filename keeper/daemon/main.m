// keeperd — daemon nền ĐỘC LẬP (không thuộc vòng đời app Keeper). App Keeper spawn
// nó ra (root, tách session) nên sống sót cả khi app bị vuốt tắt / ControlIOS cài
// đè. Nhiệm vụ: mở lại com.controlios.app khi tiến trình trollvncmanager chết.
//
// Dùng kqueue NOTE_EXIT: BLOCK chờ sự kiện tiến trình chết -> phản ứng TỨC THÌ và
// gần như 0% CPU (không poll định kỳ). Nhẹ và nhanh nhất cho máy iOS.
#import <Foundation/Foundation.h>
#import <arpa/inet.h>
#import <dlfcn.h>
#import <netinet/in.h>
#import <signal.h>
#import <sys/event.h>
#import <sys/param.h>
#import <sys/socket.h>
#import <sys/sysctl.h>
#import <sys/types.h>
#import <unistd.h>

static const int kSelfPort = 46753;                       // keeperd tự giữ (một-thể-hiện)
static NSString *const kTargetBundleID = @"com.controlios.app";
static const char *kWatchProc = "trollvncmanager";        // tiến trình giữ ControlIOS sống
static const Boolean kLaunchSuspended = false;            // false = foreground; true = thử mở nền

// Giữ cổng tự-nhận diện: bind được = thể hiện duy nhất; thất bại = đã có keeperd.
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

// Giết mọi tiến trình cùng tên KHÁC self (để keeperd mới thay keeperd cũ khi bạn
// cài đè bản Keeper mới — cũ là root, chạy root nên kill được).
static void tvKillOthers(const char *name, pid_t self) {
    int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0};
    size_t len = 0;
    if (sysctl(mib, 4, NULL, &len, NULL, 0) < 0 || len == 0)
        return;
    struct kinfo_proc *procs = (struct kinfo_proc *)malloc(len);
    if (!procs)
        return;
    if (sysctl(mib, 4, procs, &len, NULL, 0) == 0) {
        int n = (int)(len / sizeof(struct kinfo_proc));
        for (int i = 0; i < n; i++) {
            pid_t p = procs[i].kp_proc.p_pid;
            if (p != self && strncmp(procs[i].kp_proc.p_comm, name, MAXCOMLEN) == 0)
                kill(p, SIGKILL);
        }
    }
    free(procs);
}

// Tìm PID của tiến trình theo tên (p_comm bị cắt còn tối đa MAXCOMLEN ký tự).
static pid_t tvFindProc(const char *name) {
    int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0};
    size_t len = 0;
    if (sysctl(mib, 4, NULL, &len, NULL, 0) < 0 || len == 0)
        return -1;
    struct kinfo_proc *procs = (struct kinfo_proc *)malloc(len);
    if (!procs)
        return -1;
    if (sysctl(mib, 4, procs, &len, NULL, 0) < 0) {
        free(procs);
        return -1;
    }
    pid_t found = -1;
    int n = (int)(len / sizeof(struct kinfo_proc));
    for (int i = 0; i < n; i++) {
        if (strncmp(procs[i].kp_proc.p_comm, name, MAXCOMLEN) == 0) {
            found = procs[i].kp_proc.p_pid;
            break;
        }
    }
    free(procs);
    return found;
}

// Nhờ SpringBoard mở lại app đích.
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

// Chặn tới khi PID chết (kqueue NOTE_EXIT). Trả về khi tiến trình thoát; hoặc trả
// ngay nếu PID đã chết / không đăng ký được.
static void tvWaitForExit(pid_t pid) {
    int kq = kqueue();
    if (kq < 0)
        return;
    struct kevent ke;
    EV_SET(&ke, pid, EVFILT_PROC, EV_ADD | EV_ONESHOT, NOTE_EXIT, 0, NULL);
    if (kevent(kq, &ke, 1, NULL, 0, NULL) == -1) {
        close(kq); // PID đã chết trước khi kịp đăng ký
        return;
    }
    struct kevent out;
    kevent(kq, NULL, 0, &out, 1, NULL); // BLOCK, 0% CPU tới khi NOTE_EXIT
    close(kq);
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        // Thay thế keeperd cũ (nếu có): giết bản cũ rồi chiếm cổng -> cài đè Keeper
        // mới là chạy đúng logic mới, không kẹt bản cũ.
        tvKillOthers("keeperd", getpid());
        int self_fd = tvBindSelf(kSelfPort);
        for (int i = 0; i < 10 && self_fd < 0; i++) {
            usleep(300000); // chờ cổng nhả sau khi giết bản cũ
            self_fd = tvBindSelf(kSelfPort);
        }
        if (self_fd < 0) {
            NSLog(@"[keeperd] không chiếm được cổng — thoát");
            return 0;
        }
        NSLog(@"[keeperd] canh %s bằng kqueue", kWatchProc);
        for (;;) {
            pid_t pid = tvFindProc(kWatchProc);
            if (pid <= 0) {
                // ControlIOS chưa chạy. Lúc CÀI ĐÈ, TrollStore mất ~10-15s ghi bundle
                // mới, trong lúc đó mở app sẽ hụt -> THỬ MỞ LẠI mỗi 2s tới khi lên, để
                // bật đúng khoảnh khắc cài xong (không đợi hết một vòng poll dài).
                do {
                    tvLaunchTarget();
                    sleep(2);
                    pid = tvFindProc(kWatchProc);
                } while (pid <= 0);
            }
            NSLog(@"[keeperd] đang canh pid %d", pid);
            tvWaitForExit(pid);                 // ngủ tới khi ControlIOS chết (0% CPU)
            NSLog(@"[keeperd] ControlIOS chết -> mở lại ngay");
        }
    }
    return 0;
}
