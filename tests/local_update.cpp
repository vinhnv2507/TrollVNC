#include "../src/TVNCLocalUpdate.h"
#include <cassert>
int main() {
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
