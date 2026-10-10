/*
 * Test-only stand-in for libselinux's selinux_android_setcon(), preloaded into the
 * image's zygote by asahi/smoke-zygote.py. Original project file.
 *
 * zygote calls selinux_android_setcon("u:r:system_server:s0") for system_server without
 * first checking whether SELinux is enabled (selinux_android_setcontext, called just
 * before, does check and returns 0 when it is not). In the smoke-test sandbox Android's
 * libselinux sees no selinuxfs, but the write still reaches the host's /proc attr file,
 * where Fedora's policy rejects the Android label. This stub returns success without
 * writing, i.e. the same result selinux_android_setcontext already gives here. No
 * Android SELinux policy is loaded or emulated, and the host's SELinux is unchanged.
 * Built with -nostdlib: no library dependencies.
 */
__attribute__((visibility("default"))) int selinux_android_setcon(const char *context) {
    (void)context;
    return 0;
}
