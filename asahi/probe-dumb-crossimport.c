/* Graphics stage 3c: can the display driver take buffers allocated on the GPU driver?
 * Allocates dumb buffers (256x256 and the panel's 2560x1600), exports them as dma-bufs,
 * imports them into the other driver and tries to wrap them in a framebuffer with ADDFB2.
 * Also asks whether the GPU's render node can create dumb buffers at all.
 * No modeset, no commit, no DRM master, no Vulkan; framebuffers are removed again. */
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static int open_by_driver(const char *pattern, int first, int last, const char *driver) {
    char path[64];
    for (int i = first; i < last; i++) {
        snprintf(path, sizeof path, pattern, i);
        int fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd < 0) continue;
        drmVersionPtr v = drmGetVersion(fd);
        int ok = v && !strcmp(v->name, driver);
        if (v) drmFreeVersion(v);
        if (ok) { printf("STAGE3C node %s driver=%s\n", path, driver); return fd; }
        close(fd);
    }
    printf("STAGE3C no openable %s node for driver %s\n", pattern, driver);
    return -1;
}

static int dumb(int fd, uint32_t w, uint32_t h, struct drm_mode_create_dumb *c) {
    memset(c, 0, sizeof *c);
    c->width = w; c->height = h; c->bpp = 32;
    return drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, c);
}

static void addfb(const char *tag, int fd, uint32_t handle, uint32_t w, uint32_t h, uint32_t pitch) {
    uint32_t handles[4] = {handle}, pitches[4] = {pitch}, offsets[4] = {0}, fb = 0;
    int r = drmModeAddFB2(fd, w, h, DRM_FORMAT_XRGB8888, handles, pitches, offsets, &fb, 0);
    printf("STAGE3C %s addfb2=%s errno=%d\n", tag, r ? "FAIL" : "PASS", r ? errno : 0);
    if (!r) drmModeRmFB(fd, fb);
}

static void one(int apple, int asahi, uint32_t w, uint32_t h) {
    struct drm_mode_create_dumb c;
    printf("STAGE3C size=%ux%u\n", w, h);
    if (dumb(asahi, w, h, &c)) { printf("STAGE3C asahi dumb_create=FAIL errno=%d\n", errno); }
    else {
        printf("STAGE3C asahi dumb_create=PASS pitch=%u size=%llu\n", c.pitch, (unsigned long long)c.size);
        int prime = -1;
        if (drmPrimeHandleToFD(asahi, c.handle, DRM_CLOEXEC | DRM_RDWR, &prime)) printf("STAGE3C asahi export=FAIL errno=%d\n", errno);
        else {
            uint32_t h2 = 0;
            if (drmPrimeFDToHandle(apple, prime, &h2)) printf("STAGE3C asahi-buffer import_into_apple=FAIL errno=%d\n", errno);
            else {
                printf("STAGE3C asahi-buffer import_into_apple=PASS\n");
                addfb("asahi-buffer", apple, h2, w, h, c.pitch);
                struct drm_gem_close gc = {.handle = h2};
                drmIoctl(apple, DRM_IOCTL_GEM_CLOSE, &gc);
            }
            close(prime);
        }
        struct drm_mode_destroy_dumb d = {.handle = c.handle};
        drmIoctl(asahi, DRM_IOCTL_MODE_DESTROY_DUMB, &d);
    }
    if (dumb(apple, w, h, &c)) { printf("STAGE3C apple dumb_create=FAIL errno=%d\n", errno); return; }
    printf("STAGE3C apple dumb_create=PASS pitch=%u size=%llu\n", c.pitch, (unsigned long long)c.size);
    addfb("apple-buffer", apple, c.handle, w, h, c.pitch);
    struct drm_mode_destroy_dumb d = {.handle = c.handle};
    drmIoctl(apple, DRM_IOCTL_MODE_DESTROY_DUMB, &d);
}

int main(void) {
    setbuf(stdout, NULL);
    int apple = open_by_driver("/dev/dri/card%d", 0, 16, "apple");
    int asahi = open_by_driver("/dev/dri/card%d", 0, 16, "asahi");
    int render = open_by_driver("/dev/dri/renderD%d", 128, 192, "asahi");
    if (render >= 0) {
        struct drm_mode_create_dumb c;
        if (dumb(render, 256, 256, &c)) printf("STAGE3C render-node dumb_create=FAIL errno=%d\n", errno);
        else {
            printf("STAGE3C render-node dumb_create=PASS pitch=%u size=%llu\n", c.pitch, (unsigned long long)c.size);
            struct drm_mode_destroy_dumb d = {.handle = c.handle};
            drmIoctl(render, DRM_IOCTL_MODE_DESTROY_DUMB, &d);
        }
    }
    if (apple < 0 || asahi < 0) { puts("STAGE3C summary UNAVAILABLE needs both card nodes"); return 0; }
    one(apple, asahi, 256, 256);
    one(apple, asahi, 2560, 1600);
    puts("STAGE3C done");
    return 0;
}
