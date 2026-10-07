#include "../include-spi/TVNCTouchLockPolicy.h"
#include <cassert>
#include <cstdio>
#include <vector>

struct Event {
    uint64_t sender;
    bool digitizer;
    bool home;
    std::vector<Event *> children;
    bool power = false;
    bool down = false;
};
struct Adapter {
    uint64_t sender(Event *e) const { return e->sender; }
    bool digitizer(Event *e) const { return e->digitizer; }
    bool home(Event *e) const { return e->home; }
    bool power(Event *e) const { return e->power; }
    bool down(Event *e) const { return e->down; }
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
        Event power{sender, false, false, {}, true, true};
        assert(blocked(touch) && blocked(home) && blocked(power));
        assert(!blocked(touch, false) && !blocked(home, false));
        assert(!blocked(power, false));
        assert(!blocked(other)); // Volume, sensors and vendor markers.
    }
    for (uint64_t sender : {kTvRemoteSenderLegacy, kTvRemoteSenderModern}) {
        Event finger{0, true, false, {}};
        Event hand{sender, true, false, {&finger}};
        Event home{sender, false, true, {}};
        Event power{sender, false, false, {}, true, true};
        assert(!blocked(hand) && !blocked(home) && !blocked(power));
        Event inheritedPower{0, false, false, {}, true, false};
        power.children.push_back(&inheritedPower);
        assert(TVNCTouchLockSources(&power, Adapter{}) == TVNCTouchSourceRemote);
        Event physicalPower{123, false, false, {}, true, false};
        power.children.push_back(&physicalPower);
        unsigned mixed = TVNCTouchLockSources(&power, Adapter{});
        assert(mixed & TVNCTouchSourcePhysicalPowerUp);
        assert(!(mixed & TVNCTouchSourcePhysicalPowerDown));
        assert(blocked(power));
        Event wrapper{0, false, false, {&hand}};
        assert(!blocked(wrapper));
        Event physical{0x100000222ULL, true, false, {}};
        wrapper.children.push_back(&physical);
        assert(blocked(wrapper)); // Remote sibling cannot exempt ghost touch.
        hand.children.push_back(&physical);
        assert(blocked(hand)); // Explicit physical child overrides inheritance.
    }
    assert(TVNCTouchLockSources((Event *)nullptr, Adapter{}) == TVNCTouchSourceNone);
    // Short physical presses are consumed without leaving a delayed unlock.
    constexpr unsigned down = TVNCTouchSourcePhysicalPower | TVNCTouchSourcePhysicalPowerDown;
    constexpr unsigned up = TVNCTouchSourcePhysicalPower | TVNCTouchSourcePhysicalPowerUp;
    TVNCPowerHold hold;
    auto shortPress = hold.observe(down, 1, 10);
    assert(shortPress.consume && shortPress.timer);
    assert(!hold.ready(shortPress.timer, 1, 12.999));
    assert(hold.observe(up, 1, 12.999).consume);
    assert(!hold.ready(shortPress.timer, 1, 30));

    // A repeat does not restart the three-second deadline. After escape, its
    // repeat/release stay consumed; the next physical press works normally.
    auto longPress = hold.observe(down, 1, 40);
    auto repeat = hold.observe(down, 1, 42.9);
    assert(repeat.consume && !repeat.timer);
    assert(!hold.ready(shortPress.timer, 1, 43));
    assert(hold.ready(longPress.timer, 1, 43));
    hold.finish(longPress.timer);
    assert(!hold.ready(longPress.timer, 1, 44));
    assert(hold.observe(down, 2, 44).consume);
    assert(hold.observe(up, 2, 45).consume);
    assert(!hold.observe(down, 2, 46).consume);
    assert(!hold.observe(up, 2, 47).consume);

    // Remote Power never arms or cancels a physical hold, including zero-sender
    // children tagged by their parent, and does not unlock by itself.
    assert(!hold.observe(TVNCTouchSourceRemote, 3, 50).timer);
    assert(!hold.ready(0, 3, 100));
    auto physical = hold.observe(down, 3, 101);
    assert(!hold.observe(TVNCTouchSourceRemote, 3, 102).consume);
    assert(hold.ready(physical.timer, 3, 104));
    assert(hold.observe(up, 3, 104).consume);

    // A PC off/on, or even a new on request, invalidates stale holds. Repeated
    // down after relocking cannot arm until the user releases and presses again.
    auto stale = hold.observe(down, 3, 110);
    assert(!hold.ready(stale.timer, 7, 120));
    assert(!hold.observe(down, 7, 121).timer);
    assert(hold.observe(up, 7, 122).consume);
    auto fresh = hold.observe(down, 7, 123);
    assert(fresh.timer && hold.ready(fresh.timer, 7, 126));
    assert(!hold.ready(fresh.timer, 9, 127));
    assert(hold.observe(up, 9, 128).consume);

    // Enabling lock during a press that already went to iOS must balance that
    // original key pair, never interpret its repeat as a fresh escape gesture.
    assert(!hold.observe(down, 10, 130).consume);
    auto inFlight = hold.observe(down, 11, 131);
    assert(!inFlight.consume && !inFlight.timer);
    assert(!hold.observe(up, 11, 132).consume);
    assert(hold.observe(up, 11, 133).consume); // Stray release while protected.

    // Ambiguous composite packets cannot start an escape timer; unrelated
    // touch/Home/volume packets cannot arm one either.
    assert(!hold.observe(down | up, 11, 140).timer);
    assert(!hold.observe(TVNCTouchSourcePhysicalHome, 11, 141).timer);
    assert(!hold.ready(0, 11, 150));
    puts("Touch lock: noisy/unknown sources blocked; nested remote input and unlock preserved");
    puts("Power protection: 3s escape, short/repeated presses, balanced release and stale timers verified");
}
