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

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <UserNotifications/UserNotifications.h>

#import "BulletinManager.h"
#import "Logging.h"

#define BANNER_CATEGORY "com.82flex.trollvnc.notification-category.standard"

@interface UNUserNotificationCenter (Private)
- (instancetype)initWithBundleIdentifier:(NSString *)bundleIdentifier;
@end

@implementation BulletinManager {
    NSString *mSectionIdentifier;
    UNUserNotificationCenter *mNotificationCenter;
    NSString *mSingleNotificationIdentifier;
    UIWindow *mToastWindow;
    UILabel *mToastLabel;
    NSUInteger mToastGeneration;
}

+ (instancetype)sharedManager {
    static BulletinManager *sharedManager = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        sharedManager = [[BulletinManager alloc] init];
    });
    return sharedManager;
}

- (instancetype)init {
    self = [super init];
    if (self) {
#if !TARGET_IPHONE_SIMULATOR
#ifdef THEBOOTSTRAP
        mSectionIdentifier = @"com.controlios.app";
#else
    // Match the bundle that requests notification authorization in the app.
    mSectionIdentifier = @"com.controlios.app";
#endif

        mNotificationCenter = [[UNUserNotificationCenter alloc] initWithBundleIdentifier:mSectionIdentifier];

        UNNotificationCategory *showTitleCategory = [UNNotificationCategory categoryWithIdentifier:@BANNER_CATEGORY
                                                                                           actions:@[]
                                                                                 intentIdentifiers:@[]
                                                                                           options:kNilOptions];

        [mNotificationCenter setNotificationCategories:[NSSet setWithObjects:showTitleCategory, nil]];

        mSingleNotificationIdentifier = nil;
#endif
    }
    return self;
}

- (void)updateSingleBannerWithContent:(NSString *)messageContent
                           badgeCount:(NSInteger)badgeCount
                             userInfo:(NSDictionary *)userInfo {
#if !TARGET_IPHONE_SIMULATOR
    UNMutableNotificationContent *content = [[UNMutableNotificationContent alloc] init];

    content.title = @"ControlIOS";
    content.body = messageContent;
    content.categoryIdentifier = @BANNER_CATEGORY;
    content.threadIdentifier = mSectionIdentifier;
    content.userInfo = userInfo;

#ifdef THEBOOTSTRAP
    content.badge = @(badgeCount);
#endif

    if (@available(iOS 15, *)) {
        if ([content respondsToSelector:@selector(setInterruptionLevel:)])
            content.interruptionLevel = UNNotificationInterruptionLevelPassive;
    }

    if (mSingleNotificationIdentifier) {
        [mNotificationCenter removePendingNotificationRequestsWithIdentifiers:@[ mSingleNotificationIdentifier ]];
        [mNotificationCenter removeDeliveredNotificationsWithIdentifiers:@[ mSingleNotificationIdentifier ]];
    }

    UNNotificationTrigger *trigger = [UNTimeIntervalNotificationTrigger triggerWithTimeInterval:0.33 repeats:NO];

    mSingleNotificationIdentifier = [[NSUUID UUID] UUIDString];
    UNNotificationRequest *request = [UNNotificationRequest requestWithIdentifier:mSingleNotificationIdentifier
                                                                          content:content
                                                                          trigger:trigger];

    [mNotificationCenter addNotificationRequest:request withCompletionHandler:nil];
#endif
}

- (void)popBannerWithContent:(NSString *)messageContent userInfo:(NSDictionary *)userInfo {
    if (messageContent.length > 0) {
        [self showToastOverlay:messageContent];
    }

#if !TARGET_IPHONE_SIMULATOR
    UNMutableNotificationContent *content = [[UNMutableNotificationContent alloc] init];

    content.title = @"ControlIOS";
    content.body = messageContent;
    content.categoryIdentifier = @BANNER_CATEGORY;
    content.threadIdentifier = mSectionIdentifier;
    content.userInfo = userInfo;
    content.sound = [UNNotificationSound defaultSound];

    if (@available(iOS 15, *)) {
        if ([content respondsToSelector:@selector(setInterruptionLevel:)])
            content.interruptionLevel = UNNotificationInterruptionLevelActive;
    }

    NSString *uuidString = [[NSUUID UUID] UUIDString];
    UNNotificationRequest *request = [UNNotificationRequest requestWithIdentifier:uuidString
                                                                          content:content
                                                                          trigger:nil];

    [mNotificationCenter addNotificationRequest:request withCompletionHandler:nil];
#endif
}

- (void)showToastOverlay:(NSString *)messageContent {
    dispatch_async(dispatch_get_main_queue(), ^{
        CGRect screenBounds = [UIScreen mainScreen].bounds;
        CGFloat screenWidth = screenBounds.size.width;
        UIFont *font = [UIFont systemFontOfSize:16.0 weight:UIFontWeightMedium];
        NSDictionary *attributes = @{ NSFontAttributeName : font };
        CGFloat textWidth = [messageContent boundingRectWithSize:CGSizeMake(screenWidth - 64.0, 0)
                                                         options:NSStringDrawingUsesLineFragmentOrigin
                                                      attributes:attributes
                                                         context:nil].size.width;
        CGFloat pillWidth = MAX(140.0, MIN(screenWidth - 32.0, textWidth + 40.0));
        CGFloat pillHeight = 44.0;
        CGFloat pillX = (screenWidth - pillWidth) / 2.0;
        CGFloat targetY = 44.0;
        CGFloat startY = -pillHeight - 12.0;

        if (self->mToastWindow) {
            [self->mToastWindow.layer removeAllAnimations];
            self->mToastWindow.hidden = YES;
        }

        UIWindow *window = [[UIWindow alloc] initWithFrame:CGRectMake(pillX, startY, pillWidth, pillHeight)];
        window.backgroundColor = [UIColor clearColor];
        window.opaque = NO;
        window.windowLevel = UIWindowLevelAlert + 3000.0;
        window.userInteractionEnabled = NO;

        UIViewController *controller = [UIViewController new];
        controller.view.frame = CGRectMake(0, 0, pillWidth, pillHeight);
        controller.view.backgroundColor = [UIColor clearColor];
        controller.view.userInteractionEnabled = NO;

        UILabel *label = [[UILabel alloc] initWithFrame:CGRectMake(16.0, 0, pillWidth - 32.0, pillHeight)];
        label.text = messageContent;
        label.textAlignment = NSTextAlignmentCenter;
        label.textColor = [UIColor whiteColor];
        label.font = font;
        label.adjustsFontSizeToFitWidth = YES;
        label.minimumScaleFactor = 0.7;
        label.backgroundColor = [[UIColor blackColor] colorWithAlphaComponent:0.86];
        label.layer.cornerRadius = pillHeight / 2.0;
        label.layer.masksToBounds = YES;
        [controller.view addSubview:label];
        window.rootViewController = controller;
        self->mToastWindow = window;
        self->mToastLabel = label;
        self->mToastGeneration += 1;
        NSUInteger generation = self->mToastGeneration;
        window.hidden = NO;

        [UIView animateWithDuration:0.25
                              delay:0.0
             usingSpringWithDamping:0.8
              initialSpringVelocity:1.0
                            options:UIViewAnimationOptionCurveEaseInOut
                         animations:^{
                             window.frame = CGRectMake(pillX, targetY, pillWidth, pillHeight);
                         }
                         completion:nil];

        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.5 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            if (generation != self->mToastGeneration)
                return;
            [UIView animateWithDuration:0.25 animations:^{
                window.frame = CGRectMake(pillX, startY, pillWidth, pillHeight);
            } completion:^(__unused BOOL finished) {
                if (generation == self->mToastGeneration)
                    window.hidden = YES;
            }];
        });
    });
}

- (void)revokeSingleNotification {
#if !TARGET_IPHONE_SIMULATOR
    [self resetBadgeCount];
    if (mSingleNotificationIdentifier) {
        [mNotificationCenter removePendingNotificationRequestsWithIdentifiers:@[ mSingleNotificationIdentifier ]];
        [mNotificationCenter removeDeliveredNotificationsWithIdentifiers:@[ mSingleNotificationIdentifier ]];
        mSingleNotificationIdentifier = nil;
    }
#endif
}

- (void)revokeAllNotifications {
    mSingleNotificationIdentifier = nil;
    [mNotificationCenter removeAllPendingNotificationRequests];
    [mNotificationCenter removeAllDeliveredNotifications];
}

#pragma mark - Private Methods

- (void)resetBadgeCount {
#if !TARGET_IPHONE_SIMULATOR
#ifdef THEBOOTSTRAP
    if (@available(iOS 16, *)) {
        if ([mNotificationCenter respondsToSelector:@selector(setBadgeCount:withCompletionHandler:)]) {
            [mNotificationCenter setBadgeCount:0
                         withCompletionHandler:^(NSError *_Nullable error) {
                             if (error) {
                                 TVLog(@"Error setting badge count: %@", error);
                             }
                         }];
        } else {
            [self updateSingleBannerWithContent:@"" badgeCount:0 userInfo:nil];
        }
    } else {
        [self updateSingleBannerWithContent:@"" badgeCount:0 userInfo:nil];
    }
#endif
#endif
}

@end
