#import "../src/TVNCLicenseActivation.h"
#include <cassert>

static NSString *base64url(NSData *data) {
    NSString *s = [data base64EncodedStringWithOptions:0];
    return [[[s stringByReplacingOccurrencesOfString:@"+" withString:@"-"]
              stringByReplacingOccurrencesOfString:@"/" withString:@"_"]
              stringByReplacingOccurrencesOfString:@"=" withString:@""];
}
static NSString *issue(SecKeyRef key, id udid, id expiry, id token) {
    NSData *payload = [NSJSONSerialization dataWithJSONObject:@{@"v":@1,@"udid":udid,@"exp":expiry,@"tok":token}
                                                      options:0 error:NULL];
    CFDataRef signature = SecKeyCreateSignature(key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,
                                                (__bridge CFDataRef)payload, NULL);
    assert(signature);
    NSString *license = [NSString stringWithFormat:@"%@.%@",base64url(payload),base64url((__bridge NSData *)signature)];
    CFRelease(signature);
    return license;
}
int main() {
    @autoreleasepool {
        NSDictionary *attrs = @{(id)kSecAttrKeyType:(id)kSecAttrKeyTypeECSECPrimeRandom,
                                (id)kSecAttrKeySizeInBits:@256};
        SecKeyRef privateKey = SecKeyCreateRandomKey((__bridge CFDictionaryRef)attrs, NULL);
        assert(privateKey);
        SecKeyRef publicKey = SecKeyCopyPublicKey(privateKey);
        NSData *pub = CFBridgingRelease(SecKeyCopyExternalRepresentation(publicKey, NULL));
        NSString *directory = [NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
        NSString *path = [directory stringByAppendingPathComponent:@"license.dat"];
        NSString *valid = issue(privateKey,@"phone-133",@2000,@"manager-token");
        NSString *error = nil;
        assert(TVNCActivateLicense(valid,pub,@"phone-133",@"manager-token",1000,path,&error));
        assert([[NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL] isEqualToString:valid]);
        NSArray *invalid = @[
            @[@"bad-key",@"LicenseFormat"],
            @[[valid stringByAppendingString:@"x"],@"LicenseSignature"],
            @[issue(privateKey,@"other-phone",@2000,@"manager-token"),@"LicenseDeviceMismatch"],
            @[issue(privateKey,@"phone-133",@1000,@"manager-token"),@"LicenseExpired"],
            @[issue(privateKey,@"phone-133",@999,@"manager-token"),@"LicenseExpired"],
            @[issue(privateKey,@"phone-133",@2000,@"wrong-token"),@"LicenseTokenMismatch"],
            @[issue(privateKey,@"phone-133",@"2000",@"manager-token"),@"LicensePayload"],
            @[issue(privateKey,@"phone-133",@2000,NSNull.null),@"LicensePayload"],
        ];
        for (NSArray *row in invalid) {
            error = nil;
            assert(!TVNCActivateLicense(row[0],pub,@"phone-133",@"manager-token",1000,path,&error));
            assert([error isEqualToString:row[1]]);
            assert([[NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL] isEqualToString:valid]);
        }
        assert(TVNCActivateLicense(issue(privateKey,@"phone-133",@3000,@"manager-token"),pub,
                                  @"phone-133",@"manager-token",2001,path,&error)); // Renewal after old expiry.
        assert(TVNCActivateLicense(issue(privateKey,@"phone-133",@0,@"manager-token"),pub,
                                  @"phone-133",@"manager-token",999999,path,&error));
        [[NSFileManager defaultManager] removeItemAtPath:directory error:NULL];
        CFRelease(publicKey);
        CFRelease(privateKey);
        NSLog(@"License activation: signed keys, wrong device/token, expiry, preservation and renewal passed");
    }
    return 0;
}
