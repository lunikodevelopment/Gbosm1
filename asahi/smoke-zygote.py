#!/usr/bin/env python3
"""Fourth chroot smoke test: the image's zygote (app_process64 --zygote) with no system_server.

Original project script; same sandbox as smoke-logd-art.py (which supplies the mounts,
the property area, the logd launcher and the apex info list). New here:
- linkerconfig runs before any other program, so logd and everything after it use the
  generated linker configuration;
- the launcher (`--launch-zygote`) creates the zygote socket at /dev/socket/zygote,
  passes it as ANDROID_SOCKET_zygote like init, and runs the image's
  `app_process64 -Xzygote /system/bin --zygote --socket-name=zygote` as the namespace
  root with the classpath variables derive_classpath wrote;
- no --start-system-server and no binder: this tests ART's zygote init, class and
  resource preload and the socket listen only. Output of the process, the log and a
  trace of file and network calls are shown; the process is stopped after a time limit.
Nothing outside the tree's tmpfs mounts and the result directory is written.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("smoke_logd_art", HERE / "smoke-logd-art.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

PRELUDE = base.SCRIPT[:base.SCRIPT.index('say "S0')]

SCRIPT = PRELUDE + r'''ZYG="$8"; WAIT="${9:-60}"; SS="${10:-}"
say "S0 property area"
"$PY" "$PROPS" "$ROOT" "$ROOT/dev/__properties__" logd.ready=true servicemanager.ready=true apexd.status=ready \
  ro.logd.auditd=false ro.logd.kernel=false ro.boottime.init=1 sys.boot_completed=0 ro.zygote=zygote64 2>&1 | cut -c1-300

# directories Android's init.rc creates under /data before zygote starts (tmpfs here, gone at exit)
mkdir -p "$ROOT/data/dalvik-cache/arm64" "$ROOT/data/misc" "$ROOT/data/system" "$ROOT/data/user/0" "$ROOT/data/user_de/0" \
  "$ROOT/data/app" "$ROOT/data/anr" "$ROOT/data/tombstones" "$ROOT/data/local/tmp"
chmod 0771 "$ROOT/data/dalvik-cache" "$ROOT/data/dalvik-cache/arm64"

say "S1 linkerconfig first (no logd yet), then logd under the generated config"
timeout 120 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /apex/com.android.runtime/bin/linkerconfig --target /linkerconfig 2>&1 | head -n 5
ls "$ROOT/linkerconfig" | wc -l
"$PY" "$SELF" --launch "$ROOT" > "$OUT/logd.out" 2>&1 &
LOGDPID=$!
sleep 3
if kill -0 "$LOGDPID" 2>/dev/null; then echo "logd still running"; else echo "logd EXITED"; fi
head -n 6 "$OUT/logd.out" | cut -c1-200

say "S2 derive_classpath"
inroot /apex/com.android.sdkext/bin/derive_classpath /data/system/environ/classpath | head -n 5
CPENV=()
while read -r a b c; do [ "$a" = export ] && CPENV+=("$b=$c"); done < "$ROOT/data/system/environ/classpath"
echo "classpath variables: $(printf '%s ' "${CPENV[@]%%=*}")"

say "S2b what path the kernel reports for open files after pivot_root (zygote's fd allowlist compares these)"
timeout 30 "$PY" "$ZYG" --probe-paths "$ROOT" 2>&1 | cut -c1-220

say "S3 zygote: app_process64 -Xzygote --zygote, up to ${WAIT}s, no system_server, no binder"
ls -l "$ROOT/system/etc/preloaded-classes" "$ROOT/system/bin/app_process64" 2>&1 | cut -c1-160
if command -v strace >/dev/null 2>&1; then TR=(strace -f -s 160 -e 'trace=%file,%network,setgroups,setresuid,setresgid,setuid,setgid,capset,prctl,unshare,mount,setns,seccomp,setpriority,dup,dup2,dup3,open_tree,fsopen,fspick,fcntl,eventfd2,memfd_create,epoll_create1,open_by_handle_at' -o "$OUT/zygote.trace"); else TR=(); fi
"${TR[@]}" "$PY" "$ZYG" --launch-zygote "$ROOT" "${BASEENV[@]}" "${CPENV[@]}" $SS > "$OUT/zygote.out" 2>&1 &
ZPID=$!
( while kill -0 "$ZPID" 2>/dev/null; do
    find "$ROOT"/proc/[0-9]*/fd -maxdepth 1 -lname / -printf '%p\n' 2>/dev/null > "$OUT/rootfd.tmp"
    if [ -s "$OUT/rootfd.tmp" ]; then
      cp "$OUT/rootfd.tmp" "$OUT/rootfd.last"
      f=$(head -n 1 "$OUT/rootfd.tmp"); d="${f%/*}"
      { echo "descriptor table around it:"; ls -l "$d" 2>/dev/null | grep -E ' (5[6-9]|6[0-4]) ->' | sed -E 's/.* ([0-9]+ -> )/\1/';
        echo "fdinfo:"; cat "$d/../fdinfo/${f##*/}" 2>/dev/null; echo "stat:"; stat -L -c '%F inode=%i dev=%D mode=%a' "$f" 2>&1; } > "$OUT/rootfd.detail" 2>&1
    fi
    sleep 0.2
  done ) &
for i in $(seq 1 "$WAIT"); do kill -0 "$ZPID" 2>/dev/null || break; sleep 1; done
if kill -0 "$ZPID" 2>/dev/null; then echo "zygote STILL RUNNING after ${WAIT}s"; else wait "$ZPID"; echo "zygote EXITED, status $?"; fi
echo "--- zygote stdout/stderr (first 30):"; head -n 30 "$OUT/zygote.out" | cut -c1-240
echo "--- last snapshot of descriptors that point at / (process, number, command):"
if [ -s "$OUT/rootfd.last" ]; then
  while read -r f; do p="${f%/fd/*}"; echo "$f -> / ; $(tr '\0' ' ' < "$p/cmdline" 2>/dev/null | cut -c1-80)"; done < "$OUT/rootfd.last" | head -n 8
  cat "$OUT/rootfd.detail" 2>/dev/null | cut -c1-200
else echo "none seen (the process may have died between snapshots)"; fi
echo "--- zygote socket:"; ls -l "$ROOT/dev/socket" 2>&1 | cut -c1-120
echo "--- processes in the namespace:"; for p in "$ROOT"/proc/[0-9]*; do printf '%s %s\n' "${p##*/}" "$(tr '\0' ' ' < $p/cmdline 2>/dev/null | cut -c1-110)"; done | head -n 15
echo "--- logcat -d, last 40 (zygote, ART, libc, DEBUG):"
timeout 30 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/logcat -d 2>&1 | grep -v -E 'derive_classpath|CompatibilityRules|Resources:|nativeloader: (Load|Configuring)|ApplicationLoaders' | tail -n 60 | cut -c1-260
echo "--- logcat -d, fatal and error lines only, without the abort stack dump (the reason for an abort is here):"
timeout 30 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/logcat -d -v time '*:E' 2>&1 | grep -v -E 'runtime.cc:769|derive_classpath|Zygote  :.*at com' | head -n 40 | cut -c1-300
if [ -s "$OUT/zygote.trace" ]; then
  echo "--- which call opened the file descriptor named in a 'Not allowlisted (N): path' line:"
  FDN=$(timeout 30 chroot "$ROOT" /system/bin/logcat -d 2>/dev/null | grep -a -o 'Not allowlisted ([0-9]*)' | head -n 1 | tr -dc 0-9)
  echo "descriptor number: ${FDN:-none found}"
  [ -n "$FDN" ] && grep -E "\) += ${FDN}\$" "$OUT/zygote.trace" | grep -v -E 'readlinkat|socket\(|connect\(' | cut -c1-200 | tail -n 8
  echo "--- mount-namespace calls in the trace (zygote unshares after preload):"
  grep -E '^[0-9]+ +(unshare|setns|mount|umount2)\(' "$OUT/zygote.trace" | cut -c1-200 | head -n 12
  echo "--- failed file/network calls in the trace, distinct (first 30):"
  grep -E '= -1 E' "$OUT/zygote.trace" | sed -E 's/^[0-9]+ +//' | grep -v -E 'PR_SET_VMA|/proc/self|ld.so.preload|SO_DOMAIN|/mnt/expand|resource-cache|/sys/devices/system/cpu|trace_marker|qgl_config|statsdw' | cut -c1-170 | sort | uniq -c | sort -rn | head -n 30
  echo "--- last 10 trace lines:"; tail -n 10 "$OUT/zygote.trace" | cut -c1-200
fi
pkill -P $$ 2>/dev/null; kill "$ZPID" "$LOGDPID" 2>/dev/null
echo; echo "steps finished"
'''


def enter_root(root):
    """Make `root` the process's root with pivot_root (falls back to chroot, and says so).

    Zygote compares the path of every open file with an allowlist before forking system_server.
    Under chroot the paths it read were the host paths of the tree; after pivot_root the tree is
    the namespace's own root, so they should read /apex/... and /system/... as on a device.
    """
    import ctypes
    import platform
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        number = {"aarch64": 41, "x86_64": 155}[platform.machine()]
        # pivot_root changes the whole mount namespace, so this process gets its own copy first;
        # without it the test script (and its host tools) would lose their root too
        os.unshare(os.CLONE_NEWNS)
        if libc.mount(None, b"/", None, 0x4000 | 0x40000, None) != 0:  # MS_REC | MS_PRIVATE
            raise OSError(ctypes.get_errno(), "make-rprivate")
        os.chdir(root)
        if libc.syscall(number, b".", b".") != 0:
            raise OSError(ctypes.get_errno(), "pivot_root")
        if libc.umount2(b".", 2) != 0:
            raise OSError(ctypes.get_errno(), "umount2")
        os.chdir("/")
        print("root entered with pivot_root", flush=True)
    except (OSError, KeyError) as error:
        print(f"pivot_root failed ({error}); using chroot", flush=True)
        os.chroot(root)
        os.chdir("/")


def probe_paths(root):
    """After entering the root, open a few files and print the path the kernel reports for each descriptor."""
    enter_root(root)
    for path in ("/dev/urandom", "/dev/null", "/dev/zero", "/dev/tty", "/apex/com.android.art/javalib/core-oj.jar",
                 "/system/etc/fonts.xml", "/proc/self/fd", "/"):
        try:
            fd = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if path in ("/", "/proc/self/fd") else 0))
            print(f"{path} -> {os.readlink(f'/proc/self/fd/{fd}')!r}", flush=True)
        except OSError as error:
            print(f"{path} -> error {error}", flush=True)
    for line in open("/proc/self/mountinfo"):
        if any(word in line for word in ("urandom", " /dev ", "/dev/null", "/dev/zero")):
            print("mountinfo:", line.strip()[:200], flush=True)


def keep_namespace_alive():
    """Leave one sleeping process in this mount namespace, the way init stays in its own.

    Zygote unshares its mount namespace after preload (UnmountStorageOnInit in AOSP's
    com_android_internal_os_Zygote.cpp). If nothing else lives in the namespace zygote left,
    the kernel tears it down and the mounts its open descriptors refer to become detached;
    a descriptor on the bind-mounted /dev/urandom then reads as "/" and zygote's fd allowlist
    refuses it before forking system_server. The helper holds no descriptors and dies with zygote.
    """
    import ctypes
    import signal
    if os.fork() == 0:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG: exit when zygote exits
        os.closerange(0, 1024)
        while True:
            signal.pause()


def launch_zygote(root, env_items):
    enter_root(root)
    sockets = Path("/dev/socket")
    sockets.mkdir(parents=True, exist_ok=True)
    flags = [item for item in env_items if item.startswith("--")]
    env = dict(item.split("=", 1) for item in env_items if not item.startswith("--"))
    keep = []
    for name in ("zygote", "usap_pool_primary"):  # init.zygote64.rc declares both: stream 660 root system
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(f"/dev/socket/{name}")
        os.chmod(f"/dev/socket/{name}", 0o660)
        s.listen(8)
        os.set_inheritable(s.fileno(), True)
        keep.append(s)
        env[f"ANDROID_SOCKET_{name}"] = str(s.fileno())
    # app_process stops reading its own options at the first one it does not know (--socket-name),
    # so --start-system-server must come before it, as in init.zygote64.rc
    # like init, hand over only 0-2 and the sockets; anything else the test script left open is closed
    allowed = {s_.fileno() for s_ in keep}
    for number in sorted(int(n) for n in os.listdir("/proc/self/fd")):
        if number > 2 and number not in allowed:
            try:
                os.close(number)
            except OSError:
                pass
    keep_namespace_alive()
    # init starts zygote with stdin, stdout and stderr on /dev/null; zygote duplicates stdout,
    # and a descriptor on the test's log file is not on its allowlist (B5k). Its messages go to logcat.
    print("stdio of zygote now /dev/null; its output is in logcat", flush=True)
    null = os.open("/dev/null", os.O_RDWR)
    for number in (0, 1, 2):
        os.dup2(null, number)
    os.close(null)
    argv = ["app_process64", "-Xzygote", "/system/bin", "--zygote"] + flags + ["--socket-name=zygote"]
    os.execve("/system/bin/app_process64", argv, env)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--probe-paths":
        probe_paths(sys.argv[2])
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--launch-zygote":
        launch_zygote(sys.argv[2], sys.argv[3:])
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    system_server = "--system-server" in sys.argv[1:]
    print("=== BRING-UP THREAD, STAGE " + ("B5a" if system_server else "B4") + ": ZYGOTE PRELOAD" + (" WITH --start-system-server" if system_server else " WITHOUT SYSTEM_SERVER") + " (paste in the \"Remaining steps to boot Android\" thread) ===")
    trees = sorted(Path("work/asahi").glob("android-root-*/root"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not trees:
        sys.exit("no extracted tree; run extract-android-root.py first")
    root = trees[0].resolve()
    for tool in ("unshare", "chroot", "timeout", "mount"):
        if not shutil.which(tool):
            sys.exit(f"{tool} is missing")
    print(f"Tree: {root}")
    print("THIS STEP MODIFIES STORAGE")
    print("Rewrites apex/apex-info-list.xml in the tree (same content as B3g) and creates one work/asahi/root-smoke-* directory")
    print("(script, logs, a trace of file and network calls, perhaps tens of MB). The tree is mounted read-only during the run;")
    print("tmpfs mounts hold everything else and vanish at exit. Delete the directory to reverse.")
    out = Path(tempfile.mkdtemp(prefix="root-smoke-", dir="work/asahi")).resolve()
    script = out / "run.sh"
    script.write_text(SCRIPT)
    command = ["unshare", "--user", "--map-auto", "--map-root-user", "--mount", "--pid", "--fork", "--propagation", "private",
               "bash", str(script), str(root), str(out), shutil.which("python3") or "python3",
               str((HERE / "smoke-logd-art.py")), str(HERE / "make-property-area.py"),
               str(HERE / "elf-needed.py"), str(HERE / "make-apex-info-list.py"), str(Path(__file__).resolve()), "45", "--start-system-server" if system_server else ""]
    done = subprocess.run(command, capture_output=True, text=True, timeout=900)
    text = done.stdout + done.stderr
    (out / "log.txt").write_text(text)
    print(text)
    print(f"namespace run exit status: {done.returncode}")
    print(f"Send the terminal output above (log and trace: {out})")


if __name__ == "__main__":
    main()
