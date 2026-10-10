#!/usr/bin/env python3
"""Third chroot smoke test: property area, logd with passed-in sockets, linker config diagnosis, derive_classpath, ART.

Original project script; same sandbox as smoke-in-root.py (read-only tree, private
tmpfs mounts, user namespace with the /etc/subuid range, PID namespace). New here:
- a single-file property area built from the image's build.prop files by make-property-area.py
  (plus logd.ready, servicemanager.ready and apexd.status), so liblog, logcat and linkerconfig can read properties;
- a launcher (this file, `--launch`) creates logd's three sockets in /dev/socket,
  hands them to the image's `logd` through ANDROID_SOCKET_<name> exactly as Android's
  init does, and runs logd as uid/gid 1036 with its supplementary groups;
- `logcat -d` then shows what programs wrote to the log, so aborts that print their
  reason only to logd (derive_classpath did that in B3a) become readable;
- the generated linker configuration is inspected where dalvikvm64 could not find
  libnativehelper.so, and dalvikvm64 -help is also tried with no generated config.
Nothing outside the tree's tmpfs mounts and the result directory is written.
"""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

SOCKETS = [("logd", socket.SOCK_STREAM, 0o666), ("logdr", socket.SOCK_SEQPACKET, 0o666), ("logdw", socket.SOCK_DGRAM, 0o222)]

SCRIPT = r'''#!/bin/bash
ROOT="$1"; OUT="$2"; PY="$3"; SELF="$4"; PROPS="$5"
say() { printf '\n### %s\n' "$*"; }
mount --bind "$ROOT" "$ROOT" && mount -o remount,bind,ro "$ROOT" || { echo "FATAL: read-only bind of the tree failed"; exit 1; }
for d in dev data tmp linkerconfig mnt; do
  mount -t tmpfs -o mode=0755 tmpfs "$ROOT/$d" || { echo "FATAL: tmpfs on /$d failed"; exit 1; }
done
mount -t proc proc "$ROOT/proc" 2>&1 || echo "NOTE: /proc mount failed"
for n in null zero full random urandom tty; do
  : > "$ROOT/dev/$n"; mount --bind "/dev/$n" "$ROOT/dev/$n" 2>&1 || echo "NOTE: could not bind /dev/$n"
done
mkdir -p "$ROOT/dev/socket" "$ROOT/data/system/environ" "$ROOT/data/local/tmp"
chmod 0777 "$ROOT/tmp" "$ROOT/data/local/tmp"
BASEENV=(ANDROID_ROOT=/system ANDROID_DATA=/data ANDROID_STORAGE=/storage ANDROID_ART_ROOT=/apex/com.android.art
  ANDROID_RUNTIME_ROOT=/apex/com.android.runtime ANDROID_I18N_ROOT=/apex/com.android.i18n
  ANDROID_TZDATA_ROOT=/apex/com.android.tzdata HOME=/data
  PATH=/apex/com.android.art/bin:/apex/com.android.runtime/bin:/system/bin:/system/xbin:/vendor/bin)
inroot() { timeout 120 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" "$@" 2>&1; }
trimmed() { "$@" | head -n "${LINES_MAX:-40}"; echo "exit status: ${PIPESTATUS[0]}"; }
echo "mounts ready"

say "S0 property area (one regular file at /dev/__properties__, root-owned, 0444)"
"$PY" "$PROPS" "$ROOT" "$ROOT/dev/__properties__" logd.ready=true servicemanager.ready=true apexd.status=ready \
  ro.boottime.init=1 sys.boot_completed=0 2>&1 | cut -c1-300
ls -ln "$ROOT/dev/__properties__" 2>&1

say "S1 logd with sockets passed in (as init does), uid 1036"
"$PY" "$SELF" --launch "$ROOT" "$OUT/logd.trace" > "$OUT/logd.out" 2>&1 &
LOGDPID=$!
sleep 3
if kill -0 "$LOGDPID" 2>/dev/null; then echo "logd still running (pid $LOGDPID)"; else echo "logd EXITED"; fi
echo "--- logd output:"; head -n 20 "$OUT/logd.out" | cut -c1-240
echo "--- logd strace, failed calls (first 25) and last 20 lines:"
grep -E '= -1 E' "$OUT/logd.trace" 2>/dev/null | sed -E 's/^[0-9]+ +//' | cut -c1-200 | head -n 25
tail -n 20 "$OUT/logd.trace" 2>/dev/null | cut -c1-220
ls -l "$ROOT/dev/socket" | head -n 8
echo "--- getprop through the image's own getprop:"
trimmed inroot /system/bin/getprop ro.build.version.sdk
trimmed inroot /system/bin/getprop logd.ready

say "S2 linkerconfig under strace, then what it produced and what the log recorded"
if command -v strace >/dev/null 2>&1; then
  timeout 120 strace -f -s 200 -o "$OUT/linkerconfig.trace" chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" \
    /apex/com.android.runtime/bin/linkerconfig --target /linkerconfig 2>&1 | head -n 10
  echo "exit status: ${PIPESTATUS[0]}"
  echo "--- distinct failed opens/stats in the trace (first 30):"
  grep -E '= -1 E(NOENT|ACCES|PERM|INVAL)' "$OUT/linkerconfig.trace" | grep -E 'open|stat|access' | sed -E 's/^[0-9]+ +//' | cut -c1-170 | sort -u | head -n 30
else
  trimmed inroot /apex/com.android.runtime/bin/linkerconfig --target /linkerconfig
fi
echo "--- what linkerconfig created or wrote:"
grep -E 'mkdir|O_CREAT|renameat' "$OUT/linkerconfig.trace" 2>/dev/null | sed -E 's/^[0-9]+ +//' | cut -c1-190 | head -n 20
echo "--- /linkerconfig tree:"
(cd "$ROOT/linkerconfig" && find . -maxdepth 2 | sort | head -n 40)
echo "--- logcat -d (last 25):"
timeout 30 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/logcat -d 2>&1 | tail -n 25 | cut -c1-240

say "S3 dalvikvm64 -help (uses /linkerconfig/com.android.art/ld.config.txt if it exists)"
ls -l "$ROOT/linkerconfig/com.android.art" 2>&1 | head -n 4
LINES_MAX=6 trimmed inroot /apex/com.android.art/bin/dalvikvm64 -help

say "S3b dalvikvm64 -help with LD_LIBRARY_PATH to the apex libraries (workaround test: bypasses the missing per-apex linker config)"
LDPATH=/apex/com.android.art/lib64:/apex/com.android.i18n/lib64:/apex/com.android.runtime/lib64:/apex/com.android.conscrypt/lib64:/system/lib64
LINES_MAX=8 trimmed inroot LD_LIBRARY_PATH=$LDPATH /apex/com.android.art/bin/dalvikvm64 -help

say "S4 derive_classpath, with strace; then what the log recorded"
if command -v strace >/dev/null 2>&1; then
  timeout 90 strace -f -s 200 -o "$OUT/derive.trace" chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" \
    /apex/com.android.sdkext/bin/derive_classpath /data/system/environ/classpath 2>&1 | head -n 10
  echo "exit status: ${PIPESTATUS[0]}"
  echo "--- last 25 trace lines:"; tail -n 25 "$OUT/derive.trace" | cut -c1-220
else
  trimmed inroot /apex/com.android.sdkext/bin/derive_classpath /data/system/environ/classpath
fi
echo "--- logcat -d (last 30):"
timeout 30 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/logcat -d 2>&1 | tail -n 30 | cut -c1-240
echo "--- classpath file:"; cut -c1-300 "$ROOT/data/system/environ/classpath" 2>&1 | head -n 8

CPENV=()
if [ -f "$ROOT/data/system/environ/classpath" ]; then
  while read -r a b c; do [ "$a" = export ] && CPENV+=("$b=$c"); done < "$ROOT/data/system/environ/classpath"
fi
IMG=/system/framework/arm64/boot.art
BCP=""; for e in "${CPENV[@]}"; do case "$e" in BOOTCLASSPATH=*) BCP="${e#BOOTCLASSPATH=}";; esac; done
say "S5 dalvikvm64 start with $IMG and a main class that does not exist"
if [ -n "$BCP" ]; then
  timeout 150 strace -f -s 160 -o "$OUT/art.trace" chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" "${CPENV[@]}" LD_LIBRARY_PATH=$LDPATH \
    /apex/com.android.art/bin/dalvikvm64 -Xbootclasspath:"$BCP" -Ximage:"$IMG" -Xnorelocate -Xusejit:false \
    -cp /apex/com.android.art/javalib/core-oj.jar GbosNoSuchMain 2>&1 | head -n 30
  echo "exit status: ${PIPESTATUS[0]}"
  grep -E 'E(NOENT|ACCES|PERM|INVAL|ROFS)' "$OUT/art.trace" 2>/dev/null | grep -v -E '/proc/self/(maps|exe)' | head -n 20
  tail -n 8 "$OUT/art.trace" 2>/dev/null | cut -c1-200
else
  echo "SKIPPED: no BOOTCLASSPATH (derive_classpath did not produce the file)"
fi
kill "$LOGDPID" 2>/dev/null
echo; echo "steps finished"
'''


def launch(root):
    """Create logd's sockets like init, then exec the image's logd as 1036 in the chroot."""
    sockets = Path(root) / "dev/socket"
    sockets.mkdir(parents=True, exist_ok=True)
    env = {"ANDROID_ROOT": "/system", "ANDROID_DATA": "/data", "PATH": "/system/bin"}
    keep = []
    for name, kind, mode in SOCKETS:
        s = socket.socket(socket.AF_UNIX, kind)
        path = str(sockets / name)
        s.bind(path)
        os.chmod(path, mode)
        os.chown(path, 1036, 1036)
        if kind == socket.SOCK_STREAM or kind == socket.SOCK_SEQPACKET:
            s.listen(8)
        if kind == socket.SOCK_DGRAM:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        os.set_inheritable(s.fileno(), True)
        keep.append(s)  # an unreferenced socket would be closed and its fd number reused
        env[f"ANDROID_SOCKET_{name}"] = str(s.fileno())
        print(f"socket {name}: fd {s.fileno()} mode {mode:o}", flush=True)
    chroot = shutil.which("chroot") or "/usr/sbin/chroot"
    command = [chroot, "--userspec=1036:1036", "--groups=1000,1032,3009", str(root), "/system/bin/logd"]
    trace = sys.argv[3] if len(sys.argv) > 3 else None
    if trace and shutil.which("strace"):
        command = [shutil.which("strace"), "-f", "-s", "200", "-o", trace] + command
    os.execve(command[0], command, env)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--launch":
        launch(sys.argv[2])
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    print("=== BRING-UP THREAD, STAGE B3d: LOGD TRACE, LINKERCONFIG WRITES, ART WITH LIBRARY PATH (paste in the \"Remaining steps to boot Android\" thread) ===")
    trees = sorted(Path("work/asahi").glob("android-root-*/root"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not trees:
        sys.exit("no extracted tree; run extract-android-root.py first")
    root = trees[0].resolve()
    for tool in ("unshare", "chroot", "timeout", "mount"):
        if not shutil.which(tool):
            sys.exit(f"{tool} is missing")
    print(f"Tree: {root}")
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one small work/asahi/root-smoke-* directory (script, logs, strace traces, a copy of the generated linker config).")
    print("The tree is mounted read-only; tmpfs mounts hold everything else and vanish at exit. Delete the directory to reverse.")
    out = Path(tempfile.mkdtemp(prefix="root-smoke-", dir="work/asahi")).resolve()
    script = out / "run.sh"
    script.write_text(SCRIPT)
    command = ["unshare", "--user", "--map-auto", "--map-root-user", "--mount", "--pid", "--fork", "--propagation", "private",
               "bash", str(script), str(root), str(out), shutil.which("python3") or "python3", str(Path(__file__).resolve()),
               str(Path(__file__).with_name("make-property-area.py").resolve())]
    done = subprocess.run(command, capture_output=True, text=True, timeout=900)
    text = done.stdout + done.stderr
    (out / "log.txt").write_text(text)
    print(text)
    print(f"namespace run exit status: {done.returncode}")
    print(f"Send the terminal output above (log and traces: {out})")


if __name__ == "__main__":
    main()
