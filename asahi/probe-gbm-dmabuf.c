/* Graphics stage 1: GBM allocation on the Asahi render node, dma-buf import into
 * Honeykrisp with the buffer's DRM format modifier, a Vulkan clear, then readback
 * through Vulkan and through a GBM CPU map. Runs with a linear buffer, GBM's default
 * layout, and Apple's tiled and compressed modifiers requested explicitly.
 * Uses no KMS and never opens a display node for writing. */
#include <vulkan/vulkan.h>
#include <gbm.h>
#include <xf86drm.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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
    printf("STAGE1 vulkan_error %s = %d\n", #call, r_); return -1; } } while (0)

static void list_node(const char *path) {
    if (access(path, F_OK)) return;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) { printf("STAGE1 node %s open_errno=%d\n", path, errno); return; }
    drmVersionPtr v = drmGetVersion(fd);
    printf("STAGE1 node %s driver=%s\n", path, v ? v->name : "?");
    if (v) drmFreeVersion(v);
    close(fd);
}

/* Card numbers change between boots, so nodes are identified by driver name. */
static void list_nodes(void) {
    char path[64];
    for (int i = 0; i < 16; i++) { snprintf(path, sizeof path, "/dev/dri/card%d", i); list_node(path); }
    for (int i = 128; i < 144; i++) { snprintf(path, sizeof path, "/dev/dri/renderD%d", i); list_node(path); }
}

static int open_asahi_render(void) {
    char path[64];
    for (int n = 128; n < 192; n++) {
        snprintf(path, sizeof path, "/dev/dri/renderD%d", n);
        int fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd < 0) continue;
        drmVersionPtr v = drmGetVersion(fd);
        int ok = v && !strcmp(v->name, "asahi");
        if (v) drmFreeVersion(v);
        if (ok) { printf("STAGE1 render_node=%s\n", path); return fd; }
        close(fd);
    }
    return -1;
}

static int has_ext(const VkExtensionProperties *e, uint32_t n, const char *name) {
    for (uint32_t i = 0; i < n; i++) if (!strcmp(e[i].extensionName, name)) return 1;
    return 0;
}

static int init_vulkan(void) {
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                            .pApplicationName = "gbos stage1 probe", .apiVersion = VK_API_VERSION_1_2};
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
            printf("STAGE1 vulkan_device=%s driver=%s %s\n", p.properties.deviceName, driver.driverName, driver.driverInfo);
        }
    }
    if (!phys) { puts("STAGE1 vulkan_error no Honeykrisp device; refusing software-only success"); return -1; }

    const char *exts[] = {VK_KHR_EXTERNAL_MEMORY_FD_EXTENSION_NAME, VK_EXT_EXTERNAL_MEMORY_DMA_BUF_EXTENSION_NAME,
                          VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME, VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME};
    uint32_t ne = 0;
    VK(vkEnumerateDeviceExtensionProperties(phys, NULL, &ne, NULL));
    VkExtensionProperties *e = calloc(ne, sizeof *e);
    if (!e) return -1;
    VK(vkEnumerateDeviceExtensionProperties(phys, NULL, &ne, e));
    for (unsigned i = 0; i < sizeof exts / sizeof *exts; i++)
        if (!has_ext(e, ne, exts[i])) { printf("STAGE1 vulkan_error missing %s\n", exts[i]); free(e); return -1; }
    free(e);

    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, NULL);
    VkQueueFamilyProperties qf[16];
    if (nq > 16) nq = 16;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, qf);
    qfam = UINT32_MAX;
    for (uint32_t i = 0; i < nq; i++) if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { qfam = i; break; }
    if (qfam == UINT32_MAX) { puts("STAGE1 vulkan_error no graphics queue"); return -1; }
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
    if (!get_fd_props || !get_image_modifier) { puts("STAGE1 vulkan_error missing entry points"); return -1; }
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
        printf("STAGE1 vk_modifier 0x%016llx planes=%u features=0x%x\n",
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
    printf("STAGE1 modifier 0x%016llx listed_with_transfer_dst=%d dma_buf_importable=%d (query %d)\n",
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

static unsigned count_good(const unsigned char *base, uint32_t stride, uint32_t *first) {
    unsigned good = 0;
    for (uint32_t y = 0; y < H; y++) {
        const uint32_t *row = (const uint32_t *)(base + (size_t)y * stride);
        for (uint32_t x = 0; x < W; x++) good += row[x] == EXPECT;
    }
    *first = *(const uint32_t *)base;
    return good;
}

/* Returns 0 on PASS, 1 on a readback mismatch, -1 on an API failure. */
/* With a modifier, asks GBM for exactly that layout; without one, GBM picks. */
static int run_case(struct gbm_device *gbm, const char *name, uint32_t flags, const uint64_t *request) {
    printf("STAGE1 case=%s\n", name);
    struct gbm_bo *bo = request ? gbm_bo_create_with_modifiers2(gbm, W, H, GBM_FORMAT_ARGB8888, request, 1, flags)
                                : gbm_bo_create(gbm, W, H, GBM_FORMAT_ARGB8888, flags);
    if (!bo) { printf("STAGE1 case=%s gbm_alloc=FAIL errno=%d\n", name, errno); return -1; }
    uint64_t modifier = gbm_bo_get_modifier(bo);
    int planes = gbm_bo_get_plane_count(bo);
    uint32_t stride = gbm_bo_get_stride_for_plane(bo, 0), offset = gbm_bo_get_offset(bo, 0);
    printf("STAGE1 case=%s gbm_alloc=PASS modifier=0x%016llx planes=%d stride=%u offset=%u\n",
           name, (unsigned long long)modifier, planes, stride, offset);
    int result = -1, fd = -1;
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory mem = VK_NULL_HANDLE, bmem = VK_NULL_HANDLE;
    VkBuffer buf = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    if (planes != 1) { printf("STAGE1 case=%s unsupported plane count\n", name); goto out; }
    if (!modifier_supported(modifier)) { printf("STAGE1 case=%s vulkan_import=UNSUPPORTED\n", name); goto out; }
    fd = gbm_bo_get_fd(bo);
    if (fd < 0) { printf("STAGE1 case=%s dma_buf_export=FAIL\n", name); goto out; }

    VkSubresourceLayout layout = {.offset = offset, .rowPitch = stride};
    VkImageDrmFormatModifierExplicitCreateInfoEXT explicit_mod = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_EXPLICIT_CREATE_INFO_EXT,
        .drmFormatModifier = modifier, .drmFormatModifierPlaneCount = 1, .pPlaneLayouts = &layout};
    VkExternalMemoryImageCreateInfo ext_img = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
        .pNext = &explicit_mod, .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT};
    VkImageCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .pNext = &ext_img,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_B8G8R8A8_UNORM, .extent = {W, H, 1},
        .mipLevels = 1, .arrayLayers = 1, .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
    if (vkCreateImage(dev, &ici, NULL, &image) != VK_SUCCESS) { printf("STAGE1 case=%s vkCreateImage=FAIL\n", name); goto out; }

    VkMemoryFdPropertiesKHR fdp = {.sType = VK_STRUCTURE_TYPE_MEMORY_FD_PROPERTIES_KHR};
    if (get_fd_props(dev, VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT, fd, &fdp) != VK_SUCCESS) {
        printf("STAGE1 case=%s vkGetMemoryFdPropertiesKHR=FAIL\n", name); goto out; }
    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(dev, image, &req);
    uint32_t type = find_memory_type(req.memoryTypeBits & fdp.memoryTypeBits, 0);
    printf("STAGE1 case=%s image_size=%llu fd_type_bits=0x%x image_type_bits=0x%x\n", name,
           (unsigned long long)req.size, fdp.memoryTypeBits, req.memoryTypeBits);
    if (type == UINT32_MAX) { printf("STAGE1 case=%s no shared memory type\n", name); goto out; }
    VkMemoryDedicatedAllocateInfo ded = {.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO, .image = image};
    VkImportMemoryFdInfoKHR imp = {.sType = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR, .pNext = &ded,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT, .fd = fd};
    VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .pNext = &imp,
        .allocationSize = req.size, .memoryTypeIndex = type};
    if (vkAllocateMemory(dev, &mai, NULL, &mem) != VK_SUCCESS) { printf("STAGE1 case=%s import=FAIL\n", name); goto out; }
    fd = -1; /* ownership moved to the Vulkan memory object */
    if (vkBindImageMemory(dev, image, mem, 0) != VK_SUCCESS) { printf("STAGE1 case=%s bind=FAIL\n", name); goto out; }
    VkImageDrmFormatModifierPropertiesEXT imp_mod = {.sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_PROPERTIES_EXT};
    get_image_modifier(dev, image, &imp_mod);
    printf("STAGE1 case=%s vulkan_import=PASS image_modifier=0x%016llx\n", name,
           (unsigned long long)imp_mod.drmFormatModifier);

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
    /* Acquire from the foreign (GBM) owner, clear, copy out, release back as GENERAL. */
    VkImageMemoryBarrier to_dst = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT,
        .dstQueueFamilyIndex = qfam, .image = image, .subresourceRange = range};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &to_dst);
    VkClearColorValue color = {.float32 = {0x20 / 255.0f, 0x40 / 255.0f, 0x80 / 255.0f, 1.0f}};
    vkCmdClearColorImage(cb, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range);
    VkImageMemoryBarrier to_src = to_dst;
    to_src.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    to_src.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    to_src.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    to_src.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    to_src.srcQueueFamilyIndex = to_src.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &to_src);
    VkBufferImageCopy copy = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, .imageExtent = {W, H, 1}};
    vkCmdCopyImageToBuffer(cb, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buf, 1, &copy);
    VkImageMemoryBarrier release = to_src;
    release.srcAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT;
    release.dstAccessMask = 0;
    release.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
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
    printf("STAGE1 case=%s submit_wait=%d\n", name, wr);
    if (wr != VK_SUCCESS) goto out;

    void *p = NULL;
    uint32_t first = 0;
    if (vkMapMemory(dev, bmem, 0, VK_WHOLE_SIZE, 0, &p) != VK_SUCCESS) goto out;
    unsigned vk_good = count_good(p, W * 4, &first);
    vkUnmapMemory(dev, bmem);
    printf("STAGE1 case=%s vulkan_readback=%s pixels=%u/%u first=0x%08x expect=0x%08x\n", name,
           vk_good == W * H ? "PASS" : "FAIL", vk_good, W * H, first, EXPECT);

    uint32_t map_stride = 0;
    void *map_data = NULL;
    void *m = gbm_bo_map(bo, 0, 0, W, H, GBM_BO_TRANSFER_READ, &map_stride, &map_data);
    unsigned gbm_good = 0;
    if (m) {
        gbm_good = count_good(m, map_stride, &first);
        gbm_bo_unmap(bo, map_data);
    }
    printf("STAGE1 case=%s gbm_readback=%s pixels=%u/%u first=0x%08x map_stride=%u\n", name,
           m && gbm_good == W * H ? "PASS" : "FAIL", gbm_good, W * H, m ? first : 0, map_stride);
    result = vk_good == W * H && m && gbm_good == W * H ? 0 : 1;
    printf("STAGE1 case=%s result=%s\n", name, result ? "FAIL" : "PASS");
out:
    if (fence) vkDestroyFence(dev, fence, NULL);
    if (pool) vkDestroyCommandPool(dev, pool, NULL);
    if (buf) vkDestroyBuffer(dev, buf, NULL);
    if (bmem) vkFreeMemory(dev, bmem, NULL);
    if (image) vkDestroyImage(dev, image, NULL);
    if (mem) vkFreeMemory(dev, mem, NULL);
    if (fd >= 0) close(fd);
    gbm_bo_destroy(bo);
    if (result < 0) printf("STAGE1 case=%s result=ERROR\n", name);
    return result;
}

int main(void) {
    setbuf(stdout, NULL);
    printf("STAGE1 page_size=%ld\n", sysconf(_SC_PAGESIZE));
    list_nodes();
    int fd = open_asahi_render();
    if (fd < 0) { puts("STAGE1 error no asahi render node"); return 2; }
    struct gbm_device *gbm = gbm_create_device(fd);
    if (!gbm) { puts("STAGE1 error gbm_create_device failed"); return 3; }
    printf("STAGE1 gbm_backend=%s\n", gbm_device_get_backend_name(gbm));
    if (init_vulkan()) return 4;
    /* Apple vendor modifiers from drm_fourcc.h: GPU_TILED and GPU_COMPRESSED. */
    const uint64_t tiled = 0x0c00000000000001ull, compressed = 0x0c00000000000002ull;
    int linear = run_case(gbm, "linear", GBM_BO_USE_RENDERING | GBM_BO_USE_LINEAR, NULL);
    int native = run_case(gbm, "driver-layout", GBM_BO_USE_RENDERING, NULL);
    int tile = run_case(gbm, "apple-tiled", GBM_BO_USE_RENDERING, &tiled);
    int comp = run_case(gbm, "apple-compressed", GBM_BO_USE_RENDERING, &compressed);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    gbm_device_destroy(gbm);
    close(fd);
    printf("STAGE1 summary linear=%s driver-layout=%s apple-tiled=%s apple-compressed=%s\n",
           linear ? "FAIL" : "PASS", native ? "FAIL" : "PASS", tile ? "FAIL" : "PASS", comp ? "FAIL" : "PASS");
    return linear || native || tile || comp ? 1 : 0;
}
