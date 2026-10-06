#include "../src/TVNCLocalUpdate.h"
#include "../src/TVNCUpdateRootSpawn.h"
#include <cassert>

static int calls;
static int failAt;
static int persona(posix_spawnattr_t *, uid_t id, uint32_t flags) {
    assert(id == 99 && flags == 1);
    return ++calls == failAt ? EPERM : 0;
}
static int rootUID(posix_spawnattr_t *, uid_t id) {
    assert(id == 0); return ++calls == failAt ? EPERM : 0;
}
static int rootGID(posix_spawnattr_t *, gid_t id) {
    assert(id == 0); return ++calls == failAt ? EPERM : 0;
}
int main() {
    posix_spawnattr_t attributes;
    assert(posix_spawnattr_init(&attributes) == 0);
    for (failAt = 0; failAt <= 3; ++failAt) {
        calls = 0;
        assert(TVUpdateRootAttributes(&attributes, {persona, rootUID, rootGID}) == (failAt ? EPERM : 0));
        assert(calls == (failAt ? failAt : 3)); // Never continue after a failed privilege request.
    }
    calls = 0;
    assert(TVUpdateRootAttributes(&attributes, {nullptr, rootUID, rootGID}) == ENOSYS);
    assert(TVUpdateRootAttributes(&attributes, {persona, nullptr, rootGID}) == ENOSYS);
    assert(TVUpdateRootAttributes(&attributes, {persona, rootUID, nullptr}) == ENOSYS);
    assert(calls == 0);
    posix_spawnattr_destroy(&attributes);
    for (const char *ip : {"10.1.1.1", "172.16.0.1", "172.31.255.254", "192.168.2.3", "169.254.1.2"})
        assert(TVUpdateLANHost(ip));
    for (const char *ip : {"172.15.1.1", "172.32.0.1", "8.8.8.8", "127.0.0.1", "localhost", "10.1.1.999", "10.1.1.1.evil"})
        assert(!TVUpdateLANHost(ip));
    assert(TVUpdateHex(std::string(32, 'a'), 32));
    assert(!TVUpdateHex(std::string(32, '/'), 32));
    assert(!TVUpdateHex(std::string(31, 'a'), 32));
    assert(TVUpdateVersion("4.18")); assert(TVUpdateVersion("4.18.1"));
    for (const char *v : {"", ".4", "4.", "4..18", "4.18/evil", "4.18\n"}) assert(!TVUpdateVersion(v));
}
