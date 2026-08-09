#import <UIKit/UIKit.h>
#import "AppDelegate.h"
#import <crt_externs.h>
#import <spawn.h>
#import <sys/stat.h>

// Spawn keeperd NGAY trong main(), trước UIApplicationMain — bảo đảm daemon được
// bật dù phần UI có trục trặc gì. keeperd tự giết bản cũ và chiếm cổng.
extern int posix_spawnattr_set_persona_np(posix_spawnattr_t *, uid_t, uint32_t)
    __attribute__((weak_import));
extern int posix_spawnattr_set_persona_uid_np(posix_spawnattr_t *, uid_t)
    __attribute__((weak_import));
extern int posix_spawnattr_set_persona_gid_np(posix_spawnattr_t *, uid_t)
    __attribute__((weak_import));
#ifndef POSIX_SPAWN_SETSID
#define POSIX_SPAWN_SETSID 0x0400
#endif

static void KeeperSpawnDaemon(void) {
    NSString *path = [[NSBundle mainBundle] pathForResource:@"keeperd" ofType:@""];
    if (!path) {
        NSLog(@"[Keeper] KHÔNG thấy keeperd trong bundle");
        return;
    }
    chmod(path.fileSystemRepresentation, 0755);
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    if (posix_spawnattr_set_persona_np && posix_spawnattr_set_persona_uid_np &&
        posix_spawnattr_set_persona_gid_np) {
        posix_spawnattr_set_persona_np(&attr, 99, 1);
        posix_spawnattr_set_persona_uid_np(&attr, 0);
        posix_spawnattr_set_persona_gid_np(&attr, 0);
    }
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSID);
    const char *cpath = path.fileSystemRepresentation;
    char *const argv[] = {(char *)cpath, NULL};
    pid_t pid = 0;
    int rc = posix_spawn(&pid, cpath, NULL, &attr, argv, *_NSGetEnviron());
    posix_spawnattr_destroy(&attr);
    NSLog(@"[Keeper] spawn keeperd rc=%d pid=%d", rc, pid);
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        KeeperSpawnDaemon();
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([AppDelegate class]));
    }
}
