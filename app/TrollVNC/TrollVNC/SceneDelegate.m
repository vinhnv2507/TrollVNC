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

#import "SceneDelegate.h"
#import <notify.h>

static const char *kTVNCTouchLockNotification = "com.controlios.touchlock.changed";

@interface CAContext : NSObject
- (void)setSecure:(BOOL)secure;
@end

@interface UIWindow (TVNCTouchLockPrivate)
- (CAContext *)_boundContext;
- (unsigned int)_contextId;
@end

@interface SBSAccessibilityWindowHostingController : NSObject
- (void)registerWindowWithContextID:(unsigned int)contextID atLevel:(double)level;
@end

@interface TVNCTouchBlockWindow : UIWindow
@end

@implementation TVNCTouchBlockWindow
+ (BOOL)_isSecure { return YES; }
+ (BOOL)_isSystemWindow { return YES; }
- (BOOL)_isWindowServerHostingManaged { return NO; }
- (BOOL)_ignoresHitTest { return NO; }
- (BOOL)_isSecure { return YES; }
- (BOOL)_shouldCreateContextAsSecure { return YES; }
@end

@interface SceneDelegate ()
@property(nonatomic, strong) TVNCTouchBlockWindow *touchBlockWindow;
@property(nonatomic, strong) SBSAccessibilityWindowHostingController *touchBlockHost;
@property(nonatomic, assign) int touchLockNotifyToken;
@property(nonatomic, assign) BOOL touchBlockRegistered;
@end

@implementation SceneDelegate

- (void)scene:(UIScene *)scene
    willConnectToSession:(UISceneSession *)session
                 options:(UISceneConnectionOptions *)connectionOptions {
    // Use this method to optionally configure and attach the UIWindow `window` to the provided UIWindowScene `scene`.
    // If using a storyboard, the `window` property will automatically be initialized and attached to the scene.
    // This delegate does not imply the connecting scene or session are new (see
    // `application:configurationForConnectingSceneSession` instead).
    __weak typeof(self) weakSelf = self;
    notify_register_dispatch(kTVNCTouchLockNotification, &_touchLockNotifyToken,
                             dispatch_get_main_queue(), ^(int token) {
        uint64_t state = 0;
        notify_get_state(token, &state);
        [weakSelf setTouchBlockingEnabled:(state != 0) inScene:(UIWindowScene *)scene];
    });

    uint64_t state = 0;
    notify_get_state(_touchLockNotifyToken, &state);
    [self setTouchBlockingEnabled:(state != 0) inScene:(UIWindowScene *)scene];
}

- (void)setTouchBlockingEnabled:(BOOL)enabled inScene:(UIWindowScene *)scene {
    if (!enabled) {
        self.touchBlockWindow.hidden = YES;
        return;
    }

    if (!self.touchBlockWindow) {
        TVNCTouchBlockWindow *window = [[TVNCTouchBlockWindow alloc] initWithWindowScene:scene];
        window.frame = scene.screen.bounds;
        window.windowLevel = 10000001.0;
        window.backgroundColor = [UIColor colorWithWhite:0.0 alpha:0.12];

        UIViewController *controller = [[UIViewController alloc] init];
        controller.view.backgroundColor = [UIColor clearColor];
        controller.view.userInteractionEnabled = YES;

        UILabel *label = [[UILabel alloc] init];
        label.translatesAutoresizingMaskIntoConstraints = NO;
        label.text = @"🔒\nControlIOS đã khóa cảm ứng";
        label.numberOfLines = 2;
        label.textAlignment = NSTextAlignmentCenter;
        label.textColor = [UIColor whiteColor];
        label.font = [UIFont boldSystemFontOfSize:18.0];
        label.backgroundColor = [UIColor colorWithWhite:0.0 alpha:0.55];
        label.layer.cornerRadius = 14.0;
        label.layer.masksToBounds = YES;
        label.userInteractionEnabled = YES;
        [controller.view addSubview:label];
        [NSLayoutConstraint activateConstraints:@[
            [label.centerXAnchor constraintEqualToAnchor:controller.view.centerXAnchor],
            [label.centerYAnchor constraintEqualToAnchor:controller.view.centerYAnchor],
            [label.widthAnchor constraintGreaterThanOrEqualToConstant:245.0],
            [label.heightAnchor constraintEqualToConstant:86.0],
        ]];

        window.rootViewController = controller;
        self.touchBlockWindow = window;
    }

    self.touchBlockWindow.hidden = NO;
    [self.touchBlockWindow makeKeyAndVisible];

    if (!self.touchBlockRegistered) {
        self.touchBlockHost = [[NSClassFromString(@"SBSAccessibilityWindowHostingController") alloc] init];
        unsigned int contextID = [self.touchBlockWindow _contextId];
        [[self.touchBlockWindow _boundContext] setSecure:YES];
        [self.touchBlockHost registerWindowWithContextID:contextID
                                                 atLevel:self.touchBlockWindow.windowLevel];
        self.touchBlockRegistered = YES;
    }
}

- (void)sceneDidDisconnect:(UIScene *)scene {
    // Called as the scene is being released by the system.
    // This occurs shortly after the scene enters the background, or when its session is discarded.
    // Release any resources associated with this scene that can be re-created the next time the scene connects.
    // The scene may re-connect later, as its session was not necessarily discarded (see
    // `application:didDiscardSceneSessions` instead).
}

- (void)sceneDidBecomeActive:(UIScene *)scene {
    // Called when the scene has moved from an inactive state to an active state.
    // Use this method to restart any tasks that were paused (or not yet started) when the scene was inactive.
}

- (void)sceneWillResignActive:(UIScene *)scene {
    // Called when the scene will move from an active state to an inactive state.
    // This may occur due to temporary interruptions (ex. an incoming phone call).
}

- (void)sceneWillEnterForeground:(UIScene *)scene {
    // Called as the scene transitions from the background to the foreground.
    // Use this method to undo the changes made on entering the background.
}

- (void)sceneDidEnterBackground:(UIScene *)scene {
    // Called as the scene transitions from the foreground to the background.
    // Use this method to save data, release shared resources, and store enough scene-specific state information
    // to restore the scene back to its current state.
}

- (void)dealloc {
    if (_touchLockNotifyToken > 0)
        notify_cancel(_touchLockNotifyToken);
}

@end
