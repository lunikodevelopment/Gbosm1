/* Original project probe: first run of the image's own servicemanager.
 * Protocol and process reference: AOSP frameworks/native cmds/servicemanager
 * (main.cpp, ServiceManager.cpp, Access.cpp), read only as background. This
 * launcher starts the unmodified image binary through the extracted Android
 * linker inside a private chroot and records what happens; it implements no
 * service manager behavior and intercepts no API.
 */
#define main native_echo_main
#include "probe-binder.c"
#undef main
#include <sys/stat.h>

#define OBSERVE_STEPS 60 /* 100 ms each: six seconds of observation */

/* Runs inside gbos_binder_probe_t, already chrooted, after runcon. */
static int run_servicemanager(const char *runtime) {
    char linker[4096], image[4096], libraries[4096];
#define PATH_JOIN(dst, root, leaf) require(snprintf(dst, sizeof(dst), "%s/%s", root, leaf) < (int)sizeof(dst), "path too long")
    PATH_JOIN(linker, runtime, "linker64"); PATH_JOIN(image, runtime, "servicemanager");
    PATH_JOIN(libraries, runtime, "lib64");
    if (setenv("LD_LIBRARY_PATH", libraries, 1)) fail("set Android library path");
    execl(linker, "linker64", image, (char *)NULL);
    fail("execute image servicemanager");
    return 1;
}

static void bind_mount(const char *source, const char *target, unsigned long flags, int required) {
    if (mount(source, target, NULL, MS_BIND | flags, NULL)) {
        if (required) fail(target);
        fprintf(stderr, "OPTIONAL_MOUNT_FAILED %s -> %s: %s\n", source, target, strerror(errno));
    } else {
        printf("PRIVATE_ROOT_MOUNT %s -> %s\n", source, target);
    }
}

int main(int argc, char **argv) {
    if (signal(SIGALRM, timeout_exit) == SIG_ERR) fail("install alarm handler");
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 3 && strcmp(argv[1], "--sm-run") == 0) return run_servicemanager(argv[2]);
    if (argc == 3 && strcmp(argv[2], "--describe") == 0) return native_echo_main(argc, argv);
    int with_client = argc == 5 && strcmp(argv[4], "--list-client") == 0;
    require((argc == 4 || with_client) && sysconf(_SC_PAGESIZE) == 16384,
            "requires mount directory, runtime directory, root directory, optional --list-client and 16 KiB pages");
    alarm(30);
    if (mount("binder", argv[1], "binder", 0, NULL)) fail("mount private binderfs");
    char control_path[4096], device_path[4096], trace[4096], target[4096];
    PATH_JOIN(control_path, argv[1], "binder-control"); PATH_JOIN(device_path, argv[1], "asahi-service-stub");
    PATH_JOIN(trace, argv[2], "sm.trace");
    int control = open(control_path, O_RDWR | O_CLOEXEC);
    if (control < 0) fail("open private control");
    struct binderfs_device device = {0}; strcpy(device.name, "asahi-service-stub");
    if (ioctl(control, BINDER_CTL_ADD, &device)) fail("create private device");
    close(control);
    /* Same private /dev overlay as the earlier Android probes. */
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

    /* Private root. The Python runner created the directories, the Android
     * fixture files and the lib/lib64/bin symlinks under argv[3]. */
#define ROOT_PATH(leaf) PATH_JOIN(target, argv[3], leaf)
    ROOT_PATH("usr"); bind_mount("/usr", target, MS_REC, 1); /* /usr holds a mount (usr/lib/firmware/vendor); a non-recursive bind of it fails in a user namespace */
    ROOT_PATH("dev"); bind_mount("/dev", target, MS_REC, 1);
    ROOT_PATH("proc");
    if (mount("proc", target, "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL)) fail("mount private proc in root");
    puts("PRIVATE_ROOT_MOUNT proc -> root/proc");
    ROOT_PATH("sys/fs/selinux"); bind_mount("/sys/fs/selinux", target, 0, 0);
    require(snprintf(target, sizeof(target), "%s%s", argv[3], argv[2]) < (int)sizeof(target), "path too long");
    bind_mount(argv[2], target, 0, 1);
    puts("SERVICEMANAGER_ROOT=READY: fixtures, /usr, /dev, /proc and the run's runtime directory are in the private root.");

    pid_t pid = fork(); if (pid < 0) fail("fork servicemanager tree");
    if (pid == 0) {
        if (setpgid(0, 0)) fail("own process group");
        if (chroot(argv[3]) || chdir("/")) fail("enter private root");
        execl("/usr/bin/strace", "strace", "-f", "-tt", "-T", "-yy", "-s", "160", "-o", trace,
              "/usr/bin/runcon", "-t", "gbos_binder_probe_t", argv[0], "--sm-run", argv[2], (char *)NULL);
        fail("execute traced servicemanager");
    }
    int status = 0, exited = 0;
    if (with_client) {
        /* Give servicemanager time to become context manager (about 0.1 s in the first
         * successful run), then run the image's own `service list` unconfined in the same root. */
        for (int step = 0; step < 20 && !exited; step++) {
            if (waitpid(pid, &status, WNOHANG) == pid) exited = 1; else usleep(100000);
        }
        if (!exited) {
            char client[4096], libraries[4096], library_env[4200], output[4096], client_trace[4096];
            PATH_JOIN(client, argv[2], "service"); PATH_JOIN(libraries, argv[2], "lib64");
            PATH_JOIN(output, argv[2], "list-client.stdout"); PATH_JOIN(client_trace, argv[2], "list-client.trace");
            require(snprintf(library_env, sizeof(library_env), "LD_LIBRARY_PATH=%s", libraries) < (int)sizeof(library_env),
                    "library environment too long");
            char linker[4096]; PATH_JOIN(linker, argv[2], "linker64");
            pid_t cpid = fork(); if (cpid < 0) fail("fork list client");
            if (cpid == 0) {
                int out = open(output, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
                if (out < 0 || dup2(out, STDOUT_FILENO) < 0) fail("redirect list client output");
                if (chroot(argv[3]) || chdir("/")) fail("enter private root for list client");
                execl("/usr/bin/strace", "strace", "-f", "-tt", "-T", "-yy", "-s", "160", "-o", client_trace,
                      "-E", library_env, linker, client, "list", (char *)NULL);
                fail("execute traced list client");
            }
            int cstatus = 0, done = 0;
            for (int step = 0; step < 100 && !done; step++) {
                if (waitpid(cpid, &cstatus, WNOHANG) == cpid) done = 1; else usleep(100000);
            }
            if (!done) { kill(cpid, SIGKILL); waitpid(cpid, &cstatus, 0); puts("LIST_CLIENT=timed out after ten seconds"); }
            else printf("LIST_CLIENT=%s %d\n", WIFEXITED(cstatus) ? "exit status" : "signal",
                        WIFEXITED(cstatus) ? WEXITSTATUS(cstatus) : WTERMSIG(cstatus));
        }
    }
    for (int step = 0; step < OBSERVE_STEPS && !exited; step++) {
        pid_t done = waitpid(pid, &status, WNOHANG);
        if (done == pid) exited = 1; else if (done < 0) fail("wait for servicemanager tree");
        else usleep(100000);
    }
    if (exited) {
        printf("SERVICEMANAGER_OBSERVED=exited early: %s %d (strace reports the traced program's status)\n",
               WIFEXITED(status) ? "exit status" : "signal", WIFEXITED(status) ? WEXITSTATUS(status) : WTERMSIG(status));
    } else {
        puts("SERVICEMANAGER_OBSERVED=still running after six seconds; stopping it now.");
        kill(-pid, SIGTERM);
        for (int step = 0; step < 30 && waitpid(pid, &status, WNOHANG) == 0; step++) usleep(100000);
        if (kill(-pid, SIGKILL) == 0) waitpid(pid, &status, 0);
    }
    puts("SM_LAUNCHER=DONE: observation finished; the trace and stderr decide what happened. Mounts end with this namespace.");
    return 0;
}
