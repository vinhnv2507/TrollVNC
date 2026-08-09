#import "AppDelegate.h"
#import <AVFoundation/AVFoundation.h>
#import <dlfcn.h>
#import <arpa/inet.h>
#import <netinet/in.h>
#import <sys/socket.h>

// ------- Cấu hình -------
static const int kAlivePort = 46751;                       // cổng "còn sống" của ControlIOS
static NSString *const kTargetBundleID = @"com.controlios.app";
static const NSTimeInterval kCheckInterval = 20.0;         // kiểm tra mỗi 20s
static const int kFailsBeforeLaunch = 3;                   // chết ~60s mới mở lại
// Mở NỀN hay FOREGROUND. false = foreground (chắc chắn chạy, hiện app một nhịp);
// true = thử mở nền (không cướp app đang mở).
static const Boolean kLaunchSuspended = false;

@implementation AppDelegate {
    dispatch_source_t _timer;
    int _fails;
    UILabel *_status;
    AVAudioPlayer *_silence;   // phát im lặng để GIỮ APP SỐNG trong nền
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
    _status.font = [UIFont systemFontOfSize:16];
    _status.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    _status.text = @"ControlIOS Keeper\nĐang giữ ControlIOS sống…";
    [vc.view addSubview:_status];
    self.window.rootViewController = vc;
    [self.window makeKeyAndVisible];

    [self startSilentAudio];   // giữ app sống trong nền

    // Nghe gián đoạn âm thanh (cuộc gọi, app khác) để bật lại -> không rớt nền.
    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(audioInterrupted:)
                                                 name:AVAudioSessionInterruptionNotification
                                               object:nil];

    // GCD timer chạy trên hàng đợi nền.
    _timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
                                    dispatch_get_global_queue(QOS_CLASS_UTILITY, 0));
    dispatch_source_set_timer(_timer, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3 * NSEC_PER_SEC)),
                              (uint64_t)(kCheckInterval * NSEC_PER_SEC), (uint64_t)(5 * NSEC_PER_SEC));
    __weak typeof(self) weakSelf = self;
    dispatch_source_set_event_handler(_timer, ^{
        [weakSelf tick];
    });
    dispatch_resume(_timer);
    return YES;
}

// ---- Giữ app sống bằng âm thanh IM LẶNG (background mode = audio) ----
- (void)startSilentAudio {
    NSError *err = nil;
    AVAudioSession *sess = [AVAudioSession sharedInstance];
    // MixWithOthers: KHÔNG cắt nhạc/âm của app kiếm tiền đang chạy.
    [sess setCategory:AVAudioSessionCategoryPlayback
          withOptions:AVAudioSessionCategoryOptionMixWithOthers
                error:&err];
    [sess setActive:YES error:&err];

    NSString *path = [self silentWavPath];
    NSURL *url = [NSURL fileURLWithPath:path];
    _silence = [[AVAudioPlayer alloc] initWithContentsOfURL:url error:&err];
    _silence.numberOfLoops = -1;   // lặp vô hạn
    // Mẫu âm toàn số 0 nên vẫn im tuyệt đối; để volume 1.0 cho iOS coi là audio
    // ĐANG phát -> giữ app sống nền chắc hơn (volume 0 có thể bị coi là "không kêu").
    _silence.volume = 1.0;
    [_silence prepareToPlay];
    [_silence play];
}

- (void)audioInterrupted:(NSNotification *)note {
    NSNumber *type = note.userInfo[AVAudioSessionInterruptionTypeKey];
    if (type.unsignedIntegerValue == AVAudioSessionInterruptionTypeEnded) {
        // Hết gián đoạn -> bật lại để tiếp tục giữ nền.
        [[AVAudioSession sharedInstance] setActive:YES error:NULL];
        [_silence play];
    }
}

// Tạo file WAV im lặng (mono 8kHz 16-bit, ~0.5s) trong thư mục tạm.
- (NSString *)silentWavPath {
    NSString *path = [NSTemporaryDirectory() stringByAppendingPathComponent:@"keeper_silence.wav"];
    if ([[NSFileManager defaultManager] fileExistsAtPath:path])
        return path;

    int sampleRate = 8000, seconds = 1, channels = 1, bits = 16;
    int byteRate = sampleRate * channels * bits / 8;
    int dataSize = byteRate * seconds;
    int blockAlign = channels * bits / 8;
    int chunkSize = 36 + dataSize;

    NSMutableData *d = [NSMutableData data];
    void (^u32)(uint32_t) = ^(uint32_t v) { [d appendBytes:&v length:4]; };
    void (^u16)(uint16_t) = ^(uint16_t v) { [d appendBytes:&v length:2]; };
    [d appendBytes:"RIFF" length:4];
    u32((uint32_t)chunkSize);
    [d appendBytes:"WAVE" length:4];
    [d appendBytes:"fmt " length:4];
    u32(16);                       // subchunk1 size
    u16(1);                        // PCM
    u16((uint16_t)channels);
    u32((uint32_t)sampleRate);
    u32((uint32_t)byteRate);
    u16((uint16_t)blockAlign);
    u16((uint16_t)bits);
    [d appendBytes:"data" length:4];
    u32((uint32_t)dataSize);
    [d increaseLengthBy:dataSize]; // toàn số 0 = im lặng
    [d writeToFile:path atomically:YES];
    return path;
}

- (void)tick {
    BOOL alive = [self isAliveOnPort:kAlivePort];
    if (alive) {
        _fails = 0;
        [self setStatus:@"ControlIOS đang sống ✓"];
        return;
    }
    _fails++;
    [self setStatus:[NSString stringWithFormat:@"ControlIOS không phản hồi (%d/%d)…",
                                               _fails, kFailsBeforeLaunch]];
    if (_fails < kFailsBeforeLaunch)
        return;
    _fails = 0;
    int err = [self launchTarget];
    [self setStatus:[NSString stringWithFormat:@"Đã yêu cầu mở lại ControlIOS (mã %d)", err]];
}

- (void)setStatus:(NSString *)text {
    dispatch_async(dispatch_get_main_queue(), ^{
        self->_status.text = [NSString stringWithFormat:@"ControlIOS Keeper\n%@", text];
    });
}

// Thử kết nối cổng loopback: mở được = ControlIOS còn sống.
- (BOOL)isAliveOnPort:(int)port {
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

// Nhờ SpringBoard mở lại app đích. Cần entitlement launchapplications.
- (int)launchTarget {
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
    NSLog(@"[Keeper] launch %@ -> %d", kTargetBundleID, err);
    return err;
}

@end
