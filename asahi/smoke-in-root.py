#!/usr/bin/env python3
"""Run the image's own Bionic programs inside the extracted root tree (chroot smoke test).

Original project script. In an unprivileged user namespace (your user as root,
/etc/subuid range as ids 1..65535) with private mount and PID namespaces, it
makes the extracted tree read-only, puts private tmpfs mounts over /dev, /data,
/tmp, /linkerconfig and /mnt, mounts a PID-namespace /proc, binds a few host
character devices into /dev (null, zero, full, random, urandom, tty), and then
chroots into the tree for each step. No SELinux module, no sudo, no binder, no
property service and no host device other than those six are involved. The tree
itself is not modified (read-only bind); everything written goes to the tmpfs
mounts, which vanish when the namespace ends. Steps are independent: a failing
step is reported and the next one still runs.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SCRIPT = r'''#!/bin/bash
ROOT="$1"; OUT="$2"
say() { printf '\n### %s\n' "$*"; }
mount --bind "$ROOT" "$ROOT" && mount -o remount,bind,ro "$ROOT" || { echo "FATAL: read-only bind of the tree failed"; exit 1; }
for d in dev data tmp linkerconfig mnt; do
  mount -t tmpfs -o mode=0755 tmpfs "$ROOT/$d" || { echo "FATAL: tmpfs on /$d failed"; exit 1; }
done
mount -t proc proc "$ROOT/proc" 2>&1 || echo "NOTE: /proc mount failed (some steps will report it)"
for n in null zero full random urandom tty; do
  : > "$ROOT/dev/$n"; mount --bind "/dev/$n" "$ROOT/dev/$n" 2>&1 || echo "NOTE: could not bind /dev/$n"
done
mkdir -p "$ROOT/dev/socket" "$ROOT/data/system/environ" "$ROOT/data/local/tmp"
chmod 0777 "$ROOT/tmp" "$ROOT/data/local/tmp"
echo "mounts ready: tree read-only, tmpfs on dev data tmp linkerconfig mnt"

BASEENV=(ANDROID_ROOT=/system ANDROID_DATA=/data ANDROID_STORAGE=/storage ANDROID_ART_ROOT=/apex/com.android.art
  ANDROID_RUNTIME_ROOT=/apex/com.android.runtime ANDROID_I18N_ROOT=/apex/com.android.i18n
  ANDROID_TZDATA_ROOT=/apex/com.android.tzdata HOME=/data
  PATH=/apex/com.android.art/bin:/apex/com.android.runtime/bin:/system/bin:/system/xbin:/vendor/bin)
inroot() { timeout 120 chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" "$@" 2>&1; }
trimmed() { "$@" | head -n 40; echo "exit status: ${PIPESTATUS[0]}"; }

say "S0 init.environ.rc (what Android's init exports; for comparison)"
cat "$ROOT/init.environ.rc" 2>&1 | head -n 30

say "S1 shell and toybox"
trimmed inroot /system/bin/toybox uname -a
trimmed inroot /system/bin/sh -c 'echo shell ok; id; ls /apex | wc -l'

say "S2 service uids inside the namespace (logd is 1036, system is 1000, graphics is 1003)"
trimmed timeout 60 chroot --userspec=1036:1036 "$ROOT" /system/bin/toybox id
trimmed timeout 60 chroot --userspec=1000:1000 "$ROOT" /system/bin/toybox id

say "S3 linkerconfig (generates /linkerconfig/ld.config.txt from the apexes)"
trimmed inroot /apex/com.android.runtime/bin/linkerconfig --target /linkerconfig
ls -l "$ROOT/linkerconfig" 2>&1 | head -n 12
wc -l "$ROOT/linkerconfig/ld.config.txt" 2>&1

say "S4 derive_classpath (builds BOOTCLASSPATH from the apex manifests)"
trimmed inroot /apex/com.android.sdkext/bin/derive_classpath /data/system/environ/classpath
cat "$ROOT/data/system/environ/classpath" 2>&1 | cut -c1-400 | head -n 10

CPENV=()
if [ -f "$ROOT/data/system/environ/classpath" ]; then
  while read -r a b c; do [ "$a" = export ] && CPENV+=("$b=$c"); done < "$ROOT/data/system/environ/classpath"
fi
echo "exported from classpath file: $(printf '%s ' "${CPENV[@]%%=*}")"

say "S5 dalvikvm64 -help (does ART's own binary load and run?)"
trimmed inroot /apex/com.android.art/bin/dalvikvm64 -help

say "S6 boot images present in /system/framework"
find "$ROOT/system/framework" -name '*.art' 2>/dev/null | sed "s#$ROOT##" | head -n 10

IMG=$(find "$ROOT/system/framework" -name 'boot.art' 2>/dev/null | head -n 1 | sed "s#$ROOT##")
BCP=""; for e in "${CPENV[@]}"; do case "$e" in BOOTCLASSPATH=*) BCP="${e#BOOTCLASSPATH=}";; esac; done
say "S7 dalvikvm64 start with the system boot image ($IMG), a main class that does not exist"
if [ -n "$IMG" ] && [ -n "$BCP" ]; then
  if command -v strace >/dev/null 2>&1; then
    timeout 150 strace -f -s 160 -o "$OUT/art-S7.trace" chroot "$ROOT" /system/bin/toybox env -i "${BASEENV[@]}" "${CPENV[@]}" \
      /apex/com.android.art/bin/dalvikvm64 -Xbootclasspath:"$BCP" -Ximage:"$IMG" -Xnorelocate -Xusejit:false \
      -cp /apex/com.android.art/javalib/core-oj.jar GbosNoSuchMain 2>&1 | head -n 40
    echo "exit status: ${PIPESTATUS[0]}"
    echo "--- failed opens in the trace (first 25):"
    grep -E 'E(NOENT|ACCES|PERM|INVAL|ROFS)' "$OUT/art-S7.trace" | grep -v -E '/proc/self/(maps|exe)' | head -n 25
    echo "--- last 12 trace lines:"; tail -n 12 "$OUT/art-S7.trace"
  else
    trimmed inroot "${CPENV[@]}" /apex/com.android.art/bin/dalvikvm64 -Xbootclasspath:"$BCP" -Ximage:"$IMG" -Xnorelocate -Xusejit:false \
      -cp /apex/com.android.art/javalib/core-oj.jar GbosNoSuchMain
  fi
else
  echo "SKIPPED: boot image '$IMG' or BOOTCLASSPATH '${BCP:0:60}' missing"
fi
echo; echo "steps finished"
'''


def main():
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    print("=== BRING-UP THREAD, STAGE B3a: CHROOT SMOKE TEST (paste in the \"Remaining steps to boot Android\" thread) ===")
    trees = sorted(Path("work/asahi").glob("android-root-*/root"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not trees:
        sys.exit("no extracted tree; run extract-android-root.py first")
    root = trees[0].resolve()
    if not (root / "apex/com.android.art/bin/dalvikvm64").exists():
        sys.exit("apex payloads are not extracted; run extract-apexes.py first")
    for tool in ("unshare", "chroot", "timeout", "mount"):
        if not shutil.which(tool):
            sys.exit(f"{tool} is missing")
    print(f"Tree: {root}")
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one small work/asahi/root-smoke-* directory (script, log, strace trace). The tree is mounted read-only; private")
    print("tmpfs mounts hold everything the programs write and vanish when the run ends. Delete the directory to reverse.")
    out = Path(tempfile.mkdtemp(prefix="root-smoke-", dir="work/asahi")).resolve()
    script = out / "run.sh"
    script.write_text(SCRIPT)
    command = ["unshare", "--user", "--map-auto", "--map-root-user", "--mount", "--pid", "--fork",
               "--propagation", "private", "bash", str(script), str(root), str(out)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=900)
    text = done.stdout + done.stderr
    (out / "log.txt").write_text(text)
    print(text)
    print(f"namespace run exit status: {done.returncode}")
    print(f"Send the terminal output above (log and trace: {out})")


if __name__ == "__main__":
    main()
