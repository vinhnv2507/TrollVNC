#pragma once
#include <spawn.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>

// The screen server remains mobile. Only the signed update helper runs as root,
// using the same persona API as TaskProcess when it starts the service manager.
struct TVUpdatePersonaAPI {
    int (*set)(posix_spawnattr_t *, uid_t, uint32_t);
    int (*uid)(posix_spawnattr_t *, uid_t);
    int (*gid)(posix_spawnattr_t *, gid_t);
};
static inline TVUpdatePersonaAPI TVUpdatePersonaFunctions() {
    return {
        (int (*)(posix_spawnattr_t *, uid_t, uint32_t))dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_np"),
        (int (*)(posix_spawnattr_t *, uid_t))dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_uid_np"),
        (int (*)(posix_spawnattr_t *, gid_t))dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_gid_np")
    };
}
static inline int TVUpdateRootAttributes(posix_spawnattr_t *attr, TVUpdatePersonaAPI api) {
    if (!api.set || !api.uid || !api.gid) return ENOSYS;
    // set_persona_np adds an internal spawn flag. Setting the flags afterwards
    // would erase it and silently launch the child with the parent's mobile uid.
    int error = posix_spawnattr_setflags(attr, POSIX_SPAWN_CLOEXEC_DEFAULT);
    if (!error) error = api.set(attr, 99, 1); // POSIX_SPAWN_PERSONA_FLAGS_OVERRIDE
    if (!error) error = api.uid(attr, 0);
    if (!error) error = api.gid(attr, 0);
    return error;
}
