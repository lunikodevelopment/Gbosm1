/* Controlled native endpoint for the image's unmodified Android service CLI.
 * Supports only checkService2(Service union), interface/ping and one FD call.
 * Android receives/calls the returned Binder object and writes an FD Parcel.
 * Wire reference: platform/frameworks/native revision
 * 4f463a6b1de9198963dc6aff74154a504ba3f8f6: IServiceManager, Service,
 * ServiceWithMetadata AIDL; Parcel.cpp; cmds/service/service.cpp; Stability.h.
 * Unknown image layouts stop and retain the transaction hex for diagnosis.
 * No real servicemanager or HAL is implemented here.
 */
#define GBOS_PARCEL_MODE 1
#include "probe-libbinder.c"

_Static_assert(sizeof(struct flat_binder_object) == 24 && sizeof(struct binder_fd_object) == 24,
               "requires the 64-bit Binder object ABI");

enum { PARCEL_CODE = 5658, PARCEL_BYTES = 16384, PARCEL_RECEIPT = 0x47504231 };
static const binder_uintptr_t parcel_object = 0x47424f530101ULL;
static const binder_uintptr_t parcel_cookie = 0x47424f530102ULL;
static const char parcel_descriptor[] = "org.gbos.ParcelProbe";
static unsigned char parcel_byte(size_t i) { return (unsigned char)((i * 37u + 11u) & 255u); }
static int parcel_setup_input(void) {
    /* Called after the server fork; no source descriptor is inherited there. */
    int fd = memfd_create("gbos-android-parcel", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (fd < 0) fail("create Android Parcel memfd");
    char context[512]; ssize_t size = fgetxattr(fd, "security.selinux", context, sizeof(context) - 1);
    if (size < 0) fail("read Android Parcel memfd context");
    context[size] = 0; printf("BINDER_MEMFD_CONTEXT=%s\n", context);
    unsigned char data[PARCEL_BYTES];
    for (size_t i = 0; i < sizeof(data); i++) data[i] = parcel_byte(i);
    require(write(fd, data, sizeof(data)) == (ssize_t)sizeof(data), "write Android Parcel input failed");
    if (fcntl(fd, F_ADD_SEALS, F_SEAL_SEAL | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_WRITE))
        fail("seal Android Parcel input");
    char path[64]; snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
    /* Anonymous memfd mounts cannot be bind-mounted on this target (EINVAL).
     * Reopen read-only, then give only the Android client this file as stdin.
     * strace and linker exec preserve stdin; service opens /proc/self/fd/0
     * O_RDONLY and writes that new descriptor through its own Parcel API. */
    int input = open(path, O_RDONLY | O_CLOEXEC);
    if (input < 0) fail("reopen sealed Parcel input read-only");
    require(input > STDERR_FILENO, "Parcel input overlaps standard descriptors");
    if (close(fd)) fail("close launcher Parcel source FD");
    puts("PARCEL_INPUT=READY: sealed 16384-byte read-only fixture prepared for client stdin; server has no inherited copy.");
    return input;
}
static void parcel_range(struct connection c, binder_uintptr_t address, size_t bytes) {
    uintptr_t start = (uintptr_t)c.map;
    require(bytes <= MAP_BYTES && address >= start && address - start <= MAP_BYTES - bytes,
            "Android Parcel range outside Binder mapping");
}
static struct binder_transaction_data parcel_receive(struct connection c) {
    unsigned char buf[4096];
    for (;;) {
        size_t size = exchange(c.fd, NULL, 0, buf, sizeof(buf));
        int found = 0; struct binder_transaction_data request = {0};
        for (size_t pos = 0; pos < size;) {
            uint32_t cmd;
            require(size - pos >= 4, "truncated Android Parcel return command");
            memcpy(&cmd, buf + pos, 4); pos += 4;
            size_t n = _IOC_SIZE(cmd);
            require(n <= size - pos, "truncated Android Parcel return payload");
            if (cmd == BR_TRANSACTION) {
                require(!found && n == sizeof(request), "unexpected Android Parcel transaction layout");
                memcpy(&request, buf + pos, n); found = 1;
                require(!(request.flags & (TF_ONE_WAY | TF_STATUS_CODE)), "unsupported Android Parcel flags");
                require(request.data_size <= 512 && request.offsets_size <= sizeof(binder_size_t),
                        "Android Parcel exceeds endpoint limits");
                parcel_range(c, request.data.ptr.buffer, request.data_size);
                if (request.offsets_size) parcel_range(c, request.data.ptr.offsets, request.offsets_size);
            } else if (cmd == BR_INCREFS || cmd == BR_ACQUIRE) {
                struct binder_ptr_cookie cookie;
                require(n == sizeof(cookie), "unexpected Android object reference layout");
                memcpy(&cookie, buf + pos, n);
                require(cookie.ptr == parcel_object && cookie.cookie == parcel_cookie,
                        "reference targets unknown Parcel endpoint object");
                uint32_t ack = cmd == BR_INCREFS ? BC_INCREFS_DONE : BC_ACQUIRE_DONE;
                unsigned char stream[4 + sizeof(cookie)];
                memcpy(stream, &ack, 4); memcpy(stream + 4, &cookie, sizeof(cookie));
                exchange(c.fd, stream, sizeof(stream), NULL, 0);
            } else {
                if (cmd == BR_FAILED_REPLY || cmd == BR_DEAD_REPLY || cmd == BR_ERROR)
                    fprintf(stderr, "PARCEL_DRIVER_ERROR command=0x%x\n", cmd);
                require(cmd == BR_NOOP || cmd == BR_TRANSACTION_COMPLETE || cmd == BR_SPAWN_LOOPER ||
                        cmd == BR_RELEASE || cmd == BR_DECREFS, "unsupported Android Parcel return command");
            }
            pos += n;
        }
        if (found) {
            printf("PARCEL_TRANSACTION code=%u target=0x%llx bytes=%llu offsets=%llu\n", request.code,
                   (unsigned long long)request.target.ptr, (unsigned long long)request.data_size,
                   (unsigned long long)request.offsets_size);
            const unsigned char *data = (void *)(uintptr_t)request.data.ptr.buffer;
            printf("PARCEL_HEX=");
            for (size_t i = 0; i < request.data_size; i++) printf("%02x", data[i]);
            puts("");
            return request;
        }
    }
}
static size_t parcel_string(const unsigned char *data, size_t length, size_t pos, const char *expected) {
    size_t chars = strlen(expected);
    require(pos <= length && length - pos >= 4, "truncated Parcel String16 length");
    int32_t actual; memcpy(&actual, data + pos, 4); pos += 4;
    require(actual >= 0 && (size_t)actual == chars && chars <= 64 &&
            length - pos >= (chars + 1) * 2, "unexpected Parcel String16 length");
    for (size_t i = 0; i <= chars; i++) {
        uint16_t ch; memcpy(&ch, data + pos + i * 2, 2);
        require(ch == (unsigned char)expected[i], "Parcel String16 differs");
    }
    pos = (pos + (chars + 1) * 2 + 3) & ~(size_t)3;
    require(pos <= length, "Parcel String16 padding truncated");
    return pos;
}
static size_t parcel_token(struct binder_transaction_data request, const char *descriptor) {
    const unsigned char *data = (void *)(uintptr_t)request.data.ptr.buffer;
    require(request.data_size >= 16, "Android Parcel interface header truncated");
    uint32_t header; memcpy(&header, data + 8, 4);
    require(header == 0x53595354, "Android Parcel system header differs");
    return parcel_string(data, request.data_size, 12, descriptor);
}
static size_t encode_string(unsigned char *data, const char *text) {
    int32_t chars = strlen(text); memcpy(data, &chars, 4);
    for (int32_t i = 0; i <= chars; i++) { uint16_t ch = (unsigned char)text[i]; memcpy(data + 4 + i * 2, &ch, 2); }
    return (4 + (chars + 1) * 2 + 3) & ~(size_t)3;
}
static void parcel_verify_fd(int fd) {
    struct stat info;
    if (fstat(fd, &info)) fail("stat Android Parcel FD");
    require(S_ISREG(info.st_mode) && info.st_size == PARCEL_BYTES, "Android Parcel FD size/type differs");
    int flags = fcntl(fd, F_GETFL), descriptor_flags = fcntl(fd, F_GETFD), seals = fcntl(fd, F_GET_SEALS);
    const int required = F_SEAL_SEAL | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_WRITE;
    require(flags >= 0 && (flags & O_ACCMODE) == O_RDONLY && descriptor_flags >= 0 &&
            (descriptor_flags & FD_CLOEXEC) && seals >= 0 && (seals & required) == required,
            "Android Parcel FD access mode, seals or CLOEXEC differs");
    unsigned char *map = mmap(NULL, PARCEL_BYTES, PROT_READ, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) fail("map Android Parcel FD");
    for (size_t i = 0; i < PARCEL_BYTES; i++) require(map[i] == parcel_byte(i), "Android Parcel FD mapping differs");
    if (munmap(map, PARCEL_BYTES)) fail("unmap Android Parcel FD");
    unsigned char data[PARCEL_BYTES];
    require(pread(fd, data, sizeof(data), 0) == (ssize_t)sizeof(data), "read Android Parcel FD failed");
    for (size_t i = 0; i < sizeof(data); i++) require(data[i] == parcel_byte(i), "Android Parcel FD read differs");
    puts("ANDROID_FD_WRITE=PASS: Android Parcel delivered a read-only sealed FD with verified 16384-byte contents.");
}
static void parcel_server(const char *path, int ready) {
    alarm(20);
    struct connection c = connect_device(path);
    struct flat_binder_object manager = {0}; manager.hdr.type = BINDER_TYPE_BINDER;
    manager.flags = FLAT_BINDER_FLAG_ACCEPTS_FDS;
    if (ioctl(c.fd, BINDER_SET_CONTEXT_MGR_EXT, &manager)) fail("Parcel BINDER_SET_CONTEXT_MGR_EXT");
    uint32_t enter = BC_ENTER_LOOPER; exchange(c.fd, &enter, 4, NULL, 0);
    require(write(ready, "R", 1) == 1, "Parcel endpoint readiness failed"); close(ready);
    int looked_up = 0, described = 0;
    for (unsigned count = 0; count < 12; count++) {
        struct binder_transaction_data request = parcel_receive(c);
        unsigned char response[256] = {0}; size_t length = 0;
        binder_size_t object_offset = 0; size_t object_count = 0;
        int received_fd = -1, completed = 0;
        const unsigned char *data = (void *)(uintptr_t)request.data.ptr.buffer;
        int endpoint = request.target.ptr == parcel_object && request.cookie == parcel_cookie;
        require(endpoint || (request.target.ptr == 0 && request.cookie == 0), "unknown Parcel target object");
        if (request.code == 0x5f504e47) {
            require(request.data_size == 0 && request.offsets_size == 0, "unexpected ping payload"); length = 4;
        } else if (request.code == 0x5f4e5446) {
            require(request.data_size == 0 && request.offsets_size == 0, "unexpected descriptor payload");
            length = encode_string(response, endpoint ? parcel_descriptor : "android.os.IServiceManager");
            if (endpoint) { require(looked_up, "endpoint descriptor before lookup"); described = 1; }
        } else if (!endpoint && request.code == 4) {
            require(!looked_up && request.offsets_size == 0, "unexpected repeated lookup or lookup objects");
            size_t pos = parcel_token(request, "android.os.IServiceManager");
            pos = parcel_string(data, request.data_size, pos, "gbos.parcel");
            require(pos == request.data_size, "unsupported checkService2 trailing layout");
            /* Status EX_NONE, Service union tag=serviceWithMetadata(0), then the
             * present word (1) read by this image's Parcel::readParcelable, then the
             * sized ServiceWithMetadata parcelable: binder + stability + lazy bool.
             * Binder begins at offset 16, which is legal 4-byte Parcel alignment. */
            int32_t present = 1, size = 36, stability = 12, lazy = 1;
            memcpy(response + 8, &present, 4); memcpy(response + 12, &size, 4);
            struct flat_binder_object object = {0}; object.hdr.type = BINDER_TYPE_BINDER;
            object.flags = FLAT_BINDER_FLAG_ACCEPTS_FDS; object.binder = parcel_object; object.cookie = parcel_cookie;
            memcpy(response + 16, &object, sizeof(object));
            memcpy(response + 40, &stability, 4); memcpy(response + 44, &lazy, 4);
            length = 48; object_offset = 16; object_count = 1; looked_up = 1;
            puts("PARCEL_LOOKUP_REPLY=READY: 48-byte reply (status, tag, present, size, binder with stability, lazy) for a controlled service Binder object.");
        } else if (endpoint && request.code == PARCEL_CODE) {
            require(looked_up && described && request.offsets_size == sizeof(binder_size_t),
                    "missing decoded object/descriptor or FD offset");
            size_t pos = parcel_token(request, parcel_descriptor);
            require(pos + 4 + sizeof(struct binder_fd_object) == request.data_size, "unsupported FD call layout");
            int32_t challenge; memcpy(&challenge, data + pos, 4); pos += 4;
            require(challenge == PARCEL_CODE, "Android Parcel challenge differs");
            binder_size_t offset; memcpy(&offset, (void *)(uintptr_t)request.data.ptr.offsets, sizeof(offset));
            require(offset == pos, "Android Parcel FD offset differs");
            struct binder_fd_object fd; memcpy(&fd, data + pos, sizeof(fd));
            require(fd.hdr.type == BINDER_TYPE_FD && fd.fd <= INT32_MAX && fd.cookie == 1,
                    "Android Parcel FD type/ownership differs");
            received_fd = (int)fd.fd; parcel_verify_fd(received_fd);
            puts("ANDROID_OBJECT_READ=PASS: image client decoded lookup object and called its verified identity/cookie.");
            int32_t receipt[] = {PARCEL_RECEIPT, PARCEL_BYTES}; memcpy(response, receipt, sizeof(receipt));
            length = sizeof(receipt); completed = 1;
        } else {
            require(0, "unsupported Android Parcel transaction; retain PARCEL_HEX and code");
        }
        struct binder_transaction_data reply = {0};
        reply.data_size = length; reply.data.ptr.buffer = (uintptr_t)response;
        if (object_count) { reply.offsets_size = sizeof(object_offset); reply.data.ptr.offsets = (uintptr_t)&object_offset; }
        transaction_command(c.fd, BC_REPLY, &reply); free_buffer(c.fd, request.data.ptr.buffer);
        if (received_fd >= 0 && close(received_fd)) fail("close Android Parcel received FD");
        if (completed) {
            if (munmap(c.map, MAP_BYTES) || close(c.fd)) fail("close Parcel endpoint");
            puts("PARCEL_ENDPOINT_REPLY=PASS: challenge verified and 47504231/00004000 receipt submitted."); return;
        }
    }
    require(0, "too many Android Parcel metadata requests");
}
