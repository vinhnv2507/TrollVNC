#include "../include-spi/TVNCRFBHealth.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>

enum Scenario {
    READY38, READY37, READY33, AUTH, REFUSED, CLOSE_AFTER_BANNER,
    BAD_BANNER, BANNER_STALL, AUTH_STALL, INIT_STALL,
    PARTIAL_INIT, HUGE_NAME, ZERO_SIZE, SLOW_TRICKLE
};

struct TestPeer { int fd; enum Scenario scenario; int shared; };

static int put(int fd, const void *data, size_t size) {
    return TVNCRFBTransfer(fd, (void *)data, size, 1, TVNCMonotonicSeconds() + 1);
}
static int get(int fd, void *data, size_t size) {
    return TVNCRFBTransfer(fd, data, size, 0, TVNCMonotonicSeconds() + 1);
}
static void pause_stalled_peer(void) { usleep(150000); }

static void *serve(void *context) {
    struct TestPeer *peer = context;
    int fd = peer->fd;
    enum Scenario scenario = peer->scenario;
#ifdef SO_NOSIGPIPE
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, sizeof(yes));
#endif
    const char *banner = scenario == READY33 ? "RFB 003.003\n"
                       : scenario == READY37 ? "RFB 003.007\n"
                       : scenario == BAD_BANNER ? "HTTP/1.1 200"
                       : "RFB 003.008\n";
    if (scenario == SLOW_TRICKLE) {
        for (unsigned i = 0; i < 12; ++i) {
            if (!put(fd, banner + i, 1)) break;
            usleep(20000);
        }
        goto done;
    }
    if (!put(fd, banner, 12)) goto done;
    if (scenario == BAD_BANNER || scenario == CLOSE_AFTER_BANNER) goto done;
    char received[12];
    if (!get(fd, received, sizeof(received))) goto done;
    assert(memcmp(received, banner, 12) == 0);
    if (scenario == BANNER_STALL) { pause_stalled_peer(); goto done; }
    uint8_t word[4] = {0, 0, 0, 0};
    if (scenario == REFUSED) {
        put(fd, word, 1);
        goto done;
    }
    if (scenario == READY33) {
        word[3] = 1;
        if (!put(fd, word, 4)) goto done;
    } else {
        uint8_t types[2] = {1, scenario == AUTH || scenario == AUTH_STALL ? 2 : 1};
        if (!put(fd, types, sizeof(types))) goto done;
        uint8_t choice;
        if (!get(fd, &choice, 1)) goto done;
        assert(choice == types[1]);
        if (scenario == AUTH_STALL) { pause_stalled_peer(); goto done; }
        if (scenario == AUTH) {
            uint8_t challenge[16] = {0};
            put(fd, challenge, sizeof(challenge));
            goto done;
        }
        if (scenario != READY37 && !put(fd, word, sizeof(word))) goto done;
    }
    uint8_t shared;
    if (!get(fd, &shared, 1)) goto done;
    peer->shared = shared;
    if (scenario == INIT_STALL) { pause_stalled_peer(); goto done; }
    uint8_t init[24] = {0, 64, 0, 96, 32, 24, 0, 1};
    init[23] = 4;
    if (scenario == HUGE_NAME) init[20] = 1;
    if (scenario == ZERO_SIZE) init[1] = 0;
    if (scenario == PARTIAL_INIT) {
        put(fd, init, 2);
        goto done;
    }
    if (!put(fd, init, sizeof(init))) goto done;
    if (scenario != HUGE_NAME && scenario != ZERO_SIZE) put(fd, "test", 4);
done:
    close(fd);
    return NULL;
}

static void test(enum Scenario scenario, TVNCRFBHealth expected) {
    int sockets[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    struct TestPeer peer = {sockets[1], scenario, -1};
    pthread_t thread;
    assert(pthread_create(&thread, NULL, serve, &peer) == 0);
    double started = TVNCMonotonicSeconds();
    TVNCRFBHealth result = TVNCProbeRFBOnSocket(sockets[0], started + 0.08);
    double elapsed = TVNCMonotonicSeconds() - started;
    close(sockets[0]);
    pthread_join(thread, NULL);
    assert(result == expected);
    assert(elapsed < 0.25);
    if (scenario == BANNER_STALL || scenario == AUTH_STALL || scenario == INIT_STALL || scenario == SLOW_TRICKLE)
        assert(elapsed >= 0.07);
    if (expected == TVNCRFBReady || scenario == INIT_STALL || scenario == HUGE_NAME || scenario == ZERO_SIZE)
        assert(peer.shared == 1);
}

int main(void) {
    test(READY38, TVNCRFBReady);
    test(READY37, TVNCRFBReady);
    test(READY33, TVNCRFBReady);
    test(AUTH, TVNCRFBAuthRequired);
    test(REFUSED, TVNCRFBRefused);
    test(CLOSE_AFTER_BANNER, TVNCRFBRefused);
    test(BAD_BANNER, TVNCRFBUnresponsive);
    test(BANNER_STALL, TVNCRFBUnresponsive);
    test(AUTH_STALL, TVNCRFBUnresponsive);
    test(INIT_STALL, TVNCRFBUnresponsive);
    test(PARTIAL_INIT, TVNCRFBUnresponsive);
    test(HUGE_NAME, TVNCRFBUnresponsive);
    test(ZERO_SIZE, TVNCRFBUnresponsive);
    test(SLOW_TRICKLE, TVNCRFBUnresponsive);
    puts("RFB versions, shared viewer, auth/refusal policy and stalled handshake deadlines passed");
    return 0;
}
