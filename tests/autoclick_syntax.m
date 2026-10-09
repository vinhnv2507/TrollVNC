#import <Foundation/Foundation.h>
#import <JavaScriptCore/JavaScriptCore.h>

static BOOL check(JSContext *context, NSString *code, JSValueRef *exception) {
    JSStringRef script = JSStringCreateWithCFString((__bridge CFStringRef)code);
    JSStringRef url = JSStringCreateWithUTF8CString("AutoClickJS.js");
    BOOL result = JSCheckScriptSyntax(context.JSGlobalContextRef, script, url, 1, exception);
    JSStringRelease(script);
    JSStringRelease(url);
    return result;
}
int main(void) {
    @autoreleasepool {
        JSContext *context = [JSContext new];
        JSValueRef error = NULL;
        // Checking must not execute any actions in the source.
        NSCAssert(check(context, @"throw new Error('must not execute');\n", &error), @"Syntax checking executed code");
        NSCAssert(!check(context, @"var x = 1;\nvar broken = ;\n", &error), @"Invalid syntax accepted");
        JSValue *value = [JSValue valueWithJSValueRef:error inContext:context];
        NSCAssert([value[@"line"] toInt32] == 2, @"Incorrect error line");
        NSData *data = [NSData dataWithContentsOfFile:@"app/TrollVNC/TrollVNC/AutoClickJS.json"];
        NSDictionary *catalog = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
        NSArray *rows = [catalog[@"commands"] arrayByAddingObjectsFromArray:catalog[@"templates"]];
        for (NSDictionary *row in rows) {
            error = NULL;
            NSCAssert(check(context, row[@"code"], &error), @"Invalid JavaScriptCore example: %@", row[@"name"]);
        }
        NSLog(@"JavaScriptCore syntax checks passed: no execution, exact error line, %lu examples", (unsigned long)rows.count);
    }
    return 0;
}
