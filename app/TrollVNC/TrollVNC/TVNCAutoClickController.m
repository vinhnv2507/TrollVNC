#import "TVNCClientListController.h"
#import <JavaScriptCore/JavaScriptCore.h>

static NSString *const kJSDirectory = @"/var/mobile/Media/ControlIOS/AutoClickJS";
static NSString *const kJSCurrentFile = @"/var/mobile/Media/ControlIOS/AutoClickJS/current.js";

@interface TVNCJSLineGutter : UIView
@property(nonatomic, weak) UITextView *editor;
@property(nonatomic, copy) NSArray<NSNumber *> *starts;
@end

@implementation TVNCJSLineGutter
- (void)drawRect:(CGRect)rect {
    UITextView *editor = self.editor;
    if (!editor || !self.starts.count) return;
    NSDictionary *attributes = @{NSFontAttributeName: [UIFont monospacedDigitSystemFontOfSize:11 weight:UIFontWeightRegular],
                                 NSForegroundColorAttributeName: UIColor.secondaryLabelColor};
    NSLayoutManager *layout = editor.layoutManager;
    CGRect visible = CGRectMake(0, editor.contentOffset.y - editor.textContainerInset.top,
                                editor.bounds.size.width, editor.bounds.size.height);
    NSRange glyphs = [layout glyphRangeForBoundingRect:visible inTextContainer:editor.textContainer];
    [layout enumerateLineFragmentsForGlyphRange:glyphs usingBlock:
        ^(CGRect lineRect, CGRect used, NSTextContainer *container, NSRange range, BOOL *stop) {
        NSUInteger character = [layout characterIndexForGlyphAtIndex:range.location];
        NSUInteger low = 0, high = self.starts.count;
        while (low < high) {
            NSUInteger middle = (low + high) / 2;
            if (self.starts[middle].unsignedIntegerValue < character) low = middle + 1;
            else high = middle;
        }
        if (low < self.starts.count && self.starts[low].unsignedIntegerValue == character) {
            NSString *number = [NSString stringWithFormat:@"%lu", (unsigned long)low + 1];
            CGFloat y = lineRect.origin.y + editor.textContainerInset.top - editor.contentOffset.y + 2;
            [number drawAtPoint:CGPointMake(MAX(1, self.bounds.size.width - 5 - [number sizeWithAttributes:attributes].width), y)
                withAttributes:attributes];
        }
    }];
}
@end

@interface TVNCAutoClickController () <UITextViewDelegate, UITableViewDataSource, UITableViewDelegate,
                                        UISearchBarDelegate, UIDocumentPickerDelegate>
@property(nonatomic, strong) UITextView *editor;
@property(nonatomic, strong) UITextView *logView;
@property(nonatomic, strong) TVNCJSLineGutter *gutter;
@property(nonatomic, strong) UILabel *status;
@property(nonatomic, strong) UILabel *position;
@property(nonatomic, strong) UIButton *nameButton;
@property(nonatomic, strong) UIButton *runButton;
@property(nonatomic, strong) UIButton *stopButton;
@property(nonatomic, strong) UIButton *logButton;
@property(nonatomic, strong) UISegmentedControl *tabs;
@property(nonatomic, strong) UIView *codePane;
@property(nonatomic, strong) UIView *browserPane;
@property(nonatomic, strong) UISearchBar *search;
@property(nonatomic, strong) UITableView *table;
@property(nonatomic, strong) NSLayoutConstraint *logHeight;
@property(nonatomic, strong) NSLayoutConstraint *bottomConstraint;
@property(nonatomic, strong) NSArray<NSLayoutConstraint *> *headerHeights;
@property(nonatomic, strong) NSLayoutConstraint *positionHeight;
@property(nonatomic, strong) NSLayoutConstraint *logHeaderHeight;
@property(nonatomic, strong) UIStackView *logHeader;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, strong) NSDictionary *catalog;
@property(nonatomic, copy) NSArray<NSDictionary *> *savedScripts;
@property(nonatomic, copy) NSArray<NSDictionary *> *sections;
@property(nonatomic, copy) NSArray<NSNumber *> *lineStarts;
@property(nonatomic, copy) NSString *scriptID;
@property(nonatomic, copy) NSString *scriptName;
@property(nonatomic, copy) NSString *lastLog;
@property(nonatomic, copy) NSDictionary *reference;
@property(nonatomic) NSUInteger revision;
@property(nonatomic) NSUInteger controlEpoch;
@property(nonatomic) BOOL dirty;
@property(nonatomic) BOOL busy;
@property(nonatomic) BOOL polling;
@property(nonatomic) BOOL running;
@property(nonatomic) BOOL connected;
@property(nonatomic) CGFloat keyboardOverlap;
@end

@implementation TVNCAutoClickController

- (UIButton *)button:(NSString *)title icon:(NSString *)icon action:(SEL)action {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    [button setTitle:title forState:UIControlStateNormal];
    [button setImage:[UIImage systemImageNamed:icon] forState:UIControlStateNormal];
    button.titleLabel.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
    button.contentEdgeInsets = UIEdgeInsetsMake(8, 8, 8, 8);
    button.accessibilityLabel = title;
    [button addTarget:self action:action forControlEvents:UIControlEventTouchUpInside];
    return button;
}

- (UILabel *)label:(CGFloat)size {
    UILabel *label = [UILabel new];
    label.font = [UIFont systemFontOfSize:size];
    label.textColor = UIColor.secondaryLabelColor;
    label.numberOfLines = 1;
    label.adjustsFontSizeToFitWidth = YES;
    label.minimumScaleFactor = 0.75;
    return label;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"AutoClickJS";
    self.view.backgroundColor = UIColor.systemBackgroundColor;
    self.navigationItem.leftBarButtonItem = [[UIBarButtonItem alloc]
        initWithBarButtonSystemItem:UIBarButtonSystemItemClose target:self action:@selector(dismissSelf)];
    UIBarButtonItem *files = [[UIBarButtonItem alloc] initWithTitle:@"Tệp" style:UIBarButtonItemStylePlain target:nil action:nil];
    files.menu = [self fileMenu];
    self.navigationItem.rightBarButtonItem = files;
    NSURL *catalogURL = [[NSBundle bundleForClass:self.class] URLForResource:@"AutoClickJS" withExtension:@"json"];
    NSData *data = catalogURL ? [NSData dataWithContentsOfURL:catalogURL] : nil;
    id catalog = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
    self.catalog = [catalog isKindOfClass:NSDictionary.class] ? catalog : @{};
    self.scriptName = @"Script mới";
    self.lastLog = @"";

    self.status = [self label:12];
    self.status.text = @"Đang hỏi trạng thái dịch vụ…";
    self.nameButton = [self button:self.scriptName icon:@"doc.text" action:@selector(saveLibrary)];
    self.nameButton.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    self.nameButton.titleLabel.lineBreakMode = NSLineBreakByTruncatingMiddle;
    self.tabs = [[UISegmentedControl alloc] initWithItems:@[@"Code", @"Thư viện", @"Lệnh", @"Hỗ trợ"]];
    self.tabs.selectedSegmentIndex = 0;
    [self.tabs addTarget:self action:@selector(changeTab) forControlEvents:UIControlEventValueChanged];

    self.codePane = [UIView new];
    self.codePane.backgroundColor = UIColor.secondarySystemBackgroundColor;
    self.codePane.layer.cornerRadius = 12;
    self.codePane.clipsToBounds = YES;
    self.editor = [UITextView new];
    self.editor.delegate = self;
    self.editor.font = [UIFont monospacedSystemFontOfSize:14 weight:UIFontWeightRegular];
    self.editor.backgroundColor = UIColor.clearColor;
    self.editor.textColor = UIColor.labelColor;
    self.editor.textContainerInset = UIEdgeInsetsMake(10, 0, 10, 8);
    self.editor.autocapitalizationType = UITextAutocapitalizationTypeNone;
    self.editor.autocorrectionType = UITextAutocorrectionTypeNo;
    self.editor.spellCheckingType = UITextSpellCheckingTypeNo;
    self.editor.smartQuotesType = UITextSmartQuotesTypeNo;
    self.editor.smartDashesType = UITextSmartDashesTypeNo;
    self.editor.smartInsertDeleteType = UITextSmartInsertDeleteTypeNo;
    self.editor.accessibilityLabel = @"Code JavaScript";
    self.editor.keyboardDismissMode = UIScrollViewKeyboardDismissModeInteractive;
    self.editor.inputAccessoryView = [self keyboardToolbar];
    self.gutter = [TVNCJSLineGutter new];
    self.gutter.editor = self.editor;
    self.gutter.backgroundColor = UIColor.tertiarySystemBackgroundColor;
    self.gutter.accessibilityElementsHidden = YES;
    [self.codePane addSubview:self.editor];
    [self.codePane addSubview:self.gutter];

    self.browserPane = [UIView new];
    self.search = [UISearchBar new];
    self.search.delegate = self;
    self.search.placeholder = @"Tìm script, lệnh hoặc hướng dẫn";
    self.search.searchBarStyle = UISearchBarStyleMinimal;
    self.table = [[UITableView alloc] initWithFrame:CGRectZero style:UITableViewStyleInsetGrouped];
    self.table.dataSource = self;
    self.table.delegate = self;
    self.table.rowHeight = UITableViewAutomaticDimension;
    self.table.estimatedRowHeight = 76;
    self.table.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
    [self.browserPane addSubview:self.search];
    [self.browserPane addSubview:self.table];
    self.position = [self label:11];
    self.logButton = [self button:@"Nhật ký ▾" icon:@"text.alignleft" action:@selector(toggleLog)];
    UIButton *copyLog = [self button:@"Chép" icon:@"doc.on.doc" action:@selector(copyLog)];
    UIButton *clearLog = [self button:@"Xóa" icon:@"trash" action:@selector(clearLog)];
    UIStackView *logHeader = [[UIStackView alloc] initWithArrangedSubviews:@[self.logButton, copyLog, clearLog]];
    self.logHeader = logHeader;
    logHeader.distribution = UIStackViewDistributionFill;
    [self.logButton setContentHuggingPriority:UILayoutPriorityDefaultLow forAxis:UILayoutConstraintAxisHorizontal];
    self.logButton.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    self.logView = [UITextView new];
    self.logView.editable = NO;
    self.logView.font = [UIFont monospacedSystemFontOfSize:11 weight:UIFontWeightRegular];
    self.logView.backgroundColor = UIColor.secondarySystemBackgroundColor;
    self.logView.layer.cornerRadius = 10;
    self.logView.text = @"Nhật ký sẽ xuất hiện khi script chạy trên iPhone.";
    self.logView.accessibilityLabel = @"Nhật ký chạy AutoClickJS";
    UIButton *save = [self button:@"Lưu" icon:@"square.and.arrow.down" action:@selector(saveLibrary)];
    UIButton *check = [self button:@"Kiểm tra" icon:@"checkmark.circle" action:@selector(checkSyntaxAction)];
    self.runButton = [self button:@"Chạy" icon:@"play.fill" action:@selector(saveAndStart)];
    self.stopButton = [self button:@"Dừng" icon:@"stop.fill" action:@selector(stopScript)];
    self.stopButton.tintColor = UIColor.systemRedColor;
    UIStackView *controls = [[UIStackView alloc] initWithArrangedSubviews:@[save, check, self.runButton, self.stopButton]];
    controls.distribution = UIStackViewDistributionFillEqually;
    controls.spacing = 4;

    NSArray *views = @[self.nameButton, self.status, self.tabs, self.codePane, self.browserPane,
                       self.position, logHeader, self.logView, controls];
    for (UIView *view in views) { view.translatesAutoresizingMaskIntoConstraints = NO; [self.view addSubview:view]; }
    for (UIView *view in @[self.editor, self.gutter, self.search, self.table]) view.translatesAutoresizingMaskIntoConstraints = NO;
    UILayoutGuide *safe = self.view.safeAreaLayoutGuide;
    self.logHeight = [self.logView.heightAnchor constraintEqualToConstant:0];
    self.bottomConstraint = [controls.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor constant:-8];
    self.headerHeights = @[[self.nameButton.heightAnchor constraintEqualToConstant:34], [self.status.heightAnchor constraintEqualToConstant:20]];
    self.positionHeight = [self.position.heightAnchor constraintEqualToConstant:18];
    self.logHeaderHeight = [logHeader.heightAnchor constraintEqualToConstant:32];
    [NSLayoutConstraint activateConstraints:@[
        [self.nameButton.topAnchor constraintEqualToAnchor:safe.topAnchor constant:2],
        [self.nameButton.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:10],
        [self.nameButton.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-10],
        self.headerHeights[0],
        [self.status.topAnchor constraintEqualToAnchor:self.nameButton.bottomAnchor],
        [self.status.leadingAnchor constraintEqualToAnchor:self.nameButton.leadingAnchor],
        [self.status.trailingAnchor constraintEqualToAnchor:self.nameButton.trailingAnchor],
        self.headerHeights[1],
        [self.tabs.topAnchor constraintEqualToAnchor:self.status.bottomAnchor constant:6],
        [self.tabs.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:10],
        [self.tabs.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-10],
        [self.codePane.topAnchor constraintEqualToAnchor:self.tabs.bottomAnchor constant:8],
        [self.codePane.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:8],
        [self.codePane.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-8],
        [self.codePane.bottomAnchor constraintEqualToAnchor:self.position.topAnchor constant:-4],
        [self.browserPane.topAnchor constraintEqualToAnchor:self.codePane.topAnchor],
        [self.browserPane.leadingAnchor constraintEqualToAnchor:self.codePane.leadingAnchor],
        [self.browserPane.trailingAnchor constraintEqualToAnchor:self.codePane.trailingAnchor],
        [self.browserPane.bottomAnchor constraintEqualToAnchor:self.codePane.bottomAnchor],
        [self.gutter.leadingAnchor constraintEqualToAnchor:self.codePane.leadingAnchor],
        [self.gutter.topAnchor constraintEqualToAnchor:self.codePane.topAnchor],
        [self.gutter.bottomAnchor constraintEqualToAnchor:self.codePane.bottomAnchor],
        [self.gutter.widthAnchor constraintEqualToConstant:38],
        [self.editor.leadingAnchor constraintEqualToAnchor:self.gutter.trailingAnchor],
        [self.editor.trailingAnchor constraintEqualToAnchor:self.codePane.trailingAnchor],
        [self.editor.topAnchor constraintEqualToAnchor:self.codePane.topAnchor],
        [self.editor.bottomAnchor constraintEqualToAnchor:self.codePane.bottomAnchor],
        [self.search.topAnchor constraintEqualToAnchor:self.browserPane.topAnchor],
        [self.search.leadingAnchor constraintEqualToAnchor:self.browserPane.leadingAnchor],
        [self.search.trailingAnchor constraintEqualToAnchor:self.browserPane.trailingAnchor],
        [self.search.heightAnchor constraintEqualToConstant:48],
        [self.table.topAnchor constraintEqualToAnchor:self.search.bottomAnchor],
        [self.table.leadingAnchor constraintEqualToAnchor:self.browserPane.leadingAnchor],
        [self.table.trailingAnchor constraintEqualToAnchor:self.browserPane.trailingAnchor],
        [self.table.bottomAnchor constraintEqualToAnchor:self.browserPane.bottomAnchor],
        [self.position.leadingAnchor constraintEqualToAnchor:self.nameButton.leadingAnchor],
        [self.position.trailingAnchor constraintEqualToAnchor:self.nameButton.trailingAnchor],
        self.positionHeight,
        [self.position.bottomAnchor constraintEqualToAnchor:logHeader.topAnchor],
        [logHeader.leadingAnchor constraintEqualToAnchor:self.nameButton.leadingAnchor],
        [logHeader.trailingAnchor constraintEqualToAnchor:self.nameButton.trailingAnchor],
        self.logHeaderHeight,
        [logHeader.bottomAnchor constraintEqualToAnchor:self.logView.topAnchor constant:-2],
        [self.logView.leadingAnchor constraintEqualToAnchor:self.codePane.leadingAnchor],
        [self.logView.trailingAnchor constraintEqualToAnchor:self.codePane.trailingAnchor],
        self.logHeight,
        [self.logView.bottomAnchor constraintEqualToAnchor:controls.topAnchor constant:-4],
        [controls.leadingAnchor constraintEqualToAnchor:self.nameButton.leadingAnchor],
        [controls.trailingAnchor constraintEqualToAnchor:self.nameButton.trailingAnchor],
        [controls.heightAnchor constraintEqualToConstant:44], self.bottomConstraint
    ]];
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(keyboardChanged:)
        name:UIKeyboardWillChangeFrameNotification object:nil];
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(appBackground:)
        name:UIApplicationDidEnterBackgroundNotification object:nil];
    [self loadLibrary];
    NSDictionary *draft = [NSDictionary dictionaryWithContentsOfFile:[kJSDirectory stringByAppendingPathComponent:@"draft.plist"]];
    if ([draft[@"code"] isKindOfClass:NSString.class]) {
        self.scriptID = [draft[@"id"] isKindOfClass:NSString.class] ? draft[@"id"] : nil;
        self.scriptName = [draft[@"name"] isKindOfClass:NSString.class] ? draft[@"name"] : @"Script mới";
        self.editor.text = draft[@"code"];
        self.dirty = [draft[@"dirty"] boolValue];
    } else {
        self.editor.text = [self.catalog[@"templates"] firstObject][@"code"] ?: @"log(\"AutoClickJS đã sẵn sàng\");\n";
        [self loadDaemonScript:NO];
    }
    [self updateEditor];
    [self changeTab];
    [self updateButtons];
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    [self poll];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:2 target:self selector:@selector(poll) userInfo:nil repeats:YES];
}

- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    [self.timer invalidate]; self.timer = nil;
    [self saveDraft];
}

- (void)dealloc { [NSNotificationCenter.defaultCenter removeObserver:self]; }
- (void)appBackground:(NSNotification *)note { [self saveDraft]; }
- (void)dismissSelf { [self saveDraft]; [self dismissViewControllerAnimated:YES completion:nil]; }

- (void)keyboardChanged:(NSNotification *)note {
    CGRect end = [note.userInfo[UIKeyboardFrameEndUserInfoKey] CGRectValue];
    CGRect local = [self.view convertRect:end fromView:nil];
    CGFloat overlap = MAX(0, CGRectGetMaxY(self.view.bounds) - local.origin.y - self.view.safeAreaInsets.bottom);
    self.keyboardOverlap = overlap;
    self.bottomConstraint.constant = -8 - overlap;
    if (overlap > 0) { self.logHeight.constant = 0; [self.logButton setTitle:@"Nhật ký ▾" forState:UIControlStateNormal]; }
    [UIView animateWithDuration:[note.userInfo[UIKeyboardAnimationDurationUserInfoKey] doubleValue]
        animations:^{ [self.view layoutIfNeeded]; }];
}

- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    BOOL compact = self.view.bounds.size.height < 450;
    BOOL keyboard = self.keyboardOverlap > 0;
    self.nameButton.hidden = compact; self.status.hidden = compact;
    self.headerHeights[0].constant = compact ? 0 : 34;
    self.headerHeights[1].constant = compact ? 0 : 20;
    self.position.hidden = keyboard; self.logHeader.hidden = keyboard;
    self.positionHeight.constant = keyboard ? 0 : 18;
    self.logHeaderHeight.constant = keyboard ? 0 : 32;
}

- (UIToolbar *)keyboardToolbar {
    UIToolbar *bar = [[UIToolbar alloc] initWithFrame:CGRectMake(0, 0, 320, 44)];
    NSMutableArray *items = [NSMutableArray new];
    NSArray *titles = @[@"Tab", @"{}", @"()", @";", @"↶", @"↷", @"Ẩn"];
    NSArray *selectors = @[@"insertTab", @"insertBraces", @"insertParens", @"insertSemicolon", @"undoEdit", @"redoEdit", @"hideKeyboard"];
    for (NSUInteger i = 0; i < titles.count; i++) {
        [items addObject:[[UIBarButtonItem alloc] initWithTitle:titles[i] style:UIBarButtonItemStylePlain
            target:self action:NSSelectorFromString(selectors[i])]];
        if (i + 1 < titles.count) [items addObject:[[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemFlexibleSpace target:nil action:nil]];
    }
    bar.items = items;
    return bar;
}
- (void)insertTab { [self insertSnippet:@"    "]; }
- (void)insertBraces { [self insertSnippet:@"{\n    \n}\n"]; }
- (void)insertParens { [self insertSnippet:@"()"]; }
- (void)insertSemicolon { [self insertSnippet:@";"]; }
- (void)undoEdit { [self.editor.undoManager undo]; [self textViewDidChange:self.editor]; }
- (void)redoEdit { [self.editor.undoManager redo]; [self textViewDidChange:self.editor]; }
- (void)hideKeyboard { [self.view endEditing:YES]; }

- (void)insertSnippet:(NSString *)snippet {
    self.tabs.selectedSegmentIndex = 0; [self changeTab];
    UITextRange *selected = self.editor.selectedTextRange;
    if (selected) [self.editor replaceRange:selected withText:snippet];
    else [self.editor insertText:snippet];
    [self textViewDidChange:self.editor];
    [self.editor becomeFirstResponder];
}

- (void)textViewDidChange:(UITextView *)textView {
    self.revision++; self.dirty = YES;
    [self updateEditor];
    [NSObject cancelPreviousPerformRequestsWithTarget:self selector:@selector(saveDraft) object:nil];
    [self performSelector:@selector(saveDraft) withObject:nil afterDelay:0.5];
}
- (void)textViewDidChangeSelection:(UITextView *)textView { [self updatePosition]; }
- (void)scrollViewDidScroll:(UIScrollView *)scrollView {
    if (scrollView == self.editor) [self.gutter setNeedsDisplay];
}

- (void)updateEditor {
    NSString *text = self.editor.text ?: @"";
    NSMutableArray *starts = [NSMutableArray arrayWithObject:@0];
    for (NSUInteger i = 0; i < text.length; i++) if ([text characterAtIndex:i] == '\n') [starts addObject:@(i + 1)];
    self.lineStarts = starts; self.gutter.starts = starts;
    [self.nameButton setTitle:[NSString stringWithFormat:@"%@%@", self.scriptName, self.dirty ? @" •" : @""] forState:UIControlStateNormal];
    [self updatePosition]; [self.gutter setNeedsDisplay];
    [NSObject cancelPreviousPerformRequestsWithTarget:self selector:@selector(highlight) object:nil];
    [self performSelector:@selector(highlight) withObject:nil afterDelay:0.15];
}

- (void)updatePosition {
    NSUInteger position = MIN(self.editor.selectedRange.location, self.editor.text.length), line = 0;
    for (NSUInteger i = 0; i < self.lineStarts.count && self.lineStarts[i].unsignedIntegerValue <= position; i++) line = i;
    NSUInteger start = self.lineStarts.count ? self.lineStarts[line].unsignedIntegerValue : 0;
    self.position.text = [NSString stringWithFormat:@"Dòng %lu · Cột %lu · %lu dòng · %@",
        (unsigned long)line + 1, (unsigned long)(position - start) + 1, (unsigned long)self.lineStarts.count,
        self.dirty ? @"Bản nháp" : @"Đã lưu"];
}

- (void)highlight {
    if (self.editor.markedTextRange || self.editor.text.length > 150000) return;
    NSString *text = self.editor.text ?: @"";
    NSTextStorage *storage = self.editor.textStorage;
    NSRange all = NSMakeRange(0, text.length);
    BOOL undo = self.editor.undoManager.isUndoRegistrationEnabled;
    if (undo) [self.editor.undoManager disableUndoRegistration];
    [storage beginEditing];
    [storage addAttributes:@{NSFontAttributeName: self.editor.font, NSForegroundColorAttributeName: UIColor.labelColor} range:all];
    NSString *pattern = @"//[^\\n]*|/\\*[\\s\\S]*?\\*/|\"(?:\\\\.|[^\"\\\\])*\"|'(?:\\\\.|[^'\\\\])*'|`(?:\\\\.|[^`\\\\])*`|\\b(var|let|const|function|return|if|else|while|for|true|false|null|try|catch|throw|new|break|continue)\\b|\\b[0-9]+(?:\\.[0-9]+)?\\b";
    NSRegularExpression *regex = [NSRegularExpression regularExpressionWithPattern:pattern options:0 error:NULL];
    [regex enumerateMatchesInString:text options:0 range:all usingBlock:^(NSTextCheckingResult *match, NSMatchingFlags flags, BOOL *stop) {
        if (!match) return;
        NSString *token = [text substringWithRange:match.range];
        unichar first = [token characterAtIndex:0];
        UIColor *color = [token hasPrefix:@"//"] || [token hasPrefix:@"/*"] ? UIColor.systemGreenColor :
            first == '"' || first == '\'' || first == '`' ? UIColor.systemOrangeColor :
            first >= '0' && first <= '9' ? UIColor.systemBlueColor : UIColor.systemPurpleColor;
        [storage addAttribute:NSForegroundColorAttributeName value:color range:match.range];
    }];
    [storage endEditing];
    if (undo) [self.editor.undoManager enableUndoRegistration];
    self.editor.typingAttributes = @{NSFontAttributeName: [UIFont monospacedSystemFontOfSize:14 weight:UIFontWeightRegular],
                                    NSForegroundColorAttributeName: UIColor.labelColor};
}

- (BOOL)ensureDirectory:(NSError **)error {
    return [NSFileManager.defaultManager createDirectoryAtPath:kJSDirectory withIntermediateDirectories:YES attributes:nil error:error];
}
- (void)saveDraft {
    if (!self.editor) return;
    NSError *error = nil;
    if (![self ensureDirectory:&error]) { self.position.text = @"Không lưu được bản nháp trên iPhone"; return; }
    NSDictionary *draft = @{ @"code": self.editor.text ?: @"", @"name": self.scriptName ?: @"Script mới",
                             @"id": self.scriptID ?: @"", @"dirty": @(self.dirty) };
    if (![draft writeToFile:[kJSDirectory stringByAppendingPathComponent:@"draft.plist"] atomically:YES])
        self.position.text = @"Lưu bản nháp thất bại; hãy xuất code để giữ lại";
}

- (void)alert:(NSString *)title message:(NSString *)message {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:title message:message preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Đóng" style:UIAlertActionStyleCancel handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (BOOL)checkSyntax:(BOOL)showSuccess {
    NSString *code = self.editor.text ?: @"";
    if (![code stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length) {
        [self alert:@"Code đang trống" message:@"Chọn mẫu trong Thư viện hoặc chèn lệnh trước khi chạy."]; return NO;
    }
    if ([code lengthOfBytesUsingEncoding:NSUTF8StringEncoding] > 1024 * 1024) {
        [self alert:@"Script quá lớn" message:@"Giới hạn mỗi script là 1 MiB UTF-8."]; return NO;
    }
    JSContext *context = [JSContext new];
    JSStringRef source = JSStringCreateWithCFString((__bridge CFStringRef)code);
    JSStringRef url = JSStringCreateWithUTF8CString("AutoClickJS.js");
    JSValueRef exception = NULL;
    BOOL valid = JSCheckScriptSyntax(context.JSGlobalContextRef, source, url, 1, &exception);
    JSStringRelease(source); JSStringRelease(url);
    if (!valid) {
        JSValue *error = [JSValue valueWithJSValueRef:exception inContext:context];
        NSInteger line = [error[@"line"] toInt32];
        [self goToLine:MAX(1, line)];
        [self alert:[NSString stringWithFormat:@"Lỗi cú pháp · dòng %ld", (long)MAX(1, line)] message:error.toString ?: @"JavaScript không hợp lệ"];
    } else if (showSuccess) {
        [self alert:@"Cú pháp hợp lệ" message:@"Code chưa được chạy. Kiểm tra cú pháp không xác nhận OCR, tọa độ hoặc API sẽ hoạt động đúng."];
    }
    return valid;
}
- (void)checkSyntaxAction { [self checkSyntax:YES]; }
- (void)goToLine:(NSInteger)line {
    self.tabs.selectedSegmentIndex = 0; [self changeTab];
    if (!self.lineStarts.count) return;
    NSUInteger index = MIN(MAX(1, line), self.lineStarts.count) - 1;
    NSUInteger start = self.lineStarts[index].unsignedIntegerValue;
    NSUInteger end = index + 1 < self.lineStarts.count ? self.lineStarts[index+1].unsignedIntegerValue : self.editor.text.length;
    self.editor.selectedRange = NSMakeRange(start, end - start);
    [self.editor scrollRangeToVisible:self.editor.selectedRange];
}
- (void)promptLine {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Đi tới dòng" message:nil preferredStyle:UIAlertControllerStyleAlert];
    [a addTextFieldWithConfigurationHandler:^(UITextField *field) { field.keyboardType = UIKeyboardTypeNumberPad; field.placeholder = @"Số dòng"; }];
    [a addAction:[UIAlertAction actionWithTitle:@"Hủy" style:UIAlertActionStyleCancel handler:nil]];
    [a addAction:[UIAlertAction actionWithTitle:@"Đi tới" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        [self goToLine:a.textFields.firstObject.text.integerValue];
    }]];
    [self presentViewController:a animated:YES completion:nil];
}

- (void)updateButtons {
    self.runButton.enabled = !self.busy && !self.running && self.connected;
    self.stopButton.enabled = !self.busy && self.running && self.connected;
}
- (void)poll {
    if (self.polling || self.busy || UIApplication.sharedApplication.applicationState != UIApplicationStateActive) return;
    self.polling = YES;
    NSUInteger epoch = self.controlEpoch;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        NSString *reply = TVNCSendControlCommand(@"autolog", 3) ?: @"";
        NSArray *parts = [[reply stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] componentsSeparatedByString:@" "];
        BOOL connected = parts.count >= 2 && [parts[0] isEqualToString:@"OK"] &&
                         ([parts[1] isEqualToString:@"running"] || [parts[1] isEqualToString:@"stopped"]);
        BOOL running = connected && [parts[1] isEqualToString:@"running"];
        NSData *data = connected && parts.count >= 3 ? [[NSData alloc] initWithBase64EncodedString:parts[2] options:0] : nil;
        NSString *output = data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : @"";
        dispatch_async(dispatch_get_main_queue(), ^{
            if (epoch != self.controlEpoch) { self.polling = NO; [self poll]; return; }
            self.polling = NO; self.connected = connected; self.running = running;
            if (!self.busy) {
                self.status.text = !connected ? @"Không nối được dịch vụ · code vẫn được giữ trên iPhone"
                    : running ? @"● Đang chạy trên iPhone · đóng giao diện vẫn chạy" : @"○ Đã dừng · sẵn sàng chạy code";
                self.status.textColor = running ? UIColor.systemGreenColor : connected ? UIColor.secondaryLabelColor : UIColor.systemOrangeColor;
            }
            if (output && ![output isEqualToString:self.lastLog]) {
                BOOL atEnd = self.logView.contentOffset.y + self.logView.bounds.size.height >= self.logView.contentSize.height - 30;
                self.lastLog = output;
                self.logView.text = output.length ? output : @"Chưa có nhật ký.";
                if (atEnd && output.length) [self.logView scrollRangeToVisible:NSMakeRange(output.length - 1, 1)];
                if ([output containsString:@"⚠ lỗi"] && !running && self.logHeight.constant == 0 && !self.editor.isFirstResponder) [self toggleLog];
            }
            [self updateButtons];
        });
    });
}

- (void)saveAndStart {
    if (self.busy || self.running || !self.connected || ![self checkSyntax:NO]) return;
    [self.view endEditing:YES]; [self saveDraft];
    self.controlEpoch++; self.busy = YES; self.status.text = @"Đang lưu code và gửi lệnh chạy…"; [self updateButtons];
    NSString *code = [self.editor.text copy];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSError *error = nil;
        NSString *reply = nil;
        NSString *state = TVNCSendControlCommand(@"autostatus", 4) ?: @"";
        if (![state hasPrefix:@"OK stopped"]) reply = [state hasPrefix:@"OK running"] ? @"Lượt cũ đang chạy. Bấm Dừng trước khi chạy code khác." : @"Không đọc được trạng thái dịch vụ.";
        else if (![self ensureDirectory:&error] || ![code writeToFile:kJSCurrentFile atomically:YES encoding:NSUTF8StringEncoding error:&error])
            reply = error.localizedDescription ?: @"Không lưu được code trên iPhone.";
        else {
            NSString *saved = TVNCSendControlCommand(@"autoreload", 5) ?: @"";
            if (![saved hasPrefix:@"OK"]) reply = [saved containsString:@"Unknown"] ? @"Cần ControlIOS iOS 4.30 trở lên để chạy code dài từ giao diện này." : saved;
            else reply = TVNCSendControlCommand(@"autostart", 5) ?: @"";
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            self.busy = NO;
            if (![reply hasPrefix:@"OK started"] && ![reply hasPrefix:@"OK running"]) [self alert:@"Chưa chạy được script" message:reply.length ? reply : @"Dịch vụ không phản hồi. Code vẫn được lưu."];
            [self poll]; [self updateButtons];
        });
    });
}
- (void)stopScript {
    if (self.busy) return;
    self.controlEpoch++; self.busy = YES; self.status.text = @"Đang yêu cầu dừng…"; [self updateButtons];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *reply = TVNCSendControlCommand(@"autostop", 4) ?: @"";
        dispatch_async(dispatch_get_main_queue(), ^{
            self.busy = NO;
            if (![reply hasPrefix:@"OK"]) [self alert:@"Chưa gửi được lệnh Dừng" message:reply.length ? reply : @"Dịch vụ không phản hồi; không thể xác nhận script đã dừng."];
            [self poll]; [self updateButtons];
        });
    });
}
- (void)toggleLog {
    self.logHeight.constant = self.logHeight.constant > 0 ? 0 : MIN(150, self.view.bounds.size.height * 0.24);
    [self.logButton setTitle:self.logHeight.constant > 0 ? @"Nhật ký ▴" : @"Nhật ký ▾" forState:UIControlStateNormal];
    [self.view layoutIfNeeded];
}
- (void)copyLog {
    [UIPasteboard generalPasteboard].string = [NSString stringWithFormat:@"ControlIOS %@ · AutoClickJS\n%@\n%@",
        [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"] ?: @"", self.status.text ?: @"", self.lastLog ?: @""];
    [self alert:@"Đã chép nhật ký" message:@"Kiểm tra và bỏ dữ liệu riêng tư trước khi gửi cho người hỗ trợ."];
}
- (void)clearLog {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        NSString *reply = TVNCSendControlCommand(@"autologclear", 4) ?: @"";
        dispatch_async(dispatch_get_main_queue(), ^{
            if (![reply hasPrefix:@"OK"]) [self alert:@"Không xóa được nhật ký" message:reply.length ? reply : @"Dịch vụ không phản hồi."];
            [self poll];
        });
    });
}

- (NSString *)pathForID:(NSString *)identifier {
    if (!identifier.length || ![[NSUUID alloc] initWithUUIDString:identifier]) return nil;
    return [kJSDirectory stringByAppendingPathComponent:[identifier stringByAppendingString:@".script.json"]];
}
- (void)loadLibrary {
    NSMutableArray *saved = [NSMutableArray new];
    for (NSString *file in [NSFileManager.defaultManager contentsOfDirectoryAtPath:kJSDirectory error:NULL]) {
        if (![file hasSuffix:@".script.json"]) continue;
        NSData *data = [NSData dataWithContentsOfFile:[kJSDirectory stringByAppendingPathComponent:file]];
        id record = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL] : nil;
        if (![record isKindOfClass:NSDictionary.class] || ![record[@"name"] isKindOfClass:NSString.class] ||
            ![record[@"code"] isKindOfClass:NSString.class] || ![record[@"id"] isKindOfClass:NSString.class] ||
            ![[self pathForID:record[@"id"]].lastPathComponent isEqualToString:file]) continue;
        NSMutableDictionary *item = [record mutableCopy]; item[@"kind"] = @"saved";
        item[@"description"] = [NSString stringWithFormat:@"%lu ký tự · giữ để đổi tên, xuất hoặc xóa", (unsigned long)[record[@"code"] length]];
        [saved addObject:item];
    }
    self.savedScripts = [saved sortedArrayUsingComparator:^NSComparisonResult(NSDictionary *a, NSDictionary *b) {
        return [a[@"name"] localizedStandardCompare:b[@"name"]];
    }];
}
- (void)saveLibrary { [self saveLibraryCopy:NO completion:nil]; }
- (void)saveLibraryCopy:(BOOL)copy completion:(void (^)(void))completion {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:copy ? @"Lưu bản sao" : @"Lưu vào Thư viện" message:nil preferredStyle:UIAlertControllerStyleAlert];
    [alert addTextFieldWithConfigurationHandler:^(UITextField *field) { field.text = self.scriptName; field.placeholder = @"Tên script"; }];
    [alert addAction:[UIAlertAction actionWithTitle:@"Hủy" style:UIAlertActionStyleCancel handler:nil]];
    [alert addAction:[UIAlertAction actionWithTitle:@"Lưu" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        NSString *name = [alert.textFields.firstObject.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (!name.length || name.length > 100) { [self alert:@"Tên chưa hợp lệ" message:@"Dùng tên từ 1 đến 100 ký tự."]; return; }
        NSString *identifier = !copy && [self pathForID:self.scriptID] ? self.scriptID : NSUUID.UUID.UUIDString;
        NSDictionary *record = @{ @"id": identifier, @"name": name, @"code": self.editor.text ?: @"",
                                  @"modified": @([NSDate.date timeIntervalSince1970]) };
        NSError *error = nil;
        NSData *data = [NSJSONSerialization dataWithJSONObject:record options:0 error:&error];
        if (![self ensureDirectory:&error] || !data || ![data writeToFile:[self pathForID:identifier] options:NSDataWritingAtomic error:&error]) {
            [self alert:@"Lưu thất bại" message:error.localizedDescription ?: @"Không ghi được tệp."]; return;
        }
        self.scriptID = identifier; self.scriptName = name; self.dirty = NO;
        [self updateEditor]; [self saveDraft]; [self loadLibrary]; [self rebuildSections];
        if (completion) dispatch_async(dispatch_get_main_queue(), completion);
    }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)replaceCode:(NSString *)code name:(NSString *)name identifier:(NSString *)identifier {
    void (^load)(void) = ^{
        [self saveDraft];
        NSString *draft = [kJSDirectory stringByAppendingPathComponent:@"draft.plist"];
        NSData *backup = [NSData dataWithContentsOfFile:draft];
        [backup writeToFile:[kJSDirectory stringByAppendingPathComponent:@"previous-draft.plist"] atomically:YES];
        self.scriptName = name; self.scriptID = identifier; self.editor.text = code;
        self.revision++; self.dirty = identifier.length == 0;
        self.tabs.selectedSegmentIndex = 0; [self changeTab];
        [self updateEditor]; [self saveDraft];
    };
    if (!self.dirty) { load(); return; }
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Thay code đang soạn?"
        message:@"Bản hiện tại chưa lưu vào Thư viện. Thao tác này không thay script đang chạy." preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"Hủy" style:UIAlertActionStyleCancel handler:nil]];
    [a addAction:[UIAlertAction actionWithTitle:@"Thay code" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *action) { load(); }]];
    [a addAction:[UIAlertAction actionWithTitle:@"Lưu trước" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self saveLibraryCopy:NO completion:load]; });
    }]];
    [self presentViewController:a animated:YES completion:nil];
}

- (void)loadDaemonScript:(BOOL)explicit {
    NSUInteger revision = self.revision;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSString *reply = TVNCSendControlCommand(@"autoget", 5) ?: @"";
        NSString *encoded = [reply hasPrefix:@"OK "] ? [[reply substringFromIndex:3] stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] : nil;
        NSData *data = encoded ? [[NSData alloc] initWithBase64EncodedString:encoded options:0] : nil;
        NSString *code = data ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            if (!code.length) { if (explicit) [self alert:@"Không nạp được code" message:@"Dịch vụ chưa có script hoặc không phản hồi."]; return; }
            if (self.revision != revision) { if (explicit) [self alert:@"Đã giữ code đang soạn" message:@"Code đã thay đổi trong lúc nạp. Hãy nạp lại nếu cần."]; return; }
            if (explicit) [self replaceCode:code name:@"Script từ dịch vụ" identifier:nil];
            else { self.editor.text = code; self.scriptName = @"Script từ dịch vụ"; self.dirty = YES; [self updateEditor]; }
        });
    });
}

- (UIMenu *)fileMenu {
    __weak typeof(self) weak = self;
    UIAction *(^action)(NSString *, NSString *, void (^)(TVNCAutoClickController *)) =
        ^UIAction *(NSString *title, NSString *icon, void (^block)(TVNCAutoClickController *)) {
        return [UIAction actionWithTitle:title image:[UIImage systemImageNamed:icon] identifier:nil handler:^(UIAction *a) {
            TVNCAutoClickController *self = weak; if (self) block(self);
        }];
    };
    return [UIMenu menuWithTitle:@"Tệp JavaScript" children:@[
        action(@"Script mới", @"doc.badge.plus", ^(TVNCAutoClickController *s) { [s replaceCode:@"// Code chạy trên iPhone\n" name:@"Script mới" identifier:nil]; }),
        action(@"Lưu bản sao…", @"doc.on.doc", ^(TVNCAutoClickController *s) { [s saveLibraryCopy:YES completion:nil]; }),
        action(@"Nhập .js…", @"square.and.arrow.down", ^(TVNCAutoClickController *s) { [s importScript]; }),
        action(@"Xuất .js…", @"square.and.arrow.up", ^(TVNCAutoClickController *s) { [s exportCode:s.editor.text name:s.scriptName]; }),
        action(@"Nạp code từ dịch vụ", @"arrow.down.doc", ^(TVNCAutoClickController *s) { [s loadDaemonScript:YES]; }),
        action(@"Đi tới dòng…", @"number", ^(TVNCAutoClickController *s) { [s promptLine]; }),
        action(@"Chép toàn bộ code", @"doc.on.clipboard", ^(TVNCAutoClickController *s) { UIPasteboard.generalPasteboard.string = s.editor.text; }),
        action(@"Khôi phục bản nháp trước", @"arrow.uturn.backward", ^(TVNCAutoClickController *s) {
            NSDictionary *draft = [NSDictionary dictionaryWithContentsOfFile:[kJSDirectory stringByAppendingPathComponent:@"previous-draft.plist"]];
            if ([draft[@"code"] isKindOfClass:NSString.class]) [s replaceCode:draft[@"code"] name:draft[@"name"] ?: @"Bản nháp" identifier:nil];
            else [s alert:@"Chưa có bản nháp trước" message:@"Bản nháp trước được giữ khi bạn thay code bằng mẫu hoặc script khác."];
        })
    ]];
}
- (void)importScript {
    UIDocumentPickerViewController *picker = [[UIDocumentPickerViewController alloc]
        initWithDocumentTypes:@[@"public.javascript", @"public.plain-text", @"public.source-code"] inMode:UIDocumentPickerModeImport];
    picker.delegate = self; picker.allowsMultipleSelection = NO;
    [self presentViewController:picker animated:YES completion:nil];
}
- (void)documentPicker:(UIDocumentPickerViewController *)controller didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls {
    NSURL *url = urls.firstObject; if (!url) return;
    BOOL scoped = [url startAccessingSecurityScopedResource];
    NSError *error = nil;
    NSNumber *size = nil; [url getResourceValue:&size forKey:NSURLFileSizeKey error:&error];
    NSString *code = size.unsignedLongLongValue <= 1024 * 1024 ? [NSString stringWithContentsOfURL:url encoding:NSUTF8StringEncoding error:&error] : nil;
    if (scoped) [url stopAccessingSecurityScopedResource];
    if (!code) { [self alert:@"Không nhập được script" message:error.localizedDescription ?: @"Chọn tệp UTF-8 không lớn hơn 1 MiB."]; return; }
    [self replaceCode:code name:url.lastPathComponent.stringByDeletingPathExtension identifier:nil];
}
- (void)exportCode:(NSString *)code name:(NSString *)name {
    NSString *clean = [[name componentsSeparatedByCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"/\\:*?\"<>|\n\r"]] componentsJoinedByString:@"_"];
    if (!clean.length) clean = @"AutoClickJS";
    NSString *dir = [NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
    NSError *error = nil;
    if (![NSFileManager.defaultManager createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:&error]) {
        [self alert:@"Xuất thất bại" message:error.localizedDescription]; return;
    }
    NSURL *url = [NSURL fileURLWithPath:[dir stringByAppendingPathComponent:[clean stringByAppendingPathExtension:@"js"]]];
    if (![code writeToURL:url atomically:YES encoding:NSUTF8StringEncoding error:&error]) { [self alert:@"Xuất thất bại" message:error.localizedDescription]; return; }
    UIActivityViewController *share = [[UIActivityViewController alloc] initWithActivityItems:@[url] applicationActivities:nil];
    share.popoverPresentationController.sourceView = self.nameButton;
    share.popoverPresentationController.sourceRect = self.nameButton.bounds;
    share.completionWithItemsHandler = ^(UIActivityType type, BOOL completed, NSArray *items, NSError *err) {
        [NSFileManager.defaultManager removeItemAtPath:dir error:NULL];
    };
    [self presentViewController:share animated:YES completion:nil];
}

- (void)changeTab {
    BOOL code = self.tabs.selectedSegmentIndex == 0;
    self.codePane.hidden = !code; self.browserPane.hidden = code;
    if (!code) [self.editor resignFirstResponder];
    else [self.search resignFirstResponder];
    [self rebuildSections];
}
- (void)searchBar:(UISearchBar *)searchBar textDidChange:(NSString *)searchText { [self rebuildSections]; }
- (BOOL)matchesSearch:(NSDictionary *)row {
    NSString *query = [self.search.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!query.length) return YES;
    NSString *hay = [NSString stringWithFormat:@"%@ %@ %@ %@", row[@"name"] ?: row[@"title"] ?: @"", row[@"signature"] ?: @"", row[@"description"] ?: @"", row[@"body"] ?: @""];
    return [hay rangeOfString:query options:NSCaseInsensitiveSearch | NSDiacriticInsensitiveSearch].location != NSNotFound;
}
- (void)addSection:(NSMutableArray *)sections title:(NSString *)title items:(NSArray *)items kind:(NSString *)kind {
    NSMutableArray *rows = [NSMutableArray new];
    for (NSDictionary *row in items) if ([self matchesSearch:row]) {
        NSMutableDictionary *item = [row mutableCopy]; item[@"kind"] = kind; [rows addObject:item];
    }
    if (rows.count) [sections addObject:@{@"title": title, @"rows": rows}];
}
- (void)rebuildSections {
    NSMutableArray *sections = [NSMutableArray new];
    NSInteger tab = self.tabs.selectedSegmentIndex;
    if (tab == 1) {
        [self addSection:sections title:@"Script đã lưu trên iPhone" items:self.savedScripts kind:@"saved"];
        [self addSection:sections title:@"Mẫu bắt đầu · chọn để xem code" items:self.catalog[@"templates"] kind:@"template"];
    } else if (tab == 2) {
        NSMutableOrderedSet *groups = [NSMutableOrderedSet new];
        for (NSDictionary *row in self.catalog[@"commands"]) [groups addObject:row[@"group"]];
        for (NSString *group in groups) {
            NSPredicate *predicate = [NSPredicate predicateWithFormat:@"group == %@", group];
            [self addSection:sections title:group items:[self.catalog[@"commands"] filteredArrayUsingPredicate:predicate] kind:@"command"];
        }
    } else if (tab == 3) [self addSection:sections title:@"Hướng dẫn và xử lý lỗi" items:self.catalog[@"help"] kind:@"help"];
    self.sections = sections;
    [self.table reloadData];
    if (!sections.count && tab != 0) {
        UILabel *empty = [self label:14]; empty.textAlignment = NSTextAlignmentCenter;
        empty.numberOfLines = 0; empty.text = @"Không có kết quả.\nThử từ khóa khác hoặc xóa ô tìm kiếm.";
        self.table.backgroundView = empty;
    } else self.table.backgroundView = nil;
}
- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView { return self.sections.count; }
- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section { return [self.sections[section][@"rows"] count]; }
- (NSString *)tableView:(UITableView *)tableView titleForHeaderInSection:(NSInteger)section { return self.sections[section][@"title"]; }
- (NSDictionary *)rowAt:(NSIndexPath *)index { return self.sections[index.section][@"rows"][index.row]; }
- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)index {
    UITableViewCell *cell = [tableView dequeueReusableCellWithIdentifier:@"js"];
    if (!cell) cell = [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:@"js"];
    NSDictionary *row = [self rowAt:index];
    cell.textLabel.text = row[@"signature"] ?: row[@"name"] ?: row[@"title"];
    cell.textLabel.font = [UIFont systemFontOfSize:14 weight:UIFontWeightSemibold];
    cell.textLabel.numberOfLines = 0;
    cell.detailTextLabel.text = row[@"description"] ?: @"Bấm để xem hướng dẫn";
    cell.detailTextLabel.numberOfLines = 0;
    cell.detailTextLabel.textColor = UIColor.secondaryLabelColor;
    cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    return cell;
}
- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)index {
    [tableView deselectRowAtIndexPath:index animated:YES];
    NSDictionary *row = [self rowAt:index];
    if ([row[@"kind"] isEqualToString:@"saved"]) {
        [self replaceCode:row[@"code"] name:row[@"name"] identifier:row[@"id"]]; return;
    }
    [self showReference:row];
}
- (void)showReference:(NSDictionary *)row {
    UIViewController *page = [UIViewController new];
    page.title = row[@"name"] ?: row[@"title"]; page.view.backgroundColor = UIColor.systemBackgroundColor;
    UITextView *text = [UITextView new]; text.editable = NO;
    text.font = [UIFont monospacedSystemFontOfSize:14 weight:UIFontWeightRegular];
    text.textColor = UIColor.labelColor;
    text.text = [row[@"kind"] isEqualToString:@"help"] ? row[@"body"] :
        [NSString stringWithFormat:@"%@\n\n%@\n\n%@", row[@"signature"] ?: row[@"name"], row[@"description"] ?: @"", row[@"code"] ?: @""];
    text.translatesAutoresizingMaskIntoConstraints = NO; [page.view addSubview:text];
    UILayoutGuide *safe = page.view.safeAreaLayoutGuide;
    [NSLayoutConstraint activateConstraints:@[[text.topAnchor constraintEqualToAnchor:safe.topAnchor], [text.bottomAnchor constraintEqualToAnchor:safe.bottomAnchor],
        [text.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:10], [text.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-10]]];
    page.navigationItem.leftBarButtonItem = [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemClose target:self action:@selector(closeReference)];
    if (row[@"code"]) {
        self.reference = row;
        page.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc]
            initWithTitle:[row[@"kind"] isEqualToString:@"template"] ? @"Dùng mẫu" : @"Chèn code"
            style:UIBarButtonItemStyleDone target:self action:@selector(applyReference)];
    }
    [self presentViewController:[[UINavigationController alloc] initWithRootViewController:page] animated:YES completion:nil];
}
- (void)applyReference {
    NSDictionary *row = self.reference;
    [self dismissViewControllerAnimated:YES completion:^{
        if ([row[@"kind"] isEqualToString:@"template"]) [self replaceCode:row[@"code"] name:row[@"name"] identifier:nil];
        else [self insertSnippet:row[@"code"]];
    }];
}
- (void)closeReference { [self dismissViewControllerAnimated:YES completion:nil]; }
- (UIContextMenuConfiguration *)tableView:(UITableView *)tableView contextMenuConfigurationForRowAtIndexPath:(NSIndexPath *)index point:(CGPoint)point {
    NSDictionary *row = [self rowAt:index]; if (![row[@"kind"] isEqualToString:@"saved"]) return nil;
    __weak typeof(self) weak = self;
    return [UIContextMenuConfiguration configurationWithIdentifier:nil previewProvider:nil actionProvider:^UIMenu *(NSArray *suggested) {
        UIAction *rename = [UIAction actionWithTitle:@"Đổi tên" image:[UIImage systemImageNamed:@"pencil"] identifier:nil handler:^(UIAction *a) { [weak renameScript:row]; }];
        UIAction *export = [UIAction actionWithTitle:@"Xuất .js" image:[UIImage systemImageNamed:@"square.and.arrow.up"] identifier:nil handler:^(UIAction *a) { [weak exportCode:row[@"code"] name:row[@"name"]]; }];
        UIAction *remove = [UIAction actionWithTitle:@"Xóa khỏi Thư viện" image:[UIImage systemImageNamed:@"trash"] identifier:nil handler:^(UIAction *a) { [weak deleteScript:row]; }];
        remove.attributes = UIMenuElementAttributesDestructive;
        return [UIMenu menuWithTitle:row[@"name"] children:@[rename, export, remove]];
    }];
}
- (void)renameScript:(NSDictionary *)row {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Đổi tên script" message:nil preferredStyle:UIAlertControllerStyleAlert];
    [a addTextFieldWithConfigurationHandler:^(UITextField *field) { field.text = row[@"name"]; }];
    [a addAction:[UIAlertAction actionWithTitle:@"Hủy" style:UIAlertActionStyleCancel handler:nil]];
    [a addAction:[UIAlertAction actionWithTitle:@"Lưu" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        NSString *name = [a.textFields.firstObject.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (!name.length || name.length > 100) { [self alert:@"Tên chưa hợp lệ" message:@"Dùng tên từ 1 đến 100 ký tự."]; return; }
        NSMutableDictionary *record = [row mutableCopy]; [record removeObjectForKey:@"kind"]; [record removeObjectForKey:@"description"]; record[@"name"] = name;
        NSError *error = nil; NSData *data = [NSJSONSerialization dataWithJSONObject:record options:0 error:&error];
        if (!data || ![data writeToFile:[self pathForID:row[@"id"]] options:NSDataWritingAtomic error:&error]) { [self alert:@"Đổi tên thất bại" message:error.localizedDescription]; return; }
        if ([self.scriptID isEqualToString:row[@"id"]]) { self.scriptName = name; [self updateEditor]; [self saveDraft]; }
        [self loadLibrary]; [self rebuildSections];
    }]];
    [self presentViewController:a animated:YES completion:nil];
}
- (void)deleteScript:(NSDictionary *)row {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Xóa script đã lưu?" message:row[@"name"] preferredStyle:UIAlertControllerStyleAlert];
    [a addAction:[UIAlertAction actionWithTitle:@"Hủy" style:UIAlertActionStyleCancel handler:nil]];
    [a addAction:[UIAlertAction actionWithTitle:@"Xóa" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *action) {
        NSError *error = nil;
        if (![NSFileManager.defaultManager removeItemAtPath:[self pathForID:row[@"id"]] error:&error]) { [self alert:@"Xóa thất bại" message:error.localizedDescription]; return; }
        if ([self.scriptID isEqualToString:row[@"id"]]) { self.scriptID = nil; self.dirty = YES; [self updateEditor]; [self saveDraft]; }
        [self loadLibrary]; [self rebuildSections];
    }]];
    [self presentViewController:a animated:YES completion:nil];
}
@end
