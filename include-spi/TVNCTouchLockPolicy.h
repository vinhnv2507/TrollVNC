#pragma once

#include <stdint.h>

static const uint64_t kTvRemoteSenderLegacy = 0x8000000817319371ULL;
static const uint64_t kTvRemoteSenderModern = 0x8000000817319372ULL;

enum TVNCTouchSource {
    TVNCTouchSourceNone = 0,
    TVNCTouchSourcePhysical = 1,
    TVNCTouchSourcePhysicalHome = 2,
    TVNCTouchSourceRemote = 4,
    TVNCTouchSourceHome = 8
};

// Inspect each node's origin. Untagged children inherit their parent's sender,
// but a physical child must not be exempted by a remote sibling or parent.
template <typename Event, typename Adapter>
static unsigned TVNCTouchLockSources(Event event, const Adapter &adapter,
                                    uint64_t inheritedSender = 0) {
    if (!event) return TVNCTouchSourceNone;
    uint64_t sender = adapter.sender(event);
    if (!sender) sender = inheritedSender;
    bool remote = sender == kTvRemoteSenderLegacy || sender == kTvRemoteSenderModern;
    unsigned result = TVNCTouchSourceNone;
    if (adapter.digitizer(event))
        result |= remote ? TVNCTouchSourceRemote : TVNCTouchSourcePhysical;
    if (adapter.home(event)) {
        result |= TVNCTouchSourceHome;
        result |= remote ? TVNCTouchSourceRemote : TVNCTouchSourcePhysicalHome;
    }
    for (unsigned index = 0; index < adapter.childCount(event); ++index)
        result |= TVNCTouchLockSources(adapter.child(event, index), adapter, sender);
    return result;
}

static inline bool TVNCTouchLockBlocks(bool enabled, unsigned sources) {
    return enabled && (sources & (TVNCTouchSourcePhysical | TVNCTouchSourcePhysicalHome));
}
