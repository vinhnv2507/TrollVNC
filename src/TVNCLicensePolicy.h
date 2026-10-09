#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

// The caller serializes access. Once observed, elapsed time cannot be undone
// by setting the wall clock back or reloading the same license/trial record.
class TVNCLicensePolicy {
public:
    enum class Kind { Denied, Trial, License };
    void trial(int64_t started, double wall, double monotonic) {
        kind_ = started > 0 ? Kind::Trial : Kind::Denied;
        deadline_ = started > 0 ? static_cast<double>(started) + 600.0 : 0;
        expired_ = false;
        allowed(wall, monotonic);
    }
    void license(int64_t expiry, double wall, double monotonic) {
        kind_ = expiry >= 0 ? Kind::License : Kind::Denied;
        deadline_ = static_cast<double>(expiry);
        expired_ = false;
        allowed(wall, monotonic);
    }
    bool allowed(double wall, double monotonic) {
        advance(wall, monotonic);
        if (kind_ == Kind::Denied || expired_) return false;
        if (kind_ == Kind::License && deadline_ == 0) return true;
        if (now_ >= deadline_) expired_ = true;
        return !expired_;
    }
    int64_t remaining(double wall, double monotonic) {
        if (!allowed(wall, monotonic) || deadline_ == 0) return 0;
        return static_cast<int64_t>(std::ceil(deadline_ - now_));
    }
    Kind kind() const { return kind_; }
    double observedTime() const { return now_; }
private:
    void advance(double wall, double monotonic) {
        if (!initialized_) {
            now_ = wall;
            initialized_ = true;
        } else {
            now_ = std::max(wall, now_ + std::max(0.0, monotonic - monotonic_));
        }
        monotonic_ = monotonic;
    }
    Kind kind_ = Kind::Denied;
    bool initialized_ = false;
    bool expired_ = false;
    double deadline_ = 0;
    double now_ = 0;
    double monotonic_ = 0;
};
