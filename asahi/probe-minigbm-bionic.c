/* Graphics stage 3e: an Android (Bionic) program that loads the Android-built, patched minigbm,
 * allocates a buffer on the Apple display card node through gbm_bo_create, maps its dma-buf and
 * checks a CPU write/read. No GPU work yet; the Honeykrisp import was tested in stage 3d on glibc. */
#include <xf86drm.h>
#include <drm_fourcc.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define W 256
#define H 256
#define USE_SCANOUT_RENDERING_LINEAR ((1u << 0) | (1u << 2) | (1u << 4)) /* minigbm gbm.h values */

static void sync_buf(int fd, uint64_t flags) {
    struct dma_buf_sync s = {.flags = flags};
    if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &s)) printf("MGBM_BIONIC note DMA_BUF_IOCTL_SYNC errno=%d\n", errno);
}

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    if (argc != 2) { fprintf(stderr, "usage: %s /path/to/libminigbm.so\n", argv[0]); return 64; }
    printf("MGBM_BIONIC page_size=%ld\n", sysconf(_SC_PAGESIZE));
    void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!lib) { printf("MGBM_BIONIC dlopen=FAIL %s\n", dlerror()); return 2; }
    puts("MGBM_BIONIC dlopen=PASS");
    void *(*create_device)(int) = (void *(*)(int))dlsym(lib, "gbm_create_device");
    void *(*bo_create)(void *, uint32_t, uint32_t, uint32_t, uint32_t) =
        (void *(*)(void *, uint32_t, uint32_t, uint32_t, uint32_t))dlsym(lib, "gbm_bo_create");
    int (*bo_fd)(void *) = (int (*)(void *))dlsym(lib, "gbm_bo_get_fd");
    uint32_t (*bo_stride)(void *) = (uint32_t (*)(void *))dlsym(lib, "gbm_bo_get_stride");
    void (*bo_destroy)(void *) = (void (*)(void *))dlsym(lib, "gbm_bo_destroy");
    void (*dev_destroy)(void *) = (void (*)(void *))dlsym(lib, "gbm_device_destroy");
    if (!create_device || !bo_create || !bo_fd || !bo_stride || !bo_destroy || !dev_destroy) {
        puts("MGBM_BIONIC missing gbm entry points"); return 3; }

    int card = -1;
    char path[64];
    for (int i = 0; i < 16 && card < 0; i++) {
        snprintf(path, sizeof path, "/dev/dri/card%d", i);
        int fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd < 0) { if (errno != ENOENT) printf("MGBM_BIONIC node %s open_errno=%d\n", path, errno); continue; }
        drmVersionPtr v = drmGetVersion(fd);
        int ok = v && !strcmp(v->name, "apple");
        if (v) drmFreeVersion(v);
        if (ok) { card = fd; printf("MGBM_BIONIC node %s driver=apple\n", path); } else close(fd);
    }
    if (card < 0) { puts("MGBM_BIONIC no openable apple card node"); return 4; }
    void *gbm = create_device(card);
    printf("MGBM_BIONIC gbm_create_device=%s\n", gbm ? "PASS" : "FAIL");
    if (!gbm) return 5;
    void *bo = bo_create(gbm, W, H, DRM_FORMAT_ARGB8888, USE_SCANOUT_RENDERING_LINEAR);
    if (!bo) { printf("MGBM_BIONIC gbm_bo_create=FAIL errno=%d\n", errno); return 6; }
    int fd = bo_fd(bo);
    uint32_t stride = bo_stride(bo);
    off_t size = fd >= 0 ? lseek(fd, 0, SEEK_END) : -1;
    if (fd >= 0) lseek(fd, 0, SEEK_SET);
    printf("MGBM_BIONIC gbm_bo_create=PASS fd=%d stride=%u dmabuf_size=%lld\n", fd, stride, (long long)size);
    if (fd < 0 || size <= 0) return 7;
    uint32_t *p = mmap(NULL, (size_t)size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) { printf("MGBM_BIONIC mmap=FAIL errno=%d\n", errno); return 8; }
    sync_buf(fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
    for (uint32_t y = 0; y < H; y++) for (uint32_t x = 0; x < W; x++) p[(size_t)y * stride / 4 + x] = 0xff000000u | (y << 8) | x;
    sync_buf(fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);
    sync_buf(fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
    unsigned good = 0;
    for (uint32_t y = 0; y < H; y++) for (uint32_t x = 0; x < W; x++) good += p[(size_t)y * stride / 4 + x] == (0xff000000u | (y << 8) | x);
    sync_buf(fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    printf("MGBM_BIONIC cpu_readback=%s pixels=%u/%u\n", good == W * H ? "PASS" : "FAIL", good, W * H);
    munmap(p, (size_t)size);
    close(fd);
    bo_destroy(bo);
    dev_destroy(gbm);
    close(card);
    return good == W * H ? 0 : 9;
}
