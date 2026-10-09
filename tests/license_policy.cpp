#include "../src/TVNCLicensePolicy.h"
#include <cassert>
#include <iostream>

int main() {
    TVNCLicensePolicy trial;
    trial.trial(1000, 1000, 0);
    assert(trial.allowed(1000, 0));
    assert(trial.remaining(1000, 0) == 600);
    assert(trial.remaining(1599, 599) == 1);
    assert(!trial.allowed(1600, 600)); // Exact boundary, no extra second.
    assert(!trial.allowed(1100, 601));
    trial.trial(1000, 1100, 601); // Reload/reopen cannot restart the trial.
    assert(!trial.allowed(1100, 601));

    TVNCLicensePolicy reopened;
    reopened.trial(1000, 1300, 0);
    assert(reopened.remaining(1300, 0) == 300);
    assert(!reopened.allowed(1001, 300)); // Monotonic expiry despite rollback.

    TVNCLicensePolicy license;
    license.license(1030, 1000, 0);
    assert(license.remaining(1029, 29) == 1);
    assert(!license.allowed(1030, 30));
    assert(!license.allowed(1000, 31));
    license.license(1200, 1031, 31); // A new valid license restores service.
    assert(license.allowed(1031, 31));
    assert(!license.allowed(1200, 200));
    license.license(0, 1200, 200);
    assert(license.allowed(9999999, 9999)); // Forever is explicit exp=0.

    TVNCLicensePolicy expiredOnStartup;
    expiredOnStartup.license(1000, 1001, 0);
    assert(!expiredOnStartup.allowed(1001, 0));
    assert(expiredOnStartup.kind() == TVNCLicensePolicy::Kind::License);
    TVNCLicensePolicy corrupt;
    corrupt.trial(0, 1000, 0);
    assert(!corrupt.allowed(1000, 0));
    corrupt.license(-1, 1000, 0);
    assert(!corrupt.allowed(1000, 0));

    TVNCLicensePolicy jump;
    jump.license(2000, 1000, 0);
    assert(jump.allowed(1999, 1));
    assert(!jump.allowed(1000, 2)); // A wall-clock jump cannot freeze elapsed time.
    std::cout << "License policy: trial/expiry/renewal/forever/rollback passed\n";
}
