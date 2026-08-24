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

#if !__has_feature(objc_arc)
#warning This file must be compiled with ARC. Use -fobjc-arc flag.
#endif

#import <Accelerate/Accelerate.h>
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <JavaScriptCore/JavaScriptCore.h>   // engine kịch bản auto-click (JS)
#import <UIKit/UIKit.h>                      // UIImage cho findImage (template matching)
#import <Vision/Vision.h>                    // OCR (nhận chữ trên màn)
#import <Security/Security.h>   // xác thực chữ ký license (ECDSA P-256)

#import <arpa/inet.h>
#import <atomic>
#import <climits>
#import <cstdio>
#import <cstdlib>
#import <cstring>
#import <dlfcn.h>
#import <errno.h>
#import <fcntl.h>
#import <ifaddrs.h>
#import <mach-o/dyld.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <notify.h>
#import <pthread.h>
#import <rfb/keysym.h>
#import <rfb/rfb.h>
#import <signal.h>
#import <string>
#import <sys/socket.h>
#import <sys/stat.h>
#import <time.h>
#import <sys/sysctl.h>
#import <unistd.h>
#import <vector>

#import <Photos/Photos.h>

#import "BulletinManager.h"
#import "ClipboardManager.h"
#import "Control.h"
#import "FBSOrientationObserver.h"
#import "IOKitSPI.h"
#import "Logging.h"
#import "PSAssistiveTouchSettingsDetail.h"
#import "STHIDEventGenerator.h"
#import "ScreenCapturer.h"

@interface NSObject (TVBKSApplicationStateMonitor)
- (void)setHandler:(void (^)(NSDictionary *appInfo))handler;
@end

#define LocalizedString(key, comment, bundle, table)                                                                   \
    (NSLocalizedStringFromTableInBundle((key), (table), (bundle), (comment)) ?: (key))

#define TVPrintError(fmt, ...)                                                                                         \
    do {                                                                                                               \
        fprintf(stderr, fmt "\r\n", ##__VA_ARGS__);                                                                    \
    } while (0)

#pragma mark - Options

int gOrientationFixQuad = 0; // 0=0°, 1=90°CW, 2=180°, 3=270°CW

static BOOL gEnabled = YES;
static int gPort = 5901;
static int gTvCtlPort = 0;        // port for control connections (0 = disabled)
static NSString *gTvCtlToken = nil; // token bắt buộc cho kết nối không phải loopback
static BOOL gTvCtlBindAll = NO;     // YES khi có token: nghe trên mọi giao diện mạng

// Kích hoạt bản quyền: daemon chỉ phục vụ khi có license hợp lệ (ký số, buộc
// UDID, còn hạn). Xem #pragma mark - License.
static BOOL gLicenseValid = NO;
static NSString *gLicenseToken = nil;  // "khoá có ích": token control lấy từ license
static long long gLicenseExpiry = 0;   // epoch giây, 0 = vĩnh viễn

// Giữ các assertion sống suốt vòng đời daemon. Chúng chỉ chặn idle
// timeout; thao tác khóa thủ công bằng nút Power vẫn có hiệu lực.
static uint32_t gDisplayIdleAssertion = 0;
static uint32_t gSystemIdleAssertion = 0;

static void tvPreventAutomaticLock(void) {
    // Lớp UIKit là đường chuẩn trên iOS. Bọc try để daemon vẫn
    // chạy trên bootstrap không tạo UIApplication.
    @try {
        [UIApplication sharedApplication].idleTimerDisabled = YES;
    } @catch (NSException *exception) {
        TVLog(@"Prevent auto-lock: UIKit idle timer unavailable: %@", exception.reason);
    }

    // Assertion IOKit giữ hiệu lực kể cả khi UI app không ở foreground.
    typedef int (*CreateAssertionFn)(CFStringRef, uint32_t, CFStringRef, uint32_t *);
    CreateAssertionFn createAssertion = (CreateAssertionFn)dlsym(
        RTLD_DEFAULT, "IOPMAssertionCreateWithName");
    if (!createAssertion) {
        void *iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_LAZY);
        if (iokit)
            createAssertion = (CreateAssertionFn)dlsym(iokit, "IOPMAssertionCreateWithName");
    }
    if (!createAssertion) {
        TVLog(@"Prevent auto-lock: IOPMAssertionCreateWithName unavailable");
        return;
    }

    const uint32_t on = 255; // kIOPMAssertionLevelOn
    int displayResult = createAssertion(CFSTR("PreventUserIdleDisplaySleep"), on,
                                        CFSTR("ControlIOS keep display awake"),
                                        &gDisplayIdleAssertion);
    int systemResult = createAssertion(CFSTR("PreventUserIdleSystemSleep"), on,
                                       CFSTR("ControlIOS prevent automatic lock"),
                                       &gSystemIdleAssertion);
    TVLog(@"Prevent auto-lock enabled: display=%d(id=%u), system=%d(id=%u)",
          displayResult, gDisplayIdleAssertion, systemResult, gSystemIdleAssertion);
}

// SPI để liệt kê/mở app. Dùng NSClassFromString khi gọi nên không phải link
// thêm framework nào, và cũng không phải sửa Makefile.
@interface LSApplicationProxy : NSObject
@property(nonatomic, readonly) NSString *applicationIdentifier;
@property(nonatomic, readonly) NSString *localizedName;
@property(nonatomic, readonly) NSString *applicationType;
@property(nonatomic, readonly) NSString *shortVersionString;
@property(nonatomic, readonly) NSURL *bundleURL;
@property(nonatomic, readonly) NSURL *dataContainerURL;
@end
@interface LSApplicationWorkspace : NSObject
+ (instancetype)defaultWorkspace;
- (NSArray<LSApplicationProxy *> *)allApplications;
- (BOOL)openApplicationWithBundleID:(NSString *)bundleID;
- (BOOL)openSensitiveURL:(NSURL *)url withOptions:(NSDictionary *)options;
@end
static NSString *gBindHost = nil; // optional bind address from CLI/config
static NSString *gDesktopName = @"ControlIOS";
static BOOL gViewOnly = NO;
static double gKeepAliveSec = 0.0; // 15..86400
static BOOL gClipboardEnabled = YES;
static BOOL gIsDaemonMode = NO; // set when launched with -daemon

static double gScale = 0.35; // default; prefs/PC setscale may override at runtime
// Preferred frame rate range (0 = unspecified)
static int gFpsMin = 0;
static int gFpsPref = 0;
static int gFpsMax = 0;
static double gDeferWindowSec = 0.015;      // Coalescing window; 0 disables deferral
static int gMaxInflightUpdates = 2;         // Max concurrent client encodes; drop frames if >= this
static int gTileSize = 32;                  // Tile size for dirty detection (pixels)
static int gFullscreenThresholdPercent = 0; // If changed tiles exceed this %, update full screen
static int gMaxRectsLimit = 256;            // Max rects before falling back to bbox/fullscreen
static BOOL gAsyncSwapEnabled = NO;         // Enable non-blocking swap (may cause tearing)

// Wheel scroll coalescing state (async, non-blocking)
static double gWheelStepPx = 48.0;        // base pixels per wheel tick (lower = slower)
static double gWheelMaxStepPx = 192.0;    // base max distance per flush (pre-clamp)
static double gWheelCoalesceSec = 0.03;   // coalescing window
static double gWheelAbsClampFactor = 2.5; // absolute clamp = factor * gWheelMaxStepPx
static double gWheelAmpCoeff = 0.18;      // velocity amplification coefficient
static double gWheelAmpCap = 0.75;        // max extra amplification (0..1)
static double gWheelMinTakeRatio = 0.35;  // minimum take distance vs step size
static double gWheelDurBase = 0.05;       // duration base seconds
static double gWheelDurK = 0.00016;       // duration factor applied to sqrt(distance)
static double gWheelDurMin = 0.05;        // duration clamp min
static double gWheelDurMax = 0.14;        // duration clamp max
static BOOL gWheelNaturalDir = NO;        // natural scroll direction (invert delta)

// Modifier mapping scheme: 0 = standard (Alt->Option, Meta/Super->Command), 1 = Alt-as-Command
static int gModMapScheme = 0;
static BOOL gAutoAssistEnabled = NO;
static BOOL gCursorEnabled = NO;
static BOOL gKeyEventLogging = NO;
static BOOL gOrientationSyncEnabled = YES;

// Classic VNC authentication
static char **gAuthPasswdVec = NULL;        // owns the vector
static char *gAuthPasswdStr = NULL;         // owns the duplicated password string
static char *gAuthViewOnlyPasswdStr = NULL; // optional view-only password string

// HTTP server (LibVNCServer built-in web client)
static int gHttpPort = 0;
static char *gHttpDirOverride = NULL;
static char *gSslCertPath = NULL;
static char *gSslKeyPath = NULL;

// Bonjour / mDNS Auto-Discovery
static BOOL gBonjourEnabled = YES; // publish _rfb._tcp (and optional _http._tcp)

// TightVNC 1.x file transfer extension (deprecated)
static BOOL gFileTransferEnabled = NO;

// UltraVNC repeater
static int gRepeaterMode = 0; // 0: disabled, 1: viewer, 2: repeater
static char *gRepeaterHost = NULL;
static int gRepeaterPort = 5500;
static int gRepeaterId = 12345679;

// User notifications
static BOOL gUserClientNotifsEnabled = YES;
static BOOL gUserSingleNotifsEnabled = YES;

// Blocked hosts (temporary blacklist)
static NSMutableSet<NSString *> *gBlockedHosts = nil;

typedef NS_ENUM(uint8_t, TVBindHostKind) {
    kTVBindHostKindNone = 0,
    kTVBindHostKindIPv4,
    kTVBindHostKindIPv6,
    kTVBindHostKindInvalid,
};

static TVBindHostKind tvClassifyBindHost(NSString *host, in_addr_t *outIPv4, struct in6_addr *outIPv6) {
    if (outIPv4)
        *outIPv4 = 0;
    if (outIPv6)
        memset(outIPv6, 0, sizeof(*outIPv6));

    if (!host || host.length == 0)
        return kTVBindHostKindNone;

    const char *cstr = [host UTF8String];
    if (!cstr || *cstr == '\0')
        return kTVBindHostKindNone;

    struct in_addr v4;
    if (inet_pton(AF_INET, cstr, &v4) == 1) {
        if (outIPv4)
            *outIPv4 = v4.s_addr;
        return kTVBindHostKindIPv4;
    }

    char addrBuf[INET6_ADDRSTRLEN + 1];
    const char *pct = strchr(cstr, '%');
    size_t copyLen = pct ? (size_t)(pct - cstr) : strlen(cstr);
    if (copyLen >= sizeof(addrBuf))
        copyLen = sizeof(addrBuf) - 1;
    memcpy(addrBuf, cstr, copyLen);
    addrBuf[copyLen] = '\0';

    struct in6_addr v6;
    if (inet_pton(AF_INET6, addrBuf, &v6) == 1) {
        if (outIPv6)
            *outIPv6 = v6;
        return kTVBindHostKindIPv6;
    }

    return kTVBindHostKindInvalid;
}

NS_INLINE BOOL isRepeaterEnabled(void) {
    return gRepeaterMode > 0 && gRepeaterHost != NULL && gRepeaterHost[0] != '\0' && gRepeaterPort > 0;
}

#pragma mark - Bundle

static NSString *tvExecutablePath(void) {
    static NSString *sPath = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        // Resolve executable path
        uint32_t sz = 0;
        _NSGetExecutablePath(NULL, &sz); // query size
        char *exeBuf = (char *)malloc(sz > 0 ? sz : PATH_MAX);
        if (!exeBuf)
            return;
        if (_NSGetExecutablePath(exeBuf, &sz) != 0) {
            // Fallback: leave exeBuf as-is
        }

        // Canonicalize
        char realBuf[PATH_MAX];
        const char *exePath = realpath(exeBuf, realBuf) ? realBuf : exeBuf;
        NSString *exe = [NSString stringWithUTF8String:exePath ? exePath : ""];
        free(exeBuf);

        sPath = exe ?: [[NSProcessInfo processInfo] arguments][0];
    });
    return sPath;
}

static NSBundle *tvResourceBundle(void) {
    static NSBundle *sBundle = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
#ifdef THEBOOTSTRAP
        NSString *exe = tvExecutablePath();
        NSString *dir = [exe stringByDeletingLastPathComponent];
        NSBundle *mainBundle = [NSBundle bundleWithPath:dir];
        if (!mainBundle)
            return;

        NSString *resPath = [mainBundle pathForResource:@"TrollVNCPrefs" ofType:@"bundle"];
        NSBundle *resBundle = resPath ? [NSBundle bundleWithPath:resPath] : nil;
        if (!resBundle)
            return;

        sBundle = resBundle;
#else
        NSString *exe = tvExecutablePath();
        NSString *exeDir = [exe stringByDeletingLastPathComponent];
        NSString *resRel = @"../../Library/PreferenceBundles/TrollVNCPrefs.bundle";
        NSString *resPath = [[exeDir stringByAppendingPathComponent:resRel] stringByStandardizingPath];
        NSBundle *resBundle = resPath ? [NSBundle bundleWithPath:resPath] : nil;
        if (!resBundle)
            return;

        sBundle = resBundle;
#endif
    });
    return sBundle;
}

static NSBundle *tvLocalizationBundle(void) {
    static NSBundle *sBundle = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        NSBundle *resBundle = tvResourceBundle();

        NSArray<NSString *> *languages =
            [[NSUserDefaults standardUserDefaults] objectForKey:@"AppleLanguages"] ?: @"en";

        NSString *localizablePath = nil;
        for (NSString *localization in [NSBundle preferredLocalizationsFromArray:[resBundle localizations]
                                                                  forPreferences:languages]) {
            localizablePath = [resBundle pathForResource:@"Localizable"
                                                  ofType:@"strings"
                                             inDirectory:nil
                                         forLocalization:localization];
            if (localizablePath && localizablePath.length > 0)
                break;
        }

        NSString *lprojPath = [localizablePath stringByDeletingLastPathComponent];
        if (lprojPath && lprojPath.length > 0) {
            resBundle = [NSBundle bundleWithPath:lprojPath];
        }

        sBundle = resBundle;
    });
    return sBundle;
}

#pragma mark - Command-Line Parsing

/* clangd behavior workarounds */
#define STRINGIFY(x) #x
#define EXPAND_AND_STRINGIFY(x) STRINGIFY(x)
#define MYSTRINGIFY(x)                                                                                                 \
    ^{                                                                                                                 \
        NSString *str = [NSString stringWithUTF8String:EXPAND_AND_STRINGIFY(x)];                                       \
        if ([str hasPrefix:@"\""])                                                                                     \
            str = [str substringFromIndex:1];                                                                          \
        if ([str hasSuffix:@"\""])                                                                                     \
            str = [str substringToIndex:str.length - 1];                                                               \
        return strdup([str UTF8String]);                                                                               \
    }()

static void printUsageAndExit(const char *prog) {
    // Compact, grouped usage for quick reference. See README for detailed explanations.
    static const char *sPackageScheme = MYSTRINGIFY(THEOS_PACKAGE_SCHEME);
    static const char *sPackageVersion = MYSTRINGIFY(PACKAGE_VERSION);

    fprintf(stderr, "ControlIOS (%s) v%s\n", sPackageScheme, sPackageVersion);
    fprintf(stderr, "Usage: %s [-p port] [-n name] [options]\n\n", prog);

    fprintf(stderr, "Basic:\n");
    fprintf(stderr, "  -b host    Bind host address (IPv4/IPv6 literal, default to all)\n");
    fprintf(stderr, "  -p port    VNC TCP port (default: %d)\n", gPort);
    fprintf(stderr, "  -c port    Client management TCP port (0=off, default: 0)\n");
    fprintf(stderr, "  -n name    Desktop name (default: %s)\n", [gDesktopName UTF8String]);
    fprintf(stderr, "  -v         View-only (ignore input)\n");
    fprintf(stderr, "  -A sec     Keep-alive interval to prevent sleep; only when clients > 0 (15..86400, 0=off)\n\n");

    fprintf(stderr, "Display/Perf:\n");
    fprintf(stderr, "  -s scale   Output scale 0<s<=1 (default: %.2f)\n", gScale);
    fprintf(stderr, "  -F spec    Frame rate: fps | min-max | min:pref:max\n");
    fprintf(stderr, "  -d sec     Defer window (0..0.5, default: %.3f)\n", gDeferWindowSec);
    fprintf(stderr, "  -Q n       Max in-flight encodes (0=never drop, default: %d)\n\n", gMaxInflightUpdates);

    fprintf(stderr, "Dirty detection:\n");
    fprintf(stderr, "  -t size    Tile size (8..128, default: %d)\n", gTileSize);
    fprintf(stderr, "  -P pct     Fullscreen fallback threshold (0..100; 0=disable dirty detection, default: %d)\n",
            gFullscreenThresholdPercent);
    fprintf(stderr, "  -R max     Max dirty rects before bbox (default: %d)\n", gMaxRectsLimit);
    fprintf(stderr, "  -a         Non-blocking swap (may cause tearing)\n\n");

    fprintf(stderr, "Scroll/Input:\n");
    fprintf(stderr, "  -W px      Wheel step in pixels (0=disable, default: %.0f)\n", gWheelStepPx);
    fprintf(stderr,
            "  -w k=v,.. Wheel tuning keys: step,coalesce,max,clamp,amp,cap,minratio,durbase,durk,durmin,durmax\n");
    fprintf(stderr, "  -N         Natural scroll direction (invert wheel)\n");
    fprintf(stderr, "  -M scheme  Modifier mapping: std|altcmd (default: std)\n");
    fprintf(stderr, "  -K         Log keyboard events to stderr\n\n");

    fprintf(stderr, "HTTP/WebSockets:\n");
    fprintf(stderr, "  -H port    Enable built-in HTTP server on port (0=off, default: 0)\n");
    fprintf(stderr, "  -D path    Absolute path for HTTP document root\n");
    fprintf(stderr, "  -e file    Path to SSL certificate file\n");
    fprintf(stderr, "  -k file    Path to SSL private key file\n\n");

    fprintf(stderr, "Bonjour/mDNS:\n");
    fprintf(stderr, "  -B on|off  Advertise on local network via Bonjour (_rfb._tcp, _http._tcp) (default: on)\n\n");

    fprintf(stderr, "Accessibility:\n");
    fprintf(stderr, "  -O on|off  Observe iOS interface orientation and sync (default: on)\n");
    fprintf(stderr, "  -E on|off  Enable AssistiveTouch auto-activation (default: off)\n");
    fprintf(stderr, "  -U on|off  Enable server-side cursor X (default: off)\n\n");

    fprintf(stderr, "Notifications:\n");
    fprintf(stderr, "  -i on|off  Single notification when first client connects (default: on)\n");
    fprintf(stderr, "  -I on|off  User notifications for client connect/disconnect (default: on)\n\n");

    fprintf(stderr, "Extensions:\n");
    fprintf(stderr, "  -C on|off  Clipboard sync (default: on)\n");
    fprintf(stderr, "  -T on|off  File transfer (default: off)\n\n");

#if DEBUG
    fprintf(stderr, "Logging:\n");
    fprintf(stderr, "  -V         Enable verbose logging\n\n");
#endif

    fprintf(stderr, "Help:\n");
    fprintf(stderr, "  -h         Show this help message\n\n");

    fprintf(stderr, "Reverse Connection:\n");
    fprintf(stderr, "  %s -reverse host:port [options]\n", prog);
    fprintf(stderr, "  %s -repeater id host:port [options]\n\n", prog);

    fprintf(stderr, "Environment:\n");
    fprintf(
        stderr,
        "  TROLLVNC_PASSWORD                 Classic VNC password (enables VNC auth when set; first 8 chars used)\n");
    fprintf(stderr,
            "  TROLLVNC_VIEWONLY_PASSWORD        View-only password; passwords stored as [full..., view-only...]\n");
    fprintf(stderr, "  TROLLVNC_REPEATER_RETRY_INTERVAL  Repeater retry interval (default: 0)\n\n");

    exit(EXIT_SUCCESS);
}

static void parseWheelOptions(const char *spec) {
    if (!spec)
        return;
    char *dup = strdup(spec);
    if (!dup)
        return;
    char *saveptr = NULL;
    for (char *tok = strtok_r(dup, ",", &saveptr); tok; tok = strtok_r(NULL, ",", &saveptr)) {
        char *eq = strchr(tok, '=');
        if (!eq)
            continue;
        *eq = '\0';
        const char *key = tok;
        const char *val = eq + 1;
        double d = strtod(val, NULL);
        if (strcmp(key, "step") == 0) {
            if (d > 0)
                gWheelStepPx = d;
            TVLog(@"Wheel tuning: step=%g", gWheelStepPx);
        } else if (strcmp(key, "coalesce") == 0) {
            if (d >= 0 && d <= 0.5)
                gWheelCoalesceSec = d;
            TVLog(@"Wheel tuning: coalesce=%g", gWheelCoalesceSec);
        } else if (strcmp(key, "max") == 0) {
            if (d > 0)
                gWheelMaxStepPx = d;
            TVLog(@"Wheel tuning: max=%g", gWheelMaxStepPx);
        } else if (strcmp(key, "clamp") == 0) {
            if (d >= 1.0 && d <= 10.0)
                gWheelAbsClampFactor = d;
            TVLog(@"Wheel tuning: clamp=%g", gWheelAbsClampFactor);
        } else if (strcmp(key, "amp") == 0) {
            if (d >= 0.0 && d <= 5.0)
                gWheelAmpCoeff = d;
            TVLog(@"Wheel tuning: amp=%g", gWheelAmpCoeff);
        } else if (strcmp(key, "cap") == 0) {
            if (d >= 0.0 && d <= 2.0)
                gWheelAmpCap = d;
            TVLog(@"Wheel tuning: cap=%g", gWheelAmpCap);
        } else if (strcmp(key, "minratio") == 0) {
            if (d >= 0.0 && d <= 2.0)
                gWheelMinTakeRatio = d;
            TVLog(@"Wheel tuning: minratio=%g", gWheelMinTakeRatio);
        } else if (strcmp(key, "durbase") == 0) {
            if (d >= 0.0 && d <= 1.0)
                gWheelDurBase = d;
            TVLog(@"Wheel tuning: durbase=%g", gWheelDurBase);
        } else if (strcmp(key, "durk") == 0) {
            if (d >= 0.0 && d <= 1.0)
                gWheelDurK = d;
            TVLog(@"Wheel tuning: durk=%g", gWheelDurK);
        } else if (strcmp(key, "durmin") == 0) {
            if (d >= 0.0 && d <= 1.0)
                gWheelDurMin = d;
            TVLog(@"Wheel tuning: durmin=%g", gWheelDurMin);
        } else if (strcmp(key, "durmax") == 0) {
            if (d >= 0.0 && d <= 2.0)
                gWheelDurMax = d;
            TVLog(@"Wheel tuning: durmax=%g", gWheelDurMax);
        } else if (strcmp(key, "natural") == 0) {
            gWheelNaturalDir = (d != 0.0);
            TVLog(@"Wheel tuning: natural=%@", gWheelNaturalDir ? @"YES" : @"NO");
        }
    }
    free(dup);
}

static void parseDaemonOptions(void) {
    NSDictionary *prefs = nil;

    if (!prefs) {
        NSBundle *resBundle = tvResourceBundle();
        NSString *presetPath = [resBundle pathForResource:@"Managed" ofType:@"plist"];
        if (presetPath) {
            prefs = [NSDictionary dictionaryWithContentsOfFile:presetPath];
        }
    }

    if (!prefs) {
        prefs = [[NSUserDefaults standardUserDefaults] persistentDomainForName:@"com.82flex.trollvnc"];
    }

#if TARGET_IPHONE_SIMULATOR
    if (!prefs) {
        const char *sandboxPath = getenv("TROLLVNC_SANDBOX_PATH");
        if (sandboxPath && sandboxPath[0] != '\0') {
            NSString *sandbox = [NSString stringWithUTF8String:sandboxPath];
            NSString *plistPath =
                [sandbox stringByAppendingPathComponent:@"Library/Preferences/com.82flex.trollvnc.plist"];
            prefs = [NSDictionary dictionaryWithContentsOfFile:plistPath];
            if (prefs) {
                TVLog(@"-daemon: loaded simulator preferences from %@", plistPath);
            }
        }
    }
#endif

    if (!prefs) {
        TVLog(@"-daemon: no preferences found for domain com.82flex.trollvnc");
        return;
    }

    // Strings
    NSString *desktopName = [prefs objectForKey:@"DesktopName"];
    if ([desktopName isKindOfClass:[NSString class]] && desktopName.length > 0) {
        gDesktopName = desktopName;
    } else if (desktopName) {
        TVLog(@"-daemon: DesktopName is empty; using default '%@'", gDesktopName);
    }

    NSString *bindHost = [prefs objectForKey:@"BindHost"];
    if ([bindHost isKindOfClass:[NSString class]]) {
        if (bindHost.length > 0) {
            gBindHost = bindHost;
            TVLog(@"-daemon: BindHost=%@", gBindHost);
        } else {
            gBindHost = nil;
            TVLog(@"-daemon: BindHost empty; cleared to default (any)");
        }
    }

    // Numbers
    NSNumber *portN = [prefs objectForKey:@"Port"];
    if ([portN isKindOfClass:[NSNumber class]] || [portN isKindOfClass:[NSString class]]) {
        int v = portN.intValue;
        if (v < 1024 || v > 65535) {
            // Privileged or invalid -> fallback to default 5901
            TVLog(@"-daemon: invalid TCP Port=%d; using default 5901", v);
            gPort = 5901;
        } else {
            gPort = v;
        }
    }

    NSNumber *keepAliveN = [prefs objectForKey:@"KeepAliveSec"];
    if ([keepAliveN isKindOfClass:[NSNumber class]]) {
        double v = keepAliveN.doubleValue;
        if (v < 0.0) {
            TVLog(@"-daemon: KeepAliveSec < 0; set to 0");
            v = 0.0;
        } else if (v > 0.0 && v < 15.0) {
            TVLog(@"-daemon: KeepAliveSec < 15; treated as 0 (off)");
            v = 0.0;
        } else if (v > 300.0) {
            TVLog(@"-daemon: KeepAliveSec > 300; clamped to 300");
            v = 300.0;
        }
        gKeepAliveSec = v;
    }

    NSNumber *scaleN = [prefs objectForKey:@"Scale"];
    if ([scaleN isKindOfClass:[NSNumber class]]) {
        double v = scaleN.doubleValue;
        if (v <= 0.0 || v > 1.0) {
            TVLog(@"-daemon: invalid Scale=%.3f; clamped to [0.1..1.0]", v);
        }
        if (v < 0.1)
            v = 0.1;
        if (v > 1.0)
            v = 1.0;
        gScale = v;
    }

    NSNumber *deferN = [prefs objectForKey:@"DeferWindowSec"];
    if ([deferN isKindOfClass:[NSNumber class]]) {
        double v = deferN.doubleValue;
        if (v < 0.0) {
            TVLog(@"-daemon: DeferWindowSec < 0; set to 0");
            v = 0.0;
        }
        if (v > 0.5) {
            TVLog(@"-daemon: DeferWindowSec > 0.5; clamped to 0.5");
            v = 0.5;
        }
        gDeferWindowSec = v;
    }

    NSNumber *maxInflightN = [prefs objectForKey:@"MaxInflight"];
    if ([maxInflightN isKindOfClass:[NSNumber class]]) {
        int v = maxInflightN.intValue;
        if (v < 0) {
            TVLog(@"-daemon: MaxInflight < 0; set to 0");
            v = 0;
        }
        if (v > 8) {
            TVLog(@"-daemon: MaxInflight > 8; clamped to 8");
            v = 8;
        }
        gMaxInflightUpdates = v;
    }

    NSNumber *tileSizeN = [prefs objectForKey:@"TileSize"];
    if ([tileSizeN isKindOfClass:[NSNumber class]]) {
        int v = tileSizeN.intValue;
        if (v < 8) {
            TVLog(@"-daemon: TileSize < 8; set to 8");
            v = 8;
        }
        if (v > 128) {
            TVLog(@"-daemon: TileSize > 128; clamped to 128");
            v = 128;
        }
        gTileSize = v;
    }

    NSNumber *fullThreshN = [prefs objectForKey:@"FullscreenThresholdPercent"];
    if ([fullThreshN isKindOfClass:[NSNumber class]]) {
        int v = fullThreshN.intValue;
        if (v < 0) {
            TVLog(@"-daemon: FullscreenThresholdPercent < 0; set to 0");
            v = 0;
        }
        if (v > 100) {
            TVLog(@"-daemon: FullscreenThresholdPercent > 100; clamped to 100");
            v = 100;
        }
        gFullscreenThresholdPercent = v;
    }

    NSNumber *maxRectsN = [prefs objectForKey:@"MaxRects"];
    if ([maxRectsN isKindOfClass:[NSNumber class]]) {
        int v = maxRectsN.intValue;
        if (v < 1) {
            TVLog(@"-daemon: MaxRects < 1; set to 1");
            v = 1;
        }
        if (v > 4096) {
            TVLog(@"-daemon: MaxRects > 4096; clamped to 4096");
            v = 4096;
        }
        gMaxRectsLimit = v;
    }

    NSNumber *wheelPxN = [prefs objectForKey:@"WheelStepPx"];
    if ([wheelPxN isKindOfClass:[NSNumber class]]) {
        double v = wheelPxN.doubleValue;
        if (v == 0.0) {
            gWheelStepPx = 0.0;
            gWheelMaxStepPx = 0.0;
            TVLog(@"-daemon: Wheel emulation disabled (step=0)");
        } else {
            if (v <= 4.0) {
                TVLog(@"-daemon: WheelStepPx <= 4; raised to 5");
                v = 5.0;
            }
            if (v > 1000.0) {
                TVLog(@"-daemon: WheelStepPx > 1000; clamped to 1000");
                v = 1000.0;
            }
            gWheelStepPx = v;
            gWheelMaxStepPx = fmax(2.0 * gWheelStepPx, 96.0) * 1.0;
        }
    }

    NSNumber *httpPortN = [prefs objectForKey:@"HttpPort"];
    if ([httpPortN isKindOfClass:[NSNumber class]] || [httpPortN isKindOfClass:[NSString class]]) {
        int v = httpPortN.intValue;
        if (v == 0) {
            gHttpPort = 0; // disabled
        } else if (v < 0 || v > 65535 || v < 1024) {
            TVLog(@"-daemon: invalid HTTP Port=%d; using default 0 (disabled)", v);
            gHttpPort = 0;
        } else {
            gHttpPort = v;
        }
    }

    // Booleans
    NSNumber *enableN = [prefs objectForKey:@"Enabled"];
    if ([enableN isKindOfClass:[NSNumber class]])
        gEnabled = enableN.boolValue;
    NSNumber *clipN = [prefs objectForKey:@"ClipboardEnabled"];
    if ([clipN isKindOfClass:[NSNumber class]])
        gClipboardEnabled = clipN.boolValue;
    NSNumber *viewOnlyN = [prefs objectForKey:@"ViewOnly"];
    if ([viewOnlyN isKindOfClass:[NSNumber class]])
        gViewOnly = viewOnlyN.boolValue;
    NSNumber *orientN = [prefs objectForKey:@"OrientationSync"];
    if ([orientN isKindOfClass:[NSNumber class]])
        gOrientationSyncEnabled = orientN.boolValue;
    NSNumber *orientFixN = [prefs objectForKey:@"OrientationPadFix"];
    if ([orientFixN isKindOfClass:[NSNumber class]]) {
        int v = orientFixN.intValue;
        gOrientationFixQuad = (v >= 0 && v <= 3) ? v : 0;
    }
    NSNumber *naturalN = [prefs objectForKey:@"NaturalScroll"];
    if ([naturalN isKindOfClass:[NSNumber class]])
        gWheelNaturalDir = naturalN.boolValue;
    NSNumber *cursorN = [prefs objectForKey:@"ServerCursor"];
    if ([cursorN isKindOfClass:[NSNumber class]])
        gCursorEnabled = cursorN.boolValue;
    NSNumber *asyncSwapN = [prefs objectForKey:@"AsyncSwap"];
    if ([asyncSwapN isKindOfClass:[NSNumber class]])
        gAsyncSwapEnabled = asyncSwapN.boolValue;
    NSNumber *keyLogN = [prefs objectForKey:@"KeyLogging"];
    if ([keyLogN isKindOfClass:[NSNumber class]])
        gKeyEventLogging = keyLogN.boolValue;
    NSNumber *assistN = [prefs objectForKey:@"AutoAssistEnabled"];
    if ([assistN isKindOfClass:[NSNumber class]])
        gAutoAssistEnabled = assistN.boolValue;
    NSNumber *bonjourN = [prefs objectForKey:@"BonjourEnabled"];
    if ([bonjourN isKindOfClass:[NSNumber class]])
        gBonjourEnabled = bonjourN.boolValue;
    NSNumber *fileN = [prefs objectForKey:@"FileTransferEnabled"];
    if ([fileN isKindOfClass:[NSNumber class]])
        gFileTransferEnabled = fileN.boolValue;
    NSNumber *singleNotifN = [prefs objectForKey:@"SingleNotifEnabled"];
    if ([singleNotifN isKindOfClass:[NSNumber class]])
        gUserSingleNotifsEnabled = singleNotifN.boolValue;
    NSNumber *clientNotifsN = [prefs objectForKey:@"ClientNotifsEnabled"];
    if ([clientNotifsN isKindOfClass:[NSNumber class]])
        gUserClientNotifsEnabled = clientNotifsN.boolValue;

    // Modifier mapping
    NSString *modMap = [prefs objectForKey:@"ModifierMap"];
    if ([modMap isKindOfClass:[NSString class]]) {
        if ([modMap isEqualToString:@"altcmd"])
            gModMapScheme = 1;
        else
            gModMapScheme = 0;
    }

    // Frame rate spec (validate and normalize)
    NSString *fpsSpec = [prefs objectForKey:@"FrameRateSpec"];
    if ([fpsSpec isKindOfClass:[NSString class]] && fpsSpec.length > 0) {
        const char *spec = fpsSpec.UTF8String ?: "";
        int minV = 0, prefV = 0, maxV = 0;
        const char *colon1 = strchr(spec, ':');
        const char *dash = strchr(spec, '-');
        if (colon1) {
            long a = strtol(spec, NULL, 10);
            const char *p2 = colon1 + 1;
            const char *colon2 = strchr(p2, ':');
            if (colon2) {
                long b = strtol(p2, NULL, 10);
                long c = strtol(colon2 + 1, NULL, 10);
                minV = (int)a;
                prefV = (int)b;
                maxV = (int)c;
            }
        } else if (dash) {
            long a = strtol(spec, NULL, 10);
            long b = strtol(dash + 1, NULL, 10);
            minV = (int)a;
            prefV = (int)b;
            maxV = (int)b;
        } else {
            long v = strtol(spec, NULL, 10);
            minV = (int)v;
            prefV = (int)v;
            maxV = (int)v;
        }
        // Normalize & validate: allow 0..240 (0 = unspecified)
        if (minV < 0)
            minV = 0;
        if (minV > 240)
            minV = 240;
        if (prefV < 0)
            prefV = 0;
        if (prefV > 240)
            prefV = 240;
        if (maxV < 0)
            maxV = 0;
        if (maxV > 240)
            maxV = 240;
        if (minV > 0 && maxV > 0 && minV > maxV) {
            int tmp = minV;
            minV = maxV;
            maxV = tmp;
        }
        if (prefV > 0) {
            if (minV > 0 && prefV < minV)
                prefV = minV;
            if (maxV > 0 && prefV > maxV)
                prefV = maxV;
        }
        gFpsMin = minV;
        gFpsPref = prefV;
        gFpsMax = maxV;
    }

    // Wheel tuning (advanced)
    NSString *wheelTuning = [prefs objectForKey:@"WheelTuning"];
    if ([wheelTuning isKindOfClass:[NSString class]] && wheelTuning.length > 0) {
        parseWheelOptions(wheelTuning.UTF8String);
    }

    // HTTP dir override and SSL (require absolute paths)
    NSString *httpDir = [prefs objectForKey:@"HttpDir"];
    if ([httpDir isKindOfClass:[NSString class]] && httpDir.length > 0) {
        if (![httpDir hasPrefix:@"/"]) {
            TVLog(@"-daemon: HttpDir must be absolute: %@ (ignored)", httpDir);
        } else {
            gHttpDirOverride = strdup(httpDir.fileSystemRepresentation);
        }
    }
    NSString *sslCert = [prefs objectForKey:@"SslCertFile"];
    if ([sslCert isKindOfClass:[NSString class]] && sslCert.length > 0) {
        if (![sslCert hasPrefix:@"/"]) {
            TVLog(@"-daemon: SslCertFile must be absolute: %@ (ignored)", sslCert);
        } else {
            gSslCertPath = strdup(sslCert.fileSystemRepresentation);
        }
    }
    NSString *sslKey = [prefs objectForKey:@"SslKeyFile"];
    if ([sslKey isKindOfClass:[NSString class]] && sslKey.length > 0) {
        if (![sslKey hasPrefix:@"/"]) {
            TVLog(@"-daemon: SslKeyFile must be absolute: %@ (ignored)", sslKey);
        } else {
            gSslKeyPath = strdup(sslKey.fileSystemRepresentation);
        }
    }

    // Reverse Connection (26.1) from preferences
    // Expected keys (per Root.plist):
    //  - ReverseMode: "viewer" (default) | "repeater"
    //  - ReverseSocket: "host:port" or "[ipv6]:port"
    //  - ReverseRepeaterID: number id (only used when mode=repeater)
    NSString *revMode = [prefs objectForKey:@"ReverseMode"];
    if ([revMode isKindOfClass:[NSString class]]) {
        if ([revMode caseInsensitiveCompare:@"repeater"] == NSOrderedSame) {
            gRepeaterMode = 2;
        } else if ([revMode caseInsensitiveCompare:@"viewer"] == NSOrderedSame) {
            gRepeaterMode = 1;
        } else {
            gRepeaterMode = 0;
        }
    }
    NSString *revSock = [prefs objectForKey:@"ReverseSocket"];
    if ([revSock isKindOfClass:[NSString class]] && revSock.length > 0) {
        const char *hp = revSock.UTF8String;
        const char *hostBegin = hp;
        const char *hostEnd = NULL;
        const char *portStr = NULL;
        if (hp[0] == '[') {
            const char *rb = strchr(hp, ']');
            if (rb && rb[1] == ':') {
                hostBegin = hp + 1;
                hostEnd = rb;
                portStr = rb + 2;
            }
        } else {
            const char *colon = strrchr(hp, ':');
            if (colon && colon != hp && *(colon + 1) != '\0') {
                hostBegin = hp;
                hostEnd = colon;
                portStr = colon + 1;
            }
        }
        if (hostEnd && portStr) {
            long pv = strtol(portStr, NULL, 10);
            if (pv > 0 && pv <= 65535) {
                size_t hostLen = (size_t)(hostEnd - hostBegin);
                if (hostLen > 0) {
                    char *hostDup = (char *)malloc(hostLen + 1);
                    if (hostDup) {
                        memcpy(hostDup, hostBegin, hostLen);
                        hostDup[hostLen] = '\0';
                        if (gRepeaterHost) {
                            free(gRepeaterHost);
                            gRepeaterHost = NULL;
                        }
                        gRepeaterHost = hostDup;
                        gRepeaterPort = (int)pv;
                    }
                }
            } else {
                TVLog(@"-daemon: ReverseSocket port invalid: %ld (ignored)", pv);
            }
        } else {
            TVLog(@"-daemon: ReverseSocket invalid: %@ (expected host:port or [ipv6]:port)", revSock);
        }
    } else {
        // Backward-compat: accept separate ReverseHost/ReversePort if present
        NSString *revHost = [prefs objectForKey:@"ReverseHost"];
        if ([revHost isKindOfClass:[NSString class]] && revHost.length > 0) {
            gRepeaterHost = strdup(revHost.UTF8String);
        }
        NSNumber *revPortN = [prefs objectForKey:@"ReversePort"];
        if ([revPortN isKindOfClass:[NSNumber class]] || [revPortN isKindOfClass:[NSString class]]) {
            int v = revPortN.intValue;
            if (v > 0 && v <= 65535) {
                gRepeaterPort = v;
            }
        }
    }
    NSNumber *revIdN = [prefs objectForKey:@"ReverseRepeaterID"];
    if ([revIdN isKindOfClass:[NSNumber class]] || [revIdN isKindOfClass:[NSString class]]) {
        gRepeaterId = revIdN.intValue;
    }

    // If reverse connection is configured, override mutually exclusive options here in daemon mode
    if (isRepeaterEnabled()) {
        gPort = -1;    // disable local listening
        gHttpPort = 0; // disable HTTP server
        if (gHttpDirOverride) {
            free(gHttpDirOverride);
            gHttpDirOverride = NULL;
        }
        gBonjourEnabled = NO; // disable Bonjour advertisement
        TVLog(@"-daemon: Reverse enabled -> overriding: port=-1, http=0, bonjour=off");
    }

    // Passwords via environment (leveraging existing setupRfbClassicAuthentication).
    // Classic VNC authentication uses only first 8 chars; truncate here for clarity.
    NSString *fullPwd = [prefs objectForKey:@"FullPassword"];
    BOOL hasFullPwd = NO, hasViewPwd = NO;
    if ([fullPwd isKindOfClass:[NSString class]]) {
        NSString *trunc = (fullPwd.length > 8) ? [fullPwd substringToIndex:8] : fullPwd;
        setenv("TROLLVNC_PASSWORD", trunc.UTF8String ?: "", 1);
        hasFullPwd = (trunc.length > 0);
    }
    NSString *viewPwd = [prefs objectForKey:@"ViewOnlyPassword"];
    if ([viewPwd isKindOfClass:[NSString class]]) {
        NSString *trunc = (viewPwd.length > 8) ? [viewPwd substringToIndex:8] : viewPwd;
        setenv("TROLLVNC_VIEWONLY_PASSWORD", trunc.UTF8String ?: "", 1);
        hasViewPwd = (trunc.length > 0);
    }
    // Token cho control socket. Có token thì mới mở ra LAN; không có thì giữ
    // nguyên hành vi gốc là chỉ nghe 127.0.0.1.
    NSString *ctlToken = [prefs objectForKey:@"CtlToken"];
    if ([ctlToken isKindOfClass:[NSString class]] && ctlToken.length > 0) {
        gTvCtlToken = [ctlToken copy];
        gTvCtlBindAll = YES;
        TVLog(@"-daemon: control token set, control socket will listen on all interfaces");
    }
    // Single-line summary using NSMutableString; include reverse-connection fields and new options
    NSMutableString *cfg = [NSMutableString stringWithFormat:@"-daemon: cfg "];
    [cfg appendFormat:@"name='%@' ", gDesktopName];
    [cfg appendFormat:@"bindHost='%@' ", gBindHost];
    [cfg appendFormat:@"port=%d http=%d ", gPort, gHttpPort];

    // Reverse connection summary
    const char *revModeStr = isRepeaterEnabled() ? (gRepeaterMode == 2 ? "repeater" : "viewer") : "off";
    NSString *revHostStr = gRepeaterHost ? [NSString stringWithUTF8String:gRepeaterHost] : nil;
    [cfg appendFormat:@"reverse=%s host=%@ port=%d id=%d ", revModeStr, revHostStr, gRepeaterPort, gRepeaterId];

    // Core feature flags
    [cfg appendFormat:@"viewOnly=%@ clip=%@ keepAlive=%.0fs ", gViewOnly ? @"YES" : @"NO",
                      gClipboardEnabled ? @"YES" : @"NO", gKeepAliveSec];
    [cfg appendFormat:@"scale=%.2f fps=%d:%d:%d defer=%.3f ", gScale, gFpsMin, gFpsPref, gFpsMax, gDeferWindowSec];
    [cfg appendFormat:@"inflight=%d tile=%d full%%=%d rects=%d ", gMaxInflightUpdates, gTileSize,
                      gFullscreenThresholdPercent, gMaxRectsLimit];
    [cfg appendFormat:@"async=%@ cursor=%@ orient=%@ orientFix=%d keylog=%@ ", gAsyncSwapEnabled ? @"YES" : @"NO",
                      gCursorEnabled ? @"YES" : @"NO", gOrientationSyncEnabled ? @"YES" : @"NO",
                      gOrientationFixQuad, gKeyEventLogging ? @"YES" : @"NO"];

    // Wheel / input tuning
    [cfg appendFormat:@"wheel=%.1f natural=%@ mod=%s ", gWheelStepPx, gWheelNaturalDir ? @"YES" : @"NO",
                      (gModMapScheme == 1) ? "altcmd" : "std"];

    // Networking / discovery
    [cfg appendFormat:@"bonjour=%@ ", gBonjourEnabled ? @"on" : @"off"];
    [cfg appendFormat:@"fileXfer=%@ ", gFileTransferEnabled ? @"on" : @"off"];

    // Auth and paths
    [cfg appendFormat:@"auth(full=%@,view=%@,8char) ", hasFullPwd ? @"on" : @"off", hasViewPwd ? @"on" : @"off"];
    NSString *dirStr = gHttpDirOverride ? [NSString stringWithUTF8String:gHttpDirOverride] : nil;
    NSString *certStr = gSslCertPath ? [NSString stringWithUTF8String:gSslCertPath] : nil;
    NSString *keyStr = gSslKeyPath ? [NSString stringWithUTF8String:gSslKeyPath] : nil;
    [cfg appendFormat:@"dir=%@ cert=%@ key=%@", dirStr, certStr, keyStr];

    TVLog(@"%@", cfg);
    TVLog(@"-daemon: preferences applied (domain=com.82flex.trollvnc)");
}

static void parseCLI(int argc, const char *argv[]) {
    // Special mode: -daemon reads configuration from NSUserDefaults domain
    // com.82flex.trollvnc and initializes runtime options accordingly.
    BOOL isDaemon = NO;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-daemon") == 0) {
            isDaemon = YES;
            break;
        }
    }
    if (isDaemon) {
        gIsDaemonMode = YES;
        gTvCtlPort = kTvDefaultCtlPort;
        parseDaemonOptions();
        return;
    }

    // Pre-scan for Reverse Connection long options (-reverse, -repeater)
    // Build a filtered argv without these options for getopt handling of the rest.
    std::vector<const char *> __filtered;
    __filtered.reserve((size_t)argc);
    __filtered.push_back(argv[0]);

    BOOL __reverseEnabled = NO;
    for (int i = 1; i < argc; ++i) {
        const char *arg = argv[i];
        if (strcmp(arg, "-reverse") == 0) {
            if (i + 1 >= argc) {
                TVPrintError("-reverse requires host:port");
                exit(EXIT_FAILURE);
            }

            const char *hp = argv[++i];
            const char *hostBegin = hp;
            const char *hostEnd = NULL;
            const char *portStr = NULL;

            if (hp[0] == '[') {
                const char *rb = strchr(hp, ']');
                if (!rb || rb[1] != ':') {
                    TVPrintError("Invalid -reverse target: %s (expected [host]:port)", hp);
                    exit(EXIT_FAILURE);
                }

                hostBegin = hp + 1;
                hostEnd = rb;
                portStr = rb + 2;
            } else {
                const char *colon = strrchr(hp, ':');
                if (!colon || colon == hp || *(colon + 1) == '\0') {
                    TVPrintError("Invalid -reverse target: %s (expected host:port)", hp);
                    exit(EXIT_FAILURE);
                }

                hostBegin = hp;
                hostEnd = colon;
                portStr = colon + 1;
            }

            int port = (int)strtol(portStr, NULL, 10);
            if (port <= 0 || port > 65535) {
                TVPrintError("Invalid -reverse port: %s", portStr);
                exit(EXIT_FAILURE);
            }

            size_t hostLen = (size_t)(hostEnd - hostBegin);
            if (hostLen == 0) {
                TVPrintError("Invalid -reverse host (empty)");
                exit(EXIT_FAILURE);
            }

            char *hostDup = (char *)malloc(hostLen + 1);
            if (!hostDup) {
                TVPrintError("Out of memory");
                exit(EXIT_FAILURE);
            }

            memcpy(hostDup, hostBegin, hostLen);
            hostDup[hostLen] = '\0';

            if (gRepeaterHost) {
                free(gRepeaterHost);
                gRepeaterHost = NULL;
            }

            gRepeaterMode = 1;
            gRepeaterHost = hostDup;
            gRepeaterPort = port;

            TVLog(@"CLI: Reverse connection to %@:%d", [NSString stringWithUTF8String:gRepeaterHost], gRepeaterPort);

            __reverseEnabled = YES;
            continue; // skip adding this arg
        }
        if (strcmp(arg, "-repeater") == 0) {
            if (i + 2 >= argc) {
                TVPrintError("-repeater requires: id host:port");
                exit(EXIT_FAILURE);
            }

            const char *idStr = argv[++i];
            long repId = strtol(idStr, NULL, 10);
            if (repId < 0 || repId > INT_MAX) {
                TVPrintError("Invalid repeater id: %s", idStr);
                exit(EXIT_FAILURE);
            }

            const char *hp = argv[++i];
            const char *hostBegin = hp;
            const char *hostEnd = NULL;
            const char *portStr = NULL;

            if (hp[0] == '[') {
                const char *rb = strchr(hp, ']');
                if (!rb || rb[1] != ':') {
                    TVPrintError("Invalid -repeater target: %s (expected [host]:port)", hp);
                    exit(EXIT_FAILURE);
                }

                hostBegin = hp + 1;
                hostEnd = rb;
                portStr = rb + 2;
            } else {
                const char *colon = strrchr(hp, ':');
                if (!colon || colon == hp || *(colon + 1) == '\0') {
                    TVPrintError("Invalid -repeater target: %s (expected host:port)", hp);
                    exit(EXIT_FAILURE);
                }

                hostBegin = hp;
                hostEnd = colon;
                portStr = colon + 1;
            }

            int port = (int)strtol(portStr, NULL, 10);
            if (port <= 0 || port > 65535) {
                TVPrintError("Invalid -repeater port: %s", portStr);
                exit(EXIT_FAILURE);
            }

            size_t hostLen = (size_t)(hostEnd - hostBegin);
            if (hostLen == 0) {
                TVPrintError("Invalid -repeater host (empty)");
                exit(EXIT_FAILURE);
            }

            char *hostDup = (char *)malloc(hostLen + 1);
            if (!hostDup) {
                TVPrintError("Out of memory");
                exit(EXIT_FAILURE);
            }

            memcpy(hostDup, hostBegin, hostLen);
            hostDup[hostLen] = '\0';

            if (gRepeaterHost) {
                free(gRepeaterHost);
                gRepeaterHost = NULL;
            }

            gRepeaterMode = 2;
            gRepeaterId = (int)repId;
            gRepeaterHost = hostDup;
            gRepeaterPort = port;

            TVLog(@"CLI: Repeater mode id=%d target=%@:%d", gRepeaterId, [NSString stringWithUTF8String:gRepeaterHost],
                  gRepeaterPort);

            __reverseEnabled = YES;
            continue; // skip adding this arg
        }

        __filtered.push_back(arg);
    }

    // Prepare argv for getopt from filtered vector
    int __argc2 = (int)__filtered.size();
    std::vector<char *> __argv2;
    __argv2.reserve(__filtered.size());
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wc++11-extensions"
    for (const char *s : __filtered)
        __argv2.push_back(const_cast<char *>(s));
#pragma clang diagnostic pop

    int opt;
    const char *optstr = "p:b:n:vA:c:C:s:F:d:Q:t:P:R:aW:w:NM:KU:O:o:I:i:H:D:e:k:B:T:Vh";
    optind = 1;
    while ((opt = getopt(__argc2, __argv2.data(), optstr)) != -1) {
        switch (opt) {
        case 'b': {
            if (!optarg || !*optarg) {
                TVPrintError("-b requires a non-empty host name or address");
                exit(EXIT_FAILURE);
            }
            gBindHost = [NSString stringWithUTF8String:optarg];
            TVLog(@"CLI: Bind host set to '%@'", gBindHost);
            break;
        }
        case 'p': {
            long port = strtol(optarg, NULL, 10);
            if (port <= 0 || port > 65535) {
                TVPrintError("Invalid port: %s", optarg);
                exit(EXIT_FAILURE);
            }
            gPort = (int)port;
            TVLog(@"CLI: Port set to %d", gPort);
            break;
        }
        case 'n': {
            gDesktopName = [NSString stringWithUTF8String:optarg ?: "ControlIOS"];
            TVLog(@"CLI: Desktop name set to '%@'", gDesktopName);
            break;
        }
        case 'v': {
            gViewOnly = YES;
            TVLog(@"CLI: View-only mode enabled (-v)");
            break;
        }
        case 'A': {
            double sec = strtod(optarg ? optarg : "0", NULL);
            if (sec < 15.0 || sec > 24 * 3600.0) {
                TVPrintError("Invalid keep-alive seconds: %s (expected 15..86400)", optarg);
                exit(EXIT_FAILURE);
            }
            gKeepAliveSec = sec;
            TVLog(@"CLI: KeepAlive interval set to %.3f sec (-A)", gKeepAliveSec);
            break;
        }
        case 'c': {
            long port = strtol(optarg, NULL, 10);
            if (port <= 0 || port > 65535) {
                TVPrintError("Invalid port: %s", optarg);
                exit(EXIT_FAILURE);
            }
            gTvCtlPort = (int)port;
            TVLog(@"CLI: Mgmt port set to %d", gTvCtlPort);
            break;
        }
        case 'C': {
            const char *val = optarg ? optarg : "on";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gClipboardEnabled = YES;
                TVLog(@"CLI: Clipboard sync enabled (-C %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gClipboardEnabled = NO;
                TVLog(@"CLI: Clipboard sync disabled (-C %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -C value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 's': {
            double sc = strtod(optarg, NULL);
            if (!(sc > 0.0 && sc <= 1.0)) {
                TVPrintError("Invalid scale: %s (expected 0 < s <= 1)", optarg);
                exit(EXIT_FAILURE);
            }
            gScale = sc;
            TVLog(@"CLI: Output scale factor set to %.3f", gScale);
            break;
        }
        case 'F': {
            // Accept formats: "fps", "min-max", "min:pref:max"
            const char *spec = optarg ? optarg : "";
            int minV = 0, prefV = 0, maxV = 0;
            if (spec[0] == '\0') {
                break; // ignore empty
            }
            const char *colon1 = strchr(spec, ':');
            const char *dash = strchr(spec, '-');
            if (colon1) {
                // min:pref:max
                long a = strtol(spec, NULL, 10);
                const char *p2 = colon1 + 1;
                const char *colon2 = strchr(p2, ':');
                if (!colon2) {
                    TVPrintError("Invalid -F spec: %s (expected min:pref:max)", spec);
                    exit(EXIT_FAILURE);
                }
                long b = strtol(p2, NULL, 10);
                long c = strtol(colon2 + 1, NULL, 10);
                minV = (int)a;
                prefV = (int)b;
                maxV = (int)c;
            } else if (dash) {
                // min-max (preferred defaults to max)
                long a = strtol(spec, NULL, 10);
                long b = strtol(dash + 1, NULL, 10);
                minV = (int)a;
                prefV = (int)b;
                maxV = (int)b;
            } else {
                // single fps
                long v = strtol(spec, NULL, 10);
                minV = (int)v;
                prefV = (int)v;
                maxV = (int)v;
            }
            // Normalize & validate: allow 0..240 (0 = unspecified)
            if (minV < 0)
                minV = 0;
            if (minV > 240)
                minV = 240;
            if (prefV < 0)
                prefV = 0;
            if (prefV > 240)
                prefV = 240;
            if (maxV < 0)
                maxV = 0;
            if (maxV > 240)
                maxV = 240;
            if (minV > 0 && maxV > 0 && minV > maxV) {
                int tmp = minV;
                minV = maxV;
                maxV = tmp;
            }
            if (prefV > 0) {
                if (minV > 0 && prefV < minV)
                    prefV = minV;
                if (maxV > 0 && prefV > maxV)
                    prefV = maxV;
            }
            gFpsMin = minV;
            gFpsPref = prefV;
            gFpsMax = maxV;
            TVLog(@"CLI: FPS preference set to min=%d pref=%d max=%d", gFpsMin, gFpsPref, gFpsMax);
            break;
        }
        case 'd': {
            double s = strtod(optarg, NULL);
            if (s < 0.0 || s > 0.5) {
                TVPrintError("Invalid defer window seconds: %s (expected 0..0.5)", optarg);
                exit(EXIT_FAILURE);
            }
            gDeferWindowSec = s;
            TVLog(@"CLI: Defer window set to %.3f sec", gDeferWindowSec);
            break;
        }
        case 'Q': {
            long q = strtol(optarg, NULL, 10);
            if (q < 0 || q > 8) {
                TVPrintError("Invalid max in-flight: %s (expected 0..8)", optarg);
                exit(EXIT_FAILURE);
            }
            gMaxInflightUpdates = (int)q;
            TVLog(@"CLI: Max in-flight updates set to %d", gMaxInflightUpdates);
            break;
        }
        case 't': {
            long ts = strtol(optarg, NULL, 10);
            if (ts < 8 || ts > 128) {
                TVPrintError("Invalid tile size: %s (expected 8..128)", optarg);
                exit(EXIT_FAILURE);
            }
            gTileSize = (int)ts;
            TVLog(@"CLI: Tile size set to %d", gTileSize);
            break;
        }
        case 'P': {
            long p = strtol(optarg, NULL, 10);
            if (p < 0 || p > 100) {
                TVPrintError("Invalid threshold percent: %s (expected 0..100; 0 disables dirty detection)", optarg);
                exit(EXIT_FAILURE);
            }
            gFullscreenThresholdPercent = (int)p;
            TVLog(@"CLI: Fullscreen threshold percent set to %d", gFullscreenThresholdPercent);
            break;
        }
        case 'R': {
            long m = strtol(optarg, NULL, 10);
            if (m < 1 || m > 4096) {
                TVPrintError("Invalid max rects: %s (expected 1..4096)", optarg);
                exit(EXIT_FAILURE);
            }
            gMaxRectsLimit = (int)m;
            TVLog(@"CLI: Max rects limit set to %d", gMaxRectsLimit);
            break;
        }
        case 'a': {
            gAsyncSwapEnabled = YES;
            TVLog(@"CLI: Non-blocking swap enabled (-a)");
            break;
        }
        case 'W': {
            double px = strtod(optarg, NULL);
            if (px == 0.0) {
                // 0 disables wheel emulation
                gWheelStepPx = 0.0;
                gWheelMaxStepPx = 0.0;
                TVLog(@"CLI: Wheel emulation disabled (-W 0)");
                break;
            }
            if (!(px > 4.0 && px <= 1000.0)) {
                TVPrintError("Invalid wheel step px: %s (expected 0 or >4..<=1000)", optarg);
                exit(EXIT_FAILURE);
            }
            gWheelStepPx = px;
            // Scale max step roughly 4x and adjust duration slope mildly
            gWheelMaxStepPx = fmax(2.0 * gWheelStepPx, 96.0) * 1.0;
            TVLog(@"CLI: Wheel step set to %.1f px (max=%.1f)", gWheelStepPx, gWheelMaxStepPx);
            break;
        }
        case 'w': {
            parseWheelOptions(optarg);
            break;
        }
        case 'N': {
            gWheelNaturalDir = YES;
            TVLog(@"CLI: Natural scroll direction enabled (-N)");
            break;
        }
        case 'M': {
            const char *val = optarg ? optarg : "std";
            if (strcmp(val, "std") == 0)
                gModMapScheme = 0;
            else if (strcmp(val, "altcmd") == 0)
                gModMapScheme = 1;
            else {
                TVPrintError("Invalid -M scheme: %s (expected std|altcmd)", val);
                exit(EXIT_FAILURE);
            }
            TVLog(@"CLI: Modifier mapping set to %s", gModMapScheme == 0 ? "std" : "altcmd");
            break;
        }
        case 'K': {
            gKeyEventLogging = YES;
            TVLog(@"CLI: Keyboard event logging enabled (-K)");
            break;
        }
        case 'E': {
            const char *val = optarg ? optarg : "off";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gAutoAssistEnabled = YES;
                TVLog(@"CLI: AssistiveTouch auto-activation enabled (-E %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gAutoAssistEnabled = NO;
                TVLog(@"CLI: AssistiveTouch auto-activation disabled (-E %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -E value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 'U': {
            const char *val = optarg ? optarg : "off";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gCursorEnabled = YES;
                TVLog(@"CLI: Cursor enabled (-U %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gCursorEnabled = NO;
                TVLog(@"CLI: Cursor disabled (-U %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -U value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 'O': {
            const char *val = optarg ? optarg : "off";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gOrientationSyncEnabled = YES;
                TVLog(@"CLI: Orientation observer enabled (-O %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gOrientationSyncEnabled = NO;
                TVLog(@"CLI: Orientation observer disabled (-O %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -O value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 'o': {
            const char *val = optarg ? optarg : "0";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gOrientationFixQuad = 3; // legacy: "on" means 270° (original behavior)
                TVLog(@"CLI: Orientation fix quad=3 (-o %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gOrientationFixQuad = 0;
                TVLog(@"CLI: Orientation fix quad=0 (-o %s)", [@(val) UTF8String]);
            } else {
                long v = strtol(val, NULL, 10);
                if (v >= 0 && v <= 3) {
                    gOrientationFixQuad = (int)v;
                    TVLog(@"CLI: Orientation fix quad=%d (-o %s)", gOrientationFixQuad, [@(val) UTF8String]);
                } else {
                    TVPrintError("Invalid -o value: %s (expected 0..3 or on|off)", val);
                    exit(EXIT_FAILURE);
                }
            }
            break;
        }
        case 'I': {
            const char *val = optarg ? optarg : "off";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gUserClientNotifsEnabled = YES;
                TVLog(@"CLI: Client user notifications enabled (-I %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gUserClientNotifsEnabled = NO;
                TVLog(@"CLI: Client user notifications disabled (-I %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -I value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 'i': {
            const char *val = optarg ? optarg : "off";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gUserSingleNotifsEnabled = YES;
                TVLog(@"CLI: Single user notifications enabled (-i %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gUserSingleNotifsEnabled = NO;
                TVLog(@"CLI: Single user notifications disabled (-i %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -i value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 'H': {
            long hp = strtol(optarg ? optarg : "0", NULL, 10);
            if (hp < 0 || hp > 65535) {
                TVPrintError("Invalid HTTP port: %s (expected 0..65535)", optarg);
                exit(EXIT_FAILURE);
            }
            gHttpPort = (int)hp;
            TVLog(@"CLI: HTTP port set to %d (-H)", gHttpPort);
            break;
        }
        case 'D': {
            const char *path = optarg ? optarg : "";
            if (!path || path[0] != '/') {
                TVPrintError("Invalid httpDir path for -D: %s (must be absolute)", path);
                exit(EXIT_FAILURE);
            }
            gHttpDirOverride = strdup(path);
            TVLog(@"CLI: HTTP dir override set to %s (-D)", path);
            break;
        }
        case 'e': {
            const char *path = optarg ? optarg : "";
            if (!path || !*path) {
                TVPrintError("Invalid value for -e (sslcertfile)");
                exit(EXIT_FAILURE);
            }
            gSslCertPath = strdup(path);
            TVLog(@"CLI: SSL cert file set (-e %s)", path);
            break;
        }
        case 'k': {
            const char *path = optarg ? optarg : "";
            if (!path || !*path) {
                TVPrintError("Invalid value for -k (sslkeyfile)");
                exit(EXIT_FAILURE);
            }
            gSslKeyPath = strdup(path);
            TVLog(@"CLI: SSL key file set (-k %s)", path);
            break;
        }
        case 'B': {
            const char *val = optarg ? optarg : "on";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gBonjourEnabled = YES;
                TVLog(@"CLI: Bonjour advertisement enabled (-B %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gBonjourEnabled = NO;
                TVLog(@"CLI: Bonjour advertisement disabled (-B %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -B value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 'T': {
            const char *val = optarg ? optarg : "off";
            if (strcasecmp(val, "on") == 0 || strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0) {
                gFileTransferEnabled = YES;
                TVLog(@"CLI: TightVNC 1.x file transfer extension enabled (-T %s)", [@(val) UTF8String]);
            } else if (strcasecmp(val, "off") == 0 || strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0) {
                gFileTransferEnabled = NO;
                TVLog(@"CLI: TightVNC 1.x file transfer extension disabled (-T %s)", [@(val) UTF8String]);
            } else {
                TVPrintError("Invalid -T value: %s (expected on|off|1|0|true|false)", val);
                exit(EXIT_FAILURE);
            }
            break;
        }
        case 'V': {
            tvncVerboseLoggingEnabled = YES;
            TVLog(@"CLI: Verbose logging enabled (-V)");
            break;
        }
        case 'h':
        default: {
            printUsageAndExit(argv[0]);
            break;
        }
        }
    }

    // Reverse connection active -> override conflicting settings
    if (__reverseEnabled) {
        gPort = -1;    // disable listening port
        gHttpPort = 0; // disable HTTP server
        if (gHttpDirOverride) {
            free(gHttpDirOverride);
            gHttpDirOverride = NULL;
        }
        gBonjourEnabled = NO; // disable Bonjour when reverse is used
        TVLog(@"CLI: Reverse enabled -> port=-1, http=0, bonjour=off");
    }
}

#pragma mark - Display

static rfbScreenInfoPtr gScreen = NULL;
static void (^gFrameHandler)(CMSampleBufferRef) = nil;

static int gWidth = 0;
static int gHeight = 0;
static int gSrcWidth = 0;      // capture source width
static int gSrcHeight = 0;     // capture source height
static size_t gFBSize = 0;     // in bytes
static int gBytesPerPixel = 4; // ARGB/BGRA 32-bit

static void *gFrontBuffer = NULL; // Exposed to VNC clients via gScreen->frameBuffer
static void *gBackBuffer = NULL;  // We render into this and then swap

// Hash algorithm selection (auto: prefer CRC32 on ARM with hardware support)
#if DEBUG
#if defined(__aarch64__) || defined(__ARM_FEATURE_CRC32)
static const BOOL cUseCRC32Hash = YES;
#else
static const BOOL cUseCRC32Hash = NO;
#endif
#endif

typedef struct {
    int x, y, w, h;
} DirtyRect;

#if defined(__aarch64__) || defined(__ARM_FEATURE_CRC32)
NS_INLINE uint64_t crc32_update(uint64_t h, const uint8_t *data, size_t len) {
    uint32_t c = (uint32_t)h;
    const uint8_t *p = data;
    size_t n = len;
    // Process 8-byte chunks
    while (n >= 8) {
        uint64_t v;
        // Unaligned load is acceptable on ARM64; use memcpy to be safe for strict aliasing.
        memcpy(&v, p, sizeof(v));
        c = __builtin_arm_crc32d(c, v);
        p += 8;
        n -= 8;
    }
    if (n >= 4) {
        uint32_t v32;
        memcpy(&v32, p, sizeof(v32));
        c = __builtin_arm_crc32w(c, v32);
        p += 4;
        n -= 4;
    }
    if (n >= 2) {
        uint16_t v16;
        memcpy(&v16, p, sizeof(v16));
        c = __builtin_arm_crc32h(c, v16);
        p += 2;
        n -= 2;
    }
    if (n) {
        c = __builtin_arm_crc32b(c, *p);
    }
    return (uint64_t)c;
}
#else
NS_INLINE uint64_t fnv1a_basis(void) { return 1469598103934665603ULL; }
NS_INLINE uint64_t fnv1a_update(uint64_t h, const uint8_t *data, size_t len) {
    const uint64_t FNV_PRIME = 1099511628211ULL;
    for (size_t i = 0; i < len; ++i) {
        h ^= (uint64_t)data[i];
        h *= FNV_PRIME;
    }
    return h;
}
#endif

// Generic hash wrappers: prefer hardware CRC32 when enabled and available, else fallback to FNV-1a.
NS_INLINE uint64_t hash_basis(void) {
#if defined(__aarch64__) || defined(__ARM_FEATURE_CRC32)
    return 0u; // CRC32 initial accumulator
#else
    return fnv1a_basis();
#endif
}

NS_INLINE uint64_t hash_update(uint64_t h, const uint8_t *data, size_t len) {
#if defined(__aarch64__) || defined(__ARM_FEATURE_CRC32)
    return crc32_update(h, data, len);
#else
    // If CRC32 not supported at compile time, fallback to FNV-1a
    return fnv1a_update(h, data, len);
#endif
}

#pragma mark - Display Tiling

static int gTilesX = 0;
static int gTilesY = 0;
static size_t gTileCount = 0;
static uint64_t *gPrevHash = NULL;
static uint64_t *gCurrHash = NULL;
static uint8_t *gPendingDirty = NULL; // per-tile pending dirty mask
static BOOL gHasPending = NO;

static void initializeTilingOrReset(void) {
    int tilesX = (gWidth + gTileSize - 1) / gTileSize;
    int tilesY = (gHeight + gTileSize - 1) / gTileSize;
    size_t tileCount = (size_t)tilesX * (size_t)tilesY;

    if (tilesX != gTilesX || tilesY != gTilesY || tileCount != gTileCount || !gPrevHash || !gCurrHash) {
        free(gPrevHash);
        free(gCurrHash);

        if (gPendingDirty) {
            free(gPendingDirty);
            gPendingDirty = NULL;
        }

        gPrevHash = (uint64_t *)malloc(tileCount * sizeof(uint64_t));
        gCurrHash = (uint64_t *)malloc(tileCount * sizeof(uint64_t));
        gPendingDirty = (uint8_t *)malloc(tileCount);

        if (!gPrevHash || !gCurrHash) {
            TVPrintError("Out of memory for tile hashes");
            exit(EXIT_FAILURE);
        }

        for (size_t i = 0; i < tileCount; ++i) {
            gPrevHash[i] = 0; // force full update first frame
            gCurrHash[i] = hash_basis();
        }

        gTilesX = tilesX;
        gTilesY = tilesY;
        gTileCount = tileCount;

        if (gPendingDirty)
            memset(gPendingDirty, 0, gTileCount);
    } else {
        for (size_t i = 0; i < gTileCount; ++i) {
            gCurrHash[i] = hash_basis();
        }
    }
}

NS_INLINE void swapTileHashes(void) {
    uint64_t *tmp = gPrevHash;
    gPrevHash = gCurrHash;
    gCurrHash = tmp;
}

NS_INLINE void resetCurrTileHashes(void) {
    if (!gCurrHash || gTileCount == 0)
        return;
    uint64_t basis = hash_basis();
    for (size_t i = 0; i < gTileCount; ++i) {
        gCurrHash[i] = basis;
    }
}

// Accumulate pending dirty tiles for time-based coalescing
NS_INLINE void accumulatePendingDirty(void) {
    if (!gPendingDirty)
        return;

    for (size_t i = 0; i < gTileCount; ++i) {
        if (gCurrHash[i] != gPrevHash[i])
            gPendingDirty[i] = 1;
    }
}

NS_INLINE void hashTiledFromBuffer(const uint8_t *buf, int width, int height, size_t bpr) {
    resetCurrTileHashes();
    for (int y = 0; y < height; ++y) {
        int ty = y / gTileSize;
        for (int tx = 0; tx < gTilesX; ++tx) {
            int startX = tx * gTileSize;
            if (startX >= width)
                break;
            int endX = startX + gTileSize;
            if (endX > width)
                endX = width;
            size_t offset = (size_t)startX * (size_t)gBytesPerPixel;
            size_t length = (size_t)(endX - startX) * (size_t)gBytesPerPixel;
            size_t tileIndex = (size_t)ty * (size_t)gTilesX + (size_t)tx;
            gCurrHash[tileIndex] = hash_update(gCurrHash[tileIndex], buf + (size_t)y * bpr + offset, length);
        }
    }
}

// Sparse sampling hash: sample a subset of pixels per tile to reduce bandwidth.
NS_INLINE void hashTiledFromBufferSparse(const uint8_t *buf, int width, int height, size_t bpr, int sx, int sy) {
    if (sx < 1)
        sx = 1;
    if (sy < 1)
        sy = 1;
    resetCurrTileHashes();
    for (int y = 0; y < height; y += sy) {
        int ty = y / gTileSize;
        for (int tx = 0; tx < gTilesX; ++tx) {
            int startX = tx * gTileSize;
            if (startX >= width)
                break;
            int endX = startX + gTileSize;
            if (endX > width)
                endX = width;
            size_t tileIndex = (size_t)ty * (size_t)gTilesX + (size_t)tx;
            for (int x = startX; x < endX; x += sx) {
                const uint8_t *p = buf + (size_t)y * bpr + (size_t)x * (size_t)gBytesPerPixel;
                gCurrHash[tileIndex] = hash_update(gCurrHash[tileIndex], p, (size_t)gBytesPerPixel);
            }
            // Ensure last column contributes even if not aligned to stride
            int lastX = endX - 1;
            if (lastX >= startX && ((endX - startX - 1) % sx) != 0) {
                const uint8_t *p = buf + (size_t)y * bpr + (size_t)lastX * (size_t)gBytesPerPixel;
                gCurrHash[tileIndex] = hash_update(gCurrHash[tileIndex], p, (size_t)gBytesPerPixel);
            }
        }
    }
    // Also sample the last row if height-1 isn't covered by the stride
    int lastY = height - 1;
    if (lastY >= 0 && ((height - 1) % sy) != 0) {
        int ty = lastY / gTileSize;
        for (int tx = 0; tx < gTilesX; ++tx) {
            int startX = tx * gTileSize;
            if (startX >= width)
                break;
            int endX = startX + gTileSize;
            if (endX > width)
                endX = width;
            size_t tileIndex = (size_t)ty * (size_t)gTilesX + (size_t)tx;
            for (int x = startX; x < endX; x += sx) {
                const uint8_t *p = buf + (size_t)lastY * bpr + (size_t)x * (size_t)gBytesPerPixel;
                gCurrHash[tileIndex] = hash_update(gCurrHash[tileIndex], p, (size_t)gBytesPerPixel);
            }
            int lastX = endX - 1;
            if (lastX >= startX && ((endX - startX - 1) % sx) != 0) {
                const uint8_t *p = buf + (size_t)lastY * bpr + (size_t)lastX * (size_t)gBytesPerPixel;
                gCurrHash[tileIndex] = hash_update(gCurrHash[tileIndex], p, (size_t)gBytesPerPixel);
            }
        }
    }
}

// Parallel full hash over tiles: split by tile rows to reduce wall clock at flush.
NS_INLINE void hashTiledFromBufferParallel(const uint8_t *buf, int width, int height, size_t bpr, int threads) {
    if (threads <= 1) {
        hashTiledFromBuffer(buf, width, height, bpr);
        return;
    }
    resetCurrTileHashes();
    // Split by tile row bands
    int tilesY = gTilesY;
    if (tilesY <= 0)
        return;
    int bands = threads;
    if (bands > tilesY)
        bands = tilesY;
    dispatch_group_t grp = dispatch_group_create();
    for (int band = 0; band < bands; ++band) {
        dispatch_group_async(grp, dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            for (int ty = band; ty < tilesY; ty += bands) {
                int startY = ty * gTileSize;
                int endY = startY + gTileSize;
                if (startY >= height)
                    break;
                if (endY > height)
                    endY = height;
                for (int y = startY; y < endY; ++y) {
                    for (int tx = 0; tx < gTilesX; ++tx) {
                        int startX = tx * gTileSize;
                        if (startX >= width)
                            break;
                        int endX = startX + gTileSize;
                        if (endX > width)
                            endX = width;
                        size_t offset = (size_t)startX * (size_t)gBytesPerPixel;
                        size_t length = (size_t)(endX - startX) * (size_t)gBytesPerPixel;
                        size_t tileIndex = (size_t)ty * (size_t)gTilesX + (size_t)tx;
                        // Each tileIndex is updated by a single band (fixed ty), no race across bands.
                        gCurrHash[tileIndex] =
                            hash_update(gCurrHash[tileIndex], buf + (size_t)y * bpr + offset, length);
                    }
                }
            }
        });
    }
    dispatch_group_wait(grp, DISPATCH_TIME_FOREVER);
}

// Build dirty rectangles from tile hash diffs. Returns number of rects written, up to maxRects.
static int buildDirtyRects(DirtyRect *rects, int maxRects, int *outChangedTiles) {
    int rectCount = 0;
    int changedTiles = 0;

    // First pass: horizontal merge per tile row
    for (int ty = 0; ty < gTilesY; ++ty) {
        int tx = 0;
        while (tx < gTilesX) {
            size_t idx = (size_t)ty * (size_t)gTilesX + (size_t)tx;
            int changed = (gCurrHash[idx] != gPrevHash[idx]);
            if (!changed) {
                tx++;
                continue;
            }

            // Start of a run
            int runStart = tx;
            changedTiles++;
            tx++;
            while (tx < gTilesX) {
                size_t idx2 = (size_t)ty * (size_t)gTilesX + (size_t)tx;
                if (gCurrHash[idx2] != gPrevHash[idx2]) {
                    changedTiles++;
                    tx++;
                } else
                    break;
            }

            // Emit rect for this horizontal run
            if (rectCount < maxRects) {
                int x = runStart * gTileSize;
                int w = (tx - runStart) * gTileSize;
                int y = ty * gTileSize;
                int h = gTileSize;
                // Clip to screen bounds
                if (x + w > gWidth)
                    w = gWidth - x;
                if (y + h > gHeight)
                    h = gHeight - y;
                rects[rectCount++] = (DirtyRect){x, y, w, h};
            } else {
                // Too many rects; caller may fallback to fullscreen
                if (outChangedTiles)
                    *outChangedTiles = changedTiles;
                return rectCount;
            }
        }
    }

    // Optional vertical merge: merge rects with same x,w and contiguous vertically
    // Simple O(n^2) merge for small rect counts
    for (int i = 0; i < rectCount; ++i) {
        for (int j = i + 1; j < rectCount; ++j) {
            if (rects[j].w == 0 || rects[j].h == 0)
                continue;
            if (rects[i].x == rects[j].x && rects[i].w == rects[j].w) {
                if (rects[i].y + rects[i].h == rects[j].y) {
                    rects[i].h += rects[j].h;
                    rects[j].w = rects[j].h = 0; // mark removed
                } else if (rects[j].y + rects[j].h == rects[i].y) {
                    rects[j].h += rects[i].h;
                    rects[i].w = rects[i].h = 0;
                }
            }
        }
    }

    // Compact removed entries
    int k = 0;
    for (int i = 0; i < rectCount; ++i) {
        if (rects[i].w > 0 && rects[i].h > 0)
            rects[k++] = rects[i];
    }

    rectCount = k;
    if (outChangedTiles)
        *outChangedTiles = changedTiles;
    return rectCount;
}

// Build rects from pending mask by temporarily mapping to hashes
static int buildRectsFromPending(DirtyRect *rects, int maxRects) {
    if (!gPendingDirty)
        return 0;

    // Temporarily mark curr!=prev for pending tiles
    // Save originals
    // For efficiency, we only synthesize gCurrHash markers without touching buffers
    size_t changed = 0;
    for (size_t i = 0; i < gTileCount; ++i) {
        if (gPendingDirty[i]) {
            if (gCurrHash[i] == gPrevHash[i])
                gCurrHash[i] ^= 0x1ULL;
            changed++;
        }
    }

    int dummyTiles = 0;
    int cnt = buildDirtyRects(rects, maxRects, &dummyTiles);

    // Restore hashes for tiles we toggled
    for (size_t i = 0; i < gTileCount; ++i) {
        if (gPendingDirty[i]) {
            if (gCurrHash[i] == gPrevHash[i])
                gCurrHash[i] ^= 0x1ULL; // unlikely path
            else if ((gCurrHash[i] ^ 0x1ULL) == gPrevHash[i])
                gCurrHash[i] ^= 0x1ULL;
        }
    }

    (void)changed;
    return cnt;
}

NS_INLINE void markRectsModified(DirtyRect *rects, int rectCount) {
    for (int i = 0; i < rectCount; ++i) {
        rfbMarkRectAsModified(gScreen, rects[i].x, rects[i].y, rects[i].x + rects[i].w, rects[i].y + rects[i].h);
    }
}

NS_INLINE void copyRectsFromBackToFront(DirtyRect *rects, int rectCount) {
    size_t fbBPR = (size_t)gWidth * (size_t)gBytesPerPixel;
    for (int i = 0; i < rectCount; ++i) {
        int x = rects[i].x, y = rects[i].y, w = rects[i].w, h = rects[i].h;
        size_t rowBytes = (size_t)w * (size_t)gBytesPerPixel;
        for (int r = 0; r < h; ++r) {
            uint8_t *dst = (uint8_t *)gFrontBuffer + (size_t)(y + r) * fbBPR + (size_t)x * gBytesPerPixel;
            uint8_t *src = (uint8_t *)gBackBuffer + (size_t)(y + r) * fbBPR + (size_t)x * gBytesPerPixel;
            memcpy(dst, src, rowBytes);
        }
    }
}

#pragma mark - Display Hooks

static std::atomic<int> gInflight(0);

// Track encode life-cycle to provide backpressure via inflight counter
static void displayHook(rfbClientPtr cl) {
    (void)cl;
    gInflight.fetch_add(1, std::memory_order_relaxed);
}

static void displayFinishedHook(rfbClientPtr cl, int result) {
    (void)cl;
    (void)result;
    gInflight.fetch_sub(1, std::memory_order_relaxed);
}

static int setDesktopSizeHook(int width, int height, int numScreens, rfbExtDesktopScreen *extDesktopScreens,
                              rfbClientPtr cl) {
    (void)cl;
    (void)numScreens;
    (void)extDesktopScreens;
    [[ScreenCapturer sharedCapturer] forceNextFrameUpdate];
    // We do not support client-initiated resizing
    return rfbExtDesktopSize_ResizeProhibited;
}

#pragma mark - Display Tiling Constants

// Hashing performance controls
static const int cHashStrideX = 4;              // sparse sampling stride X (>=1; 1 = full scan)
static const int cHashStrideY = 4;              // sparse sampling stride Y (>=1; 1 = full scan)
static const BOOL cSparseHashDuringDefer = YES; // use sparse hashing while within defer window
// Skip vImage scaling when src/dst size difference is small; copy with pad/crop instead
static const int cNoScalePadThresholdPx = 8; // if both |dW| and |dH| <= this, do pad/crop copy

// Flush-time hashing optimization
static const BOOL cParallelHashOnFlush = YES; // use parallel hashing at flush to reduce wall time

#pragma mark - Frame Handlers

static std::atomic<int> gRotationQuad(0); // 0=0°, 1=90°, 2=180°, 3=270° (clockwise)
static void *gRotateScratch = NULL;       // rotation scratch (for 90°/270°)
static size_t gRotateScratchSize = 0;     // bytes
static void *gScaleTemp = NULL;           // vImage scale temp buffer
static size_t gScaleTempSize = 0;         // bytes

// Align width up to a multiple of 4 (helps encoders/clients). Preserve aspect by adjusting height.
NS_INLINE void alignDimensions(int rawW, int rawH, int *alignedW, int *alignedH) {
    if (rawW <= 0)
        rawW = 1;
    if (rawH <= 0)
        rawH = 1;
    // Round width up to next multiple of 4
    int w4 = (rawW + 3) & ~3;
    long long numer = (long long)rawH * (long long)w4;
    int hAdj = (int)((numer + rawW / 2) / rawW); // rounded to nearest
    if (hAdj <= 0)
        hAdj = 1;
    *alignedW = w4;
    *alignedH = hAdj;
}

// Resize framebuffer according to rotation (0/180 keep WxH from src, 90/270 swap), then apply scale
static BOOL tvDisconnectAllClients(void); // định nghĩa ở dưới; cần khi xoay đổi cỡ

// Trả YES nếu ĐÃ đổi cỡ framebuffer (để nhánh xoay ngắt client cho PC nối lại ở
// cỡ mới — client này không hỗ trợ NewFBSize nên phải nối lại).
NS_INLINE BOOL maybeResizeFramebufferForRotation(int rotQ) {
    // Source capture size (portrait-orientated)
    int srcW = gSrcWidth;
    int srcH = gSrcHeight;
    if (srcW <= 0 || srcH <= 0)
        return NO;

    // Rotate at source dimension stage
    int rotW = (rotQ % 2 == 0) ? srcW : srcH;
    int rotH = (rotQ % 2 == 0) ? srcH : srcW;

    // Apply output scaling then align width to multiple of 4 (adjust height to preserve aspect)
    int outWraw = (gScale > 0.0 && gScale < 1.0) ? MAX(1, (int)floor((double)rotW * gScale)) : rotW;
    int outHraw = (gScale > 0.0 && gScale < 1.0) ? MAX(1, (int)floor((double)rotH * gScale)) : rotH;
    int outW = 0, outH = 0;
    alignDimensions(outWraw, outHraw, &outW, &outH);

    if (outW == gWidth && outH == gHeight)
        return NO; // no change

    // Allocate new double buffers
    size_t newFBSize = (size_t)outW * (size_t)outH * (size_t)gBytesPerPixel;
    void *newFront = calloc(1, newFBSize);
    void *newBack = calloc(1, newFBSize);
    if (!newFront || !newBack) {
        TVPrintError("Failed to allocate required frame buffers");
        exit(EXIT_FAILURE);
    }

    // Swap buffers into screen & notify clients
    gWidth = outW;
    gHeight = outH;
    gFBSize = newFBSize;

    if (gScreen) {
        // Update server with new framebuffer
        rfbNewFramebuffer(gScreen, (char *)newFront, gWidth, gHeight, 8, 3, gBytesPerPixel);
        // Restore BGRA little-endian channel layout (R shift=16, G=8, B=0)
        int bps = 8;
        gScreen->serverFormat.redShift = bps * 2;   // 16
        gScreen->serverFormat.greenShift = bps * 1; // 8
        gScreen->serverFormat.blueShift = 0;        // 0
        gScreen->paddedWidthInBytes = gWidth * gBytesPerPixel;
    }

    // Free old buffers and store new pointers
    if (gFrontBuffer)
        free(gFrontBuffer);
    if (gBackBuffer)
        free(gBackBuffer);
    gFrontBuffer = newFront;
    gBackBuffer = newBack;

    // Keep gScreen->frameBuffer in sync (rfbNewFramebuffer already did, but ensure local)
    if (gScreen)
        gScreen->frameBuffer = (char *)gFrontBuffer;

    // Re-init tiling/hash state for new geometry
    initializeTilingOrReset();
    // Clear pending dirty flags to avoid carrying over old-geometry state into the new geometry
    if (gPendingDirty)
        memset(gPendingDirty, 0, gTileCount);

    gHasPending = NO;
    TVLog(@"Resize: framebuffer changed to %dx%d (rotQ=%d, scale=%.3f)", gWidth, gHeight, rotQ, gScale);
    return YES;
}

// Ensure scratch buffer for rotation is available and large enough
NS_INLINE int ensureRotateScratch(size_t w, size_t h) {
    size_t need = w * h * (size_t)gBytesPerPixel;
    if (need == 0)
        return -1;
    if (gRotateScratchSize >= need && gRotateScratch)
        return 0;
    void *nbuf = realloc(gRotateScratch, need);
    memset(nbuf, 0, need);
    if (!nbuf)
        return -1;
    gRotateScratch = nbuf;
    gRotateScratchSize = need;
    return 0;
}

NS_INLINE int ensureScaleTemp(size_t srcW, size_t srcH, size_t dstW, size_t dstH, vImage_Flags flags) {
    vImage_Buffer s = {.data = NULL,
                       .height = (vImagePixelCount)srcH,
                       .width = (vImagePixelCount)srcW,
                       .rowBytes = srcW * (size_t)gBytesPerPixel};
    vImage_Buffer d = {.data = NULL,
                       .height = (vImagePixelCount)dstH,
                       .width = (vImagePixelCount)dstW,
                       .rowBytes = dstW * (size_t)gBytesPerPixel};
    vImage_Error need = vImageScale_ARGB8888(&s, &d, NULL, flags | kvImageGetTempBufferSize);
    if (need < 0)
        return -1;
    size_t nbytes = (size_t)need;
    if (nbytes == 0)
        return 0;
    if (gScaleTempSize >= nbytes && gScaleTemp)
        return 0;
    void *nbuf = realloc(gScaleTemp, nbytes);
    memset(nbuf, 0, nbytes);
    if (!nbuf)
        return -1;
    gScaleTemp = nbuf;
    gScaleTempSize = nbytes;
    return 0;
}

// Row-by-row copy to convert a possibly-strided captured buffer into a tightly packed VNC buffer.
NS_INLINE void copyWithStrideTight(uint8_t *dstTight, const uint8_t *src, int width, int height,
                                   size_t srcBytesPerRow) {
    size_t dstBPR = (size_t)width * gBytesPerPixel;
    for (int y = 0; y < height; ++y) {
        memcpy(dstTight + (size_t)y * dstBPR, src + (size_t)y * srcBytesPerRow, dstBPR);
    }
}

// Copy with small pad/crop to avoid expensive scaling when sizes are close.
// Strategy:
// - Copy overlap region at (0,0) with width=min(srcW,dstW), height=min(srcH,dstH)
// - If dst wider, horizontally replicate the last pixel in each row to fill the right pad.
// - If dst taller, vertically replicate the last valid row to fill the bottom pad.
NS_INLINE void copyPadOrCropToTight(uint8_t *dstTight, int dstW, int dstH, const uint8_t *src, int srcW, int srcH,
                                    size_t srcBytesPerRow) {
    const int bpp = gBytesPerPixel;
    const size_t dstBPR = (size_t)dstW * (size_t)bpp;
    const int overlapW = srcW < dstW ? srcW : dstW;
    const int overlapH = srcH < dstH ? srcH : dstH;

    // 1) Copy overlap region row-by-row
    if (overlapW > 0 && overlapH > 0) {
        const size_t copyBytes = (size_t)overlapW * (size_t)bpp;
        for (int y = 0; y < overlapH; ++y) {
            uint8_t *drow = dstTight + (size_t)y * dstBPR;
            const uint8_t *srow = src + (size_t)y * srcBytesPerRow;
            memcpy(drow, srow, copyBytes);
            // 2) Right pad by replicating last pixel if needed
            if (dstW > overlapW) {
                const uint8_t *lastPx = (overlapW > 0) ? (drow + ((size_t)overlapW - 1) * (size_t)bpp) : drow;
                for (int x = overlapW; x < dstW; ++x) {
                    memcpy(drow + (size_t)x * (size_t)bpp, lastPx, (size_t)bpp);
                }
            }
        }
    }

    // 3) Bottom pad by replicating last valid row if needed
    if (dstH > overlapH) {
        uint8_t *lastRow = (overlapH > 0) ? (dstTight + (size_t)(overlapH - 1) * dstBPR) : dstTight;
        for (int y = overlapH; y < dstH; ++y) {
            uint8_t *drow = dstTight + (size_t)y * dstBPR;
            memcpy(drow, lastRow, dstBPR);
        }
    }
}

NS_INLINE void swapBuffers(void) {
    void *tmp = gFrontBuffer;
    gFrontBuffer = gBackBuffer;
    gBackBuffer = tmp;
    gScreen->frameBuffer = (char *)gFrontBuffer;
}

// Try to acquire all clients' sendMutex without blocking.
// Returns 1 on success and fills locked[] with acquired mutexes (count in *lockedCount),
// otherwise returns 0 and releases any partial locks.
static int tryLockAllClients(pthread_mutex_t **locked, size_t *lockedCount, size_t capacity) {
    *lockedCount = 0;
    rfbClientIteratorPtr it = rfbGetClientIterator(gScreen);
    rfbClientPtr cl;

    int ok = 1;
    while ((cl = rfbClientIteratorNext(it))) {
        if (*lockedCount >= capacity) {
            ok = 0;
            break;
        }
        pthread_mutex_t *m = &cl->sendMutex;
        if (pthread_mutex_trylock(m) == 0) {
            locked[(*lockedCount)++] = m;
        } else {
            ok = 0;
            break;
        }
    }

    rfbReleaseClientIterator(it);

    if (!ok) {
        // release any that were acquired
        for (size_t i = 0; i < *lockedCount; ++i) {
            pthread_mutex_unlock(locked[i]);
        }
        *lockedCount = 0;
        return 0;
    }

    return 1;
}

// Blocking lock helpers (original behavior): lock all clients, then unlock all.
NS_INLINE void lockAllClientsBlocking(void) {
    rfbClientIteratorPtr it = rfbGetClientIterator(gScreen);
    rfbClientPtr cl;
    while ((cl = rfbClientIteratorNext(it))) {
        pthread_mutex_lock(&cl->sendMutex);
    }
    rfbReleaseClientIterator(it);
}

NS_INLINE void unlockAllClientsBlocking(void) {
    rfbClientIteratorPtr it = rfbGetClientIterator(gScreen);
    rfbClientPtr cl;
    while ((cl = rfbClientIteratorNext(it))) {
        pthread_mutex_unlock(&cl->sendMutex);
    }
    rfbReleaseClientIterator(it);
}

static void handleFramebuffer(CMSampleBufferRef sampleBuffer) {

#if DEBUG
    // Perf: overall start timestamp
    CFAbsoluteTime __tv_tStart = CFAbsoluteTimeGetCurrent();
#endif

    CVPixelBufferRef pb = CMSampleBufferGetImageBuffer(sampleBuffer);
    if (!pb) {
        TVLogVerbose(@"sampleBuffer has no image buffer (skip)");
        return;
    }

    // Busy-drop: if encoders are busy and limit reached, skip this frame (disabled when -Q 0)
    if (gMaxInflightUpdates > 0 && gInflight.load(std::memory_order_relaxed) >= gMaxInflightUpdates) {
        // When busy dropping, skip all hashing/dirty work.
        TVLogVerbose(@"drop frame due to inflight=%d >= limit=%d", gInflight.load(std::memory_order_relaxed),
                     gMaxInflightUpdates);
        return;
    }

#if DEBUG
    CFAbsoluteTime __tv_tLock0 = CFAbsoluteTimeGetCurrent();
#endif

    CVPixelBufferLockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);

#if DEBUG
    CFAbsoluteTime __tv_tLock1 = CFAbsoluteTimeGetCurrent();
    CFTimeInterval __tv_msLock = (__tv_tLock1 - __tv_tLock0) * 1000.0;
    TVLogVerbose(@"lock pixel buffer took %.3f ms", __tv_msLock);
#endif

    uint8_t *base = (uint8_t *)CVPixelBufferGetBaseAddress(pb);
    const size_t srcBPR = (size_t)CVPixelBufferGetBytesPerRow(pb);
    const size_t width = (size_t)CVPixelBufferGetWidth(pb);
    const size_t height = (size_t)CVPixelBufferGetHeight(pb);

    // Determine rotation and resize framebuffer if orientation implies new dimensions.
    int rotQ = (gOrientationSyncEnabled ? gRotationQuad.load(std::memory_order_relaxed) : 0) & 3;

#if DEBUG
    CFAbsoluteTime __tv_tResize0 = CFAbsoluteTimeGetCurrent();
#endif

    // Xoay làm đổi cỡ (W↔H) -> ngắt client để PC nối lại ở cỡ mới (không có
    // DesktopSize thì không tự cập nhật được).
    if (maybeResizeFramebufferForRotation(rotQ))
        tvDisconnectAllClients();

#if DEBUG
    CFAbsoluteTime __tv_tResize1 = CFAbsoluteTimeGetCurrent();
    CFTimeInterval __tv_msResize = (__tv_tResize1 - __tv_tResize0) * 1000.0;
    TVLogVerbose(@"maybeResize(rotQ=%d) took %.3f ms (server=%dx%d, src=%zux%zu)", rotQ, __tv_msResize, gWidth, gHeight,
                 width, height);
#endif

    if ((int)width != gWidth || (int)height != gHeight) {
        // With scaling enabled, this is expected; log once for info. Without scaling, warn once.
        static BOOL sLoggedSizeInfoOnce = NO;
        if (!sLoggedSizeInfoOnce) {
            sLoggedSizeInfoOnce = YES;
            if (gScale != 1.0) {
                TVLogVerbose(@"Scaling source %zux%zu -> output %dx%d (scale=%.3f)", width, height, gWidth, gHeight,
                             gScale);
            } else {
                TVLogVerbose(@"Captured frame size %zux%zu differs from server %dx%d; cropping/copying minimum region.",
                             width, height, gWidth, gHeight);
            }
        }
    }

    // Copy/Rotate/Scale into back buffer. ScreenCapturer is always portrait-oriented.
    // We rotate by UI orientation then scale to server size.
    BOOL dirtyDisabled = (gFullscreenThresholdPercent == 0);

    static int sLastRotQ = -1;
    bool rotationChanged = (sLastRotQ == -1) ? false : ((rotQ & 3) != (sLastRotQ & 3));
    bool needsRotate = (rotQ != 0);

    vImage_Buffer srcBuf = {
        .data = base, .height = (vImagePixelCount)height, .width = (vImagePixelCount)width, .rowBytes = srcBPR};

    vImage_Buffer stage = srcBuf; // after rotation
    vImage_Buffer rotBuf = {0};

#if DEBUG
    CFTimeInterval __tv_msRotate = 0.0;
    CFTimeInterval __tv_msScaleOrCopy = 0.0;
#endif

    if (needsRotate) {

#if DEBUG
        CFAbsoluteTime __tv_tRot0 = CFAbsoluteTimeGetCurrent();
#endif

        size_t rotW = (rotQ % 2 == 0) ? (size_t)width : (size_t)height;
        size_t rotH = (rotQ % 2 == 0) ? (size_t)height : (size_t)width;
        if (ensureRotateScratch(rotW, rotH) != 0) {
            CVPixelBufferUnlockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);
            return;
        }

        rotBuf.data = gRotateScratch;
        rotBuf.width = (vImagePixelCount)rotW;
        rotBuf.height = (vImagePixelCount)rotH;
        rotBuf.rowBytes = rotW * (size_t)gBytesPerPixel;

        uint8_t rotConst = kRotate0DegreesClockwise;
        switch (rotQ) {
        case 1:
            rotConst = kRotate90DegreesClockwise;
            break;
        case 2:
            rotConst = kRotate180DegreesClockwise;
            break;
        case 3:
            rotConst = kRotate270DegreesClockwise;
            break;
        default:
            rotConst = kRotate0DegreesClockwise;
            break;
        }

        uint8_t bg[4] = {0, 0, 0, 0};
        vImage_Error rerr = vImageRotate90_ARGB8888(&srcBuf, &rotBuf, rotConst, bg, kvImageNoFlags);
        if (rerr != kvImageNoError) {
            static BOOL sLoggedRotErrOnce = NO;
            if (!sLoggedRotErrOnce) {
                sLoggedRotErrOnce = YES;
                TVLog(@"vImageRotate90_ARGB8888 failed: %ld", (long)rerr);
            }

            CVPixelBufferUnlockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);
            return;
        }

        stage = rotBuf;

#if DEBUG
        CFAbsoluteTime __tv_tRot1 = CFAbsoluteTimeGetCurrent();
        __tv_msRotate = (__tv_tRot1 - __tv_tRot0) * 1000.0;
        TVLogVerbose(@"rotate %d*90 took %.3f ms (rotW=%zu, rotH=%zu)", rotQ, __tv_msRotate, (size_t)rotBuf.width,
                     (size_t)rotBuf.height);
#endif
    }

    // Scale stage to back buffer (tightly packed)
    vImage_Buffer dstBuf = {.data = gBackBuffer,
                            .height = (vImagePixelCount)gHeight,
                            .width = (vImagePixelCount)gWidth,
                            .rowBytes = (size_t)gWidth * (size_t)gBytesPerPixel};
    if (stage.width == dstBuf.width && stage.height == dstBuf.height && gScale == 1.0) {

#if DEBUG
        CFAbsoluteTime __tv_tCopy0 = CFAbsoluteTimeGetCurrent();
#endif

        copyWithStrideTight((uint8_t *)dstBuf.data, (const uint8_t *)stage.data, gWidth, gHeight, stage.rowBytes);

#if DEBUG
        CFAbsoluteTime __tv_tCopy1 = CFAbsoluteTimeGetCurrent();
        __tv_msScaleOrCopy = (__tv_tCopy1 - __tv_tCopy0) * 1000.0;
        TVLogVerbose(@"copy stage->back (tight) took %.3f ms", __tv_msScaleOrCopy);
#endif

    } else {

        // Small-diff pad/crop fast path to avoid vImageScale when sizes are close
        int dW = (int)dstBuf.width - (int)stage.width;
        int dH = (int)dstBuf.height - (int)stage.height;
        if (cNoScalePadThresholdPx > 0 && dW <= cNoScalePadThresholdPx && dW >= -cNoScalePadThresholdPx &&
            dH <= cNoScalePadThresholdPx && dH >= -cNoScalePadThresholdPx) {

#if DEBUG
            CFAbsoluteTime __tv_tPad0 = CFAbsoluteTimeGetCurrent();
#endif

            copyPadOrCropToTight((uint8_t *)dstBuf.data, (int)dstBuf.width, (int)dstBuf.height,
                                 (const uint8_t *)stage.data, (int)stage.width, (int)stage.height, stage.rowBytes);

#if DEBUG
            CFAbsoluteTime __tv_tPad1 = CFAbsoluteTimeGetCurrent();
            __tv_msScaleOrCopy = (__tv_tPad1 - __tv_tPad0) * 1000.0;
            TVLogVerbose(@"pad/crop copy stage->back took %.3f ms (stage=%zux%zu -> dst=%dx%d, thr=%d)",
                         __tv_msScaleOrCopy, (size_t)stage.width, (size_t)stage.height, gWidth, gHeight,
                         cNoScalePadThresholdPx);
#endif

        } else {

#if DEBUG
            CFAbsoluteTime __tv_tScale0 = CFAbsoluteTimeGetCurrent();
#endif

            if (ensureScaleTemp(stage.width, stage.height, dstBuf.width, dstBuf.height, kvImageHighQualityResampling) !=
                0) {
                CVPixelBufferUnlockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);
                return;
            }

            vImage_Error err = vImageScale_ARGB8888(&stage, &dstBuf, gScaleTemp, kvImageHighQualityResampling);
            if (err != kvImageNoError) {
                static BOOL sLoggedVImageErrOnce = NO;
                if (!sLoggedVImageErrOnce) {
                    sLoggedVImageErrOnce = YES;
                    TVLog(@"vImageScale_ARGB8888 failed: %ld", (long)err);
                }
                CVPixelBufferUnlockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);
                return;
            }

#if DEBUG
            CFAbsoluteTime __tv_tScale1 = CFAbsoluteTimeGetCurrent();
            __tv_msScaleOrCopy = (__tv_tScale1 - __tv_tScale0) * 1000.0;
            TVLogVerbose(@"scale stage->back took %.3f ms (stage=%zux%zu -> dst=%dx%d)", __tv_msScaleOrCopy,
                         (size_t)stage.width, (size_t)stage.height, gWidth, gHeight);
#endif
        }
    }

#if DEBUG
    CFAbsoluteTime __tv_tUnlock0 = CFAbsoluteTimeGetCurrent();
#endif

    CVPixelBufferUnlockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);

#if DEBUG
    CFAbsoluteTime __tv_tUnlock1 = CFAbsoluteTimeGetCurrent();
    CFTimeInterval __tv_msUnlock = (__tv_tUnlock1 - __tv_tUnlock0) * 1000.0;
    TVLogVerbose(@"unlock pixel buffer took %.3f ms", __tv_msUnlock);
#endif

    // If rotation just changed, force a full-screen update and reset dirty state
    // to avoid mixing hashes/pending dirties from the previous orientation.
    if (rotationChanged) {
        // Clear pending mask/state
        if (gPendingDirty)
            memset(gPendingDirty, 0, gTileCount);
        gHasPending = NO;

#if DEBUG
        CFAbsoluteTime __tv_tSwap0 = CFAbsoluteTimeGetCurrent();
#endif

        if (gAsyncSwapEnabled) {
            pthread_mutex_t *locked[64];
            size_t lockedCount = 0;
            if (tryLockAllClients(locked, &lockedCount, sizeof(locked) / sizeof(locked[0]))) {
                swapBuffers();
                for (size_t i = 0; i < lockedCount; ++i)
                    pthread_mutex_unlock(locked[i]);
                rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);

#if DEBUG
                CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
                TVLogVerbose(@"rotationChanged async-swap+mark fullscreen took %.3f ms",
                             (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif

            } else {
                copyWithStrideTight((uint8_t *)gFrontBuffer, (uint8_t *)gBackBuffer, gWidth, gHeight,
                                    (size_t)gWidth * (size_t)gBytesPerPixel);
                rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);

#if DEBUG
                CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
                TVLogVerbose(@"rotationChanged copy(fullscreen)+mark took %.3f ms",
                             (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif
            }
        } else {
            lockAllClientsBlocking();
            swapBuffers();
            rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);
            unlockAllClientsBlocking();

#if DEBUG
            CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
            TVLogVerbose(@"rotationChanged blocking-swap+mark fullscreen took %.3f ms",
                         (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif
        }

        // Skip dirty detection for this frame after rotation; return early
        sLastRotQ = rotQ;

        // Rotation may not change geometry (0<->180). Maintain hashes here so
        // the next frame recomputes curr and swaps to form a clean baseline.
        resetCurrTileHashes();
        swapTileHashes();

#if DEBUG
        CFAbsoluteTime __tv_tEnd = CFAbsoluteTimeGetCurrent();
        TVLogVerbose(@"rotationChanged summary rotQ=%d lock=%.3fms resize=%.3fms rotate=%.3fms scale/copy=%.3fms "
                     @"total=%.3fms",
                     rotQ, __tv_msLock, __tv_msResize, __tv_msRotate, __tv_msScaleOrCopy,
                     (__tv_tEnd - __tv_tStart) * 1000.0);
#endif

        return;
    }

    // If dirty detection is disabled, perform a full-screen update
    if (dirtyDisabled) {

#if DEBUG
        CFAbsoluteTime __tv_tSwap0 = CFAbsoluteTimeGetCurrent();
#endif

        if (gAsyncSwapEnabled) {
            pthread_mutex_t *locked[64];
            size_t lockedCount = 0;
            if (tryLockAllClients(locked, &lockedCount, sizeof(locked) / sizeof(locked[0]))) {
                swapBuffers();
                for (size_t i = 0; i < lockedCount; ++i)
                    pthread_mutex_unlock(locked[i]);
                rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);

#if DEBUG
                CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
                TVLogVerbose(@"dirtyDisabled async-swap+mark fullscreen took %.3f ms",
                             (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif

            } else {
                // Whole screen copy fallback (tight -> tight)
                copyWithStrideTight((uint8_t *)gFrontBuffer, (uint8_t *)gBackBuffer, gWidth, gHeight,
                                    (size_t)gWidth * (size_t)gBytesPerPixel);
                rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);

#if DEBUG
                CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
                TVLogVerbose(@"dirtyDisabled copy(fullscreen)+mark took %.3f ms", (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif
            }
        } else {
            // Blocking swap to avoid tearing
            lockAllClientsBlocking();
            swapBuffers();
            rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);
            unlockAllClientsBlocking();

#if DEBUG
            CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
            TVLogVerbose(@"dirtyDisabled blocking-swap+mark fullscreen took %.3f ms",
                         (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif
        }

#if DEBUG
        CFAbsoluteTime __tv_tEnd = CFAbsoluteTimeGetCurrent();
        TVLogVerbose(
            @"dirtyDisabled summary rotQ=%d lock=%.3fms resize=%.3fms rotate=%.3fms scale/copy=%.3fms total=%.3fms",
            rotQ, __tv_msLock, __tv_msResize, __tv_msRotate, __tv_msScaleOrCopy, (__tv_tEnd - __tv_tStart) * 1000.0);
#endif

        return;
    }

    // Build dirty rectangles with deferred coalescing window (enabled)
    // Lightweight hashing to update pending and decide whether to flush.

#if DEBUG
    CFAbsoluteTime __tv_tHash0 = CFAbsoluteTimeGetCurrent();
#endif

    if (cSparseHashDuringDefer && gDeferWindowSec > 0) {
        hashTiledFromBufferSparse((const uint8_t *)gBackBuffer, gWidth, gHeight,
                                  (size_t)gWidth * (size_t)gBytesPerPixel, cHashStrideX, cHashStrideY);
    } else {
        resetCurrTileHashes();
        hashTiledFromBuffer((const uint8_t *)gBackBuffer, gWidth, gHeight, (size_t)gWidth * (size_t)gBytesPerPixel);
    }

#if DEBUG
    CFAbsoluteTime __tv_tHash1 = CFAbsoluteTimeGetCurrent();
    CFTimeInterval __tv_msHash = (__tv_tHash1 - __tv_tHash0) * 1000.0;
    TVLogVerbose(@"tile hashing took %.3f ms (tiles=%zu, tileSize=%d)%@%@", __tv_msHash, gTileCount, gTileSize,
                 (cSparseHashDuringDefer && gDeferWindowSec > 0) ? @" [sparse]" : @"",
                 cUseCRC32Hash ? @" [crc32]" : @" [fnv]");
#endif

    enum { kRectBuf = 1024 };
    DirtyRect rects[kRectBuf];
    int changedTiles = 0;

    // Accumulate pending dirty tiles

#if DEBUG
    CFAbsoluteTime __tv_tPend0 = CFAbsoluteTimeGetCurrent();
#endif

    accumulatePendingDirty();

#if DEBUG
    CFAbsoluteTime __tv_tPend1 = CFAbsoluteTimeGetCurrent();
    CFTimeInterval __tv_msPend = (__tv_tPend1 - __tv_tPend0) * 1000.0;
    TVLogVerbose(@"accumulate pending took %.3f ms (hasPending=%@)", __tv_msPend, gHasPending ? @"YES" : @"NO");
#endif

    // Decide whether to flush now
    BOOL shouldFlush = YES;
    static CFAbsoluteTime sDeferStartTime = 0;
    if (gDeferWindowSec > 0) {
        if (!gHasPending) {
            gHasPending = YES;
            sDeferStartTime = CFAbsoluteTimeGetCurrent();
            shouldFlush = NO; // start window, wait for more
        } else {
            CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
            shouldFlush = ((now - sDeferStartTime) >= gDeferWindowSec);
            TVLogVerbose(@"defer window elapsed=%.3f ms (threshold=%.3f ms) -> %@", (now - sDeferStartTime) * 1000.0,
                         gDeferWindowSec * 1000.0, shouldFlush ? @"FLUSH" : @"WAIT");
        }
    }

    int rectCount = 0;
    int changedPct = 0;
    BOOL fullScreen = NO;

    if (!shouldFlush) {
        // Still deferring: do not notify clients yet; keep previous full-hash baseline.

#if DEBUG
        CFAbsoluteTime __tv_tEnd = CFAbsoluteTimeGetCurrent();
        TVLogVerbose(@"deferred (no flush) summary rotQ=%d lock=%.3fms resize=%.3fms rotate=%.3fms scale/copy=%.3fms "
                     @"hash=%.3fms total=%.3fms",
                     rotQ, __tv_msLock, __tv_msResize, __tv_msRotate, __tv_msScaleOrCopy, __tv_msHash,
                     (__tv_tEnd - __tv_tStart) * 1000.0);
#endif

        return;
    }

    // At flush: recompute full hashes for precise rects
    {

#if DEBUG
        CFAbsoluteTime __tv_tHashFull0 = CFAbsoluteTimeGetCurrent();
#endif

        if (cParallelHashOnFlush) {
            // Use number of logical CPUs as thread hint (capped)
            int threads = (int)[[NSProcessInfo processInfo] processorCount];
            if (threads < 2)
                threads = 2;
            if (threads > 8)
                threads = 8;
            hashTiledFromBufferParallel((const uint8_t *)gBackBuffer, gWidth, gHeight,
                                        (size_t)gWidth * (size_t)gBytesPerPixel, threads);
        } else {
            resetCurrTileHashes();
            hashTiledFromBuffer((const uint8_t *)gBackBuffer, gWidth, gHeight, (size_t)gWidth * (size_t)gBytesPerPixel);
        }

#if DEBUG
        CFAbsoluteTime __tv_tHashFull1 = CFAbsoluteTimeGetCurrent();
        __tv_msHash = (__tv_tHashFull1 - __tv_tHashFull0) * 1000.0;
        TVLogVerbose(@"tile hashing (flush full)%@ took %.3f ms (tiles=%zu, tileSize=%d)%@",
                     cParallelHashOnFlush ? @" [parallel]" : @"", __tv_msHash, gTileCount, gTileSize,
                     cUseCRC32Hash ? @" [crc32]" : @" [fnv]");
#endif
    }

// Promote pending tiles into rects
#if DEBUG
    CFAbsoluteTime __tv_tRects0 = CFAbsoluteTimeGetCurrent();
#endif

    rectCount = buildRectsFromPending(rects, MIN(gMaxRectsLimit, kRectBuf));

    // If anything from this frame is also new dirty not in pending, ensure included
    int extraTiles = 0;
    if (rectCount == 0) {
        rectCount = buildDirtyRects(rects, MIN(gMaxRectsLimit, kRectBuf), &changedTiles);
    } else {
        // Merge current frame dirties by re-running with hashes, bounded
        DirtyRect rectsNow[kRectBuf];
        int nowCount = buildDirtyRects(rectsNow, MIN(gMaxRectsLimit, kRectBuf), &extraTiles);

        // Simple append then vertical merge will compact later in pipeline
        int space = kRectBuf - rectCount;
        int take = nowCount < space ? nowCount : space;
        if (take > 0)
            memcpy(&rects[rectCount], rectsNow, (size_t)take * sizeof(DirtyRect));
        rectCount += take;
    }

    int totalTiles = (int)gTileCount;
    int totalChanged = changedTiles + extraTiles;
    changedPct = (totalTiles > 0) ? (totalChanged * 100 / totalTiles) : 100;

    if (rectCount >= gMaxRectsLimit) {
        // Collapse to bounding box
        int minX = gWidth, minY = gHeight, maxX = 0, maxY = 0;
        for (int i = 0; i < rectCount; ++i) {
            if (rects[i].w <= 0 || rects[i].h <= 0)
                continue;
            if (rects[i].x < minX)
                minX = rects[i].x;
            if (rects[i].y < minY)
                minY = rects[i].y;
            if (rects[i].x + rects[i].w > maxX)
                maxX = rects[i].x + rects[i].w;
            if (rects[i].y + rects[i].h > maxY)
                maxY = rects[i].y + rects[i].h;
        }

        rects[0] = (DirtyRect){minX, minY, maxX - minX, maxY - minY};
        rectCount = 1;

        TVLogVerbose(@"rects exceeded limit -> collapse to bbox");
    }

    fullScreen = (changedPct >= gFullscreenThresholdPercent) || rectCount == 0;

#if DEBUG
    CFAbsoluteTime __tv_tRects1 = CFAbsoluteTimeGetCurrent();
    CFTimeInterval __tv_msRects = (__tv_tRects1 - __tv_tRects0) * 1000.0;
    TVLogVerbose(@"build rects took %.3f ms (rects=%d, changedTiles=%d, extraTiles=%d, changedPct=%d%%, fsThresh=%d%%, "
                 @"fullscreen=%@)",
                 __tv_msRects, rectCount, changedTiles, extraTiles, changedPct, gFullscreenThresholdPercent,
                 fullScreen ? @"YES" : @"NO");
#endif

    // Clear pending
    if (gPendingDirty)
        memset(gPendingDirty, 0, gTileCount);

    gHasPending = NO;

#if DEBUG
    CFAbsoluteTime __tv_tSwap0 = CFAbsoluteTimeGetCurrent();
#endif

    if (gAsyncSwapEnabled) {
        // Try non-blocking swap with fallback to single-buffer copy.
        pthread_mutex_t *locked[64];
        size_t lockedCount = 0;

        if (tryLockAllClients(locked, &lockedCount, sizeof(locked) / sizeof(locked[0]))) {
            swapBuffers();
            for (size_t i = 0; i < lockedCount; ++i)
                pthread_mutex_unlock(locked[i]);
            if (fullScreen) {
                rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);
            } else {
                markRectsModified(rects, rectCount);
            }

#if DEBUG
            CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
            TVLogVerbose(@"async-swap+mark took %.3f ms (%@)", (__tv_tSwap1 - __tv_tSwap0) * 1000.0,
                         fullScreen ? @"fullscreen" : @"partial");
#endif

        } else {
            if (fullScreen) {
                // Whole screen copy fallback (tight -> tight)
                copyWithStrideTight((uint8_t *)gFrontBuffer, (uint8_t *)gBackBuffer, gWidth, gHeight,
                                    (size_t)gWidth * (size_t)gBytesPerPixel);
                rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);

#if DEBUG
                CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
                TVLogVerbose(@"async path copy(fullscreen)+mark took %.3f ms", (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif

            } else {
                // Only copy dirty regions from back to front to reduce tearing and bandwidth
                copyRectsFromBackToFront(rects, rectCount);
                markRectsModified(rects, rectCount);

#if DEBUG
                CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
                TVLogVerbose(@"async path copy(dirty %d rects)+mark took %.3f ms", rectCount,
                             (__tv_tSwap1 - __tv_tSwap0) * 1000.0);
#endif
            }
        }
    } else {
        // Original blocking behavior to avoid tearing.
        lockAllClientsBlocking();
        swapBuffers();
        if (fullScreen) {
            rfbMarkRectAsModified(gScreen, 0, 0, gWidth, gHeight);
        } else {
            markRectsModified(rects, rectCount);
        }
        unlockAllClientsBlocking();

#if DEBUG
        CFAbsoluteTime __tv_tSwap1 = CFAbsoluteTimeGetCurrent();
        TVLogVerbose(@"blocking-swap+mark took %.3f ms (%@)", (__tv_tSwap1 - __tv_tSwap0) * 1000.0,
                     fullScreen ? @"fullscreen" : @"partial");
#endif
    }

    // Prepare for next frame: current hashes become previous
    swapTileHashes();
    sLastRotQ = rotQ;

#if DEBUG
    CFAbsoluteTime __tv_tEnd = CFAbsoluteTimeGetCurrent();
    TVLogVerbose(@"frame summary rotQ=%d lock=%.3fms resize=%.3fms rotate=%.3fms scale/copy=%.3fms hash=%.3fms "
                 @"rects=%.3fms total=%.3fms (rectCount=%d, changedPct=%d%%, fullscreen=%@, inflight=%d/%d)",
                 rotQ, __tv_msLock, __tv_msResize, __tv_msRotate, __tv_msScaleOrCopy, __tv_msHash, __tv_msRects,
                 (__tv_tEnd - __tv_tStart) * 1000.0, rectCount, changedPct, fullScreen ? @"YES" : @"NO",
                 gInflight.load(std::memory_order_relaxed), gMaxInflightUpdates);
#endif
}

#pragma mark - Event Handlers

NS_INLINE NSString *keysymToString(rfbKeySym ks) {
    // Alphanumeric and basic ASCII
    if ((ks >= 0x20 && ks <= 0x7E) || ks == ' ') {
        unichar ch = (unichar)ks;
        return [NSString stringWithCharacters:&ch length:1];
    }
    switch (ks) {
    case XK_Return:
    case XK_KP_Enter:
        return @"RETURN";
    case XK_Tab:
        return @"TAB";
    case XK_Escape:
        return @"ESCAPE";
    case XK_BackSpace:
        return @"BACKSPACE";
    case XK_Delete:
        return @"FORWARDDELETE";
    case XK_Insert:
        return @"INSERT";
    case XK_Home:
        return @"HOME";
    case XK_End:
        return @"END";
    case XK_Page_Up:
        return @"PAGEUP";
    case XK_Page_Down:
        return @"PAGEDOWN";
    case XK_Left:
        return @"LEFTARROW";
    case XK_Right:
        return @"RIGHTARROW";
    case XK_Up:
        return @"UPARROW";
    case XK_Down:
        return @"DOWNARROW";
    case XK_space:
        return @" ";
    case XK_Shift_L:
        return @"LEFTSHIFT";
    case XK_Shift_R:
        return @"RIGHTSHIFT";
    case XK_Control_L:
        return @"LEFTCONTROL";
    case XK_Control_R:
        return @"RIGHTCONTROL";
    // Modifier mapping depending on scheme
    case XK_Alt_L:
        return (gModMapScheme == 1) ? @"LEFTCOMMAND" : @"LEFTALT"; // Option or Command
    case XK_Alt_R:
        return (gModMapScheme == 1) ? @"RIGHTCOMMAND" : @"RIGHTALT"; // Option or Command
    case XK_ISO_Level3_Shift:
        return @"LEFTALT"; // macOS left Option often sent as ISO_Level3_Shift
    case XK_Mode_switch:
        return @"RIGHTALT"; // Mode switch often behaves like AltGr
    case XK_Meta_L:
        return (gModMapScheme == 1) ? @"LEFTALT" : @"LEFTCOMMAND"; // Option or Command
    case XK_Meta_R:
        return (gModMapScheme == 1) ? @"RIGHTALT" : @"RIGHTCOMMAND"; // Option or Command
    case XK_Super_L:
        return @"LEFTCOMMAND"; // Treat Super as Command in both schemes
    case XK_Super_R:
        return @"RIGHTCOMMAND";
    default:
        break;
    }
    // Function keys XK_F1..XK_F24
    if (ks >= XK_F1 && ks <= XK_F24) {
        int idx = (int)(ks - XK_F1) + 1;
        return [NSString stringWithFormat:@"F%d", idx];
    }
    return nil;
}

static void kbdAddEvent(rfbBool down, rfbKeySym keySym, rfbClientPtr cl) {
    (void)cl;
    if (gViewOnly)
        return;

    STHIDEventGenerator *gen = [STHIDEventGenerator sharedGenerator];

    // Map common XF86 multimedia/brightness keysyms to iOS HID events
    switch ((unsigned long)keySym) {
    // Brightness Up/Down
    case 0x1008ff02UL: // XF86MonBrightnessUp
        if (down)
            [gen displayBrightnessIncrementDown];
        else
            [gen displayBrightnessIncrementUp];
        return;
    case 0x1008ff03UL: // XF86MonBrightnessDown
        if (down)
            [gen displayBrightnessDecrementDown];
        else
            [gen displayBrightnessDecrementUp];
        return;
    // Volume/Mute
    case 0x1008ff13UL: // XF86AudioRaiseVolume
        if (down)
            [gen volumeIncrementDown];
        else
            [gen volumeIncrementUp];
        return;
    case 0x1008ff11UL: // XF86AudioLowerVolume
        if (down)
            [gen volumeDecrementDown];
        else
            [gen volumeDecrementUp];
        return;
    case 0x1008ff12UL: // XF86AudioMute
        if (down)
            [gen muteDown];
        else
            [gen muteUp];
        return;
    // Media keys: Previous / Play-Pause / Next (use Consumer usages)
    case 0x1008ff3eUL: // Map as Previous Track (per user observation)
        if (down)
            [gen otherConsumerUsageDown:kHIDUsage_Csmr_ScanPreviousTrack];
        else
            [gen otherConsumerUsageUp:kHIDUsage_Csmr_ScanPreviousTrack];
        return;
    case 0x1008ff14UL: // XF86AudioPlay (toggle Play/Pause)
        if (down)
            [gen otherConsumerUsageDown:kHIDUsage_Csmr_PlayOrPause];
        else
            [gen otherConsumerUsageUp:kHIDUsage_Csmr_PlayOrPause];
        return;
    case 0x1008ff97UL: // Map as Next Track (per user observation)
        if (down)
            [gen otherConsumerUsageDown:kHIDUsage_Csmr_ScanNextTrack];
        else
            [gen otherConsumerUsageUp:kHIDUsage_Csmr_ScanNextTrack];
        return;
    default:
        break;
    }

    NSString *keyStr = keysymToString(keySym);
    if (gKeyEventLogging && tvncLoggingEnabled) {
        const char *mapped = keyStr ? [keyStr UTF8String] : "(nil)";
        rfbLog("[key] %s keysym=0x%lx (%lu) mapped=%s\n", down ? "down" : " up ", (unsigned long)keySym,
               (unsigned long)keySym, mapped);
    }

    if (!keyStr)
        return;

    if (down)
        [gen keyDown:keyStr];
    else
        [gen keyUp:keyStr];
}

static void kbdReleaseAllKeys(rfbClientPtr cl) {
    (void)cl;
    if (gViewOnly)
        return;

    STHIDEventGenerator *gen = [STHIDEventGenerator sharedGenerator];
    [gen releaseEveryKeys];
}

NS_INLINE CGPoint vncPointToDevicePoint(int vx, int vy) {
    // Map from VNC framebuffer space (gWidth x gHeight, post-rotation & scaling)
    // back to device capture space (portrait, gSrcWidth x gSrcHeight), inverting rotation.
    int rotQ = (gOrientationSyncEnabled ? gRotationQuad.load(std::memory_order_relaxed) : 0) & 3;

#if !TARGET_IPHONE_SIMULATOR
    // Apply user-configured rotation offset (0..3 quadrants CW)
    int effRotQ = (rotQ + gOrientationFixQuad) & 3;
#else
    int effRotQ = rotQ;
#endif

    // Dimensions of the rotated (pre-scale) stage
    int rotW = (effRotQ % 2 == 0) ? gSrcWidth : gSrcHeight;
    int rotH = (effRotQ % 2 == 0) ? gSrcHeight : gSrcWidth;

    // Undo scaling from stage(rotW x rotH) -> VNC(gWidth x gHeight)
    double sx = (gWidth > 0) ? ((double)rotW / (double)gWidth) : 1.0;
    double sy = (gHeight > 0) ? ((double)rotH / (double)gHeight) : 1.0;
    double stX = sx * (double)vx;
    double stY = sy * (double)vy;

    // Clamp to stage bounds
    if (stX < 0)
        stX = 0;
    if (stY < 0)
        stY = 0;
    if (stX > (double)(rotW - 1))
        stX = (double)(rotW - 1);
    if (stY > (double)(rotH - 1))
        stY = (double)(rotH - 1);

    // Invert rotation: stage -> source portrait space
    double dx = 0.0, dy = 0.0;
    switch (effRotQ) {
    case 0: // identity
        dx = stX;
        dy = stY;
        break;
    case 1: // 90 CW: inverse of stageX=srcH-1-srcY, stageY=srcX -> srcX=stageY; srcY=srcH-1-stageX
        dx = stY;
        dy = (double)(gSrcHeight - 1) - stX;
        break;
    case 2: // 180: srcX = srcW-1 - stageX; srcY = srcH-1 - stageY
        dx = (double)(gSrcWidth - 1) - stX;
        dy = (double)(gSrcHeight - 1) - stY;
        break;
    case 3: // 270 CW (90 CCW): inverse of stageX=srcY, stageY=srcW-1-srcX -> srcX=srcW-1-stageY; srcY=stageX
        dx = (double)(gSrcWidth - 1) - stY;
        dy = stX;
        break;
    }

    // Final clamp to device bounds
    if (dx < 0)
        dx = 0;
    if (dy < 0)
        dy = 0;
    if (dx > (double)(gSrcWidth - 1))
        dx = (double)(gSrcWidth - 1);
    if (dy > (double)(gSrcHeight - 1))
        dy = (double)(gSrcHeight - 1);

    return CGPointMake((CGFloat)dx, (CGFloat)dy);
}

@interface STHIDEventGenerator (Private)
- (void)touchDownAtPoints:(CGPoint *)locations touchCount:(NSUInteger)touchCount;
- (void)liftUpAtPoints:(CGPoint *)locations touchCount:(NSUInteger)touchCount;
- (void)_updateTouchPoints:(CGPoint *)points count:(NSUInteger)count;
@end

#define CLIENT_ID_LEN 8

// Per-client state stored in cl->clientData to avoid cross-client conflicts.
typedef struct {
    int lastButtonMask;                // last received pointer button mask from this client
    double wheelAccumPx;               // accumulated scroll in pixels (+down, -up) for this client
    BOOL wheelFlushScheduled;          // whether a flush is pending for this client
    BOOL isRepeaterClient;             // whether this client is a repeater
    char clientId8[CLIENT_ID_LEN + 1]; // cached 8-char client id (NUL-terminated)
} TVClientState;

NS_INLINE TVClientState *tvGetClientState(rfbClientPtr cl) { return cl ? (TVClientState *)cl->clientData : NULL; }

static dispatch_queue_t gWheelQueue = nil; // serial queue for wheel gestures

static void wheelScheduleFlush(rfbClientPtr cl, CGPoint anchorPoint, double delaySec, int rotQ) {
    TVClientState *st = tvGetClientState(cl);
    if (!st)
        return;

    if (gWheelStepPx <= 0) { // disabled
        st->wheelAccumPx = 0.0;
        st->wheelFlushScheduled = NO;
        return;
    }

    // Ensure client remains valid during delayed execution
    rfbIncrClientRef(cl);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(delaySec * NSEC_PER_SEC)), gWheelQueue, ^{
        TVClientState *st2 = tvGetClientState(cl);
        if (!st2) {
            rfbDecrClientRef(cl);
            return;
        }

        // Consume the entire accumulation in one gesture to avoid many small drags.
        double takeRaw = st2->wheelAccumPx;
        st2->wheelAccumPx = 0.0; // zero out
        st2->wheelFlushScheduled = NO;
        double mag = fabs(takeRaw);
        if (mag < 1.0) {
            rfbDecrClientRef(cl);
            return;
        }

        // Velocity-like amplification: for larger accumulations (faster wheel),
        // slightly increase distance instead of emitting many short drags.
        double amp = 1.0 + fmin(gWheelAmpCap, gWheelAmpCoeff * log1p(mag / fmax(gWheelStepPx, 1.0)));
        double take = copysign(mag * amp, takeRaw);

        // Guarantee a small-but-meaningful movement for tiny scrolls
        if (fabs(take) < (gWheelMinTakeRatio * gWheelStepPx)) {
            take = copysign(gWheelMinTakeRatio * gWheelStepPx, take);
        }

        // Absolute clamp for safety
        double absClamp = gWheelMaxStepPx * gWheelAbsClampFactor;
        if (take > absClamp)
            take = absClamp;
        if (take < -absClamp)
            take = -absClamp;

        // Map VNC-vertical delta into device axis based on rotation
        CGFloat dx = 0, dy = 0;
        switch (rotQ & 3) {
        case 0: // portrait
            dx = 0;
            dy = (CGFloat)take;
            break;
        case 2: // upside-down
            dx = 0;
            dy = (CGFloat)(-take);
            break;
        case 1: // landscape left (90 CW)
            dx = (CGFloat)(+take);
            dy = 0;
            break;
        case 3: // landscape right (270 CW)
            dx = (CGFloat)(-take);
            dy = 0;
            break;
        }

        CGFloat endX = anchorPoint.x + dx;
        CGFloat endY = anchorPoint.y + dy;
        if (endX < 0)
            endX = 0;
        CGFloat maxX = (CGFloat)gSrcWidth - 1;
        if (endX > maxX)
            endX = maxX;
        if (endY < 0)
            endY = 0;
        CGFloat maxY = (CGFloat)gSrcHeight - 1;
        if (endY > maxY)
            endY = maxY;
        CGPoint endPt = CGPointMake(endX, endY);

        // Duration scales sub-linearly with distance; parameters configurable
        double dur = gWheelDurBase + gWheelDurK * sqrt(fabs(take));
        if (dur > gWheelDurMax)
            dur = gWheelDurMax;
        if (dur < gWheelDurMin)
            dur = gWheelDurMin;

        [[STHIDEventGenerator sharedGenerator] dragLinearWithStartPoint:anchorPoint endPoint:endPt duration:dur];

        rfbDecrClientRef(cl);
    });
}

static BOOL tvGetTouchLockNotifyState(void);

static NSMutableArray<NSString *> *tvHomeAuditEntries(void) {
    static NSMutableArray<NSString *> *entries;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{ entries = [NSMutableArray array]; });
    return entries;
}

static void tvRecordHomeAudit(NSString *source, NSString *detail) {
    NSMutableArray<NSString *> *entries = tvHomeAuditEntries();
    @synchronized (entries) {
        NSString *line = [NSString stringWithFormat:@"%.3f | %@ | touchLock=%d%@%@",
                          [[NSDate date] timeIntervalSince1970], source ?: @"unknown",
                          tvGetTouchLockNotifyState(), detail.length ? @" | " : @"", detail ?: @""];
        [entries addObject:line];
        while (entries.count > 200) [entries removeObjectAtIndex:0];
        TVLog(@"HOME-AUDIT %@", line);
    }
}

static void ptrAddEvent(int buttonMask, int x, int y, rfbClientPtr cl) {
    if (gViewOnly)
        return;

    STHIDEventGenerator *gen = [STHIDEventGenerator sharedGenerator];
    CGPoint pt = vncPointToDevicePoint(x, y);

    TVClientState *st = tvGetClientState(cl);
    int lastMask = st ? st->lastButtonMask : 0;

    // Left button (bit 0)
    bool leftNow = (buttonMask & 1) != 0;
    bool leftPrev = (lastMask & 1) != 0;
    if (leftNow && !leftPrev) {
        [gen touchDownAtPoints:&pt touchCount:1];
    } else if (!leftNow && leftPrev) {
        [gen liftUpAtPoints:&pt touchCount:1];
    } else if (leftNow) {
        CGPoint p = pt;
        [gen _updateTouchPoints:&p count:1];
    }

    // Middle button (bit 1 -> mask 2): map to Power key
    bool midNow = (buttonMask & 2) != 0;
    bool midPrev = (lastMask & 2) != 0;
    if (midNow && !midPrev) {
        [gen powerDown];
    } else if (!midNow && midPrev) {
        [gen powerUp];
    }

    // Right button (bit 2 -> mask 4): map to Home/Menu key
    bool rightNow = (buttonMask & 4) != 0;
    bool rightPrev = (lastMask & 4) != 0;
    if (rightNow && !rightPrev) {
        tvRecordHomeAudit(@"vnc-right-button", cl && cl->host
                          ? [NSString stringWithUTF8String:cl->host] : @"unknown-client");
        [gen menuDown];
    } else if (!rightNow && rightPrev) {
        [gen menuUp];
    }

    // Wheel emulation: coalesce ticks and perform async flicks off the VNC thread.
    bool wheelUpNow = (buttonMask & 8) != 0;  // button 4
    bool wheelDnNow = (buttonMask & 16) != 0; // button 5
    bool wheelUpPrev = (lastMask & 8) != 0;
    bool wheelDnPrev = (lastMask & 16) != 0;

    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        gWheelQueue = dispatch_queue_create("com.82flex.trollvnc.wheel", DISPATCH_QUEUE_SERIAL_WITH_AUTORELEASE_POOL);
    });

    if (gWheelStepPx > 0 && ((wheelUpNow && !wheelUpPrev) || (wheelDnNow && !wheelDnPrev))) {
        double delta = (wheelDnNow && !wheelDnPrev) ? +gWheelStepPx : -gWheelStepPx;
        if (gWheelNaturalDir)
            delta = -delta;
        int rotQ = (gOrientationSyncEnabled ? gRotationQuad.load(std::memory_order_relaxed) : 0) & 3;
        // Ensure client remains valid while we touch its state asynchronously
        rfbIncrClientRef(cl);
        dispatch_async(gWheelQueue, ^{
            TVClientState *st2 = tvGetClientState(cl);
            if (st2) {
                st2->wheelAccumPx += delta;
                if (!st2->wheelFlushScheduled) {
                    st2->wheelFlushScheduled = YES;
                    wheelScheduleFlush(cl, pt, gWheelCoalesceSec, rotQ);
                }
            }
            rfbDecrClientRef(cl);
        });
    }

    if (st)
        st->lastButtonMask = buttonMask;
}

#pragma mark - Bonjour (mDNS) Advertisement

static NSNetService *gBonjourService = nil;     // VNC service (_rfb._tcp.)
static NSNetService *gBonjourHttpService = nil; // Optional HTTP service (_http._tcp.)

// Compute a short, per-boot stable hash suffix (8 hex chars) from boot time
static NSString *tvBootHash8(void) {
    static NSString *suf = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        // Read boot time from the kernel (stable across process restarts in the same boot)
        struct timeval boottv = {0};
        size_t len = sizeof(boottv);
        int mib[2] = {CTL_KERN, KERN_BOOTTIME};
        int rc = sysctl(mib, 2, &boottv, &len, NULL, 0);

        uint64_t h = 1469598103934665603ULL; // FNV-1a 64-bit offset basis
        const uint64_t p = 1099511628211ULL; // prime
        if (rc == 0 && len == sizeof(boottv)) {
            uint64_t sec = (uint64_t)boottv.tv_sec;
            uint64_t usec = (uint64_t)boottv.tv_usec;
            for (int i = 0; i < 8; i++) {
                h ^= (uint8_t)((sec >> (i * 8)) & 0xFF);
                h *= p;
            }
            for (int i = 0; i < 8; i++) {
                h ^= (uint8_t)((usec >> (i * 8)) & 0xFF);
                h *= p;
            }
        } else {
            // Fallback: hash the monotonic uptime seconds (may vary between app restarts in same boot)
            uint64_t up_ms = (uint64_t)([NSProcessInfo processInfo].systemUptime * 1000.0);
            for (int i = 0; i < 8; i++) {
                h ^= (uint8_t)((up_ms >> (i * 8)) & 0xFF);
                h *= p;
            }
        }
        unsigned int shortHash = (unsigned int)(h & 0xFFFFFFFFu); // 32-bit
        suf = [NSString stringWithFormat:@"%08x", shortHash];
    });
    return suf;
}

// Compose Bonjour service name as gDesktopName + 8-char boot hash, clamped to 63 bytes
static NSString *tvBonjourServiceName(NSString *baseName) {
    NSString *name = baseName ?: @"ControlIOS";
    NSString *suffix = tvBootHash8();
    // mDNS single-label length limit is 63 bytes (UTF-8). We reserve suffix bytes.
    const NSUInteger maxBytes = 63;
    NSData *data = [name dataUsingEncoding:NSUTF8StringEncoding];
    // Reserve bytes for "-" + 8-char suffix (ASCII)
    NSUInteger reserve = suffix.length + 1; // hyphen + suffix
    if (reserve >= maxBytes) {
        // Pathological, but keep at least the suffix
        return suffix;
    }
    while (data.length + reserve > maxBytes && name.length > 0) {
        name = [name substringToIndex:name.length - 1];
        data = [name dataUsingEncoding:NSUTF8StringEncoding];
    }
    if (name.length == 0) {
        return suffix; // no space left for hyphen
    }
    return [NSString stringWithFormat:@"%@-%@", name, suffix];
}

@interface TVBonjourDelegate : NSObject <NSNetServiceDelegate>
@end

@implementation TVBonjourDelegate
- (void)netServiceDidPublish:(NSNetService *)sender {
    TVLog(@"Bonjour: published %@.%@:%ld", sender.name, sender.type, (long)sender.port);
}
- (void)netService:(NSNetService *)sender didNotPublish:(NSDictionary<NSString *, NSNumber *> *)errorDict {
    TVLog(@"Bonjour: failed to publish %@.%@ (err=%@)", sender.name, sender.type, errorDict);
}
- (void)netServiceDidStop:(NSNetService *)sender {
    TVLog(@"Bonjour: stopped %@.%@", sender.name, sender.type);
}
@end

static TVBonjourDelegate *gBonjourDelegate = nil;

static NSData *bonjourTXTRecord(void) {
    // Minimal helpful metadata for clients
    // Keys kept short; values ASCII per convention
    NSMutableDictionary<NSString *, NSData *> *txt = [NSMutableDictionary dictionary];
    // Name
    if (gDesktopName.length > 0) {
        txt[@"vn"] = [gDesktopName dataUsingEncoding:NSUTF8StringEncoding];
    }
    // View-only flag
    txt[@"vo"] = [[NSString stringWithFormat:@"%d", gViewOnly ? 1 : 0] dataUsingEncoding:NSASCIIStringEncoding];
    // HTTP availability
    txt[@"hp"] = [[NSString stringWithFormat:@"%d", gHttpPort] dataUsingEncoding:NSASCIIStringEncoding];
    // FPS pref (if provided)
    if (gFpsMin || gFpsPref || gFpsMax) {
        NSString *fps = [NSString stringWithFormat:@"%d:%d:%d", gFpsMin, gFpsPref, gFpsMax];
        txt[@"fps"] = [fps dataUsingEncoding:NSASCIIStringEncoding];
    }
    return [NSNetService dataFromTXTRecordDictionary:txt];
}

static void refreshBonjourTXTRecord(void) {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{
            refreshBonjourTXTRecord();
        });
        return;
    }
    if (!gBonjourService)
        return;
    [gBonjourService setTXTRecordData:bonjourTXTRecord()];
}

static void stopBonjour(void) {
    // NSNetService expects interactions on a runloop thread (prefer main).
    if (![NSThread isMainThread]) {
        dispatch_sync(dispatch_get_main_queue(), ^{
            stopBonjour();
        });
        return;
    }
    if (gBonjourService) {
        [gBonjourService stop];
        gBonjourService = nil;
    }
    if (gBonjourHttpService) {
        [gBonjourHttpService stop];
        gBonjourHttpService = nil;
    }
}

static void startBonjour(void) {
    // NSNetService expects interactions on a runloop thread (prefer main).
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{
            startBonjour();
        });
        return;
    }
    if (!gBonjourEnabled) {
        TVLog(@"Bonjour: disabled");
        return;
    }

    if (!gBonjourDelegate)
        gBonjourDelegate = [TVBonjourDelegate new];

    // Publish VNC service: _rfb._tcp. on gPort
    if (!gBonjourService) {
        NSString *svcName = tvBonjourServiceName(gDesktopName);
        gBonjourService = [[NSNetService alloc] initWithDomain:@"local." type:@"_rfb._tcp." name:svcName port:gPort];
        gBonjourService.delegate = gBonjourDelegate;
        [gBonjourService setTXTRecordData:bonjourTXTRecord()];
        [gBonjourService publish];
    } else {
        [gBonjourService stop];
        gBonjourService = nil;
        startBonjour();
        return;
    }

    // Optionally publish HTTP service when enabled
    if (gHttpPort > 0) {
        if (gBonjourHttpService) {
            [gBonjourHttpService stop];
            gBonjourHttpService = nil;
        }
        NSString *svcName = tvBonjourServiceName(gDesktopName);
        gBonjourHttpService = [[NSNetService alloc] initWithDomain:@"local."
                                                              type:@"_http._tcp."
                                                              name:svcName
                                                              port:gHttpPort];
        gBonjourHttpService.delegate = gBonjourDelegate;
        [gBonjourHttpService publish];
    }
}

#pragma mark - Control Socket

static int gTvCtlListenFd = -1;
static dispatch_source_t gTvCtlAcceptSource = NULL;

// Number of connected clients
static int gClientCount = 0;

// Subscribers for control change notifications (store as NSNumber wrapping fd)
static NSMutableSet<NSNumber *> *gTvCtlSubscribers = nil;
static dispatch_source_t gTvCtlDebounceTimer = NULL; // debounce timer for change notifications

// Global client states, populated via newClientHook/clientGoneHook.
// Key: 8-char client id; Value: immutable snapshot dictionary.
static NSMutableDictionary<NSString *, NSDictionary<NSString *, id> *> *gClientStates = nil;

// Generate a stable-length 8-char id for a given socket fd (deterministic per fd).
static NSString *tvGenerateClientId8(int fd) {
    static uint64_t sSeed = 0;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        // Mix boot hash and time for seed
        NSString *boot = tvBonjourServiceName(@""); // 8-char suffix only when baseName is empty
        uint64_t h = 1469598103934665603ULL;
        for (NSUInteger i = 0; i < boot.length; i++) {
            unichar c = [boot characterAtIndex:i];
            h ^= (uint8_t)(c & 0xFF);
            h *= 1099511628211ULL;
        }
        struct timeval tv;
        gettimeofday(&tv, NULL);
        h ^= (uint64_t)tv.tv_sec;
        h *= 1099511628211ULL;
        h ^= (uint64_t)tv.tv_usec;
        h *= 1099511628211ULL;
        sSeed = h;
    });
    uint64_t x = sSeed ^ (uint64_t)(uint32_t)fd;
    // xorshift mix to spread bits
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    uint32_t v = (uint32_t)(x & 0xFFFFFFFFu);
    return [NSString stringWithFormat:@"%08x", v];
}

static int tvSetNonBlocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags == -1)
        return -1;
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static void tvStopControlSocket(void) {
    if (gTvCtlAcceptSource) {
        dispatch_source_cancel(gTvCtlAcceptSource);
        gTvCtlAcceptSource = NULL;
    }

    if (gTvCtlDebounceTimer) {
        dispatch_source_cancel(gTvCtlDebounceTimer);
        gTvCtlDebounceTimer = NULL;
    }

    // Close all subscriber sockets and clear set
    if (gTvCtlSubscribers) {
        @synchronized(gTvCtlSubscribers) {
            for (NSNumber *num in gTvCtlSubscribers) {
                int fd = [num intValue];
                if (fd >= 0)
                    close(fd);
            }
            [gTvCtlSubscribers removeAllObjects];
        }
    }

    if (gTvCtlListenFd >= 0) {
        close(gTvCtlListenFd);
        gTvCtlListenFd = -1;
    }
}

static void tvStartControlSocketIfNeeded(void) {
    if (!gTvCtlPort || isRepeaterEnabled())
        return;
    if (gTvCtlAcceptSource)
        return; // already started

    // Create listening socket bound to 127.0.0.1:gTvCtlPort
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        TVPrintError("Control socket: socket() failed: %s", strerror(errno));
        exit(EXIT_FAILURE);
    }

    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
#ifdef SO_NOSIGPIPE
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
#endif

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_len = sizeof(addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)gTvCtlPort);
    addr.sin_addr.s_addr = htonl(gTvCtlBindAll ? INADDR_ANY : INADDR_LOOPBACK);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        TVPrintError("Control socket: bind 127.0.0.1:%d failed: %s", gTvCtlPort, strerror(errno));
        close(fd);
        exit(EXIT_FAILURE);
    }

    if (listen(fd, 8) < 0) {
        TVPrintError("Control socket: listen() failed: %s", strerror(errno));
        close(fd);
        exit(EXIT_FAILURE);
    }

    if (tvSetNonBlocking(fd) < 0) {
        TVPrintError("Control socket: failed to set O_NONBLOCK: %s", strerror(errno));
        // Continue anyway
    }

    gTvCtlListenFd = fd;

    static dispatch_queue_t sTVCtlQueue = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        sTVCtlQueue = dispatch_queue_create("com.82flex.trollvnc.control", DISPATCH_QUEUE_SERIAL_WITH_AUTORELEASE_POOL);
    });

    gTvCtlAcceptSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_READ, (uintptr_t)fd, 0, sTVCtlQueue);
    // Helper forward declaration
    void tvCtlHandleConnection(int cfd, struct sockaddr_in caddr);
    dispatch_source_set_event_handler(gTvCtlAcceptSource, ^{
        for (;;) {
            struct sockaddr_in caddr;
            socklen_t clen = sizeof(caddr);
            int cfd = accept(fd, (struct sockaddr *)&caddr, &clen);
            if (cfd < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK)
                    break;
                TVLog(@"Control socket: accept() error: %s", strerror(errno));
                break;
            }
            tvCtlHandleConnection(cfd, caddr);
        }
    });

    dispatch_source_set_cancel_handler(gTvCtlAcceptSource, ^{
        if (gTvCtlListenFd >= 0) {
            close(gTvCtlListenFd);
            gTvCtlListenFd = -1;
        }
    });

    dispatch_resume(gTvCtlAcceptSource);
    TVLog(@"Control socket listening on 127.0.0.1:%d (daemon=%@, repeater=%@)", gTvCtlPort,
          gIsDaemonMode ? @"YES" : @"NO", isRepeaterEnabled() ? @"YES" : @"NO");
}

// ---------- Control Protocol Implementation ----------

static void tvCtlWriteAll(int fd, const void *buf, size_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    size_t left = len;
    while (left > 0) {
        ssize_t n = send(fd, p, left, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (n == 0)
            break;
        p += (size_t)n;
        left -= (size_t)n;
    }
}

// --- Subscription helpers ---
static void tvCtlAddSubscriber(int fd) {
    if (fd < 0)
        return;
    (void)tvSetNonBlocking(fd);
#ifdef SO_NOSIGPIPE
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
#endif
    // Enable TCP keepalive to detect disappeared clients
    int kaOn = 1;
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &kaOn, sizeof(kaOn));
#ifdef TCP_KEEPALIVE
    int kaIdle = 10;
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPALIVE, &kaIdle, sizeof(kaIdle));
#endif
#ifdef TCP_KEEPINTVL
    int kaIntvl = 3;
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &kaIntvl, sizeof(kaIntvl));
#endif
#ifdef TCP_KEEPCNT
    int kaCnt = 3;
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &kaCnt, sizeof(kaCnt));
#endif
    if (!gTvCtlSubscribers)
        gTvCtlSubscribers = [[NSMutableSet alloc] init];
    @synchronized(gTvCtlSubscribers) {
        [gTvCtlSubscribers addObject:@(fd)];
    }
    TVLog(@"Control socket: subscribed fd=%d (total=%lu)", fd, (unsigned long)gTvCtlSubscribers.count);
}

static void tvCtlRemoveSubscriber(int fd, BOOL closeFd) {
    if (!gTvCtlSubscribers)
        return;
    @synchronized(gTvCtlSubscribers) {
        [gTvCtlSubscribers removeObject:@(fd)];
    }
    if (closeFd && fd >= 0)
        close(fd);
    TVLog(@"Control socket: unsubscribed fd=%d", fd);
}

static void tvCtlBroadcastChanged(void) {
    if (!gTvCtlSubscribers || gTvCtlSubscribers.count == 0)
        return;
    const char *msg = "changed\n";
    size_t len = strlen(msg);
    NSMutableArray<NSNumber *> *dead = [NSMutableArray array];
    @synchronized(gTvCtlSubscribers) {
        for (NSNumber *num in gTvCtlSubscribers) {
            int fd = [num intValue];
            ssize_t n;
        retry_send:
            n = send(fd, msg, len, 0);
            if (n == (ssize_t)len)
                continue; // success
            if (n < 0) {
                if (errno == EINTR)
                    goto retry_send;
                if (errno == EAGAIN || errno == EWOULDBLOCK)
                    continue; // buffer temporarily full — skip, not dead
                // Truly dead (EPIPE, ECONNRESET, EBADF, etc.)
                [dead addObject:num];
            }
            // Partial write (0 <= n < len): best-effort for 8-byte msg, not fatal
        }
        if (dead.count) {
            for (NSNumber *num in dead) {
                int fd = [num intValue];
                (void)close(fd);
                [gTvCtlSubscribers removeObject:num];
            }
        }
    }
}

static void tvCtlScheduleBroadcastChanged(void) {
    // Coalesce rapid changes to ~150ms
    if (gTvCtlDebounceTimer) {
        dispatch_source_cancel(gTvCtlDebounceTimer);
        gTvCtlDebounceTimer = NULL;
    }

    dispatch_queue_t q = dispatch_get_main_queue();
    dispatch_source_t t = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, q);
    gTvCtlDebounceTimer = t;

    uint64_t delayNs = (uint64_t)(150 * NSEC_PER_MSEC);
    dispatch_source_set_timer(t, dispatch_time(DISPATCH_TIME_NOW, delayNs), DISPATCH_TIME_FOREVER, delayNs / 4);
    dispatch_source_set_event_handler(t, ^{
        tvCtlBroadcastChanged();
        if (gTvCtlDebounceTimer) {
            dispatch_source_cancel(gTvCtlDebounceTimer);
            gTvCtlDebounceTimer = NULL;
        }
    });

    dispatch_resume(t);
}

static NSArray *tvSnapshotClients(void) {
    // Build JSON-safe snapshot
    NSMutableArray *arr = [NSMutableArray array];
    if (!gClientStates)
        return arr;

    NSDate *now = [NSDate date];
    // No dedicated lock object earlier; guard with @synchronized on dictionary itself.
    @synchronized(gClientStates) {
        [gClientStates enumerateKeysAndObjectsUsingBlock:^(NSString *key, NSDictionary *info, BOOL *stop) {
            (void)stop;
            NSString *cid = info[@"id"] ?: key;
            NSString *host = info[@"host"] ?: @"";
            NSNumber *viewOnly = info[@"viewOnly"] ?: @(NO);
            NSDate *connectAt = info[@"connectAt"];

            double t0 = connectAt ? [connectAt timeIntervalSince1970] : [now timeIntervalSince1970];
            double dur = [[NSNumber numberWithDouble:([now timeIntervalSince1970] - t0)] doubleValue];
            [arr addObject:@{
                @"id" : cid,
                @"host" : host,
                @"viewOnly" : viewOnly,
                @"connectedAt" : @(t0),
                @"durationSec" : @(dur)
            }];
        }];
    }

    return arr;
}

static NSData *tvCtlTSVForList(void) {
    NSArray *clients = tvSnapshotClients();
    NSMutableString *out = [NSMutableString string];

    // Header
    [out appendString:@"id\thost\tviewOnly\tconnectedAt\tdurationSec\n"];
    for (NSDictionary *c in clients) {
        NSString *cid = c[@"id"] ?: @"";
        NSString *host = c[@"host"] ?: @"";
        BOOL vo = [c[@"viewOnly"] boolValue];
        double t0 = [c[@"connectedAt"] doubleValue];
        double dur = [c[@"durationSec"] doubleValue];
        [out appendFormat:@"%@\t%@\t%@\t%.0f\t%.3f\n", cid, host, vo ? @"1" : @"0", t0, dur];
    }

    return [out dataUsingEncoding:NSUTF8StringEncoding];
}

static BOOL tvDisconnectClientById(NSString *cid, BOOL addToBlocklist) {
    if (!cid || cid.length == 0 || !gScreen)
        return NO;

    BOOL found = NO;
    rfbClientPtr cl = NULL;
    rfbClientIteratorPtr it = rfbGetClientIterator(gScreen);
    while ((cl = rfbClientIteratorNext(it))) {
        NSString *kid = tvGenerateClientId8(cl->sock);
        if (![kid isEqualToString:cid]) {
            continue;
        }

        found = YES;

        // Add to blocked hosts list if requested
        if (addToBlocklist) {
            do {
                NSString *host = (cl && cl->host) ? [NSString stringWithUTF8String:cl->host] : @"";
                if (!host.length) {
                    break;
                }

                if (!gBlockedHosts) {
                    gBlockedHosts = [[NSMutableSet alloc] init];
                }

                @synchronized(gBlockedHosts) {
                    [gBlockedHosts addObject:host];
                }

                TVLog(@"Blocked host: %@", host);
            } while (NO);
        }

        rfbCloseClient(cl);
        break;
    }

    rfbReleaseClientIterator(it);
    return found;
}

static NSData *tvCtlTextForKick(NSString *cid, BOOL addToBlocklist) {
    BOOL ok = tvDisconnectClientById(cid, addToBlocklist);
    const char *raw = ok ? "OK\n" : "NOT_FOUND\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

static BOOL tvDisconnectAllClients(void) {
    if (!gScreen)
        return NO;

    rfbClientPtr cl = NULL;
    rfbClientIteratorPtr it = rfbGetClientIterator(gScreen);
    while ((cl = rfbClientIteratorNext(it))) {
        rfbCloseClient(cl);
    }

    rfbReleaseClientIterator(it);
    return YES;
}

#pragma mark - App management

static LSApplicationWorkspace *tvAppWorkspace(void) {
    Class cls = NSClassFromString(@"LSApplicationWorkspace");
    if (!cls) {
        TVLog(@"LSApplicationWorkspace unavailable");
        return nil;
    }
    return [cls defaultWorkspace];
}

/// TSV: bundleId \t ten hien thi \t loai (User/System) \t phien ban
static NSData *tvCtlTSVForApps(void) {
    LSApplicationWorkspace *ws = tvAppWorkspace();
    if (!ws)
        return [@"ERR Unavailable\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSMutableString *out = [NSMutableString string];
    NSArray<LSApplicationProxy *> *apps = [ws allApplications];
    for (LSApplicationProxy *app in apps) {
        NSString *bid = app.applicationIdentifier ?: @"";
        if (bid.length == 0)
            continue;
        NSString *name = app.localizedName ?: @"";
        NSString *type = app.applicationType ?: @"";
        NSString *ver = app.shortVersionString ?: @"";
        // A tab inside the name would break the TSV layout.
        name = [name stringByReplacingOccurrencesOfString:@"\t" withString:@" "];
        [out appendFormat:@"%@\t%@\t%@\t%@\n", bid, name, type, ver];
    }
    TVLog(@"Control socket: listed %lu applications", (unsigned long)apps.count);
    return [out dataUsingEncoding:NSUTF8StringEncoding];
}

static NSData *tvCtlLaunchApp(NSString *bundleId) {
    if (bundleId.length == 0)
        return [@"ERR MissingBundleID\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = NO;
    int sbsErr = -1; // mã lỗi SpringBoardServices gần nhất (để chẩn đoán)

    // 1) SpringBoardServices — đường đáng tin nhất khi gọi từ daemon root. Ưu
    //    tiên bản CÓ launch options (iOS mới trả lỗi với bản không options).
    //    entitlement com.apple.springboard.launchapplications đã có sẵn.
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsLaunchOpts)(CFStringRef, CFDictionaryRef, Boolean) =
            (int (*)(CFStringRef, CFDictionaryRef, Boolean))dlsym(
                h, "SBSLaunchApplicationWithIdentifierAndLaunchOptions");
        if (sbsLaunchOpts) {
            sbsErr = sbsLaunchOpts((__bridge CFStringRef)bundleId, NULL, false);
            ok = (sbsErr == 0);
        }
        if (!ok) {
            int (*sbsLaunch)(CFStringRef, Boolean) =
                (int (*)(CFStringRef, Boolean))dlsym(h, "SBSLaunchApplicationWithIdentifier");
            if (sbsLaunch) {
                sbsErr = sbsLaunch((__bridge CFStringRef)bundleId, false);
                ok = (sbsErr == 0);
            }
        }
    }

    // 2) Fallback: LSApplicationWorkspace (đôi khi trả NO dù đã mở, nên để sau).
    if (!ok) {
        LSApplicationWorkspace *ws = tvAppWorkspace();
        if ([ws respondsToSelector:@selector(openApplicationWithBundleID:)])
            ok = [ws openApplicationWithBundleID:bundleId];
    }

    TVLog(@"Control socket: launch %@ -> %@ (sbsErr=%d)", bundleId, ok ? @"OK" : @"FAIL", sbsErr);
    if (ok)
        return [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
    // Kèm mã lỗi SBS để biết vì sao (vd not-found, permission, đã chạy...).
    NSString *msg = [NSString stringWithFormat:@"ERR LaunchFailed sbs=%d\n", sbsErr];
    return [msg dataUsingEncoding:NSUTF8StringEncoding];
}

static NSData *tvCtlTerminateApp(NSString *bundleId) {
    if (bundleId.length == 0)
        return [@"ERR MissingBundleID\n" dataUsingEncoding:NSUTF8StringEncoding];

    pid_t pid = 0;
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsPid)(CFStringRef, pid_t *) =
            (int (*)(CFStringRef, pid_t *))dlsym(h, "SBSProcessIDForDisplayIdentifier");
        if (sbsPid)
            sbsPid((__bridge CFStringRef)bundleId, &pid);
    }

    if (pid <= 0) {
        TVLog(@"Control socket: terminate %@ -> not running", bundleId);
        return [@"NOT_RUNNING\n" dataUsingEncoding:NSUTF8StringEncoding];
    }

    BOOL ok = (kill(pid, SIGKILL) == 0);
    TVLog(@"Control socket: terminate %@ (pid %d) -> %@", bundleId, pid, ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR KillFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

#pragma mark - Clipboard

// `clipset <size>` — đọc đúng `size` byte payload (đã gồm phần bị vòng đọc dòng
// lệnh nuốt trước) rồi đặt làm clipboard. Tái dùng ClipboardManager sẵn có —
// chính lớp mà đường clipboard VNC dùng — thay vì gọi thẳng UIPasteboard, cho
// nhất quán và tránh vòng lặp echo (setStringFromRemote bỏ qua callback + thông
// báo hệ thống một lần).
static NSData *tvCtlSetClipboard(int cfd, NSString *spec, const uint8_t *pending,
                                 size_t pendingLength) {
    long long size = [[spec stringByTrimmingCharactersInSet:
                          [NSCharacterSet whitespaceAndNewlineCharacterSet]] longLongValue];
    if (size < 0 || size > 16 * 1024 * 1024) // 16 MiB dư sức cho chữ; lớn hơn là gõ nhầm
        return [@"ERR BadSize\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSMutableData *buffer = [NSMutableData dataWithCapacity:(NSUInteger)size];

    // Phần đã bị nuốt trước phải ghi vào trước tiên.
    if (pendingLength > 0) {
        size_t take = (size_t)MIN((uint64_t)pendingLength, (uint64_t)size);
        [buffer appendBytes:pending length:take];
    }

    struct timeval tv;
    tv.tv_sec = 15;
    tv.tv_usec = 0;
    setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint8_t chunk[65536];
    while ((long long)buffer.length < size) {
        size_t want = (size_t)MIN((uint64_t)sizeof(chunk), (uint64_t)(size - buffer.length));
        ssize_t n = recv(cfd, chunk, want, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (n == 0)
            break;
        [buffer appendBytes:chunk length:(NSUInteger)n];
    }

    if ((long long)buffer.length != size)
        return [@"ERR Incomplete\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSString *text = [[NSString alloc] initWithData:buffer encoding:NSUTF8StringEncoding];
    if (!text)
        text = @"";
    [[ClipboardManager sharedManager] setStringFromRemote:text];

    TVLog(@"Control socket: clipset (%lld bytes)", size);
    NSString *ok = [NSString stringWithFormat:@"OK %lld\n", size];
    return [ok dataUsingEncoding:NSUTF8StringEncoding];
}

// `clipget` — trả về `OK <n>\n` rồi đúng n byte nội dung clipboard (UTF-8).
static NSData *tvCtlGetClipboard(void) {
    NSString *text = [[ClipboardManager sharedManager] currentString] ?: @"";
    NSData *body = [text dataUsingEncoding:NSUTF8StringEncoding] ?: [NSData data];
    NSMutableData *resp = [NSMutableData data];
    NSString *header = [NSString stringWithFormat:@"OK %lu\n", (unsigned long)body.length];
    [resp appendData:[header dataUsingEncoding:NSUTF8StringEncoding]];
    [resp appendData:body];
    return resp;
}

#pragma mark - Photo library

// `savephoto <path>` — nạp một file ảnh/video đã có trên máy vào Thư viện Ảnh.
// Chép vào thư mục thường không đủ: iOS quản ảnh bằng CSDL riêng, phải qua
// PHPhotoLibrary thì ảnh mới dùng được trong app khác.
//
// Quan trọng: phải **xin quyền trước**. Gọi thẳng performChanges khi chưa được
// cấp quyền có thể làm framework Photos abort (đóng socket, không trả lời) thay
// vì báo lỗi. Xin quyền trước biến crash thành "ERR Denied" đọc được.
static NSData *tvCtlSavePhoto(NSString *path) {
    path = [path stringByTrimmingCharactersInSet:
               [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (path.length == 0 || ![path hasPrefix:@"/"] ||
        [path rangeOfString:@".."].location != NSNotFound)
        return [@"ERR BadPath\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (![[NSFileManager defaultManager] fileExistsAtPath:path])
        return [@"ERR NotFound\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSURL *fileURL = [NSURL fileURLWithPath:path];
    NSString *ext = path.pathExtension.lowercaseString;
    BOOL isVideo = [@[ @"mov", @"mp4", @"m4v" ] containsObject:ext];

    // 1) Xin quyền (thêm-only) và CHỜ kết quả trước khi đụng tới thư viện.
    __block PHAuthorizationStatus status = PHAuthorizationStatusNotDetermined;
    dispatch_semaphore_t authDone = dispatch_semaphore_create(0);
    if (@available(iOS 14, *)) {
        [PHPhotoLibrary requestAuthorizationForAccessLevel:PHAccessLevelAddOnly
                                                   handler:^(PHAuthorizationStatus s) {
            status = s;
            dispatch_semaphore_signal(authDone);
        }];
    } else {
        [PHPhotoLibrary requestAuthorization:^(PHAuthorizationStatus s) {
            status = s;
            dispatch_semaphore_signal(authDone);
        }];
    }
    dispatch_semaphore_wait(authDone,
        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(10 * NSEC_PER_SEC)));

    if (status != PHAuthorizationStatusAuthorized) {
        TVLog(@"Control socket: savephoto %@ -> chưa được cấp quyền (status=%ld)",
              path, (long)status);
        NSString *msg = [NSString stringWithFormat:@"ERR Denied status=%ld\n",
                                                   (long)status];
        return [msg dataUsingEncoding:NSUTF8StringEncoding];
    }

    // 2) Nạp asset. Bọc @try để một NSException của Photos không giết daemon.
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    __block BOOL ok = NO;
    __block NSError *err = nil;
    @try {
        [[PHPhotoLibrary sharedPhotoLibrary] performChanges:^{
            if (isVideo) {
                // addResourceWithType bỏ qua bước kiểm tra tương thích gắt của
                // creationRequestForAssetFromVideoAtFileURL: — bước đó từ chối cả
                // video H.264 hợp lệ nếu thiếu track audio, level cao, hoặc moov
                // atom nằm cuối file (không faststart).
                PHAssetCreationRequest *req = [PHAssetCreationRequest creationRequestForAsset];
                [req addResourceWithType:PHAssetResourceTypeVideo
                                 fileURL:fileURL
                                 options:nil];
            } else {
                [PHAssetCreationRequest creationRequestForAssetFromImageAtFileURL:fileURL];
            }
        } completionHandler:^(BOOL success, NSError *error) {
            ok = success;
            err = error;
            dispatch_semaphore_signal(done);
        }];
    } @catch (NSException *ex) {
        TVLog(@"Control socket: savephoto %@ -> exception %@", path, ex.reason);
        NSString *msg = [NSString stringWithFormat:@"ERR %@\n",
                                                   ex.reason ?: @"PhotosException"];
        return [msg dataUsingEncoding:NSUTF8StringEncoding];
    }

    // Chờ nạp xong (giới hạn 30s) để trả lời đúng trạng thái cho PC.
    if (dispatch_semaphore_wait(done,
            dispatch_time(DISPATCH_TIME_NOW, (int64_t)(30 * NSEC_PER_SEC))) != 0)
        return [@"ERR Timeout\n" dataUsingEncoding:NSUTF8StringEncoding];

    if (ok) {
        TVLog(@"Control socket: savephoto %@ -> OK (%@)", path,
              isVideo ? @"video" : @"ảnh");
        return [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
    }

    TVLog(@"Control socket: savephoto %@ -> FAIL %@", path, err);
    NSString *msg = err.localizedDescription.length
        ? [NSString stringWithFormat:@"ERR %@\n", err.localizedDescription]
        : @"ERR Failed\n";
    return [msg dataUsingEncoding:NSUTF8StringEncoding];
}

#pragma mark - Power

// `respring` — kill SpringBoard; backboardd tự bật lại. Gỡ giao diện treo mà
// KHÔNG reboot, nên giữ nguyên jailbreak trên máy semi-untethered (Dopamine).
static NSData *tvCtlRespring(void) {
    pid_t pid = 0;
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsPid)(CFStringRef, pid_t *) =
            (int (*)(CFStringRef, pid_t *))dlsym(h, "SBSProcessIDForDisplayIdentifier");
        if (sbsPid)
            sbsPid((__bridge CFStringRef)@"com.apple.springboard", &pid);
    }
    if (pid <= 0)
        return [@"ERR NoSpringBoard\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = (kill(pid, SIGKILL) == 0);
    TVLog(@"Control socket: respring (SpringBoard pid %d) -> %@", pid, ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR RespringFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

// Ghi chú: reboot kernel thật sự KHÔNG làm được từ daemon này — reboot(2) bị AMFI
// chặn (EPERM) dù root, reboot3() bị gate (trả cùng một mã bất kể cờ), và
// SBSRelaunchAction chỉ respring. Với farm Dopamine reboot còn làm mất jailbreak
// nên cũng không nên dùng. Chỉ giữ `respring` ở trên.

#pragma mark - Reboot

static NSData *tvCtlReboot(void) {
    dlopen("/System/Library/PrivateFrameworks/FrontBoardServices.framework/"
           "FrontBoardServices", RTLD_LAZY);
    Class serviceClass = NSClassFromString(@"FBSSystemService");
    SEL sharedSelector = NSSelectorFromString(@"sharedService");
    SEL rebootSelector = NSSelectorFromString(@"reboot");
    if (!serviceClass || ![serviceClass respondsToSelector:sharedSelector])
        return [@"ERR RebootAPINotFound\n" dataUsingEncoding:NSUTF8StringEncoding];

    id service = ((id (*)(id, SEL))objc_msgSend)((id)serviceClass, sharedSelector);
    if (!service || ![service respondsToSelector:rebootSelector])
        return [@"ERR RebootAPINotFound\n" dataUsingEncoding:NSUTF8StringEncoding];

    // Gọi runtime để mọi scheme build được dù SDK không export private class.
    // Trả OK trước khi thiết bị ngắt kết nối để GUI PC không báo lỗi giả.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_MSEC),
                   dispatch_get_main_queue(), ^{
                       ((void (*)(id, SEL))objc_msgSend)(service, rebootSelector);
                   });
    return [@"OK rebooting\n" dataUsingEncoding:NSUTF8StringEncoding];
}

#pragma mark - Wi-Fi IP loss watchdog

static dispatch_source_t gWiFiIPWatchTimer = NULL;
static BOOL gWiFiHadIPv4 = NO;
static BOOL gWiFiRebootRequested = NO;
static int gWiFiMissingChecks = 0;

static NSString *tvCurrentWiFiIPv4(void) {
    struct ifaddrs *interfaces = NULL;
    if (getifaddrs(&interfaces) != 0)
        return @"";
    NSString *result = @"";
    for (struct ifaddrs *item = interfaces; item; item = item->ifa_next) {
        if (!item->ifa_addr || item->ifa_addr->sa_family != AF_INET ||
            strcmp(item->ifa_name, "en0") != 0)
            continue;
        struct sockaddr_in *sin = (struct sockaddr_in *)item->ifa_addr;
        uint32_t hostAddress = ntohl(sin->sin_addr.s_addr);
        // Không coi loopback, 0.0.0.0 hay link-local 169.254/16 là IP Wi-Fi đã sẵn sàng.
        if (hostAddress == 0 || (hostAddress >> 24) == 127 ||
            (hostAddress & 0xFFFF0000U) == 0xA9FE0000U)
            continue;
        char address[INET_ADDRSTRLEN] = {0};
        if (inet_ntop(AF_INET, &sin->sin_addr, address, sizeof(address)))
            result = [NSString stringWithUTF8String:address] ?: @"";
        break;
    }
    freeifaddrs(interfaces);
    return result;
}

static void tvCheckWiFiIP(void) {
    NSString *ip = tvCurrentWiFiIPv4();
    if (ip.length > 0) {
        if (!gWiFiHadIPv4)
            TVLog(@"Wi-Fi IP watchdog armed after receiving %@", ip);
        else if (gWiFiMissingChecks > 0)
            TVLog(@"Wi-Fi IP restored: %@", ip);
        gWiFiHadIPv4 = YES;
        gWiFiMissingChecks = 0;
        return;
    }

    // Quan trọng: trước khi máy từng có IP, không reboot dù chờ bao lâu.
    if (!gWiFiHadIPv4 || gWiFiRebootRequested)
        return;
    ++gWiFiMissingChecks;
    TVLog(@"Wi-Fi IP missing after previously online (%d/3)", gWiFiMissingChecks);
    if (gWiFiMissingChecks < 3)
        return;

    gWiFiRebootRequested = YES;
    NSData *response = tvCtlReboot();
    NSString *message = [[NSString alloc] initWithData:response
                                              encoding:NSUTF8StringEncoding] ?: @"";
    TVLog(@"Wi-Fi IP missing for 30s -> reboot: %@", message);
}

static void tvStartWiFiIPWatchdog(void) {
    if (gWiFiIPWatchTimer)
        return;
    dispatch_queue_t queue = dispatch_queue_create(
        "com.controlios.server.wifi-ip-watch", DISPATCH_QUEUE_SERIAL);
    gWiFiIPWatchTimer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, queue);
    dispatch_source_set_timer(
        gWiFiIPWatchTimer,
        dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC),
        10 * NSEC_PER_SEC,
        1 * NSEC_PER_SEC);
    dispatch_source_set_event_handler(gWiFiIPWatchTimer, ^{
        @autoreleasepool { tvCheckWiFiIP(); }
    });
    dispatch_resume(gWiFiIPWatchTimer);
    TVLog(@"Wi-Fi IP watchdog started (reboot only after IP was acquired, 30s loss)");
}

static NSData *tvCtlShutdown(void) {
    dlopen("/System/Library/PrivateFrameworks/FrontBoardServices.framework/"
           "FrontBoardServices", RTLD_LAZY);
    Class serviceClass = NSClassFromString(@"FBSSystemService");
    SEL sharedSelector = NSSelectorFromString(@"sharedService");
    SEL shutdownSelector = NSSelectorFromString(@"shutdown");
    if (!serviceClass || ![serviceClass respondsToSelector:sharedSelector])
        return [@"ERR ShutdownAPINotFound\n" dataUsingEncoding:NSUTF8StringEncoding];

    id service = ((id (*)(id, SEL))objc_msgSend)((id)serviceClass, sharedSelector);
    if (!service || ![service respondsToSelector:shutdownSelector])
        return [@"ERR ShutdownAPINotFound\n" dataUsingEncoding:NSUTF8StringEncoding];

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_MSEC),
                   dispatch_get_main_queue(), ^{
                       ((void (*)(id, SEL))objc_msgSend)(service, shutdownSelector);
                   });
    return [@"OK shutting down\n" dataUsingEncoding:NSUTF8StringEncoding];
}

static BOOL tvCtlNotifyState(const char *name) {
    int token = 0;
    uint64_t state = 0;
    if (notify_register_check(name, &token) != NOTIFY_STATUS_OK)
        return NO;
    int status = notify_get_state(token, &state);
    notify_cancel(token);
    return status == NOTIFY_STATUS_OK && state != 0;
}

static BOOL tvReadAccurateLockState(BOOL *keychainLocked, BOOL *passcodeSet) {
    BOOL locked = tvCtlNotifyState("com.apple.springboard.lockstate");
    BOOL keychain = NO;
    BOOL passcode = NO;
    void *sbs = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices",
                       RTLD_LAZY | RTLD_LOCAL);
    auto serverPort = sbs ? (mach_port_t (*)(void))dlsym(sbs, "SBSSpringBoardServerPort") : NULL;
    auto getLockStatus = sbs ? (void (*)(mach_port_t, BOOL *, BOOL *))
        dlsym(sbs, "SBGetScreenLockStatus") : NULL;
    if (serverPort && getLockStatus) {
        mach_port_t port = serverPort();
        if (port != MACH_PORT_NULL)
            getLockStatus(port, &locked, &passcode);
    }
    void *mkb = dlopen("/System/Library/PrivateFrameworks/MobileKeyBag.framework/MobileKeyBag",
                       RTLD_LAZY | RTLD_LOCAL);
    auto getDeviceLockState = mkb ? (int (*)(CFDictionaryRef))
        dlsym(mkb, "MKBGetDeviceLockState") : NULL;
    if (getDeviceLockState) {
        int state = getDeviceLockState(NULL);
        keychain = state == 1 || state == 2;
    }
    if (keychainLocked) *keychainLocked = keychain;
    if (passcodeSet) *passcodeSet = passcode;
    return locked;
}

static NSData *tvCtlWakeIfLocked(void) {
    BOOL keychainLocked = NO;
    BOOL passcodeSet = NO;
    BOOL locked = tvReadAccurateLockState(&keychainLocked, &passcodeSet);
    BOOL blanked = tvCtlNotifyState("com.apple.springboard.hasBlankedScreen");
    if (!locked && !blanked)
        return [@"OK unlocked\n" dataUsingEncoding:NSUTF8StringEncoding];

    tvRecordHomeAudit(@"wakeiflocked",
                      [NSString stringWithFormat:@"locked=%d blanked=%d keychain=%d passcode=%d",
                       locked, blanked, keychainLocked, passcodeSet]);
    [[STHIDEventGenerator sharedGenerator] menuPress];
    TVLog(@"Control socket: wakeiflocked -> Home (locked=%d blanked=%d)", locked, blanked);
    return [(locked ? @"OK home locked\n" : @"OK home blanked\n")
        dataUsingEncoding:NSUTF8StringEncoding];
}

// Open Control Center through the Accessibility/SpringBoard route used by
// AssistiveTouch. This is independent of synthetic edge gestures.
static BOOL tvOpenControlCenterThroughAccessibility(void) {
    void *handle = dlopen("/System/Library/PrivateFrameworks/AccessibilityUtilities.framework/AccessibilityUtilities",
                          RTLD_LAZY | RTLD_LOCAL);
    if (!handle)
        return NO;

    Class serverClass = NSClassFromString(@"AXSpringBoardServer");
    SEL serverSelector = NSSelectorFromString(@"server");
    if (!serverClass || ![serverClass respondsToSelector:serverSelector])
        return NO;

    id server = ((id (*)(id, SEL))objc_msgSend)((id)serverClass, serverSelector);
    if (!server)
        return NO;

    SEL showSelector = NSSelectorFromString(@"showControlCenter:");
    if (![server respondsToSelector:showSelector])
        return NO;

    BOOL opened = ((BOOL (*)(id, SEL, BOOL))objc_msgSend)(server, showSelector, YES);
    TVLog(@"Control socket: controlcenter -> AXSpringBoardServer showControlCenter:YES (%d)",
          opened);
    return opened;
}

static NSData *tvCtlControlCenter(void) {
    __block BOOL opened = NO;
    void (^action)(void) = ^{
        opened = tvOpenControlCenterThroughAccessibility();
    };
    if ([NSThread isMainThread])
        action();
    else
        dispatch_sync(dispatch_get_main_queue(), action);

    return [(opened ? @"OK\n" : @"ERR ControlCenterUnavailable\n")
        dataUsingEncoding:NSUTF8StringEncoding];
}

static NSData *tvCtlRotationLock(NSString *requestedState) {
    NSString *state = [[requestedState
        stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]
        lowercaseString];
    if (![state isEqualToString:@"on"] && ![state isEqualToString:@"off"] &&
        ![state isEqualToString:@"toggle"] && ![state isEqualToString:@"status"])
        return [@"ERR Usage rotationlock on|off|toggle|status\n"
            dataUsingEncoding:NSUTF8StringEncoding];

    void *handle = dlopen("/System/Library/PrivateFrameworks/AccessibilityUtilities.framework/AccessibilityUtilities",
                          RTLD_LAZY | RTLD_LOCAL);
    Class serverClass = handle ? NSClassFromString(@"AXSpringBoardServer") : Nil;
    SEL serverSelector = NSSelectorFromString(@"server");
    if (!serverClass || ![serverClass respondsToSelector:serverSelector])
        return [@"ERR RotationLockUnavailable\n" dataUsingEncoding:NSUTF8StringEncoding];

    id server = ((id (*)(id, SEL))objc_msgSend)((id)serverClass, serverSelector);
    SEL getSelector = NSSelectorFromString(@"isOrientationLocked");
    SEL setSelector = NSSelectorFromString(@"setOrientationLocked:");
    if (!server || ![server respondsToSelector:getSelector] ||
        ![server respondsToSelector:setSelector])
        return [@"ERR RotationLockUnavailable\n" dataUsingEncoding:NSUTF8StringEncoding];

    __block BOOL locked = NO;
    void (^action)(void) = ^{
        locked = ((BOOL (*)(id, SEL))objc_msgSend)(server, getSelector);
        if (![state isEqualToString:@"status"]) {
            BOOL desired = [state isEqualToString:@"toggle"] ? !locked :
                           [state isEqualToString:@"on"];
            ((void (*)(id, SEL, BOOL))objc_msgSend)(server, setSelector, desired);
            locked = desired;
        }
    };
    if ([NSThread isMainThread])
        action();
    else
        dispatch_sync(dispatch_get_main_queue(), action);

    TVLog(@"Control socket: rotationlock %@ -> %@", state, locked ? @"on" : @"off");
    return [[NSString stringWithFormat:@"OK %@\n", locked ? @"on" : @"off"]
        dataUsingEncoding:NSUTF8StringEncoding];
}

static id gTvBKSAppStateMonitor = nil;
static NSString *gTvBKSFrontmostBundleID = nil;

static void tvInstallBKSFrontmostMonitor(void) {
    void *handle = dlopen("/System/Library/PrivateFrameworks/BackBoardServices.framework/BackBoardServices",
                          RTLD_LAZY | RTLD_LOCAL);
    Class monitorClass = handle ? NSClassFromString(@"BKSApplicationStateMonitor") : Nil;
    if (!monitorClass) {
        TVLog(@"BKS foreground monitor unavailable; AX fallback retained");
        return;
    }
    id monitor = [[monitorClass alloc] init];
    if (![monitor respondsToSelector:@selector(setHandler:)]) {
        TVLog(@"BKS foreground monitor has no handler API; AX fallback retained");
        return;
    }
    [monitor setHandler:^(NSDictionary *appInfo) {
        NSString *bundleID = appInfo[@"SBApplicationStateDisplayIDKey"];
        if (!bundleID.length || [appInfo[@"BKSApplicationStateExtensionKey"] boolValue])
            return;
        BOOL frontmost = [appInfo[@"BKSApplicationStateAppIsFrontmost"] boolValue];
        @synchronized ([NSObject class]) {
            if (frontmost)
                gTvBKSFrontmostBundleID = [bundleID copy];
            else if ([gTvBKSFrontmostBundleID isEqualToString:bundleID])
                gTvBKSFrontmostBundleID = nil;
        }
    }];
    gTvBKSAppStateMonitor = monitor;
    TVLog(@"BKS foreground monitor installed");
}

static NSData *tvCtlFrontmostApp(void) {
    @synchronized ([NSObject class]) {
        if (gTvBKSFrontmostBundleID.length) {
            return [[NSString stringWithFormat:@"OK %@\n", gTvBKSFrontmostBundleID]
                    dataUsingEncoding:NSUTF8StringEncoding];
        }
    }
    void *handle = dlopen("/System/Library/PrivateFrameworks/AccessibilityUtilities.framework/AccessibilityUtilities",
                          RTLD_LAZY | RTLD_LOCAL);
    Class serverClass = handle ? NSClassFromString(@"AXSpringBoardServer") : Nil;
    SEL serverSelector = NSSelectorFromString(@"server");
    if (!serverClass || ![serverClass respondsToSelector:serverSelector])
        return [@"ERR FrontmostUnavailable\n" dataUsingEncoding:NSUTF8StringEncoding];

    id server = ((id (*)(id, SEL))objc_msgSend)((id)serverClass, serverSelector);
    SEL focusedSelector = NSSelectorFromString(@"focusedApps");
    if (!server || ![server respondsToSelector:focusedSelector])
        return [@"ERR FrontmostUnavailable\n" dataUsingEncoding:NSUTF8StringEncoding];

    __block NSString *bundleID = nil;
    void (^action)(void) = ^{
        id apps = ((id (*)(id, SEL))objc_msgSend)(server, focusedSelector);
        if (![apps isKindOfClass:[NSArray class]])
            return;
        SEL bundleSelector = NSSelectorFromString(@"bundleIdentifier");
        SEL primarySelector = NSSelectorFromString(@"isLayoutPrimary");
        for (id app in (NSArray *)apps) {
            if (![app respondsToSelector:bundleSelector])
                continue;
            BOOL primary = ![app respondsToSelector:primarySelector] ||
                ((BOOL (*)(id, SEL))objc_msgSend)(app, primarySelector);
            NSString *candidate = ((id (*)(id, SEL))objc_msgSend)(app, bundleSelector);
            if (candidate.length && (primary || !bundleID)) {
                bundleID = [candidate copy];
                if (primary)
                    break;
            }
        }
    };
    if ([NSThread isMainThread])
        action();
    else
        dispatch_sync(dispatch_get_main_queue(), action);

    if (!bundleID.length)
        return [@"OK none\n" dataUsingEncoding:NSUTF8StringEncoding];
    TVLog(@"Control socket: frontmost -> %@", bundleID);
    return [[NSString stringWithFormat:@"OK %@\n", bundleID]
        dataUsingEncoding:NSUTF8StringEncoding];
}

#pragma mark - TrollStore system-wide touch blocker

static const char *kTvTouchLockNotification = "com.controlios.touchlock.changed";
static NSString *const kTvControlIOSBundleID = @"com.controlios.app";

static BOOL tvSetTouchLockNotifyState(BOOL enabled) {
    int token = 0;
    if (notify_register_check(kTvTouchLockNotification, &token) != NOTIFY_STATUS_OK)
        return NO;
    int status = notify_set_state(token, enabled ? 1 : 0);
    if (status == NOTIFY_STATUS_OK)
        status = notify_post(kTvTouchLockNotification);
    notify_cancel(token);
    return status == NOTIFY_STATUS_OK;
}

static BOOL tvGetTouchLockNotifyState(void) {
    int token = 0;
    uint64_t state = 0;
    if (notify_register_check(kTvTouchLockNotification, &token) != NOTIFY_STATUS_OK)
        return NO;
    int status = notify_get_state(token, &state);
    notify_cancel(token);
    return status == NOTIFY_STATUS_OK && state != 0;
}

static NSData *tvCtlHomeAudit(BOOL clear) {
    NSMutableArray<NSString *> *entries = tvHomeAuditEntries();
    @synchronized (entries) {
        if (clear) {
            [entries removeAllObjects];
            return [@"OK cleared\n" dataUsingEncoding:NSUTF8StringEncoding];
        }
        NSString *body = entries.count ? [entries componentsJoinedByString:@"\n"] : @"(empty)";
        return [[NSString stringWithFormat:@"OK %lu\n%@\n", (unsigned long)entries.count, body]
                dataUsingEncoding:NSUTF8StringEncoding];
    }
}

typedef boolean_t (^TVIOHIDEventFilterBlock)(void *, void *, void *, IOHIDEventRef);

static BOOL tvHIDEventContainsHome(IOHIDEventRef event) {
    if (!event)
        return NO;
    if (IOHIDEventGetType(event) == kIOHIDEventTypeKeyboard &&
        IOHIDEventGetIntegerValue(event, kIOHIDEventFieldKeyboardUsagePage) == kHIDPage_Consumer &&
        IOHIDEventGetIntegerValue(event, kIOHIDEventFieldKeyboardUsage) == kHIDUsage_Csmr_Menu)
        return YES;

    CFArrayRef children = IOHIDEventGetChildren(event);
    if (!children)
        return NO;
    for (CFIndex index = 0; index < CFArrayGetCount(children); ++index) {
        if (tvHIDEventContainsHome((IOHIDEventRef)CFArrayGetValueAtIndex(children, index)))
            return YES;
    }
    return NO;
}

static BOOL tvHIDEventContainsDigitizer(IOHIDEventRef event) {
    if (!event)
        return NO;
    if (IOHIDEventGetType(event) == kIOHIDEventTypeDigitizer)
        return YES;
    CFArrayRef children = IOHIDEventGetChildren(event);
    if (!children)
        return NO;
    for (CFIndex index = 0; index < CFArrayGetCount(children); ++index) {
        if (tvHIDEventContainsDigitizer((IOHIDEventRef)CFArrayGetValueAtIndex(children, index)))
            return YES;
    }
    return NO;
}

static IOHIDEventSystemClientRef gTvTouchLockHIDClient = NULL;
static const uint64_t kTvBlockedPhysicalHomeSenderID = 0x1000001d4ULL;
static const uint64_t kTvRemoteSenderLegacy = 0x8000000817319371ULL;
static const uint64_t kTvRemoteSenderModern = 0x8000000817319372ULL;

static void tvInstallTouchLockHIDFilter(void) {
    void *handle = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",
                          RTLD_LAZY | RTLD_LOCAL);
    if (!handle) {
        TVLog(@"Touch lock HID filter: IOKit unavailable");
        return;
    }

    auto createClient = (IOHIDEventSystemClientRef (*)(CFAllocatorRef))
        dlsym(handle, "IOHIDEventSystemClientCreate");
    auto registerFilter = (void (*)(IOHIDEventSystemClientRef, TVIOHIDEventFilterBlock,
                                    void *, void *))
        dlsym(handle, "IOHIDEventSystemClientRegisterEventFilterBlock");
    auto scheduleClient = (void (*)(IOHIDEventSystemClientRef, CFRunLoopRef, CFStringRef))
        dlsym(handle, "IOHIDEventSystemClientScheduleWithRunLoop");
    auto getSenderID = (uint64_t (*)(IOHIDEventRef))
        dlsym(handle, "IOHIDEventGetSenderID");
    if (!createClient || !registerFilter || !scheduleClient) {
        TVLog(@"Touch lock HID filter: required API unavailable");
        return;
    }

    gTvTouchLockHIDClient = createClient(kCFAllocatorDefault);
    if (!gTvTouchLockHIDClient) {
        TVLog(@"Touch lock HID filter: client creation failed");
        return;
    }

    registerFilter(gTvTouchLockHIDClient,
                   ^boolean_t(void *target, void *refcon, void *sender, IOHIDEventRef event) {
        (void)target;
        (void)refcon;
        BOOL containsHome = tvHIDEventContainsHome(event);
        BOOL containsDigitizer = tvHIDEventContainsDigitizer(event);
        uint64_t senderID = (containsHome || containsDigitizer) && getSenderID
            ? getSenderID(event) : 0;
        BOOL remoteEvent = senderID == kTvRemoteSenderLegacy ||
            senderID == kTvRemoteSenderModern;
        BOOL blockedPhysicalHome = containsHome &&
            senderID == kTvBlockedPhysicalHomeSenderID;
        BOOL blockedPhysicalTouch = containsDigitizer && !remoteEvent;
        BOOL consumed = (blockedPhysicalHome || blockedPhysicalTouch) &&
            tvGetTouchLockNotifyState();
        if (containsHome) {
            tvRecordHomeAudit(@"hid-home",
                              [NSString stringWithFormat:@"sender=%p senderID=0x%llx down=%lld physicalTarget=%d consumed=%d",
                               sender, (unsigned long long)senderID,
                               (long long)IOHIDEventGetIntegerValue(event, kIOHIDEventFieldKeyboardDown),
                               blockedPhysicalHome, consumed]);
        }
        if (containsDigitizer && tvGetTouchLockNotifyState() && !remoteEvent) {
            TVLog(@"Touch lock: physical digitizer blocked senderID=0x%llx",
                  (unsigned long long)senderID);
        }
        // Block only the noisy physical Home source while touch lock is on.
        // Synthetic Home from the authenticated VNC client remains available.
        return consumed;
    }, NULL, NULL);
    scheduleClient(gTvTouchLockHIDClient, CFRunLoopGetMain(), kCFRunLoopCommonModes);
    TVLog(@"Touch lock HID filter installed (local touch + physical Home sender 0x%llx)",
          (unsigned long long)kTvBlockedPhysicalHomeSenderID);
}

static int gTvLockResetToken = 0;
static int gTvBlankResetToken = 0;

static void tvInstallLockTouchResetObservers(void) {
    notify_register_dispatch("com.apple.springboard.lockstate", &gTvLockResetToken,
                             dispatch_get_main_queue(), ^(int token) {
        (void)token;
        if (tvReadAccurateLockState(NULL, NULL)) {
            [[STHIDEventGenerator sharedGenerator] dispatchHandResetEvent];
            TVLog(@"Touch state reset: screen locked");
        }
    });
    notify_register_dispatch("com.apple.springboard.hasBlankedScreen", &gTvBlankResetToken,
                             dispatch_get_main_queue(), ^(int token) {
        uint64_t state = 0;
        if (notify_get_state(token, &state) == NOTIFY_STATUS_OK && state != 0) {
            [[STHIDEventGenerator sharedGenerator] dispatchHandResetEvent];
            TVLog(@"Touch state reset: screen blanked");
        }
    });
    TVLog(@"Lock/blank touch-reset observers installed");
}

static NSString *tvFrontmostBundleID(void) {
    NSData *data = tvCtlFrontmostApp();
    NSString *reply = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    reply = [reply stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (![reply hasPrefix:@"OK "] || [reply isEqualToString:@"OK none"])
        return nil;
    return [reply substringFromIndex:3];
}

static NSData *tvCtlTouchLock(NSString *argument) {
    NSString *state = [[argument stringByTrimmingCharactersInSet:
                        [NSCharacterSet whitespaceAndNewlineCharacterSet]] lowercaseString];
    if ([state isEqualToString:@"status"]) {
        return [[NSString stringWithFormat:@"OK %@\n",
                 tvGetTouchLockNotifyState() ? @"on" : @"off"]
                dataUsingEncoding:NSUTF8StringEncoding];
    }
    if (![state isEqualToString:@"on"] && ![state isEqualToString:@"off"])
        return [@"ERR Usage touchlock on|off|status\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL enabled = [state isEqualToString:@"on"];
    NSString *previousBundleID = tvFrontmostBundleID();
    if (!tvSetTouchLockNotifyState(enabled))
        return [@"ERR TouchLockNotifyFailed\n" dataUsingEncoding:NSUTF8StringEncoding];

    // Launch only after this control request has had time to send its reply.
    // Foregrounding ControlIOS can recycle the app/daemon connection on some
    // iOS versions even though the VNC session itself remains alive.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 250 * NSEC_PER_MSEC),
                   dispatch_get_main_queue(), ^{
        (void)tvCtlLaunchApp(kTvControlIOSBundleID);
        if (previousBundleID.length && ![previousBundleID isEqualToString:kTvControlIOSBundleID]) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 1200 * NSEC_PER_MSEC),
                           dispatch_get_main_queue(), ^{
                (void)tvCtlLaunchApp(previousBundleID);
            });
        }
    });
    TVLog(@"Control socket: touchlock %@ (restore=%@)", state,
          previousBundleID ?: @"none");
    return [[NSString stringWithFormat:@"OK %@\n", state]
            dataUsingEncoding:NSUTF8StringEncoding];
}

#pragma mark - ControlIOSKeeper watchdog

static const int kTvKeeperdPort = 46753;
static NSString *const kTvKeeperBundleID = @"com.controlios.keeper";
static CFAbsoluteTime gKeeperLastLaunchAttempt = 0;

static BOOL tvKeeperdRunning(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return NO;
    struct timeval timeout = {1, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)kTvKeeperdPort);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    BOOL running = connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0;
    close(fd);
    return running;
}

static BOOL tvLaunchKeeperApp(void) {
    if (tvKeeperdRunning())
        return YES;
    @synchronized(kTvKeeperBundleID) {
        CFAbsoluteTime now = CFAbsoluteTimeGetCurrent();
        if (gKeeperLastLaunchAttempt > 0 && now - gKeeperLastLaunchAttempt < 60.0)
            return YES; // một lần đánh thức đang chờ Keeper spawn daemon
        gKeeperLastLaunchAttempt = now;

        void *handle = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                              "SpringBoardServices", RTLD_LAZY);
        if (!handle) {
            TVLog(@"Keeper watchdog: SpringBoardServices unavailable");
            return NO;
        }
        int result = -1;
        int (*launchOptions)(CFStringRef, CFDictionaryRef, Boolean) =
            (int (*)(CFStringRef, CFDictionaryRef, Boolean))dlsym(
                handle, "SBSLaunchApplicationWithIdentifierAndLaunchOptions");
        if (launchOptions)
            result = launchOptions((__bridge CFStringRef)kTvKeeperBundleID, NULL, true);
        if (result != 0) {
            int (*launch)(CFStringRef, Boolean) =
                (int (*)(CFStringRef, Boolean))dlsym(
                    handle, "SBSLaunchApplicationWithIdentifier");
            if (launch)
                result = launch((__bridge CFStringRef)kTvKeeperBundleID, true);
        }
        TVLog(@"Keeper watchdog: keeperd missing, launch Keeper result=%d", result);
        return result == 0;
    }
    return NO;
}

static NSData *tvCtlKeeper(NSString *argument) {
    NSString *arg = [[argument stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]] lowercaseString];
    if ([arg isEqualToString:@"status"])
        return [(tvKeeperdRunning() ? @"OK keeperd\n" : @"OK\n")
            dataUsingEncoding:NSUTF8StringEncoding];
    if ([arg isEqualToString:@"start"]) {
        if (tvKeeperdRunning())
            return [@"OK keeperd da chay san\n" dataUsingEncoding:NSUTF8StringEncoding];
        BOOL launched = tvLaunchKeeperApp();
        return [(launched ? @"OK launched Keeper\n" : @"ERR KeeperLaunchFailed\n")
            dataUsingEncoding:NSUTF8StringEncoding];
    }
    return [@"ERR Usage keeper status|start\n" dataUsingEncoding:NSUTF8StringEncoding];
}

static NSData *tvCtlDiagnostics(void) {
    NSProcessInfo *processInfo = [NSProcessInfo processInfo];
    NSDictionary *fs = [[NSFileManager defaultManager] attributesOfFileSystemForPath:@"/var" error:nil];
    unsigned long long total = [fs[NSFileSystemSize] unsignedLongLongValue];
    unsigned long long free = [fs[NSFileSystemFreeSize] unsignedLongLongValue];
    double loads[3] = {0, 0, 0};
    getloadavg(loads, 3);

    BOOL keychainLocked = NO;
    BOOL passcodeSet = NO;
    BOOL screenLocked = tvReadAccurateLockState(&keychainLocked, &passcodeSet);
    BOOL blanked = tvCtlNotifyState("com.apple.springboard.hasBlankedScreen");
    NSString *frontmost = tvFrontmostBundleID() ?: @"none";

    NSMutableArray<NSDictionary *> *crashes = [NSMutableArray array];
    NSArray<NSString *> *roots = @[@"/var/mobile/Library/Logs/CrashReporter",
                                   @"/Library/Logs/CrashReporter"];
    NSFileManager *fm = [NSFileManager defaultManager];
    for (NSString *root in roots) {
        NSDirectoryEnumerator *enumerator = [fm enumeratorAtPath:root];
        for (NSString *relative in enumerator) {
            NSString *lower = relative.lowercaseString;
            if (!([lower hasSuffix:@".ips"] || [lower hasSuffix:@".crash"]))
                continue;
            if (![lower containsString:@"controlios"] && ![lower containsString:@"trollvnc"] &&
                ![lower containsString:@"keeper"] && ![lower containsString:@"earnapp"])
                continue;
            NSString *path = [root stringByAppendingPathComponent:relative];
            NSDictionary *attrs = [fm attributesOfItemAtPath:path error:nil];
            [crashes addObject:@{@"path": path,
                                 @"date": attrs[NSFileModificationDate] ?: [NSDate distantPast]}];
        }
    }
    [crashes sortUsingComparator:^NSComparisonResult(NSDictionary *a, NSDictionary *b) {
        return [b[@"date"] compare:a[@"date"]];
    }];

    NSMutableString *out = [NSMutableString stringWithString:@"OK\n"];
    [out appendFormat:@"uptime=%.0f\n", processInfo.systemUptime];
    [out appendFormat:@"memory_total=%llu\n", processInfo.physicalMemory];
    [out appendFormat:@"disk_total=%llu\n", total];
    [out appendFormat:@"disk_free=%llu\n", free];
    [out appendFormat:@"load=%.2f %.2f %.2f\n", loads[0], loads[1], loads[2]];
    [out appendFormat:@"screen_locked=%d\nkeychain_locked=%d\npasscode=%d\nblanked=%d\n",
                      screenLocked, keychainLocked, passcodeSet, blanked];
    [out appendFormat:@"touch_lock=%d\nkeeper=%d\nvnc_clients=%d\nfrontmost=%@\n",
                      tvGetTouchLockNotifyState(), tvKeeperdRunning(), gClientCount, frontmost];
    NSUInteger count = MIN((NSUInteger)5, crashes.count);
    [out appendFormat:@"crash_count=%lu\n", (unsigned long)crashes.count];
    for (NSUInteger index = 0; index < count; ++index)
        [out appendFormat:@"crash=%@\n", crashes[index][@"path"]];
    return [out dataUsingEncoding:NSUTF8StringEncoding];
}

static void tvEnsureKeeperAtStartup(void) {
    dispatch_queue_t queue = dispatch_queue_create(
        "com.controlios.server.keeper-startup-check", DISPATCH_QUEUE_SERIAL);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), queue, ^{
        @autoreleasepool {
            if (!tvKeeperdRunning())
                (void)tvLaunchKeeperApp();
        }
    });
    TVLog(@"Keeper startup check scheduled (port %d)", kTvKeeperdPort);
}

static void tvDimDisplayToMinimum(void) {
    for (int i = 0; i < 16; ++i)
        [[STHIDEventGenerator sharedGenerator] displayBrightnessDecrementPress];
    TVLog(@"First-client unlock check: brightness reduced to minimum");
}

static void tvVerifyStartupUnlockAndOpenSettings(NSUInteger attempt) {
    // A pending startup-unlock retry must never inject Home while the explicit
    // touch blocker is active. Otherwise two retries can open App Switcher.
    if (tvGetTouchLockNotifyState()) {
        TVLog(@"First-client unlock verification cancelled: touch lock is active");
        return;
    }
    BOOL keychainLocked = NO;
    BOOL passcodeSet = NO;
    BOOL locked = tvReadAccurateLockState(&keychainLocked, &passcodeSet);
    BOOL blanked = tvCtlNotifyState("com.apple.springboard.hasBlankedScreen");
    TVLog(@"First-client unlock verification %lu/6: locked=%d blanked=%d keychain=%d passcode=%d",
          (unsigned long)attempt, locked, blanked, keychainLocked, passcodeSet);

    if (!locked) {
        tvDimDisplayToMinimum();
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 500 * NSEC_PER_MSEC),
                       dispatch_get_main_queue(), ^{
            (void)tvCtlLaunchApp(@"com.apple.Preferences");
            TVLog(@"First-client unlock verified; Settings launch requested");
        });
        return;
    }

    tvRecordHomeAudit(@"startup-unlock",
                      [NSString stringWithFormat:@"attempt=%lu locked=%d blanked=%d",
                       (unsigned long)attempt, locked, blanked]);
    [[STHIDEventGenerator sharedGenerator] menuPress];
    TVLog(@"First-client unlock verification %lu/6: Home pressed",
          (unsigned long)attempt);

    if (attempt < 6) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{
            tvVerifyStartupUnlockAndOpenSettings(attempt + 1);
        });
    } else {
        // Keep the requested low brightness even if iOS still reports locked.
        // A passcode-protected first unlock after reboot cannot be bypassed.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 800 * NSEC_PER_MSEC),
                       dispatch_get_main_queue(), ^{
            tvDimDisplayToMinimum();
            (void)tvCtlLaunchApp(@"com.apple.Preferences");
            TVLog(@"First-client unlock remained locked after 6 attempts; Settings launch requested");
        });
    }
}

static BOOL tvShouldRunStartupUnlockCheck(void) {
    // Installing/restarting ControlIOS while the device is already in use must
    // never be treated as a device startup. Only allow the startup gesture in
    // the first few minutes after iOS boot.
    NSTimeInterval uptime = [NSProcessInfo processInfo].systemUptime;
    static const NSTimeInterval kStartupUnlockWindow = 5 * 60.0;
    if (uptime > kStartupUnlockWindow) {
        TVLog(@"Startup unlock check skipped: uptime %.0fs exceeds %.0fs window",
              uptime, kStartupUnlockWindow);
        return NO;
    }

    struct timeval boottv = {0};
    size_t len = sizeof(boottv);
    int mib[2] = {CTL_KERN, KERN_BOOTTIME};
    if (sysctl(mib, 2, &boottv, &len, NULL, 0) != 0 || len != sizeof(boottv)) {
        TVLog(@"Startup unlock check skipped: boot time unavailable");
        return NO;
    }

    NSString *bootID = [NSString stringWithFormat:@"%lld.%06d",
                        (long long)boottv.tv_sec, (int)boottv.tv_usec];
    NSString *markerPath = @"/var/tmp/com.controlios.startup-unlock-boot";
    NSString *previousBootID = [NSString stringWithContentsOfFile:markerPath
                                                          encoding:NSUTF8StringEncoding
                                                             error:NULL];
    if ([previousBootID isEqualToString:bootID]) {
        TVLog(@"Startup unlock check skipped: already handled this boot");
        return NO;
    }

    NSError *error = nil;
    BOOL written = [bootID writeToFile:markerPath
                            atomically:YES
                              encoding:NSUTF8StringEncoding
                                 error:&error];
    if (!written) {
        TVLog(@"Startup unlock check skipped: cannot write boot marker (%@)", error);
        return NO;
    }
    return YES;
}

static void tvScheduleInitialUnlockCheckAfterFirstClient(void) {
    // Run at most once per iOS boot. A daemon restart after ControlIOS is killed
    // must not be mistaken for a device reboot and must not press Home/Settings.
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        if (!tvShouldRunStartupUnlockCheck())
            return;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 1 * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{
            tvVerifyStartupUnlockAndOpenSettings(1);
        });
        TVLog(@"One-shot unlock check scheduled after first VNC client of this boot (1s)");
    });
}

#pragma mark - Scale

// `setscale <0..1>` — đổi hệ số scale khung hình LÚC ĐANG CHẠY để giảm tải cho
// máy đời cũ (khung nhỏ hơn -> nén nhanh hơn -> mượt hơn). Chỉ cần đổi gScale;
// luồng xử lý khung gọi maybeResizeFramebufferForRotation() mỗi khung nên sẽ tự
// đổi kích thước framebuffer ngay ở lượt kế (trên đúng luồng đó, không race).
// Đổi kích thước khiến client nối lại một nhịp (client này không đăng ký
// DesktopSize) — giống lúc xoay máy. Giá trị không lưu qua lần khởi động lại
// daemon; muốn cố định thì đặt pref `Scale`.
static NSData *tvCtlSetScale(NSString *arg) {
    double v = [[arg stringByTrimmingCharactersInSet:
                    [NSCharacterSet whitespaceAndNewlineCharacterSet]] doubleValue];
    if (!(v > 0.0 && v <= 1.0))
        return [@"ERR BadScale (can 0 < s <= 1)\n" dataUsingEncoding:NSUTF8StringEncoding];
    int oldW = gWidth, oldH = gHeight;
    gScale = v;

    // Quan trọng: ScreenCapturer bỏ qua khung khi màn hình KHÔNG đổi nội dung
    // (dirty frame count không đổi), nên maybeResize không tự chạy trên màn hình
    // tĩnh -> framebuffer không đổi cỡ tới khi có tương tác. Vì vậy ÉP resize NGAY
    // tại đây. Chạy trên main queue (nơi frame handler chạy) để không đua với
    // luồng capture khi cấp phát lại buffer. dispatch_sync để chắc chắn xong rồi
    // mới trả lời -> PC nối lại là ServerInit thấy đúng cỡ mới, hết lồng nhau.
    int rotQ = (gOrientationSyncEnabled
                    ? (int)gRotationQuad.load(std::memory_order_relaxed) : 0) & 3;
    if ([NSThread isMainThread]) {
        maybeResizeFramebufferForRotation(rotQ);
    } else {
        dispatch_sync(dispatch_get_main_queue(), ^{
            maybeResizeFramebufferForRotation(rotQ);
        });
    }

    TVLog(@"Control socket: setscale %.3f: %dx%d -> %dx%d", v, oldW, oldH, gWidth, gHeight);
    NSString *ok = [NSString stringWithFormat:@"OK %.3f %dx%d\n", v, gWidth, gHeight];
    return [ok dataUsingEncoding:NSUTF8StringEncoding];
}

#pragma mark - File transfer

// Giới hạn cho chắc: file lớn hơn mức này gần như luôn là gõ nhầm.
static const uint64_t kTvMaxPutBytes = 1024ull * 1024ull * 1024ull; // 1 GiB

/// `put <size> <path>` — đọc đúng `size` byte từ socket rồi ghi ra `path`.
static NSData *tvCtlReceiveFile(int cfd, NSString *spec, const uint8_t *pending,
                                size_t pendingLength) {
    NSRange space = [spec rangeOfString:@" "];
    if (space.location == NSNotFound)
        return [@"ERR Usage put <size> <path>\n" dataUsingEncoding:NSUTF8StringEncoding];

    long long size = [[spec substringToIndex:space.location] longLongValue];
    NSString *path = [[spec substringFromIndex:space.location + 1]
        stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];

    if (size < 0 || (uint64_t)size > kTvMaxPutBytes)
        return [@"ERR BadSize\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (path.length == 0 || ![path hasPrefix:@"/"] ||
        [path rangeOfString:@".."].location != NSNotFound)
        return [@"ERR BadPath\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    [fm createDirectoryAtPath:path.stringByDeletingLastPathComponent
        withIntermediateDirectories:YES
                         attributes:nil
                              error:NULL];

    FILE *fp = fopen(path.fileSystemRepresentation, "wb");
    if (!fp) {
        TVLog(@"Control socket: cannot open %@ for writing: %s", path, strerror(errno));
        return [@"ERR CannotWrite\n" dataUsingEncoding:NSUTF8StringEncoding];
    }

    // Nhận file có thể lâu hơn nhiều so với một dòng lệnh.
    struct timeval tv;
    tv.tv_sec = 30;
    tv.tv_usec = 0;
    setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint64_t written = 0;

    // Phần đã bị vòng đọc dòng lệnh nuốt trước phải ghi ra trước tiên.
    if (pendingLength > 0) {
        size_t take = (size_t)MIN((uint64_t)pendingLength, (uint64_t)size);
        if (take > 0 && fwrite(pending, 1, take, fp) != take) {
            fclose(fp);
            return [@"ERR WriteFailed\n" dataUsingEncoding:NSUTF8StringEncoding];
        }
        written += take;
    }

    uint8_t chunk[65536];
    while (written < (uint64_t)size) {
        size_t want = (size_t)MIN((uint64_t)sizeof(chunk), (uint64_t)size - written);
        ssize_t n = recv(cfd, chunk, want, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (n == 0)
            break;
        if (fwrite(chunk, 1, (size_t)n, fp) != (size_t)n)
            break;
        written += (uint64_t)n;
    }

    fclose(fp);

    if (written != (uint64_t)size) {
        TVLog(@"Control socket: put %@ incomplete (%llu/%lld)", path, written, size);
        unlink(path.fileSystemRepresentation);
        NSString *err = [NSString stringWithFormat:@"ERR Incomplete %llu/%lld\n", written, size];
        return [err dataUsingEncoding:NSUTF8StringEncoding];
    }

    TVLog(@"Control socket: put %@ (%llu bytes)", path, written);
    NSString *ok = [NSString stringWithFormat:@"OK %llu\n", written];
    return [ok dataUsingEncoding:NSUTF8StringEncoding];
}

/// `container <bundle id>` — thư mục dữ liệu của app, để đẩy file vào đúng chỗ.
///
/// `/var/mobile/Documents/` là thư mục thật nhưng app Tệp của iOS không hiện
/// nó: Tệp chỉ hiện container của những app tự khai báo hỗ trợ duyệt tài liệu.
/// Muốn file nhìn thấy được thì phải ghi vào container của một app cụ thể.
static NSData *tvCtlContainerForApp(NSString *bundleId) {
    LSApplicationWorkspace *ws = tvAppWorkspace();
    if (!ws)
        return [@"ERR Unavailable\n" dataUsingEncoding:NSUTF8StringEncoding];

    for (LSApplicationProxy *app in [ws allApplications]) {
        if (![app.applicationIdentifier isEqualToString:bundleId])
            continue;
        NSString *data = app.dataContainerURL.path ?: @"";
        NSString *bundle = app.bundleURL.path ?: @"";
        NSString *out = [NSString stringWithFormat:@"%@\t%@\n", data, bundle];
        return [out dataUsingEncoding:NSUTF8StringEncoding];
    }
    return [@"NOT_FOUND\n" dataUsingEncoding:NSUTF8StringEncoding];
}

/// `ls <path>` — TSV: tên, cỡ byte, 1 nếu là thư mục.
/// Có nó mới kiểm chứng được file đẩy lên đã tới nơi hay chưa.
static NSData *tvCtlListDirectory(NSString *path) {
    if (path.length == 0 || ![path hasPrefix:@"/"])
        return [@"ERR BadPath\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSError *error = nil;
    NSArray<NSString *> *names = [fm contentsOfDirectoryAtPath:path error:&error];
    if (!names) {
        TVLog(@"Control socket: ls %@ failed: %@", path, error.localizedDescription);
        return [@"ERR CannotRead\n" dataUsingEncoding:NSUTF8StringEncoding];
    }

    NSMutableString *out = [NSMutableString string];
    for (NSString *name in names) {
        NSString *full = [path stringByAppendingPathComponent:name];
        BOOL isDir = NO;
        [fm fileExistsAtPath:full isDirectory:&isDir];
        NSDictionary *attrs = [fm attributesOfItemAtPath:full error:NULL];
        unsigned long long size = isDir ? 0ull : [attrs fileSize];
        [out appendFormat:@"%@\t%llu\t%d\n",
                          [name stringByReplacingOccurrencesOfString:@"\t" withString:@" "],
                          size, isDir ? 1 : 0];
    }
    return [out dataUsingEncoding:NSUTF8StringEncoding];
}

/// `getfile <path>` — truyền file nhị phân qua control socket. Chỉ cho phép đọc
/// trong kho snapshot để không biến cổng điều khiển thành trình đọc file tùy ý.
static void tvCtlSendSnapshotFile(int cfd, NSString *path) {
    NSString *root = @"/var/mobile/controlios-snap/";
    NSString *clean = [path stringByStandardizingPath];
    if (![clean hasPrefix:root] || [clean containsString:@"/../"]) {
        const char *err = "ERR BadPath\n";
        tvCtlWriteAll(cfd, err, strlen(err));
        return;
    }
    int fd = open(clean.fileSystemRepresentation, O_RDONLY);
    if (fd < 0) {
        const char *err = "ERR CannotRead\n";
        tvCtlWriteAll(cfd, err, strlen(err));
        return;
    }
    struct stat st = {};
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
        close(fd);
        const char *err = "ERR NotFile\n";
        tvCtlWriteAll(cfd, err, strlen(err));
        return;
    }
    NSString *header = [NSString stringWithFormat:@"OK %lld\n", (long long)st.st_size];
    NSData *headerData = [header dataUsingEncoding:NSUTF8StringEncoding];
    tvCtlWriteAll(cfd, headerData.bytes, headerData.length);
    uint8_t chunk[64 * 1024];
    for (;;) {
        ssize_t count = read(fd, chunk, sizeof(chunk));
        if (count <= 0)
            break;
        tvCtlWriteAll(cfd, chunk, (size_t)count);
    }
    close(fd);
}

/// `openurlin <bundle id> <url>` — mở URL bằng **đúng app đó**.
///
/// Cần thiết vì `apple-magnifier://` là scheme TrollStore chiếm lại của app
/// Kính lúp. Khi để hệ thống tự chọn, nó chọn app Kính lúp gốc và bật camera.
/// Chỉ đích danh bundle id thì bỏ qua hẳn bước chọn đó.
static NSData *tvCtlOpenURLInApp(NSString *bundleId, NSString *urlString) {
    NSURL *url = [NSURL URLWithString:urlString];
    if (!url || bundleId.length == 0)
        return [@"ERR BadURL\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = NO;
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsLaunchWithURL)(CFStringRef, CFURLRef, CFDictionaryRef, CFDictionaryRef,
                                CFStringRef, Boolean) =
            (int (*)(CFStringRef, CFURLRef, CFDictionaryRef, CFDictionaryRef, CFStringRef,
                     Boolean))dlsym(h, "SBSLaunchApplicationWithIdentifierAndURL");
        if (sbsLaunchWithURL)
            ok = (sbsLaunchWithURL((__bridge CFStringRef)bundleId, (__bridge CFURLRef)url,
                                   NULL, NULL, NULL, true) == 0);
    }

    TVLog(@"Control socket: openurlin %@ %@ -> %@", bundleId, urlString,
          ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR OpenFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

/// `openurl <url>` — để hệ thống tự chọn app cho scheme đó.
static NSData *tvCtlOpenURL(NSString *urlString) {
    NSURL *url = [NSURL URLWithString:urlString];
    if (!url)
        return [@"ERR BadURL\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = NO;
    void *h = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                     "SpringBoardServices",
                     RTLD_LAZY);
    if (h) {
        int (*sbsOpen)(CFURLRef, Boolean) =
            (int (*)(CFURLRef, Boolean))dlsym(h, "SBSOpenSensitiveURLAndUnlock");
        if (sbsOpen)
            ok = (sbsOpen((__bridge CFURLRef)url, true) == 0);
    }

    if (!ok) {
        LSApplicationWorkspace *ws = tvAppWorkspace();
        if ([ws respondsToSelector:@selector(openSensitiveURL:withOptions:)])
            ok = [ws openSensitiveURL:url withOptions:nil];
    }

    TVLog(@"Control socket: openurl %@ -> %@", urlString, ok ? @"OK" : @"FAIL");
    const char *raw = ok ? "OK\n" : "ERR OpenFailed\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

#pragma mark - License (kích hoạt bản quyền)

// Bật/tắt GÁC CỔNG bản quyền. 0 = KHÔNG gác (để TEST tự do), 1 = bắt buộc
// license hợp lệ mới phục vụ. License vẫn được đọc/hiển thị khi = 0, chỉ không
// chặn. Khi hoàn thiện đổi thành 1.
#define CIOS_ENFORCE_LICENSE 0

// KHOÁ CÔNG KHAI của bạn (65 byte, 04||X||Y). Sinh bằng
// `tools/controlios_keygen.py genkeys` rồi DÁN mảng đó vào đây. Khoá riêng đi kèm
// (controlios_private.pem) GIỮ BÍ MẬT — mất nó là ai cũng chế được license.
static const uint8_t kLicensePubKey[65] = {
    0x04, 0x24, 0xa7, 0xf1, 0x9e, 0xe2, 0xa7, 0xdd, 0x87, 0xa6, 0xdc, 0xdd, 0xc7,
    0xa0, 0x4c, 0xf2, 0xa2, 0x64, 0x1a, 0x73, 0x79, 0x1f, 0xa4, 0x68, 0x92, 0x58,
    0xb1, 0x98, 0x94, 0xac, 0x1d, 0xbe, 0xd6, 0x05, 0x22, 0xf0, 0x18, 0xf7, 0x51,
    0xac, 0x69, 0xa0, 0x9c, 0x56, 0x8e, 0xc1, 0x42, 0x22, 0x2c, 0xe0, 0x22, 0x07,
    0x57, 0x07, 0x46, 0xfa, 0x86, 0x5d, 0x5d, 0xc1, 0xe6, 0x0b, 0x6f, 0x44, 0xb5};

static NSString *tvLicensePath(void) {
    return @"/var/mobile/Library/controlios/license.dat";
}

// UDID máy qua libMobileGestalt (daemon có entitlement đọc được).
static NSString *tvDeviceUDID(void) {
    static NSString *cached = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        void *h = dlopen("/usr/lib/libMobileGestalt.dylib", RTLD_LAZY);
        if (h) {
            CFStringRef (*mgCopy)(CFStringRef) =
                (CFStringRef (*)(CFStringRef))dlsym(h, "MGCopyAnswer");
            if (mgCopy) {
                CFStringRef v = mgCopy(CFSTR("UniqueDeviceID"));
                if (v)
                    cached = (__bridge_transfer NSString *)v;
            }
        }
    });
    return cached;
}

static NSData *tvB64UrlDecode(NSString *s) {
    NSMutableString *m = [s mutableCopy];
    [m replaceOccurrencesOfString:@"-" withString:@"+" options:0 range:NSMakeRange(0, m.length)];
    [m replaceOccurrencesOfString:@"_" withString:@"/" options:0 range:NSMakeRange(0, m.length)];
    while (m.length % 4)
        [m appendString:@"="];
    return [[NSData alloc] initWithBase64EncodedString:m options:0];
}

// Đọc + kiểm license: chữ ký ECDSA-P256-SHA256 hợp lệ, đúng UDID máy, chưa hết
// hạn. Đặt gLicenseValid/gLicenseToken/gLicenseExpiry. Gọi lúc khởi động và khi
// `relicense`.
static void tvLicenseLoad(void) {
    gLicenseValid = NO;
    gLicenseToken = nil;
    gLicenseExpiry = 0;

    NSString *lic = [[NSString stringWithContentsOfFile:tvLicensePath()
                                               encoding:NSUTF8StringEncoding
                                                  error:NULL]
        stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (lic.length == 0) {
        TVLog(@"License: chưa có file %@", tvLicensePath());
        return;
    }
    NSArray<NSString *> *parts = [lic componentsSeparatedByString:@"."];
    if (parts.count != 2) {
        TVLog(@"License: sai định dạng");
        return;
    }
    NSData *payload = tvB64UrlDecode(parts[0]);
    NSData *sig = tvB64UrlDecode(parts[1]);
    if (!payload || !sig) {
        TVLog(@"License: base64 hỏng");
        return;
    }

    NSData *keyData = [NSData dataWithBytes:kLicensePubKey length:sizeof(kLicensePubKey)];
    NSDictionary *attrs = @{
        (id)kSecAttrKeyType : (id)kSecAttrKeyTypeECSECPrimeRandom,
        (id)kSecAttrKeyClass : (id)kSecAttrKeyClassPublic,
        (id)kSecAttrKeySizeInBits : @256,
    };
    SecKeyRef pub = SecKeyCreateWithData((__bridge CFDataRef)keyData,
                                         (__bridge CFDictionaryRef)attrs, NULL);
    if (!pub) {
        TVLog(@"License: dựng khoá công khai lỗi");
        return;
    }
    BOOL sigOK = SecKeyVerifySignature(pub, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,
                                       (__bridge CFDataRef)payload, (__bridge CFDataRef)sig, NULL);
    CFRelease(pub);
    if (!sigOK) {
        TVLog(@"License: chữ ký KHÔNG hợp lệ");
        return;
    }

    NSDictionary *p = [NSJSONSerialization JSONObjectWithData:payload options:0 error:NULL];
    if (![p isKindOfClass:[NSDictionary class]]) {
        TVLog(@"License: payload lỗi");
        return;
    }
    NSString *udid = p[@"udid"];
    long long exp = [p[@"exp"] longLongValue];
    NSString *tok = p[@"tok"];
    NSString *devUDID = tvDeviceUDID();
    if (udid.length == 0 || ![udid isEqualToString:devUDID]) {
        TVLog(@"License: sai UDID (license=%@, máy=%@)", udid, devUDID);
        return;
    }
    if (exp != 0 && (long long)time(NULL) > exp) {
        TVLog(@"License: đã hết hạn (%lld)", exp);
        return;
    }

    gLicenseValid = YES;
    gLicenseToken = [tok copy];
    gLicenseExpiry = exp;
#if CIOS_ENFORCE_LICENSE
    // "Khoá có ích": token control lấy TỪ license. Thiếu license hợp lệ thì không
    // có token đúng -> PC không điều khiển được kể cả khi patch phần kiểm.
    if (tok.length > 0)
        gTvCtlToken = [tok copy];
#endif
    TVLog(@"License: HỢP LỆ (UDID khớp, hạn=%lld)", exp);
}

// `license` — trả trạng thái kích hoạt (cho phép kể cả khi CHƯA kích hoạt để app
// hiện UDID + trạng thái).
static NSData *tvCtlLicenseStatus(void) {
    NSString *udid = tvDeviceUDID() ?: @"";
    NSString *s = gLicenseValid
        ? [NSString stringWithFormat:@"OK valid exp=%lld udid=%@\n", gLicenseExpiry, udid]
        : [NSString stringWithFormat:@"OK invalid udid=%@\n", udid];
    return [s dataUsingEncoding:NSUTF8StringEncoding];
}

#pragma mark - Auto-click (kịch bản tự chạy trên máy)

// Chạy một kịch bản chạm/vuốt NGAY TRÊN MÁY theo vòng lặp, không cần PC. Toạ độ
// theo tỉ lệ 0..1 (không gian màn dọc gSrcWidth×gSrcHeight). Bơm sự kiện qua
// STHIDEventGenerator — đúng bộ mà VNC dùng để điều khiển.
static NSString *gAutoScript = nil;
static dispatch_queue_t gAutoQueue = nil;
static std::atomic<bool> gAutoStop{false};
static std::atomic<bool> gAutoRunning{false};
static std::atomic<bool> gAutoTrace{true}; // tự ghi từng lệnh + kết quả vào nhật ký

static NSString *tvAutoScriptPath(void) {
    return @"/var/mobile/Library/controlios/autoscript.txt";
}

static CGPoint tvAutoPoint(double rx, double ry) {
    // Toạ độ tỉ lệ tính theo KHUNG ĐANG PHỤC VỤ (gWidth×gHeight) — CÙNG không gian
    // với getColor/matchColor/findText/OCR. Đưa về điểm thiết bị qua ĐÚNG đường
    // map của điều khiển VNC (vncPointToDevicePoint) để KHỬ XOAY + KHỬ SCALE, nếu
    // không, máy có xoay/offset xoay thì cú chạm sẽ rơi sai chỗ.
    rx = rx < 0 ? 0 : (rx > 1 ? 1 : rx);
    ry = ry < 0 ? 0 : (ry > 1 ? 1 : ry);
    int vx = (int)(rx * (double)((gWidth > 0 ? gWidth : 1) - 1));
    int vy = (int)(ry * (double)((gHeight > 0 ? gHeight : 1) - 1));
    return vncPointToDevicePoint(vx, vy);
}

// Ngủ theo nhịp nhỏ để DỪNG nhanh khi có yêu cầu dừng.
static void tvAutoSleep(double sec) {
    double slept = 0;
    while (slept < sec && !gAutoStop.load()) {
        double step = (sec - slept) < 0.1 ? (sec - slept) : 0.1;
        if (step <= 0)
            break;
        usleep((useconds_t)(step * 1e6));
        slept += step;
    }
}

// ---- Dò MÀU điểm ảnh (đọc thẳng framebuffer đang phục vụ VNC) ----
static BOOL tvSampleColor(double rx, double ry, uint8_t *oR, uint8_t *oG, uint8_t *oB) {
    if (!gScreen || !gScreen->frameBuffer || gWidth <= 0 || gHeight <= 0)
        return NO;
    int x = (int)(rx * (gWidth - 1)), y = (int)(ry * (gHeight - 1));
    if (x < 0) x = 0;
    if (x >= gWidth) x = gWidth - 1;
    if (y < 0) y = 0;
    if (y >= gHeight) y = gHeight - 1;
    rfbPixelFormat *f = &gScreen->serverFormat;
    int bpp = f->bitsPerPixel / 8;
    if (bpp < 3)
        return NO;
    uint8_t *px = (uint8_t *)gScreen->frameBuffer + (size_t)y * gScreen->paddedWidthInBytes + (size_t)x * bpp;
    uint32_t pixel = 0;
    memcpy(&pixel, px, bpp > 4 ? 4 : bpp);
    int rmax = f->redMax ? f->redMax : 255;
    int gmax = f->greenMax ? f->greenMax : 255;
    int bmax = f->blueMax ? f->blueMax : 255;
    *oR = (uint8_t)(((pixel >> f->redShift) & f->redMax) * 255 / rmax);
    *oG = (uint8_t)(((pixel >> f->greenShift) & f->greenMax) * 255 / gmax);
    *oB = (uint8_t)(((pixel >> f->blueShift) & f->blueMax) * 255 / bmax);
    return YES;
}

// args = x y RRGGBB [tol] : điểm (rx,ry) có màu gần RRGGBB trong dung sai không.
// Nhật ký auto-click: bộ đệm vòng để PC kéo về theo dõi tiến trình (log/toast).
static NSMutableArray<NSString *> *gAutoLog = nil;
static NSObject *gAutoLogLock = nil;

static void tvAutoLog(NSString *msg) {
    if (!gAutoLogLock)
        gAutoLogLock = [NSObject new];
    @synchronized(gAutoLogLock) {
        if (!gAutoLog)
            gAutoLog = [NSMutableArray array];
        static NSDateFormatter *fmt;
        static dispatch_once_t once;
        dispatch_once(&once, ^{
            fmt = [NSDateFormatter new];
            fmt.dateFormat = @"HH:mm:ss";
        });
        [gAutoLog addObject:[NSString stringWithFormat:@"%@  %@", [fmt stringFromDate:[NSDate date]],
                                                       msg ?: @""]];
        while (gAutoLog.count > 250)
            [gAutoLog removeObjectAtIndex:0];
    }
}

// Ghi trace (từng lệnh) — chỉ khi bật, dùng cho theo dõi tiến trình trên PC.
static inline void tvTrace(NSString *m) {
    if (gAutoTrace.load())
        tvAutoLog(m);
}

static NSString *tvAutoLogText(void) {
    if (!gAutoLogLock)
        gAutoLogLock = [NSObject new];
    @synchronized(gAutoLogLock) {
        return [(gAutoLog ?: @[]) componentsJoinedByString:@"\n"];
    }
}

// ================= Engine JavaScript (JavaScriptCore), kiểu AutoTouch =========
static BOOL tvSetAssistiveTouch(int mode); // định nghĩa ở dưới

// Dừng HỢP TÁC: có yêu cầu dừng thì ném exception để JS thoát ngay ở lệnh kế.
static void tvJSStopIfNeeded(void) {
    if (gAutoStop.load()) {
        JSContext *c = [JSContext currentContext];
        c.exception = [JSValue valueWithNewErrorFromMessage:@"__STOP__" inContext:c];
    }
}

static void tvHexToRGB(NSString *hex, int *r, int *g, int *b) {
    unsigned int v = 0;
    [[NSScanner scannerWithString:(hex ?: @"")] scanHexInt:&v];
    *r = (v >> 16) & 0xff;
    *g = (v >> 8) & 0xff;
    *b = v & 0xff;
}

static BOOL tvColorAt(double x, double y, NSString *hex, double tol) {
    int tr, tg, tb;
    tvHexToRGB(hex, &tr, &tg, &tb);
    uint8_t r, g, b;
    if (!tvSampleColor(x, y, &r, &g, &b))
        return NO;
    int t = (int)(tol <= 0 ? 12 : tol);
    return abs((int)r - tr) <= t && abs((int)g - tg) <= t && abs((int)b - tb) <= t;
}

// Tải ảnh mẫu -> RGBA thô (caller free).
static uint8_t *tvLoadImageRGBA(NSString *path, int *outW, int *outH) {
    UIImage *img = [UIImage imageWithContentsOfFile:path];
    CGImageRef cg = img.CGImage;
    if (!cg)
        return NULL;
    int w = (int)CGImageGetWidth(cg), h = (int)CGImageGetHeight(cg);
    if (w <= 0 || h <= 0)
        return NULL;
    uint8_t *buf = (uint8_t *)calloc((size_t)w * h * 4, 1);
    if (!buf)
        return NULL;
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGContextRef c = CGBitmapContextCreate(
        buf, w, h, 8, w * 4, cs,
        (uint32_t)(kCGImageAlphaPremultipliedLast | kCGBitmapByteOrder32Big));
    CGColorSpaceRelease(cs);
    if (!c) {
        free(buf);
        return NULL;
    }
    CGContextDrawImage(c, CGRectMake(0, 0, w, h), cg);
    CGContextRelease(c);
    *outW = w;
    *outH = h;
    return buf;
}

// Tìm ảnh mẫu trên màn: khớp theo LƯỚI ĐIỂM MẪU (nhanh, đủ cho biểu tượng/nút).
// Trả về tỉ lệ tâm khớp. tol = sai màu 0..255.
static BOOL tvFindImage(NSString *path, double rx1, double ry1, double rx2, double ry2,
                        double tol, double *foundRx, double *foundRy) {
    if (!gScreen || !gScreen->frameBuffer || gWidth <= 0 || gHeight <= 0)
        return NO;
    int tw = 0, th = 0;
    uint8_t *tpl = tvLoadImageRGBA(path, &tw, &th);
    if (!tpl)
        return NO;

    int fx1 = (int)(MAX(0.0, rx1) * (gWidth - 1));
    int fy1 = (int)(MAX(0.0, ry1) * (gHeight - 1));
    int fx2 = (int)((rx2 <= 0 ? 1.0 : rx2) * (gWidth - 1));
    int fy2 = (int)((ry2 <= 0 ? 1.0 : ry2) * (gHeight - 1));
    if (fx2 <= fx1)
        fx2 = gWidth - 1;
    if (fy2 <= fy1)
        fy2 = gHeight - 1;

    rfbPixelFormat *f = &gScreen->serverFormat;
    int bpp = f->bitsPerPixel / 8;
    if (bpp < 3) {
        free(tpl);
        return NO;
    }
    int t = (int)(tol <= 0 ? 24 : tol);
    int rmax = f->redMax ? f->redMax : 255, gmax = f->greenMax ? f->greenMax : 255,
        bmax = f->blueMax ? f->blueMax : 255;

    // ~25 điểm mẫu rải đều trong ảnh mẫu.
    const int SN = 5;
    int sx[SN * SN], sy[SN * SN];
    uint8_t sr[SN * SN], sg[SN * SN], sb[SN * SN];
    int np = 0;
    for (int i = 0; i < SN; i++)
        for (int j = 0; j < SN; j++) {
            int px = (tw <= 1) ? 0 : j * (tw - 1) / (SN - 1);
            int py = (th <= 1) ? 0 : i * (th - 1) / (SN - 1);
            uint8_t *p = tpl + ((size_t)py * tw + px) * 4;
            sx[np] = px;
            sy[np] = py;
            sr[np] = p[0];
            sg[np] = p[1];
            sb[np] = p[2];
            np++;
        }

    for (int oy = fy1; oy + th <= fy2 + 1 && !gAutoStop.load(); oy += 2) {
        for (int ox = fx1; ox + tw <= fx2 + 1; ox += 2) {
            BOOL ok = YES;
            for (int k = 0; k < np; k++) {
                int X = ox + sx[k], Y = oy + sy[k];
                uint8_t *px = (uint8_t *)gScreen->frameBuffer +
                              (size_t)Y * gScreen->paddedWidthInBytes + (size_t)X * bpp;
                uint32_t pixel = 0;
                memcpy(&pixel, px, bpp > 4 ? 4 : bpp);
                int R = ((pixel >> f->redShift) & f->redMax) * 255 / rmax;
                int G = ((pixel >> f->greenShift) & f->greenMax) * 255 / gmax;
                int B = ((pixel >> f->blueShift) & f->blueMax) * 255 / bmax;
                if (abs(R - sr[k]) > t || abs(G - sg[k]) > t || abs(B - sb[k]) > t) {
                    ok = NO;
                    break;
                }
            }
            if (ok) {
                *foundRx = (double)(ox + tw / 2) / (gWidth - 1);
                *foundRy = (double)(oy + th / 2) / (gHeight - 1);
                free(tpl);
                return YES;
            }
        }
    }
    free(tpl);
    return NO;
}

// OCR một vùng màn (Vision, trên máy, không cần mạng). Trả về chữ nhận được
// (mỗi dòng một mục). Chạy đồng bộ trên luồng auto nền.
static NSString *tvOCR(double rx1, double ry1, double rx2, double ry2) {
    if (!gScreen || !gScreen->frameBuffer || gWidth <= 0 || gHeight <= 0)
        return @"";
    int x1 = (int)(MAX(0.0, rx1) * (gWidth - 1));
    int y1 = (int)(MAX(0.0, ry1) * (gHeight - 1));
    int x2 = (int)((rx2 <= 0 ? 1.0 : rx2) * (gWidth - 1));
    int y2 = (int)((ry2 <= 0 ? 1.0 : ry2) * (gHeight - 1));
    if (x2 <= x1)
        x2 = gWidth - 1;
    if (y2 <= y1)
        y2 = gHeight - 1;

    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGDataProviderRef dp = CGDataProviderCreateWithData(
        NULL, gScreen->frameBuffer, (size_t)gScreen->paddedWidthInBytes * gHeight, NULL);
    CGImageRef full = CGImageCreate(
        gWidth, gHeight, 8, 32, gScreen->paddedWidthInBytes, cs,
        (CGBitmapInfo)(kCGImageAlphaNoneSkipFirst | kCGBitmapByteOrder32Little), dp, NULL, false,
        kCGRenderingIntentDefault);
    CGColorSpaceRelease(cs);
    CGDataProviderRelease(dp);
    if (!full)
        return @"";
    CGImageRef crop = CGImageCreateWithImageInRect(full, CGRectMake(x1, y1, x2 - x1, y2 - y1));
    CGImageRelease(full);
    if (!crop)
        return @"";

    NSMutableString *out = [NSMutableString string];
    if (@available(iOS 13.0, *)) {
        VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] init];
        req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        req.usesLanguageCorrection = YES;
        VNImageRequestHandler *h = [[VNImageRequestHandler alloc] initWithCGImage:crop options:@{}];
        NSError *err = nil;
        if ([h performRequests:@[ req ] error:&err]) {
            for (VNRecognizedTextObservation *o in req.results) {
                VNRecognizedText *t = [[o topCandidates:1] firstObject];
                if (t.string.length) {
                    [out appendString:t.string];
                    [out appendString:@"\n"];
                }
            }
        }
    }
    CGImageRelease(crop);
    return out;
}

// HTTP đồng bộ (chạy trên luồng auto nền, chặn bằng semaphore — completion chạy
// ở luồng khác nên không deadlock). Trả về nội dung phản hồi (chuỗi).
static NSString *tvHttpRequest(NSString *method, NSString *urlStr, NSString *body, NSString *contentType) {
    NSURL *url = [NSURL URLWithString:urlStr];
    if (!url)
        return @"";
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:url];
    req.HTTPMethod = method;
    req.timeoutInterval = 20;
    if (body.length) {
        req.HTTPBody = [body dataUsingEncoding:NSUTF8StringEncoding];
        [req setValue:(contentType.length ? contentType : @"application/x-www-form-urlencoded")
            forHTTPHeaderField:@"Content-Type"];
    }
    __block NSString *result = @"";
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    NSURLSessionDataTask *task = [[NSURLSession sharedSession]
        dataTaskWithRequest:req
          completionHandler:^(NSData *data, NSURLResponse *resp, NSError *err) {
              if (data)
                  result = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
              dispatch_semaphore_signal(sem);
          }];
    [task resume];
    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(25 * NSEC_PER_SEC)));
    return result;
}

// Chạm cấp thấp GIỐNG HỆT đường VNC (touchDownAtPoints/liftUpAtPoints) — KHÔNG
// dùng -[STHIDEventGenerator tap:] vì nó gọi _sendTaps với delayBetweenTaps:0,
// vướng NSParameterAssert(delay > 0.0) → ném exception (chạm không tới nơi, có
// thể làm sập daemon khiến phiên VNC nối lại/màn đen). Đây là bộ HID mà điều
// khiển VNC đang dùng nên chắc chắn hoạt động.
static void tvAutoTap(STHIDEventGenerator *gen, CGPoint p, NSUInteger fingers) {
    if (fingers < 1)
        fingers = 1;
    if (fingers > 3)
        fingers = 3;
    CGPoint pts[3];
    for (NSUInteger i = 0; i < fingers; i++)
        pts[i] = CGPointMake(p.x + (CGFloat)(i * 24), p.y); // nhiều ngón: tách điểm
    [gen touchDownAtPoints:pts touchCount:fingers];
    usleep(60000); // giữ 60ms cho hệ nhận là một cú chạm
    [gen liftUpAtPoints:pts touchCount:fingers];
}

// ---- Tìm CHỮ trên màn (OCR + toạ độ) : findText -> tâm ô chứa chuỗi con ----
static BOOL tvFindText(NSString *needle, double rx1, double ry1, double rx2, double ry2,
                       double *foundRx, double *foundRy) {
    if (needle.length == 0 || !gScreen || !gScreen->frameBuffer || gWidth <= 0 || gHeight <= 0)
        return NO;
    int x1 = (int)(MAX(0.0, rx1) * (gWidth - 1));
    int y1 = (int)(MAX(0.0, ry1) * (gHeight - 1));
    int x2 = (int)((rx2 <= 0 ? 1.0 : rx2) * (gWidth - 1));
    int y2 = (int)((ry2 <= 0 ? 1.0 : ry2) * (gHeight - 1));
    if (x2 <= x1)
        x2 = gWidth - 1;
    if (y2 <= y1)
        y2 = gHeight - 1;

    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGDataProviderRef dp = CGDataProviderCreateWithData(
        NULL, gScreen->frameBuffer, (size_t)gScreen->paddedWidthInBytes * gHeight, NULL);
    CGImageRef full = CGImageCreate(
        gWidth, gHeight, 8, 32, gScreen->paddedWidthInBytes, cs,
        (CGBitmapInfo)(kCGImageAlphaNoneSkipFirst | kCGBitmapByteOrder32Little), dp, NULL, false,
        kCGRenderingIntentDefault);
    CGColorSpaceRelease(cs);
    CGDataProviderRelease(dp);
    if (!full)
        return NO;
    CGImageRef crop = CGImageCreateWithImageInRect(full, CGRectMake(x1, y1, x2 - x1, y2 - y1));
    CGImageRelease(full);
    if (!crop)
        return NO;

    BOOL found = NO;
    if (@available(iOS 13.0, *)) {
        VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] init];
        req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        req.usesLanguageCorrection = YES;
        VNImageRequestHandler *h = [[VNImageRequestHandler alloc] initWithCGImage:crop options:@{}];
        NSError *err = nil;
        NSString *low = [needle lowercaseString];
        if ([h performRequests:@[ req ] error:&err]) {
            for (VNRecognizedTextObservation *o in req.results) {
                VNRecognizedText *t = [[o topCandidates:1] firstObject];
                if (t.string.length && [[t.string lowercaseString] containsString:low]) {
                    CGRect bb = o.boundingBox; // chuẩn hoá theo CROP, gốc dưới-trái
                    double cx = bb.origin.x + bb.size.width / 2.0;
                    double cy = bb.origin.y + bb.size.height / 2.0;
                    double px = x1 + cx * (x2 - x1);
                    double py = y1 + (1.0 - cy) * (y2 - y1); // Vision y đảo -> dọc
                    if (foundRx) *foundRx = px / (double)(gWidth - 1);
                    if (foundRy) *foundRy = py / (double)(gHeight - 1);
                    found = YES;
                    break;
                }
            }
        }
    }
    CGImageRelease(crop);
    return found;
}

static NSData *tvCtlFindText(NSString *encoded) {
    NSData *raw = [[NSData alloc] initWithBase64EncodedString:encoded options:0];
    NSString *needle = raw ? [[NSString alloc] initWithData:raw encoding:NSUTF8StringEncoding] : nil;
    if (needle.length == 0)
        return [@"ERR BadText\n" dataUsingEncoding:NSUTF8StringEncoding];
    double x = 0, y = 0;
    BOOL found = tvFindText(needle, 0, 0, 1, 1, &x, &y);
    NSString *reply = found ? [NSString stringWithFormat:@"OK found %.4f %.4f\n", x, y]
                            : @"OK notfound\n";
    return [reply dataUsingEncoding:NSUTF8StringEncoding];
}

static NSData *tvCtlTypeText(NSString *encoded) {
    NSData *raw = [[NSData alloc] initWithBase64EncodedString:encoded options:0];
    NSString *value = raw ? [[NSString alloc] initWithData:raw encoding:NSUTF8StringEncoding] : nil;
    if (!value)
        return [@"ERR BadText\n" dataUsingEncoding:NSUTF8StringEncoding];
    STHIDEventGenerator *gen = [STHIDEventGenerator sharedGenerator];
    for (NSUInteger i = 0; i < value.length; i++)
        [gen keyPress:[value substringWithRange:NSMakeRange(i, 1)]];
    return [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
}

// ---- Biến BỀN qua các lần chạy (lưu JSON /var/mobile/Library/controlios) ----
static NSMutableDictionary *gVars = nil;
static NSString *tvVarsPath(void) { return @"/var/mobile/Library/controlios/vars.json"; }
static void tvVarsLoad(void) {
    if (gVars)
        return;
    NSData *d = [NSData dataWithContentsOfFile:tvVarsPath()];
    id o = d ? [NSJSONSerialization JSONObjectWithData:d options:0 error:NULL] : nil;
    gVars = [o isKindOfClass:[NSDictionary class]] ? [o mutableCopy] : [NSMutableDictionary dictionary];
}
static void tvVarsSave(void) {
    if (!gVars)
        return;
    [[NSFileManager defaultManager] createDirectoryAtPath:[tvVarsPath() stringByDeletingLastPathComponent]
                              withIntermediateDirectories:YES attributes:nil error:NULL];
    NSData *d = [NSJSONSerialization dataWithJSONObject:gVars options:0 error:NULL];
    [d writeToFile:tvVarsPath() atomically:YES];
}

// ---- Chạy đồng bộ trên luồng chính (UIKit) an toàn ----
static void tvRunMainSync(void (^block)(void)) {
    if ([NSThread isMainThread])
        block();
    else
        dispatch_sync(dispatch_get_main_queue(), block);
}
static NSString *tvGetClipboard(void) {
    __block NSString *s = @"";
    tvRunMainSync(^{ s = [UIPasteboard generalPasteboard].string ?: @""; });
    return s;
}
static void tvSetClipboard(NSString *s) {
    tvRunMainSync(^{ [UIPasteboard generalPasteboard].string = (s ?: @""); });
}

// ---- Lưu ảnh chụp màn hiện tại ra PNG ----
static BOOL tvSaveScreenshot(NSString *path) {
    if (!gScreen || !gScreen->frameBuffer || gWidth <= 0 || gHeight <= 0 || path.length == 0)
        return NO;
    CGColorSpaceRef cs = CGColorSpaceCreateDeviceRGB();
    CGDataProviderRef dp = CGDataProviderCreateWithData(
        NULL, gScreen->frameBuffer, (size_t)gScreen->paddedWidthInBytes * gHeight, NULL);
    CGImageRef img = CGImageCreate(
        gWidth, gHeight, 8, 32, gScreen->paddedWidthInBytes, cs,
        (CGBitmapInfo)(kCGImageAlphaNoneSkipFirst | kCGBitmapByteOrder32Little), dp, NULL, false,
        kCGRenderingIntentDefault);
    CGColorSpaceRelease(cs);
    CGDataProviderRelease(dp);
    if (!img)
        return NO;
    UIImage *ui = [UIImage imageWithCGImage:img];
    CGImageRelease(img);
    NSData *png = UIImagePNGRepresentation(ui);
    return [png writeToFile:path atomically:YES];
}

// Cài API native cho JS (kiểu AutoTouch). gen bắt trong block.
static void tvInstallJSApi(JSContext *ctx, STHIDEventGenerator *gen) {
    ctx.exceptionHandler = ^(JSContext *c, JSValue *e) {
        NSString *m = [e toString];
        if (![m containsString:@"__STOP__"]) {
            tvAutoLog([@"⚠ lỗi: " stringByAppendingString:(m ?: @"")]);
            TVLog(@"Auto-JS lỗi: %@", m);
        }
    };
    ctx[@"sleep"] = ^(double sec) {
        tvTrace([NSString stringWithFormat:@"sleep %.2fs", sec]);
        tvAutoSleep(sec);
        tvJSStopIfNeeded();
    };
    ctx[@"wait"] = ctx[@"sleep"];
    ctx[@"random"] = ^double(double a, double b) {
        return a + ((double)arc4random() / UINT32_MAX) * (b > a ? (b - a) : 0);
    };
    ctx[@"log"] = ^(NSString *m) {
        tvAutoLog(m);
        TVLog(@"Auto-JS: %@", m);
    };
    ctx[@"stop"] = ^{
        gAutoStop.store(true);
        tvJSStopIfNeeded();
    };
    ctx[@"screenWidth"] = ^int { return gSrcWidth; };
    ctx[@"screenHeight"] = ^int { return gSrcHeight; };

    // Bật/tắt tự ghi trace (mặc định BẬT). setTrace(false) để yên lặng.
    ctx[@"setTrace"] = ^(BOOL on) { gAutoTrace.store(on); };

    ctx[@"tap"] = ^(double x, double y) {
        tvTrace([NSString stringWithFormat:@"tap %.3f, %.3f", x, y]);
        tvAutoTap(gen, tvAutoPoint(x, y), 1);
        tvJSStopIfNeeded();
    };
    ctx[@"tapRegion"] = ^(double x1, double y1, double x2, double y2) {
        double rx = x1 + ((double)arc4random() / UINT32_MAX) * (x2 - x1);
        double ry = y1 + ((double)arc4random() / UINT32_MAX) * (y2 - y1);
        tvTrace([NSString stringWithFormat:@"tapRegion -> tap %.3f, %.3f", rx, ry]);
        tvAutoTap(gen, tvAutoPoint(rx, ry), 1);
        tvJSStopIfNeeded();
    };
    ctx[@"doubleTap"] = ^(double x, double y) {
        tvTrace([NSString stringWithFormat:@"doubleTap %.3f, %.3f", x, y]);
        CGPoint p = tvAutoPoint(x, y);
        tvAutoTap(gen, p, 1);
        usleep(120000);
        tvAutoTap(gen, p, 1);
        tvJSStopIfNeeded();
    };
    ctx[@"twoFingerTap"] = ^(double x, double y) {
        tvTrace([NSString stringWithFormat:@"twoFingerTap %.3f, %.3f", x, y]);
        tvAutoTap(gen, tvAutoPoint(x, y), 2);
        tvJSStopIfNeeded();
    };
    ctx[@"threeFingerTap"] = ^(double x, double y) {
        tvTrace([NSString stringWithFormat:@"threeFingerTap %.3f, %.3f", x, y]);
        tvAutoTap(gen, tvAutoPoint(x, y), 3);
        tvJSStopIfNeeded();
    };
    ctx[@"longPress"] = ^(double x, double y, double sec) {
        tvTrace([NSString stringWithFormat:@"longPress %.3f, %.3f (%.1fs)", x, y, sec > 0 ? sec : 0.6]);
        CGPoint p = tvAutoPoint(x, y);
        [gen touchDown:p];
        tvAutoSleep(sec > 0 ? sec : 0.6);
        [gen liftUp:p];
        tvJSStopIfNeeded();
    };
    ctx[@"swipe"] = ^(double x1, double y1, double x2, double y2, double sec) {
        tvTrace([NSString stringWithFormat:@"swipe %.3f,%.3f -> %.3f,%.3f", x1, y1, x2, y2]);
        [gen dragLinearWithStartPoint:tvAutoPoint(x1, y1)
                             endPoint:tvAutoPoint(x2, y2)
                             duration:sec > 0 ? sec : 0.3];
        tvJSStopIfNeeded();
    };
    ctx[@"home"] = ^{
        tvTrace(@"home");
        tvRecordHomeAudit(@"javascript-home", @"");
        [gen menuPress];
    };
    ctx[@"key"] = ^(NSString *k) { tvTrace([@"key " stringByAppendingString:(k ?: @"")]); [gen keyPress:k]; };
    ctx[@"typeText"] = ^(NSString *s) {
        tvTrace([@"typeText " stringByAppendingString:(s ?: @"")]);
        for (NSUInteger i = 0; i < s.length && !gAutoStop.load(); i++)
            [gen keyPress:[s substringWithRange:NSMakeRange(i, 1)]];
        tvJSStopIfNeeded();
    };

    ctx[@"getColor"] = ^NSString *(double x, double y) {
        uint8_t r, g, b;
        if (!tvSampleColor(x, y, &r, &g, &b)) {
            tvTrace([NSString stringWithFormat:@"getColor %.3f,%.3f = (không có khung)", x, y]);
            return @"";
        }
        NSString *hex = [NSString stringWithFormat:@"%02X%02X%02X", r, g, b];
        tvTrace([NSString stringWithFormat:@"getColor %.3f,%.3f = %@", x, y, hex]);
        return hex;
    };
    ctx[@"matchColor"] = ^BOOL(double x, double y, NSString *hex, double tol) {
        BOOL ok = tvColorAt(x, y, hex, tol);
        tvTrace([NSString stringWithFormat:@"matchColor %.3f,%.3f \"%@\" = %@", x, y, hex ?: @"",
                                           ok ? @"true" : @"false"]);
        return ok;
    };
    ctx[@"waitColor"] = ^BOOL(double x, double y, NSString *hex, double timeout, double tol) {
        double waited = 0, tmo = timeout > 0 ? timeout : 10;
        while (waited < tmo && !gAutoStop.load()) {
            if (tvColorAt(x, y, hex, tol)) {
                tvTrace([NSString stringWithFormat:@"waitColor %.3f,%.3f \"%@\" = true (%.1fs)", x, y,
                                                   hex ?: @"", waited]);
                return YES;
            }
            usleep(100000);
            waited += 0.1;
        }
        tvTrace([NSString stringWithFormat:@"waitColor %.3f,%.3f \"%@\" = false (hết %.0fs)", x, y,
                                           hex ?: @"", tmo]);
        return NO;
    };
    ctx[@"assistiveTouch"] = ^(BOOL on) { tvSetAssistiveTouch(on ? 1 : 0); };

    // App / URL (đóng-mở app theo bundle id, mở URL).
    ctx[@"launchApp"] = ^(NSString *b) { tvTrace([@"launchApp " stringByAppendingString:(b ?: @"")]); tvCtlLaunchApp(b); };
    ctx[@"killApp"] = ^(NSString *b) { tvTrace([@"killApp " stringByAppendingString:(b ?: @"")]); tvCtlTerminateApp(b); };
    ctx[@"openURL"] = ^(NSString *u) { tvTrace([@"openURL " stringByAppendingString:(u ?: @"")]); tvCtlOpenURL(u); };
    ctx[@"openURLIn"] = ^(NSString *b, NSString *u) { tvTrace([@"openURLIn " stringByAppendingString:(b ?: @"")]); tvCtlOpenURLInApp(b, u); };

    // Tệp (đọc/ghi chuỗi; JSON dùng JSON.parse/stringify có sẵn của JS).
    ctx[@"readFile"] = ^NSString *(NSString *p) {
        return [NSString stringWithContentsOfFile:p encoding:NSUTF8StringEncoding error:NULL] ?: @"";
    };
    ctx[@"writeFile"] = ^BOOL(NSString *p, NSString *s) {
        return [s writeToFile:p atomically:YES encoding:NSUTF8StringEncoding error:NULL];
    };
    ctx[@"fileExists"] = ^BOOL(NSString *p) {
        return [[NSFileManager defaultManager] fileExistsAtPath:p];
    };

    // HTTP (đồng bộ).
    ctx[@"httpGet"] = ^NSString *(NSString *u) { return tvHttpRequest(@"GET", u, nil, nil); };
    ctx[@"httpPost"] = ^NSString *(NSString *u, NSString *body, JSValue *ct) {
        return tvHttpRequest(@"POST", u, body, (ct && ![ct isUndefined]) ? [ct toString] : nil);
    };

    // Thông báo (banner trên máy) — daemon không có hộp thoại nên alert = banner.
    // Máy chỉ-TrollStore (không jailbreak) không vẽ HUD đè lên app được, nên
    // toast/alert chỉ GHI NHẬT KÝ — xem trên PC (Auto-click JS → ô Nhật ký).
    ctx[@"toast"] = ^(NSString *m) {
        NSString *message = [[(m ?: @"") stringByTrimmingCharactersInSet:
                              [NSCharacterSet whitespaceAndNewlineCharacterSet]] copy];
        tvAutoLog([@"toast: " stringByAppendingString:message]);
        if (!message.length)
            return;
        if (message.length > 240)
            message = [[message substringToIndex:240] stringByAppendingString:@"…"];
        dispatch_async(dispatch_get_main_queue(), ^{
            [[BulletinManager sharedManager] popBannerWithContent:message
                                                         userInfo:@{@"source": @"automation-toast"}];
        });
    };
    ctx[@"alert"] = ^(NSString *m) { tvAutoLog([@"alert: " stringByAppendingString:(m ?: @"")]); };

    // OCR: đọc chữ trong vùng màn (Vision). ocr([x1,y1,x2,y2]) -> chuỗi.
    ctx[@"ocr"] = ^NSString *(JSValue *x1, JSValue *y1, JSValue *x2, JSValue *y2) {
        double a = (x1 && ![x1 isUndefined]) ? [x1 toDouble] : 0;
        double b = (y1 && ![y1 isUndefined]) ? [y1 toDouble] : 0;
        double c = (x2 && ![x2 isUndefined]) ? [x2 toDouble] : 1;
        double d = (y2 && ![y2 isUndefined]) ? [y2 toDouble] : 1;
        return tvOCR(a, b, c, d);
    };

    // findImage(path[, x1,y1,x2,y2][, tol]) -> {x,y} (tỉ lệ) hoặc null.
    ctx[@"findImage"] =
        ^JSValue *(NSString *path, JSValue *x1, JSValue *y1, JSValue *x2, JSValue *y2, JSValue *tol) {
        double a = (x1 && ![x1 isUndefined]) ? [x1 toDouble] : 0;
        double b = (y1 && ![y1 isUndefined]) ? [y1 toDouble] : 0;
        double c = (x2 && ![x2 isUndefined]) ? [x2 toDouble] : 1;
        double d = (y2 && ![y2 isUndefined]) ? [y2 toDouble] : 1;
        double t = (tol && ![tol isUndefined]) ? [tol toDouble] : 24;
        JSContext *ct = [JSContext currentContext];
        double fx, fy;
        if (tvFindImage(path, a, b, c, d, t, &fx, &fy)) {
            tvTrace([NSString stringWithFormat:@"findImage %@ = %.3f,%.3f",
                                               [path lastPathComponent] ?: @"", fx, fy]);
            return [JSValue valueWithObject:@{@"x" : @(fx), @"y" : @(fy)} inContext:ct];
        }
        tvTrace([NSString stringWithFormat:@"findImage %@ = null", [path lastPathComponent] ?: @""]);
        return [JSValue valueWithNullInContext:ct];
    };

    // findText(chuoi[, x1,y1,x2,y2]) -> {x,y} (tỉ lệ tâm) hoặc null. So khớp
    // KHÔNG phân biệt hoa/thường, chứa chuỗi con.
    ctx[@"findText"] = ^JSValue *(NSString *needle, JSValue *x1, JSValue *y1, JSValue *x2, JSValue *y2) {
        double a = (x1 && ![x1 isUndefined]) ? [x1 toDouble] : 0;
        double b = (y1 && ![y1 isUndefined]) ? [y1 toDouble] : 0;
        double c = (x2 && ![x2 isUndefined]) ? [x2 toDouble] : 1;
        double d = (y2 && ![y2 isUndefined]) ? [y2 toDouble] : 1;
        JSContext *ct = [JSContext currentContext];
        double fx, fy;
        if (tvFindText(needle, a, b, c, d, &fx, &fy)) {
            tvTrace([NSString stringWithFormat:@"findText \"%@\" = %.3f,%.3f", needle ?: @"", fx, fy]);
            return [JSValue valueWithObject:@{@"x" : @(fx), @"y" : @(fy)} inContext:ct];
        }
        tvTrace([NSString stringWithFormat:@"findText \"%@\" = null", needle ?: @""]);
        return [JSValue valueWithNullInContext:ct];
    };

    // Thời gian: now() -> mốc mili-giây (dùng đo thời lượng, giới hạn tần suất).
    ctx[@"now"] = ^double { return [[NSDate date] timeIntervalSince1970] * 1000.0; };

    // Biến BỀN qua các lần chạy: setVar(khoá, giá trị) / getVar(khoá[, mặc định]).
    ctx[@"setVar"] = ^(NSString *k, JSValue *v) {
        tvVarsLoad();
        id val = (v && ![v isUndefined] && ![v isNull]) ? [v toObject] : [NSNull null];
        gVars[k ?: @""] = val ?: [NSNull null];
        tvVarsSave();
        tvTrace([NSString stringWithFormat:@"setVar %@", k ?: @""]);
    };
    ctx[@"getVar"] = ^JSValue *(NSString *k, JSValue *def) {
        tvVarsLoad();
        id val = gVars[k ?: @""];
        JSContext *ct = [JSContext currentContext];
        if (val && ![val isKindOfClass:[NSNull class]])
            return [JSValue valueWithObject:val inContext:ct];
        return (def && ![def isUndefined]) ? def : [JSValue valueWithNullInContext:ct];
    };

    // Bảng tạm (clipboard) của iOS.
    ctx[@"getClipboard"] = ^NSString * { return tvGetClipboard(); };
    ctx[@"setClipboard"] = ^(NSString *s) { tvSetClipboard(s); tvTrace(@"setClipboard"); };

    // Ảnh chụp màn hiện tại -> PNG.
    ctx[@"saveScreenshot"] = ^BOOL(NSString *path) {
        BOOL ok = tvSaveScreenshot(path);
        tvTrace([NSString stringWithFormat:@"saveScreenshot %@ = %@", path ?: @"", ok ? @"ok" : @"lỗi"]);
        return ok;
    };

    // Phím cứng: âm lượng / tắt tiếng / khoá màn (nút nguồn).
    ctx[@"volumeUp"] = ^{ tvTrace(@"volumeUp"); [gen volumeIncrementPress]; };
    ctx[@"volumeDown"] = ^{ tvTrace(@"volumeDown"); [gen volumeDecrementPress]; };
    ctx[@"mute"] = ^{ tvTrace(@"mute"); [gen mutePress]; };
    ctx[@"lockScreen"] = ^{ tvTrace(@"lockScreen"); [gen powerPress]; };
}

// Prelude JS: hàm tiện ích thuần JS dựng trên các API native, nạp TRƯỚC kịch bản.
static NSString *const kAutoPrelude =
    @"function swipeUp(d,t){d=d||0.4;swipe(0.5,0.5+d/2,0.5,0.5-d/2,t||0.3);}"
    @"function swipeDown(d,t){d=d||0.4;swipe(0.5,0.5-d/2,0.5,0.5+d/2,t||0.3);}"
    @"function swipeLeft(d,t){d=d||0.6;swipe(0.5+d/2,0.5,0.5-d/2,0.5,t||0.3);}"
    @"function swipeRight(d,t){d=d||0.6;swipe(0.5-d/2,0.5,0.5+d/2,0.5,t||0.3);}"
    @"function tapImage(p){var q=findImage(p);if(q){tap(q.x,q.y);return true;}return false;}"
    @"function tapText(s){var q=findText(s);if(q){tap(q.x,q.y);return true;}return false;}"
    @"function tapIfColor(x,y,c,tol){if(matchColor(x,y,c,tol===undefined?15:tol)){tap(x,y);return true;}return false;}"
    @"function waitImage(p,timeout){var t=timeout||10,w=0;while(w<t){var q=findImage(p);if(q)return q;sleep(0.4);w+=0.4;}return null;}"
    @"function waitText(s,timeout){var t=timeout||10,w=0;while(w<t){var q=findText(s);if(q)return q;sleep(0.4);w+=0.4;}return null;}"
    @"function repeat(n,fn){for(var i=0;i<n;i++)fn(i);}"
    @"function retry(n,fn){for(var i=0;i<n;i++){if(fn(i))return true;sleep(0.5);}return false;}";

// Thư viện hàm do PC ĐẨY xuống (setprelude) — nạp sau prelude built-in, cho phép
// thêm hàm tiện ích JS mới mà KHÔNG phải cài lại app.
static NSString *tvUserPreludePath(void) { return @"/var/mobile/Library/controlios/prelude.js"; }

static void tvAutoStart(void) {
    if (gAutoRunning.load())
        return;
    NSString *script = gAutoScript ?: @"";
    if (script.length == 0)
        return;
    gAutoStop.store(false);
    if (!gAutoQueue)
        gAutoQueue = dispatch_queue_create("com.controlios.autoclick", DISPATCH_QUEUE_SERIAL);
    dispatch_async(gAutoQueue, ^{
        @autoreleasepool {
            gAutoRunning.store(true);
            gAutoTrace.store(true); // mỗi lần chạy mặc định ghi tiến trình
            JSContext *ctx = [[JSContext alloc] init];
            tvInstallJSApi(ctx, [STHIDEventGenerator sharedGenerator]);
            [ctx evaluateScript:kAutoPrelude]; // hàm tiện ích (swipeUp, tapText, retry…)
            NSString *userLib = [NSString stringWithContentsOfFile:tvUserPreludePath()
                                                          encoding:NSUTF8StringEncoding error:NULL];
            if (userLib.length)
                [ctx evaluateScript:userLib]; // thư viện hàm PC đẩy xuống (không cần cài lại)
            tvAutoLog(@"▶ bắt đầu");
            TVLog(@"Auto-JS: chạy");
            [ctx evaluateScript:script]; // lỗi/dừng -> exceptionHandler nuốt gọn
            gAutoRunning.store(false);
            tvAutoLog(@"■ dừng");
            TVLog(@"Auto-JS: xong/dừng");
        }
    });
}

static void tvAutoStop(void) {
    gAutoStop.store(true);
}

static void tvAutoSetScript(NSString *script) {
    gAutoScript = [script copy];
    NSFileManager *fm = [NSFileManager defaultManager];
    [fm createDirectoryAtPath:[tvAutoScriptPath() stringByDeletingLastPathComponent]
   withIntermediateDirectories:YES
                    attributes:nil
                         error:NULL];
    [script writeToFile:tvAutoScriptPath() atomically:YES encoding:NSUTF8StringEncoding error:NULL];
}

static void tvAutoLoadFromDisk(void) {
    if (gAutoScript == nil) {
        NSString *s = [NSString stringWithContentsOfFile:tvAutoScriptPath()
                                                encoding:NSUTF8StringEncoding
                                                   error:NULL];
        if (s)
            gAutoScript = [s copy];
    }
}

#pragma mark - AssistiveTouch

// Bật/tắt AssistiveTouch của iOS (nút tròn nổi). mode: 0=tắt, 1=bật, 2=đảo.
// Dùng API riêng của libAccessibility (daemon root gọi được).
static BOOL tvSetAssistiveTouch(int mode) {
    void *h = dlopen("/usr/lib/libAccessibility.dylib", RTLD_LAZY);
    if (!h)
        return NO;
    BOOL (*isOn)(void) = (BOOL (*)(void))dlsym(h, "_AXSAssistiveTouchEnabled");
    void (*setOn)(BOOL) = (void (*)(BOOL))dlsym(h, "_AXSAssistiveTouchSetEnabled");
    if (!setOn)
        return NO;
    BOOL target = (mode == 2) ? (isOn ? !isOn() : YES) : (mode == 1);
    setOn(target);
    TVLog(@"AssistiveTouch -> %@", target ? @"BẬT" : @"TẮT");
    return YES;
}

// `assistivetouch on|off|toggle`
static NSData *tvCtlAssistiveTouch(NSString *arg) {
    arg = [[arg stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]
        lowercaseString];
    int mode = [arg isEqualToString:@"on"] ? 1 : ([arg isEqualToString:@"off"] ? 0 : 2);
    BOOL ok = tvSetAssistiveTouch(mode);
    void *h = dlopen("/usr/lib/libAccessibility.dylib", RTLD_LAZY);
    BOOL (*isOn)(void) = h ? (BOOL (*)(void))dlsym(h, "_AXSAssistiveTouchEnabled") : NULL;
    NSString *state = isOn ? (isOn() ? @"on" : @"off") : @"?";
    NSString *s = ok ? [NSString stringWithFormat:@"OK %@\n", state] : @"ERR Unavailable\n";
    return [s dataUsingEncoding:NSUTF8StringEncoding];
}

#pragma mark - App data reset

// mobile chạy uid/gid 501 trên iOS. File do root chép vào container phải được
// trả quyền về mobile, nếu không app (chạy dưới mobile) đọc/ghi không được và
// coi như hỏng dữ liệu.
static const uid_t kMobileUID = 501;
static const gid_t kMobileGID = 501;

// Đường dẫn container DỮ LIỆU của một app (nơi chứa Documents/Library/tmp...).
static NSString *tvDataContainerPath(NSString *bundleId) {
    LSApplicationWorkspace *ws = tvAppWorkspace();
    if (!ws)
        return nil;
    for (LSApplicationProxy *app in [ws allApplications]) {
        if ([app.applicationIdentifier isEqualToString:bundleId])
            return app.dataContainerURL.path;
    }
    return nil;
}

static NSString *tvSnapshotDir(NSString *bundleId) {
    return [@"/var/mobile/controlios-snap" stringByAppendingPathComponent:bundleId];
}

// Các thư mục dữ liệu do app quản. Cố tình KHÔNG đụng tới
// `.com.apple.mobile_container_manager.metadata.plist` ở gốc container — iOS cần
// nó để nhận diện container; xoá đi thì app có thể không mở lại được.
static NSArray<NSString *> *tvAppDataSubdirs(void) {
    return @[ @"Documents", @"Library", @"tmp", @"SystemData" ];
}

// Xoá sạch nội dung bên trong một thư mục nhưng GIỮ lại chính thư mục đó (để
// nguyên quyền sở hữu mobile của nó).
static BOOL tvEmptyDir(NSString *dir, NSFileManager *fm) {
    BOOL isDir = NO;
    if (![fm fileExistsAtPath:dir isDirectory:&isDir] || !isDir)
        return YES; // không có gì để làm
    BOOL ok = YES;
    for (NSString *name in [fm contentsOfDirectoryAtPath:dir error:NULL]) {
        NSError *e = nil;
        if (![fm removeItemAtPath:[dir stringByAppendingPathComponent:name] error:&e]) {
            TVLog(@"Control socket: wipe không xoá được %@/%@: %@", dir, name,
                  e.localizedDescription);
            ok = NO;
        }
    }
    return ok;
}

// Trả quyền sở hữu cả cây thư mục về mobile. lchown để không đi theo symlink.
static void tvChownTree(NSString *path, NSFileManager *fm) {
    lchown(path.fileSystemRepresentation, kMobileUID, kMobileGID);
    BOOL isDir = NO;
    if ([fm fileExistsAtPath:path isDirectory:&isDir] && isDir) {
        for (NSString *name in [fm contentsOfDirectoryAtPath:path error:NULL])
            tvChownTree([path stringByAppendingPathComponent:name], fm);
    }
}

// Chép cây thư mục CHỊU LỖI: bỏ qua từng file không chép được (socket/fifo,
// cache đang khoá, file thiếu quyền) thay vì để copyItemAtPath hỏng CẢ bản. App
// lớn (Shopee...) hay có file như vậy trong Library/Caches và tmp. Trả về YES
// miễn tạo được thư mục đích; file lẻ bỏ qua chỉ ghi log.
static BOOL tvCopyTree(NSString *src, NSString *dst, NSFileManager *fm) {
    NSDictionary *attrs = [fm attributesOfItemAtPath:src error:NULL];
    NSString *type = attrs.fileType;
    if ([type isEqualToString:NSFileTypeDirectory]) {
        NSError *de = nil;
        if (![fm createDirectoryAtPath:dst withIntermediateDirectories:YES
                            attributes:nil error:&de]) {
            TVLog(@"Control socket: copy không tạo được %@: %@", dst, de.localizedDescription);
            return NO;
        }
        for (NSString *child in [fm contentsOfDirectoryAtPath:src error:NULL])
            tvCopyTree([src stringByAppendingPathComponent:child],
                       [dst stringByAppendingPathComponent:child], fm);
        return YES;
    }
    if ([type isEqualToString:NSFileTypeRegular] || [type isEqualToString:NSFileTypeSymbolicLink]) {
        NSError *e = nil;
        if (![fm copyItemAtPath:src toPath:dst error:&e])
            TVLog(@"Control socket: copy bỏ qua %@ (%@)", src, e.localizedDescription);
        return YES; // file lẻ hỏng không làm hỏng cả bản
    }
    return YES; // socket/fifo/device: bỏ qua
}

// Các thư mục con dữ liệu — dùng để LỌC khỏi danh sách snapshot: bản snapshot cũ
// (kiểu một-bản) để lẫn 4 thư mục này ngay dưới <bundle>/ nên đừng coi là snapshot.
static BOOL tvIsReservedSubdir(NSString *name) {
    for (NSString *sub in tvAppDataSubdirs())
        if ([name isEqualToString:sub])
            return YES;
    return NO;
}

// `wipeapp <bundle id>` — xoá dữ liệu app (Documents/Library/tmp/SystemData) như
// vừa cài lại, nhưng GIỮ container. Nên `terminate` app trước khi gọi. Lưu ý:
// KHÔNG đụng keychain — token/khoá trong keychain vẫn còn.
static NSData *tvCtlWipeApp(NSString *bundleId) {
    bundleId = [bundleId stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (bundleId.length == 0)
        return [@"ERR BadArg\n" dataUsingEncoding:NSUTF8StringEncoding];
    NSString *data = tvDataContainerPath(bundleId);
    if (!data)
        return [@"NOT_FOUND\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    BOOL ok = YES;
    for (NSString *sub in tvAppDataSubdirs())
        ok = tvEmptyDir([data stringByAppendingPathComponent:sub], fm) && ok;

    TVLog(@"Control socket: wipeapp %@ -> %@", bundleId, ok ? @"OK" : @"PARTIAL");
    const char *raw = ok ? "OK\n" : "ERR Partial\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

// Tên snapshot hợp lệ: không rỗng, không quá dài, không chứa '/' hay '..' và
// không bắt đầu bằng '.' (chặn thoát thư mục và file ẩn).
static BOOL tvValidSnapName(NSString *name) {
    if (name.length == 0 || name.length > 64)
        return NO;
    if ([name rangeOfString:@"/"].location != NSNotFound)
        return NO;
    if ([name rangeOfString:@".."].location != NSNotFound)
        return NO;
    if ([name hasPrefix:@"."])
        return NO;
    return YES;
}

// Tên tự sinh theo thời gian máy khi người dùng không đặt tên.
static NSString *tvTimestampName(void) {
    NSDateFormatter *f = [NSDateFormatter new];
    f.dateFormat = @"yyyyMMdd-HHmmss";
    f.locale = [NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"];
    return [f stringFromDate:[NSDate date]];
}

// Tổng cỡ (byte) mọi file thường bên trong một thư mục.
static unsigned long long tvDirSize(NSString *path, NSFileManager *fm) {
    unsigned long long total = 0;
    NSDirectoryEnumerator *e = [fm enumeratorAtPath:path];
    for (NSString *sub in e) {
        (void)sub;
        NSDictionary *a = [e fileAttributes];
        if ([a.fileType isEqualToString:NSFileTypeRegular])
            total += a.fileSize;
    }
    return total;
}

// `snapshot <bundle id> [tên]` — lưu bản sao dữ liệu app hiện tại vào
// /var/mobile/controlios-snap/<bundle id>/<tên>/. Không đặt tên thì tự sinh theo
// thời gian. NHIỀU bản cùng lúc, mỗi tên một bản; cùng tên thì GHI ĐÈ. Bản
// snapshot nằm NGAY TRÊN MÁY nên không phải truyền qua PC.
static NSData *tvCtlSnapshotApp(NSString *bundleId, NSString *snapName) {
    bundleId = [bundleId stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    snapName = [snapName stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (bundleId.length == 0)
        return [@"ERR BadArg\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (snapName.length == 0)
        snapName = tvTimestampName();
    else if (!tvValidSnapName(snapName))
        return [@"ERR BadName\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSString *data = tvDataContainerPath(bundleId);
    if (!data)
        return [@"NOT_FOUND\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *snap = [tvSnapshotDir(bundleId) stringByAppendingPathComponent:snapName];
    [fm removeItemAtPath:snap error:NULL]; // trùng tên -> ghi đè
    NSError *e = nil;
    if (![fm createDirectoryAtPath:snap
       withIntermediateDirectories:YES
                        attributes:nil
                             error:&e]) {
        NSString *msg = [NSString stringWithFormat:@"ERR %@\n",
                                                   e.localizedDescription ?: @"CannotCreate"];
        return [msg dataUsingEncoding:NSUTF8StringEncoding];
    }

    for (NSString *sub in tvAppDataSubdirs()) {
        NSString *src = [data stringByAppendingPathComponent:sub];
        BOOL isDir = NO;
        if (![fm fileExistsAtPath:src isDirectory:&isDir] || !isDir)
            continue; // subdir chưa tồn tại thì bỏ qua
        // Chép chịu lỗi: bỏ qua file lẻ (socket/cache khoá) thay vì hỏng cả bản.
        tvCopyTree(src, [snap stringByAppendingPathComponent:sub], fm);
    }

    TVLog(@"Control socket: snapshot %@ '%@' -> OK", bundleId, snapName);
    NSString *okmsg = [NSString stringWithFormat:@"OK %@\n", snapName];
    return [okmsg dataUsingEncoding:NSUTF8StringEncoding];
}

// `snaplist <bundle id>` — TSV các bản snapshot: tên, thời điểm (epoch giây), cỡ
// byte. Chưa có bản nào thì trả về rỗng.
static NSData *tvCtlSnapshotList(NSString *bundleId) {
    bundleId = [bundleId stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (bundleId.length == 0)
        return [@"ERR BadArg\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *root = tvSnapshotDir(bundleId);
    BOOL isDir = NO;
    if (![fm fileExistsAtPath:root isDirectory:&isDir] || !isDir)
        return [NSData data]; // chưa có bản nào

    NSMutableString *out = [NSMutableString string];
    for (NSString *name in [fm contentsOfDirectoryAtPath:root error:NULL]) {
        if (tvIsReservedSubdir(name))
            continue; // sót lại từ bản snapshot cũ (một-bản) -> không phải snapshot
        NSString *full = [root stringByAppendingPathComponent:name];
        BOOL d = NO;
        if (![fm fileExistsAtPath:full isDirectory:&d] || !d)
            continue;
        NSDictionary *attrs = [fm attributesOfItemAtPath:full error:NULL];
        long long epoch = (long long)[attrs.fileModificationDate timeIntervalSince1970];
        unsigned long long size = tvDirSize(full, fm);
        [out appendFormat:@"%@\t%lld\t%llu\n",
                          [name stringByReplacingOccurrencesOfString:@"\t" withString:@" "],
                          epoch, size];
    }
    return [out dataUsingEncoding:NSUTF8StringEncoding];
}

// `restore <bundle id> <tên>` — thay dữ liệu app hiện tại bằng đúng bản snapshot
// tên đó. Nên `terminate` app trước, relaunch sau. File chép vào chown về mobile.
static NSData *tvCtlRestoreApp(NSString *bundleId, NSString *snapName) {
    bundleId = [bundleId stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    snapName = [snapName stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (bundleId.length == 0 || snapName.length == 0)
        return [@"ERR BadArg\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (!tvValidSnapName(snapName))
        return [@"ERR BadName\n" dataUsingEncoding:NSUTF8StringEncoding];
    NSString *data = tvDataContainerPath(bundleId);
    if (!data)
        return [@"NOT_FOUND\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *snap = [tvSnapshotDir(bundleId) stringByAppendingPathComponent:snapName];
    BOOL isDir = NO;
    if (![fm fileExistsAtPath:snap isDirectory:&isDir] || !isDir)
        return [@"ERR NoSnapshot\n" dataUsingEncoding:NSUTF8StringEncoding];

    BOOL ok = YES;
    for (NSString *sub in tvAppDataSubdirs()) {
        NSString *src = [snap stringByAppendingPathComponent:sub];
        BOOL srcDir = NO;
        if (![fm fileExistsAtPath:src isDirectory:&srcDir] || !srcDir)
            continue;
        NSString *dst = [data stringByAppendingPathComponent:sub];
        [fm removeItemAtPath:dst error:NULL]; // bỏ dữ liệu hiện tại
        if (!tvCopyTree(src, dst, fm)) {
            TVLog(@"Control socket: restore %@/%@ %@ FAIL", bundleId, snapName, sub);
            ok = NO;
            continue;
        }
        tvChownTree(dst, fm); // root vừa chép -> trả quyền về mobile
    }

    TVLog(@"Control socket: restore %@ '%@' -> %@", bundleId, snapName, ok ? @"OK" : @"PARTIAL");
    const char *raw = ok ? "OK\n" : "ERR Partial\n";
    return [NSData dataWithBytes:raw length:strlen(raw)];
}

// `snapdel <bundle id> <tên>` — xoá một bản snapshot.
static NSData *tvCtlSnapshotDelete(NSString *bundleId, NSString *snapName) {
    bundleId = [bundleId stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    snapName = [snapName stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (bundleId.length == 0 || snapName.length == 0)
        return [@"ERR BadArg\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (!tvValidSnapName(snapName))
        return [@"ERR BadName\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *snap = [tvSnapshotDir(bundleId) stringByAppendingPathComponent:snapName];
    BOOL isDir = NO;
    if (![fm fileExistsAtPath:snap isDirectory:&isDir] || !isDir)
        return [@"NOT_FOUND\n" dataUsingEncoding:NSUTF8StringEncoding];
    NSError *e = nil;
    if (![fm removeItemAtPath:snap error:&e]) {
        NSString *msg = [NSString stringWithFormat:@"ERR %@\n",
                                                   e.localizedDescription ?: @"CannotDelete"];
        return [msg dataUsingEncoding:NSUTF8StringEncoding];
    }
    TVLog(@"Control socket: snapdel %@ '%@' -> OK", bundleId, snapName);
    return [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
}

// `snapclear <bundle id>` — xoá TẤT CẢ snapshot của một app (cả thư mục
// /var/mobile/controlios-snap/<bundle id>/). Cũng dọn luôn dữ liệu sót của bản
// snapshot cũ (kiểu một-bản) nếu còn.
static NSData *tvCtlSnapshotClear(NSString *bundleId) {
    bundleId = [bundleId stringByTrimmingCharactersInSet:
                             [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (bundleId.length == 0)
        return [@"ERR BadArg\n" dataUsingEncoding:NSUTF8StringEncoding];

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *root = tvSnapshotDir(bundleId);
    BOOL isDir = NO;
    if (![fm fileExistsAtPath:root isDirectory:&isDir] || !isDir)
        return [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding]; // vốn không có gì
    NSError *e = nil;
    if (![fm removeItemAtPath:root error:&e]) {
        NSString *msg = [NSString stringWithFormat:@"ERR %@\n",
                                                   e.localizedDescription ?: @"CannotDelete"];
        return [msg dataUsingEncoding:NSUTF8StringEncoding];
    }
    TVLog(@"Control socket: snapclear %@ -> OK", bundleId);
    return [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
}

void tvCtlHandleConnection(int cfd, struct sockaddr_in caddr) {
    // Log peer and set short timeouts
    char ipbuf[INET_ADDRSTRLEN] = {0};
    const char *ip = inet_ntop(AF_INET, &caddr.sin_addr, ipbuf, sizeof(ipbuf));
    TVLog(@"Control socket: connection from %s:%d (fd=%d)", ip ? ip : "?", ntohs(caddr.sin_port), cfd);

    struct timeval tv;
    tv.tv_sec = 2;
    tv.tv_usec = 0;
    setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(cfd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    // On Darwin an accepted socket inherits O_NONBLOCK from the listener, so
    // the first recv() can return EAGAIN before the client's line arrives and
    // the command is read as empty. SO_RCVTIMEO above already bounds the wait.
    int ctlFlags = fcntl(cfd, F_GETFL, 0);
    if (ctlFlags >= 0)
        fcntl(cfd, F_SETFL, ctlFlags & ~O_NONBLOCK);

    // Read a single line command
    uint8_t buf[1024];
    size_t off = 0;
    for (;;) {
        ssize_t n = recv(cfd, buf + off, sizeof(buf) - off, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (n == 0)
            break;
        off += (size_t)n;
        if (off >= sizeof(buf))
            break;
        if (memchr(buf, '\n', off))
            break;
    }

    // Parse command
    // Split at the first newline. For `put` everything after it is already the
    // start of the payload, so it must not be parsed as text — and it must not
    // be dropped either, or the received file loses its first bytes.
    uint8_t *newline = (uint8_t *)memchr(buf, '\n', off);
    size_t lineLength = newline ? (size_t)(newline - buf) : off;
    const uint8_t *pending = newline ? newline + 1 : buf + off;
    size_t pendingLength = newline ? off - lineLength - 1 : 0;

    NSString *cmd = [[NSString alloc] initWithBytes:buf
                                             length:lineLength
                                           encoding:NSUTF8StringEncoding];
    if (!cmd)
        cmd = @"";
    cmd = [cmd stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];

    // This port can disconnect every client and launch apps, so anything
    // coming from outside the device must present the token. Loopback keeps
    // working without one so the on-device TrollVNC app is unaffected.
    BOOL isLoopback = (caddr.sin_addr.s_addr == htonl(INADDR_LOOPBACK));

    // Kích hoạt bản quyền. Cho phép `license` (hỏi trạng thái) và `relicense`
    // (nạp lại sau khi app ghi file license) KỂ CẢ khi chưa kích hoạt — để app
    // kích hoạt được. Mọi lệnh khác đòi license hợp lệ.
    if ([cmd isEqualToString:@"license"]) {
        NSData *st = tvCtlLicenseStatus();
        tvCtlWriteAll(cfd, st.bytes, st.length);
        close(cfd);
        return;
    }
    if ([cmd isEqualToString:@"relicense"]) {
        tvLicenseLoad();
        NSData *st = tvCtlLicenseStatus();
        tvCtlWriteAll(cfd, st.bytes, st.length);
        close(cfd);
        return;
    }
    if (CIOS_ENFORCE_LICENSE && !gLicenseValid) {
        const char *deny = "ERR NotActivated\n";
        tvCtlWriteAll(cfd, deny, strlen(deny));
        close(cfd);
        return;
    }

    if (!isLoopback && cmd.length > 0) {
        NSString *prefix =
            gTvCtlToken.length > 0 ? [NSString stringWithFormat:@"auth %@ ", gTvCtlToken] : nil;
        if (prefix && [cmd hasPrefix:prefix]) {
            cmd = [[cmd substringFromIndex:prefix.length]
                stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        } else {
            TVLog(@"Control socket: unauthorized command from %s", ip ? ip : "?");
            const char *deny = "ERR Unauthorized\n";
              tvCtlWriteAll(cfd, deny, strlen(deny));
            close(cfd);
            return;
        }
    }

    NSData *resp = nil;
    BOOL keepOpen = NO;
    if (cmd.length == 0) {
        resp = [@"ERR Empty\n" dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd isEqualToString:@"count"]) {
        NSString *s = [NSString stringWithFormat:@"%d\n", gClientCount];
        resp = [s dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd isEqualToString:@"list"]) {
        resp = tvCtlTSVForList();
    } else if ([cmd isEqualToString:@"subscribe on"]) {
        tvCtlAddSubscriber(cfd);
        const char *ok = "OK\n";
        resp = [NSData dataWithBytes:ok length:strlen(ok)];
        keepOpen = YES; // keep connection open for pushes
    } else if ([cmd isEqualToString:@"subscribe off"]) {
        tvCtlRemoveSubscriber(cfd, NO);
        const char *ok = "OK\n";
        resp = [NSData dataWithBytes:ok length:strlen(ok)];
    } else if ([cmd hasPrefix:@"disconnect "] || [cmd hasPrefix:@"kick "] || [cmd hasPrefix:@"block "]) {
        NSArray *parts = [cmd componentsSeparatedByCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
        NSString *cid = parts.count >= 2 ? parts[1] : @"";
        if ([cid isEqualToString:@"ALL"]) {
            tvDisconnectAllClients();
            resp = [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
        } else if (cid.length != 8) {
            resp = [@"ERR InvalidID\n" dataUsingEncoding:NSUTF8StringEncoding];
        } else {
            BOOL shouldBlock = [cmd hasPrefix:@"block "];
            resp = tvCtlTextForKick(cid, shouldBlock);
        }
    } else if ([cmd isEqualToString:@"apps"]) {
        resp = tvCtlTSVForApps();
    } else if ([cmd hasPrefix:@"launch "]) {
        resp = tvCtlLaunchApp([[cmd substringFromIndex:7]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else if ([cmd hasPrefix:@"container "]) {
        resp = tvCtlContainerForApp([[cmd substringFromIndex:10]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else if ([cmd hasPrefix:@"ls "]) {
        resp = tvCtlListDirectory([[cmd substringFromIndex:3]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else if ([cmd hasPrefix:@"getfile "]) {
        tvCtlSendSnapshotFile(cfd, [[cmd substringFromIndex:8]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else if ([cmd hasPrefix:@"put "]) {
        resp = tvCtlReceiveFile(cfd, [cmd substringFromIndex:4], pending, pendingLength);
    } else if ([cmd hasPrefix:@"clipset "]) {
        resp = tvCtlSetClipboard(cfd, [cmd substringFromIndex:8], pending, pendingLength);
    } else if ([cmd isEqualToString:@"clipget"]) {
        resp = tvCtlGetClipboard();
    } else if ([cmd hasPrefix:@"savephoto "]) {
        resp = tvCtlSavePhoto([cmd substringFromIndex:10]);
    } else if ([cmd isEqualToString:@"respring"]) {
        resp = tvCtlRespring();
    } else if ([cmd isEqualToString:@"reboot"]) {
        resp = tvCtlReboot();
    } else if ([cmd isEqualToString:@"shutdown"]) {
        resp = tvCtlShutdown();
    } else if ([cmd isEqualToString:@"wakeiflocked"]) {
        resp = tvCtlWakeIfLocked();
    } else if ([cmd isEqualToString:@"homeaudit"]) {
        resp = tvCtlHomeAudit(NO);
    } else if ([cmd isEqualToString:@"homeaudit clear"]) {
        resp = tvCtlHomeAudit(YES);
    } else if ([cmd isEqualToString:@"controlcenter"]) {
        resp = tvCtlControlCenter();
    } else if ([cmd hasPrefix:@"rotationlock "]) {
        resp = tvCtlRotationLock([cmd substringFromIndex:13]);
    } else if ([cmd isEqualToString:@"frontmost"]) {
        resp = tvCtlFrontmostApp();
    } else if ([cmd hasPrefix:@"touchlock "]) {
        resp = tvCtlTouchLock([cmd substringFromIndex:10]);
    } else if ([cmd hasPrefix:@"assistivetouch "]) {
        resp = tvCtlAssistiveTouch([cmd substringFromIndex:15]);
    } else if ([cmd hasPrefix:@"keeper "]) {
        resp = tvCtlKeeper([cmd substringFromIndex:7]);
    } else if ([cmd isEqualToString:@"diagnostics"]) {
        resp = tvCtlDiagnostics();
    } else if ([cmd hasPrefix:@"setscale "]) {
        resp = tvCtlSetScale([cmd substringFromIndex:9]);
    } else if ([cmd hasPrefix:@"findtext64 "]) {
        resp = tvCtlFindText([cmd substringFromIndex:11]);
    } else if ([cmd hasPrefix:@"typetext64 "]) {
        resp = tvCtlTypeText([cmd substringFromIndex:11]);
    } else if ([cmd hasPrefix:@"openurlin "]) {
        NSString *rest = [cmd substringFromIndex:10];
        NSRange space = [rest rangeOfString:@" "];
        if (space.location == NSNotFound) {
            resp = [@"ERR Usage openurlin <bundle id> <url>\n"
                dataUsingEncoding:NSUTF8StringEncoding];
        } else {
            resp = tvCtlOpenURLInApp(
                [rest substringToIndex:space.location],
                [[rest substringFromIndex:space.location + 1]
                    stringByTrimmingCharactersInSet:[NSCharacterSet
                                                        whitespaceAndNewlineCharacterSet]]);
        }
    } else if ([cmd hasPrefix:@"openurl "]) {
        resp = tvCtlOpenURL([[cmd substringFromIndex:8]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else if ([cmd hasPrefix:@"terminate "]) {
        resp = tvCtlTerminateApp([[cmd substringFromIndex:10]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]]);
    } else if ([cmd hasPrefix:@"wipeapp "]) {
        resp = tvCtlWipeApp([cmd substringFromIndex:8]);
    } else if ([cmd hasPrefix:@"snapshot "]) {
        NSString *rest = [[cmd substringFromIndex:9]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        NSRange sp = [rest rangeOfString:@" "];
        NSString *bid = (sp.location == NSNotFound) ? rest : [rest substringToIndex:sp.location];
        NSString *nm = (sp.location == NSNotFound) ? @"" : [rest substringFromIndex:sp.location + 1];
        resp = tvCtlSnapshotApp(bid, nm);
    } else if ([cmd hasPrefix:@"snaplist "]) {
        resp = tvCtlSnapshotList([cmd substringFromIndex:9]);
    } else if ([cmd hasPrefix:@"snapclear "]) {
        resp = tvCtlSnapshotClear([cmd substringFromIndex:10]);
    } else if ([cmd hasPrefix:@"snapdel "]) {
        NSString *rest = [[cmd substringFromIndex:8]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        NSRange sp = [rest rangeOfString:@" "];
        NSString *bid = (sp.location == NSNotFound) ? rest : [rest substringToIndex:sp.location];
        NSString *nm = (sp.location == NSNotFound) ? @"" : [rest substringFromIndex:sp.location + 1];
        resp = tvCtlSnapshotDelete(bid, nm);
    } else if ([cmd hasPrefix:@"restore "]) {
        NSString *rest = [[cmd substringFromIndex:8]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        NSRange sp = [rest rangeOfString:@" "];
        NSString *bid = (sp.location == NSNotFound) ? rest : [rest substringToIndex:sp.location];
        NSString *nm = (sp.location == NSNotFound) ? @"" : [rest substringFromIndex:sp.location + 1];
        resp = tvCtlRestoreApp(bid, nm);
    } else if ([cmd hasPrefix:@"autoset "]) {
        NSData *raw = [[NSData alloc] initWithBase64EncodedString:[cmd substringFromIndex:8] options:0];
        NSString *script = raw ? [[NSString alloc] initWithData:raw encoding:NSUTF8StringEncoding] : nil;
        if (!script) {
            resp = [@"ERR BadScript\n" dataUsingEncoding:NSUTF8StringEncoding];
        } else {
            tvAutoSetScript(script);
            resp = [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
        }
    } else if ([cmd isEqualToString:@"autostart"]) {
        tvAutoStart();
        resp = [(gAutoScript.length == 0 ? @"ERR NoScript\n"
                                         : (gAutoRunning.load() ? @"OK running\n" : @"OK started\n"))
            dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd isEqualToString:@"autostop"]) {
        tvAutoStop();
        resp = [@"OK stopped\n" dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd isEqualToString:@"autostatus"]) {
        resp = [(gAutoRunning.load() ? @"OK running\n" : @"OK stopped\n")
            dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd isEqualToString:@"autolog"]) {
        // Trạng thái + nhật ký (base64 để không vướng xuống dòng).
        NSString *b64 = [[tvAutoLogText() dataUsingEncoding:NSUTF8StringEncoding]
            base64EncodedStringWithOptions:0];
        NSString *s = [NSString stringWithFormat:@"OK %@ %@\n",
                                                 gAutoRunning.load() ? @"running" : @"stopped", b64];
        resp = [s dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd isEqualToString:@"autologclear"]) {
        if (!gAutoLogLock)
            gAutoLogLock = [NSObject new];
        @synchronized(gAutoLogLock) {
            [gAutoLog removeAllObjects];
        }
        resp = [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd isEqualToString:@"autoget"]) {
        tvAutoLoadFromDisk();
        NSString *b64 = [[(gAutoScript ?: @"") dataUsingEncoding:NSUTF8StringEncoding]
            base64EncodedStringWithOptions:0];
        resp = [[NSString stringWithFormat:@"OK %@\n", b64] dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd hasPrefix:@"setprelude "]) {
        // Đẩy THƯ VIỆN HÀM (JS) từ PC — nạp trước mọi kịch bản, KHÔNG cần cài lại.
        NSData *raw = [[NSData alloc] initWithBase64EncodedString:[cmd substringFromIndex:11] options:0];
        NSString *js = raw ? [[NSString alloc] initWithData:raw encoding:NSUTF8StringEncoding] : nil;
        if (js == nil) {
            resp = [@"ERR BadData\n" dataUsingEncoding:NSUTF8StringEncoding];
        } else {
            [[NSFileManager defaultManager]
                      createDirectoryAtPath:[tvUserPreludePath() stringByDeletingLastPathComponent]
                withIntermediateDirectories:YES attributes:nil error:NULL];
            [js writeToFile:tvUserPreludePath() atomically:YES encoding:NSUTF8StringEncoding error:NULL];
            resp = [@"OK\n" dataUsingEncoding:NSUTF8StringEncoding];
        }
    } else if ([cmd isEqualToString:@"getprelude"]) {
        NSString *js = [NSString stringWithContentsOfFile:tvUserPreludePath()
                                                 encoding:NSUTF8StringEncoding error:NULL] ?: @"";
        NSString *b64 = [[js dataUsingEncoding:NSUTF8StringEncoding] base64EncodedStringWithOptions:0];
        resp = [[NSString stringWithFormat:@"OK %@\n", b64] dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd hasPrefix:@"setinflight "]) {
        // Số khung tối đa đang mã hoá trước khi BỎ khung mới (Q). 1 = độ trễ thấp
        // nhất (bỏ khung cũ). KHÔNG resize -> không nối lại.
        long n = strtol([[cmd substringFromIndex:12] UTF8String], NULL, 10);
        if (n < 0) n = 0;
        if (n > 8) n = 8;
        gMaxInflightUpdates = (int)n;
        resp = [[NSString stringWithFormat:@"OK %d\n", gMaxInflightUpdates]
            dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd hasPrefix:@"setdefer "]) {
        // Cửa sổ gộp khung (giây). Nhỏ = trễ thấp hơn; lớn = nhẹ CPU/băng thông hơn.
        double s = [[cmd substringFromIndex:9] doubleValue];
        if (s < 0) s = 0;
        if (s > 0.5) s = 0.5;
        gDeferWindowSec = s;
        resp = [[NSString stringWithFormat:@"OK %.3f\n", gDeferWindowSec]
            dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd hasPrefix:@"setorient "]) {
        // Bật/tắt đồng bộ xoay. TẮT -> bỏ qua xoay -> không resize framebuffer khi
        // app xoay -> HẾT chớp đen giữa chừng (hợp farm không cần xoay).
        NSString *a = [[cmd substringFromIndex:10]
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        gOrientationSyncEnabled = [a isEqualToString:@"on"] ? YES : NO;
        resp = [[NSString stringWithFormat:@"OK %@\n", gOrientationSyncEnabled ? @"on" : @"off"]
            dataUsingEncoding:NSUTF8StringEncoding];
    } else if ([cmd hasPrefix:@"color "]) {
        // color <rx> <ry> : đọc MÀU THẬT tại điểm tỉ lệ trên framebuffer -> OK RRGGBB
        // Dùng cho PC lấy màu chuẩn (đúng cái getColor/matchColor auto-click dùng).
        NSArray *parts = [cmd componentsSeparatedByCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
        if (parts.count >= 3) {
            double rx = [parts[1] doubleValue], ry = [parts[2] doubleValue];
            uint8_t r, g, b;
            if (tvSampleColor(rx, ry, &r, &g, &b)) {
                resp = [[NSString stringWithFormat:@"OK %02X%02X%02X\n", r, g, b]
                    dataUsingEncoding:NSUTF8StringEncoding];
            } else {
                resp = [@"ERR NoFrame\n" dataUsingEncoding:NSUTF8StringEncoding];
            }
        } else {
            resp = [@"ERR Args\n" dataUsingEncoding:NSUTF8StringEncoding];
        }
    } else {
        resp = [@"ERR Unknown\n" dataUsingEncoding:NSUTF8StringEncoding];
    }
    if (resp)
        tvCtlWriteAll(cfd, resp.bytes, resp.length);

    if (keepOpen) {
        // Do not close; subscriber lifecycle managed elsewhere
        return;
    }

    close(cfd);
}

#pragma mark - User Notifications

static void tvPublishUserSingleNotifs(void) {
    if (!gUserSingleNotifsEnabled || isRepeaterEnabled())
        return;

    BulletinManager *mgr = [BulletinManager sharedManager];

    if (gClientCount == 0) {
        dispatch_async(dispatch_get_main_queue(), ^(void) {
            [mgr revokeSingleNotification];
        });
        return;
    }

    NSDictionary *userInfo = @{
        @"clientCount" : @(gClientCount),
    };

    NSString *localizedContentTmpl;
    localizedContentTmpl = (gClientCount == 1) ? LocalizedString(@"There is %d active VNC client.", @"Localizable",
                                                                 tvLocalizationBundle(), @"trollvncserver")
                                               : LocalizedString(@"There are %d active VNC clients.", @"Localizable",
                                                                 tvLocalizationBundle(), @"trollvncserver");

    NSString *localizedContent = [NSString stringWithFormat:localizedContentTmpl, gClientCount];
    dispatch_async(dispatch_get_main_queue(), ^(void) {
        [mgr updateSingleBannerWithContent:localizedContent badgeCount:gClientCount userInfo:userInfo];
    });
}

static void tvPublishClientConnectedNotif(NSString *host) {
    if (!gUserClientNotifsEnabled || isRepeaterEnabled() || !host || host.length == 0)
        return;

    // Check if host is a loopback address
    if ([host isEqualToString:@"127.0.0.1"] || [host isEqualToString:@"::1"] || [host isEqualToString:@"localhost"] ||
        [host hasPrefix:@"127."] || [host hasPrefix:@"::ffff:127."]) {
        TVLog(@"Skipping notification for loopback connection from %@", host);
        return;
    }

    BulletinManager *mgr = [BulletinManager sharedManager];

    NSDictionary *userInfo = @{
        @"clientHost" : host,
    };

    NSString *localizedContentTmpl;
    localizedContentTmpl =
        LocalizedString(@"A VNC client connected from %@.", @"Localizable", tvLocalizationBundle(), @"trollvncserver");

    NSString *localizedContent = [NSString stringWithFormat:localizedContentTmpl, host];
    dispatch_async(dispatch_get_main_queue(), ^(void) {
        [mgr popBannerWithContent:localizedContent userInfo:userInfo];
    });
}

static void tvPublishClientDisconnectedNotif(NSString *host) {
    if (!gUserClientNotifsEnabled || !host || host.length == 0)
        return;

    BulletinManager *mgr = [BulletinManager sharedManager];

    NSDictionary *userInfo = @{
        @"clientHost" : host,
    };

    NSString *localizedContentTmpl;
    localizedContentTmpl = LocalizedString(@"A VNC client disconnected from %@.", @"Localizable",
                                           tvLocalizationBundle(), @"trollvncserver");

    NSString *localizedContent = [NSString stringWithFormat:localizedContentTmpl, host];
    dispatch_async(dispatch_get_main_queue(), ^(void) {
        [mgr popBannerWithContent:localizedContent userInfo:userInfo];
    });
}

#pragma mark - Client Handlers

static BOOL gIsCaptureStarted = NO;
static BOOL gIsClipboardStarted = NO;

#if !TARGET_OS_SIMULATOR
static BOOL gRestoreAssist = NO;
#endif

static void clientGoneHook(rfbClientPtr cl) {
    // Free per-client state
    TVClientState *st = tvGetClientState(cl);
    BOOL isRepeaterClient = NO;
    NSString *removeKey = nil;
    if (st) {
        isRepeaterClient = st->isRepeaterClient;
        if (st->clientId8[0] != '\0') {
            removeKey = [NSString stringWithUTF8String:st->clientId8];
        }
        free(st);
        cl->clientData = NULL;
    }

    // Remove by cached id (fallback to fd-derived if unavailable)
    if (!removeKey)
        removeKey = tvGenerateClientId8(cl->sock);
    if (removeKey && gClientStates) {
        @synchronized(gClientStates) {
            [gClientStates removeObjectForKey:removeKey];
        }
    }

    // Decrement client count and stop capture if this was the last client.
    if (gClientCount > 0)
        gClientCount--;

    NSString *host = (cl && cl->host) ? [NSString stringWithUTF8String:cl->host] : @"";
    TVLog(@"Client %@ disconnected, active clients=%d", host, gClientCount);

    if (gIsCaptureStarted && gClientCount == 0) {
        [[ScreenCapturer sharedCapturer] endCapture];
        gIsCaptureStarted = NO;
        TVLog(@"No clients remaining; screen capture stopped.");
    }

    if (gIsClipboardStarted && gClientCount == 0) {
        [[ClipboardManager sharedManager] stop];
        gIsClipboardStarted = NO;
        TVLog(@"No clients remaining; clipboard listening stopped.");
    }

#if !TARGET_OS_SIMULATOR
    // AutoAssist: disable AssistiveTouch if we enabled it and no clients remain
    if (gClientCount == 0 && gRestoreAssist) {
        gRestoreAssist = NO;
        [PSAssistiveTouchSettingsDetail setEnabled:NO];
    }
#endif

    // Update TXT with possibly changed state (e.g., viewOnly unaffected, but keep consistent)
    refreshBonjourTXTRecord();

    // Notify subscribers after removal (debounced)
    tvCtlScheduleBroadcastChanged();

    // Update user notification
    tvPublishUserSingleNotifs();

    // Notify client disconnected
    tvPublishClientDisconnectedNotif(host);

    // Stop the main run loop if this was a repeater client
    if (isRepeaterClient) {
        CFRunLoopStop(CFRunLoopGetMain());
    }
}

static enum rfbNewClientAction newClientHook(rfbClientPtr cl) {
    // Gác cổng bản quyền: chưa kích hoạt (sai/thiếu/hết hạn license) thì từ chối.
    if (CIOS_ENFORCE_LICENSE && !gLicenseValid) {
        TVLog(@"VNC: từ chối client — chưa kích hoạt bản quyền");
        return RFB_CLIENT_REFUSE;
    }
    cl->clientGoneHook = clientGoneHook;
    if (!cl->viewOnly && gViewOnly)
        cl->viewOnly = TRUE;

    // Allocate per-client state bag
    TVClientState *st = (TVClientState *)calloc(1, sizeof(TVClientState));
    if (st) {
        st->lastButtonMask = 0;
        st->wheelAccumPx = 0;
        st->wheelFlushScheduled = NO;
        st->clientId8[0] = '\0';
        cl->clientData = st;
    }

    gClientCount++;
    TVLog(@"Client connected, active clients=%d", gClientCount);
    tvScheduleInitialUnlockCheckAfterFirstClient();

    // Client mới cần một framebuffer đầy đủ ngay cả khi màn hình đang đứng yên.
    // Nếu không, kênh input vẫn chạy nhưng ô trên PC có thể đen vô thời hạn.
    [[ScreenCapturer sharedCapturer] forceNextFrameUpdate];

    // Add to global client states
    NSString *clientId = tvGenerateClientId8(cl->sock);
    if (st && clientId.length) {
        // Cache into fixed buffer
        const char *u8 = [clientId UTF8String];
        if (u8) {
            size_t n = strnlen(u8, 8);
            memcpy(st->clientId8, u8, n);
            st->clientId8[n] = '\0';
        }
    }
    NSString *host = (cl && cl->host) ? [NSString stringWithUTF8String:cl->host] : @"";
    NSDate *now = [NSDate date];
    NSDictionary *entry = @{
        @"id" : clientId,
        @"host" : host,
        @"viewOnly" : @(cl->viewOnly ? YES : NO),
        @"connectAt" : now,
    };

    if (!gClientStates)
        gClientStates = [[NSMutableDictionary alloc] init];
    gClientStates[clientId] = entry;

    // Update TXT (e.g., potential dynamic flags in future)
    refreshBonjourTXTRecord();

    // Notify subscribers (debounced)
    tvCtlScheduleBroadcastChanged();

    // Update user notification
    tvPublishUserSingleNotifs();

    // Notify client connected
    tvPublishClientConnectedNotif(host);

    if (!gIsCaptureStarted && gClientCount > 0 && gFrameHandler) {
        // Start capture when entering non-zero client population.
        gIsCaptureStarted = YES;
        [[ScreenCapturer sharedCapturer] startCaptureWithFrameHandler:gFrameHandler];
        TVLog(@"Screen capture started (clients=%d).", gClientCount);
    }

    if (gClipboardEnabled && !gIsClipboardStarted && gClientCount > 0) {
        gIsClipboardStarted = YES;
        [[ClipboardManager sharedManager] start];
        TVLog(@"Clipboard listening started (clients=%d).", gClientCount);
    }

#if !TARGET_OS_SIMULATOR
    // AutoAssist: enable AssistiveTouch if not already enabled
    if (gClientCount > 0 && gAutoAssistEnabled && ![PSAssistiveTouchSettingsDetail isEnabled]) {
        gRestoreAssist = YES;
        [PSAssistiveTouchSettingsDetail setEnabled:YES];
    }
#endif

    return RFB_CLIENT_ACCEPT;
}

#pragma mark - Clipboard Extension

static std::atomic<int> gClipboardSuppressSend(0); // >0 means suppress sending clipboard to clients

static void setXCutTextLatin1(char *str, int len, rfbClientPtr cl) {
    (void)cl;
    if (!str || len < 0)
        len = 0;

    TVLog(@"Clipboard: received client cut text (Latin-1) len=%d", len);
    NSData *data = [NSData dataWithBytes:str length:(NSUInteger)len];
    NSString *s = [[NSString alloc] initWithData:data encoding:NSISOLatin1StringEncoding];
    if (!s)
        s = @"";

    dispatch_async(dispatch_get_main_queue(), ^{
        gClipboardSuppressSend.fetch_add(1, std::memory_order_relaxed);

        TVLog(@"Clipboard: applying client text to UIPasteboard (Latin-1), suppression now=%d",
              gClipboardSuppressSend.load(std::memory_order_relaxed));
        [[ClipboardManager sharedManager] setStringFromRemote:s];

        gClipboardSuppressSend.fetch_sub(1, std::memory_order_relaxed);
    });
}

static void setXCutTextUTF8(char *str, int len, rfbClientPtr cl) {
    (void)cl;
    if (!str || len < 0)
        len = 0;

    TVLog(@"Clipboard: received client cut text (UTF-8) len=%d", len);

    NSData *data = [NSData dataWithBytes:str length:(NSUInteger)len];
    NSString *s = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (!s) {
        // Fallback try Latin-1 if UTF-8 decode fails
        s = [[NSString alloc] initWithData:data encoding:NSISOLatin1StringEncoding];
        if (!s)
            s = @"";
    }

    dispatch_async(dispatch_get_main_queue(), ^{
        gClipboardSuppressSend.fetch_add(1, std::memory_order_relaxed);

        TVLog(@"Clipboard: applying client text to UIPasteboard (UTF-8), suppression now=%d",
              gClipboardSuppressSend.load(std::memory_order_relaxed));
        [[ClipboardManager sharedManager] setStringFromRemote:s];

        gClipboardSuppressSend.fetch_sub(1, std::memory_order_relaxed);
    });
}

static void sendClipboardToClients(NSString *_Nullable text) {
    if (!gScreen) {
        TVLog(@"Clipboard: screen not initialized; skipping send");
        return;
    }

    if (!gClipboardEnabled) {
        TVLog(@"Clipboard: sync disabled; skipping send");
        return;
    }

    if (gClientCount <= 0) {
        TVLog(@"Clipboard: no connected clients; skipping send");
        return;
    }

    if (gClipboardSuppressSend.load(std::memory_order_relaxed) > 0) {
        TVLog(@"Clipboard: send suppressed (local set echo avoidance)");
        return; // suppressed (likely local set)
    }

    char *utf8 = NULL;
    int utf8Len = 0;
    char *latin1 = NULL;
    int latin1Len = 0;

    do {
        if (!text) {
            break;
        }

        // Prepare best-effort Latin-1 fallback
        NSData *latin1Data = [text dataUsingEncoding:NSISOLatin1StringEncoding allowLossyConversion:YES];
        latin1Len = (int)latin1Data.length;
        if (!latin1Len)
            break;

        latin1 = (char *)malloc((size_t)latin1Len);
        if (!latin1) {
            latin1Len = 0;
            break;
        }

        memcpy(latin1, [latin1Data bytes], (size_t)latin1Len);

    } while (0);

    do {
        if (!text) {
            break;
        }

        NSData *utf8Data = [text dataUsingEncoding:NSUTF8StringEncoding allowLossyConversion:NO];
        utf8Len = (int)utf8Data.length;
        if (!utf8Len)
            break;

        utf8 = (char *)malloc((size_t)utf8Len);
        if (!utf8) {
            utf8Len = 0;
            break;
        }

        memcpy(utf8, [utf8Data bytes], (size_t)utf8Len);

    } while (0);

    if (utf8 || latin1) {
        TVLog(@"Clipboard: sending to clients (utf8Len=%d, latin1Len=%d, clients=%d)", utf8Len, latin1Len,
              gClientCount);
    }

    if (utf8 && latin1) {
        rfbSendServerCutTextUTF8(gScreen, utf8, utf8Len, latin1, latin1Len);
    } else if (latin1) {
        rfbSendServerCutText(gScreen, latin1, latin1Len);
    } else {
        TVLog(@"Clipboard: no valid clipboard data to send");
    }

    if (utf8)
        free(utf8);

    if (latin1)
        free(latin1);
}

#pragma mark - Server-Side Cursor

NS_INLINE void setupXCursor(rfbScreenInfoPtr screen) {
    int width = 13, height = 11;

    const char cursor[] = "             "
                          " xx       xx "
                          "  xx     xx  "
                          "   xx   xx   "
                          "    xx xx    "
                          "     xxx     "
                          "    xx xx    "
                          "   xx   xx   "
                          "  xx     xx  "
                          " xx       xx "
                          "             ";
    const char mask[] = "xxxx     xxxx"
                        "xxxx     xxxx"
                        " xxxx   xxxx "
                        "  xxxx xxxx  "
                        "   xxxxxxx   "
                        "    xxxxx    "
                        "   xxxxxxx   "
                        "  xxxx xxxx  "
                        " xxxx   xxxx "
                        "xxxx     xxxx"
                        "xxxx     xxxx";

    rfbCursorPtr c = rfbMakeXCursor(width, height, (char *)cursor, (char *)mask);
    if (!c)
        return;

    c->xhot = width / 2;
    c->yhot = height / 2;
    rfbSetCursor(screen, c);
}

NS_INLINE void setupAlphaCursor(rfbScreenInfoPtr screen, int mode) {
    int i, j;
    rfbCursorPtr c = screen ? screen->cursor : NULL;
    if (!c)
        return;

    int maskStride = (c->width + 7) / 8;

    if (c->alphaSource) {
        free(c->alphaSource);
        c->alphaSource = NULL;
    }
    if (mode == 0)
        return;

    c->alphaSource = (unsigned char *)malloc((size_t)c->width * (size_t)c->height);
    if (!c->alphaSource)
        return;

    for (j = 0; j < c->height; j++) {
        for (i = 0; i < c->width; i++) {
            unsigned char value = (unsigned char)(0x100 * i / c->width);
            rfbBool masked = (c->mask[(i / 8) + maskStride * j] << (i & 7)) & 0x80;
            c->alphaSource[i + c->width * j] = (unsigned char)(masked ? (mode == 1 ? value : 0xff - value) : 0);
        }
    }

    if (c->cleanupMask)
        free(c->mask);

    c->mask = (unsigned char *)rfbMakeMaskFromAlphaSource(c->width, c->height, c->alphaSource);
    c->cleanupMask = TRUE;
}

#pragma mark - Setups (Native)

static void prepareClipboardManager(void) {
    // server->client sync; start/stop tied to client presence
    if (gClipboardEnabled) {
        [[ClipboardManager sharedManager] setOnChange:^(NSString *_Nullable text) {
            // If we’re in suppression (coming from client->server), do nothing
            if (gClipboardSuppressSend.load(std::memory_order_relaxed) > 0)
                return;
            sendClipboardToClients(text);
        }];
    } else {
        [[ClipboardManager sharedManager] setOnChange:nil];
    }
}

static void prepareScreenCapturer(void) {
    // Apply preferred frame rate (if provided)
    if (gFpsMin > 0 || gFpsPref > 0 || gFpsMax > 0) {
        TVLog(@"Applying preferred FPS to ScreenCapturer: min=%d pref=%d max=%d", gFpsMin, gFpsPref, gFpsMax);
        [[ScreenCapturer sharedCapturer] setPreferredFrameRateWithMin:gFpsMin preferred:gFpsPref max:gFpsMax];
    }

    gFrameHandler = ^(CMSampleBufferRef _Nonnull sampleBuffer) {
        handleFramebuffer(sampleBuffer);
    };
}

static void prepareBulletinManager(void) {
    BulletinManager *mgr = [BulletinManager sharedManager];
    [mgr revokeSingleNotification];
}

static void setupGeometry(void) {
    NSDictionary *props = [[ScreenCapturer sharedCapturer] renderProperties];
    gSrcWidth = [props[(__bridge NSString *)kIOSurfaceWidth] intValue];
    gSrcHeight = [props[(__bridge NSString *)kIOSurfaceHeight] intValue];
    if (gSrcWidth <= 0 || gSrcHeight <= 0) {
        TVPrintError("Failed to get screen dimensions");
        exit(EXIT_FAILURE);
    }

    // Apply output scaling if requested, then align (width multiple of 4)
    int tmpW = (gScale > 0.0 && gScale < 1.0) ? MAX(1, (int)floor((double)gSrcWidth * gScale)) : gSrcWidth;
    int tmpH = (gScale > 0.0 && gScale < 1.0) ? MAX(1, (int)floor((double)gSrcHeight * gScale)) : gSrcHeight;
    alignDimensions(tmpW, tmpH, &gWidth, &gHeight);
    gFBSize = (size_t)gWidth * (size_t)gHeight * (size_t)gBytesPerPixel;

    // Allocate double buffers (tightly packed BGRA/ARGB32)
    gFrontBuffer = calloc(1, gFBSize);
    gBackBuffer = calloc(1, gFBSize);
    if (!gFrontBuffer || !gBackBuffer) {
        TVPrintError("Failed to allocate required frame buffers");
        exit(EXIT_FAILURE);
    }
}

#if !TARGET_IPHONE_SIMULATOR
// Rotate an orientation by N quadrants (each quadrant = 90° CW)
NS_INLINE UIInterfaceOrientation rotateOrientation(UIInterfaceOrientation o, int quads) {
    // Map orientation to quadrant index: Portrait=0, LandLeft=1, UpsideDown=2, LandRight=3
    int q;
    switch (o) {
    case UIInterfaceOrientationPortrait:
    default:
        q = 0;
        break;
    case UIInterfaceOrientationLandscapeLeft:
        q = 1;
        break;
    case UIInterfaceOrientationPortraitUpsideDown:
        q = 2;
        break;
    case UIInterfaceOrientationLandscapeRight:
        q = 3;
        break;
    }
    q = (q + quads) & 3;
    static const UIInterfaceOrientation map[] = {
        UIInterfaceOrientationPortrait,
        UIInterfaceOrientationLandscapeLeft,
        UIInterfaceOrientationPortraitUpsideDown,
        UIInterfaceOrientationLandscapeRight,
    };
    return map[q];
}
#endif

// Map UIInterfaceOrientation to rotation quadrant (clockwise degrees/90)
NS_INLINE int rotationForOrientation(UIInterfaceOrientation o) {
#if !TARGET_IPHONE_SIMULATOR
    if (gOrientationFixQuad != 0) {
        o = rotateOrientation(o, gOrientationFixQuad);
    }
#endif
    switch (o) {
    case UIInterfaceOrientationPortrait:
    default:
        return 0; // 0°
    case UIInterfaceOrientationPortraitUpsideDown:
        return 2; // 180°
    case UIInterfaceOrientationLandscapeLeft:
        return 1; // 90° CW
    case UIInterfaceOrientationLandscapeRight:
        return 3; // 270° CW
    }
}

static void setupOrientationObserver(void) {
    if (!gOrientationSyncEnabled)
        return;

    static FBSOrientationObserver *sObserver;
    sObserver = [[FBSOrientationObserver alloc] init];
    if (!sObserver) {
        TVPrintError("Failed to create orientation observer instance");
        exit(EXIT_FAILURE);
    }

    // Set update handler
    void (^handler)(FBSOrientationUpdate *) = ^(FBSOrientationUpdate *update) {
        if (!update)
            return;

        UIInterfaceOrientation activeOrientation = [update orientation];

        // Note: Actual framebuffer rotation will be handled in the next step.
        gRotationQuad.store(rotationForOrientation(activeOrientation), std::memory_order_relaxed);

#if DEBUG
        NSUInteger seq = [update sequenceNumber];
        NSInteger direction = [update rotationDirection];
        NSTimeInterval dur = [update duration];
        TVLog(@"Orientation update: seq=%lu dir=%ld ori=%ld dur=%.3f", seq, direction, (long)activeOrientation, dur);
#endif
    };

    [sObserver setHandler:handler];

    // Prime current orientation if available
    UIInterfaceOrientation activeOrientation = [sObserver activeInterfaceOrientation];
    gRotationQuad.store(rotationForOrientation(activeOrientation), std::memory_order_relaxed);

    TVLog(@"Orientation observer registered (initial=%ld -> rotQ=%d)", (long)activeOrientation,
          gRotationQuad.load(std::memory_order_relaxed));
}

#pragma mark - Setups (RFB)

static void setupRfbScreen(int argc, const char *argv[]) {
    int argcCopy = argc; // rfbGetScreen may modify argc/argv
    char **argvCopy = (char **)argv;
    int bitsPerSample = 8;
    gScreen = rfbGetScreen(&argcCopy, argvCopy, gWidth, gHeight, bitsPerSample, 3, gBytesPerPixel);
    if (!gScreen) {
        TVPrintError("Failed to create rfbScreenInfo with rfbGetScreen");
        exit(EXIT_FAILURE);
    }

    // BGRA (little-endian) layout
    gScreen->paddedWidthInBytes = gWidth * gBytesPerPixel;
    gScreen->serverFormat.redShift = bitsPerSample * 2;   // 16
    gScreen->serverFormat.greenShift = bitsPerSample * 1; // 8
    gScreen->serverFormat.blueShift = 0;
    gScreen->frameBuffer = (char *)gFrontBuffer;

    // Desktop name
    gScreen->desktopName = strdup([gDesktopName UTF8String]);

    // Server ports
    gScreen->port = gPort;
    gScreen->ipv6port = gPort;

    // Server bind addresses
    in_addr_t v4Addr = INADDR_ANY;
    struct in6_addr v6Addr;
    memset(&v6Addr, 0, sizeof(v6Addr));

    TVBindHostKind hostKind = tvClassifyBindHost(gBindHost, &v4Addr, &v6Addr);
    if (hostKind == kTVBindHostKindIPv4) {
        gScreen->listenInterface = v4Addr;
    } else if (hostKind == kTVBindHostKindIPv6) {
        char ifaceBuf[INET6_ADDRSTRLEN];
        const char *iface = inet_ntop(AF_INET6, &v6Addr, ifaceBuf, sizeof(ifaceBuf));
        if (!iface) {
            TVPrintError("Failed to normalize IPv6 bind host");
            exit(EXIT_FAILURE);
        }
        gScreen->listen6Interface = strdup(iface);
    } else if (hostKind == kTVBindHostKindInvalid && gBindHost) {
        TVPrintError("Invalid host address: %s", [gBindHost UTF8String]);
        exit(EXIT_FAILURE);
    } else {
        // Do nothing; default ANY
    }

    // Event handlers
    gScreen->newClientHook = newClientHook;
    gScreen->displayHook = displayHook;
    gScreen->displayFinishedHook = displayFinishedHook;
    gScreen->setDesktopSizeHook = setDesktopSizeHook;
}

static void setupRfbEventHandlers(void) {
    gScreen->ptrAddEvent = ptrAddEvent;
    gScreen->kbdAddEvent = kbdAddEvent;
    gScreen->kbdReleaseAllKeys = kbdReleaseAllKeys;
}

static rfbBool tvCheckPasswordByList(rfbClientPtr cl, const char *passwd, int len) {
    // Check if client host is blocked
    if (gBlockedHosts && cl && cl->host) {
        NSString *host = [NSString stringWithUTF8String:cl->host];
        BOOL isBlocked = NO;
        @synchronized(gBlockedHosts) {
            isBlocked = [gBlockedHosts containsObject:host];
        }
        if (isBlocked) {
            TVLog(@"Rejected connection from blocked host: %@", host);
            return FALSE; // Reject authentication
        }
    }

    rfbBool rc = rfbCheckPasswordByList(cl, passwd, len);

    TVClientState *st = tvGetClientState(cl);
    NSString *updateKey = nil;
    if (st && st->clientId8[0] != '\0') {
        updateKey = [NSString stringWithUTF8String:st->clientId8];
    }

    if (!updateKey)
        updateKey = tvGenerateClientId8(cl->sock);
    if (updateKey && gClientStates) {
        @synchronized(gClientStates) {
            NSMutableDictionary *entry = [gClientStates[updateKey] mutableCopy];
            if (entry) {
                entry[@"viewOnly"] = @(cl->viewOnly ? YES : NO);
                gClientStates[updateKey] = [entry copy];
            }
        }
    }

    // Notify subscribers about property change (debounced)
    tvCtlScheduleBroadcastChanged();

    return rc;
}

static void setupRfbClassicAuthentication(void) {
    // Enable classic VNC authentication if environment variables are provided
    const char *envPwd = getenv("TROLLVNC_PASSWORD");
    const char *envViewPwd = getenv("TROLLVNC_VIEWONLY_PASSWORD");

    int fullCount = (envPwd && *envPwd) ? 1 : 0;
    int viewCount = (envViewPwd && *envViewPwd) ? 1 : 0;
    if (fullCount + viewCount > 0) {
        // Vector size = number of passwords + 1 for NULL terminator
        int vecCount = fullCount + viewCount + 1;
        gAuthPasswdVec = (char **)calloc((size_t)vecCount, sizeof(char *));

        int idx = 0;
        if (fullCount) {
            gAuthPasswdStr = strdup(envPwd);
            gAuthPasswdVec[idx++] = gAuthPasswdStr;
        }

        if (viewCount) {
            gAuthViewOnlyPasswdStr = strdup(envViewPwd);
            gAuthPasswdVec[idx++] = gAuthViewOnlyPasswdStr;
        }

        gAuthPasswdVec[idx] = NULL; // NULL-terminated array
        gScreen->authPasswdData = (void *)gAuthPasswdVec;

        // Index of first view-only password = number of full-access passwords
        // From that index onward (1-based in description, 0-based in array) are view-only.
        gScreen->authPasswdFirstViewOnly = fullCount;
        gScreen->passwordCheck = tvCheckPasswordByList;

        TVLog(@"Classic VNC authentication enabled via env: full=%d, view-only=%d", fullCount, viewCount);
    }
}

static void setupRfbCutTextHandlers(void) {
    // client->server sync
    if (gClipboardEnabled) {
        gScreen->setXCutText = setXCutTextLatin1;
        gScreen->setXCutTextUTF8 = setXCutTextUTF8;
        TVLog(@"Clipboard: client->server handlers registered (enabled)");
    } else {
        TVLog(@"Clipboard: client->server handlers not registered (disabled)");
    }
}

static void setupRfbServerSideCursor(void) {
    if (gCursorEnabled) {
        setupXCursor(gScreen);
        setupAlphaCursor(gScreen, 0);
        TVLog(@"Cursor: XCursor + alpha mode=2 enabled");
    } else {
        TVLog(@"Cursor: disabled (default; enable with -U on)");
    }
}

static void setupRfbHttpServer(void) {
    // Built-in HTTP server settings (see rfb.h http* fields)
    gScreen->httpEnableProxyConnect = TRUE; // always allow CONNECT if HTTP is enabled
    if (gHttpPort > 0) {
        gScreen->httpPort = gHttpPort; // enable HTTP on specified port
        gScreen->http6Port = gHttpPort;
        if (gHttpDirOverride) {
            // Use override absolute path
            gScreen->httpDir = strdup(gHttpDirOverride);
            TVLog(@"HTTP server config: port=%d, dir=%s (override), proxyConnect=YES", gHttpPort, gHttpDirOverride);
        } else {
            // Compute httpDir relative to executable: ../share/trollvnc/webclients
            do {
                NSString *exe = tvExecutablePath();
                NSString *exeDir = [exe stringByDeletingLastPathComponent];
                NSString *webRel;
#ifdef THEBOOTSTRAP
                webRel = @"./webclients";
#else
                webRel = @"../share/trollvnc/webclients";
#endif
                NSString *webPath = [[exeDir stringByAppendingPathComponent:webRel] stringByStandardizingPath];
                const char *fs = [webPath fileSystemRepresentation];
                if (fs && *fs) {
                    gScreen->httpDir = strdup(fs);
                    TVLog(@"HTTP server config: port=%d, dir=%@, proxyConnect=YES", gHttpPort, webPath);
                }
            } while (0);
        }
    } else {
        gScreen->httpPort = 0;   // disabled
        gScreen->httpDir = NULL; // do not set dir to avoid default startup
    }

    // SSL certificate and key (optional)
    if (gSslCertPath && *gSslCertPath) {
        gScreen->sslcertfile = strdup(gSslCertPath);
    }
    if (gSslKeyPath && *gSslKeyPath) {
        gScreen->sslkeyfile = strdup(gSslKeyPath);
    }
}

static BOOL gFileTransferRegistered = NO;

static void setupRfbFileTransferExtension(void) {
    if (!gFileTransferEnabled) {
        return;
    }

    TVLog(@"TightVNC 1.x file transfer extension registered");
    rfbRegisterTightVNCFileTransferExtension();

    gFileTransferRegistered = YES;
}

#pragma mark - Setups (Event Model)

static const long cSelectTimeout = 1e4; // 10 ms

// Background event thread for reverse-connection mode
static pthread_t gRfbEventThread = 0;
static std::atomic<int> gRfbEventThreadRunning(0);

static void *tvRfbEventThreadMain(void *arg) {
    (void)arg;
    for (;;) {
        if (!gRfbEventThreadRunning.load(std::memory_order_relaxed))
            break;
        if (!gScreen)
            break;
        rfbProcessEvents(gScreen, cSelectTimeout);
        if (!rfbIsActive(gScreen))
            break;
    }
    CFRunLoopStop(CFRunLoopGetMain());
    gRfbEventThreadRunning.store(0, std::memory_order_relaxed);
    return NULL;
}

static void tvStartRfbEventThread(void) {
    if (gRfbEventThreadRunning.exchange(1, std::memory_order_acq_rel))
        return;
    int rc = pthread_create(&gRfbEventThread, NULL, tvRfbEventThreadMain, NULL);
    if (rc != 0) {
        gRfbEventThreadRunning.store(0, std::memory_order_relaxed);
        TVPrintError("Failed to create VNC event thread (rc=%d)", rc);
        exit(EXIT_FAILURE);
    }
}

static void tvStopRfbEventThread(void) {
    if (!gRfbEventThreadRunning.exchange(0, std::memory_order_acq_rel))
        return;
    if (gRfbEventThread) {
        if (!pthread_equal(gRfbEventThread, pthread_self()))
            pthread_join(gRfbEventThread, NULL);
        gRfbEventThread = 0;
    }
}

static void initializeAndRunRfbServer(void) {
    // Kiểm license TRƯỚC khi mở server. Không hợp lệ thì newClientHook từ chối mọi
    // client VNC và control socket chỉ trả "ERR NotActivated".
    tvLicenseLoad();
    TVLog(@"License: %@ (máy %@)", gLicenseValid ? @"đã kích hoạt" : @"CHƯA kích hoạt",
          tvDeviceUDID() ?: @"?");
    rfbInitServer(gScreen);
    TVLog(@"VNC server initialized on port %d, %dx%d, name '%@'", gPort, gWidth, gHeight, gDesktopName);

    if (isRepeaterEnabled()) {
        static CFTimeInterval sRetryInterval = 0.0;
        const char *envRetryInterval = getenv("TROLLVNC_REPEATER_RETRY_INTERVAL");
        if (envRetryInterval) {
            sRetryInterval = atof(envRetryInterval);
        }

        static rfbClientPtr sClient = NULL;
        if (gRepeaterMode == 2) {
            TVLog(@"VNC server running in repeater mode");
            static NSString *sRepeaterId = [NSString stringWithFormat:@"%d", gRepeaterId];
            const char *repeaterId = [sRepeaterId UTF8String];
            sClient = rfbUltraVNCRepeaterMode2Connection(gScreen, gRepeaterHost, gRepeaterPort, repeaterId);
        } else {
            TVLog(@"VNC server running in viewer mode");
            sClient = rfbReverseConnection(gScreen, gRepeaterHost, gRepeaterPort);
        }

        if (!sClient) {
            TVPrintError("Failed to establish reverse connection to %s", gRepeaterHost);
            if (sRetryInterval > 0)
                CFRunLoopRunInMode(kCFRunLoopDefaultMode, sRetryInterval, false);
            exit(EXIT_FAILURE);
        }

        TVClientState *st = tvGetClientState(sClient);
        if (st) {
            st->isRepeaterClient = YES;
        }

        TVLog(@"Reverse connection established to %s", gRepeaterHost);

        // Start background event thread to pump events while in reverse mode
        tvStartRfbEventThread();
    } else {
        // Run VNC in background thread
        rfbRunEventLoop(gScreen, cSelectTimeout, TRUE);
    }

    // Start Bonjour advertisement after server is ready
    startBonjour();
}

static void handleSignal(int signum) {
    (void)signum;
    TVLog(@"Signal %d received", signum);

    // Best-effort: stop runloop to unwind main and allow cleanup.
    CFRunLoopStop(CFRunLoopGetMain());
}

static void installSignalHandlers(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = handleSignal;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
}

static void installTerminationHandlers(void) {
    atexit_b(^(void) {
#if !TARGET_OS_SIMULATOR
        if (gRestoreAssist) {
            gRestoreAssist = NO;
            [PSAssistiveTouchSettingsDetail setEnabled:NO];
        }
#endif
    });
}

#pragma mark - Logging

BOOL tvncLoggingEnabled = YES;
BOOL tvncVerboseLoggingEnabled = NO;

#define LOCK(mutex) pthread_mutex_lock(&(mutex))
#define UNLOCK(mutex) pthread_mutex_unlock(&(mutex))

static MUTEX(logMutex);
static int logMutex_initialized = 0;

static void rfbCustomLog(const char *format, ...) {
    va_list args;
    char buf[256];
    time_t log_clock;

    if (!tvncLoggingEnabled)
        return;

    if (!logMutex_initialized) {
        INIT_MUTEX(logMutex);
        logMutex_initialized = 1;
    }

    LOCK(logMutex);
    va_start(args, format);

    time(&log_clock);
    strftime(buf, 255, "%Y-%m-%d %X ", localtime(&log_clock));
    fprintf(stderr, "%s", buf);

    /* If format ends with a \n, replace with \r\n */
    const char *fmt_to_use = format;
    char *fmt_copy = NULL;
    if (format) {
        size_t flen = strlen(format);
        if (flen > 0 && format[flen - 1] == '\n') {
            fmt_copy = (char *)malloc(flen + 2);
            if (fmt_copy) {
                memcpy(fmt_copy, format, flen - 1);
                fmt_copy[flen - 1] = '\r';
                fmt_copy[flen] = '\n';
                fmt_copy[flen + 1] = '\0';
                fmt_to_use = fmt_copy;
            }
        }
    }

    vfprintf(stderr, fmt_to_use, args);
    fflush(stderr);

    if (fmt_copy)
        free(fmt_copy);

    va_end(args);
    UNLOCK(logMutex);
}

static void setupRfbLogging(void) { rfbLog = rfbErr = rfbCustomLog; }

#pragma mark - Main Procedure

#define REQUIRED_UID 501
#define REQUIRED_GID 501

static void dropPrivileges(void) {
    if (isatty(STDIN_FILENO)) {
        return;
    }

    int rc;
    if (getuid() != REQUIRED_UID) {
        rc = setuid(REQUIRED_UID);
        if (rc != 0) {
            TVPrintError("Failed to set uid to %d: %d", REQUIRED_UID, rc);
            exit(EXIT_FAILURE);
        }
    }

    if (getgid() != REQUIRED_GID) {
        rc = setgid(REQUIRED_GID);
        if (rc != 0) {
            TVPrintError("Failed to set gid to %d: %d", REQUIRED_GID, rc);
            // exit(EXIT_FAILURE);
        }
    }
}

static void cleanupAndExit(int code) {
    // Stop auto discovery
    stopBonjour();

    // Clear all user notifications
    [[BulletinManager sharedManager] revokeAllNotifications];

    // Stop control socket if any
    tvStopControlSocket();

    // Stop event thread if running
    tvStopRfbEventThread();

    if (gFileTransferRegistered) {
        rfbUnregisterTightVNCFileTransferExtension();
    }

    if (gScreen) {
        rfbShutdownServer(gScreen, YES);
        rfbScreenCleanup(gScreen);
        gScreen = NULL;
    }

    // There’s no need to free other resources because we’re going to exit the process. Yay!
    exit(code);
}

#ifdef THEBOOTSTRAP
#define SINGLETON_PARENT_NAME "trollvncmanager"
#define SINGLETON_MARKER_PATH "/var/mobile/Library/Caches/com.82flex.trollvnc.server.pid"

static void monitorParentProcess(void) {
    if (isatty(STDIN_FILENO)) {
        return;
    }

    static pid_t ppid = getppid();
    if (ppid == 1) {
        return;
    }

    static dispatch_source_t source =
        dispatch_source_create(DISPATCH_SOURCE_TYPE_PROC, ppid, DISPATCH_PROC_EXIT | DISPATCH_PROC_SIGNAL,
                               dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0));

    dispatch_source_set_event_handler(source, ^{
        if (dispatch_source_get_data(source) & DISPATCH_PROC_EXIT) {
            dispatch_source_cancel(source);
            TVPrintError("Parent process %d exited", ppid);
            exit(EXIT_SUCCESS);
        } else if (kill(ppid, 0) == -1 && errno == ESRCH) {
            dispatch_source_cancel(source);
            TVPrintError("Parent process %d is gone", ppid);
            exit(EXIT_SUCCESS);
        }
    });

    dispatch_resume(source);
}

static void monitorSelfAndRestartIfVnodeDeleted(const char *executable) {
    int myHandle = open(executable, O_EVTONLY);
    if (myHandle <= 0) {
        return;
    }

    static unsigned long monitorMask = DISPATCH_VNODE_DELETE;
    static dispatch_source_t monitorSource;
    monitorSource =
        dispatch_source_create(DISPATCH_SOURCE_TYPE_VNODE, myHandle, monitorMask, dispatch_get_main_queue());

    dispatch_source_set_event_handler(monitorSource, ^{
        unsigned long flags = dispatch_source_get_data(monitorSource);
        if (flags & DISPATCH_VNODE_DELETE) {
            dispatch_source_cancel(monitorSource);
            exit(EXIT_SUCCESS);
        }
    });

    dispatch_resume(monitorSource);
}

static void ensureSingleton(const char *argv[]) {
    if (isatty(STDIN_FILENO)) {
        return;
    }

    if (!argv || !argv[0] || argv[0][0] != '/') {
        return;
    }

    monitorSelfAndRestartIfVnodeDeleted(argv[0]);

    NSString *markerPath = @SINGLETON_MARKER_PATH;
    const char *cMarkerPath = [markerPath fileSystemRepresentation];

    // Open file for read/write, create if doesn't exist
    static int lockFD = open(cMarkerPath, O_RDWR | O_CREAT, 0644);
    if (lockFD == -1) {
        TVPrintError("Failed to open lock file: %s", strerror(errno));
        exit(EXIT_FAILURE);
    }

    // Try to acquire an exclusive lock
    struct flock fl;
    fl.l_type = F_WRLCK;
    fl.l_whence = SEEK_SET;
    fl.l_start = 0;
    fl.l_len = 0; // Lock entire file

    if (fcntl(lockFD, F_SETLK, &fl) == -1) {
        // Lock already held by another process
        TVPrintError("Another instance is already running");
        close(lockFD);
        exit(EXIT_FAILURE);
    }

    // Truncate the file to clear any previous content
    if (ftruncate(lockFD, 0) == -1) {
        TVPrintError("Failed to truncate lock file: %s", strerror(errno));
        // Continue anyway
    }

    // Write PID to file
    pid_t pid = getpid();
    char pidStr[16];
    int len = snprintf(pidStr, sizeof(pidStr), "%d\n", pid);
    if (write(lockFD, pidStr, len) != len) {
        TVPrintError("Failed to write PID to lock file: %s", strerror(errno));
        // Continue anyway
    }

    // Keep the file descriptor open to maintain the lock
    // It will be automatically closed when the process exits
    fchown(lockFD, 501, 501);
}
#endif

int main(int argc, const char *argv[]) {

    /* Drop privileges: this program should run as mobile */
    dropPrivileges();

    @autoreleasepool {
        parseCLI(argc, argv);

#ifdef THEBOOTSTRAP
        monitorParentProcess();
        ensureSingleton(argv);
#endif
    }

    /* Do nothing but keep the runloop alive */
    if (!gEnabled) {
        CFRunLoopRun();
        return EXIT_SUCCESS;
    }

    @autoreleasepool {
        setupGeometry();
        setupOrientationObserver();

        setupRfbLogging();
        tvPreventAutomaticLock();
        setupRfbScreen(argc, argv);
        setupRfbEventHandlers();
        setupRfbClassicAuthentication();
        setupRfbCutTextHandlers();
        setupRfbServerSideCursor();
        setupRfbHttpServer();
        setupRfbFileTransferExtension();

        prepareBulletinManager();
        prepareClipboardManager();
        prepareScreenCapturer();

        initializeTilingOrReset();
        initializeAndRunRfbServer();

        installSignalHandlers();
        installTerminationHandlers();

        tvStartControlSocketIfNeeded();
        // Keep touch-lock state across a daemon restart. notifyd resets it on a
        // real device reboot, so reboot still starts safely with touch lock off.
        tvInstallTouchLockHIDFilter();
        tvInstallLockTouchResetObservers();
        tvInstallBKSFrontmostMonitor();
        tvEnsureKeeperAtStartup();
        tvStartWiFiIPWatchdog();
    }

    CFRunLoopRun();
    cleanupAndExit(EXIT_SUCCESS);

    return EXIT_SUCCESS;
}
