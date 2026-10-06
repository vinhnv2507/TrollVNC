// Detached local updater: its executable lives outside the app being replaced.
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>
#import <dlfcn.h>
#import <fcntl.h>
#import <errno.h>
#import <spawn.h>
#include <vector>
#include <cstring>
#include <cstdlib>
#include <signal.h>
#import <sys/file.h>
#import <sys/stat.h>
#import <sys/wait.h>
#import <unistd.h>
#import "TVNCLocalUpdate.h"

@interface TVUpdateDownloadDelegate : NSObject <NSURLSessionTaskDelegate>
@end
@implementation TVUpdateDownloadDelegate
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
 willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
 completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil);
}
@end

static NSURL *statusURL;
static NSString *jobDirectory;
static NSString *jobID;
static NSString *targetVersion;

static void report(NSString *state, NSString *message) {
    NSDictionary *body = @{@"job": jobID, @"state": state, @"message": message,
                           @"version": targetVersion};
    NSData *data = [NSJSONSerialization dataWithJSONObject:body options:0 error:nil];
    if (jobDirectory) [data writeToFile:[jobDirectory stringByAppendingPathComponent:@"status.json"] atomically:YES];
    if (jobDirectory && [state isEqualToString:@"error"]) {
        unlink([jobDirectory stringByAppendingPathComponent:@"ControlIOS.tipa"].fileSystemRepresentation);
        unlink([jobDirectory stringByAppendingPathComponent:@"updater"].fileSystemRepresentation);
    }
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:statusURL];
    request.HTTPMethod = @"POST";
    request.HTTPBody = data;
    request.timeoutInterval = 4;
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    NSURLSessionDataTask *task = [[NSURLSession sharedSession] dataTaskWithRequest:request
        completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
            dispatch_semaphore_signal(done);
        }];
    [task resume];
    if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)))
        [task cancel];
}

static int spawn(NSString *executable, NSArray<NSString *> *arguments, BOOL wait) {
    std::vector<char *> argv;
    argv.push_back(strdup(executable.fileSystemRepresentation));
    for (NSString *arg in arguments) argv.push_back(strdup(arg.UTF8String));
    argv.push_back(NULL);
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, "/dev/null", O_WRONLY, 0);
    posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0);
    posix_spawnattr_t attributes;
    posix_spawnattr_init(&attributes);
    posix_spawnattr_setflags(&attributes, POSIX_SPAWN_CLOEXEC_DEFAULT);
    pid_t pid = 0;
    extern char **environ;
    int error = posix_spawn(&pid, executable.fileSystemRepresentation, &actions,
                           &attributes, argv.data(), environ);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attributes);
    for (char *arg : argv) free(arg);
    if (error) return -error;
    if (!wait) return 0;
    int result = 0;
    for (int i = 0; i < 180; ++i) {
        pid_t exited = waitpid(pid, &result, WNOHANG);
        if (exited == pid) return WIFEXITED(result) ? WEXITSTATUS(result) : -1;
        if (exited < 0 && errno != EINTR) return -errno;
        sleep(1);
    }
    kill(pid, SIGTERM);
    sleep(2);
    if (waitpid(pid, &result, WNOHANG) == 0) kill(pid, SIGKILL);
    waitpid(pid, &result, 0);
    return -ETIMEDOUT;
}

static NSString *fileSHA(NSString *path) {
    NSInputStream *stream = [NSInputStream inputStreamWithFileAtPath:path];
    [stream open];
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    uint8_t buffer[65536];
    NSInteger count;
    while ((count = [stream read:buffer maxLength:sizeof(buffer)]) > 0)
        CC_SHA256_Update(&context, buffer, (CC_LONG)count);
    [stream close];
    if (count < 0) return nil;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    NSMutableString *result = [NSMutableString string];
    for (unsigned char c : digest) [result appendFormat:@"%02x", c];
    return result;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--check") == 0)
            return getuid() != 0 ? 10 : geteuid() != 0 ? 11 : getgid() != 0 ? 12 : getegid() != 0 ? 13 : 0;
        if (argc != 7 || getuid() != 0) return 2;
        // job, version, SHA256, package URL, TrollStore helper, existing app path
        jobID = @(argv[1]); targetVersion = @(argv[2]);
        NSString *sha = @(argv[3]);
        NSURL *url = [NSURL URLWithString:@(argv[4])];
        NSString *tsHelper = @(argv[5]);
        NSString *appPath = @(argv[6]);
        if (!TVUpdateHex(jobID.UTF8String, 32) || !TVUpdateHex(sha.UTF8String, 64) ||
            !TVUpdateVersion(targetVersion.UTF8String) ||
            ![url.scheme isEqualToString:@"http"] || !TVUpdateLANHost(url.host.UTF8String) ||
            url.user || url.password || url.query || url.fragment ||
            ![url.path isEqualToString:[NSString stringWithFormat:@"/%@/ControlIOS.tipa", jobID]]) return 2;
        jobDirectory = [NSString stringWithFormat:@"/var/tmp/controlios-update-%@.app", jobID];
        statusURL = [[url URLByDeletingLastPathComponent] URLByAppendingPathComponent:@"status"];
        NSString *detached = [jobDirectory stringByAppendingPathComponent:@"updater"];
        if (![@(argv[0]) isEqualToString:detached]) {
            // Stage as root, before the installer can delete the original bundle.
            // Never reuse a pre-existing path, including a symlink or mobile-owned directory.
            if (mkdir(jobDirectory.fileSystemRepresentation, 0700) != 0) {
                jobDirectory = nil; // Do not touch a previous job's files.
                report(@"error", @"Không tạo được thư mục cập nhật riêng; chưa cài gói"); return 8;
            }
            NSDictionary *info = @{@"CFBundleIdentifier": @"com.controlios.localupdater",
                @"CFBundleExecutable": @"updater", @"CFBundlePackageType": @"APPL",
                @"NSAppTransportSecurity": @{@"NSAllowsArbitraryLoads": @YES}};
            struct stat staged = {};
            if (![info writeToFile:[jobDirectory stringByAppendingPathComponent:@"Info.plist"] atomically:YES] ||
                ![[NSFileManager defaultManager] copyItemAtPath:@(argv[0]) toPath:detached error:nil] ||
                chown(detached.fileSystemRepresentation, 0, 0) != 0 ||
                chmod(detached.fileSystemRepresentation, 0700) != 0 ||
                lstat(detached.fileSystemRepresentation, &staged) != 0 ||
                !S_ISREG(staged.st_mode) || staged.st_uid != 0) {
                report(@"error", @"Không chuẩn bị được bộ cập nhật quyền root; chưa cài gói"); return 8;
            }
            NSMutableArray<NSString *> *arguments = [NSMutableArray array];
            for (int i = 1; i < argc; ++i) [arguments addObject:@(argv[i])];
            int result = spawn(detached, arguments, NO);
            if (result) report(@"error", [NSString stringWithFormat:@"Không khởi chạy được bộ cập nhật: %d", result]);
            return result ? 8 : 0;
        }
        int lockFD = open("/var/tmp/controlios-update.lock", O_CREAT | O_RDWR | O_NOFOLLOW, 0600);
        if (lockFD < 0 || flock(lockFD, LOCK_EX | LOCK_NB) != 0) {
            report(@"error", @"Đang có một lượt cập nhật khác trên máy"); return 3;
        }
        fcntl(lockFD, F_SETFD, FD_CLOEXEC);
        NSString *packagePath = [jobDirectory stringByAppendingPathComponent:@"ControlIOS.tipa"];
        report(@"downloading", @"Đang tải gói từ PC qua LAN");
        NSURLSessionConfiguration *configuration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
        configuration.timeoutIntervalForRequest = 20;
        configuration.timeoutIntervalForResource = 120;
        TVUpdateDownloadDelegate *delegate = [TVUpdateDownloadDelegate new];
        NSURLSession *session = [NSURLSession sessionWithConfiguration:configuration delegate:delegate delegateQueue:nil];
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        __block NSString *downloadError = nil;
        NSURLSessionDownloadTask *task = [session downloadTaskWithURL:url
            completionHandler:^(NSURL *file, NSURLResponse *response, NSError *error) {
                if (error || [(NSHTTPURLResponse *)response statusCode] != 200 || !file) {
                    downloadError = @"Không tải được gói; kiểm tra LAN và tường lửa PC";
                } else {
                    NSNumber *size = [[[NSFileManager defaultManager] attributesOfItemAtPath:file.path error:nil]
                                      objectForKey:NSFileSize];
                    if (!size || size.unsignedLongLongValue > 128ULL * 1024 * 1024 ||
                        ![[NSFileManager defaultManager] moveItemAtURL:file
                            toURL:[NSURL fileURLWithPath:packagePath] error:nil])
                        downloadError = @"Gói quá lớn hoặc không ghi được file";
                }
                dispatch_semaphore_signal(done);
            }];
        [task resume];
        if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 130 * NSEC_PER_SEC))) {
            [task cancel]; [session invalidateAndCancel];
            report(@"error", @"Hết thời gian tải gói từ PC"); return 4;
        }
        [session finishTasksAndInvalidate];
        if (downloadError || ![[fileSHA(packagePath) lowercaseString] isEqualToString:sha]) {
            report(@"error", downloadError ?: @"SHA256 không khớp; đã dừng trước khi cài"); return 5;
        }
        report(@"installing", @"Đang cài đè ControlIOS qua TrollStore");
        // Only replace an existing TrollStore app. Never force installation.
        int result = spawn(tsHelper, @[@"install", @"custom", packagePath], YES);
        if (result != 0 && result != 182 && result != 184) {
            report(@"error", [NSString stringWithFormat:@"TrollStore trả mã lỗi %d", result]); return 6;
        }
        NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:
                               [appPath stringByAppendingPathComponent:@"Info.plist"]];
        if (![info[@"CFBundleIdentifier"] isEqualToString:@"com.controlios.app"] ||
            ![info[@"CFBundleShortVersionString"] isEqualToString:targetVersion]) {
            report(@"error", @"Phiên bản gói đã cài không khớp"); return 7;
        }
        report(@"restarting", @"Đã cài gói, đang bật lại dịch vụ và chờ PC kiểm tra");
        NSString *manager = [appPath stringByAppendingPathComponent:@"trollvncmanager"];
        // The old manager exits when its vnode is deleted. Retry starting the new
        // one while that exit is in progress; singleton locking prevents duplicates.
        for (int i = 0; i < 4; ++i) {
            sleep(2);
            spawn(manager, @[], NO);
        }
        void *sbs = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/SpringBoardServices", RTLD_LAZY);
        if (sbs) {
            int (*launch)(CFStringRef, Boolean) = (int (*)(CFStringRef, Boolean))dlsym(sbs, "SBSLaunchApplicationWithIdentifier");
            if (launch) launch(CFSTR("com.controlios.app"), true);
            dlclose(sbs);
        }
        report(@"installed", result == 182 ? @"Đã cài; iOS yêu cầu bật Developer Mode" : @"Đã cài; chờ xác nhận phiên bản đang chạy");
        unlink(packagePath.fileSystemRepresentation);
        // Preserve status.json for diagnosis, remove the detached executable.
        unlink(argv[0]);
        close(lockFD);
        return 0;
    }
}
