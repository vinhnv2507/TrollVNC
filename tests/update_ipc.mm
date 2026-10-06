#import "../src/TVNCUpdateService.h"
#include <cassert>
int main() {
    @autoreleasepool {
        NSString *helper = @"/var/containers/Bundle/Application/example/TrollStore.app/trollstorehelper";
        NSString *app = @"/var/containers/Bundle/Application/example/ControlIOS.app";
        NSString *job = @"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        NSString *sha = @"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        NSString *url = [NSString stringWithFormat:@"http://172.30.0.91:5555/%@/ControlIOS.tipa", job];
        NSString *request = [NSString stringWithFormat:@"%@ 4.26 %@ %@", job, sha, url];
        NSArray *args = TVUpdateManagerArguments(request, helper, app, @"4.25");
        assert(args.count == 6 && [args[4] isEqualToString:helper] && [args[5] isEqualToString:app]);
        assert([TVUpdateManagerArguments(@"check", helper, app, @"4.25") isEqualToArray:@[@"--check"]]);
        assert(!TVUpdateManagerArguments(request, helper, app, @"4.26"));
        assert(!TVUpdateManagerArguments(request, helper, app, @"4.27"));
        assert(!TVUpdateManagerArguments((id)NSNull.null, helper, app, @"4.25"));
        assert(!TVUpdateManagerArguments(request, @"/bin/sh", app, @"4.25"));
        for (NSString *badURL in @[@"https://172.30.0.91/a", @"http://8.8.8.8/a", @"http://localhost/a",
                                  [url stringByAppendingString:@"?a=b"], [url stringByAppendingString:@"#x"],
                                  @"http://user:pass@172.30.0.91/a", @"http://172.30.0.91/other/ControlIOS.tipa"]) {
            assert(!TVUpdateManagerArguments([NSString stringWithFormat:@"%@ 4.26 %@ %@", job, sha, badURL], helper, app, @"4.25"));
        }
        assert(!TVUpdateManagerArguments([request stringByAppendingString:@" extra"], helper, app, @"4.25"));
    }
}
