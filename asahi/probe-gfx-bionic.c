/* Graphics stage 3f: one Android (Bionic) process using both Android-built libraries. A buffer is
 * allocated through the patched minigbm on the display card node, written by the CPU, imported into
 * Honeykrisp (loaded directly as an ICD, no Android loader) as a linear image, copied out and cleared
 * by the GPU, then read back by the CPU through the dma-buf mapping. Same checks as stage 3b/3d on
 * glibc; Honeykrisp calls go through function pointers because the NDK has no Vulkan loader link. */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
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
#define PRE 0xff112233u
#define EXPECT 0xff204080u
#define USE_SCANOUT_RENDERING_LINEAR ((1u << 0) | (1u << 2) | (1u << 4)) /* minigbm gbm.h values */

typedef VkResult (VKAPI_PTR *PFN_icd_negotiate)(uint32_t *version);
static PFN_vkGetInstanceProcAddr gipa;
static PFN_vkGetDeviceProcAddr gdpa;

#define LOADI(inst, name) PFN_##name name = (PFN_##name)gipa(inst, #name); \
    if (!name) { printf("GFX_BIONIC missing instance entry point %s\n", #name); return 1; }
#define LOADD(dev, name) PFN_##name name = (PFN_##name)gdpa(dev, #name); \
    if (!name) { printf("GFX_BIONIC missing device entry point %s\n", #name); return 1; }
#define CK(call) do { VkResult r_ = (call); if (r_ != VK_SUCCESS) { \
    printf("GFX_BIONIC %s = %d\n", #call, r_); return 1; } } while (0)

static void sync_buf(int fd, uint64_t flags) {
    struct dma_buf_sync s = {.flags = flags};
    if (ioctl(fd, DMA_BUF_IOCTL_SYNC, &s)) printf("GFX_BIONIC note DMA_BUF_IOCTL_SYNC errno=%d\n", errno);
}

static uint32_t find_type(const VkPhysicalDeviceMemoryProperties *mp, uint32_t bits, VkMemoryPropertyFlags want) {
    for (uint32_t i = 0; i < mp->memoryTypeCount; i++)
        if ((bits & (1u << i)) && (mp->memoryTypes[i].propertyFlags & want) == want) return i;
    return UINT32_MAX;
}

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    if (argc != 3) { fprintf(stderr, "usage: %s libminigbm.so vulkan.asahi.so\n", argv[0]); return 64; }
    printf("GFX_BIONIC page_size=%ld\n", sysconf(_SC_PAGESIZE));

    /* ---- minigbm: allocate on the display card node ---- */
    void *mg = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!mg) { printf("GFX_BIONIC minigbm dlopen=FAIL %s\n", dlerror()); return 2; }
    void *(*create_device)(int) = (void *(*)(int))dlsym(mg, "gbm_create_device");
    void *(*bo_create)(void *, uint32_t, uint32_t, uint32_t, uint32_t) =
        (void *(*)(void *, uint32_t, uint32_t, uint32_t, uint32_t))dlsym(mg, "gbm_bo_create");
    int (*bo_fd)(void *) = (int (*)(void *))dlsym(mg, "gbm_bo_get_fd");
    uint32_t (*bo_stride)(void *) = (uint32_t (*)(void *))dlsym(mg, "gbm_bo_get_stride");
    if (!create_device || !bo_create || !bo_fd || !bo_stride) { puts("GFX_BIONIC missing gbm entry points"); return 3; }
    int card = -1;
    char path[64];
    for (int i = 0; i < 16 && card < 0; i++) {
        snprintf(path, sizeof path, "/dev/dri/card%d", i);
        int fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd < 0) continue;
        drmVersionPtr v = drmGetVersion(fd);
        int ok = v && !strcmp(v->name, "apple");
        if (v) drmFreeVersion(v);
        if (ok) { card = fd; printf("GFX_BIONIC node %s driver=apple\n", path); } else close(fd);
    }
    if (card < 0) { puts("GFX_BIONIC no openable apple card node"); return 4; }
    void *gbm = create_device(card);
    void *bo = gbm ? bo_create(gbm, W, H, DRM_FORMAT_ARGB8888, USE_SCANOUT_RENDERING_LINEAR) : NULL;
    if (!bo) { puts("GFX_BIONIC minigbm allocation=FAIL"); return 5; }
    int fd = bo_fd(bo);
    uint32_t stride = bo_stride(bo);
    off_t size = fd >= 0 ? lseek(fd, 0, SEEK_END) : -1;
    if (fd >= 0) lseek(fd, 0, SEEK_SET);
    printf("GFX_BIONIC minigbm allocation=PASS fd=%d stride=%u dmabuf_size=%lld\n", fd, stride, (long long)size);
    if (fd < 0 || size <= 0) return 6;
    int cpu_fd = dup(fd);
    uint32_t *cpu = mmap(NULL, (size_t)size, PROT_READ | PROT_WRITE, MAP_SHARED, cpu_fd, 0);
    if (cpu == MAP_FAILED) { printf("GFX_BIONIC cpu mmap=FAIL errno=%d\n", errno); return 7; }
    sync_buf(cpu_fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_WRITE);
    for (uint32_t y = 0; y < H; y++) for (uint32_t x = 0; x < W; x++) cpu[(size_t)y * stride / 4 + x] = PRE;
    sync_buf(cpu_fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_WRITE);

    /* ---- Honeykrisp as a bare ICD ---- */
    void *icd = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
    if (!icd) { printf("GFX_BIONIC honeykrisp dlopen=FAIL %s\n", dlerror()); return 8; }
    PFN_icd_negotiate negotiate = (PFN_icd_negotiate)dlsym(icd, "vk_icdNegotiateLoaderICDInterfaceVersion");
    if (negotiate) { uint32_t v = 5; negotiate(&v); }
    gipa = (PFN_vkGetInstanceProcAddr)dlsym(icd, "vk_icdGetInstanceProcAddr");
    if (!gipa) { puts("GFX_BIONIC missing vk_icdGetInstanceProcAddr"); return 9; }
    LOADI(NULL, vkCreateInstance);
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "gbos stage3f",
                            .apiVersion = VK_API_VERSION_1_2};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
    VkInstance inst;
    CK(vkCreateInstance(&ici, NULL, &inst));
    LOADI(inst, vkEnumeratePhysicalDevices);
    LOADI(inst, vkGetPhysicalDeviceProperties);
    LOADI(inst, vkGetPhysicalDeviceQueueFamilyProperties);
    LOADI(inst, vkGetPhysicalDeviceMemoryProperties);
    LOADI(inst, vkCreateDevice);
    LOADI(inst, vkGetDeviceProcAddr);
    gdpa = vkGetDeviceProcAddr;
    uint32_t count = 1;
    VkPhysicalDevice phys;
    VkResult er = vkEnumeratePhysicalDevices(inst, &count, &phys);
    if ((er != VK_SUCCESS && er != VK_INCOMPLETE) || !count) { printf("GFX_BIONIC enumerate = %d\n", er); return 10; }
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(phys, &props);
    printf("GFX_BIONIC device=%s\n", props.deviceName);
    uint32_t nq = 8;
    VkQueueFamilyProperties qf[8];
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, qf);
    uint32_t fam = UINT32_MAX;
    for (uint32_t i = 0; i < nq; i++) if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { fam = i; break; }
    if (fam == UINT32_MAX) { puts("GFX_BIONIC no graphics queue"); return 11; }
    const char *exts[] = {VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME, VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
                          VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME, VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME};
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = fam,
                                   .queueCount = 1, .pQueuePriorities = &prio};
    VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
                              .pQueueCreateInfos = &qci, .enabledExtensionCount = 4, .ppEnabledExtensionNames = exts};
    VkDevice dev;
    CK(vkCreateDevice(phys, &dci, NULL, &dev));
    LOADD(dev, vkGetDeviceQueue); LOADD(dev, vkCreateImage); LOADD(dev, vkGetImageMemoryRequirements);
    LOADD(dev, vkGetMemoryFdPropertiesKHR); LOADD(dev, vkAllocateMemory); LOADD(dev, vkBindImageMemory);
    LOADD(dev, vkCreateBuffer); LOADD(dev, vkGetBufferMemoryRequirements); LOADD(dev, vkBindBufferMemory);
    LOADD(dev, vkCreateCommandPool); LOADD(dev, vkAllocateCommandBuffers); LOADD(dev, vkBeginCommandBuffer);
    LOADD(dev, vkCmdPipelineBarrier); LOADD(dev, vkCmdCopyImageToBuffer); LOADD(dev, vkCmdClearColorImage);
    LOADD(dev, vkEndCommandBuffer); LOADD(dev, vkCreateFence); LOADD(dev, vkQueueSubmit);
    LOADD(dev, vkWaitForFences); LOADD(dev, vkMapMemory);
    VkQueue queue;
    vkGetDeviceQueue(dev, fam, 0, &queue);
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(phys, &mp);

    VkSubresourceLayout layout = {.offset = 0, .rowPitch = stride};
    VkImageDrmFormatModifierExplicitCreateInfoEXT explicit_mod = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT,
        .drmFormatModifier = DRM_FORMAT_MOD_LINEAR, .drmFormatModifierPlaneCount = 1, .pPlaneLayouts = &layout};
    VkExternalMemoryImageCreateInfo ext_img = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
        .pNext = &explicit_mod, .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkImageCreateInfo imgci = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .pNext = &ext_img,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_B8G8R8A8_UNORM, .extent = {W, H, 1},
        .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_PREINITIALIZED};
    VkImage image;
    CK(vkCreateImage(dev, &imgci, NULL, &image));
    VkMemoryFdPropertiesKHR fdp = {.sType = VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR};
    CK(vkGetMemoryFdPropertiesKHR(dev, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT, fd, &fdp));
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(dev, image, &req);
    uint32_t type = find_type(&mp, req.memoryTypeBits & fdp.memoryTypeBits, 0);
    printf("GFX_BIONIC image_size=%llu fd_type_bits=0x%x image_type_bits=0x%x\n", (unsigned long long)req.size,
           fdp.memoryTypeBits, req.memoryTypeBits);
    if (type == UINT32_MAX || req.size > (uint64_t)size) { puts("GFX_BIONIC no usable memory type or image too large"); return 12; }
    VkMemoryDedicatedAllocateInfo ded = {.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO, .image = image};
    VkImportMemoryFdInfoKHR imp = {.sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR, .pNext = &ded,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT, .fd = fd};
    VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .pNext = &imp,
        .allocationSize = req.size, .memoryTypeIndex = type};
    VkDeviceMemory mem;
    CK(vkAllocateMemory(dev, &mai, NULL, &mem)); /* fd is now owned by Vulkan; cpu_fd is the CPU view */
    CK(vkBindImageMemory(dev, image, mem, 0));
    puts("GFX_BIONIC vulkan_import=PASS");

    VkBufferCreateInfo bci = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = W * H * 4,
                              .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT};
    VkBuffer buf;
    CK(vkCreateBuffer(dev, &bci, NULL, &buf));
    VkMemoryRequirements breq;
    vkGetBufferMemoryRequirements(dev, buf, &breq);
    VkMemoryAllocateInfo bai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = breq.size,
        .memoryTypeIndex = find_type(&mp, breq.memoryTypeBits,
                                     VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)};
    if (bai.memoryTypeIndex == UINT32_MAX) { puts("GFX_BIONIC no host-visible coherent memory"); return 13; }
    VkDeviceMemory bmem;
    CK(vkAllocateMemory(dev, &bai, NULL, &bmem));
    CK(vkBindBufferMemory(dev, buf, bmem, 0));

    VkCommandPoolCreateInfo pci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = fam};
    VkCommandPool pool;
    CK(vkCreateCommandPool(dev, &pci, NULL, &pool));
    VkCommandBufferAllocateInfo cai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool,
                                       .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer cb;
    CK(vkAllocateCommandBuffers(dev, &cai, &cb));
    VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                   .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    CK(vkBeginCommandBuffer(cb, &bi));
    VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VkImageMemoryBarrier acquire = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT, .oldLayout = VK_IMAGE_LAYOUT_PREINITIALIZED,
        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT,
        .dstQueueFamilyIndex = fam, .image = image, .subresourceRange = range};
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
    release.srcQueueFamilyIndex = fam;
    release.dstQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT;
    VkBufferMemoryBarrier host = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buf, .size = VK_WHOLE_SIZE};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT | VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, NULL, 1, &host, 1, &release);
    CK(vkEndCommandBuffer(cb));
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence;
    CK(vkCreateFence(dev, &fci, NULL, &fence));
    VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
    CK(vkQueueSubmit(queue, 1, &si, fence));
    CK(vkWaitForFences(dev, 1, &fence, VK_TRUE, 10000000000ull));
    puts("GFX_BIONIC gpu_submit=PASS");

    void *p;
    CK(vkMapMemory(dev, bmem, 0, VK_WHOLE_SIZE, 0, &p));
    unsigned gpu_good = 0;
    for (uint32_t i = 0; i < W * H; i++) gpu_good += ((const uint32_t *)p)[i] == PRE;
    printf("GFX_BIONIC cpu_to_gpu=%s pixels=%u/%u first=0x%08x expect=0x%08x\n", gpu_good == W * H ? "PASS" : "FAIL",
           gpu_good, W * H, ((const uint32_t *)p)[0], PRE);
    sync_buf(cpu_fd, DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ);
    unsigned cpu_good = 0;
    for (uint32_t y = 0; y < H; y++) for (uint32_t x = 0; x < W; x++) cpu_good += cpu[(size_t)y * stride / 4 + x] == EXPECT;
    uint32_t cpu_first = cpu[0];
    sync_buf(cpu_fd, DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ);
    printf("GFX_BIONIC gpu_to_cpu=%s pixels=%u/%u first=0x%08x expect=0x%08x\n", cpu_good == W * H ? "PASS" : "FAIL",
           cpu_good, W * H, cpu_first, EXPECT);
    int ok = gpu_good == W * H && cpu_good == W * H;
    printf("GFX_BIONIC result=%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 14;
}
