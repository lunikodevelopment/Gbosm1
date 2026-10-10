#!/usr/bin/env python3
"""Second chroot smoke test: logd with passed-in sockets, linker config diagnosis, derive_classpath, ART.

Original project script; same sandbox as smoke-in-root.py (read-only tree, private
tmpfs mounts, user namespace with the /etc/subuid range, PID namespace). New here:
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
ROOT="$1"; OUT="$2"; PY="$3"; SELF="$4"
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
lc_hide() { mkdir -p "$OUT/lc-saved"; cp -a "$ROOT/linkerconfig/." "$OUT/lc-saved/"; umount "$ROOT/linkerconfig"; mount -t tmpfs -o mode=0755 tmpfs "$ROOT/linkerconfig"; }
lc_restore() { umount "$ROOT/linkerconfig"; mount -t tmpfs -o mode=0755 tmpfs "$ROOT/linkerconfig"; cp -a "$OUT/lc-saved/." "$ROOT/linkerconfig/"; }
echo "mounts ready"

say "S1 linkerconfig"
trimmed inroot /apex/com.android.runtime/bin/linkerconfig --target /linkerconfig
ls -l "$ROOT/linkerconfig" | head -n 6

say "S2 where is libnativehelper.so, and what does the generated config say about it"
find "$ROOT/apex" "$ROOT/system/lib64" "$ROOT/system_ext/lib64" "$ROOT/vendor/lib64" -name 'libnativehelper*' 2>/dev/null | sed "s#$ROOT##" | head -n 10
echo "--- sections and dir lines in ld.config.txt:"
grep -n -E '^\[|^dir\.' "$ROOT/linkerconfig/ld.config.txt" | head -n 40
echo "--- lines mentioning nativehelper:"
grep -n -i 'nativehelper' "$ROOT/linkerconfig/ld.config.txt" | head -n 12
echo "--- dir lines for the art apex:"
grep -n -E 'com.android.art' "$ROOT/linkerconfig/ld.config.txt" | head -n 12

say "S3 dalvikvm64 -help with the generated config, then with no generated config"
LINES_MAX=6 trimmed inroot /apex/com.android.art/bin/dalvikvm64 -help
lc_hide
LINES_MAX=8 trimmed inroot /apex/com.android.art/bin/dalvikvm64 -help
lc_restore

say "S4 logd with sockets passed in (as init does), uid 1036"
"$PY" "$SELF" --launch "$ROOT" > "$OUT/logd.out" 2>&1 &
LOGDPID=$!
sleep 3
if kill -0 "$LOGDPID" 2>/dev/null; then echo "logd launcher/process still running (pid $LOGDPID)"; else echo "logd EXITED"; fi
echo "--- logd output:"; head -n 20 "$OUT/logd.out"
ls -l "$ROOT/dev/socket" | head -n 8

say "S5 derive_classpath again, with strace; then what the log recorded"
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
say "S6 dalvikvm64 start with $IMG and a main class that does not exist"
if [ -n "$BCP" ]; then
  timeout 150 strace -f -s 160 -o "$OUT/art.trace" chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" "${CPENV[@]}" \
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
        env[f"ANDROID_SOCKET_{name}"] = str(s.fileno())
        print(f"socket {name}: fd {s.fileno()} mode {mode:o}", flush=True)
    os.execvpe("chroot", ["chroot", "--userspec=1036:1036", "--groups=1000,1032,3009", str(root), "/system/bin/logd"], env)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--launch":
        launch(sys.argv[2])
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    print("=== BRING-UP THREAD, STAGE B3b: LOGD, LINKER CONFIG, DERIVE_CLASSPATH, ART (paste in the \"Remaining steps to boot Android\" thread) ===")
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
               "bash", str(script), str(root), str(out), shutil.which("python3") or "python3", str(Path(__file__).resolve())]
    done = subprocess.run(command, capture_output=True, text=True, timeout=900)
    text = done.stdout + done.stderr
    (out / "log.txt").write_text(text)
    print(text)
    print(f"namespace run exit status: {done.returncode}")
    print(f"Send the terminal output above (log and traces: {out})")


if __name__ == "__main__":
    main()
