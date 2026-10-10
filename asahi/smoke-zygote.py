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

SCRIPT = PRELUDE + r'''ZYG="$8"; WAIT="${9:-60}"
say "S0 property area"
"$PY" "$PROPS" "$ROOT" "$ROOT/dev/__properties__" logd.ready=true servicemanager.ready=true apexd.status=ready \
  ro.logd.auditd=false ro.logd.kernel=false ro.boottime.init=1 sys.boot_completed=0 ro.zygote=zygote64 2>&1 | cut -c1-300

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

say "S3 zygote: app_process64 -Xzygote --zygote, up to ${WAIT}s, no system_server, no binder"
ls -l "$ROOT/system/etc/preloaded-classes" "$ROOT/system/bin/app_process64" 2>&1 | cut -c1-160
if command -v strace >/dev/null 2>&1; then TR=(strace -f -s 160 -e 'trace=%file,%network' -o "$OUT/zygote.trace"); else TR=(); fi
"${TR[@]}" "$PY" "$ZYG" --launch-zygote "$ROOT" "${BASEENV[@]}" "${CPENV[@]}" > "$OUT/zygote.out" 2>&1 &
ZPID=$!
for i in $(seq 1 "$WAIT"); do kill -0 "$ZPID" 2>/dev/null || break; sleep 1; done
if kill -0 "$ZPID" 2>/dev/null; then echo "zygote STILL RUNNING after ${WAIT}s"; else wait "$ZPID"; echo "zygote EXITED, status $?"; fi
echo "--- zygote stdout/stderr (first 30):"; head -n 30 "$OUT/zygote.out" | cut -c1-240
echo "--- zygote socket:"; ls -l "$ROOT/dev/socket" 2>&1 | cut -c1-120
echo "--- processes in the namespace:"; for p in /proc/[0-9]*; do printf '%s %s\n' "${p#/proc/}" "$(tr '\0' ' ' < $p/cmdline 2>/dev/null | cut -c1-110)"; done | head -n 15
echo "--- logcat -d, last 40 (zygote, ART, libc, DEBUG):"
timeout 30 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/logcat -d 2>&1 | grep -v -E 'derive_classpath' | tail -n 40 | cut -c1-260
if [ -s "$OUT/zygote.trace" ]; then
  echo "--- failed file/network calls in the trace, distinct (first 30):"
  grep -E '= -1 E' "$OUT/zygote.trace" | sed -E 's/^[0-9]+ +//' | grep -v -E 'PR_SET_VMA|/proc/self|ld.so.preload' | cut -c1-170 | sort | uniq -c | sort -rn | head -n 30
  echo "--- last 10 trace lines:"; tail -n 10 "$OUT/zygote.trace" | cut -c1-200
fi
pkill -P $$ 2>/dev/null; kill "$ZPID" "$LOGDPID" 2>/dev/null
echo; echo "steps finished"
'''


def launch_zygote(root, env_items):
    os.chroot(root)
    os.chdir("/")
    sockets = Path("/dev/socket")
    sockets.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind("/dev/socket/zygote")
    os.chmod("/dev/socket/zygote", 0o660)
    s.listen(8)
    os.set_inheritable(s.fileno(), True)
    env = dict(item.split("=", 1) for item in env_items)
    env["ANDROID_SOCKET_zygote"] = str(s.fileno())
    argv = ["app_process64", "-Xzygote", "/system/bin", "--zygote", "--socket-name=zygote"]
    os.execve("/system/bin/app_process64", argv, env)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--launch-zygote":
        launch_zygote(sys.argv[2], sys.argv[3:])
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    print("=== BRING-UP THREAD, STAGE B4: ZYGOTE PRELOAD WITHOUT SYSTEM_SERVER (paste in the \"Remaining steps to boot Android\" thread) ===")
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
               str(HERE / "elf-needed.py"), str(HERE / "make-apex-info-list.py"), str(Path(__file__).resolve()), "60"]
    done = subprocess.run(command, capture_output=True, text=True, timeout=900)
    text = done.stdout + done.stderr
    (out / "log.txt").write_text(text)
    print(text)
    print(f"namespace run exit status: {done.returncode}")
    print(f"Send the terminal output above (log and trace: {out})")


if __name__ == "__main__":
    main()
