#pragma once

#include "TVNCSocket.h"

typedef enum {
    TVNCRFBUnresponsive = 0,
    TVNCRFBReady = 1,
    TVNCRFBAuthRequired = 2,
    TVNCRFBRefused = 3
} TVNCRFBHealth;

// Every stage uses the same deadline. A banner is emitted by the listener
// before the per-client thread processes ProtocolVersion; it is not a health
// check for that thread. In particular, a banner-only server can accumulate
// dead PC retries and loopback probes while never negotiating a session.
static inline int TVNCRFBTransfer(int fd, void *buffer, size_t size,
                                int writing, double deadline) {
    uint8_t *bytes = (uint8_t *)buffer;
    size_t offset = 0;
    while (offset < size) {
        double left = deadline - TVNCMonotonicSeconds();
        if (left <= 0) { errno = ETIMEDOUT; return 0; }
        struct pollfd waitFD = {fd, (short)(writing ? POLLOUT : POLLIN), 0};
        int ready = poll(&waitFD, 1, (int)(left * 1000) + 1);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) { if (!ready) errno = ETIMEDOUT; return 0; }
        if (waitFD.revents & POLLNVAL) return 0;
        ssize_t count;
        if (writing) {
            int flags = 0;
#ifdef MSG_NOSIGNAL
            flags = MSG_NOSIGNAL;
#endif
            count = send(fd, bytes + offset, size - offset, flags);
        } else {
            count = recv(fd, bytes + offset, size - offset, 0);
        }
        if (count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK))
            continue;
        if (count == 0) { errno = ECONNRESET; return 0; }
        if (count < 0) return 0;
        offset += (size_t)count;
    }
    return 1;
}

static inline uint32_t TVNCRFBU32(const uint8_t *bytes) {
    return ((uint32_t)bytes[0] << 24) | ((uint32_t)bytes[1] << 16) |
           ((uint32_t)bytes[2] << 8) | bytes[3];
}

static inline TVNCRFBHealth TVNCProbeRFBOnSocket(int fd, double deadline) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        return TVNCRFBUnresponsive;
#ifdef SO_NOSIGPIPE
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
#endif
    uint8_t banner[12];
    if (!TVNCRFBTransfer(fd, banner, sizeof(banner), 0, deadline))
        return TVNCRFBUnresponsive;
    int minor;
    if (memcmp(banner, "RFB 003.008\n", 12) == 0) minor = 8;
    else if (memcmp(banner, "RFB 003.007\n", 12) == 0) minor = 7;
    else if (memcmp(banner, "RFB 003.003\n", 12) == 0) minor = 3;
    else return TVNCRFBUnresponsive;
    if (!TVNCRFBTransfer(fd, banner, sizeof(banner), 1, deadline))
        return errno == EPIPE || errno == ECONNRESET ? TVNCRFBRefused : TVNCRFBUnresponsive;

    uint8_t word[4];
    uint32_t security = 0;
    if (minor == 3) {
        if (!TVNCRFBTransfer(fd, word, sizeof(word), 0, deadline))
            return errno == ECONNRESET ? TVNCRFBRefused : TVNCRFBUnresponsive;
        security = TVNCRFBU32(word);
        if (!security) return TVNCRFBRefused;
    } else {
        uint8_t count;
        if (!TVNCRFBTransfer(fd, &count, 1, 0, deadline))
            return errno == ECONNRESET ? TVNCRFBRefused : TVNCRFBUnresponsive;
        if (!count) return TVNCRFBRefused;
        uint8_t types[255];
        if (!TVNCRFBTransfer(fd, types, count, 0, deadline))
            return TVNCRFBUnresponsive;
        for (unsigned i = 0; i < count; ++i) {
            if (types[i] == 1) { security = 1; break; }
            if (types[i] == 2) security = 2;
        }
        // A valid list of other auth methods still demonstrates that the
        // protocol worker answered; never authenticate or restart for policy.
        if (!security) return TVNCRFBAuthRequired;
        uint8_t choice = (uint8_t)security;
        if (!TVNCRFBTransfer(fd, &choice, 1, 1, deadline))
            return TVNCRFBUnresponsive;
    }
    if (security == 2) {
        uint8_t challenge[16];
        return TVNCRFBTransfer(fd, challenge, sizeof(challenge), 0, deadline)
            ? TVNCRFBAuthRequired : TVNCRFBUnresponsive;
    }
    if (security != 1) return TVNCRFBAuthRequired;
    if (minor == 8) {
        if (!TVNCRFBTransfer(fd, word, sizeof(word), 0, deadline))
            return TVNCRFBUnresponsive;
        if (TVNCRFBU32(word)) return TVNCRFBRefused;
    }
    // A shared ClientInit must not evict an existing PC viewer.
    uint8_t shared = 1;
    if (!TVNCRFBTransfer(fd, &shared, 1, 1, deadline))
        return TVNCRFBUnresponsive;
    uint8_t init[24];
    if (!TVNCRFBTransfer(fd, init, sizeof(init), 0, deadline))
        return TVNCRFBUnresponsive;
    if (!(init[0] || init[1]) || !(init[2] || init[3]))
        return TVNCRFBUnresponsive;
    uint32_t nameSize = TVNCRFBU32(init + 20);
    if (nameSize > 4096) return TVNCRFBUnresponsive;
    uint8_t name[4096];
    if (!TVNCRFBTransfer(fd, name, nameSize, 0, deadline))
        return TVNCRFBUnresponsive;
    return TVNCRFBReady;
}

static inline TVNCRFBHealth TVNCProbeRFBLoopback(int port, double timeoutSeconds) {
    double deadline = TVNCMonotonicSeconds() + timeoutSeconds;
    int fd = TVNCConnectLoopback(port, timeoutSeconds < 0.75 ? timeoutSeconds : 0.75);
    if (fd < 0) return TVNCRFBUnresponsive;
    TVNCRFBHealth health = TVNCProbeRFBOnSocket(fd, deadline);
    close(fd);
    return health;
}
