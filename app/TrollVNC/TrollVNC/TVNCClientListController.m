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

#import "TVNCClientListController.h"
#import "TVNCClientCell.h"

#import <UIKit/UIKit.h>
#import <arpa/inet.h>
#import <errno.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <string.h>
#import <sys/socket.h>
#import <unistd.h>

#import "Control.h"

#pragma mark - Networking

// Placeholder item id used when there are no clients
static NSString *const kTVNCEmptyItemId = @"__empty__";

static inline BOOL TVNCIsEmptyItemId(NSString *_Nullable itemId) {
    return itemId != nil && [itemId isEqualToString:kTVNCEmptyItemId];
}

static NSData *TVNCReadAll(int fd, double timeoutSec) {
    NSMutableData *md = [NSMutableData data];
    struct timeval tv;
    tv.tv_sec = (int)timeoutSec;
    tv.tv_usec = (int)((timeoutSec - tv.tv_sec) * 1e6);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    uint8_t buf[2048];
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n < 0) {
            // EAGAIN/EWOULDBLOCK means timeout fired — no more data available
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                break;
            if (errno == EINTR)
                continue;
            break; // real error
        }
        if (n == 0)
            break; // peer closed / EOF
        [md appendBytes:buf length:(NSUInteger)n];
    }
    return md;
}

static int TVNCSendLine(int fd, NSString *line) {
    NSString *ln = [line hasSuffix:@"\n"] ? line : [line stringByAppendingString:@"\n"];
    NSData *d = [ln dataUsingEncoding:NSUTF8StringEncoding];
    const uint8_t *p = d.bytes;
    size_t left = d.length;
    while (left > 0) {
        ssize_t n = send(fd, p, left, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0)
            break;
        p += (size_t)n;
        left -= (size_t)n;
    }
    return 0;
}

static int TVNCConnect(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_len = sizeof(addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(kTvDefaultCtlPort);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

#pragma mark - Private Interface

@interface TVNCClientListController ()

@property(nonatomic, strong) UIBarButtonItem *dismissItem;
@property(nonatomic, strong) UIBarButtonItem *disconnectItem;

@property(nonatomic, strong) UITableViewDiffableDataSource<NSString *, NSString *> *dataSource; // section -> itemId
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSDictionary *> *clientLookup;     // id -> dict

// Subscription (long-lived connection)
@property(nonatomic, assign) int subFd;
@property(nonatomic, strong) dispatch_source_t subReadSource;

// Reconnection state
@property(nonatomic, strong) dispatch_source_t subReconnectTimer;
@property(nonatomic, assign) NSTimeInterval subReconnectDelay;
@property(nonatomic, assign) BOOL subIntentionallyStopped;

@end

#pragma mark - Implementation

@implementation TVNCClientListController

- (void)viewDidLoad {
    [super viewDidLoad];

    self.title = NSLocalizedStringFromTableInBundle(@"Clients", @"Localizable", self.bundle, nil);

    UIRefreshControl *refreshControl = [UIRefreshControl new];
    [refreshControl addTarget:self action:@selector(refresh) forControlEvents:UIControlEventValueChanged];
    self.refreshControl = refreshControl;

    self.navigationItem.leftBarButtonItem = self.disconnectItem;
    self.navigationItem.rightBarButtonItem = self.dismissItem;

    // Diffable data source
    self.clientLookup = [NSMutableDictionary new];
    __weak typeof(self) weakSelf = self;
    self.dataSource = [[UITableViewDiffableDataSource alloc]
        initWithTableView:self.tableView
             cellProvider:^UITableViewCell *_Nullable(UITableView *tableView, NSIndexPath *indexPath,
                                                      NSString *identifier) {
                 return [weakSelf cellForTableView:tableView indexPath:indexPath itemId:identifier];
             }];

    // Initial empty snapshot with one section
    NSDiffableDataSourceSnapshot<NSString *, NSString *> *empty = [NSDiffableDataSourceSnapshot new];
    [empty appendSectionsWithIdentifiers:@[ @"main" ]];
    [self.dataSource applySnapshot:empty animatingDifferences:NO];

    [self refresh];
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    self.subIntentionallyStopped = NO;
    self.subReconnectDelay = 1.0;
    [self startSubscriptionIfNeeded];
}

- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    [self stopSubscription];
}

- (void)dealloc {
    [self stopSubscription];
}

#pragma mark - Getters

- (UIBarButtonItem *)dismissItem {
    if (!_dismissItem) {
        _dismissItem = [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemClose
                                                                     target:self
                                                                     action:@selector(dismiss)];
    }
    return _dismissItem;
}

- (UIBarButtonItem *)disconnectItem {
    if (!_disconnectItem) {
        NSString *title = NSLocalizedStringFromTableInBundle(@"Disconnect All", @"Localizable", self.bundle, nil);
        _disconnectItem = [[UIBarButtonItem alloc] initWithTitle:title
                                                           style:UIBarButtonItemStylePlain
                                                          target:self
                                                          action:@selector(disconnectAll)];
        _disconnectItem.tintColor = self.primaryColor;
        _disconnectItem.enabled = NO;
    }
    return _disconnectItem;
}

#pragma mark - Subscription (Plan B)

- (void)startSubscriptionIfNeeded {
    if (self.subFd > 0 || self.subReadSource)
        return;

    int fd = TVNCConnect();
    if (fd < 0)
        return;

    if (TVNCSendLine(fd, @"subscribe on") < 0) {
        close(fd);
        return;
    }

    // Verify server acknowledged the subscription
    NSData *okData = TVNCReadAll(fd, 0.5);
    if (!okData || okData.length < 2 || memmem(okData.bytes, okData.length, "OK", 2) == NULL) {
        close(fd);
        return;
    }

    // Enable TCP keepalive to detect dead connections promptly
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &yes, sizeof(yes));
#ifdef TCP_KEEPALIVE
    int idle = 5; // Start probing after 5s idle
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPALIVE, &idle, sizeof(idle));
#endif
#ifdef TCP_KEEPINTVL
    int intvl = 2; // Probe every 2s
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof(intvl));
#endif
#ifdef TCP_KEEPCNT
    int cnt = 3; // Give up after 3 failed probes (~11s total)
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &cnt, sizeof(cnt));
#endif

    self.subFd = fd;

    dispatch_queue_t q = dispatch_get_main_queue();
    dispatch_source_t src = dispatch_source_create(DISPATCH_SOURCE_TYPE_READ, (uintptr_t)fd, 0, q);
    __weak typeof(self) weakSelf = self;
    dispatch_source_set_event_handler(src, ^{
        [weakSelf onSubscriptionReadable];
    });

    dispatch_source_set_cancel_handler(src, ^{
        if (weakSelf.subFd > 0) {
            close(weakSelf.subFd);
            weakSelf.subFd = 0;
        }
    });

    self.subReadSource = src;
    dispatch_resume(src);
}

- (void)teardownSubscriptionConnection {
    if (self.subReadSource) {
        dispatch_source_cancel(self.subReadSource);
        self.subReadSource = nil;
    }
    if (self.subFd > 0) {
        close(self.subFd);
        self.subFd = 0;
    }
}

- (void)stopSubscription {
    [self cancelReconnect];
    self.subIntentionallyStopped = YES;
    self.subReconnectDelay = 1.0;

    [self teardownSubscriptionConnection];
}

- (void)scheduleReconnect {
    if (self.subIntentionallyStopped)
        return;
    if (self.subReconnectTimer)
        return;

    // Add ±20% jitter to avoid thundering herd
    NSTimeInterval base = self.subReconnectDelay;
    if (base < 1.0)
        base = 1.0;
    double jitter = base * 0.2 * ((double)arc4random_uniform(UINT32_MAX) / UINT32_MAX * 2.0 - 1.0);
    NSTimeInterval delay = base + jitter;

    dispatch_queue_t q = dispatch_get_main_queue();
    dispatch_source_t t = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, q);
    uint64_t delayNs = (uint64_t)(delay * NSEC_PER_SEC);
    dispatch_source_set_timer(t, dispatch_time(DISPATCH_TIME_NOW, delayNs), DISPATCH_TIME_FOREVER, delayNs / 10);

    __weak typeof(self) weakSelf = self;
    dispatch_source_set_event_handler(t, ^{
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf)
            return;

        strongSelf.subReconnectTimer = nil;

        [strongSelf startSubscriptionIfNeeded];
        if (strongSelf.subFd > 0) {
            // Reconnected successfully — reset backoff and pull fresh data
            strongSelf.subReconnectDelay = 1.0;
            [strongSelf refresh];
        } else {
            // Still failing — exponential backoff, cap at 30s
            strongSelf.subReconnectDelay = MIN(strongSelf.subReconnectDelay * 2.0, 30.0);
            [strongSelf scheduleReconnect];
        }
    });

    self.subReconnectTimer = t;
    dispatch_resume(t);
}

- (void)cancelReconnect {
    if (self.subReconnectTimer) {
        dispatch_source_cancel(self.subReconnectTimer);
        self.subReconnectTimer = nil;
    }
}

#pragma mark - Actions

- (void)dismiss {
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (void)refresh {
    [self reloadDataFromServer];
}

// Removed index-based disconnect; use -disconnectClientWithId:block: instead.

- (void)disconnectClientWithId:(NSString *)cid block:(BOOL)shouldBlock {
    if (cid.length == 0)
        return;

    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        int fd = TVNCConnect();
        if (fd >= 0) {
            NSString *cmd = shouldBlock ? @"block" : @"disconnect";
            TVNCSendLine(fd, [NSString stringWithFormat:@"%@ %@", cmd, cid]);
            (void)TVNCReadAll(fd, 2.0);
            close(fd);
        }

        dispatch_async(dispatch_get_main_queue(), ^{
            [self refresh];
        });
    });
}

- (void)disconnectAll {
    [self.disconnectItem setEnabled:NO];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        int fd = TVNCConnect();
        if (fd >= 0) {
            TVNCSendLine(fd, @"disconnect ALL");
            (void)TVNCReadAll(fd, 2.0);
            close(fd);
        }

        dispatch_async(dispatch_get_main_queue(), ^{
            [self refresh];
        });
    });
}

#pragma mark - Helpers (Cells)

- (UITableViewCell *)cellForTableView:(UITableView *)tableView
                            indexPath:(NSIndexPath *)indexPath
                               itemId:(NSString *)identifier {
    if (TVNCIsEmptyItemId(identifier)) {
        return [self dequeuePlaceholderCellForTableView:tableView];
    }
    return [self dequeueClientCellForTableView:tableView itemId:identifier];
}

- (UITableViewCell *)dequeuePlaceholderCellForTableView:(UITableView *)tableView {
    static NSString *const kEmptyReuse = @"TVNCEmptyCell";
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:kEmptyReuse];
    if (!cell) {
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:kEmptyReuse];
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        cell.textLabel.textAlignment = NSTextAlignmentCenter;
        cell.textLabel.textColor = [UIColor secondaryLabelColor];
        cell.textLabel.numberOfLines = 0;
    }
    cell.textLabel.text = NSLocalizedStringFromTableInBundle(@"No clients connected", @"Localizable", self.bundle, nil);
    return cell;
}

- (UITableViewCell *)dequeueClientCellForTableView:(UITableView *)tableView itemId:(NSString *)identifier {
    TVNCClientCell *cell = (TVNCClientCell *)[tableView dequeueReusableCellWithIdentifier:@"TVNCClientCell"];
    if (!cell) {
        cell = [[TVNCClientCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:@"TVNCClientCell"];
        cell.bundle = self.bundle;
    }

    NSDictionary *c = self.clientLookup[identifier] ?: @{};
    NSString *cid = c[@"id"] ?: identifier ?: @"";
    NSString *host = c[@"host"] ?: @"";
    BOOL vo = [[c objectForKey:@"viewOnly"] boolValue] || [[c objectForKey:@"viewOnly"] isEqual:@"1"];
    double dur = [[c objectForKey:@"durationSec"] doubleValue];

    static NSRelativeDateTimeFormatter *sFmt;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        sFmt = [NSRelativeDateTimeFormatter new];
        sFmt.unitsStyle = NSRelativeDateTimeFormatterUnitsStyleFull;
    });

    NSString *rel = [sFmt localizedStringFromTimeInterval:-dur];
    NSString *subtitle = [NSString
        stringWithFormat:NSLocalizedStringFromTableInBundle(@"Connected %@", @"Localizable", self.bundle, nil),
                         rel ?: @"-"];

    [cell configureWithId:cid host:host viewOnly:vo subtitle:subtitle primaryColor:self.primaryColor];
    cell.accessoryType = UITableViewCellAccessoryNone;
    return cell;
}

#pragma mark - Helpers (Networking)

- (NSArray<NSDictionary *> *)parseTSV:(NSString *)tsv {
    if (tsv.length == 0)
        return @[];
    NSArray<NSString *> *lines = [tsv componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]];
    NSMutableArray<NSDictionary *> *rows =
        [NSMutableArray arrayWithCapacity:MAX((NSInteger)0, (NSInteger)lines.count - 1)];
    BOOL first = YES;
    for (NSString *ln in lines) {
        if (ln.length == 0)
            continue;
        if (first) {
            first = NO;
            continue;
        } // skip header
        NSArray *cols = [ln componentsSeparatedByString:@"\t"];
        if (cols.count < 5)
            continue;
        [rows addObject:@{
            @"id" : cols[0],
            @"host" : cols[1],
            @"viewOnly" : cols[2],
            @"connectedAt" : cols[3],
            @"durationSec" : cols[4]
        }];
    }
    return rows;
}

- (void)applyRows:(NSArray<NSDictionary *> *)rows {
    [self.clientLookup removeAllObjects];

    NSMutableArray<NSString *> *ids = [NSMutableArray arrayWithCapacity:rows.count];
    for (NSDictionary *item in rows) {
        NSString *cid = item[@"id"] ?: @"";
        if (!cid.length)
            continue;
        self.clientLookup[cid] = item;
        [ids addObject:cid];
    }

    NSDiffableDataSourceSnapshot<NSString *, NSString *> *snap = [NSDiffableDataSourceSnapshot new];
    [snap appendSectionsWithIdentifiers:@[ @"main" ]];
    if (ids.count == 0) {
        [snap appendItemsWithIdentifiers:@[ kTVNCEmptyItemId ] intoSectionWithIdentifier:@"main"];
    } else {
        [snap appendItemsWithIdentifiers:ids intoSectionWithIdentifier:@"main"];
        [snap reloadItemsWithIdentifiers:ids]; // force reconfigure for content changes
    }

    [self.dataSource applySnapshot:snap animatingDifferences:YES];
    [self.disconnectItem setEnabled:(ids.count > 0)];
}

- (void)reloadDataFromServer {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        int fd = TVNCConnect();
        if (fd < 0) {
            dispatch_async(dispatch_get_main_queue(), ^{
                [self.refreshControl endRefreshing];
                [self applyRows:@[]];
            });
            return;
        }

        TVNCSendLine(fd, @"list");
        NSData *data = TVNCReadAll(fd, 2.0);
        close(fd);

        NSString *tsv = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
        NSArray<NSDictionary *> *rows = [self parseTSV:tsv];
        dispatch_async(dispatch_get_main_queue(), ^{
            [self.refreshControl endRefreshing];
            [self applyRows:rows];
        });
    });
}

- (void)onSubscriptionReadable {
    int fd = self.subFd;
    if (fd <= 0) {
        return;
    }
    uint8_t buf[256];
    ssize_t n = recv(fd, buf, sizeof(buf) - 1, 0);
    if (n <= 0) {
        // Connection lost — tear down and schedule reconnect
        [self teardownSubscriptionConnection];
        [self scheduleReconnect];
        return;
    }
    buf[n] = '\0';
    // Any line containing "changed" triggers a refresh
    if (memmem(buf, (size_t)n, "changed", 7) != NULL) {
        [self refresh];
    }
}

#pragma mark - Table

// Diffable data source drives cells; no need to implement UITableViewDataSource methods here.
- (UISwipeActionsConfiguration *)tableView:(UITableView *)tableView
    trailingSwipeActionsConfigurationForRowAtIndexPath:(NSIndexPath *)indexPath {

    NSString *itemId = [self.dataSource itemIdentifierForIndexPath:indexPath];
    if ([itemId isEqualToString:kTVNCEmptyItemId])
        return nil;

    __weak typeof(self) weakSelf = self;

    UIContextualAction *block = [UIContextualAction
        contextualActionWithStyle:UIContextualActionStyleDestructive
                            title:NSLocalizedStringFromTableInBundle(@"Block", @"Localizable", self.bundle, nil)
                          handler:^(__kindof UIContextualAction *action, __kindof UIView *sourceView,
                                    void (^completionHandler)(BOOL)) {
                              NSString *cid = [weakSelf.dataSource itemIdentifierForIndexPath:indexPath] ?: @"";
                              [weakSelf disconnectClientWithId:cid block:YES];
                              if (completionHandler)
                                  completionHandler(YES);
                          }];

    UIContextualAction *kick = [UIContextualAction
        contextualActionWithStyle:UIContextualActionStyleNormal
                            title:NSLocalizedStringFromTableInBundle(@"Disconnect", @"Localizable", self.bundle, nil)
                          handler:^(__kindof UIContextualAction *action, __kindof UIView *sourceView,
                                    void (^completionHandler)(BOOL)) {
                              NSString *cid = [weakSelf.dataSource itemIdentifierForIndexPath:indexPath] ?: @"";
                              [weakSelf disconnectClientWithId:cid block:NO];
                              if (completionHandler)
                                  completionHandler(YES);
                          }];

    UISwipeActionsConfiguration *config = [UISwipeActionsConfiguration configurationWithActions:@[ block, kick ]];
    config.performsFirstActionWithFullSwipe = NO;
    return config;
}

- (BOOL)tableView:(UITableView *)tableView canEditRowAtIndexPath:(NSIndexPath *)indexPath {
    NSString *itemId = [self.dataSource itemIdentifierForIndexPath:indexPath];
    if ([itemId isEqualToString:kTVNCEmptyItemId])
        return NO;
    return YES;
}

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
}

// iOS 14 min: Provide long-press context menu with copy actions
- (UIContextMenuConfiguration *)tableView:(UITableView *)tableView
    contextMenuConfigurationForRowAtIndexPath:(NSIndexPath *)indexPath
                                        point:(CGPoint)point {
    NSString *cid = [self.dataSource itemIdentifierForIndexPath:indexPath];
    if ([cid isEqualToString:kTVNCEmptyItemId])
        return nil;
    if (cid.length == 0)
        return nil;

    NSString *host = self.clientLookup[cid][@"host"] ?: @"";
    return [UIContextMenuConfiguration
        configurationWithIdentifier:nil
                    previewProvider:nil
                     actionProvider:^UIMenu *_Nullable(NSArray<UIMenuElement *> *_Nonnull suggestedActions) {
                         UIAction *copyId = [UIAction
                             actionWithTitle:NSLocalizedStringFromTableInBundle(@"Copy ID", @"Localizable", self.bundle,
                                                                                nil)
                                       image:[UIImage systemImageNamed:@"doc.on.doc"]
                                  identifier:nil
                                     handler:^(__kindof UIAction *_Nonnull action) {
                                         [UIPasteboard generalPasteboard].string = cid;
                                         UINotificationFeedbackGenerator *gen = [UINotificationFeedbackGenerator new];
                                         [gen notificationOccurred:UINotificationFeedbackTypeSuccess];
                                     }];
                         UIAction *copyHost = [UIAction
                             actionWithTitle:NSLocalizedStringFromTableInBundle(@"Copy Host", @"Localizable",
                                                                                self.bundle, nil)
                                       image:[UIImage systemImageNamed:@"globe"]
                                  identifier:nil
                                     handler:^(__kindof UIAction *_Nonnull action) {
                                         [UIPasteboard generalPasteboard].string = host;
                                         UINotificationFeedbackGenerator *gen = [UINotificationFeedbackGenerator new];
                                         [gen notificationOccurred:UINotificationFeedbackTypeSuccess];
                                     }];
                         UIAction *disconnect = [UIAction
                             actionWithTitle:NSLocalizedStringFromTableInBundle(@"Disconnect Client", @"Localizable",
                                                                                self.bundle, nil)
                                       image:[UIImage systemImageNamed:@"xmark.circle"]
                                  identifier:nil
                                     handler:^(__kindof UIAction *_Nonnull action) {
                                         [self disconnectClientWithId:cid block:NO];
                                     }];
                         disconnect.attributes = UIMenuElementAttributesDestructive;

                         UIAction *block = [UIAction
                             actionWithTitle:NSLocalizedStringFromTableInBundle(@"Block Client", @"Localizable",
                                                                                self.bundle, nil)
                                       image:[UIImage systemImageNamed:@"hand.raised.fill"]
                                  identifier:nil
                                     handler:^(__kindof UIAction *_Nonnull action) {
                                         [self disconnectClientWithId:cid block:YES];
                                     }];
                         block.attributes = UIMenuElementAttributesDestructive;

                         return [UIMenu menuWithTitle:@"" children:@[ copyId, copyHost, disconnect, block ]];
                     }];
}

@end

#pragma mark - App Data Reset

// Gửi một lệnh control (loopback) và trả về nguyên văn phần trả lời, hoặc nil nếu
// không nối được. Dùng chung TVNCConnect/TVNCSendLine/TVNCReadAll ở trên.
static NSString *TVNCRunCommand(NSString *line, double timeoutSec) {
    int fd = TVNCConnect();
    if (fd < 0)
        return nil;
    if (TVNCSendLine(fd, line) < 0) {
        close(fd);
        return nil;
    }
    NSData *data = TVNCReadAll(fd, timeoutSec);
    close(fd);
    return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
}

// Màn danh sách snapshot của một app: chọn bản để khôi phục / xoá, lưu bản mới,
// hoặc xoá dữ liệu app. Đẩy ra từ TVNCAppDataController khi chạm một app.
@interface TVNCSnapshotListController : UITableViewController
@property(nonatomic, copy) NSString *appBundle;
@property(nonatomic, copy) NSString *appName;
@property(nonatomic, strong) UIColor *primaryColor;
@property(nonatomic, strong) UINotificationFeedbackGenerator *notificationGenerator;
@end

@interface TVNCAppDataController ()
@property(nonatomic, strong) NSArray<NSDictionary *> *apps; // {bundle,name}
@end

@implementation TVNCAppDataController

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"App Data";
    self.apps = @[];

    self.navigationItem.rightBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemClose
                                                      target:self
                                                      action:@selector(dismissSelf)];

    UIRefreshControl *rc = [UIRefreshControl new];
    [rc addTarget:self action:@selector(reload) forControlEvents:UIControlEventValueChanged];
    self.refreshControl = rc;

    [self reload];
}

- (void)dismissSelf {
    [self dismissViewControllerAnimated:YES completion:nil];
}

#pragma mark - Load app list

- (void)reload {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *tsv = TVNCRunCommand(@"apps", 4.0);
        NSMutableArray<NSDictionary *> *rows = [NSMutableArray array];
        // `apps` trả về bundle\tname\ttype\tver mỗi dòng, KHÔNG có header. Chỉ lấy
        // app người dùng (type == User).
        for (NSString *ln in [tsv componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
            if (ln.length == 0)
                continue;
            NSArray<NSString *> *cols = [ln componentsSeparatedByString:@"\t"];
            if (cols.count < 3)
                continue;
            if (![cols[2] isEqualToString:@"User"])
                continue;
            NSString *name = cols[1].length ? cols[1] : cols[0];
            [rows addObject:@{@"bundle" : cols[0], @"name" : name}];
        }
        [rows sortUsingComparator:^NSComparisonResult(NSDictionary *a, NSDictionary *b) {
            return [a[@"name"] localizedCaseInsensitiveCompare:b[@"name"]];
        }];
        dispatch_async(dispatch_get_main_queue(), ^{
            self.apps = rows;
            [self.refreshControl endRefreshing];
            [self.tableView reloadData];
        });
    });
}

#pragma mark - Table

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    return self.apps.count ?: 1; // 1 dòng placeholder khi rỗng
}

- (NSString *)tableView:(UITableView *)tableView titleForHeaderInSection:(NSInteger)section {
    return @"Chọn app để reset dữ liệu (chỉ máy này)";
}

- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {
    return @"Wipe = xoá dữ liệu như cài lại (giữ keychain). Snapshot lưu bản hiện "
           @"tại trên máy; Restore quay về bản đã lưu. App sẽ được đóng trước.";
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    static NSString *const kReuse = @"TVNCAppDataCell";
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:kReuse];
    if (!cell)
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:kReuse];

    if (self.apps.count == 0) {
        cell.textLabel.text = @"Không lấy được danh sách app";
        cell.detailTextLabel.text = @"Kéo xuống để thử lại — cần TrollVNC đã vá";
        cell.textLabel.textColor = [UIColor secondaryLabelColor];
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        cell.accessoryType = UITableViewCellAccessoryNone;
        return cell;
    }

    NSDictionary *app = self.apps[indexPath.row];
    cell.textLabel.text = app[@"name"];
    cell.textLabel.textColor = [UIColor labelColor];
    cell.detailTextLabel.text = app[@"bundle"];
    cell.selectionStyle = UITableViewCellSelectionStyleDefault;
    cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    return cell;
}

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
    if (self.apps.count == 0)
        return;

    NSDictionary *app = self.apps[indexPath.row];
    TVNCSnapshotListController *vc =
        [[TVNCSnapshotListController alloc] initWithStyle:UITableViewStyleInsetGrouped];
    vc.appBundle = app[@"bundle"];
    vc.appName = app[@"name"];
    vc.primaryColor = self.primaryColor;
    vc.notificationGenerator = self.notificationGenerator;
    [self.navigationController pushViewController:vc animated:YES];
}

@end

#pragma mark - Snapshot List

@interface TVNCSnapshotListController ()
@property(nonatomic, strong) NSArray<NSDictionary *> *snaps; // {name,epoch,size}
@end

@implementation TVNCSnapshotListController

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = self.appName ?: self.appBundle;
    self.snaps = @[];

    UIRefreshControl *rc = [UIRefreshControl new];
    [rc addTarget:self action:@selector(reload) forControlEvents:UIControlEventValueChanged];
    self.refreshControl = rc;

    [self reload];
}

- (void)reload {
    NSString *bundle = self.appBundle;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *tsv = TVNCRunCommand([NSString stringWithFormat:@"snaplist %@", bundle], 5.0);
        NSMutableArray<NSDictionary *> *rows = [NSMutableArray array];
        for (NSString *ln in [tsv componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]]) {
            if (ln.length == 0)
                continue;
            NSArray<NSString *> *cols = [ln componentsSeparatedByString:@"\t"];
            if (cols.count < 1 || cols[0].length == 0)
                continue;
            [rows addObject:@{
                @"name" : cols[0],
                @"epoch" : @(cols.count > 1 ? [cols[1] longLongValue] : 0),
                @"size" : @(cols.count > 2 ? [cols[2] longLongValue] : 0),
            }];
        }
        // Mới nhất lên đầu.
        [rows sortUsingComparator:^NSComparisonResult(NSDictionary *a, NSDictionary *b) {
            return [b[@"epoch"] compare:a[@"epoch"]];
        }];
        dispatch_async(dispatch_get_main_queue(), ^{
            self.snaps = rows;
            [self.refreshControl endRefreshing];
            [self.tableView reloadData];
        });
    });
}

#pragma mark - Table

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {
    return 2; // 0: thao tác, 1: danh sách snapshot
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    if (section == 0)
        return 3; // Lưu bản mới · Xoá dữ liệu app · Xoá tất cả snapshot
    return self.snaps.count ?: 1;
}

- (NSString *)tableView:(UITableView *)tableView titleForHeaderInSection:(NSInteger)section {
    return section == 0 ? @"Thao tác" : @"Bản snapshot (chạm để khôi phục / xoá)";
}

- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {
    if (section == 0)
        return @"Wipe = xoá dữ liệu như cài lại (giữ keychain). App sẽ được đóng trước.";
    return nil;
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    if (indexPath.section == 0) {
        static NSString *const kA = @"TVNCSnapActionCell";
        UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:kA];
        if (!cell)
            cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:kA];
        if (indexPath.row == 0) {
            cell.textLabel.text = @"Lưu snapshot mới…";
            cell.textLabel.textColor = self.primaryColor ?: [UIColor labelColor];
        } else if (indexPath.row == 1) {
            cell.textLabel.text = @"Xoá dữ liệu app (như cài lại)";
            cell.textLabel.textColor = [UIColor systemRedColor];
        } else {
            cell.textLabel.text = @"Xoá tất cả snapshot";
            cell.textLabel.textColor = [UIColor systemRedColor];
        }
        cell.accessoryType = UITableViewCellAccessoryNone;
        return cell;
    }

    static NSString *const kS = @"TVNCSnapCell";
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:kS];
    if (!cell)
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:kS];

    if (self.snaps.count == 0) {
        cell.textLabel.text = @"Chưa có bản snapshot nào";
        cell.textLabel.textColor = [UIColor secondaryLabelColor];
        cell.detailTextLabel.text = nil;
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
        cell.accessoryType = UITableViewCellAccessoryNone;
        return cell;
    }

    NSDictionary *s = self.snaps[indexPath.row];
    cell.textLabel.text = s[@"name"];
    cell.textLabel.textColor = [UIColor labelColor];

    static NSDateFormatter *fmt;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        fmt = [NSDateFormatter new];
        fmt.dateFormat = @"dd/MM HH:mm";
    });
    long long epoch = [s[@"epoch"] longLongValue];
    NSString *when = epoch > 0 ? [fmt stringFromDate:[NSDate dateWithTimeIntervalSince1970:epoch]] : @"—";
    double mb = [s[@"size"] longLongValue] / (1024.0 * 1024.0);
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@ · %.1f MB", when, mb];
    cell.selectionStyle = UITableViewCellSelectionStyleDefault;
    cell.accessoryType = UITableViewCellAccessoryNone;
    return cell;
}

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    [tableView deselectRowAtIndexPath:indexPath animated:YES];

    if (indexPath.section == 0) {
        if (indexPath.row == 0)
            [self promptSaveSnapshot];
        else if (indexPath.row == 1)
            [self confirmWipe];
        else
            [self confirmClearAll];
        return;
    }

    if (self.snaps.count == 0)
        return;
    NSDictionary *s = self.snaps[indexPath.row];
    NSString *name = s[@"name"];

    UIAlertController *sheet = [UIAlertController alertControllerWithTitle:name
                                                                  message:nil
                                                           preferredStyle:UIAlertControllerStyleActionSheet];
    [sheet addAction:[UIAlertAction actionWithTitle:@"Khôi phục về bản này"
                                              style:UIAlertActionStyleDestructive
                                            handler:^(UIAlertAction *a) {
                                                [self confirmRestore:name];
                                            }]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"Xoá bản này"
                                              style:UIAlertActionStyleDestructive
                                            handler:^(UIAlertAction *a) {
                                                [self runControl:[NSString stringWithFormat:@"snapdel %@ %@",
                                                                                            self.appBundle, name]
                                                  terminateFirst:NO
                                                            verb:@"Xoá snapshot"
                                                     reloadAfter:YES];
                                            }]];
    [sheet addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];

    UITableViewCell *cell = [tableView cellForRowAtIndexPath:indexPath];
    sheet.popoverPresentationController.sourceView = cell;
    sheet.popoverPresentationController.sourceRect = cell.bounds;
    [self presentViewController:sheet animated:YES completion:nil];
}

#pragma mark - Ops

- (void)promptSaveSnapshot {
    UIAlertController *alert =
        [UIAlertController alertControllerWithTitle:@"Lưu snapshot mới"
                                            message:@"Đặt tên (để trống = tự đặt theo giờ). Không dùng '/'."
                                     preferredStyle:UIAlertControllerStyleAlert];
    [alert addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.placeholder = @"tên bản (tuỳ chọn)";
        tf.autocapitalizationType = UITextAutocapitalizationTypeNone;
    }];
    [alert addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Lưu"
                                              style:UIAlertActionStyleDefault
                                            handler:^(UIAlertAction *a) {
                                                NSString *nm = [alert.textFields.firstObject.text
                                                    stringByTrimmingCharactersInSet:[NSCharacterSet
                                                                                        whitespaceCharacterSet]];
                                                NSString *cmd = nm.length
                                                    ? [NSString stringWithFormat:@"snapshot %@ %@", self.appBundle, nm]
                                                    : [NSString stringWithFormat:@"snapshot %@", self.appBundle];
                                                [self runControl:cmd terminateFirst:YES verb:@"Lưu snapshot"
                                                     reloadAfter:YES];
                                            }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)confirmWipe {
    UIAlertController *alert = [UIAlertController
        alertControllerWithTitle:@"Xoá dữ liệu app"
                         message:@"Xoá sạch dữ liệu app này (như cài lại)? Không hoàn tác trừ khi đã có snapshot."
                  preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Xoá"
                                              style:UIAlertActionStyleDestructive
                                            handler:^(UIAlertAction *a) {
                                                [self runControl:[NSString stringWithFormat:@"wipeapp %@",
                                                                                            self.appBundle]
                                                  terminateFirst:YES
                                                            verb:@"Xoá dữ liệu"
                                                     reloadAfter:NO];
                                            }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)confirmClearAll {
    UIAlertController *alert = [UIAlertController
        alertControllerWithTitle:@"Xoá tất cả snapshot"
                         message:@"Xoá tất cả bản snapshot của app này? (Dọn luôn dữ liệu sót của bản cũ.)"
                  preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Xoá tất cả"
                                              style:UIAlertActionStyleDestructive
                                            handler:^(UIAlertAction *a) {
                                                [self runControl:[NSString stringWithFormat:@"snapclear %@",
                                                                                            self.appBundle]
                                                  terminateFirst:NO
                                                            verb:@"Xoá tất cả snapshot"
                                                     reloadAfter:YES];
                                            }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)confirmRestore:(NSString *)name {
    UIAlertController *alert = [UIAlertController
        alertControllerWithTitle:@"Khôi phục"
                         message:[NSString stringWithFormat:@"Thay dữ liệu hiện tại bằng bản “%@”? "
                                                            @"Dữ liệu hiện tại sẽ mất.",
                                                            name]
                  preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Khôi phục"
                                              style:UIAlertActionStyleDestructive
                                            handler:^(UIAlertAction *a) {
                                                [self runControl:[NSString stringWithFormat:@"restore %@ %@",
                                                                                            self.appBundle, name]
                                                  terminateFirst:YES
                                                            verb:@"Khôi phục"
                                                     reloadAfter:NO];
                                            }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)runControl:(NSString *)command
    terminateFirst:(BOOL)term
              verb:(NSString *)verb
       reloadAfter:(BOOL)reloadAfter {
    NSString *bundle = self.appBundle;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        if (term) // đóng app trước để file được ghi/nhả (giống bên PC)
            (void)TVNCRunCommand([NSString stringWithFormat:@"terminate %@", bundle], 4.0);
        // App lớn (Shopee ~700MB) chép lâu; daemon chỉ trả lời sau khi xong.
        NSString *reply = TVNCRunCommand(command, 180.0);
        NSString *head = [reply stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        BOOL ok = [head hasPrefix:@"OK"];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.notificationGenerator)
                [self.notificationGenerator notificationOccurred:ok ? UINotificationFeedbackTypeSuccess
                                                                    : UINotificationFeedbackTypeError];
            if (reloadAfter)
                [self reload];
            NSString *msg = ok ? [NSString stringWithFormat:@"%@: xong.", verb]
                               : [NSString stringWithFormat:@"%@ thất bại: %@", verb,
                                                            head.length ? head : @"máy không trả lời"];
            UIAlertController *done = [UIAlertController alertControllerWithTitle:(ok ? @"Xong" : @"Lỗi")
                                                                         message:msg
                                                                  preferredStyle:UIAlertControllerStyleAlert];
            [done addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
            [self presentViewController:done animated:YES completion:nil];
        });
    });
}

@end

#pragma mark - Activation (kích hoạt bản quyền)

static NSString *const kTVNCLicensePath = @"/var/mobile/Library/controlios/license.dat";

@interface TVNCActivationController ()
@property(nonatomic, copy) NSString *udid;
@property(nonatomic, copy) NSString *statusLine;
@end

@implementation TVNCActivationController

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"Kích hoạt";
    self.udid = @"";
    self.statusLine = @"Đang kiểm tra…";
    UIRefreshControl *rc = [UIRefreshControl new];
    [rc addTarget:self action:@selector(refresh) forControlEvents:UIControlEventValueChanged];
    self.refreshControl = rc;
    [self refresh];
}

// Hỏi daemon: "OK valid exp=<epoch> udid=<udid>" hoặc "OK invalid udid=<udid>".
- (void)refresh {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *reply = TVNCRunCommand(@"license", 4.0) ?: @"";
        NSString *udid = @"";
        NSString *status = @"Không đọc được trạng thái (daemon chưa chạy?)";
        for (NSString *field in [reply componentsSeparatedByCharactersInSet:
                                            [NSCharacterSet whitespaceAndNewlineCharacterSet]]) {
            if ([field hasPrefix:@"udid="])
                udid = [field substringFromIndex:5];
        }
        BOOL activated = [reply containsString:@"valid"] && ![reply containsString:@"invalid"];
        if (activated) {
            long long exp = 0;
            for (NSString *field in [reply componentsSeparatedByString:@" "])
                if ([field hasPrefix:@"exp="])
                    exp = [[field substringFromIndex:4] longLongValue];
            if (exp == 0) {
                status = @"✓ Đã kích hoạt — vĩnh viễn";
            } else {
                NSDateFormatter *f = [NSDateFormatter new];
                f.dateFormat = @"dd/MM/yyyy";
                status = [NSString stringWithFormat:@"✓ Đã kích hoạt — hạn %@",
                          [f stringFromDate:[NSDate dateWithTimeIntervalSince1970:exp]]];
            }
        } else if ([reply containsString:@"invalid"]) {
            status = @"✗ Chưa kích hoạt";
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            self.udid = udid;
            self.statusLine = status;
            [self.refreshControl endRefreshing];
            [self.tableView reloadData];
        });
    });
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView {
    return 3; // UDID · trạng thái · kích hoạt
}

- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section {
    return 1;
}

- (NSString *)tableView:(UITableView *)tableView titleForHeaderInSection:(NSInteger)section {
    if (section == 0)
        return @"UDID máy này (gửi cho người bán để lấy key)";
    if (section == 1)
        return @"Trạng thái";
    return @"Kích hoạt";
}

- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)indexPath {
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:@"c"];
    if (!cell)
        cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleDefault reuseIdentifier:@"c"];
    cell.textLabel.numberOfLines = 0;
    cell.accessoryType = UITableViewCellAccessoryNone;
    cell.selectionStyle = UITableViewCellSelectionStyleDefault;
    if (indexPath.section == 0) {
        cell.textLabel.text = self.udid.length ? self.udid : @"(không đọc được)";
        cell.textLabel.textColor = [UIColor labelColor];
    } else if (indexPath.section == 1) {
        cell.textLabel.text = self.statusLine;
        cell.textLabel.textColor =
            [self.statusLine hasPrefix:@"✓"] ? [UIColor systemGreenColor] : [UIColor systemOrangeColor];
        cell.selectionStyle = UITableViewCellSelectionStyleNone;
    } else {
        cell.textLabel.text = @"Dán license & kích hoạt…";
        cell.textLabel.textColor = self.primaryColor ?: [UIColor labelColor];
    }
    return cell;
}

- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)indexPath {
    [tableView deselectRowAtIndexPath:indexPath animated:YES];
    if (indexPath.section == 0 && self.udid.length) {
        [UIPasteboard generalPasteboard].string = self.udid;
        [self alert:@"Đã chép UDID" message:self.udid];
    } else if (indexPath.section == 2) {
        [self promptActivate];
    }
}

- (void)promptActivate {
    UIAlertController *alert =
        [UIAlertController alertControllerWithTitle:@"Kích hoạt"
                                            message:@"Dán chuỗi license người bán cấp:"
                                     preferredStyle:UIAlertControllerStyleAlert];
    [alert addTextFieldWithConfigurationHandler:^(UITextField *tf) {
        tf.placeholder = @"license…";
        tf.autocapitalizationType = UITextAutocapitalizationTypeNone;
        tf.autocorrectionType = UITextAutocorrectionTypeNo;
    }];
    [alert addAction:[UIAlertAction actionWithTitle:@"Huỷ" style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Kích hoạt"
                                              style:UIAlertActionStyleDefault
                                            handler:^(UIAlertAction *a) {
        NSString *lic = [alert.textFields.firstObject.text
            stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        [self activateWith:lic];
    }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)activateWith:(NSString *)license {
    if (license.length == 0)
        return;
    NSFileManager *fm = [NSFileManager defaultManager];
    [fm createDirectoryAtPath:[kTVNCLicensePath stringByDeletingLastPathComponent]
  withIntermediateDirectories:YES
                   attributes:nil
                        error:NULL];
    NSError *werr = nil;
    BOOL wrote = [license writeToFile:kTVNCLicensePath
                          atomically:YES
                            encoding:NSUTF8StringEncoding
                               error:&werr];
    if (!wrote) {
        [self alert:@"Lỗi"
            message:[NSString stringWithFormat:@"Không ghi được file license: %@",
                                               werr.localizedDescription ?: @"?"]];
        return;
    }
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *reply = TVNCRunCommand(@"relicense", 5.0) ?: @"";
        BOOL ok = [reply containsString:@"valid"] && ![reply containsString:@"invalid"];
        dispatch_async(dispatch_get_main_queue(), ^{
            [self refresh];
            [self alert:(ok ? @"Thành công" : @"Chưa kích hoạt được")
                message:(ok ? @"License hợp lệ. Nếu đang mở phiên VNC, respring cho chắc."
                            : @"Không hợp lệ / sai UDID / hết hạn. Kiểm tra lại key.")];
        });
    });
}

- (void)alert:(NSString *)title message:(NSString *)message {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:title
                                                              message:message
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:a animated:YES completion:nil];
}

@end

#pragma mark - Auto-click (kịch bản tự chạy)

@interface TVNCAutoClickController ()
@property(nonatomic, strong) UITextView *editor;
@property(nonatomic, strong) UILabel *status;
@end

@implementation TVNCAutoClickController

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"Tự động chạm";
    self.view.backgroundColor = [UIColor systemBackgroundColor];

    self.navigationItem.leftBarButtonItem =
        [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemClose
                                                      target:self
                                                      action:@selector(dismissSelf)];
    UIBarButtonItem *start = [[UIBarButtonItem alloc] initWithTitle:@"▶ Bắt đầu"
                                                             style:UIBarButtonItemStyleDone
                                                            target:self
                                                            action:@selector(saveAndStart)];
    UIBarButtonItem *stop = [[UIBarButtonItem alloc] initWithTitle:@"■ Dừng"
                                                            style:UIBarButtonItemStylePlain
                                                           target:self
                                                           action:@selector(stop)];
    self.navigationItem.rightBarButtonItems = @[ start, stop ];

    UILabel *hint = [UILabel new];
    hint.numberOfLines = 0;
    hint.font = [UIFont systemFontOfSize:12];
    hint.textColor = [UIColor secondaryLabelColor];
    hint.text = @"JavaScript (như AutoTouch). Toạ độ TỈ LỆ 0..1. Hàm: tap(x,y) · "
                @"tapRegion(x1,y1,x2,y2) · doubleTap · twoFingerTap · threeFingerTap · "
                @"longPress(x,y,giây) · swipe(x1,y1,x2,y2,giây) · home() · key('a') · "
                @"typeText('...') · sleep(giây) · random(a,b) · getColor(x,y)->'RRGGBB' · "
                @"matchColor(x,y,'RRGGBB',sai)->bool · waitColor(x,y,'RRGGBB',giây,sai) · "
                @"findImage('/đường/dẫn.png'[,x1,y1,x2,y2][,sai])->{x,y}|null · "
                @"assistiveTouch(true/false) · stop() · log('...'). Dùng while/for/if của JS. "
                @"Mẹo: lấy toạ độ trên khung VNC ở PC (góc dưới hiện tỉ lệ).";
    hint.translatesAutoresizingMaskIntoConstraints = NO;

    self.editor = [UITextView new];
    self.editor.font = [UIFont fontWithName:@"Menlo" size:14] ?: [UIFont systemFontOfSize:14];
    self.editor.autocapitalizationType = UITextAutocapitalizationTypeNone;
    self.editor.autocorrectionType = UITextAutocorrectionTypeNo;
    self.editor.backgroundColor = [UIColor secondarySystemBackgroundColor];
    self.editor.translatesAutoresizingMaskIntoConstraints = NO;

    self.status = [UILabel new];
    self.status.font = [UIFont systemFontOfSize:13];
    self.status.textColor = [UIColor secondaryLabelColor];
    self.status.text = @"…";
    self.status.translatesAutoresizingMaskIntoConstraints = NO;

    [self.view addSubview:hint];
    [self.view addSubview:self.editor];
    [self.view addSubview:self.status];
    UILayoutGuide *g = self.view.safeAreaLayoutGuide;
    [NSLayoutConstraint activateConstraints:@[
        [hint.topAnchor constraintEqualToAnchor:g.topAnchor constant:8],
        [hint.leadingAnchor constraintEqualToAnchor:g.leadingAnchor constant:12],
        [hint.trailingAnchor constraintEqualToAnchor:g.trailingAnchor constant:-12],
        [self.editor.topAnchor constraintEqualToAnchor:hint.bottomAnchor constant:8],
        [self.editor.leadingAnchor constraintEqualToAnchor:g.leadingAnchor constant:8],
        [self.editor.trailingAnchor constraintEqualToAnchor:g.trailingAnchor constant:-8],
        [self.status.topAnchor constraintEqualToAnchor:self.editor.bottomAnchor constant:6],
        [self.status.leadingAnchor constraintEqualToAnchor:g.leadingAnchor constant:12],
        [self.status.trailingAnchor constraintEqualToAnchor:g.trailingAnchor constant:-12],
        [self.status.bottomAnchor constraintEqualToAnchor:g.bottomAnchor constant:-8],
    ]];

    [self loadScript];
    [self refreshStatus];
}

- (void)dismissSelf {
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (void)loadScript {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *reply = TVNCRunCommand(@"autoget", 4.0) ?: @"";
        NSString *script = @"";
        NSRange sp = [reply rangeOfString:@" "];
        if ([reply hasPrefix:@"OK"] && sp.location != NSNotFound) {
            NSString *b64 = [[reply substringFromIndex:sp.location + 1]
                stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
            NSData *d = [[NSData alloc] initWithBase64EncodedString:b64 options:0];
            if (d)
                script = [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] ?: @"";
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.editor.text.length == 0)
                self.editor.text = script.length ? script
                    : @"// Ví dụ: chạm giữa-dưới mỗi 1–3 giây, lặp mãi\n"
                      @"while (true) {\n  tap(0.5, 0.9);\n  sleep(random(1, 3));\n}\n";
        });
    });
}

- (void)refreshStatus {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *reply = TVNCRunCommand(@"autostatus", 4.0) ?: @"";
        BOOL running = [reply containsString:@"running"];
        dispatch_async(dispatch_get_main_queue(), ^{
            self.status.text = running ? @"● Đang chạy" : @"○ Đã dừng";
            self.status.textColor = running ? [UIColor systemGreenColor] : [UIColor secondaryLabelColor];
        });
    });
}

- (void)saveAndStart {
    NSString *script = self.editor.text ?: @"";
    NSString *b64 = [[script dataUsingEncoding:NSUTF8StringEncoding] base64EncodedStringWithOptions:0];
    [self.editor endEditing:YES];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        (void)TVNCRunCommand([NSString stringWithFormat:@"autoset %@", b64], 5.0);
        NSString *r = TVNCRunCommand(@"autostart", 5.0) ?: @"";
        dispatch_async(dispatch_get_main_queue(), ^{
            [self refreshStatus];
            if ([r containsString:@"NoScript"])
                [self toast:@"Kịch bản trống"];
        });
    });
}

- (void)stop {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        (void)TVNCRunCommand(@"autostop", 4.0);
        dispatch_async(dispatch_get_main_queue(), ^{
            [self refreshStatus];
        });
    });
}

- (void)toast:(NSString *)t {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:t
                                                              message:nil
                                                       preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:a animated:YES completion:nil];
}

@end
