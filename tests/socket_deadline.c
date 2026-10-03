#include "../include-spi/TVNCSocket.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    assert(listener >= 0);
    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    assert(bind(listener, (struct sockaddr *)&address, sizeof(address)) == 0);
    socklen_t length = sizeof(address);
    assert(getsockname(listener, (struct sockaddr *)&address, &length) == 0);
    int port = ntohs(address.sin_port);
    assert(listen(listener, 1) == 0);

    int client = TVNCConnectLoopback(port, 0.1);
    assert(client >= 0);
    assert((fcntl(client, F_GETFL, 0) & O_NONBLOCK) == 0);
    int peer = accept(listener, NULL, NULL);
    assert(peer >= 0);
    close(peer);
    close(client);

    // Fill a real TCP accept backlog and verify timeout rather than merely
    // testing connection-refused (which returns immediately in either version).
    int clients[512];
    int count = 0;
    while (count < 512) {
        client = TVNCConnectLoopback(port, 0.02);
        if (client < 0) break;
        clients[count++] = client;
    }
    assert(count < 512);
    assert(errno == ETIMEDOUT);
    double before = TVNCMonotonicSeconds();
    assert(TVNCConnectLoopback(port, 0.1) == -1);
    assert(errno == ETIMEDOUT);
    double elapsed = TVNCMonotonicSeconds() - before;
    assert(elapsed >= 0.09 && elapsed < 0.5);
    while (count > 0) close(clients[--count]);
    close(listener);
    assert(TVNCConnectLoopback(port, 0.1) == -1);
    printf("Loopback success, refused port and saturated backlog passed (%.3fs timeout)\n", elapsed);
    return 0;
}
