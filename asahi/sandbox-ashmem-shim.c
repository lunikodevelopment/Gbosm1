/*
 * Test-only stand-in for libcutils' ashmem_* functions, preloaded into the image's zygote by
 * asahi/smoke-zygote.py. Original project file.
 *
 * Android 17's libcutils creates shared memory with memfd only when the kernel also accepts
 * ashmem ioctls on memfds (an Android kernel addition), and otherwise opens /dev/ashmem<boot_id>.
 * Fedora's kernel has neither (ashmem left mainline Linux in 5.18), so system_server stopped at
 * "Failed to create ashmem" (stage B6c). These functions follow the memfd branch of AOSP's
 * libcutils/ashmem-dev.cpp (memfd_create_region, memfd_set_prot_region, the memfd cases of
 * ashmem_get_size_region / pin / unpin / valid), implemented with raw aarch64 system calls so the
 * library has no dependencies. Differences: errno is not set on failure, and ashmem_valid() accepts
 * any descriptor that answers F_GET_SEALS (any memfd).
 */
typedef unsigned long size_t;

static long sys(long n, long a, long b, long c) {
    register long x8 __asm__("x8") = n;
    register long x0 __asm__("x0") = a;
    register long x1 __asm__("x1") = b;
    register long x2 __asm__("x2") = c;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
    return x0;
}

enum { NR_FCNTL = 25, NR_FTRUNCATE = 46, NR_CLOSE = 57, NR_FSTAT = 80, NR_MEMFD_CREATE = 279 };
enum { F_ADD_SEALS = 1033, F_GET_SEALS = 1034, F_SEAL_SHRINK = 2, F_SEAL_GROW = 4, F_SEAL_FUTURE_WRITE = 16 };
enum { MFD_CLOEXEC = 1, MFD_ALLOW_SEALING = 2, PROT_WRITE = 2 };

#define EXPORT __attribute__((visibility("default")))

EXPORT int ashmem_valid(int fd) {
    return sys(NR_FCNTL, fd, F_GET_SEALS, 0) >= 0;
}

EXPORT int ashmem_create_region(const char *name, size_t size) {
    long fd = sys(NR_MEMFD_CREATE, (long)(name ? name : "none"), MFD_CLOEXEC | MFD_ALLOW_SEALING, 0);
    if (fd < 0) return -1;
    if (sys(NR_FTRUNCATE, fd, (long)size, 0) < 0 || sys(NR_FCNTL, fd, F_ADD_SEALS, F_SEAL_GROW | F_SEAL_SHRINK) < 0) {
        sys(NR_CLOSE, fd, 0, 0);
        return -1;
    }
    return (int)fd;
}

EXPORT int ashmem_set_prot_region(int fd, int prot) {
    long seals = sys(NR_FCNTL, fd, F_GET_SEALS, 0);
    if (seals < 0) return -1;
    if (seals & F_SEAL_FUTURE_WRITE) return (prot & PROT_WRITE) ? -1 : 0;  /* already write protected */
    if (prot & PROT_WRITE) return 0;
    return sys(NR_FCNTL, fd, F_ADD_SEALS, F_SEAL_FUTURE_WRITE) < 0 ? -1 : 0;
}

EXPORT int ashmem_pin_region(int fd, size_t offset, size_t len) {
    (void)fd; (void)offset; (void)len;
    return 0;  /* memfd regions are never purged (ASHMEM_NOT_PURGED) */
}

EXPORT int ashmem_unpin_region(int fd, size_t offset, size_t len) {
    (void)fd; (void)offset; (void)len;
    return 0;
}

EXPORT int ashmem_get_size_region(int fd) {
    long st[16];  /* struct stat on aarch64 is 128 bytes; st_size is at byte 48 */
    if (sys(NR_FSTAT, fd, (long)st, 0) < 0) return -1;
    return (int)st[6];
}
