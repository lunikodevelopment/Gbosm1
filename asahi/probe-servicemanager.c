/* Original project probe: first run of the image's own servicemanager.
 * Protocol and process reference: AOSP frameworks/native cmds/servicemanager
 * (main.cpp, ServiceManager.cpp, Access.cpp), read only as background. This
 * launcher starts the unmodified image binary through the extracted Android
 * linker inside a private chroot and records what happens; it implements no
 * service manager behavior and intercepts no API.
 */
#define GBOS_NO_MAIN 1 /* this file supplies main; the property fixture, Binder helpers and the Parcel endpoint come from the includes */
#include "probe-android-parcel.c"

#define PATH_JOIN(dst, root, leaf) require(snprintf(dst, sizeof(dst), "%s/%s", root, leaf) < (int)sizeof(dst), "path too long")
#define OBSERVE_STEPS 60 /* 100 ms each: six seconds of observation */

/* Runs inside gbos_binder_probe_t, already chrooted, after runcon. */
static int run_servicemanager(const char *runtime) {
    char linker[4096], image[4096], libraries[4096];
    PATH_JOIN(linker, runtime, "linker64"); PATH_JOIN(image, runtime, "servicemanager");
    PATH_JOIN(libraries, runtime, "lib64");
    if (setenv("LD_LIBRARY_PATH", libraries, 1)) fail("set Android library path");
    execl(linker, "linker64", image, (char *)NULL);
    fail("execute image servicemanager");
    return 1;
}

/* Runs one image program unconfined in the private root under strace; stdout to `output`,
 * `input` (when >= 0) becomes its stdin. `args` is a NULL-terminated list after the program name.
 * Returns the wait status, or -1 when it had to be killed after `seconds`. */
static int run_in_root(const char *root, const char *runtime, const char *trace, const char *output,
                       const char *program, const char *const *args, int input, int seconds) {
    char libraries[4096], library_env[4200], linker[4096], image[4096];
    PATH_JOIN(libraries, runtime, "lib64"); PATH_JOIN(linker, runtime, "linker64"); PATH_JOIN(image, runtime, program);
    require(snprintf(library_env, sizeof(library_env), "LD_LIBRARY_PATH=%s", libraries) < (int)sizeof(library_env),
            "library environment too long");
    pid_t pid = fork(); if (pid < 0) fail("fork image program");
    if (pid == 0) {
        const char *argv_exec[24]; int n = 0;
        const char *head[] = {"strace", "-f", "-tt", "-T", "-yy", "-s", "160", "-o", trace, "-E", library_env, linker, image};
        for (size_t i = 0; i < sizeof(head) / sizeof(head[0]); i++) argv_exec[n++] = head[i];
        for (int i = 0; args && args[i] && n < 22; i++) argv_exec[n++] = args[i];
        argv_exec[n] = NULL;
        int out = open(output, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
        if (out < 0 || dup2(out, STDOUT_FILENO) < 0) fail("redirect image program output");
        if (input >= 0 && dup2(input, STDIN_FILENO) < 0) fail("install image program stdin");
        if (chroot(root) || chdir("/")) fail("enter private root for image program");
        execv("/usr/bin/strace", (char *const *)argv_exec);
        fail("execute traced image program");
    }
    int status = 0;
    for (int step = 0; step < seconds * 10; step++) {
        if (waitpid(pid, &status, WNOHANG) == pid) return status;
        usleep(100000);
    }
    kill(pid, SIGKILL); waitpid(pid, &status, 0);
    return -1;
}
static void report_status(const char *label, int status) {
    if (status == -1) printf("%s=timed out\n", label);
    else printf("%s=%s %d\n", label, WIFEXITED(status) ? "exit status" : "signal",
                WIFEXITED(status) ? WEXITSTATUS(status) : WTERMSIG(status));
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
    if (argc == 5 && strcmp(argv[1], "--register-endpoint") == 0) {
        /* Unconfined native endpoint: registers through the real servicemanager. */
        char *end; long ready = strtol(argv[4], &end, 10);
        require(*end == 0 && ready >= 0 && ready <= 1024, "bad readiness descriptor");
        parcel_registered_server(argv[2], (int)ready);
        return 0;
    }
    if (argc == 3 && strcmp(argv[2], "--describe") == 0) return native_echo_main(argc, argv);
    int with_client = argc == 5 && strcmp(argv[4], "--list-client") == 0;
    int with_register = argc == 5 && strcmp(argv[4], "--register-parcel") == 0;
    int with_serve = (argc == 6 || argc == 8) && strcmp(argv[4], "--serve-image") == 0;
    const char *served = with_serve ? argv[5] : NULL;
    /* Optional: --serve-image NAME FIRST LAST tries `service call <name> <code> fd /proc/self/fd/0 i64 16384` for each code. */
    long first_code = 0, last_code = -1;
    if (with_serve && argc == 8) { first_code = strtol(argv[6], NULL, 10); last_code = strtol(argv[7], NULL, 10);
        require(first_code >= 1 && last_code >= first_code && last_code <= 32, "unsupported transaction code range"); }
    require(!with_serve || (strlen(served) < 128 && strspn(served, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-") == strlen(served)),
            "unsupported image program name");
    require((argc == 4 || with_client || with_register || with_serve) && sysconf(_SC_PAGESIZE) == 16384,
            "requires mount directory, runtime directory, root directory, optional --list-client and 16 KiB pages");
    alarm(with_serve && last_code >= first_code ? 110 : 30);
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
    if (with_client || with_register || with_serve) {
        /* The real servicemanager would set servicemanager.ready through the property
         * service, which is absent here; the client library waits for it, so publish the
         * same private immutable fixture the earlier probes used (not Android init). */
        stub_ready_property();
        /* Give servicemanager time to become context manager (about 0.1 s in the first
         * successful run), then run the image's own `service list` unconfined in the same root. */
        for (int step = 0; step < 20 && !exited; step++) {
            if (waitpid(pid, &status, WNOHANG) == pid) exited = 1; else usleep(100000);
        }
        if (!exited && with_register) {
            /* Endpoint: fork before the sealed input exists so it inherits no copy. */
            char client[4096], libraries[4096], library_env[4200], output[4096], client_trace[4096], linker[4096], device_arg[4096];
            int ready[2]; if (pipe(ready)) fail("endpoint readiness pipe");
            char ready_text[32]; snprintf(ready_text, sizeof(ready_text), "%d", ready[1]);
            pid_t epid = fork(); if (epid < 0) fail("fork native endpoint");
            if (epid == 0) {
                close(ready[0]);
                execl(argv[0], argv[0], "--register-endpoint", device_path, "unused", ready_text, (char *)NULL);
                fail("execute native endpoint");
            }
            close(ready[1]);
            char token; alarm(30);
            require(read(ready[0], &token, 1) == 1 && token == 'R', "native endpoint stopped before registering with the real servicemanager");
            close(ready[0]);
            int input = parcel_setup_input();
            PATH_JOIN(client, argv[2], "service"); PATH_JOIN(libraries, argv[2], "lib64"); PATH_JOIN(linker, argv[2], "linker64");
            PATH_JOIN(output, argv[2], "parcel-client.stdout"); PATH_JOIN(client_trace, argv[2], "parcel-client.trace");
            (void)device_arg;
            require(snprintf(library_env, sizeof(library_env), "LD_LIBRARY_PATH=%s", libraries) < (int)sizeof(library_env),
                    "library environment too long");
            pid_t cpid = fork(); if (cpid < 0) fail("fork Parcel client");
            if (cpid == 0) {
                int out = open(output, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
                if (out < 0 || dup2(out, STDOUT_FILENO) < 0 || dup2(input, STDIN_FILENO) < 0) fail("redirect Parcel client");
                if (chroot(argv[3]) || chdir("/")) fail("enter private root for Parcel client");
                execl("/usr/bin/strace", "strace", "-f", "-tt", "-T", "-yy", "-s", "160", "-o", client_trace,
                      "-E", library_env, linker, client, "call", "gbos.parcel", "5658",
                      "i32", "5658", "fd", "/proc/self/fd/0", (char *)NULL);
                fail("execute traced Parcel client");
            }
            close(input);
            int cstatus = 0, done = 0;
            for (int step = 0; step < 100 && !done; step++) {
                if (waitpid(cpid, &cstatus, WNOHANG) == cpid) done = 1; else usleep(100000);
            }
            if (!done) { kill(cpid, SIGKILL); waitpid(cpid, &cstatus, 0); puts("REGISTER_PARCEL_CLIENT=timed out after ten seconds"); }
            else printf("REGISTER_PARCEL_CLIENT=%s %d\n", WIFEXITED(cstatus) ? "exit status" : "signal",
                        WIFEXITED(cstatus) ? WEXITSTATUS(cstatus) : WTERMSIG(cstatus));
            int estatus = 0, edone = 0;
            for (int step = 0; step < 30 && !edone; step++) {
                if (waitpid(epid, &estatus, WNOHANG) == epid) edone = 1; else usleep(100000);
            }
            if (!edone) { kill(epid, SIGKILL); waitpid(epid, &estatus, 0); puts("REGISTER_ENDPOINT=did not finish within three seconds"); }
            else printf("REGISTER_ENDPOINT=%s %d\n", WIFEXITED(estatus) ? "exit status" : "signal",
                        WIFEXITED(estatus) ? WEXITSTATUS(estatus) : WTERMSIG(estatus));
        }
        if (!exited && with_serve) {
            /* The image's own program serves; the image's `service` CLI is the client. */
            char server_trace[4096], server_out[4096], list_trace[4096], list_out[4096], call_trace[4096], call_out[4096];
            PATH_JOIN(server_trace, argv[2], "server.trace"); PATH_JOIN(server_out, argv[2], "server.stdout");
            PATH_JOIN(list_trace, argv[2], "list-client.trace"); PATH_JOIN(list_out, argv[2], "list-client.stdout");
            PATH_JOIN(call_trace, argv[2], "call-client.trace"); PATH_JOIN(call_out, argv[2], "call-client.stdout");
            char libraries[4096], library_env[4200], linker[4096], image[4096];
            PATH_JOIN(libraries, argv[2], "lib64"); PATH_JOIN(linker, argv[2], "linker64"); PATH_JOIN(image, argv[2], served);
            require(snprintf(library_env, sizeof(library_env), "LD_LIBRARY_PATH=%s", libraries) < (int)sizeof(library_env),
                    "library environment too long");
            pid_t spid = fork(); if (spid < 0) fail("fork image server");
            if (spid == 0) {
                if (setpgid(0, 0)) fail("own server process group");
                int out = open(server_out, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);
                if (out < 0 || dup2(out, STDOUT_FILENO) < 0 || dup2(out, STDERR_FILENO) < 0) fail("redirect image server output");
                if (chroot(argv[3]) || chdir("/")) fail("enter private root for image server");
                execl("/usr/bin/strace", "strace", "-f", "-tt", "-T", "-yy", "-s", "160", "-o", server_trace, "-E", library_env,
                      linker, image, (char *)NULL);
                fail("execute traced image server");
            }
            usleep(2000000);
            int sstatus = 0;
            if (waitpid(spid, &sstatus, WNOHANG) == spid) {
                report_status("IMAGE_SERVER_EXITED_EARLY", sstatus);
            } else {
                report_status("SERVE_LIST_CLIENT", run_in_root(argv[3], argv[2], list_trace, list_out, "service", (const char *const[]){"list", NULL}, -1, 10));
                /* Find the registered name for the served program in the list output: "<n>\t<name>: [<descriptor>]". */
                char name[256] = "", text[8192] = ""; FILE *f = fopen(list_out, "r");
                if (f) { size_t got = fread(text, 1, sizeof(text) - 1, f); text[got] = 0; fclose(f); }
                char key[160]; snprintf(key, sizeof(key), "%s", served);
                for (char *line = strtok(text, "\n"); line; line = strtok(NULL, "\n")) {
                    char *tab = strchr(line, '\t'), *colon = tab ? strchr(tab, ':') : NULL;
                    if (tab && colon && colon - tab < (long)sizeof(name) &&
                        (strstr(line, "disk_info") || strstr(line, "gsi") || strstr(line, key))) {
                        snprintf(name, sizeof(name), "%.*s", (int)(colon - tab - 1), tab + 1); break;
                    }
                }
                if (name[0]) {
                    printf("SERVE_NAME_FOUND=%s\n", name);
                    /* 1598968902 = INTERFACE_TRANSACTION: the service returns its interface descriptor. */
                    report_status("SERVE_CALL_CLIENT", run_in_root(argv[3], argv[2], call_trace, call_out, "service", (const char *const[]){"call", name, "1598968902", NULL}, -1, 10));
                    for (long code = first_code; code <= last_code; code++) {
                        /* A sealed read-only 16 KiB memfd on the client's stdin, as in the Parcel probe. */
                        int input = parcel_setup_input();
                        char code_text[16], label[64], trace_path[4096], out_path[4096], file_name[64];
                        snprintf(code_text, sizeof(code_text), "%ld", code);
                        snprintf(file_name, sizeof(file_name), "fd-call-%ld.trace", code); PATH_JOIN(trace_path, argv[2], file_name);
                        snprintf(file_name, sizeof(file_name), "fd-call-%ld.stdout", code); PATH_JOIN(out_path, argv[2], file_name);
                        snprintf(label, sizeof(label), "SERVE_FD_CALL_%ld", code);
                        report_status(label, run_in_root(argv[3], argv[2], trace_path, out_path, "service",
                            (const char *const[]){"call", name, code_text, "fd", "/proc/self/fd/0", "i64", "16384", NULL}, input, 10));
                        close(input);
                    }
                } else {
                    puts("SERVE_NAME_FOUND=none: the served program did not appear in the service list");
                }
                kill(-spid, SIGTERM);
                for (int step = 0; step < 30 && waitpid(spid, &sstatus, WNOHANG) == 0; step++) usleep(100000);
                if (kill(-spid, SIGKILL) == 0) waitpid(spid, &sstatus, 0);
                puts("IMAGE_SERVER_STOPPED");
            }
        }
        if (!exited && with_client) {
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
