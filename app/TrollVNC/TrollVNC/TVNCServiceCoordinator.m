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

#import "TVNCServiceCoordinator.h"
#import "TrollVNC-Swift.h"

#import <Foundation/Foundation.h>
#import <MobileCoreServices/LSApplicationProxy.h>
#import <arpa/inet.h>
#import <netinet/in.h>
#import <sys/socket.h>

#import "Control.h"
#import "../../../include-spi/TVNCSocket.h"

NSNotificationName const TVNCServiceStatusDidChangeNotification = @"TVNCServiceStatusDidChangeNotification";

FOUNDATION_EXPORT NSString *const SBSApplicationLaunchOptionUnlockDeviceKey;
FOUNDATION_EXPORT
int SBSLaunchApplicationWithIdentifierAndURLAndLaunchOptions(CFStringRef bundleIdentifier, CFURLRef url,
                                                             CFDictionaryRef appOptions, CFDictionaryRef launchOptions,
                                                             BOOL suspended);

@interface TVNCServiceCoordinator ()
@property(nonatomic, strong) NSTimer *checkTimer;
@property(nonatomic, strong) NSUserDefaults *userDefaults;
@property(nonatomic, assign) BOOL checking;
@end

@implementation TVNCServiceCoordinator

+ (instancetype)sharedCoordinator {
    static TVNCServiceCoordinator *sharedInstance = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        sharedInstance = [[self alloc] init];
    });
    return sharedInstance;
}

+ (NSDictionary *)sharedTaskEnvironment {
    static NSDictionary *sharedEnvironment = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        NSMutableDictionary *env =
            [NSMutableDictionary dictionaryWithDictionary:[[NSProcessInfo processInfo] environment]];
        NSString *languageCode = [[NSLocale preferredLanguages] firstObject];
        if (languageCode) {
            env[@"TVNC_LANGUAGE_CODE"] = languageCode;
        }
#if TARGET_IPHONE_SIMULATOR
        [env addEntriesFromDictionary:[[NSProcessInfo processInfo] environment]];
#endif
        sharedEnvironment = [env copy];
    });
    return sharedEnvironment;
}

- (instancetype)init {
    self = [super init];
    if (self) {
        [self commonInit];
    }
    return self;
}

- (void)commonInit {
    _checkTimer = nil;
    _serviceRunning = NO;
    _userDefaults = [[NSUserDefaults alloc] initWithSuiteName:@"com.82flex.trollvnc"];

    NSBundle *prefsBundle = [NSBundle bundleWithPath:[[NSBundle mainBundle] pathForResource:@"TrollVNCPrefs"
                                                                                     ofType:@"bundle"]];

    NSString *presetPath = [prefsBundle pathForResource:@"Managed" ofType:@"plist"];
    if (presetPath) {
        NSDictionary *presetDefaults = [NSDictionary dictionaryWithContentsOfFile:presetPath];
        if (presetDefaults) {
            [_userDefaults registerDefaults:presetDefaults];
        }
    }
}

#pragma mark - Public Methods

- (void)registerServiceMonitor {
    [_checkTimer invalidate];
    [self checkTimerFired:nil];
    _checkTimer = [NSTimer scheduledTimerWithTimeInterval:3.0
                                                   target:self
                                                 selector:@selector(checkTimerFired:)
                                                 userInfo:nil
                                                  repeats:YES];
}

- (BOOL)isServiceRunning {
    return _serviceRunning;
}

#pragma mark - Private Methods

- (void)checkTimerFired:(NSTimer *_Nullable)timer {
    if (self.checking)
        return;
    self.checking = YES;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        [self ensureServiceRunning];
        dispatch_async(dispatch_get_main_queue(), ^{ self.checking = NO; });
    });
}

- (void)ensureServiceRunning {
    BOOL running = [self _isServiceRunning];
    if (!running) {
        [self checkPrebootDependencies];
        [self spawnService];
    }
    dispatch_async(dispatch_get_main_queue(), ^{
        if (self->_serviceRunning != running) {
            self->_serviceRunning = running;
            [[NSNotificationCenter defaultCenter] postNotificationName:TVNCServiceStatusDidChangeNotification object:self];
        }
    });
}

- (BOOL)_isServiceRunning {
#if TARGET_IPHONE_SIMULATOR
    return YES;
#else
    int sockfd = TVNCConnectLoopback(kTvAlivePort, 0.75);
    if (sockfd < 0) {
        return NO;
    }

    close(sockfd);
    return YES;
#endif
}

- (void)spawnService {
    static TRTask *serviceTask = nil;
    serviceTask = [[TRTask alloc] init];

    NSString *executablePath = [[NSBundle mainBundle] pathForResource:@"trollvncmanager" ofType:@""];
    if (!executablePath) {
        return;
    }

    [serviceTask setExecutableURL:[NSURL fileURLWithPath:executablePath]];

#if !TARGET_IPHONE_SIMULATOR
    [serviceTask setUserIdentifier:0];
    [serviceTask setGroupIdentifier:0];
#endif

    [serviceTask setArguments:[NSArray array]];
    [serviceTask setEnvironment:[TVNCServiceCoordinator sharedTaskEnvironment]];

    NSError *error = nil;
    BOOL launched = [serviceTask launchAndReturnError:&error];
    if (!launched) {
#if DEBUG
        NSLog(@"[TVNC] Failed to launch service: %@", error);
#endif
        return;
    }

    int unused;
    waitpid(serviceTask.processIdentifier, &unused, WNOHANG);
}

- (void)checkPrebootDependencies {
#if !TARGET_IPHONE_SIMULATOR
    id configVal = [_userDefaults objectForKey:@"LaunchAtLogin"];

    NSString *appId = nil;
    if ([configVal isKindOfClass:[NSNumber class]]) {
        BOOL launchAtLogin = [(NSNumber *)configVal boolValue];
        if (launchAtLogin) {
            appId = [[NSBundle mainBundle] bundleIdentifier];
        }
    } else if ([configVal isKindOfClass:[NSString class]]) {
        appId = (NSString *)configVal;
    } else if ([configVal isKindOfClass:[NSArray class]]) {
        NSArray *appIds = (NSArray *)configVal;
        for (NSString *candidateAppId in appIds) {
            LSApplicationProxy *appProxy = [LSApplicationProxy applicationProxyForIdentifier:candidateAppId];
            if (![appProxy isInstalled]) {
                continue;
            }
            appId = candidateAppId;
        }
    }

    if (!appId) {
        return;
    }

    NSDate *lastLaunch = [_userDefaults objectForKey:@"LastPrebootLaunch"];
    if (lastLaunch) {
        // Compare with device uptime
        NSTimeInterval uptime = [[NSProcessInfo processInfo] systemUptime];
        NSDate *bootTime = [NSDate dateWithTimeIntervalSinceNow:-uptime];
        if ([lastLaunch compare:bootTime] == NSOrderedDescending) {
            // Already launched since last boot
            return;
        }
    }

    UInt32 result;
    result = SBSLaunchApplicationWithIdentifierAndURLAndLaunchOptions(
        (__bridge CFStringRef)appId, NULL, NULL,
        (__bridge CFDictionaryRef) @{SBSApplicationLaunchOptionUnlockDeviceKey : @YES}, NO);

    if (result == 0) {
        NSDate *now = [NSDate date];
        [_userDefaults setObject:now forKey:@"LastPrebootLaunch"];
        [_userDefaults synchronize];
    }
#endif
}

@end
