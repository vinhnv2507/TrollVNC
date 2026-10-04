#include "../include-spi/TVNCTouchLockPolicy.h"
#include <cassert>
#include <cstdio>
#include <vector>

struct Event {
    uint64_t sender;
    bool digitizer;
    bool home;
    std::vector<Event *> children;
};
struct Adapter {
    uint64_t sender(Event *e) const { return e->sender; }
    bool digitizer(Event *e) const { return e->digitizer; }
    bool home(Event *e) const { return e->home; }
    unsigned childCount(Event *e) const { return (unsigned)e->children.size(); }
    Event *child(Event *e, unsigned index) const { return e->children[index]; }
};
static bool blocked(Event &event, bool enabled = true) {
    return TVNCTouchLockBlocks(enabled, TVNCTouchLockSources(&event, Adapter{}));
}
int main() {
    // Unknown/zero sender and device-specific touch/Home sources all block.
    for (uint64_t sender : {0ULL, 0x1000001d4ULL, 0x100000222ULL}) {
        Event touch{sender, true, false, {}};
        Event home{sender, false, true, {}};
        Event other{sender, false, false, {}};
        assert(blocked(touch) && blocked(home));
        assert(!blocked(touch, false) && !blocked(home, false));
        assert(!blocked(other)); // Volume, power, sensors and vendor markers.
    }
    for (uint64_t sender : {kTvRemoteSenderLegacy, kTvRemoteSenderModern}) {
        Event finger{0, true, false, {}};
        Event hand{sender, true, false, {&finger}};
        Event home{sender, false, true, {}};
        assert(!blocked(hand) && !blocked(home));
        Event wrapper{0, false, false, {&hand}};
        assert(!blocked(wrapper));
        Event physical{0x100000222ULL, true, false, {}};
        wrapper.children.push_back(&physical);
        assert(blocked(wrapper)); // Remote sibling cannot exempt ghost touch.
        hand.children.push_back(&physical);
        assert(blocked(hand)); // Explicit physical child overrides inheritance.
    }
    assert(TVNCTouchLockSources((Event *)nullptr, Adapter{}) == TVNCTouchSourceNone);
    puts("Touch lock: noisy/unknown sources blocked; nested remote input and unlock preserved");
}
