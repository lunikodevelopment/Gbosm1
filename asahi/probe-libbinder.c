/* Original project probe: minimal empty-list IServiceManager stub.
 * Protocol reference: AOSP android-16.0.0_r1 IServiceManager.aidl and
 * cmds/service/service.cpp. This is not a service-manager implementation.
 */
#define main native_echo_main
#include "probe-binder.c"
#undef main
#include <sys/stat.h>

/* Immutable, single-property fixture for Bionic's ContextsPreSplit reader.
 * Layout reference: platform/bionic revision
 * 731631f300090436d7f5df80d50b6275c8c60a93,
 * libc/system_properties/{prop_area.cpp,
 * include/system_properties/prop_area.h,include/system_properties/prop_info.h}.
 * The wire format is versioned; it is not a text build.prop file. No Android
 * property service or property API interposition is used by this probe.
 */
static void stub_ready_property(void) {
    enum { AREA_BYTES = 128 * 1024, HEADER_BYTES = 128, NODE_BYTES = 20,
           VALUE_BYTES = 92, ROOT = 0, SERVICE = NODE_BYTES + VALUE_BYTES,
           READY = SERVICE + 36, INFO = READY + 28, USED = INFO + 120 };
    unsigned char *area = calloc(1, AREA_BYTES);
    if (!area) fail("allocate readiness property area");
#define WORD(offset, value) do { uint32_t word = (value); memcpy(area + (offset), &word, 4); } while (0)
    WORD(0, USED); WORD(4, 1);
    WORD(8, 0x504f5250); WORD(12, 0xfc6ed0ab);
    /* Trie offsets are relative to data_, after the 128-byte header. The
     * root's 92-byte dirty-value backup remains zero; no writer is started. */
    WORD(HEADER_BYTES + ROOT + 16, SERVICE);
    WORD(HEADER_BYTES + SERVICE, 14);
    WORD(HEADER_BYTES + SERVICE + 16, READY);
    memcpy(area + HEADER_BYTES + SERVICE + NODE_BYTES, "servicemanager", 15);
    WORD(HEADER_BYTES + READY, 5);
    WORD(HEADER_BYTES + READY + 4, INFO);
    memcpy(area + HEADER_BYTES + READY + NODE_BYTES, "ready", 6);
    WORD(HEADER_BYTES + INFO, 4u << 24); /* length=4; dirty bit clear */
    memcpy(area + HEADER_BYTES + INFO + 4, "true", 5);
    memcpy(area + HEADER_BYTES + INFO + 4 + VALUE_BYTES,
           "servicemanager.ready", sizeof("servicemanager.ready"));
#undef WORD
    int fd = open("/dev/__properties__", O_CREAT | O_EXCL | O_RDWR | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) fail("create private readiness property area");
    struct stat info;
    if (fstat(fd, &info)) fail("stat readiness property area");
    require(info.st_uid == 0 && info.st_gid == 0 && S_ISREG(info.st_mode),
            "property area must be owned by namespace root");
    size_t done = 0;
    while (done < AREA_BYTES) {
        ssize_t n = write(fd, area + done, AREA_BYTES - done);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) fail("write readiness property area");
        done += (size_t)n;
    }
    free(area);
    if (fchmod(fd, 0444) || close(fd)) fail("seal readiness property area");
    puts("STUB_READY_PROPERTY=CREATED: private servicemanager.ready=true; Bionic consumption remains to be observed.");
}

#ifndef GBOS_PARCEL_MODE
static struct binder_transaction_data receive_request(struct connection c) {
    unsigned char buf[4096];
    for (;;) {
        size_t size = exchange(c.fd, NULL, 0, buf, sizeof(buf));
        for (size_t pos = 0; pos < size;) {
            uint32_t cmd;
            require(size - pos >= 4, "truncated return command");
            memcpy(&cmd, buf + pos, 4); pos += 4;
            size_t n = _IOC_SIZE(cmd);
            require(n <= size - pos, "truncated return payload");
            if (cmd == BR_TRANSACTION) {
                struct binder_transaction_data t;
                require(n == sizeof(t), "unexpected Binder transaction layout");
                memcpy(&t, buf + pos, n);
                require(t.data_size <= 1024 && t.offsets_size == 0, "unsupported parcel size or Binder objects");
                uintptr_t addr = t.data.ptr.buffer, start = (uintptr_t)c.map;
                require(addr >= start && addr - start <= MAP_BYTES - t.data_size,
                        "parcel outside Binder mapping");
                return t;
            }
            if (cmd == BR_INCREFS || cmd == BR_ACQUIRE) {
                struct binder_ptr_cookie cookie;
                require(n == sizeof(cookie), "unexpected reference notification");
                memcpy(&cookie, buf + pos, n);
                uint32_t ack = cmd == BR_INCREFS ? BC_INCREFS_DONE : BC_ACQUIRE_DONE;
                unsigned char stream[4 + sizeof(cookie)];
                memcpy(stream, &ack, 4); memcpy(stream + 4, &cookie, sizeof(cookie));
                exchange(c.fd, stream, sizeof(stream), NULL, 0);
            } else {
                require(cmd == BR_NOOP || cmd == BR_TRANSACTION_COMPLETE || cmd == BR_SPAWN_LOOPER ||
                        cmd == BR_RELEASE || cmd == BR_DECREFS, "unsupported Binder return command");
            }
            pos += n;
        }
    }
}
static void list_server(const char *path, int ready) {
    alarm(20);
    struct connection c = connect_device(path);
    int zero = 0;
    if (ioctl(c.fd, BINDER_SET_CONTEXT_MGR, &zero)) fail("stub BINDER_SET_CONTEXT_MGR");
    uint32_t enter = BC_ENTER_LOOPER;
    exchange(c.fd, &enter, 4, NULL, 0);
    require(write(ready, "R", 1) == 1, "stub readiness failed"); close(ready);
    const char descriptor[] = "android.os.IServiceManager";
    uint16_t descriptor16[sizeof(descriptor)];
    for (size_t i = 0; i < sizeof(descriptor); i++) descriptor16[i] = descriptor[i];
    for (unsigned count = 0; count < 8; count++) {
        struct binder_transaction_data request = receive_request(c);
        unsigned char *data = (void *)(uintptr_t)request.data.ptr.buffer;
        printf("ANDROID_PARCEL code=%u bytes=%llu flags=0x%x\n", request.code,
               (unsigned long long)request.data_size, request.flags);
        require((request.flags & TF_ONE_WAY) == 0, "one-way request unsupported");
        unsigned char response[256] = {0};
        size_t length;
        int listed = 0;
        if (request.code == 0x5f504e47) { /* IBinder PING_TRANSACTION: status OK */
            length = 4;
        } else if (request.code == 0x5f4e5446) { /* INTERFACE_TRANSACTION: String16 */
            int32_t chars = sizeof(descriptor) - 1;
            memcpy(response, &chars, 4); memcpy(response + 4, descriptor16, sizeof(descriptor16));
            length = (4 + sizeof(descriptor16) + 3) & ~(size_t)3;
        } else {
            /* AOSP reference orders listServices sixth. Unknown wire layouts stop. */
            require(request.code == 6 && request.data_size >= sizeof(descriptor16) + 8,
                    "unsupported service-manager transaction; retain parcel code/size");
            size_t token = 0;
            int found = 0;
            for (size_t i = 4; i + sizeof(descriptor16) <= request.data_size; i += 4) {
                int32_t chars;
                memcpy(&chars, data + i - 4, 4);
                if (chars == (int32_t)sizeof(descriptor) - 1 &&
                    memcmp(data + i, descriptor16, sizeof(descriptor16)) == 0) {
                    token = i; found = 1; break;
                }
            }
            require(found, "IServiceManager interface token missing");
            size_t priority_offset = (token + sizeof(descriptor16) + 3) & ~(size_t)3;
            require(priority_offset + 4 == request.data_size, "unsupported trailing parcel layout");
            int32_t priority;
            memcpy(&priority, data + priority_offset, 4);
            require(priority == 15, "unexpected listServices dump-priority flags");
            /* binder::Status EX_NONE (int32 0), followed by empty string-vector size. */
            length = 8; listed = 1;
            puts("Verified IServiceManager token and listServices priority=15; replying with empty list.");
        }
        struct binder_transaction_data reply = {0};
        reply.data_size = length; reply.data.ptr.buffer = (uintptr_t)response;
        transaction_command(c.fd, BC_REPLY, &reply);
        free_buffer(c.fd, request.data.ptr.buffer);
        if (listed) {
            munmap(c.map, MAP_BYTES); close(c.fd);
            puts("LIBBINDER_STUB_REPLY=PASS");
            return;
        }
    }
    require(0, "too many metadata requests before listServices");
}
#else
static void parcel_server(const char *path, int ready);
static int parcel_setup_input(void);
#endif
int main(int argc, char **argv) {
    if (signal(SIGALRM, timeout_exit) == SIG_ERR) fail("install alarm handler");
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 4 && strcmp(argv[1], "--server") == 0) {
        char *end; long ready = strtol(argv[3], &end, 10);
        require(*end == 0 && ready >= 0 && ready <= 1024, "bad readiness descriptor");
#ifdef GBOS_PARCEL_MODE
        parcel_server(argv[2], (int)ready);
#else
        list_server(argv[2], (int)ready);
#endif
        return 0;
    }
    if (argc == 3 && strcmp(argv[2], "--describe") == 0) return native_echo_main(argc, argv);
    require(argc == 3 && sysconf(_SC_PAGESIZE) == 16384, "requires mount directory, runtime and 16 KiB pages");
    alarm(25);
    if (mount("binder", argv[1], "binder", 0, NULL)) fail("mount private binderfs");
    char control_path[4096], device_path[4096], linker[4096], client[4096], libraries[4096], trace[4096];
#define PATH_JOIN(dst, root, leaf) require(snprintf(dst, sizeof(dst), "%s/%s", root, leaf) < (int)sizeof(dst), "path too long")
    PATH_JOIN(control_path, argv[1], "binder-control"); PATH_JOIN(device_path, argv[1], "asahi-service-stub");
    PATH_JOIN(linker, argv[2], "linker64"); PATH_JOIN(client, argv[2], "service"); PATH_JOIN(libraries, argv[2], "lib64");
    PATH_JOIN(trace, argv[2], "client.trace");
    int control = open(control_path, O_RDWR | O_CLOEXEC);
    if (control < 0) fail("open private control");
    struct binderfs_device device = {0}; strcpy(device.name, "asahi-service-stub");
    if (ioctl(control, BINDER_CTL_ADD, &device)) fail("create private device");
    close(control);
    /* Overlay /dev in this private mount namespace; keep original null via its fd. */
    int nullfd = open("/dev/null", O_PATH | O_CLOEXEC);
    if (nullfd < 0) fail("retain null device");
    if (mount("tmpfs", "/dev", "tmpfs", MS_NOSUID, "size=1m,mode=755")) fail("private /dev tmpfs overlay");
    int placeholder = open("/dev/binder", O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
    if (placeholder < 0) fail("private binder placeholder");
    close(placeholder);
    if (mount(device_path, "/dev/binder", NULL, MS_BIND, NULL)) fail("bind private Binder at private /dev/binder");
    placeholder = open("/dev/null", O_CREAT | O_WRONLY | O_CLOEXEC, 0600);
    if (placeholder < 0) fail("private null placeholder");
    close(placeholder);
    char nullsource[64]; snprintf(nullsource, sizeof(nullsource), "/proc/self/fd/%d", nullfd);
    if (mount(nullsource, "/dev/null", NULL, MS_BIND, NULL)) fail("bind private null device");
    close(nullfd);
    puts("Private /dev overlay active; only this namespace sees the probe's /dev/binder.");
    int ready[2]; if (pipe(ready)) fail("readiness pipe");
    pid_t server_pid = fork(); if (server_pid < 0) fail("fork server");
    if (server_pid == 0) {
        close(ready[0]); char text[32]; snprintf(text, sizeof(text), "%d", ready[1]);
        execl("/usr/bin/runcon", "runcon", "-t", "gbos_binder_probe_t", argv[0],
              "--server", device_path, text, (char *)NULL); fail("runcon service stub");
    }
    close(ready[1]); char token;
    require(read(ready[0], &token, 1) == 1 && token == 'R', "stub stopped before readiness"); close(ready[0]);
#ifdef GBOS_PARCEL_MODE
    int parcel_input = parcel_setup_input();
#endif
    stub_ready_property();
    pid_t client_pid = fork(); if (client_pid < 0) fail("fork Android client");
    if (client_pid == 0) {
        char library_env[sizeof(libraries) + 32];
        require(snprintf(library_env, sizeof(library_env), "LD_LIBRARY_PATH=%s", libraries) <
                (int)sizeof(library_env), "library environment too long");
#ifdef GBOS_PARCEL_MODE
        if (dup2(parcel_input, STDIN_FILENO) < 0) fail("install client read-only Parcel stdin");
        if (close(parcel_input)) fail("close client Parcel input duplicate");
        char output_path[4096]; PATH_JOIN(output_path, argv[2], "parcel-client.stdout");
        int output = open(output_path, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
        if (output < 0 || dup2(output, STDOUT_FILENO) < 0) fail("redirect Android Parcel output");
        close(output);
        execl("/usr/bin/strace", "strace", "-f", "-tt", "-T", "-yy", "-s", "160",
              "-o", trace, "-E", library_env, linker, client, "call", "gbos.parcel", "5658",
              "i32", "5658", "fd", "/proc/self/fd/0", (char *)NULL);
#else
        puts("Starting Android service list under client-only syscall tracing.");
        execl("/usr/bin/strace", "strace", "-f", "-tt", "-T", "-yy", "-s", "160",
              "-o", trace, "-E", library_env, linker, client, "list", (char *)NULL);
#endif
        fail("execute traced Android service client");
    }
#ifdef GBOS_PARCEL_MODE
    if (close(parcel_input)) fail("close launcher read-only Parcel input");
#endif
    int client_status, server_status;
    require(waitpid(client_pid, &client_status, 0) == client_pid && WIFEXITED(client_status) &&
            WEXITSTATUS(client_status) == 0, "Android service client failed");
    require(waitpid(server_pid, &server_status, 0) == server_pid && WIFEXITED(server_status) &&
            WEXITSTATUS(server_status) == 0, "native service stub failed");
    if (umount("/dev/null") || umount("/dev/binder") || umount("/dev") || umount(argv[1])) fail("unmount private probe mounts");
#ifdef GBOS_PARCEL_MODE
    puts("ANDROID_PARCEL=PASS: Android client and native Parcel endpoint exited successfully; runner must verify receipt output.");
#else
    puts("LIBBINDER_LIST=PASS: Android service client and native empty-list stub completed.");
#endif
    return 0;
}
