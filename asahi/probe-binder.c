/* Targeted Linux Binder UAPI echo probe; original project source. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/android/binder.h>
#include <linux/android/binderfs.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/wait.h>
#include <sys/xattr.h>
#include <unistd.h>

#define MAP_BYTES (1024 * 1024)
static const char payload[] = "Asahi Binder 16KiB echo";
struct connection { int fd; void *map; };
static void fail(const char *where) { perror(where); exit(1); }
static void require(int ok, const char *why) {
    if (!ok) { fprintf(stderr, "STOPPED: %s\n", why); exit(1); }
}
static struct connection connect_device(const char *path) {
    struct connection c;
    c.fd = open(path, O_RDWR | O_CLOEXEC);
    if (c.fd < 0) fail("open private binder device");
    struct binder_version v = {0};
    if (ioctl(c.fd, BINDER_VERSION, &v)) fail("BINDER_VERSION");
    printf("Binder protocol=%d page_size=%ld\n", v.protocol_version, sysconf(_SC_PAGESIZE));
    require(v.protocol_version == BINDER_CURRENT_PROTOCOL_VERSION, "Binder protocol mismatch");
    c.map = mmap(NULL, MAP_BYTES, PROT_READ, MAP_PRIVATE, c.fd, 0);
    if (c.map == MAP_FAILED) fail("mmap binder buffer");
    return c;
}
static size_t exchange(int fd, const void *write, size_t bytes, void *read, size_t capacity) {
    struct binder_write_read b = {0};
    b.write_size = bytes; b.write_buffer = (uintptr_t)write;
    b.read_size = capacity; b.read_buffer = (uintptr_t)read;
    int status;
    do { status = ioctl(fd, BINDER_WRITE_READ, &b); } while (status < 0 && errno == EINTR);
    if (status < 0) fail("BINDER_WRITE_READ");
    require(b.write_consumed == bytes, "partial Binder command consumption");
    return b.read_consumed;
}
static struct binder_transaction_data receive(struct connection c, uint32_t expected) {
    unsigned char buf[4096];
    for (;;) {
        size_t size = exchange(c.fd, NULL, 0, buf, sizeof(buf));
        for (size_t pos = 0; pos < size;) {
            uint32_t cmd;
            require(size - pos >= sizeof(cmd), "truncated Binder return command");
            memcpy(&cmd, buf + pos, sizeof(cmd)); pos += sizeof(cmd);
            size_t n = _IOC_SIZE(cmd);
            require(n <= size - pos, "truncated Binder return payload");
            if (cmd == expected) {
                struct binder_transaction_data transaction;
                require(n == sizeof(transaction), "unexpected Binder transaction layout");
                memcpy(&transaction, buf + pos, n);
                require(transaction.data_size == sizeof(payload) && transaction.offsets_size == 0,
                        "unexpected echo size or Binder objects");
                uintptr_t addr = transaction.data.ptr.buffer, start = (uintptr_t)c.map;
                require(addr >= start && addr - start <= MAP_BYTES - sizeof(payload),
                        "transaction buffer outside mapped Binder memory");
                require(memcmp((void *)addr, payload, sizeof(payload)) == 0, "Binder echo mismatch");
                return transaction;
            }
            if (cmd == BR_FAILED_REPLY || cmd == BR_DEAD_REPLY || cmd == BR_ERROR) {
                fprintf(stderr, "Binder return error command=0x%x\n", cmd);
                exit(1);
            }
            require(cmd == BR_NOOP || cmd == BR_TRANSACTION_COMPLETE || cmd == BR_SPAWN_LOOPER,
                    "unexpected Binder return command");
            pos += n;
        }
    }
}
/* Commands are packed in byte arrays: Binder command streams have no C padding. */
static void transaction_command(int fd, uint32_t cmd, struct binder_transaction_data *t) {
    unsigned char stream[sizeof(cmd) + sizeof(*t)];
    memcpy(stream, &cmd, sizeof(cmd)); memcpy(stream + sizeof(cmd), t, sizeof(*t));
    exchange(fd, stream, sizeof(stream), NULL, 0);
}
static void free_buffer(int fd, binder_uintptr_t address) {
    uint32_t cmd = BC_FREE_BUFFER;
    unsigned char stream[sizeof(cmd) + sizeof(address)];
    memcpy(stream, &cmd, sizeof(cmd)); memcpy(stream + sizeof(cmd), &address, sizeof(address));
    exchange(fd, stream, sizeof(stream), NULL, 0);
}
static void server(const char *path, int ready_fd) {
    alarm(15);
    struct connection c = connect_device(path);
    int fd = c.fd;
    int unused = 0;
    if (ioctl(fd, BINDER_SET_CONTEXT_MGR, &unused)) fail("BINDER_SET_CONTEXT_MGR");
    uint32_t enter = BC_ENTER_LOOPER;
    exchange(fd, &enter, sizeof(enter), NULL, 0);
    require(write(ready_fd, "R", 1) == 1, "server readiness failed"); close(ready_fd);
    struct binder_transaction_data request = receive(c, BR_TRANSACTION);
    require(request.code == 0x1616, "unexpected transaction code");
    struct binder_transaction_data reply = {0};
    reply.data_size = sizeof(payload); reply.data.ptr.buffer = (uintptr_t)payload;
    transaction_command(fd, BC_REPLY, &reply);
    free_buffer(fd, request.data.ptr.buffer);
    munmap(c.map, MAP_BYTES); close(fd);
    puts("Dedicated-domain server verified request and submitted reply.");
}
int main(int argc, char **argv) {
    if (argc == 4 && strcmp(argv[1], "--server") == 0) {
        setvbuf(stdout, NULL, _IONBF, 0);
        char *end;
        long ready = strtol(argv[3], &end, 10);
        require(*end == 0 && ready >= 0 && ready <= 1024, "invalid readiness descriptor");
        server(argv[2], (int)ready);
        return 0;
    }
    int describe = argc == 3 && strcmp(argv[2], "--describe") == 0;
    require((argc == 2 || describe) && sysconf(_SC_PAGESIZE) == 16384, "requires private mount directory and 16 KiB pages");
    setvbuf(stdout, NULL, _IONBF, 0);
    alarm(20);
    if (mount("binder", argv[1], "binder", 0, NULL)) fail("mount private binderfs in user namespace");
    char control_path[4096], device_path[4096];
    require(snprintf(control_path, sizeof(control_path), "%s/binder-control", argv[1]) < (int)sizeof(control_path), "mount path too long");
    require(snprintf(device_path, sizeof(device_path), "%s/asahi-echo", argv[1]) < (int)sizeof(device_path), "device path too long");
    int control = open(control_path, O_RDWR | O_CLOEXEC);
    if (control < 0) fail("open private binder-control");
    struct binderfs_device device = {0};
    strcpy(device.name, "asahi-echo");
    if (ioctl(control, BINDER_CTL_ADD, &device)) fail("BINDER_CTL_ADD");
    close(control);
    printf("Private binder device major=%u minor=%u\n", device.major, device.minor);
    char context[512];
    ssize_t size = getxattr(device_path, "security.selinux", context, sizeof(context) - 1);
    if (size < 0) fail("read Binder device SELinux context");
    context[size] = 0;
    printf("BINDER_DEVICE_CONTEXT=%s\n", context);
    if (describe) {
        size = getxattr(argv[1], "security.selinux", context, sizeof(context) - 1);
        if (size < 0) fail("read binderfs root SELinux context");
        context[size] = 0;
        printf("BINDER_ROOT_CONTEXT=%s\n", context);
        if (umount(argv[1])) fail("unmount description binderfs");
        return 0;
    }
    int ready[2]; if (pipe(ready)) fail("pipe");
    pid_t child = fork(); if (child < 0) fail("fork");
    if (child == 0) {
        close(ready[0]);
        char ready_text[32];
        snprintf(ready_text, sizeof(ready_text), "%d", ready[1]);
        execl("/usr/bin/runcon", "runcon", "-t", "gbos_binder_probe_t",
              argv[0], "--server", device_path, ready_text, (char *)NULL);
        fail("runcon dedicated Binder server");
    }
    close(ready[1]); char token;
    require(read(ready[0], &token, 1) == 1 && token == 'R', "server stopped before readiness");
    close(ready[0]);
    struct connection client = connect_device(device_path);
    struct binder_transaction_data request = {0};
    request.target.handle = 0; request.code = 0x1616;
    request.data_size = sizeof(payload); request.data.ptr.buffer = (uintptr_t)payload;
    transaction_command(client.fd, BC_TRANSACTION, &request);
    struct binder_transaction_data reply = receive(client, BR_REPLY);
    free_buffer(client.fd, reply.data.ptr.buffer);
    munmap(client.map, MAP_BYTES); close(client.fd);
    int status;
    require(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0,
            "echo server failed");
    if (umount(argv[1])) fail("unmount private binderfs");
    puts("BINDER_ECHO=PASS: two native Linux processes exchanged verified payloads through private Binder.");
    return 0;
}
