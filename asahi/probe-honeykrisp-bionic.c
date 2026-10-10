/* Graphics stage 2d: an Android (Bionic) program that dlopens the stage 2c Honeykrisp
 * library directly, without Android's Vulkan loader, and runs the same 4 KiB buffer
 * fill and readback as guest/guest_vulkan_probe.c on the Asahi GPU. */
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define WORDS 1024
#define PATTERN 0x47424f53u /* "GBOS" */

/* From vk_icd.h, which the NDK sysroot does not ship. */
typedef VkResult (VKAPI_PTR *PFN_icd_negotiate)(uint32_t *version);

static PFN_vkGetInstanceProcAddr gipa;

#define CHECK(call) do { VkResult r_ = (call); printf("HK_BIONIC %s = %d\n", #call, r_); \
    if (r_ != VK_SUCCESS) return 1; } while (0)
#define LOAD(inst, name) PFN_##name name = (PFN_##name)gipa(inst, #name); \
    if (!name) { printf("HK_BIONIC missing entry point %s\n", #name); return 1; }

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    if (argc != 2) { fprintf(stderr, "usage: %s /path/to/vulkan.asahi.so\n", argv[0]); return 64; }
    printf("HK_BIONIC page_size=%ld\n", sysconf(_SC_PAGESIZE));
    void *icd = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!icd) { printf("HK_BIONIC dlopen=FAIL %s\n", dlerror()); return 2; }
    puts("HK_BIONIC dlopen=PASS");
    PFN_icd_negotiate negotiate =
        (PFN_icd_negotiate)dlsym(icd, "vk_icdNegotiateLoaderICDInterfaceVersion");
    if (negotiate) {
        uint32_t version = 5;
        printf("HK_BIONIC icd_interface negotiate=%d version=%u\n", negotiate(&version), version);
    }
    gipa = (PFN_vkGetInstanceProcAddr)dlsym(icd, "vk_icdGetInstanceProcAddr");
    if (!gipa) { puts("HK_BIONIC missing vk_icdGetInstanceProcAddr"); return 3; }

    LOAD(NULL, vkCreateInstance);
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "gbos stage2d",
                            .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
    VkInstance inst;
    CHECK(vkCreateInstance(&ici, NULL, &inst));
    LOAD(inst, vkEnumeratePhysicalDevices);
    LOAD(inst, vkGetPhysicalDeviceProperties);
    LOAD(inst, vkGetPhysicalDeviceQueueFamilyProperties);
    LOAD(inst, vkGetPhysicalDeviceMemoryProperties);
    LOAD(inst, vkCreateDevice);
    LOAD(inst, vkGetDeviceProcAddr);
    LOAD(inst, vkDestroyInstance);
    uint32_t count = 1;
    VkPhysicalDevice phys;
    VkResult er = vkEnumeratePhysicalDevices(inst, &count, &phys);
    printf("HK_BIONIC vkEnumeratePhysicalDevices = %d count=%u\n", er, count);
    if ((er != VK_SUCCESS && er != VK_INCOMPLETE) || !count) return 4;
    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(phys, &props);
    printf("HK_BIONIC device=%s api=%u.%u.%u type=%d\n", props.deviceName, VK_VERSION_MAJOR(props.apiVersion),
           VK_VERSION_MINOR(props.apiVersion), VK_VERSION_PATCH(props.apiVersion), props.deviceType);

    uint32_t nq = 8;
    VkQueueFamilyProperties qf[8];
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &nq, qf);
    uint32_t fam = UINT32_MAX;
    for (uint32_t i = 0; i < nq; i++) if (qf[i].queueFlags & VK_QUEUE_TRANSFER_BIT || qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) { fam = i; break; }
    if (fam == UINT32_MAX) { puts("HK_BIONIC no transfer queue"); return 5; }
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = fam,
                                   .queueCount = 1, .pQueuePriorities = &prio};
    VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
                              .pQueueCreateInfos = &qci};
    VkDevice dev;
    CHECK(vkCreateDevice(phys, &dci, NULL, &dev));
#define DLOAD(name) PFN_##name name = (PFN_##name)vkGetDeviceProcAddr(dev, #name); \
    if (!name) { printf("HK_BIONIC missing device entry point %s\n", #name); return 1; }
    DLOAD(vkGetDeviceQueue); DLOAD(vkCreateBuffer); DLOAD(vkGetBufferMemoryRequirements);
    DLOAD(vkAllocateMemory); DLOAD(vkBindBufferMemory); DLOAD(vkCreateCommandPool);
    DLOAD(vkAllocateCommandBuffers); DLOAD(vkBeginCommandBuffer); DLOAD(vkCmdFillBuffer);
    DLOAD(vkCmdPipelineBarrier); DLOAD(vkEndCommandBuffer); DLOAD(vkCreateFence); DLOAD(vkQueueSubmit);
    DLOAD(vkWaitForFences); DLOAD(vkMapMemory); DLOAD(vkInvalidateMappedMemoryRanges); DLOAD(vkDestroyDevice);
    VkQueue queue;
    vkGetDeviceQueue(dev, fam, 0, &queue);

    VkBufferCreateInfo bci = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = WORDS * 4,
                              .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT};
    VkBuffer buf;
    CHECK(vkCreateBuffer(dev, &bci, NULL, &buf));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, buf, &req);
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(phys, &mp);
    uint32_t type = UINT32_MAX;
    for (uint32_t i = 0; i < mp.memoryTypeCount && type == UINT32_MAX; i++)
        if ((req.memoryTypeBits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT)) type = i;
    if (type == UINT32_MAX) { puts("HK_BIONIC no host-visible memory"); return 6; }
    VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = req.size,
                                .memoryTypeIndex = type};
    VkDeviceMemory mem;
    CHECK(vkAllocateMemory(dev, &mai, NULL, &mem));
    CHECK(vkBindBufferMemory(dev, buf, mem, 0));
    VkCommandPoolCreateInfo pci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = fam};
    VkCommandPool pool;
    CHECK(vkCreateCommandPool(dev, &pci, NULL, &pool));
    VkCommandBufferAllocateInfo cai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool,
                                       .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer cb;
    CHECK(vkAllocateCommandBuffers(dev, &cai, &cb));
    /* Mesa's dispatchable objects carry their own dispatch; no loader trampoline is needed. */
    VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                   .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    CHECK(vkBeginCommandBuffer(cb, &bi));
    vkCmdFillBuffer(cb, buf, 0, VK_WHOLE_SIZE, PATTERN);
    VkBufferMemoryBarrier host = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buf, .size = VK_WHOLE_SIZE};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1, &host, 0, NULL);
    CHECK(vkEndCommandBuffer(cb));
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence;
    CHECK(vkCreateFence(dev, &fci, NULL, &fence));
    VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
    CHECK(vkQueueSubmit(queue, 1, &si, fence));
    CHECK(vkWaitForFences(dev, 1, &fence, VK_TRUE, 10000000000ull));
    void *p;
    CHECK(vkMapMemory(dev, mem, 0, VK_WHOLE_SIZE, 0, &p));
    VkMappedMemoryRange range = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, .memory = mem, .size = VK_WHOLE_SIZE};
    CHECK(vkInvalidateMappedMemoryRanges(dev, 1, &range));
    unsigned good = 0;
    for (unsigned i = 0; i < WORDS; i++) good += ((const uint32_t *)p)[i] == PATTERN;
    printf("HK_BIONIC readback=%s words=%u/%u first=0x%08x\n", good == WORDS ? "PASS" : "FAIL", good, WORDS,
           ((const uint32_t *)p)[0]);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return good == WORDS ? 0 : 7;
}
