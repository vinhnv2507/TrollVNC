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

#import "AppDelegate.h"
#import "TVNCHotspotManager.h"
#import "TVNCServiceCoordinator.h"
#import <arpa/inet.h>
#import <dlfcn.h>
#import <netinet/in.h>
#import <sys/socket.h>
#import <unistd.h>

#ifdef THEBOOTSTRAP
#import "GitHubReleaseUpdater.h"
#endif

static NSString *const kControlIOSKeeperBundleID = @"com.controlios.keeper";
static const int kControlIOSKeeperPort = 46753;

static BOOL TVControlIOSKeeperdRunning(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return NO;
    struct timeval timeout = {1, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)kControlIOSKeeperPort);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    BOOL running = connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0;
    close(fd);
    return running;
}

static void TVEnsureControlIOSKeeperRunning(void) {
    // Commit 150ac2a nhận diện Keeper bằng keeperd giữ cổng 46753. App Keeper
    // có thể đã đóng nhưng daemon vẫn sống, nên không kiểm tra PID của app.
    if (TVControlIOSKeeperdRunning())
        return;

    void *handle = dlopen("/System/Library/PrivateFrameworks/SpringBoardServices.framework/"
                          "SpringBoardServices", RTLD_LAZY);
    if (!handle)
        return;

    int result = -1;
    int (*launchOptions)(CFStringRef, CFDictionaryRef, Boolean) =
        (int (*)(CFStringRef, CFDictionaryRef, Boolean))dlsym(
            handle, "SBSLaunchApplicationWithIdentifierAndLaunchOptions");
    if (launchOptions)
        result = launchOptions((__bridge CFStringRef)kControlIOSKeeperBundleID, NULL, false);
    if (result != 0) {
        int (*launch)(CFStringRef, Boolean) =
            (int (*)(CFStringRef, Boolean))dlsym(handle,
                                                 "SBSLaunchApplicationWithIdentifier");
        if (launch)
            launch((__bridge CFStringRef)kControlIOSKeeperBundleID, false);
    }
}

@implementation AppDelegate

- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    // Override point for customization after application launch.
    [[TVNCServiceCoordinator sharedCoordinator] registerServiceMonitor];
    [[TVNCHotspotManager sharedManager] registerWithName:@"ControlIOS"];
    TVEnsureControlIOSKeeperRunning();
    [NSTimer scheduledTimerWithTimeInterval:10.0
                                     repeats:YES
                                       block:^(__unused NSTimer *timer) {
                                           TVEnsureControlIOSKeeperRunning();
                                       }];

#ifdef THEBOOTSTRAP
    // Initialize Auto Updater
    GHUpdateStrategy *updateStrategy = [[GHUpdateStrategy alloc] init];
    [updateStrategy setRepoFullName:@"OwnGoalStudio/TrollVNC"];

    GitHubReleaseUpdater *updater = [GitHubReleaseUpdater shared];
#if TARGET_IPHONE_SIMULATOR
    [updater configureWithStrategy:updateStrategy];
#else
    [updater configureWithStrategy:updateStrategy currentVersion:@PACKAGE_VERSION];
#endif
    [updater start];
#endif

    return YES;
}

- (void)applicationDidBecomeActive:(UIApplication *)application {
    TVEnsureControlIOSKeeperRunning();
}

#pragma mark - UISceneSession lifecycle

- (UISceneConfiguration *)application:(UIApplication *)application
    configurationForConnectingSceneSession:(UISceneSession *)connectingSceneSession
                                   options:(UISceneConnectionOptions *)options {
    // Called when a new scene session is being created.
    // Use this method to select a configuration to create the new scene with.
    return [[UISceneConfiguration alloc] initWithName:@"Default Configuration" sessionRole:connectingSceneSession.role];
}

- (void)application:(UIApplication *)application didDiscardSceneSessions:(NSSet<UISceneSession *> *)sceneSessions {
    // Called when the user discards a scene session.
    // If any sessions were discarded while the application was not running, this will be called shortly after
    // application:didFinishLaunchingWithOptions. Use this method to release any resources that were specific to the
    // discarded scenes, as they will not return.
}

@end
