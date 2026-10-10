/* Graphics stage 3b: which non-GBM allocations Honeykrisp can use. Allocates 256x256
 * ARGB8888 linear dma-bufs three ways without Mesa's GBM, imports each into Honeykrisp as a
 * DRM_FORMAT_MOD_LINEAR image, and checks both directions: CPU write then GPU read, and GPU
 * clear then CPU read (the CPU view is an mmap of the dma-buf with DMA_BUF_IOCTL_SYNC).
 *   dumb-apple : DRM_IOCTL_MODE_CREATE_DUMB on the display driver's card node
 *   dumb-asahi : the same ioctl on the GPU driver's card node
 *   dma-heap   : DMA_HEAP_IOCTL_ALLOC from each readable heap under /dev/dma_heap
 *   minigbm-apple : with a library path argument, a patched minigbm's gbm_bo_create on the
 *                   display card node (its dumb backend), then the same checks
 * Opens no node for modesetting and never takes DRM master. */
#include <vulkan/vulkan.h>
#include <xf86drm.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <dirent.h>
#include <dlfcn.h>

/* Older headers lack the enum; 26 is what Honeykrisp reported in the completed probe. */
#ifndef VK_DRIVER_ID_MESA_HONEYKRISP
#define VK_DRIVER_ID_MESA_HONEYKRISP ((VkDriverId)26)
#endif

#define W 256
#define H 256
/* B8G8R8A8_UNORM clear of (0x20, 0x40, 0x80, 0xff)/255 stored as DRM ARGB8888. */
#define EXPECT 0xff204080u

static VkInstance inst;
static VkPhysicalDevice phys;
static VkDevice dev;
static VkQueue queue;
static uint32_t qfam;
static PFN_vkGetMemoryFdPropertiesKHR get_fd_props;
static PFN_vkGetImageDrmFormatModifierPropertiesEXT get_image_modifier;

#define VK(call) do { VkResult r_ = (call); if (r_ != VK_SUCCESS) { \
    printf("STAGE3B vulkan_error %s = %d\n", #call, r_); return -1; } } while (0)


static int has_ext(const VkExtensionProperties *e, uint32_t n, const char *name) {
    for (uint32_t i = 0; i < n; i++) if (!strcmp(e[i].extensionName, name)) return 1;
    return 0;
}

static int init_vulkan(void) {
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                            .pApplicationName = "gbos stage3b probe", .apiVersion = VK_API_VERSION_1_2};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
    VK(vkCreateInstance(&ici, NULL, &inst));
    uint32_t count = 0;
    VK(vkEnumeratePhysicalDevices(inst, &count, NULL));
    VkPhysicalDevice devices[16];
    if (count > 16) count = 16;
    VK(vkEnumeratePhysicalDevices(inst, &count, devices));
    for (uint32_t i = 0; i < count && !phys; i++) {
        VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
        VkPhysicalDeviceProperties2 p = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
        vkGetPhysicalDeviceProperties2(devices[i], &p);
        if (driver.driverID == VK_DRIVER_ID_MESA_HONEYKRISP) {
            phys = devices[i];
            printf("STAGE3B vulkan_device=%s driver=%s %s\n", p.properties.deviceName, driver.driverName, driver.driverInfo);
        }
    }
    if (!phys) { puts("STAGE3B vulkan_error no Honeykrisp device; refusing software-only success"); return -1; }

    const char *exts[] = {VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME, VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
                          VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME, VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME};
    uint32_t ne = 0;
    VK(vkEnumerateDeviceExtensionProperties(phys, NULL, &ne, NULL));
    VkExtensionProperties *e = calloc(ne, sizeof *e);
    if (!e) return -1;
    VK(vkEnumerateDeviceExtensionProperties(phys, NULL, &ne, e));
    for (unsigned i = 0; i < sizeof exts / sizeof *exts; i++)
        if (!has_ext(e, ne, exts[i])) { printf("STAGE3B vulkan_error missing %s\n", exts[i]); free(e); return -1; }
    free(e);

    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, NULL);
    VkQueueFamilyProperties qf[16];
    if (nq > 16) nq = 16;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, qf);
    qfam = UINT32_MAX;
    for (uint32_t i = 0; i < nq; i++) if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { qfam = i; break; }
    if (qfam == UINT32_MAX) { puts("STAGE3B vulkan_error no graphics queue"); return -1; }
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                   .queueFamilyIndex = qfam, .queueCount = 1, .pQueuePriorities = &prio};
    VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
                              .pQueueCreateInfos = &qci, .enabledExtensionCount = sizeof exts / sizeof *exts,
                              .ppEnabledExtensionNames = exts};
    VK(vkCreateDevice(phys, &dci, NULL, &dev));
    vkGetDeviceQueue(dev, qfam, 0, &queue);
    get_fd_props = (PFN_vkGetMemoryFdPropertiesKHR)vkGetDeviceProcAddr(dev, "vkGetMemoryFdPropertiesKHR");
    get_image_modifier = (PFN_vkGetImageDrmFormatModifierPropertiesEXT)
        vkGetDeviceProcAddr(dev, "vkGetImageDrmFormatModifierPropertiesEXT");
    if (!get_fd_props || !get_image_modifier) { puts("STAGE3B vulkan_error missing entry points"); return -1; }
    return 0;
}

/* Prints what Vulkan says about this modifier for B8G8R8A8 and whether dma-buf import is allowed. */
static int modifier_supported(uint64_t modifier) {
    VkDrmFormatModifierPropertiesEXT mods[32];
    VkDrmFormatModifierPropertiesListEXT list = {.sType = VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT,
                                                 .drmFormatModifierCount = 32, .pDrmFormatModifierProperties = mods};
    VkFormatProperties2 fp = {.sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2, .pNext = &list};
    vkGetPhysicalDeviceFormatProperties2(phys, VK_FORMAT_B8G8R8A8_UNORM, &fp);
    int found = 0;
    for (uint32_t i = 0; i < list.drmFormatModifierCount && i < 32; i++) {
        printf("STAGE3B vk_modifier 0x%016llx planes=%u features=0x%x\n",
               (unsigned long long)mods[i].drmFormatModifier, mods[i].drmFormatModifierPlaneCount,
               mods[i].drmFormatModifierTilingFeatures);
        if (mods[i].drmFormatModifier == modifier &&
            (mods[i].drmFormatModifierTilingFeatures & VK_FORMAT_FEATURE_TRANSFER_DST_BIT)) found = 1;
    }
    VkPhysicalDeviceImageDrmFormatModifierInfoEXT mi = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT,
        .drmFormatModifier = modifier, .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    VkPhysicalDeviceExternalImageFormatInfo ei = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
        .pNext = &mi, .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkPhysicalDeviceImageFormatInfo2 fi = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
        .pNext = &ei, .format = VK_FORMAT_B8G8R8A8_UNORM, .type = VK_IMAGE_TYPE_2D,
        .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT};
    VkExternalImageFormatProperties ep = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
    VkImageFormatProperties2 ip = {.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2, .pNext = &ep};
    VkResult r = vkGetPhysicalDeviceImageFormatProperties2(phys, &fi, &ip);
    int importable = r == VK_SUCCESS &&
        (ep.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT);
    printf("STAGE3B modifier 0x%016llx listed_with_transfer_dst=%d dma_buf_importable=%d (query %d)\n",
           (unsigned long long)modifier, found, importable, r);
    return found && importable;
}

static uint32_t find_memory_type(uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(phys, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want) return i;
    return UINT32_MAX;
}


#define PRE 0xff112233u

static void dmabuf_sync(int fd, uint64_t flags) {
    struct dma_buf_sync s = {.flags = flags};
    if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &s)) printf("STAGE3B note DMA_BUF_IOCTL_SYNC errno=%d\n", errno);
}

/* Takes ownership of fd. Returns 0 PASS, 1 data mismatch, -1 API or setup failure. */
static int roundtrip(const char *name, int fd, uint32_t stride, uint64_t size) {
    int result = -1;
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory mem = VK_NULL_HANDLE, bmem = VK_NULL_HANDLE;
    VkBuffer buf = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    int cpu_fd = dup(fd);
    printf("STAGE3B case=%s dma_buf stride=%u size=%llu\n", name, stride, (unsigned long long)size);
    if (cpu_fd < 0 || !modifier_supported(DRM_FORMAT_MOD_LINEAR)) goto out;

    uint32_t *cpu = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, cpu_fd, 0);
    if (cpu == MAP_FAILED) { printf("STAGE3B case=%s cpu_mmap=FAIL errno=%d\n", name, errno); goto out; }
    dmabuf_sync(cpu_fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
    for (uint32_t y = 0; y < H; y++) for (uint32_t x = 0; x < W; x++) cpu[(size_t)y * stride / 4 + x] = PRE;
    dmabuf_sync(cpu_fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);

    VkSubresourceLayout layout = {.offset = 0, .rowPitch = stride};
    VkImageDrmFormatModifierExplicitCreateInfoEXT explicit_mod = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT,
        .drmFormatModifier = DRM_FORMAT_MOD_LINEAR, .drmFormatModifierPlaneCount = 1, .pPlaneLayouts = &layout};
    VkExternalMemoryImageCreateInfo ext_img = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
        .pNext = &explicit_mod, .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkImageCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .pNext = &ext_img,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_B8G8R8A8_UNORM, .extent = {W, H, 1},
        .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_PREINITIALIZED};
    if (vkCreateImage(dev, &ici, NULL, &image) != VK_SUCCESS) { printf("STAGE3B case=%s vkCreateImage=FAIL\n", name); goto out; }
    VkMemoryFdPropertiesKHR fdp = {.sType = VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR};
    VkResult fr = get_fd_props(dev, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT, fd, &fdp);
    if (fr != VK_SUCCESS) { printf("STAGE3B case=%s vkGetMemoryFdPropertiesKHR=FAIL %d\n", name, fr); goto out; }
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(dev, image, &req);
    uint32_t type = find_memory_type(req.memoryTypeBits & fdp.memoryTypeBits, 0);
    printf("STAGE3B case=%s image_size=%llu fd_type_bits=0x%x image_type_bits=0x%x\n", name,
           (unsigned long long)req.size, fdp.memoryTypeBits, req.memoryTypeBits);
    if (type == UINT32_MAX) { printf("STAGE3B case=%s no shared memory type\n", name); goto out; }
    VkMemoryDedicatedAllocateInfo ded = {.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO, .image = image};
    VkImportMemoryFdInfoKHR imp = {.sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR, .pNext = &ded,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT, .fd = fd};
    VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .pNext = &imp,
        .allocationSize = req.size, .memoryTypeIndex = type};
    if (req.size > size) { printf("STAGE3B case=%s image needs %llu > buffer %llu\n", name,
                                 (unsigned long long)req.size, (unsigned long long)size); goto out; }
    if (vkAllocateMemory(dev, &mai, NULL, &mem) != VK_SUCCESS) { printf("STAGE3B case=%s vulkan_import=FAIL\n", name); goto out; }
    fd = -1;
    if (vkBindImageMemory(dev, image, mem, 0) != VK_SUCCESS) { printf("STAGE3B case=%s bind=FAIL\n", name); goto out; }
    printf("STAGE3B case=%s vulkan_import=PASS\n", name);

    VkBufferCreateInfo bci = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = W * H * 4,
                              .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT};
    if (vkCreateBuffer(dev, &bci, NULL, &buf) != VK_SUCCESS) goto out;
    VkMemoryRequirements breq;
    vkGetBufferMemoryRequirements(dev, buf, &breq);
    VkMemoryAllocateInfo bai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = breq.size,
        .memoryTypeIndex = find_memory_type(breq.memoryTypeBits,
                                            VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
    if (bai.memoryTypeIndex == UINT32_MAX || vkAllocateMemory(dev, &bai, NULL, &bmem) != VK_SUCCESS ||
        vkBindBufferMemory(dev, buf, bmem, 0) != VK_SUCCESS) goto out;

    VkCommandPoolCreateInfo pci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = qfam};
    VkCommandBuffer cb;
    VkCommandBufferAllocateInfo cai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
                                       .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                   .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    if (vkCreateCommandPool(dev, &pci, NULL, &pool) != VK_SUCCESS) goto out;
    cai.commandPool = pool;
    if (vkAllocateCommandBuffers(dev, &cai, &cb) != VK_SUCCESS || vkBeginCommandBuffer(cb, &bi) != VK_SUCCESS) goto out;
    VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    /* Acquire from the foreign (CPU-written) owner keeping the contents: PREINITIALIZED. */
    VkImageMemoryBarrier acquire = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT, .oldLayout = VK_IMAGE_LAYOUT_PREINITIALIZED,
        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT,
        .dstQueueFamilyIndex = qfam, .image = image, .subresourceRange = range};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &acquire);
    VkBufferImageCopy copy = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, .imageExtent = {W, H, 1}};
    vkCmdCopyImageToBuffer(cb, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buf, 1, &copy);
    VkImageMemoryBarrier to_dst = acquire;
    to_dst.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    to_dst.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    to_dst.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    to_dst.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    to_dst.srcQueueFamilyIndex = to_dst.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &to_dst);
    VkClearColorValue color = {.float32 = {0x20 / 255.0f, 0x40 / 255.0f, 0x80 / 255.0f, 1.0f}};
    vkCmdClearColorImage(cb, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range);
    VkImageMemoryBarrier release = to_dst;
    release.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    release.dstAccessMask = 0;
    release.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    release.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    release.srcQueueFamilyIndex = qfam;
    release.dstQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT;
    VkBufferMemoryBarrier host = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buf, .size = VK_WHOLE_SIZE};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT | VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, NULL, 1, &host, 1, &release);
    if (vkEndCommandBuffer(cb) != VK_SUCCESS) goto out;
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
    if (vkCreateFence(dev, &fci, NULL, &fence) != VK_SUCCESS || vkQueueSubmit(queue, 1, &si, fence) != VK_SUCCESS) goto out;
    VkResult wr = vkWaitForFences(dev, 1, &fence, VK_TRUE, 10000000000ull);
    printf("STAGE3B case=%s submit_wait=%d\n", name, wr);
    if (wr != VK_SUCCESS) goto out;

    void *p = NULL;
    if (vkMapMemory(dev, bmem, 0, VK_WHOLE_SIZE, 0, &p) != VK_SUCCESS) goto out;
    unsigned gpu_good = 0;
    for (uint32_t i = 0; i < W * H; i++) gpu_good += ((const uint32_t *)p)[i] == PRE;
    uint32_t gpu_first = *(const uint32_t *)p;
    vkUnmapMemory(dev, bmem);
    printf("STAGE3B case=%s cpu_to_gpu=%s pixels=%u/%u first=0x%08x expect=0x%08x\n", name,
           gpu_good == W * H ? "PASS" : "FAIL", gpu_good, W * H, gpu_first, PRE);

    dmabuf_sync(cpu_fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
    unsigned cpu_good = 0;
    for (uint32_t y = 0; y < H; y++) for (uint32_t x = 0; x < W; x++) cpu_good += cpu[(size_t)y * stride / 4 + x] == EXPECT;
    uint32_t cpu_first = cpu[0];
    dmabuf_sync(cpu_fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    printf("STAGE3B case=%s gpu_to_cpu=%s pixels=%u/%u first=0x%08x expect=0x%08x\n", name,
           cpu_good == W * H ? "PASS" : "FAIL", cpu_good, W * H, cpu_first, EXPECT);
    result = gpu_good == W * H && cpu_good == W * H ? 0 : 1;
    printf("STAGE3B case=%s result=%s\n", name, result ? "FAIL" : "PASS");
    munmap(cpu, size);
out:
    if (fence) vkDestroyFence(dev, fence, NULL);
    if (pool) vkDestroyCommandPool(dev, pool, NULL);
    if (buf) vkDestroyBuffer(dev, buf, NULL);
    if (bmem) vkFreeMemory(dev, bmem, NULL);
    if (image) vkDestroyImage(dev, image, NULL);
    if (mem) vkFreeMemory(dev, mem, NULL);
    if (fd >= 0) close(fd);
    if (cpu_fd >= 0) close(cpu_fd);
    if (result < 0) printf("STAGE3B case=%s result=ERROR\n", name);
    return result;
}

/* Opens the first /dev/dri/card* whose driver name matches; card numbers change between boots. */
static int open_card(const char *driver) {
    char path[64];
    for (int i = 0; i < 16; i++) {
        snprintf(path, sizeof path, "/dev/dri/card%d", i);
        int fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd < 0) { if (errno != ENOENT) printf("STAGE3B node %s open_errno=%d\n", path, errno); continue; }
        drmVersionPtr v = drmGetVersion(fd);
        int ok = v && !strcmp(v->name, driver);
        if (v) drmFreeVersion(v);
        if (ok) { printf("STAGE3B node %s driver=%s\n", path, driver); return fd; }
        close(fd);
    }
    printf("STAGE3B no openable card node for driver %s\n", driver);
    return -1;
}

static int dumb_case(const char *name, const char *driver) {
    int card = open_card(driver);
    if (card < 0) return -1;
    struct drm_mode_create_dumb c = {.width = W, .height = H, .bpp = 32};
    if (drmIoctl(card, DRM_IOCTL_MODE_CREATE_DUMB, &c)) {
        printf("STAGE3B case=%s dumb_create=FAIL errno=%d\n", name, errno);
        close(card);
        return -1;
    }
    printf("STAGE3B case=%s dumb_create=PASS handle=%u pitch=%u size=%llu\n", name, c.handle, c.pitch, (unsigned long long)c.size);
    int prime = -1;
    if (drmPrimeHandleToFD(card, c.handle, DRM_CLOEXEC | DRM_RDWR, &prime)) {
        printf("STAGE3B case=%s prime_export=FAIL errno=%d\n", name, errno);
        close(card);
        return -1;
    }
    struct drm_mode_destroy_dumb d = {.handle = c.handle};
    drmIoctl(card, DRM_IOCTL_MODE_DESTROY_DUMB, &d); /* the dma-buf keeps the memory alive */
    close(card);
    return roundtrip(name, prime, c.pitch, c.size);
}

static int heap_cases(unsigned *tried) {
    int worst = 0;
    DIR *dir = opendir("/dev/dma_heap");
    if (!dir) { printf("STAGE3B dma_heap directory errno=%d\n", errno); return -1; }
    struct dirent *e;
    while ((e = readdir(dir))) {
        if (e->d_name[0] == '.') continue;
        char path[300], name[300];
        snprintf(path, sizeof path, "/dev/dma_heap/%s", e->d_name);
        snprintf(name, sizeof name, "dma-heap:%s", e->d_name);
        int heap = open(path, O_RDWR | O_CLOEXEC);
        if (heap < 0) { printf("STAGE3B case=%s open_errno=%d\n", name, errno); continue; }
        struct dma_heap_allocation_data a = {.len = W * H * 4, .fd_flags = O_RDWR | O_CLOEXEC};
        if (ioctl(heap, DMA_HEAP_IOCTL_ALLOC, &a)) {
            printf("STAGE3B case=%s alloc=FAIL errno=%d\n", name, errno);
            close(heap);
            continue;
        }
        close(heap);
        (*tried)++;
        int r = roundtrip(name, (int)a.fd, W * 4, W * H * 4);
        if (r > worst || (r < 0 && !worst)) worst = r ? r : worst;
    }
    closedir(dir);
    return worst;
}

/* Stage 3d: allocate through a (patched) minigbm built from the AOSP tree, loaded by path.
 * Local prototypes keep Mesa's system gbm.h out of the picture. Usage flags are minigbm's
 * gbm.h values: SCANOUT 1<<0, RENDERING 1<<2, LINEAR 1<<4. */
static int minigbm_case(const char *path) {
    void *lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!lib) { printf("STAGE3B case=minigbm-apple dlopen=FAIL %s\n", dlerror()); return -1; }
    void *(*create_device)(int) = (void *(*)(int))dlsym(lib, "gbm_create_device");
    void *(*bo_create)(void *, uint32_t, uint32_t, uint32_t, uint32_t) =
        (void *(*)(void *, uint32_t, uint32_t, uint32_t, uint32_t))dlsym(lib, "gbm_bo_create");
    int (*bo_fd)(void *) = (int (*)(void *))dlsym(lib, "gbm_bo_get_fd");
    uint32_t (*bo_stride)(void *) = (uint32_t (*)(void *))dlsym(lib, "gbm_bo_get_stride");
    void (*bo_destroy)(void *) = (void (*)(void *))dlsym(lib, "gbm_bo_destroy");
    void (*dev_destroy)(void *) = (void (*)(void *))dlsym(lib, "gbm_device_destroy");
    if (!create_device || !bo_create || !bo_fd || !bo_stride || !bo_destroy || !dev_destroy) {
        printf("STAGE3B case=minigbm-apple missing gbm entry points in %s\n", path); return -1; }
    int card = open_card("apple");
    if (card < 0) return -1;
    void *gbm = create_device(card);
    if (!gbm) { printf("STAGE3B case=minigbm-apple gbm_create_device=FAIL (no backend matched?)\n"); close(card); return -1; }
    printf("STAGE3B case=minigbm-apple gbm_create_device=PASS\n");
    void *bo = bo_create(gbm, W, H, DRM_FORMAT_ARGB8888, (1u << 0) | (1u << 2) | (1u << 4));
    int result = -1;
    if (!bo) printf("STAGE3B case=minigbm-apple gbm_bo_create=FAIL errno=%d\n", errno);
    else {
        int fd = bo_fd(bo);
        uint32_t stride = bo_stride(bo);
        off_t size = fd >= 0 ? lseek(fd, 0, SEEK_END) : -1;
        if (fd >= 0) lseek(fd, 0, SEEK_SET);
        printf("STAGE3B case=minigbm-apple gbm_bo_create=PASS fd=%d stride=%u dmabuf_size=%lld\n", fd, stride, (long long)size);
        if (fd >= 0 && size > 0) result = roundtrip("minigbm-apple", fd, stride, (uint64_t)size);
        else if (fd >= 0) close(fd);
        bo_destroy(bo);
    }
    dev_destroy(gbm);
    close(card);
    return result;
}

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    printf("STAGE3B page_size=%ld\n", sysconf(_SC_PAGESIZE));
    if (init_vulkan()) return 4;
    int apple = dumb_case("dumb-apple", "apple");
    int asahi = dumb_case("dumb-asahi", "asahi");
    unsigned heaps = 0;
    int heap = heap_cases(&heaps);
    int mg = argc > 1 ? minigbm_case(argv[1]) : -2;
    printf("STAGE3B summary dumb-apple=%s dumb-asahi=%s dma-heap=%s (heaps tried: %u) minigbm-apple=%s\n",
           apple == 0 ? "PASS" : apple > 0 ? "FAIL" : "UNAVAILABLE",
           asahi == 0 ? "PASS" : asahi > 0 ? "FAIL" : "UNAVAILABLE",
           heaps && heap == 0 ? "PASS" : heap > 0 ? "FAIL" : "UNAVAILABLE", heaps,
           mg == -2 ? "NOT_RUN" : mg == 0 ? "PASS" : mg > 0 ? "FAIL" : "ERROR");
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return 0; /* every outcome is a result; the summary line carries it */
}
