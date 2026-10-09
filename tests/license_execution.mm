#import <Foundation/Foundation.h>
#import <JavaScriptCore/JavaScriptCore.h>
#include "../src/TVNCLicensePolicy.h"
#include "../src/TVNCJSExecutionLimit.h"
#include <atomic>
#include <chrono>
#include <thread>
#include <cassert>
#include <unistd.h>

using Clock = std::chrono::steady_clock;
struct TestRun {
    Clock::time_point started = Clock::now();
    std::atomic<bool> stop{false};
    TVNCLicensePolicy policy;
    TestRun() { policy.license(1001, 1000.85, 0); }
};
static bool terminate(JSContextRef, void *data) {
    auto *run = static_cast<TestRun *>(data);
    double elapsed = std::chrono::duration<double>(Clock::now() - run->started).count();
    return run->stop.load() || !run->policy.allowed(1000.85 + elapsed, elapsed);
}
static void testLoop(NSString *source, bool externalStop) {
    JSContext *context = [JSContext new];
    TestRun run;
    TVNCJSExecutionLimit limit(context.JSGlobalContextRef, terminate, &run);
    assert(limit.installed());
    std::thread watchdog;
    if (externalStop) watchdog = std::thread([&] {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        run.stop.store(true);
    });
    [context evaluateScript:source];
    if (watchdog.joinable()) watchdog.join();
    assert(context.exception);
    assert(Clock::now() - run.started < std::chrono::seconds(3));
}
int main() {
    alarm(15); // A broken interrupt must fail CI instead of hanging a runner.
    @autoreleasepool {
        testLoop(@"while(true){}", false);
        testLoop(@"while(true){try{throw new Error('caught');}catch(e){}}", false);
        testLoop(@"while(true){}", true); // Stop without any PC/native API request.
        JSContext *context = [JSContext new];
        __block bool allowed = true;
        __block int taps = 0;
        context[@"tap"] = ^{ taps++; };
        TVNCGuardNativeAPIs(context, @[@"tap"], ^BOOL{ return allowed; });
        [context evaluateScript:@"var savedTap=tap; tap();"];
        assert(!context.exception && taps == 1);
        allowed = false;
        [context evaluateScript:@"try{savedTap();}catch(e){};try{tap();}catch(e){}"];
        assert(taps == 1); // Saved references cannot send taps after expiry.
        allowed = true;
        [context evaluateScript:@"tap();"];
        assert(taps == 2);
        [context evaluateScript:@"Function.prototype.apply=function(){this();}; tap();"];
        assert(taps == 3); // A changed Function prototype cannot expose original native functions.
        NSLog(@"License execution: infinite loops, caught errors, stop, guarded actions passed");
    }
    return 0;
}
