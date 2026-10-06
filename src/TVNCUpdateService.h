#pragma once
#import "TVNCUpdateIPC.h"
#import "TVNCLocalUpdate.h"
#include <spawn.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include "libproc.h"

static inline NSArray<NSString *> *TVUpdateManagerArguments(NSString *request, NSString *helper,
                                                            NSString *app, NSString *current) {
    if (![request isKindOfClass:NSString.class] || ![helper isKindOfClass:NSString.class] ||
        ![helper hasPrefix:@"/"] || ![helper.lastPathComponent isEqualToString:@"trollstorehelper"]) return nil;
    if ([request isEqualToString:@"check"]) return @[@"--check"];
    NSMutableArray<NSString *> *parts = [NSMutableArray array];
    for (NSString *p in [request componentsSeparatedByCharactersInSet:NSCharacterSet.whitespaceCharacterSet])
        if (p.length) [parts addObject:p];
    if (parts.count != 4 || !TVUpdateHex(parts[0].UTF8String, 32) ||
        !TVUpdateVersion(parts[1].UTF8String) || !TVUpdateHex(parts[2].UTF8String, 64) ||
        !TVUpdateVersion(current.UTF8String) ||
        [current compare:parts[1] options:NSNumericSearch] != NSOrderedAscending) return nil;
    NSURL *url = [NSURL URLWithString:parts[3]];
    if (![url.scheme isEqualToString:@"http"] || !TVUpdateLANHost(url.host.UTF8String) ||
        url.user || url.password || url.query || url.fragment ||
        ![url.path isEqualToString:[NSString stringWithFormat:@"/%@/ControlIOS.tipa", parts[0]]]) return nil;
    [parts addObjectsFromArray:@[helper, app]];
    return parts;
}

static inline NSString *TVUpdateManagerLaunch(NSDictionary *message, NSString *app) {
    if (getuid() != 0 || geteuid() != 0)
        return [NSString stringWithFormat:@"ERR LocalUpdateUnavailable manager uid=%u euid=%u\n", getuid(), geteuid()];
    NSDictionary *info = [NSDictionary dictionaryWithContentsOfFile:[app stringByAppendingPathComponent:@"Info.plist"]];
    NSArray<NSString *> *arguments = TVUpdateManagerArguments(message[@"request"], message[@"helper"],
                                                             app, info[@"CFBundleShortVersionString"] ?: @"");
    NSString *updater = [app stringByAppendingPathComponent:@"controliosupdater"];
    if (!arguments || access(updater.fileSystemRepresentation, X_OK) != 0 ||
        access([message[@"helper"] fileSystemRepresentation], X_OK) != 0)
        return @"ERR LocalUpdateUnavailable invalid update request/helper\n";
    // The manager has already verified its real/effective root credentials.
    // No installation or subprocess is started by the preflight request.
    if ([message[@"request"] isEqualToString:@"check"]) return @"OK LAN_UPDATE_1\n";
    const char *argv[8] = {updater.fileSystemRepresentation};
    for (NSUInteger i = 0; i < arguments.count; ++i) argv[i + 1] = arguments[i].UTF8String;
    posix_spawnattr_t attributes;
    posix_spawnattr_init(&attributes);
    posix_spawnattr_setflags(&attributes, POSIX_SPAWN_CLOEXEC_DEFAULT);
    pid_t pid = 0;
    extern char **environ;
    int error = posix_spawn(&pid, updater.fileSystemRepresentation, NULL, &attributes, (char *const *)argv, environ);
    posix_spawnattr_destroy(&attributes);
    if (error) return [NSString stringWithFormat:@"ERR LocalUpdateUnavailable manager spawn %d\n", error];
    // The existing manager SIGCHLD handler reaps this short staging parent.
    return [NSString stringWithFormat:@"OK %@\n", arguments[0]];
}

static inline void TVStartUpdateManager(NSString *app) {
    if (getuid() != 0) return;
    if (mkdir(TVUPDATE_DIRECTORY, 0755) != 0 && errno != EEXIST) return;
    struct stat info = {};
    if (lstat(TVUPDATE_DIRECTORY, &info) != 0 || !S_ISDIR(info.st_mode) || info.st_uid != 0) return;
    if (chmod(TVUPDATE_DIRECTORY, 0755) != 0) return;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return;
    struct sockaddr_un address = {};
    address.sun_len = sizeof(address);
    address.sun_family = AF_UNIX;
    strlcpy(address.sun_path, TVUPDATE_SOCKET, sizeof(address.sun_path));
    unlink(TVUPDATE_SOCKET); // The existing manager singleton already holds its lock.
    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        chown(TVUPDATE_SOCKET, 0, 0) != 0 || chmod(TVUPDATE_SOCKET, 0666) != 0 || listen(fd, 4) != 0) {
        close(fd); return;
    }
    fcntl(fd, F_SETFD, FD_CLOEXEC);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        for (;;) {
            int client = accept(fd, NULL, NULL);
            if (client < 0) { if (errno == EINTR) continue; break; }
            dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
                uid_t uid; gid_t gid; pid_t pid = 0;
                socklen_t size = sizeof(pid);
                char executable[PROC_PIDPATHINFO_MAXSIZE] = {};
                NSString *expected = [[app stringByAppendingPathComponent:@"trollvncserver"] stringByResolvingSymlinksInPath];
                // Access is checked by peer uid AND exact executable path; the
                // mobile screen process can retain wheel as its primary group.
                BOOL trusted = getpeereid(client, &uid, &gid) == 0 && (uid == 0 || uid == 501) &&
                    getsockopt(client, SOL_LOCAL, LOCAL_PEERPID, &pid, &size) == 0 && pid > 0 &&
                    proc_pidpath(pid, executable, sizeof(executable)) > 0 &&
                    [[@(executable) stringByResolvingSymlinksInPath] isEqualToString:expected];
                if (trusted) {
                    struct timeval limit = {4, 0};
                    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit));
                    setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit));
                    int yes = 1; setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
                    NSMutableData *body = [NSMutableData data];
                    char c;
                    while (body.length < 4096 && recv(client, &c, 1, 0) == 1 && c != '\n')
                        [body appendBytes:&c length:1];
                    id message = [NSJSONSerialization JSONObjectWithData:body options:0 error:nil];
                    if ([message isKindOfClass:NSDictionary.class]) {
                        NSData *reply = [TVUpdateManagerLaunch(message, app) dataUsingEncoding:NSUTF8StringEncoding];
                        send(client, reply.bytes, reply.length, 0);
                    }
                }
                close(client);
            });
        }
        close(fd);
    });
}
