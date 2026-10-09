#pragma once

#include <JavaScriptCore/JavaScriptCore.h>
#include <dlfcn.h>

// Available in JavaScriptCore on iOS 7+. Resolve at runtime because the API
// is exported but absent from the public SDK headers. CPU-only loops must
// terminate too: a script must not bypass expiry by omitting native calls.
class TVNCJSExecutionLimit {
public:
    using Callback = bool (*)(JSContextRef, void *);
    TVNCJSExecutionLimit(JSGlobalContextRef context, Callback callback, void *data)
        : callback_(callback), data_(data) {
        set_ = reinterpret_cast<Set>(dlsym(RTLD_DEFAULT, "JSContextGroupSetExecutionTimeLimit"));
        clear_ = reinterpret_cast<Clear>(dlsym(RTLD_DEFAULT, "JSContextGroupClearExecutionTimeLimit"));
        if (set_ && clear_) {
            group_ = JSContextGetGroup(context);
            set_(group_, 0.1, check, this);
        }
    }
    ~TVNCJSExecutionLimit() { if (group_) clear_(group_); }
    bool installed() const { return group_ != nullptr; }
    TVNCJSExecutionLimit(const TVNCJSExecutionLimit &) = delete;
    TVNCJSExecutionLimit &operator=(const TVNCJSExecutionLimit &) = delete;
private:
    static bool check(JSContextRef context, void *data) {
        auto *limit = static_cast<TVNCJSExecutionLimit *>(data);
        if (limit->callback_(context, limit->data_)) return true;
        // Explicitly rearm. Some system WebKit versions leave the watchdog
        // inactive after a callback returns false, despite the API contract.
        limit->set_(limit->group_, 0.1, check, limit);
        return false;
    }
    using Set = void (*)(JSContextGroupRef, double, Callback, void *);
    using Clear = void (*)(JSContextGroupRef);
    JSContextGroupRef group_ = nullptr;
    Set set_ = nullptr;
    Clear clear_ = nullptr;
    Callback callback_;
    void *data_;
};

// Keep the original functions and native permission check private to each
// closure. User scripts cannot get an unguarded native action via a global.
static inline void TVNCGuardNativeAPIs(JSContext *context, NSArray<NSString *> *names,
                                     BOOL (^allowed)(void)) {
    JSValue *install = [context evaluateScript:
        @"(function(g,names,allowed){var invoke=Function.prototype.call.bind(Function.prototype.apply);"
         "names.forEach(function(name){"
         "var fn=g[name];g[name]=function(){"
         "if(!allowed())throw new Error('__STOP__');"
         "var result=invoke(fn,this,arguments);"
         "if(!allowed())throw new Error('__STOP__');return result;};});})"];
    [install callWithArguments:@[context.globalObject, names, allowed]];
}
