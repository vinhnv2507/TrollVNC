#pragma once

#include <stdint.h>

static const uint64_t kTvRemoteSenderLegacy = 0x8000000817319371ULL;
static const uint64_t kTvRemoteSenderModern = 0x8000000817319372ULL;

enum TVNCTouchSource {
    TVNCTouchSourceNone = 0,
    TVNCTouchSourcePhysical = 1,
    TVNCTouchSourcePhysicalHome = 2,
    TVNCTouchSourceRemote = 4,
    TVNCTouchSourceHome = 8,
    TVNCTouchSourcePhysicalPower = 16,
    TVNCTouchSourcePhysicalPowerDown = 32,
    TVNCTouchSourcePhysicalPowerUp = 64
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
    if (adapter.power(event)) {
        if (remote) {
            result |= TVNCTouchSourceRemote;
        } else {
            result |= TVNCTouchSourcePhysicalPower;
            result |= adapter.down(event) ? TVNCTouchSourcePhysicalPowerDown
                                          : TVNCTouchSourcePhysicalPowerUp;
        }
    }
    for (unsigned index = 0; index < adapter.childCount(event); ++index)
        result |= TVNCTouchLockSources(adapter.child(event, index), adapter, sender);
    return result;
}

static inline bool TVNCTouchLockBlocks(bool enabled, unsigned sources) {
    return enabled && (sources & (TVNCTouchSourcePhysical | TVNCTouchSourcePhysicalHome |
                                  TVNCTouchSourcePhysicalPower));
}

static constexpr double kTvPowerEscapeHoldSeconds = 3.0;

struct TVNCPowerDecision {
    bool consume;
    uint64_t timer; // Nonzero only for a fresh physical press under protection.
};

// Owned by the serial HID queue. lockState packs an epoch and enabled bit;
// changing it invalidates old holds even for a rapid off/on or repeated "on".
class TVNCPowerHold {
    uint64_t state_ = 0;
    uint64_t press_ = 0;
    bool down_ = false;
    bool consumeRelease_ = false;
    bool armed_ = false;
    double deadline_ = 0;

    void synchronize(uint64_t state) {
        if (state_ != state) {
            armed_ = false;
            state_ = state;
        }
    }

public:
    TVNCPowerDecision observe(unsigned sources, uint64_t state, double now) {
        synchronize(state);
        bool enabled = (state & 1) != 0;
        // An ambiguous packet containing both edges is treated as a release:
        // it cannot start a delayed unlock.
        if (sources & TVNCTouchSourcePhysicalPowerUp) {
            bool consume = down_ ? consumeRelease_ : enabled;
            down_ = consumeRelease_ = armed_ = false;
            ++press_;
            return {consume, 0};
        }
        if (!(sources & TVNCTouchSourcePhysicalPowerDown)) return {false, 0};
        if (down_) return {consumeRelease_, 0}; // Repeats never extend the hold.
        down_ = true;
        consumeRelease_ = armed_ = enabled;
        deadline_ = now + kTvPowerEscapeHoldSeconds;
        ++press_;
        return {enabled, armed_ ? press_ : 0};
    }

    bool ready(uint64_t timer, uint64_t state, double now) {
        synchronize(state);
        return armed_ && down_ && (state & 1) && timer == press_ && now >= deadline_;
    }

    void finish(uint64_t timer) {
        if (timer == press_) armed_ = false;
        // Keep consuming repeats and the paired release after unlocking, so
        // this escape gesture cannot also lock the screen or invoke Siri.
    }
};
