#pragma once
#import <Foundation/Foundation.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
#include <string.h>

#define TVUPDATE_DIRECTORY "/var/tmp/controlios-update-service"
#define TVUPDATE_SOCKET TVUPDATE_DIRECTORY "/control.sock"

static inline NSData *TVUpdateBridge(NSString *request, NSString *helper) {
    struct stat info = {};
    if (lstat(TVUPDATE_SOCKET, &info) != 0 || !S_ISSOCK(info.st_mode) || info.st_uid != 0)
        return [@"ERR LocalUpdateUnavailable root manager socket missing\n" dataUsingEncoding:NSUTF8StringEncoding];
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return [@"ERR LocalUpdateUnavailable manager socket failed\n" dataUsingEncoding:NSUTF8StringEncoding];
    struct timeval limit = {5, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit));
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
    struct sockaddr_un address = {};
    address.sun_len = sizeof(address);
    address.sun_family = AF_UNIX;
    strlcpy(address.sun_path, TVUPDATE_SOCKET, sizeof(address.sun_path));
    uid_t uid; gid_t gid;
    NSData *result = [@"ERR LocalUpdateUnavailable root manager not responding\n" dataUsingEncoding:NSUTF8StringEncoding];
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0 &&
        getpeereid(fd, &uid, &gid) == 0 && uid == 0) {
        NSData *body = [NSJSONSerialization dataWithJSONObject:@{@"request": request, @"helper": helper}
                                                       options:0 error:nil];
        NSMutableData *line = [body mutableCopy];
        [line appendBytes:"\n" length:1];
        const uint8_t *bytes = (const uint8_t *)line.bytes;
        size_t remaining = line.length;
        while (remaining) {
            ssize_t count = send(fd, bytes, remaining, 0);
            if (count <= 0) break;
            bytes += count; remaining -= count;
        }
        if (!remaining) {
            char response[1024];
            ssize_t count = recv(fd, response, sizeof(response), 0);
            if (count > 0) result = [NSData dataWithBytes:response length:(NSUInteger)count];
        }
    }
    close(fd);
    return result;
}
