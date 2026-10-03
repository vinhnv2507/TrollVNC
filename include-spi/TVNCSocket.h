#pragma once

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

static inline double TVNCMonotonicSeconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (double)now.tv_sec + (double)now.tv_nsec / 1e9;
}

// SO_SNDTIMEO does not bound Darwin's blocking connect(). Use a nonblocking
// connect and one monotonic deadline, including interrupted poll() calls.
static inline int TVNCConnectLoopback(int port, double timeoutSeconds) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0)
        return -1;
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        close(fd);
        return -1;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
#ifdef __APPLE__
    addr.sin_len = sizeof(addr);
#endif
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    int result = connect(fd, (struct sockaddr *)&addr, sizeof(addr));
    int error = result == 0 ? 0 : errno;
    if (result < 0 && (error == EINPROGRESS || error == EINTR)) {
        double deadline = TVNCMonotonicSeconds() + timeoutSeconds;
        struct pollfd waitFD = {fd, POLLOUT, 0};
        for (;;) {
            double left = deadline - TVNCMonotonicSeconds();
            if (left <= 0) { error = ETIMEDOUT; break; }
            result = poll(&waitFD, 1, (int)(left * 1000) + 1);
            if (result < 0 && errno == EINTR)
                continue;
            if (result <= 0) { error = result == 0 ? ETIMEDOUT : errno; break; }
            socklen_t length = sizeof(error);
            if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) < 0)
                error = errno;
            break;
        }
    }
    if (error != 0 || fcntl(fd, F_SETFL, flags) < 0) {
        if (!error) error = errno;
        close(fd);
        errno = error;
        return -1;
    }
    struct timeval timeout = {1, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
#ifdef SO_NOSIGPIPE
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
#endif
    return fd;
}
