#pragma once
#include <arpa/inet.h>
#include <string>

// Updates come only from a numeric IPv4 LAN address; never follow redirects.
static inline bool TVUpdateLANHost(const char *host) {
    struct in_addr address = {};
    if (!host || inet_pton(AF_INET, host, &address) != 1) return false;
    uint32_t ip = ntohl(address.s_addr);
    return (ip >> 24) == 10 || (ip >> 20) == 0xac1 ||
           (ip >> 16) == 0xc0a8 || (ip >> 16) == 0xa9fe;
}
static inline bool TVUpdateHex(const std::string &value, size_t length) {
    if (value.size() != length) return false;
    for (char c : value)
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}
static inline bool TVUpdateVersion(const std::string &value) {
    if (value.empty() || value.size() > 32) return false;
    bool digit = false;
    for (char c : value) {
        if (c >= '0' && c <= '9') digit = true;
        else if (c == '.' && digit) digit = false;
        else return false;
    }
    return digit;
}
