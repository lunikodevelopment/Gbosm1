/* Native Binder object + sealed memfd transfer prerequisite, not Android IPC. */
#define main native_echo_main
#include "probe-binder.c"
#undef main
#include <stddef.h>
#include <sys/stat.h>

enum { TRANSFER_CODE = 0x1617, CALL_CODE = 0x1618, FINISH_CODE = 0x1619,
       FILE_BYTES = 16384 };
static const binder_uintptr_t object_id = 0x47424f530001ULL;
static const binder_uintptr_t object_cookie = 0x47424f530002ULL;
struct transfer_reply {
    struct flat_binder_object object;
    struct binder_fd_object descriptor;
};
static void handle_command(int fd, uint32_t command, uint32_t handle) {
    unsigned char stream[8];
    memcpy(stream, &command, 4); memcpy(stream + 4, &handle, 4);
    exchange(fd, stream, sizeof(stream), NULL, 0);
}
static void mapped_range(struct connection c, binder_uintptr_t address, size_t bytes) {
    uintptr_t start = (uintptr_t)c.map;
    require(bytes <= MAP_BYTES && address >= start && address - start <= MAP_BYTES - bytes,
            "transfer parcel range outside Binder mapping");
}
static struct binder_transaction_data transfer_receive(struct connection c, uint32_t expected) {
    unsigned char buf[4096];
    for (;;) {
        size_t size = exchange(c.fd, NULL, 0, buf, sizeof(buf));
        struct binder_transaction_data result = {0};
        int found = 0;
        /* Drain the whole return stream, including reference notifications after
         * a transaction, before handing its still-live mapped buffer to caller. */
        for (size_t pos = 0; pos < size;) {
            uint32_t cmd;
            require(size - pos >= 4, "truncated Binder transfer command");
            memcpy(&cmd, buf + pos, 4); pos += 4;
            size_t n = _IOC_SIZE(cmd);
            require(n <= size - pos, "truncated Binder transfer notification");
            if (cmd == expected) {
                require(!found && n == sizeof(result), "unexpected transfer transaction layout");
                memcpy(&result, buf + pos, n); found = 1;
                require(!(result.flags & (TF_STATUS_CODE | TF_ONE_WAY)), "transfer returned status/error or one-way transaction");
                require(result.data_size <= 256 && result.offsets_size <= 2 * sizeof(binder_size_t),
                        "transfer parcel exceeds probe limits");
                mapped_range(c, result.data.ptr.buffer, result.data_size);
                if (result.offsets_size) mapped_range(c, result.data.ptr.offsets, result.offsets_size);
            } else if (cmd == BR_INCREFS || cmd == BR_ACQUIRE) {
                struct binder_ptr_cookie cookie;
                require(n == sizeof(cookie), "unexpected object reference notification");
                memcpy(&cookie, buf + pos, n);
                require(cookie.ptr == object_id && cookie.cookie == object_cookie,
                        "reference notification targets unknown local object");
                uint32_t ack = cmd == BR_INCREFS ? BC_INCREFS_DONE : BC_ACQUIRE_DONE;
                unsigned char stream[4 + sizeof(cookie)];
                memcpy(stream, &ack, 4); memcpy(stream + 4, &cookie, sizeof(cookie));
                exchange(c.fd, stream, sizeof(stream), NULL, 0);
            } else {
                if (cmd == BR_FAILED_REPLY || cmd == BR_DEAD_REPLY || cmd == BR_ERROR)
                    fprintf(stderr, "TRANSFER_DRIVER_ERROR command=0x%x\n", cmd);
                require(cmd == BR_NOOP || cmd == BR_TRANSACTION_COMPLETE || cmd == BR_SPAWN_LOOPER ||
                        cmd == BR_RELEASE || cmd == BR_DECREFS, "unexpected transfer Binder return command");
            }
            pos += n;
        }
        if (found) return result;
    }
}
static void verify_offsets(struct binder_transaction_data t, const binder_size_t *offsets, size_t count) {
    require(t.offsets_size == count * sizeof(*offsets), "wrong transferred object count");
    require(memcmp((void *)(uintptr_t)t.data.ptr.offsets, offsets, t.offsets_size) == 0,
            "wrong transferred object offsets");
}
static unsigned char file_byte(size_t i) { return (unsigned char)((i * 37u + 11u) & 255u); }
static void verify_file(int fd, const char *side) {
    struct stat info;
    if (fstat(fd, &info)) fail("stat transferred memfd");
    require(S_ISREG(info.st_mode) && info.st_size == FILE_BYTES, "wrong transferred file size/type");
    int seals = fcntl(fd, F_GET_SEALS);
    int required = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE;
    require(seals >= 0 && (seals & required) == required, "transferred memfd is not fully sealed");
    int descriptor_flags = fcntl(fd, F_GETFD);
    require(descriptor_flags >= 0 && (descriptor_flags & FD_CLOEXEC) != 0,
            "received descriptor missing close-on-exec");
    int access_flags = fcntl(fd, F_GETFL);
    require(access_flags >= 0 && (access_flags & O_ACCMODE) == O_RDONLY,
            "transferred descriptor must be read-only");
    unsigned char *map = mmap(NULL, FILE_BYTES, PROT_READ, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) fail("map transferred memfd");
    for (size_t i = 0; i < FILE_BYTES; i++) require(map[i] == file_byte(i), "transferred memory contents differ");
    if (munmap(map, FILE_BYTES)) fail("unmap transferred memfd");
    unsigned char bytes[FILE_BYTES];
    require(pread(fd, bytes, sizeof(bytes), 0) == (ssize_t)sizeof(bytes), "pread transferred memfd failed");
    for (size_t i = 0; i < sizeof(bytes); i++) require(bytes[i] == file_byte(i), "transferred file read differs");
    printf("BINDER_FD_%s=PASS: 16384 bytes, full contents, seals and CLOEXEC verified.\n", side);
}
static void verify_echo(struct binder_transaction_data t) {
    require(t.offsets_size == 0 && t.data_size == sizeof(payload), "unexpected callback payload layout");
    require(memcmp((void *)(uintptr_t)t.data.ptr.buffer, payload, sizeof(payload)) == 0,
            "object callback payload differs");
}
static void reply_echo(struct connection c, struct binder_transaction_data request) {
    struct binder_transaction_data reply = {0};
    reply.data_size = sizeof(payload); reply.data.ptr.buffer = (uintptr_t)payload;
    transaction_command(c.fd, BC_REPLY, &reply);
    free_buffer(c.fd, request.data.ptr.buffer);
}
static void transfer_server(const char *path, int ready) {
    alarm(20);
    struct connection c = connect_device(path);
    struct flat_binder_object manager = {0};
    manager.hdr.type = BINDER_TYPE_BINDER;
    manager.flags = FLAT_BINDER_FLAG_ACCEPTS_FDS;
    if (ioctl(c.fd, BINDER_SET_CONTEXT_MGR_EXT, &manager)) fail("transfer BINDER_SET_CONTEXT_MGR_EXT");
    uint32_t enter = BC_ENTER_LOOPER;
    exchange(c.fd, &enter, 4, NULL, 0);
    require(write(ready, "R", 1) == 1, "transfer readiness failed"); close(ready);
    struct binder_transaction_data request = transfer_receive(c, BR_TRANSACTION);
    require(request.code == TRANSFER_CODE && (request.flags & TF_ACCEPT_FDS) &&
            request.data_size == sizeof(struct binder_fd_object), "unexpected FD request");
    const binder_size_t request_offsets[] = {0};
    verify_offsets(request, request_offsets, 1);
    struct binder_fd_object received;
    memcpy(&received, (void *)(uintptr_t)request.data.ptr.buffer, sizeof(received));
    require(received.hdr.type == BINDER_TYPE_FD && received.fd <= INT32_MAX, "missing transferred FD");
    verify_file((int)received.fd, "SERVER");
    struct transfer_reply data = {0};
    data.object.hdr.type = BINDER_TYPE_BINDER;
    data.object.flags = FLAT_BINDER_FLAG_ACCEPTS_FDS;
    data.object.binder = object_id; data.object.cookie = object_cookie;
    data.descriptor.hdr.type = BINDER_TYPE_FD; data.descriptor.fd = received.fd;
    const binder_size_t offsets[] = {offsetof(struct transfer_reply, object), offsetof(struct transfer_reply, descriptor)};
    struct binder_transaction_data reply = {0};
    reply.data_size = sizeof(data); reply.data.ptr.buffer = (uintptr_t)&data;
    reply.offsets_size = sizeof(offsets); reply.data.ptr.offsets = (uintptr_t)offsets;
    transaction_command(c.fd, BC_REPLY, &reply);
    free_buffer(c.fd, request.data.ptr.buffer);
    if (close((int)received.fd)) fail("close server received FD");
    request = transfer_receive(c, BR_TRANSACTION);
    require(request.code == CALL_CODE && request.target.ptr == object_id && request.cookie == object_cookie,
            "call did not reach returned Binder object");
    verify_echo(request); reply_echo(c, request);
    puts("BINDER_OBJECT_SERVER=PASS: returned object identity and callback payload verified.");
    request = transfer_receive(c, BR_TRANSACTION);
    require(request.code == FINISH_CODE, "missing final client acknowledgement");
    verify_echo(request); reply_echo(c, request);
    if (munmap(c.map, MAP_BYTES) || close(c.fd)) fail("close transfer server");
    puts("BINDER_TRANSFER_SERVER=PASS");
}
static void call_echo(struct connection c, uint32_t handle, uint32_t code) {
    struct binder_transaction_data request = {0};
    request.target.handle = handle; request.code = code;
    request.data_size = sizeof(payload); request.data.ptr.buffer = (uintptr_t)payload;
    transaction_command(c.fd, BC_TRANSACTION, &request);
    struct binder_transaction_data reply = transfer_receive(c, BR_REPLY);
    verify_echo(reply); free_buffer(c.fd, reply.data.ptr.buffer);
}
int main(int argc, char **argv) {
    if (signal(SIGALRM, timeout_exit) == SIG_ERR) fail("install transfer alarm");
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 4 && strcmp(argv[1], "--server") == 0) {
        char *end; long ready = strtol(argv[3], &end, 10);
        require(*end == 0 && ready >= 0 && ready <= 1024, "invalid transfer ready FD");
        transfer_server(argv[2], (int)ready); return 0;
    }
    if (argc == 3 && strcmp(argv[2], "--describe") == 0) return native_echo_main(argc, argv);
    require(argc == 2 && sysconf(_SC_PAGESIZE) == FILE_BYTES, "requires mount directory and 16 KiB pages");
    alarm(25);
    if (mount("binder", argv[1], "binder", 0, NULL)) fail("mount transfer binderfs");
    char control_path[4096], device_path[4096];
    require(snprintf(control_path, sizeof(control_path), "%s/binder-control", argv[1]) < (int)sizeof(control_path), "control path too long");
    require(snprintf(device_path, sizeof(device_path), "%s/asahi-transfer", argv[1]) < (int)sizeof(device_path), "device path too long");
    int control = open(control_path, O_RDWR | O_CLOEXEC);
    if (control < 0) fail("open transfer binder-control");
    struct binderfs_device device = {0}; strcpy(device.name, "asahi-transfer");
    if (ioctl(control, BINDER_CTL_ADD, &device)) fail("create transfer device");
    close(control);
    int ready[2]; if (pipe(ready)) fail("transfer readiness pipe");
    pid_t child = fork(); if (child < 0) fail("fork transfer server");
    if (child == 0) {
        close(ready[0]); char text[32]; snprintf(text, sizeof(text), "%d", ready[1]);
        execl("/usr/bin/runcon", "runcon", "-t", "gbos_binder_probe_t", argv[0],
              "--server", device_path, text, (char *)NULL); fail("runcon transfer server");
    }
    close(ready[1]); char token;
    require(read(ready[0], &token, 1) == 1 && token == 'R', "transfer server stopped before readiness"); close(ready[0]);
    /* Created after server fork: its only path to this file is Binder FD transfer. */
    int source = memfd_create("gbos-binder-transfer", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (source < 0) fail("create sealed transfer memfd");
    char memfd_context[512];
    ssize_t context_bytes = fgetxattr(source, "security.selinux", memfd_context, sizeof(memfd_context) - 1);
    if (context_bytes < 0) fail("read transfer memfd SELinux context");
    memfd_context[context_bytes] = 0;
    printf("BINDER_MEMFD_CONTEXT=%s\n", memfd_context);
    unsigned char bytes[FILE_BYTES];
    for (size_t i = 0; i < sizeof(bytes); i++) bytes[i] = file_byte(i);
    require(write(source, bytes, sizeof(bytes)) == (ssize_t)sizeof(bytes), "write transfer memfd failed");
    if (fcntl(source, F_ADD_SEALS, F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE)) fail("seal transfer memfd");
    /* A seal blocks writes but does not change O_RDWR. Binder's SELinux FD
     * transfer hook checks file access mode, so send a genuinely read-only
     * open file description and do not grant the server write permission. */
    char source_path[64]; snprintf(source_path, sizeof(source_path), "/proc/self/fd/%d", source);
    int readonly = open(source_path, O_RDONLY | O_CLOEXEC);
    if (readonly < 0) fail("reopen sealed memfd read-only");
    if (close(source)) fail("close original writable memfd");
    source = readonly;
    struct connection c = connect_device(device_path);
    struct binder_fd_object sent = {0}; sent.hdr.type = BINDER_TYPE_FD; sent.fd = (uint32_t)source;
    binder_size_t fd_offset = 0;
    struct binder_transaction_data request = {0};
    request.target.handle = 0; request.code = TRANSFER_CODE; request.flags = TF_ACCEPT_FDS;
    request.data_size = sizeof(sent); request.data.ptr.buffer = (uintptr_t)&sent;
    request.offsets_size = sizeof(fd_offset); request.data.ptr.offsets = (uintptr_t)&fd_offset;
    transaction_command(c.fd, BC_TRANSACTION, &request);
    struct binder_transaction_data reply = transfer_receive(c, BR_REPLY);
    require(reply.data_size == sizeof(struct transfer_reply), "unexpected transfer reply size");
    const binder_size_t offsets[] = {offsetof(struct transfer_reply, object), offsetof(struct transfer_reply, descriptor)};
    verify_offsets(reply, offsets, 2);
    struct transfer_reply received;
    memcpy(&received, (void *)(uintptr_t)reply.data.ptr.buffer, sizeof(received));
    require(received.object.hdr.type == BINDER_TYPE_HANDLE && received.object.handle != 0,
            "local Binder object was not translated to a handle");
    require(received.descriptor.hdr.type == BINDER_TYPE_FD && received.descriptor.fd <= INT32_MAX &&
            (int)received.descriptor.fd != source, "returned FD was not installed separately");
    struct stat before, after;
    if (fstat(source, &before) || fstat((int)received.descriptor.fd, &after)) fail("stat FD identity");
    require(before.st_dev == after.st_dev && before.st_ino == after.st_ino, "returned FD identifies another file");
    verify_file((int)received.descriptor.fd, "CLIENT");
    /* Retain the received strong handle before freeing its parcel buffer. */
    handle_command(c.fd, BC_ACQUIRE, received.object.handle);
    free_buffer(c.fd, reply.data.ptr.buffer);
    if (close(source) || close((int)received.descriptor.fd)) fail("close client memory FDs");
    call_echo(c, received.object.handle, CALL_CODE);
    puts("BINDER_OBJECT_CLIENT=PASS: translated nonzero handle returned verified callback reply.");
    handle_command(c.fd, BC_RELEASE, received.object.handle);
    call_echo(c, 0, FINISH_CODE);
    if (munmap(c.map, MAP_BYTES) || close(c.fd)) fail("close transfer client");
    int status;
    require(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0,
            "transfer server failed");
    if (umount(argv[1])) fail("unmount transfer binderfs");
    puts("BINDER_TRANSFER=PASS: native Binder object call and sealed memfd round trip verified.");
    return 0;
}
