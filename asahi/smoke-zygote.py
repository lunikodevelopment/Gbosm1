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
With --binder (stage B6a onward, approved by the user) the run also installs the temporary
gbos_binder_probe SELinux module (one permissive domain; Fedora stays enforcing), mounts a
private binderfs and starts the image's servicemanager in that domain, then removes the module.
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

SCRIPT = PRELUDE + r'''ZYG="$8"; WAIT="${9:-60}"; SS="${10:-}"; SMCTX="${11:-}"; SMENTRY="${12:-}"
# /dev/kmsg: a FIFO drained into $OUT/kmsg.log instead of the prelude's plain file, where every writer
# overwrote the others from offset 0 (B6f lost apexd's last lines that way); FIFO writes of a line are atomic
rm -f "$ROOT/dev/kmsg"; mkfifo -m 0666 "$ROOT/dev/kmsg"
( exec 9<>"$ROOT/dev/kmsg"; exec cat <&9 >> "$OUT/kmsg.log" ) &
KMSGPID=$!
say "S0 property area"
"$PY" "$PROPS" "$ROOT" "$ROOT/dev/__properties__" logd.ready=true servicemanager.ready=true apexd.status=ready \
  ro.logd.auditd=false ro.logd.kernel=false ro.boottime.init=1 sys.boot_completed=0 ro.zygote=zygote64 \
  ro.property_service.version=2 2>&1 | cut -c1-300

# directories Android's init.rc creates under /data before zygote starts (tmpfs here, gone at exit)
mkdir -p "$ROOT/data/dalvik-cache/arm64" "$ROOT/data/misc" "$ROOT/data/system" "$ROOT/data/user/0" "$ROOT/data/user_de/0" \
  "$ROOT/data/app" "$ROOT/data/anr" "$ROOT/data/tombstones" "$ROOT/data/local/tmp"
chmod 0771 "$ROOT/data/dalvik-cache" "$ROOT/data/dalvik-cache/arm64"
# and under /mnt (zygote prepares /mnt/user/<user> for system_server and apps; init.rc makes the parents)
mkdir -p "$ROOT/mnt/user/0" "$ROOT/mnt/runtime/default" "$ROOT/mnt/runtime/read" "$ROOT/mnt/runtime/write" \
  "$ROOT/mnt/runtime/full" "$ROOT/mnt/expand" "$ROOT/mnt/installer" "$ROOT/mnt/androidwritable" "$ROOT/mnt/pass_through" \
  "$ROOT/mnt/media_rw" "$ROOT/mnt/data_mirror"
chmod 0751 "$ROOT/mnt/user/0"
echo "/storage in the tree: $(ls -ld "$ROOT/storage" 2>&1)"
# init mounts sysfs and the cgroup hierarchies; here /sys is a private tmpfs so the cgroup paths can exist
if [ -d "$ROOT/sys" ]; then mount -t tmpfs -o mode=0755 tmpfs "$ROOT/sys" && echo "tmpfs on /sys (stand-in: no sysfs, no real cgroups)"; else echo "NOTE: tree has no /sys"; fi
# /metadata, /data_mirror and /cache are mount points on a device (B6b listed their missing subfolders); private tmpfs here
for d in metadata data_mirror cache; do
  if [ -L "$ROOT/$d" ]; then echo "/$d in the tree is a symlink to $(readlink "$ROOT/$d") (no tmpfs; the init replay follows it)"
  elif [ -d "$ROOT/$d" ]; then mount -t tmpfs -o mode=0755 tmpfs "$ROOT/$d" && echo "tmpfs on /$d"; fi
done
say "S0b every mkdir and symlink of the image's init .rc files, replayed under the tmpfs mounts, plus cgroups.json paths"
"$PY" "$(dirname "$ZYG")/replay-init-dirs.py" "$ROOT" 2>&1 | cut -c1-900

say "S0c stand-ins: property service socket, and the selinux_android_setcon stub for zygote"
# Services started on request, as init does: apexd is a lazy service (B6h: it exits when it has no client) and
# system_server asks for HALs with ctl.interface_start (B6i: IPowerStats). The stand-in looks the request up in the
# image's .rc files and runs the service through this launcher: own mount namespace, chroot, strace, one at a time.
# Services run as the namespace's root (init would switch to the .rc user/group); they see only the sandbox /dev.
SVCENV=()
if [ -n "$SMCTX" ]; then
  if command -v strace >/dev/null 2>&1; then ATR="strace -f -s 160 -o $OUT/svc-\$NAME-\$(date +%s%N).trace"; else ATR=""; fi
  printf '#!/bin/bash\nNAME="$1"; shift\nexec unshare --mount --propagation private %s chroot "%s" /system/bin/toybox env -i %s "$@" >> "%s/svc-$NAME.out" 2>&1\n' \
    "$ATR" "$ROOT" "${BASEENV[*]}" "$OUT" > "$OUT/start-service.sh"
  chmod 0755 "$OUT/start-service.sh"
  SVCENV=(GBOS_RC_ROOT="$ROOT" GBOS_LAUNCHER="$OUT/start-service.sh")
fi
env "${SVCENV[@]}" "$PY" "$(dirname "$ZYG")/sandbox-property-service.py" "$ROOT/dev/__properties__" "$ROOT/dev/socket/property_service" "$OUT/property-sets.log" &
PROPPID=$!
sleep 1
ls -l "$ROOT/dev/socket/property_service" 2>&1 | cut -c1-120
PREENV=(); PRELOADS=""
mkdir -p "$ROOT/data/local/tmp/gbos"
for lib in libgbos-setcon-shim.so libgbos-ashmem-shim.so; do
  if [ -f "$OUT/$lib" ]; then cp "$OUT/$lib" "$ROOT/data/local/tmp/gbos/"; PRELOADS="${PRELOADS:+$PRELOADS:}/data/local/tmp/gbos/$lib"
  else echo "NOTE: $lib was not built"; fi
done
[ -n "$PRELOADS" ] && PREENV=(LD_PRELOAD="$PRELOADS") && echo "zygote will preload $PRELOADS"
echo "what the image's libcutils says about shared memory (strings) and the related properties:"
strings -n 6 "$ROOT/system/lib64/libcutils.so" 2>/dev/null | grep -i -E 'ashmem|memfd' | head -n 14 | cut -c1-140
grep -h -E '^(sys.use_memfd|ro.treble.enabled)=' "$ROOT"/system/build.prop "$ROOT"/vendor/build.prop "$ROOT"/system_ext/etc/build.prop "$ROOT"/product/etc/build.prop 2>/dev/null

say "S1 linkerconfig first (no logd yet), then logd under the generated config"
timeout 120 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /apex/com.android.runtime/bin/linkerconfig --target /linkerconfig 2>&1 | head -n 5
ls "$ROOT/linkerconfig" | wc -l
echo "system section, default namespace permitted paths (the preloaded stub must be under one):"
grep -n -A400 '^\[system\]' "$ROOT/linkerconfig/ld.config.txt" 2>/dev/null | grep -m 6 'namespace.default.permitted.paths' | cut -c1-160
"$PY" "$SELF" --launch "$ROOT" > "$OUT/logd.out" 2>&1 &
LOGDPID=$!
sleep 3
if kill -0 "$LOGDPID" 2>/dev/null; then echo "logd still running"; else echo "logd EXITED"; fi
head -n 6 "$OUT/logd.out" | cut -c1-200

SMPID=""; APEXDPID=""
if [ -n "$SMCTX" ]; then
  say "S1b private binderfs (binder, hwbinder, vndbinder) and the image's servicemanager in the temporary domain"
  mkdir -p "$ROOT/dev/binderfs"
  if mount -t binder binder "$ROOT/dev/binderfs" 2>&1; then
    "$PY" "$ZYG" --binderfs-devices "$ROOT/dev/binderfs" binder hwbinder vndbinder 2>&1
    for n in binder hwbinder vndbinder; do [ -e "$ROOT/dev/$n" ] || [ -L "$ROOT/dev/$n" ] || ln -s "/dev/binderfs/$n" "$ROOT/dev/$n"; done
    # binderfs creates its devices 0600 root; init's own chmod lines open them up (B6a: system_server got EACCES)
    "$PY" "$(dirname "$ZYG")/replay-init-dirs.py" "$ROOT" --perms-under /dev/binderfs 2>&1 | cut -c1-200
    for n in binder hwbinder vndbinder; do
      [ "$(stat -c %a "$ROOT/dev/binderfs/$n" 2>/dev/null)" = 666 ] || { chmod 0666 "$ROOT/dev/binderfs/$n" && echo "NOTE: no init line set $n to 0666; set it here (Android's ueventd.rc also lists the binder devices as 0666)"; }
    done
    ls -l "$ROOT/dev/binderfs" 2>&1 | cut -c1-120
    ls -l "$ROOT/dev/binder" "$ROOT/dev/hwbinder" "$ROOT/dev/vndbinder" 2>&1 | sed "s#$ROOT##" | cut -c1-140
    "$PY" "$ZYG" --launch-servicemanager "$ROOT" "$SMCTX" "$SMENTRY" "${BASEENV[@]}" > "$OUT/servicemanager.out" 2>&1 &
    SMPID=$!
    sleep 3
    if kill -0 "$SMPID" 2>/dev/null; then echo "servicemanager still running"; else echo "servicemanager EXITED"; fi
    head -n 10 "$OUT/servicemanager.out" | cut -c1-220
    echo "servicemanager domain: $(cat "$ROOT"/proc/[0-9]*/attr/current 2>/dev/null | tr '\0' '\n' | grep -m1 gbos_binder_probe_t || echo 'not seen')"
  else echo "binderfs mount FAILED"; fi
  say "S1c services from the image's .rc files: started by the property stand-in on ctl.interface_start/ctl.start; installd upfront"
  sed 's/^/  /' "$OUT/start-service.sh" | cut -c1-240
  # installd is an ordinary (not lazy) class-main service on a device; system_server polls for it ("installd not found")
  INSTALLD=$("$PY" "$(dirname "$ZYG")/sandbox-property-service.py" --print-service "$ROOT" installd 2>&1)
  echo "installd command line from the .rc files: $INSTALLD"
  case "$INSTALLD" in /*) "$OUT/start-service.sh" installd $INSTALLD & sleep 2
    if kill -0 $! 2>/dev/null; then echo "installd still running"; else echo "installd EXITED"; head -n 5 "$OUT/svc-installd.out" | cut -c1-200; fi;; esac
  APEXDPID=on-demand
fi

say "S2 derive_classpath"
inroot /apex/com.android.sdkext/bin/derive_classpath /data/system/environ/classpath | head -n 5
CPENV=()
while read -r a b c; do [ "$a" = export ] && CPENV+=("$b=$c"); done < "$ROOT/data/system/environ/classpath"
echo "classpath variables: $(printf '%s ' "${CPENV[@]%%=*}")"

say "S2b what path the kernel reports for open files after pivot_root (zygote's fd allowlist compares these)"
timeout 30 "$PY" "$ZYG" --probe-paths "$ROOT" 2>&1 | cut -c1-220

say "S3 zygote: app_process64 -Xzygote --zygote ${SS}, up to ${WAIT}s, no binder"
ls -l "$ROOT/system/etc/preloaded-classes" "$ROOT/system/bin/app_process64" 2>&1 | cut -c1-160
if command -v strace >/dev/null 2>&1; then TR=(strace -f -s 160 -e 'trace=%file,%network,setgroups,setresuid,setresgid,setuid,setgid,capset,prctl,unshare,mount,setns,seccomp,setpriority,dup,dup2,dup3,open_tree,fsopen,fspick,fcntl,eventfd2,memfd_create,epoll_create1,open_by_handle_at' -o "$OUT/zygote.trace"); else TR=(); fi
"${TR[@]}" "$PY" "$ZYG" --launch-zygote "$ROOT" "${BASEENV[@]}" "${CPENV[@]}" "${PREENV[@]}" $SS > "$OUT/zygote.out" 2>&1 &
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
if [ -n "$SMCTX" ]; then
  echo "--- services registered with servicemanager now (image's own 'service list', first 60):"
  timeout 15 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/service list 2>&1 | grep -v linkerconfig | head -n 60 | cut -c1-160
fi
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
echo "--- property set requests the stand-in received ($(wc -l < "$OUT/property-sets.log" 2>/dev/null) lines; repeats counted):"
sed -E 's/ \((updated|added)\)$//' "$OUT/property-sets.log" 2>/dev/null | awk '{c[$0]++; if (!o[$0]++) l[++n]=$0} END {for (i=1;i<=n;i++) printf "%4d %s\n", c[l[i]], l[i]}' | head -n 50 | cut -c1-200
echo "--- system_server lines in logcat (last 25):"
timeout 30 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/logcat -d -b all 2>&1 | grep -a -E ' (SystemServer|SystemServiceManager|ServiceManager|servicemanager|system_server|Looper|ActivityManager|PackageManager)' | grep -v 'runtime.cc:769' | tail -n 25 | cut -c1-260
if [ -n "$APEXDPID" ]; then
  echo "--- services the property stand-in started (first start per service, and any restart):"
  grep -a -E '\(started ' "$OUT/property-sets.log" | sed -E 's/^OK   //' | cut -c1-220 | head -n 20
  grep -a -c 'no .rc service declares' "$OUT/property-sets.log" | sed 's/^/requests no .rc service declares: /'
  grep -a 'no .rc service declares' "$OUT/property-sets.log" | sort -u | head -n 5 | cut -c1-200
  echo "--- processes started from the .rc files, running now:"
  for p in "$ROOT"/proc/[0-9]*; do c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null); case "$c" in /system/*|/vendor/*|/apex/*|/system_ext/*|/odm/*) echo "${p##*/} $c" | cut -c1-140;; esac; done | grep -v -E ' /system/bin/(logd|servicemanager|toybox)' | head -n 15
  echo "--- how each service trace ended:"
  for t in "$OUT"/svc-*.trace; do [ -s "$t" ] && echo "${t##*/}: $(grep -E '^[0-9]+ +\+\+\+ (exited|killed)' "$t" | tail -n 1 | cut -c1-120)"; done 2>/dev/null | head -n 15
  echo "--- service stdout/stderr (first 5 lines each):"
  for f in "$OUT"/svc-*.out; do [ -s "$f" ] && { echo "${f##*/}:"; grep -v linkerconfig "$f" | head -n 5 | cut -c1-200; }; done 2>/dev/null
  echo "--- apexd and lazy-service messages in /dev/kmsg (first 25, without the pre-installed APEX list):"
  grep -a -E 'apexd|LazyServiceRegistrar' "$OUT/kmsg.log" | grep -v -E 'Found pre-installed APEX|Scanning /' | head -n 25 | cut -c1-240
  echo "--- other /dev/kmsg messages, without SELinux duplicate-specification warnings (first 15):"
  grep -a -v -E 'apexd|LazyServiceRegistrar|Multiple same specifications' "$OUT/kmsg.log" | head -n 15 | cut -c1-240
  echo "--- logcat lines from installd, power stats and other started HALs (last 25):"
  timeout 30 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" /system/bin/logcat -d -b all 2>&1 | grep -a -i -E ' (installd|powerstats|power.stats|android.hardware|vendor\.)' | grep -v 'Waited one second' | tail -n 25 | cut -c1-240
fi
pkill -P $$ 2>/dev/null; kill "$ZPID" "$LOGDPID" "$PROPPID" $SMPID "$KMSGPID" 2>/dev/null
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


def binderfs_devices(path, names):
    """Create binder devices in a binderfs mount (BINDER_CTL_ADD on binder-control), as Android's init does."""
    import fcntl
    import struct
    control = os.open(f"{path}/binder-control", os.O_RDWR | os.O_CLOEXEC)
    for name in names:
        request = bytearray(name.encode().ljust(256, b"\0") + bytes(8))  # struct binderfs_device: name[256], major, minor
        try:
            fcntl.ioctl(control, 0xC1086201, request, True)  # _IOWR('b', 1, struct binderfs_device)
            major, minor = struct.unpack_from("<II", request, 256)
            print(f"binderfs device {name}: {major}:{minor}", flush=True)
        except OSError as error:
            print(f"binderfs device {name}: FAILED {error}", flush=True)
    os.close(control)


def launch_servicemanager(root, context, entry, env_items):
    """Start the image's servicemanager in the temporary gbos_binder_probe_t domain, in its own mount namespace.

    Same arrangement as the earlier real-servicemanager probes (probe-servicemanager.c): the host's
    selinuxfs is visible to servicemanager only, and the domain is entered on exec of a file
    labelled gbos_binder_probe_exec_t. That file is a copy of the image's own servicemanager,
    bind-mounted over /system/bin/servicemanager in this namespace only, so the linker sees the
    usual path. The exec context is set through /proc/self/attr/exec, as runcon does.
    """
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)

    def mount(source, target, fstype, flags):
        if libc.mount(source and source.encode(), target.encode(), fstype and fstype.encode(), flags, None) != 0:
            raise OSError(ctypes.get_errno(), f"mount {target}")
    os.unshare(os.CLONE_NEWNS)
    mount(None, "/", None, 0x4000 | 0x40000)  # MS_REC | MS_PRIVATE
    mount(entry, f"{root}/system/bin/servicemanager", None, 0x1000)  # MS_BIND
    mount("tmpfs", f"{root}/sys", "tmpfs", 0)
    os.makedirs(f"{root}/sys/fs/selinux")
    mount("/sys/fs/selinux", f"{root}/sys/fs/selinux", None, 0x1000)
    os.chroot(root)
    os.chdir("/")
    for number in sorted(int(n) for n in os.listdir("/proc/self/fd")):
        if number > 2:
            try:
                os.close(number)
            except OSError:
                pass
    with open("/proc/self/attr/exec", "w") as attr:
        attr.write(context)
    print(f"exec context set to {context}", flush=True)
    env = dict(item.split("=", 1) for item in env_items)
    os.execve("/system/bin/servicemanager", ["servicemanager"], env)


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


def prepare_binder_policy(root, out):
    """Install the temporary gbos_binder_probe module (permissive domain only) and label the servicemanager entry copy.

    Approved by the user for stage B6a. Same module name, template and permissive-domain
    arrangement as probe-binder.py --permissive. The rules beyond the template are the binder
    permissions the earlier real-servicemanager runs needed between unconfined processes and that
    domain (see the comments in probe-binder.py).
    """
    for tool in ("sudo", "semodule", "chcon", "ausearch"):
        if not shutil.which(tool):
            sys.exit(f"{tool} is missing; nothing was installed")
    if Path("/sys/fs/selinux/enforce").read_text().strip() != "1":
        sys.exit("requires SELinux enforcing; no enforcement change will be made")
    current = Path("/proc/self/attr/current").read_text().strip("\0\n ")
    parts = current.split(":")
    if len(parts) < 4 or parts[2] != "unconfined_t":
        sys.exit(f"expected an unconfined_t session, found {current}; nothing was installed")
    context = ":".join(parts[:2] + ["gbos_binder_probe_t"] + parts[3:])
    modules = subprocess.run(["sudo", "semodule", "-l"], capture_output=True, text=True)
    if modules.returncode or any(line.split()[:1] == ["gbos_binder_probe"] for line in modules.stdout.splitlines()):
        sys.exit("cannot list modules, or gbos_binder_probe already exists (left as is); nothing was installed")
    entry = out / "servicemanager-entry"
    shutil.copy2(root / "system/bin/servicemanager", entry)
    entry.chmod(0o755)
    rules = ["(typepermissive gbos_binder_probe_t)",
             "(allow unconfined_t gbos_binder_probe_t (binder (call transfer)))",
             "(allow gbos_binder_probe_t unconfined_t (binder (call transfer)))",
             "(allow unconfined_t unconfined_t (binder (call transfer)))"]
    policy = out / "gbos_binder_probe.cil"
    policy.write_text((HERE / "binder-probe-policy.cil.in").read_text() + "\n".join(rules) + "\n")
    print("Installing the temporary gbos_binder_probe module (sudo may ask for your password)", flush=True)
    if subprocess.run(["sudo", "semodule", "-i", str(policy)]).returncode:
        sys.exit("policy installation failed; nothing else was changed")
    if subprocess.run(["sudo", "chcon", "-t", "gbos_binder_probe_exec_t", str(entry)]).returncode:
        remove_binder_policy(out, entry)
        sys.exit("labelling the servicemanager copy failed; module removed")
    print(f"module installed; servicemanager copy labelled; exec context {context}", flush=True)
    return context, entry


def remove_binder_policy(out, entry):
    import re
    if entry and entry.exists():
        entry.unlink()
    removed = subprocess.run(["sudo", "semodule", "-r", "gbos_binder_probe"], capture_output=True, text=True)
    print(f"temporary module removed: {'yes' if removed.returncode == 0 else 'NO, run: sudo semodule -r gbos_binder_probe'}", flush=True)
    audit = subprocess.run(["sudo", "ausearch", "-m", "AVC,USER_AVC", "-ts", "recent", "-i"], capture_output=True, text=True)
    (out / "denials.txt").write_text(audit.stdout)
    enforced, permissive = {}, {}
    android = re.compile(r'comm="?(system_server|servicemanager|zygote64|app_process64|main|logd|binder:|HwBinder)')
    for line in audit.stdout.splitlines():
        if "avc:" not in line or not ("gbos_binder_probe" in line or "tclass=binder" in line or android.search(line)):
            continue
        short = re.sub(r"^.*avc:\s+", "", line)
        short = re.sub(r" pid=\d+| ino=\d+| dev=\S+| path=\S+| name=\S+", "", short)
        if "permissive=0" in line:
            enforced[short] = enforced.get(short, 0) + 1
        else:
            m = re.search(r"\{ ([^}]*) \}.*tcontext=\S+:(\w+):\S+ tclass=(\w+)", short)
            key = f"{m[1]} on {m[3]} {m[2]}" if m else short[:120]
            permissive[key] = permissive.get(key, 0) + 1
    print(f"--- SELinux denials ENFORCED in the last 10 minutes for Android processes, the temporary domain or binder "
          f"({len(enforced)} distinct; all audit lines in {out / 'denials.txt'}):")
    for short, count in list(enforced.items())[:30]:
        print(f"{count:4d} {short[:300]}")
    print(f"--- logged only (permissive temporary domain), grouped: {len(permissive)} kinds:")
    print("  " + "; ".join(f"{k} x{n}" for k, n in list(permissive.items())[:30]))


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--probe-paths":
        probe_paths(sys.argv[2])
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--launch-zygote":
        launch_zygote(sys.argv[2], sys.argv[3:])
    if len(sys.argv) >= 3 and sys.argv[1] == "--binderfs-devices":
        binderfs_devices(sys.argv[2], sys.argv[3:])
        return
    if len(sys.argv) >= 5 and sys.argv[1] == "--launch-servicemanager":
        launch_servicemanager(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    binder = "--binder" in sys.argv[1:]
    system_server = binder or "--system-server" in sys.argv[1:]
    print("=== BRING-UP THREAD, STAGE " + ("B6j" if binder else "B5a" if system_server else "B4") + ": ZYGOTE PRELOAD" + (" WITH --start-system-server" if system_server else " WITHOUT SYSTEM_SERVER") + " (paste in the \"Remaining steps to boot Android\" thread) ===")
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
    if shutil.which("clang") and shutil.which("ld.lld"):
        for name, source in (("libgbos-setcon-shim.so", "sandbox-setcon-shim.c"), ("libgbos-ashmem-shim.so", "sandbox-ashmem-shim.c")):
            built = subprocess.run(["clang", "--target=aarch64-linux-android35", "-shared", "-nostdlib", "-fPIC", "-O2",
                                    "-fuse-ld=lld", "-Wl,-z,max-page-size=16384", f"-Wl,-soname,{name}",
                                    str(HERE / source), "-o", str(out / name)], capture_output=True, text=True)
            print(f"{name} build exit status: {built.returncode} {built.stderr.strip()[:300]}")
    else:
        print("NOTE: clang or ld.lld missing; the stubs are not built")
    script = out / "run.sh"
    script.write_text(SCRIPT)
    smctx, entry, installed = "", None, False
    if binder:
        smctx, entry = prepare_binder_policy(root, out)
        installed = True
        # Android raises thread priorities (SystemServer.run: setThreadPriority(-2), zygote threads -8,
        # display threads real-time). Nice values are not namespaced: without CAP_SYS_NICE the kernel
        # allows them only within RLIMIT_NICE / RLIMIT_RTPRIO, which are 0 for a Fedora session (B6b:
        # "Cannot set priority to -2"). Raise both limits for this one process tree; they end with it.
        raised = subprocess.run(["sudo", "prlimit", "--pid", str(os.getpid()), "--nice=40:40", "--rtprio=99:99"],
                                capture_output=True, text=True)
        limits = subprocess.run(["prlimit", "--pid", str(os.getpid()), "--nice", "--rtprio", "--noheadings"],
                                capture_output=True, text=True)
        print(f"priority limits for this run (exit {raised.returncode}): {' | '.join(limits.stdout.split(chr(10))).strip()}", flush=True)
    command = ["unshare", "--user", "--map-auto", "--map-root-user", "--mount", "--pid", "--fork", "--propagation", "private",
               "bash", str(script), str(root), str(out), shutil.which("python3") or "python3",
               str((HERE / "smoke-logd-art.py")), str(HERE / "make-property-area.py"),
               str(HERE / "elf-needed.py"), str(HERE / "make-apex-info-list.py"), str(Path(__file__).resolve()), "45",
               "--start-system-server" if system_server else "", smctx, str(entry or "")]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=900)
        text = done.stdout + done.stderr
        (out / "log.txt").write_text(text)
        print(text)
        print(f"namespace run exit status: {done.returncode}")
    finally:
        if installed:
            remove_binder_policy(out, entry)
    print(f"Send the terminal output above (log and trace: {out})")


if __name__ == "__main__":
    main()
