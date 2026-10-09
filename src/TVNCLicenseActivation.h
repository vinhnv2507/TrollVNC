#pragma once
#import <Foundation/Foundation.h>
#import <Security/Security.h>

static inline NSData *TVNCLicenseDecode(NSString *value) {
    NSMutableString *s = [value mutableCopy];
    [s replaceOccurrencesOfString:@"-" withString:@"+" options:0 range:NSMakeRange(0, s.length)];
    [s replaceOccurrencesOfString:@"_" withString:@"/" options:0 range:NSMakeRange(0, s.length)];
    while (s.length % 4) [s appendString:@"="];
    return [[NSData alloc] initWithBase64EncodedString:s options:0];
}

// Pure verification shared by startup, LAN activation and native tests.
// Never trust identity/expiry/token before the signature has been checked.
static inline NSDictionary *TVNCVerifyLicense(NSString *license, NSData *publicKey,
                                              NSString *udid, NSString **error) {
    NSArray *parts = [license componentsSeparatedByString:@"."];
    if (parts.count != 2 || license.length > 16384) {
        if (error) *error = @"LicenseFormat";
        return nil;
    }
    NSData *payload = TVNCLicenseDecode(parts[0]);
    NSData *signature = TVNCLicenseDecode(parts[1]);
    NSDictionary *attrs = @{(id)kSecAttrKeyType:(id)kSecAttrKeyTypeECSECPrimeRandom,
                            (id)kSecAttrKeyClass:(id)kSecAttrKeyClassPublic,
                            (id)kSecAttrKeySizeInBits:@256};
    SecKeyRef key = SecKeyCreateWithData((__bridge CFDataRef)publicKey,
                                         (__bridge CFDictionaryRef)attrs, NULL);
    BOOL valid = key && payload.length && signature.length &&
        SecKeyVerifySignature(key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,
                              (__bridge CFDataRef)payload, (__bridge CFDataRef)signature, NULL);
    if (key) CFRelease(key);
    if (!valid) {
        if (error) *error = @"LicenseSignature";
        return nil;
    }
    NSDictionary *p = [NSJSONSerialization JSONObjectWithData:payload options:0 error:NULL];
    if (![p isKindOfClass:NSDictionary.class] || ![p[@"udid"] isKindOfClass:NSString.class] ||
        ![p[@"exp"] isKindOfClass:NSNumber.class] || ![p[@"tok"] isKindOfClass:NSString.class] ||
        [p[@"tok"] length] == 0 || [p[@"exp"] doubleValue] < 0 ||
        [p[@"exp"] doubleValue] != (double)[p[@"exp"] longLongValue]) {
        if (error) *error = @"LicensePayload";
        return nil;
    }
    if (!udid.length || ![p[@"udid"] isEqualToString:udid]) {
        if (error) *error = @"LicenseDeviceMismatch";
        return nil;
    }
    return p;
}

// Bad/expired/wrong-device keys must preserve the current license on disk.
// A LAN key must use the existing connection token, avoiding disconnecting
// the Manager or changing other devices' credentials when activating one.
static inline NSDictionary *TVNCActivateLicense(NSString *license, NSData *publicKey,
                                                NSString *udid, NSString *token,
                                                double now, NSString *path, NSString **error) {
    NSDictionary *p = TVNCVerifyLicense(license, publicKey, udid, error);
    if (!p) return nil;
    long long expiry = [p[@"exp"] longLongValue];
    if (expiry && now >= (double)expiry) {
        if (error) *error = @"LicenseExpired";
        return nil;
    }
    if (token.length && ![p[@"tok"] isEqualToString:token]) {
        if (error) *error = @"LicenseTokenMismatch";
        return nil;
    }
    NSFileManager *fm = NSFileManager.defaultManager;
    if (![fm createDirectoryAtPath:path.stringByDeletingLastPathComponent
       withIntermediateDirectories:YES attributes:nil error:NULL] ||
        ![license writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL]) {
        if (error) *error = @"LicenseSaveFailed";
        return nil;
    }
    return p;
}
