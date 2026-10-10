# Asahi development runbook

**All current and future user-executed steps belong here.** Work in Fedora Asahi
and retain the existing checkout and result directories.

Last updated: 10 October 2026, Europe/Amsterdam.
Current status: the image's real servicemanager started and became context
manager in the private root (stage 5a), and the image's own `service list`
got an answer from it over Binder (stage 5b, below). Earlier: Bionic Toybox execution, native Binder echo, Android libbinder's
empty-list request/reply, native Binder object/FD transfer, and the Android
returned-object plus outgoing-FD Parcel probe all passed on Fedora Asahi
ARM64/16 KiB.
The dedicated Binder server domain ran with SELinux enforcing; its temporary
module was removed and the new executable label restored after the latest run.
Qualcomm graphics adaptation remains required.
Next action: the Binder/ART track has nothing for you right now (the ART scan,
apex inspection and apex payload listing are done, results below; stage 9a
waits on a go-ahead). The separate graphics track has its own pending step,
"Graphics stage 3b" further down.
Retain all earlier results; no completed probe needs rerunning.

## Plan: run the image's real servicemanager (changed from "stop" at the user's request)

The user asked to plan this despite Fedora lacking Android SELinux labels. This
is a plan, not a result. Each stage needs the previous stage's evidence, and
any stage may end in "not feasible without changing the image or Fedora policy
classes", which would be reported rather than worked around. No partitioning,
flashing, boot-chain or boot-policy change is part of it; SELinux stays enforcing;
policy changes would use the same temporary, removable module as earlier probes.

1. Host SELinux facts (read-only, command below). Why: the image's servicemanager
   calls `selinux_check_access` with Android classes (`service_manager`) and
   Android-format contexts (`u:object_r:...`). Fedora's kernel policy has no such
   classes or users. A temporary module can add types and rules but cannot add
   object classes. Whether those checks deny depends on kernel behavior for
   unknown classes (`deny_unknown`) and on whether the context strings validate.
   Stage 1 reads those facts; it does not decide the outcome.
2. Read what the image's servicemanager reads (read-only): the VINTF manifest and
   service-contexts file paths and how `Access.cpp` builds the check, from the
   image's libselinux and servicemanager strings/disassembly, as done for the
   Service decoder.
3. Choose the SELinux handling from stages 1-2: (a) host behavior already permits
   the checks, (b) a temporary module grants what Fedora can express, or
   (c) the checks cannot pass without changing the image or the policy classes,
   in which case stop and report. No image binary is patched and no API is
   intercepted.
4. Fixtures inside the private namespace only: copy size-limited VINTF manifests
   and service-contexts from the image to the paths it reads, and provide a
   native property-service stand-in at the private `/dev/socket` so
   `servicemanager.ready` can be set. Both are new native/script code, built and
   run only after their runbook entries.
5. First run: the real `servicemanager` as context manager, then the image's
   `service list` against it (an empty-list success would be a real
   servicemanager milestone). Then extend the native endpoint to register through
   the real `addService` and rerun the Parcel client against it.
6. Android receiving an FD remains a separate question: the stage-5 setup would
   give an Android BBinder (servicemanager itself) that receives a dump
   transaction FD, but nothing observable shows it reading FD data. Decide after
   stage 5 whether another Android service is worth a probe.

### Result: host SELinux facts (stage 1 done)

Reported by the user: `Enforcing`, `deny_unknown=0`, `reject_unknown=0`,
`policyvers=35`, 134 object classes, and of the Android-relevant names only
`binder` exists (`service_manager`, `hwservice_manager` and `property_service` do
not). So the host kernel does not deny unknown classes (it allows them).
Hypothesis, recalled from upstream libselinux and not yet checked in the
image's libselinux: `selinux_check_access` returns success for an unknown class
when `deny_unknown` is 0, which would let the image's servicemanager pass its
service checks without Android labels. Stage 2 must confirm this against the
image's own libselinux before any run.

### Stage 2 partial result: image libselinux file paths

Reported by the user from `work/asahi/servicemanager-inspect-4e70qfl1`
(`system_a:/system/lib64/libselinux.so`, ARM64/16 KiB, 135528 bytes;
`DT_NEEDED`: libpcre2, liblog, libpackagelistparser, libc). Static strings only.
It references these context files: `/system/etc/selinux/plat_service_contexts`,
`plat_hwservice_contexts`, `plat_tee_service_contexts`, `plat_file_contexts`,
`plat_seapp_contexts`, `plat_keystore2_key_contexts`; the matching
`/vendor/etc/selinux/vendor_*`, `/vendor/etc/selinux/vndservice_contexts`,
`/odm/etc/selinux/odm_*`, `/product/etc/selinux/product_*` and
`/system_ext/etc/selinux/system_ext_*` files. A servicemanager run would
therefore read service-contexts files from those fixed paths.

The `nm` lookup printed an empty `selinux_check_access=` because exported
names carry a version suffix (`@@...`); the lookup, not the library, failed.
Whether `selinux_check_access` returns success for an unknown class is still
unconfirmed.

### Stage 2 partial result: selinux_check_access disassembly (first 0x200 bytes)

Reported by the user (image libselinux, `selinux_check_access` at 0xd4e0, ARM64).
Reading of the code (my interpretation of the disassembly, not run):

- After a one-time init, if an internal enabled flag is not 1 the function
  returns 0 (allowed) immediately (0xd5f0).
- Otherwise it converts both context strings, calls `selinux_status_updated`,
  then `string_to_security_class(class)`. A zero result takes the unknown-class
  branch (0xd620). That branch saves errno, calls the log callback if one is set,
  and in one path calls a local helper at 0xe230 and returns 0 when that helper
  returns 0, else continues at 0xd6e8. In upstream libselinux the equivalent
  helper is `security_deny_unknown`, which would match `deny_unknown=0`
  giving "allowed". That identification is a guess from structure: the
  symbol label shown for 0xe230 is wrong (stripped binary).
- A known class with an unknown permission goes to a separate branch (0xd658).
- The disassembly ended at 0xd6dc inside the no-callback path, so the final
  return value there is not seen.

Conclusion so far: consistent with, but not proof of, the unknown-class check
passing on this host. One more read-only look settles it.

### Stage 2 result: selinux_check_access (image libselinux)

Reported by the user; reading is my interpretation of the ARM64 disassembly,
not a run. The helper at 0xe230 builds a path in a 4096-byte buffer from a
stored selinuxfs mount pointer, opens it, reads up to 19 bytes and parses an
integer; that matches `security_deny_unknown` reading `<selinuxfs>/deny_unknown`.
In `selinux_check_access`, an unknown class (string_to_security_class returns 0)
calls this helper at every exit path: a result of 0 returns 0 (allowed), anything
else returns -1 with the saved errno. With the host's `deny_unknown=0` the
unknown-class `service_manager` check should therefore return allowed. Still
unverified: the selinuxfs mount pointer is set (the helper takes another path,
not seen, if it is null), whether the two earlier context conversions accept
the Android-format `u:object_r:...` strings, and the other servicemanager
checks (service-contexts lookup must find a match, else "No match for" denies).

Stage 3 decision: continue with option (a) (host behavior permits the check, no
policy module needed for it), to be confirmed by the first run. Stop and report
if a run shows a denial.

### Stage 4 partial result: fixture directory listings

Reported by the user (`work/asahi/servicemanager-inspect-8_p893ig`, `-p8kux7d8`,
`-1_nwe186`):

- `system_a:/system/etc/vintf`: `manifest.xml`, a `manifest` directory,
  `compatibility_matrix.7.xml`, `.8.xml`, `.202404.xml`, `.202504.xml`,
  `.202604.xml` and `.device.xml`.
- `system_a:/system/etc/selinux`: `plat_service_contexts` (the file the
  servicemanager's service-name lookup needs), `plat_hwservice_contexts`,
  `plat_tee_service_contexts`, `plat_property_contexts`, `plat_file_contexts`,
  `plat_seapp_contexts`, `plat_keystore2_key_contexts`, `plat_sepolicy.cil`,
  mapping files and others.
- `vendor_a:/vendor/etc/vintf` produced no listing, most likely because the
  partition's root is the vendor tree itself, so the path inside it is
  `/etc/vintf`, not `/vendor/etc/vintf`. Not yet confirmed.

### Stage 4 result: fixture inventory complete

Reported by the user (`work/asahi/servicemanager-inspect-4pxrh6i_`, `-b4br_469`,
`-kg1n053n`):

- `vendor_a` is rooted at the vendor tree, so its paths are `/etc/...`, not
  `/vendor/etc/...` (confirmed by the listings).
- `vendor_a:/etc/vintf`: `manifest.xml`, `manifest/`, `compatibility_matrix.xml`.
- `vendor_a:/etc/selinux`: `vendor_service_contexts`, `vndservice_contexts`,
  `vendor_hwservice_contexts`, `vendor_tee_service_contexts`,
  `vendor_property_contexts`, `precompiled_sepolicy` and others.
- `system_a:/system/etc/vintf/manifest/`: 15 service manifest XML files
  (stats, keystore2, netd, suspend, vold, desktop and snapshot services, media
  C2, camera, devicestate, location, omapi, gscd).

So every fixture file named by the libselinux and libvintf strings has a source
in the image, except the odm, product and system_ext copies, which were not
listed (they may not exist; the real servicemanager may tolerate their absence,
unconfirmed).

### Design problem and proposed approach for the first real run

Not yet built or run; this is the plan for stages 4-5.

- Problem: the image's code reads `/system/etc/vintf`, `/system/etc/selinux` and
  `/vendor/etc/...` at fixed absolute paths. Fedora's root has no `/system` or
  `/vendor` and an unprivileged user cannot create them; the existing probes only
  mount over the existing `/dev`.
- Approach: inside the existing private user+mount namespace, build a new root
  directory under `work/asahi/<run>/root` containing copied fixtures at the
  Android paths, read-only bind mounts of the Fedora directories the run needs
  (`/usr`, `/lib64`, and the run's own work directory), private `/dev` and `/proc`,
  and `chroot` into it. Nothing outside the run directory is written; the mounts
  vanish with the namespace.
- First run goal (stage 5a): start only the image's `servicemanager` as the
  context manager, with the existing readiness handshake replaced by whatever it
  needs, and capture its stderr and a syscall trace. No client yet. Expected
  outcomes include "ready", a clean refusal naming the missing piece, or a crash;
  all are evidence.
- Property service: `servicemanager.ready` is set through the property service
  socket. Stage 5a records what it attempts; a native stand-in is built only if
  the trace shows it is the blocker.
- SELinux: per the stage 2/3 reading, no module is expected for the unknown-class
  check. If the first run shows denials, the existing temporary-module pattern
  with an explicit scope statement is the only tool used.

Stage 5a is now built (go-ahead given); its command is under "Run now" above.

## First 5a run result: private-root `/usr` bind mount failed

Reported by the user (`work/asahi/real-servicemanager-cbv6o_kt`): extraction,
build, policy install and label all succeeded; all 12 libraries resolved (none
unresolved), 4 service-context files and the VINTF manifests were copied.
The launcher then stopped with `.../sm-root/usr: Invalid argument` when
bind-mounting `/usr` into the private root, so **servicemanager was never
started**. Cleanup codes were 0 (label restored, module removed); the AVC query
returned `<no matches>`. No SELinux denial was involved. The cause of the
`EINVAL` is not known; I will not guess a fix.

Read-only diagnostic (only an empty temporary directory under `work/asahi`,
removed at the end; no policy or label change):

```bash
cd ~/Gbosm1 && findmnt -o TARGET,PROPAGATION,FSTYPE,OPTIONS -T /usr; d=$(mktemp -d work/asahi/bindtest-XXXXXX); mkdir "$d/usr" "$d/lib64"; unshare --user --map-root-user --mount --propagation private sh -c "mount --bind /usr '$d/usr' && echo USR_BIND_OK || echo USR_BIND_FAIL; mount --bind /usr/lib64 '$d/lib64' && echo LIB64_BIND_OK || echo LIB64_BIND_FAIL; grep -E ' /usr | / ' /proc/self/mountinfo" 2>&1; rmdir "$d/usr" "$d/lib64" "$d" 2>/dev/null; true
```

If `/usr` is not itself a mount point or is marked unbindable, a bind of a
mount point (or another approach) is the next change; the output decides.

### Diagnostic result and fix

Reported by the user: `/` is btrfs (`subvol=/root`), and a tmpfs is mounted at
`/usr/lib/firmware/vendor`, inside `/usr`. In a new user namespace the plain
`mount --bind /usr` failed (`wrong fs type, bad option ...`, EINVAL) while
`mount --bind /usr/lib64` succeeded. Likely cause, from my recollection of the
kernel rule and not verified: a non-recursive bind of a tree that contains a
mount inherited from the parent namespace is refused. Change: the launcher now
binds `/usr` recursively (`MS_REC`), which carries that mount along. The next run
tests this; the cleanup and policy steps are unchanged.

### Second 5a run: root built, `runcon` refused inside it

Reported by the user (`work/asahi/real-servicemanager-sqnoccec`): the recursive
`/usr` bind worked and the whole private root came up (`SERVICEMANAGER_ROOT=READY`).
Then `runcon` printed `/usr/bin/runcon may be used only on a SELinux kernel` and
exited 125, so **servicemanager still never started**. Cleanup codes 0, no AVC
denials. The trace shows `statfs("/sys/fs/selinux")` succeeding with
`SELINUX_MAGIC`, then `faccessat("/etc/selinux/config")` returning ENOENT right
before the message. So the selinuxfs bind is fine; Fedora's libselinux evidently
also wants that config file to exist (my reading of the trace, not of its source).
Change: the extraction now writes a copy of the host `/etc/selinux/config` text
into the private root. Nothing else changed.

### Third 5a run: `runcon` ran, but my launcher passed it a wrong argument

Reported by the user (`work/asahi/real-servicemanager-dtj_y4uc`): with
`/etc/selinux/config` present (`faccessat` now returns 0 in the trace), `runcon`
no longer refused the kernel but printed `failed to create security context:
'runcon': Invalid argument` and exited 125. Cause, in my launcher and not in the
image or SELinux: I passed `"runcon"` as an extra argument after the program path
in the `strace` command line, so `runcon` took the word `runcon` as a context
string to parse. Fixed by removing the extra word. Cleanup codes 0, no AVC
denials; **servicemanager still has not started**.

### Fourth 5a run: the Android linker started and hit SELinux

Reported by the user (`work/asahi/real-servicemanager-wsakyux_`): `runcon` worked,
the traced chain reached `linker64` running `servicemanager` in
`gbos_binder_probe_t`, which exited 1 while loading its first library:
`CANNOT LINK EXECUTABLE ...: unable to fstatfs file for the library "libbase.so":
Permission denied`. First time Android code of this image has run in the private
root; `servicemanager` itself has not run yet. The AVC query listed five denials
for `linker64`, all in the dedicated domain:

- `search` on the private `/dev` tmpfs root (type `user_tmp_t`) three times plus
  once more (it also blocked `/dev/__properties__` and the log socket path);
- `getattr` on the root filesystem (`filesystem`, type `fs_t`, the btrfs root),
  which is the `fstatfs` that failed.

Other things seen and not yet a problem: no `/linkerconfig/ld.config.txt` (the
linker warned and used its default namespaces), no `/dev/__properties__` yet
(blocked by the same denial), and `prctl(PR_SET_VMA_ANON_NAME)` returning EINVAL
(harmless on this kernel as far as shown).

Change: exactly those two permissions are added to the temporary module for
this mode (`user_tmp_t` dir search/getattr and `fs_t` filesystem getattr), on
`gbos_binder_probe_t` only. The run still enforces SELinux; any further denial is
again reported rather than widened blindly.

### Fifth 5a run: the real servicemanager started (milestone)

Reported by the user (`work/asahi/real-servicemanager-hjx_37m8`; launcher exit 0,
cleanup codes 0). Reported facts from its strace and stderr:

- `SERVICEMANAGER_OBSERVED=still running after six seconds`; it ended only when
  the launcher sent SIGTERM (`killed by SIGTERM`).
- It read `/system/etc/selinux/plat_service_contexts` and
  `/vendor/etc/selinux/vendor_service_contexts` (logged "SELinux: Loaded service
  context from:") and the VINTF manifests (logged "Multiple same specifications
  for ..." for a few interfaces, which are log lines in the trace, not failures).
- `ioctl(/dev/binder, BINDER_SET_CONTEXT_MGR_EXT) = 0`: **it became context
  manager** of the private Binder device. It then created an epoll loop on the
  Binder fd and a 5-second timer.
- SELinux: it looked up `/sys/fs/selinux/class/service_manager/index` (ENOENT),
  logged "Unknown class service_manager", and read `deny_unknown` (0). This is
  the Android-class check the plan feared, and it did not stop startup. Whether
  an actual add/find/list check is then allowed is still untested.
- Missing but non-fatal so far: `/dev/socket/property_service` (ENOENT; libc
  logged "Using old property service protocol"), `/dev/socket/logdw` (logd),
  `/dev/socket/traced_producer` (perfetto retries), `/linkerconfig/ld.config.txt`
  (linker default namespaces).
- The five AVC denials printed at the end are the **previous** run's (their
  timestamps, 21:42:39, are before this run's 21:43:34; the AVC query window
  overlaps). I read no new denial in this run's output, but the query does not
  separate runs, so treat that as unconfirmed until the query is bounded.

Confirmed from the run's `summary.json` (reported by the user): `exit_status` 0,
cleanup `context` 0 and `policy` 0, `unresolved` libraries empty, run start
21:43:34 local time. That start time is after the 21:42:39 denials, which confirms
those five AVC lines belong to the previous run.

What this establishes: the unmodified image `servicemanager` runs under the
extracted Android linker in the dedicated domain, with Fedora SELinux
enforcing, and takes the context-manager role. It does not yet establish that
it serves any request: no client has talked to it.

Next (needs the user's go-ahead, since it adds a new client path): run the
image's own `service list` against it. An answer listing zero services would be
the first Android-to-Android Binder exchange in this setup.

### First 5b run: the client binary was missing (my bug)

Reported by the user (`work/asahi/real-servicemanager-4i88qaxi`, attached
report): `servicemanager` again became context manager (`BINDER_SET_CONTEXT_MGR_EXT = 0`)
and ran to the SIGTERM; cleanup codes 0; no new AVC denials (the ones printed
are the old 21:42:39 ones). The list client failed before connecting:
`unable to open file ".../android-runtime/service"`, because
`prepare-servicemanager.py` did not copy the `service` binary into this
mode's runtime. The extraction now copies it (and adds its libraries to the
closure). No Binder exchange between the two Android processes has happened yet.

### Second 5b run: the client waited for `servicemanager.ready`

Reported by the user (`work/asahi/real-servicemanager-mv3v29jr`; launcher exit 0,
cleanup 0, no new AVC denials). The copied `service` started under the extracted
linker and `libbinder` logged `libbinder.BackendUnifiedServiceManager: Waited for
servicemanager.ready for a second, waiting another...` until the launcher's
ten-second limit (`LIST_CLIENT=timed out after ten seconds`, no output). The
real `servicemanager` is expected to set that property through the property
service (`/dev/socket/property_service`, absent here: ENOENT in its trace), so
nothing ever set it. This is the property-service gap named in plan stage 4, now
seen. Change: when `--list-client` is used, the launcher publishes the same
private, immutable one-property fixture (`servicemanager.ready=true`) that the
earlier Android probes used, just before starting the client. This is a stand-in
for the property service, not Android init, and it is stated here so a pass is
not read as more than it is.

### First 5c run: `addService` was refused by the driver (SELinux)

Reported by the user (`work/asahi/real-servicemanager-ugx7yux7`, exit 1, cleanup
0/0): the endpoint's `addService` transaction came back as `BR_FAILED_REPLY`
(0x7211) and the runner stopped with "native endpoint stopped before registering".
The AVC log shows a new denial in this run (21:57:14.258; entries at 21:51 are
stale): `{ transfer }` binder from `unconfined_t` (the endpoint, comm
`binder-echo`) to `gbos_binder_probe_t` (the servicemanager). Passing the
endpoint's own Binder object to the servicemanager needs that permission in this
direction as well; I had only added the servicemanager-to-client direction. The
request never reached the servicemanager's name checks, so whether
`service_contexts` accepts `gbos.parcel` and whether my request layout is right
are still untested. Change: the temporary module also allows `binder transfer`
from `unconfined_t` to `gbos_binder_probe_t` in this mode. Rerun the same command.

### Second 5c run: real `addService` accepted; the client's call was denied (SELinux)

Reported by the user (`work/asahi/real-servicemanager-2jwv26rg`; cleanup 0/0):
- Operationally tested: the native endpoint's `addService` for `gbos.parcel` was
  accepted by the image's real servicemanager (`REGISTER_REPLY bytes=4 status=0`).
  So the request layout and the name check passed in this image (the lookup in
  `service_contexts` did not refuse it).
- The image's `service call gbos.parcel ...` then exited 10 with `Service gbos.parcel
  does not exist`. Its trace shows libbinder obtaining the binder from the lookup
  ("Cannot cache binder as linkToDeath cannot be implemented. serviceName:
  gbos.parcel", a log line with thread pool size 0), then giving up.
- New AVC in this run (21:59:13.441; the earlier entries are stale): `{ call }` binder
  from `unconfined_t` (the client, `linker64`) to `unconfined_t` (the endpoint).
  A call between two unconfined processes is denied here, so the client's first
  transaction to the endpoint was refused; I infer that this made the CLI report the
  service as missing, not yet confirmed. Change: the temporary module also allows
  `binder call` from `unconfined_t` to `unconfined_t` in this mode. Rerun.

### Third 5c run: Android client, real servicemanager and native endpoint passed (milestone)

Reported by the user (`work/asahi/real-servicemanager-3rm97p03`; launcher exit 0,
cleanup 0/0). All required markers appeared: `REGISTER_REPLY bytes=4 status=0`,
`REGISTER_SERVICE=PASS`, `ANDROID_OBJECT_READ=PASS`, `ANDROID_FD_WRITE=PASS`,
`PARCEL_ENDPOINT_REPLY=PASS`, `REGISTER_PARCEL_CLIENT=exit status 0`,
`REGISTER_ENDPOINT=exit status 0`, and the client printed
`Result: Parcel( 47504231 00004000 ...)` (`REGISTER_RECEIPT_VERIFIED=True`).

Operationally tested: the image's real `servicemanager` accepted a native
endpoint's `addService`; the image's `service` CLI found it through that
servicemanager, received the returned Binder object, called it, and sent an
outgoing sealed read-only memfd that the endpoint verified (16384 bytes) before
replying with the receipt. This is the earlier Parcel milestone with the stand-in
lookup replaced by the real servicemanager.

New AVC in this run: only the stale ones plus two `{ open }` on `/dev/__properties__`
for the servicemanager domain at 22:00:28 (its libc cannot read the property
file; no effect observed). Not changed.

Limits: the property file is my stand-in for the property service; service_manager
access checks are only observed as unknown class allowed by `deny_unknown`;
`gbos.parcel` was accepted by whatever `service_contexts` matching the image
performs (not examined further); no HAL or Android-side Binder server was run.

Proposed next stage (not started, needs your go-ahead): reverse the direction,
an Android-built Binder server registering with the real servicemanager (the
image has no suitable test service, so this needs either an existing image
service that can run alone or a minimal client I would have to build, which
I will check before proposing a design), then Android receiving an FD.

## Next action: ART / apex / zygote scan (read-only)

**This step modifies no system file and does not touch the image.** It writes
only one new `work/asahi/art-scan-*` result directory (a text report). Delete
that directory to reverse it. Nothing from the image is executed or unpacked.

Why: the Java side (zygote, system_server) needs ART, the apex files and the
boot images. Before any start attempt, this lists which of those the image
actually contains (apex names, `/system/framework` boot images and jars,
`app_process`/`dalvikvm`-type binaries) and every zygote or BOOTCLASSPATH line
in the `.rc` files. It shows presence only, not that anything is valid or can
run.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/audit-art.py
```

Paste the terminal output.

## Results: apex payload listing (`apex-payload-3m01gbay`; enumerated, nothing mounted or run)

- `com.google.android.art`: `bin/dalvikvm64`, `dex2oat64`, `odrefresh`,
  `lib64/libart.so`, and `javalib/core-oj.jar`, `core-libart.jar`,
  `okhttp.jar`, `bouncycastle.jar` are present.
- `com.android.runtime`: `bin/linker64` present. The check for
  `lib64/bionic/libc.so` reported not found (its `/lib64` has 16 entries; the
  path may differ, not yet looked at).
- `com.android.i18n` (`libicu.so`, `etc/icu`, `core-icu4j.jar`),
  `com.google.android.tzdata6` (`etc/tz/tzdata`) and
  `com.google.android.conscrypt` (`libjavacrypto.so`, `conscrypt.jar`) are present.
- Every payload sits at zip offset 16384, so `dump.erofs --offset=16384` reads
  it in place.
- Not shown: that `dalvikvm64` starts, that a boot image loads, or any dex to run.

**Next action: none for you right now.** The proposed next stage (9a) extracts
these five payloads into the private root at `/apex/...` and tries a bare
`dalvikvm64` start; it is not yet written and waits on your go-ahead in the thread.

## Next action: apex inspection (copies five apex files; nothing is run)

**THIS STEP MODIFIES STORAGE (ordinary development write).** Scope: one new
`work/asahi/apex-scan-*` directory in your checkout holding copies of five apex
files (runtime, art, i18n, tzdata6, conscrypt; a few hundred MB at most).
Filesystem: your home filesystem. The image is only read; nothing is mounted,
unpacked or executed. Loss: none. Reversal: delete the printed directory.

Why: ART needs the runtime and art apex payloads mounted at `/apex/...`. This
reports each file's zip members and whether the payload is ext4 or erofs and
stored uncompressed, which decides how the payload can be exposed in the
private root without a loop mount.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/audit-apex.py
```

Paste the terminal output.

## Results: ART / zygote scan (`art-scan-gmclkum2`; enumerated, nothing run)

- 39 apex files in `/system/apex`, including `com.android.runtime`,
  `com.google.android.art`, `i18n`, `tzdata6`, `conscrypt`.
- `/system/bin` has `app_process`, `app_process64`, `linker64`, `installd`,
  `bootanimation`, `zygote_cli` and `zygote_next`.
- `/system/framework` has 16 boot-image files (`.art/.oat/.vdex`) and 163 jars;
  `system_ext_a:/framework` has 233 entries.
- Zygote runs from `init.zygote64.rc`: `app_process64 -Xzygote /system/bin
  --zygote --start-system-server --socket-name=zygote`, with a stream socket
  `zygote` (660 root system). Its classpath comes from init properties, which
  the private root does not provide, so it must be supplied by hand.
- Not shown: that any apex is valid, mountable or complete, or that ART starts.

## Results: stage 8b (`real-servicemanager-wn7agd9c`)

Operationally tested on the user's M1 (output read):
- The three missing libraries were in `vendor_a:/lib64/hw` (plus one more
  dependency of `libec_executor`). With them, nine of twelve programs registered
  and answered the interface-descriptor call: amp calibration, camera-tool, ec,
  futility, i2c, keyboard proxy, vpd, demura file finder and servicetracker.
  Counting stage 6 (`disk_info`, `gsiservice`) and stage 7 (five), sixteen
  distinct image services have registered in the private root so far.
- Still failing, reasons not established: `display.color-service` (exit 255, no
  message), `suspend_notification_service` (Rust "No such file or directory",
  file not named) and `thermal-service.pixel` (SIGABRT after the
  property-protocol message). Traces are in the result directory.
- Denials this run: the same two as before; none for any HAL.

## Completed command: stage 8b, the stage 8a command again with a wider library search

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stage 8a (one new
`work/asahi/real-servicemanager-*` directory, the temporary `gbos_binder_probe`
module with only `gbos_binder_probe_t` permissive, one label on the new launcher,
all removed or restored by the runner; SELinux stays enforcing; private mounts
end with the namespace; no host device added; no partitioning, formatting,
flashing or boot change).

What changes: three programs in 8a could not start because one library each was
not found in `system_a:/system/lib64` or `vendor_a:/lib64`. The extractor now also
tries `vendor_a:/lib64/hw` and, when those partitions exist in the image,
`system_ext_a`, `product_a` and `odm_a`, and the "unresolved" message lists every
place it tried. The program list is the same twelve.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --permissive --serve-image vendor_a:/bin/hw/vendor.google.desktop.ec-executor,vendor_a:/bin/hw/vendor.google.desktop.keyboard_proxy_service,vendor_a:/bin/hw/vendor.google.desktop.vpd_executor.vpd-executor,vendor_a:/bin/hw/vendor.google.desktop.i2cexecutor.i2c-executor,vendor_a:/bin/hw/vendor.google.desktop.futility-executor,vendor_a:/bin/hw/vendor.google.desktop.camera-tool-executor,vendor_a:/bin/hw/vendor.qcom.amp.calib,vendor_a:/bin/hw/vendor.qti.hardware.display.color-service,vendor_a:/bin/hw/vendor.qti.hardware.display.demura-service,vendor_a:/bin/hw/suspend_notification_service,vendor_a:/bin/hw/vendor.qti.hardware.servicetrackeraidl-service,vendor_a:/bin/hw/android.hardware.thermal-service.pixel
```

Wait for the prompt to return (about 60 seconds), then paste from the first
`UNRESOLVED` line (if any) and from `servicemanager-run` to the end.

## Results: stage 8a (`real-servicemanager-wnf_t3xy`)

Operationally tested on the user's M1 (output read):
- Six of twelve registered and answered the interface-descriptor call:
  `IAmpCalibration`, `IFutilityExecutor`, `II2CExecutor`, `IKeyboardProxyService`,
  `IVpdExecutor` and `IDemuraFileFinder` (all `/default`). With stage 7 that is
  eleven distinct image HAL services registered in the private root so far (plus
  `disk_info` and `gsiservice` from stage 6).
- Did not start: `ec-executor`, `camera-tool-executor` and
  `servicetrackeraidl-service` (a library each not found in the searched places:
  `libec_executor.dylib.so`, `libdesktop_camera_device_config.dylib.so`,
  `vendor.qti.hardware.servicetrackeraidl-impl.so`).
- Started and failed: `suspend_notification_service` (Rust error "No such file or
  directory", file not named), `display.color-service` (exit 255, no message) and
  `thermal-service.pixel` (SIGABRT after the property-protocol message). Reasons
  not yet established; the traces are in the result directory.
- Denials this run: the same two as before; none for the HALs.

## Completed command: stage 8a, start twelve more image HAL programs and list who registers

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stage 7a/7b: one new
`work/asahi/real-servicemanager-*` directory, the temporary `gbos_binder_probe`
module with only `gbos_binder_probe_t` permissive, one label on the new launcher,
all removed or restored by the runner; SELinux stays enforcing; private mounts
end with the namespace. No host device is added to the private root. No
partitioning, formatting, flashing or boot change.

What it does: as 7a, with twelve other programs chosen from the `.rc` scan
(hardware-light, AIDL-declaring): `ec-executor`, `keyboard_proxy_service`,
`vpd-executor`, `i2c-executor`, `futility-executor`, `camera-tool-executor`,
`amp.calib`, `display.color-service`, `display.demura-service`,
`suspend_notification_service`, `servicetrackeraidl-service` and
`thermal-service.pixel`. Each starts unconfined in the private root, 0.5 s apart.
After about 9 seconds `service list` runs, every registered service gets the
interface-descriptor call, and everything is stopped. Expect several to exit
early (missing devices, sockets, properties or config files). Their exit state,
output and last trace lines are what this stage collects. Their command-line
arguments in the `.rc` files are not passed.

If an extraction path is wrong, the run stops before the module is installed.
Wait for the prompt to return (about 60 seconds), then paste from
`servicemanager-run` to the end.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --permissive --serve-image vendor_a:/bin/hw/vendor.google.desktop.ec-executor,vendor_a:/bin/hw/vendor.google.desktop.keyboard_proxy_service,vendor_a:/bin/hw/vendor.google.desktop.vpd_executor.vpd-executor,vendor_a:/bin/hw/vendor.google.desktop.i2cexecutor.i2c-executor,vendor_a:/bin/hw/vendor.google.desktop.futility-executor,vendor_a:/bin/hw/vendor.google.desktop.camera-tool-executor,vendor_a:/bin/hw/vendor.qcom.amp.calib,vendor_a:/bin/hw/vendor.qti.hardware.display.color-service,vendor_a:/bin/hw/vendor.qti.hardware.display.demura-service,vendor_a:/bin/hw/suspend_notification_service,vendor_a:/bin/hw/vendor.qti.hardware.servicetrackeraidl-service,vendor_a:/bin/hw/android.hardware.thermal-service.pixel
```

## Completed command: stage 7b, call each registered HAL once and read two getters

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stage 7a (one new
`work/asahi/real-servicemanager-*` directory, the temporary `gbos_binder_probe`
module with only `gbos_binder_probe_t` permissive, one label on the new launcher,
all removed or restored by the runner; SELinux stays enforcing; private mounts
end with the namespace). No partitioning, formatting, flashing or boot change.

What changes from 7a: `health` is dropped (it aborts without
`/sys/class/power_supply`, which the private root does not have and which this
step does not add). After the group is up and `service list` has run, the runner
sends the interface-descriptor call to every registered service (proves each
answers over Binder), then `lights` code 2 and `oemlock` code 1. Those two codes
are my recollection of the AIDL order (`getLights` and `getName`, both read-only);
they are unverified, and the private root has no devices for a wrong code to
touch. Replies print as raw Parcel dumps.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --permissive --serve-image vendor_a:/bin/hw/android.hardware.lights-service.android-desktop,vendor_a:/bin/hw/android.hardware.dumpstate-service-desktop,vendor_a:/bin/hw/android.hardware.power.stats-service.desktop,vendor_a:/bin/hw/vendor.google.desktop.oemlock-service,vendor_a:/bin/hw/vendor.qti.hardware.sensorscalibrate-service --group-calls android.hardware.light.ILights/default:2,android.hardware.oemlock.IOemLock/default:1
```

Wait for the prompt to return (about 40 seconds), then paste from
`servicemanager-run` to the end.

## Results: stage 7b (`real-servicemanager-oqsaxpof`)

Operationally tested on the user's M1 (output read):
- All five registered image programs (dumpstate, lights, oemlock, power.stats,
  sensorscalibrate) answered the interface-descriptor call with their AIDL
  descriptor over Binder through the image servicemanager and `service` CLI.
- `lights` code 2 returned an empty reply parcel with status 0 (consistent with
  `getLights` returning an empty list; there are no light devices in the private
  root). `oemlock` code 1 returned status 0 and the string "Desktop OEMLock HAL"
  (consistent with `getName`). The code-to-method mapping was from memory; the
  replies fit it but do not prove it.
- Denials this run: the same two servicemanager `/dev/__properties__` ones
  (`open`, `map`); none for the five HALs.

## Results: stage 7a (`real-servicemanager-me3h5b6w`)

Operationally tested on the user's M1 (output read):
- After the library-symlink fix, all six programs extracted and started in the
  private root under the image servicemanager. `service list` then showed five
  registered, as the image's own programs: `android.hardware.dumpstate.IDumpstateDevice`,
  `android.hardware.light.ILights`, `android.hardware.oemlock.IOemLock` (a Rust
  program), `android.hardware.power.stats.IPowerStats` and
  `vendor.qti.hardware.sensorscalibrate.ISensorsCalibrate` (all `/default`).
- `health` aborted (SIGABRT): its uncaught `std::filesystem` error was
  `/sys/class/power_supply` missing. Not a Binder problem.
- Denials this run: the same two as before (servicemanager `open` and `map` of the
  private `/dev/__properties__`); no others, permissive or not, for any program.
- First run (`real-servicemanager-oe8haswq`) left seven libraries out because the
  image stores them as symlinks or in the vendor partition; fixed in 1990ff4.

## Completed command: stage 7a, start six image HAL programs together and list who registers

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stages 5 and 6: one new
`work/asahi/real-servicemanager-*` result directory, the temporary
`gbos_binder_probe` SELinux module (with `--permissive`, only for
`gbos_binder_probe_t`) and one label on the new launcher, restored and removed by
the runner (it refuses a preexisting module; SELinux stays enforcing), and
private mounts that end with the namespace. Nothing else is written; no
partitioning, formatting, flashing or boot change.

What it does: starts the image servicemanager, then these image programs one
after another (0.5 s apart, unconfined, in the private root, each under strace):
`lights`, `dumpstate`, `power.stats`, `oemlock`, `health` and
`sensorscalibrate`. After about 3.5 seconds it runs the image's `service list` and
stops them all. This is not Android init: no property service, no mounts, no
`/sys/fs/selinux` write. The programs only see the private root and the private
binder device. Expect some to exit early (missing properties, devices or
sockets); that is the result being collected. The runner prints each program's
exit state, its output and the last trace lines, and the service list.

If extraction fails (a path differs in the image), the run stops before any
module is installed and nothing is changed.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --permissive --serve-image vendor_a:/bin/hw/android.hardware.lights-service.android-desktop,vendor_a:/bin/hw/android.hardware.dumpstate-service-desktop,vendor_a:/bin/hw/android.hardware.power.stats-service.desktop,vendor_a:/bin/hw/vendor.google.desktop.oemlock-service,vendor_a:/bin/hw/android.hardware.health-service.android-desktop,vendor_a:/bin/hw/vendor.qti.hardware.sensorscalibrate-service
```

Paste the terminal output from `servicemanager-run` to the end. Wait for the
prompt to return first (about 40 seconds).

## Results: stage 6d with the permissive domain (`real-servicemanager-ywc97a33`)

Operationally tested on the user's M1 (reported by the user, output read):
- `.rc` scan: 188 files, 196 services; no SELinux-touching line in action
  blocks (static text only).
- Permissive run: the whole servicemanager domain produced exactly 2 unique
  denials, `open` and `map` of the private `/dev/__properties__` (`user_tmp_t`).
  Both were already known and benign. The temporary module was removed and the
  label restored. Unconfined processes were not loosened and logged no denials.
- gsid codes 9-24, sent `fd /proc/self/fd/0 i64 16384`: 7 replied (codes 9, 11,
  18, 20, 22, 23, 24) and 9 timed out (gsid waits on the absent `vold` service
  and runs out of binder threads). Code 24 ran real gsid code that failed on the
  absent `/dev/device-mapper` ("Could not list devices"). No gsid system call on
  the received memfd except `close`; libbinder again rejected plain reads at the
  descriptor's position ("protected data in Parcel"). So no gsid method with this
  layout read the descriptor. Not shown: whether any method with the descriptor
  in another position reads it.

## Completed scan: init `.rc` files (read-only; `init-rc-tn9de5ou`)

**This step does not modify the image or any system file.** It writes only one
new `work/asahi/init-rc-*` result directory (a text report). Delete that
directory to reverse it. Nothing from the image is executed.

Why: instead of starting the image's whole `init` (which loads SELinux policy,
mounts filesystems, runs ueventd and may reboot, so it is not proposed), the
scan lists every service the image's `.rc` files declare, with executable,
user, class, flags and sockets, and counts the risky non-service lines. The
result decides which few services can be started together from one launcher in
dependency order. The listing shows what is declared, not that a service can
start outside Android init.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/audit-init-rc.py
```

Paste the terminal output.

## Completed option: permissive probe domain (`--permissive`; used in `real-servicemanager-ywc97a33`)

**THIS STEP MODIFIES STORAGE** (same scope as stages 5 and 6: one new
`work/asahi/real-servicemanager-*` directory, the temporary `gbos_binder_probe`
SELinux module and one label on the new launcher, restored and removed by the
runner, which refuses a preexisting module; private mounts end with the
namespace; nothing else is written).

What changes with `--permissive`, plainly:
- The temporary module adds `(typepermissive gbos_binder_probe_t)`. Only the
  servicemanager domain this probe creates becomes permissive, meaning its
  denials are logged but not enforced.
- Fedora stays enforcing. Every other process, including your session and the
  probe's native endpoint, `service` clients and served image programs
  (`unconfined_t`), is not loosened. Their binder rules still need explicit
  entries.
- The same cleanup removes the module afterwards; the run prints the unique
  denials from this run only (`denials-this-run.txt`).

Run it only after the two outputs above are in. Example for the stage 6d probe:

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --permissive --serve-image system_a:/system/bin/gsid --fd-codes 9-24
```

## Completed command: stage 6d, try `gsid` codes 9 to 24 with the descriptor first

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stage 6b: one new
`work/asahi/real-servicemanager-*` result directory, the temporary
`gbos_binder_probe` SELinux module and one label on the new launcher (restored
and removed by the runner; refuses a preexisting module; SELinux stays
enforcing), and private mounts that end with the namespace. Nothing outside the
new directory is written; no partitioning, formatting, flashing or boot change.

What changes from 6b: only the code range, 9 to 24 instead of 1 to 8. Each call is
`service call gsiservice <code> fd /proc/self/fd/0 i64 16384` with a fresh sealed
read-only memfd on the client's stdin. Codes that do not exist return an
unknown-transaction error. Methods that mutate GSI state can only fail: `gsid`'s
paths (`/data/gsi`, `/metadata/gsi`, device-mapper) do not exist in the private
root, whose `/dev` has only the private binder and null devices. A pass for this
question is a `gsid` system call on the received descriptor other than `close`
(for example `fstat`, `mmap`, `read`), which the runner prints.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --serve-image system_a:/system/bin/gsid --fd-codes 9-24
```

Paste the terminal output.

## Completed look: stage 6c, what did `gsid` do around each received descriptor? (read-only)

This step does not modify storage: it only reads the trace file the stage 6b run
already wrote and prints some lines; nothing is created or changed and nothing is
executed.

### Result: stage 6c

Reported by the user (`server.trace` of `real-servicemanager-7bw1u09s`, lines around
the eight `close(5</memfd:gbos-android-parcel>)` calls):
- In all eight calls the only system call that names the received descriptor is its
  final `close(5)`: no `fstat`, `mmap`, `read`, `ftruncate` or `dup` on it. No handler
  consumed the descriptor for codes 1 to 8.
- The five calls that returned `Operation not permitted` (codes 1, 3, 4, 5, 6) are the
  five that log `libbinder.Parcel: Attempt to read or write from protected data ...
  pos: 64 ... object size: 24` just before the close. By recollection of upstream (not
  checked in this image) libbinder returns `PERMISSION_DENIED` (-1) from exactly this
  check: a handler read a plain integer at offset 64, where the descriptor object is.
  So the refusals are Parcel-layout mismatches, not a caller permission check, and these
  five methods take a number first, not a descriptor.
- Call 7 additionally tried to open `/metadata/gsi/dsu/install_status` (ENOENT in the
  private root) and replied false; calls 2 and 8 take no arguments and ignored the extra data.

Reading: none of codes 1-8 is a method that takes a file descriptor first. The
descriptor-taking methods (by recollection `commitGsiChunkFromStream` and
`setGsiAshmem`, both "descriptor then number", which is the layout already sent) must
have higher codes.

## Completed command: stage 6b, does Android receive a file descriptor? (`gsid`; FD delivered, content not yet read)

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stage 6a: one new
`work/asahi/real-servicemanager-*` result directory, the temporary
`gbos_binder_probe` SELinux module and one label on the new launcher (restored
and removed by the runner; refuses a preexisting module; SELinux stays
enforcing), and private mounts that end with the namespace. Nothing outside the
new directory is written; no partitioning, formatting, flashing or boot change.

What happens: the real `servicemanager` starts, then the image's unmodified
`/system/bin/gsid` (the GSI installer service; the only candidate whose strings show
it reading an FD from a call) is started in the same private root, unconfined as your
user. The image's `service list` finds its name and `service call <name> 1598968902`
asks for its interface descriptor, as in 6a. Then, for each transaction code from 1
to 8, the image's `service call <name> <code> fd /proc/self/fd/0 i64 16384` sends
a sealed read-only 16 KiB memfd on the client's stdin plus a number.

Why a range: I do not know which code of the image's `IGsiService` takes a file
descriptor (my recollection of upstream suggests `commitGsiChunkFromStream` and
`setGsiAshmem`, but their numbering is unverified). A wrong code should be refused
with a bad-Parcel status. gsid works only on `/data/gsi`, `/metadata/gsi` and device-mapper
paths that do not exist in the private root (its `/dev` holds only the private
binder and null devices), so any install action fails inside the namespace; it has
no path to your disks. Evidence of receipt is the gsid trace naming the sent
memfd (`memfd:gbos-android-parcel`) after the call; the runner collects those lines. A
gsid that cannot start without Android init, or that exits, is an acceptable
result to report.

### Result: stage 6b

Reported by the user (`work/asahi/real-servicemanager-7bw1u09s`; launcher exit 0,
cleanup 0/0; the only new AVC lines are two more `/dev/__properties__` `open`
denials for the servicemanager domain). Operationally tested:
- The image's unmodified `/system/bin/gsid` started in the private root, registered
  `gsiservice: [android.gsi.IGsiService]` with the real servicemanager, and answered
  the interface-descriptor call (`android.gsi.IGsiService`, exit 0).
- For each code 1 to 8 the image's `service call gsiservice <code> fd /proc/self/fd/0
  i64 16384` exited 0. In `gsid`'s own `strace -yy` the sent memfd appears as
  `5</memfd:gbos-android-parcel>(deleted)` and is closed by `close(5)` after each of the
  eight calls. So the kernel installed the descriptor into the Android server's fd
  table and libbinder released it afterwards: an Android process *received* an FD
  sent through a Binder call.
- Replies by code: 1, 3, 4, 5, 6 returned `Error: 0xffffffffffffffff "Operation not
  permitted"` (status -1); 2 returned a 36-byte reply (status 0, then a zeroed
  32-byte parcelable, consistent with a progress query that takes no arguments);
  7 returned `00000000 00000000`; 8 returned `00000000 00000001`. Which method each
  code is remains unverified.
- `gsid`'s trace logs `Attempt to read or write from protected data in Parcel ... pos: 64
  ... object size: 24` for several calls: a handler tried to read plain data at offset 64,
  where my FD object sits (it is the first value after the interface token). That
  says those handlers did not read the descriptor as an FD. (My first guess that the
  `-1` replies were a caller permission check is superseded by the 6c result below.) A `/metadata/gsi/dsu/install_status` open failing with ENOENT
  also appears, as expected in the private root.

Not established: that any Android code *read the contents* of the received file. The
trace only shows delivery and close. The next step (read-only, existing files) looks at
exactly what `gsid` did around each received descriptor to see whether the refusals
come before or after the FD is read.

## Completed command: stage 6a, an image program serves, the image's `service` CLI calls it (passed)

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stage 5a/5c: one new
`work/asahi/real-servicemanager-*` result directory, the temporary
`gbos_binder_probe` SELinux module and one label on the new launcher (restored
and removed by the runner; refuses a preexisting module; SELinux stays
enforcing), and private mounts that end with the namespace. Nothing outside the
new directory is written; no partitioning, formatting, flashing or boot change.
The vendor program is extracted from the image into the new directory and run
unmodified under the extracted linker, unconfined as your user in the private
namespace.

What happens: the real `servicemanager` starts as in 5c. After the private
readiness property fixture is published, `vendor_a:/bin/disk_info_service` is
started in the same private root. The image's `service list` then shows what it
registered, and the image's `service call <that name> 1598968902` (the standard
interface-descriptor transaction) asks it for its interface descriptor. No FD is
involved yet; this tests an Android-built server registering with the real
servicemanager and answering one call. The SELinux additions are the same three
binder permissions as in 5c (no new ones planned).

### Result: stage 6a passed

Reported by the user (`work/asahi/real-servicemanager-jckrdcwm`; launcher exit 0,
cleanup 0/0). Operationally tested:
- The image's unmodified `vendor_a:/bin/disk_info_service` (an NDK Binder server)
  registered `com.google.desktop.disk_info.IDiskInfoService/default` with the
  image's real servicemanager. `service list` printed `Found 2 services:` that
  name and `manager`.
- `service call ... 1598968902` exited 0 and returned the String16
  `com.google.desktop.disk_info.IDiskInfoService`.
- The server's own log shows libbinder's lazy-service registrar receiving the
  servicemanager's client-count callback ("has clients: 1"), so the real
  servicemanager also made a callback transaction into the Android server.
- The only new AVC lines are two more `{ open }` denials of
  `/dev/__properties__` for the servicemanager domain (22:10:03); the earlier lines
  in the output are stale. The server and clients ran unconfined, so no extra
  SELinux permission was needed beyond the three from 5c.

Limits: no FD was involved; the server never touched its block devices; the
property service remains a stand-in.

## Completed scans: which image program could serve a call (stage 6 candidates)

**THIS STEP MODIFIES STORAGE**

Scope: creates one new `work/asahi/binder-servers-*` directory (a `summary.json`)
on your Fedora filesystem. The image and everything else are only read; nothing is
executed, no SELinux module is installed. Loss: none. Reversal: delete that
directory.

Why: Android *receiving* an FD needs an Android process that serves a Binder
object. The real servicemanager is probably not one: by recollection of upstream
(not checked in this image) it registers itself as context manager without
accepting FDs, so the driver would refuse a call that carries one. The scan ranks
the 88 Binder-linked programs from the earlier scan by static hints (registers a
service, reads a file descriptor, avoids HAL-only dependencies, needs few extra
libraries beyond the runtime already prepared). Hints are not proof; the next
step would pick one candidate and inspect it before any run.

### Results

Candidate scan (`work/asahi/binder-servers-i5l64kvf`, reported by the user; 88
programs ranked, none skipped): by static hints the programs that register a
service with few extra libraries are `gsid`, vendor `disk_info_service`,
`sensorservice`, `mediametrics` and `drmserver`. The "registers a service" hint
was a loose string match (it also matches `joinThreadPool`), so it is weak.

Candidate inspection (`work/asahi/candidate-inspect-noibietj`, reported by the
user, strings and `DT_NEEDED` only, nothing executed):
- `vendor_a:/bin/disk_info_service` (120 KB): NDK Binder (`libbinder_ndk.so`),
  interface descriptor string `com.google.desktop.disk_info.IDiskInfoService`,
  `AServiceManager_registerLazyService` and `ABinderProcess_joinThreadPool`,
  paths `/dev/block/by-name/userdata`, `/sys/block`, `/sys/fs/ext4`; no property or
  FD strings. All its libraries are ones already in the prepared runtime except
  the NDK one. The image also contains a VINTF manifest fragment for it
  (`com.google.desktop.disk_info.xml`, already copied as a fixture).
- `system_a:/system/bin/gsid` (885 KB): C++ `libbinder.so` plus `libcrypto.so`;
  references `ParcelFileDescriptor`, `Parcel::readUniqueFileDescriptor`,
  `setGsiAshmem`, `commitGsiChunkFromAshmem`, ashmem/memfd helpers and
  `/metadata/gsi`, `/data/gsi`, device-mapper paths. This is the only candidate
  whose strings show it reading a file descriptor from a call, but it is
  tied to GSI/dm storage and I do not yet know whether it starts without them.
- `sensorservice`, `mediametrics`, `drmserver` are tiny launchers around large
  libraries (`libsensorservice.so`, `libmediametricsservice.so`,
  `libdrmframeworkcommon.so`); nothing in them shows FD handling.

Reading: Android *receiving* an FD has one plausible candidate (`gsid`), and its
call layout (which transaction code takes the descriptor) is not known yet.
A safer first step is to show that an unmodified image server can register with
the real servicemanager and answer a call at all, using the smallest one.

## Run now: stage 5c, a native endpoint registers through the real `addService`

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as stage 5a/5b below: one new
`work/asahi/real-servicemanager-*` result directory, the temporary
`gbos_binder_probe` SELinux module and one label on the new launcher (restored
and removed by the runner; refuses a preexisting module; SELinux stays
enforcing), and private mounts that end with the namespace. Nothing outside the
new directory is written; no partitioning, formatting, flashing or boot change.

What is new: the real `servicemanager` runs as before. A native endpoint
(unconfined, in the private namespace) calls the real `addService` with the name
`gbos.parcel`, then the image's own `service call gbos.parcel 5658 i32 5658 fd
/proc/self/fd/0` looks it up through the real servicemanager, receives the
returned Binder object, calls it and sends a sealed read-only memfd. The endpoint
verifies the file and replies with the same receipt as the earlier Parcel probe.
The one SELinux addition is `binder transfer` from the servicemanager domain to
`unconfined_t` (the pair denied in the 5b run), because the lookup reply now carries
a Binder handle to the client.

Expected, as a plan and not a result: from my recollection of upstream (not
checked in this image) `addService` consults `service_contexts` for the name
`gbos.parcel`; if the image's file has no matching entry the real servicemanager
may refuse the registration (the runner then stops at `REGISTER_REPLY` with a
non-zero status and the trace shows why). That would be a result to report, not a
defect to hide. The wire layout of my `addService` request (code 5, String16
name, strong Binder with stability word, bool, dump priority) is also from
recollection of upstream and unverified here; a rejection or crash is evidence.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --register-parcel
```

The runner prints `REGISTER_REPLY`, `REGISTER_SERVICE=`, the client output,
`REGISTER_RECEIPT_VERIFIED=` and both trace tails. After it finishes, say **get logs**.

## Completed command: stage 5b, the image's `service list` against the real servicemanager (passed on the third run)

**THIS STEP MODIFIES STORAGE**

Same scope, filesystem, loss and reversal as "Run now: real servicemanager first
run (stage 5a)" below, whose five runs established that setup: one new
`work/asahi/real-servicemanager-*` result directory, the temporary
`gbos_binder_probe` SELinux module and one label on the new launcher (restored
and removed by the runner; refuses a preexisting module; SELinux stays
enforcing), and private mounts that end with the namespace. Nothing outside the
new directory is written; no partitioning, formatting, flashing or boot change.

What is new: after `servicemanager` has had about two seconds to take the
context-manager role, the launcher runs the image's own `service list` (the
same extracted `linker64` and `service` as the earlier Parcel probe) in the same
private root, unconfined as your user, under `strace`. Its output goes to
`android-runtime/list-client.stdout` and its trace to
`list-client.trace`. No SELinux permission is added for the client or the server
beyond the 5a module; if the server's check of the client or any transaction is
denied, the AVC output will name it and I will add only that.

Expected, as a plan and not a result: the client prints `Found N services:`.
From my recollection of upstream (not checked in this image) the real
servicemanager registers itself as `manager`, so N may be 1 rather than 0; both
count. A hang, an error line, or a denial are also results.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager --list-client
```

The runner prints `SERVICE_LIST_FOUND=`, the client output and both trace tails.
After it finishes, say **get logs**.

### Third 5b run: the real servicemanager answered `service list` (milestone)

Reported by the user (`work/asahi/real-servicemanager-fx76zo2f`): `STUB_READY_PROPERTY=CREATED`,
`LIST_CLIENT=exit status 0`, `SERVICEMANAGER_OBSERVED=still running after six
seconds`, `SERVICE_LIST_FOUND=1`, client output `Found 1 services:` / `0 manager: []`.
Cleanup: restore-context 0, policy-remove 0. The client trace shows
`BINDER_WRITE_READ` exchanges and the `writev` of that output; the server trace
shows two reads of "Unknown class service_manager" answered under `deny_unknown`=0.

Facts, by kind:
- Operationally tested: the image's `servicemanager` and the image's `service`
  (both under the extracted Android linker) exchanged Binder transactions on a
  private binderfs device with Fedora SELinux enforcing; the only registered
  service is `manager`, the servicemanager itself.
- Reported but not a pass criterion: three new AVC denials in this run (timestamps
  21:51:13-21:51:15, after the run's start): `{ open }` on `/dev/__properties__`
  (`user_tmp_t` file, twice, domain `gbos_binder_probe_t`, the server) and
  `{ transfer }` on binder from `gbos_binder_probe_t` to `unconfined_t`. They did
  not stop the run. Earlier-listed denials in the runner output are stale
  (older timestamps) and not from this run.
- Limits: the property file is my stand-in for Android's property service, not
  init; access checks are not enforced in any Android sense (unknown class,
  allowed by `deny_unknown`); no service other than `manager` was exercised.

Proposed next stage (not started, needs your go-ahead): a native endpoint
registers itself through the real `addService`, and the earlier Parcel client
looks it up through the real servicemanager, which tests object return against
the real server. The two denials above would be handled in that change only if
they block it; stage 6 (Android receiving a file descriptor) follows separately.

## Run now: real servicemanager first run (stage 5a)

**THIS STEP MODIFIES STORAGE**

Scope: the git update changes source in `~/Gbosm1`. The runner creates one new
`work/asahi/real-servicemanager-*` directory holding copied Android files from
the recovery image (the `servicemanager` binary and its libraries, each under
the existing 64 MiB limit, at most 96 libraries), small fixture files (VINTF
manifests and `*service_contexts`, each under 8 MiB, from `system_a` and
`vendor_a`), a native launcher, generated policy, syscall trace and reports.
The image is only read. Nothing is written outside the new directory except
the temporary SELinux module and one file label described next.

Affected filesystem: the working tree on your Fedora root filesystem, plus the
active SELinux policy store while the module is installed. It temporarily
installs `gbos_binder_probe` and labels only the new launcher executable inside
the new directory; the runner restores that label and removes the module
afterwards, refuses to run if the module already exists, and keeps SELinux
enforcing. Compared with earlier probes, this run's module also lets the
dedicated domain execute the extracted Android files and read the copied
fixtures, `/proc`, the private `/dev`, and the SELinux filesystem access check
(`compute_av`) for its own checks. No other domain gains anything.

Processes and mounts: in new user, mount and PID namespaces the launcher
mounts binderfs, the same private `/dev` overlay as earlier, and builds a
private root (`sm-root`) with bind mounts of `/usr`, `/dev`, the run's runtime
directory and `/sys/fs/selinux`, plus a fresh `/proc`. It then `chroot`s into it
under `strace`, starts `servicemanager` in the dedicated domain, watches it for
six seconds, and stops it. Mounts vanish with the namespace; Fedora's real
`/dev`, `/usr` and boot setup are untouched. No partitioning, formatting,
flashing or boot change.

Possible loss: none of your files; a new result directory and, if cleanup is
interrupted, a leftover module/label.

Reversal: confirm both cleanup codes in the summary are 0. If interrupted, follow
the cleanup instructions under "Completed command: dedicated-domain Binder
echo" (`sudo semodule -r gbos_binder_probe`, after restoring the launcher label
recorded in the summary; in this mode the launcher is `android-runtime/binder-echo`
inside the new result directory). Delete only the new printed result directory to
remove generated files.

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --real-servicemanager
```

What to expect, stated as a plan and not a result: the run prints each
fixture it copied (`FIXTURE ...`), `SERVICEMANAGER_ROOT=READY`, then
`SERVICEMANAGER_OBSERVED=` with either "still running after six seconds" or the
exit status/signal. The runner also prints the `CONTEXT_MGR` lines from the
trace; a `= 0` return from `BINDER_SET_CONTEXT_MGR` would mean it became
context manager. Any of these are valid outcomes: running, a clean refusal
naming a missing piece, a crash, an SELinux denial in the AVC query, or an
unresolved library. A policy-install error would stop before anything runs. I
do not know which; the run decides. Exit 0 means the observation completed, not
that servicemanager works. If it stops on a denial or a missing piece, I will
add exactly that to the next revision instead of widening anything blindly.

Provenance: the launcher and extraction code are original to this project.
Behavior descriptions of `servicemanager` come from AOSP `cmds/servicemanager`
as general background and from this image's own binary; no image file is
modified and no API is intercepted. Not yet built on the target: this code was
only compile-checked on the cloud machine, which cannot run it.

After it finishes, say **get logs**.

## Completed inspection: the image's servicemanager (read-only)

Reported by the user from `work/asahi/servicemanager-inspect-00epe3xu`: ARM64,
16 KiB aligned, 137096 bytes. `DT_NEEDED`: libbase, libvintf, libcutils, liblog,
libselinux, libperfetto_c, libbinder, libutils, libc++, libc, libm, libdl.
Static strings only; they show what it references, not what it does when run.

What the strings show it needs beyond the current private setup:

- VINTF: `VintfObject::GetInstance` and messages such as "Could not find ... in
  the VINTF manifest", so it expects the image's VINTF manifests at their
  usual paths and may refuse or lazily start services otherwise.
- SELinux: `selinux_status_open`, `selinux_check_access`,
  `selinux_android_service_context_handle`, "SELinux denied for service" and
  "SELinux: No match for". Its access checks use the host kernel's policy, and
  Fedora's enforcing policy has no Android service labels, so registration and
  lookup checks are likely to be denied unless handled. Not tested.
- Property: it sets `servicemanager.ready` (existing private property fixture
  only covers the read side; a set needs a property service) and can request
  `ctl.interface_start` for lazy services.
- Other: opens `/dev/binder`, links libperfetto_c, and references
  `/system/bin/bootstrap/linker64`.

What it does not show: any code path that reads the contents of a received FD.
`BBinder::dump` is imported, which suggests the default dump handler receives
an FD in a dump transaction but does nothing observable with it, so a run
against it would not by itself prove Android reading FD data. The real
`servicemanager` would still cover the "real servicemanager" gap, at the cost of
fixtures for VINTF, SELinux behavior and the property service.

Options: stop at the completed Parcel milestone; or plan a multi-step
real-servicemanager attempt with those fixtures, each step needing evidence
first.

## Completed scan: Binder-linked executables in the image

Reported by the user from `work/asahi/binder-clients-mckyu74q` (exit 0, no
skipped entries): 88 executables link `libbinder.so` or `libbinder_ndk.so`
(system_a:/system/bin and vendor_a:/bin, static `DT_NEEDED` only; nothing was
executed). This is a candidate list, not proof of any behavior.

What it shows for the FD question:

- The only small generic Binder clients are `service` (already used), `cmd`,
  `dumpsys` and the vendor `vndservice`/`dumpsys`. By recollection of upstream
  (not checked in this image) `cmd` and `dumpsys` pass FDs outward to a service,
  as `service call ... fd` already did, so they exercise Android *sending* an
  FD, which the Parcel probe covered.
- Android *receiving* an FD needs an Android process that serves a Binder
  object. Candidates present in the image: `servicemanager` and
  `vndservicemanager` (the real context managers, also listed under the
  untested "real servicemanager" item), and heavier services such as
  `mediametrics`, `sensorservice` and `gsid`. None is known to read an FD from
  a plain test call.
- Not established: which of these can start in the private Binder namespace
  with the existing prepared runtime, what extra libraries they need, or
  whether any handles a call that carries an FD without a full Android boot.

Suggested direction (a recommendation, unverified): inspect `servicemanager`
first. It is already an image binary, needs no registration of its own, and
replacing the native stub context manager with it would also cover the
"real servicemanager" gap. A read-only check of its `DT_NEEDED` libraries and
transaction handlers should come before any run.

## Android Parcel probe: markers passed with the 52-byte lookup reply

Reported by the user (filtered output of commit 1605971): `PARCEL_LOOKUP_REPLY=READY: 52-byte
reply ...`, `ANDROID_OBJECT_READ=PASS`, `ANDROID_FD_WRITE=PASS`,
`PARCEL_ENDPOINT_REPLY=PASS` and `ANDROID_PARCEL=PASS`. The runner's own
`android_receipt_verified`, exit statuses, cleanup codes and AVC query were not
in the filtered output, so full success is not yet confirmed (see the check below).

How it got there (provenance): the first lookup reply lacked a present word; adding the
inner present word before the sized `ServiceWithMetadata` (48 bytes,
`work/asahi/android-parcel-probe-3n45nvjg`) still ended with
`Service gbos.parcel does not exist`. Adding the outer present word that the
generated checkService2 client consumes before the `Service` union
(status 0, present 1, tag 0, present 1, size 36, binder at offset 20 plus
stability 12, lazy bool; 52 bytes) passed. The outer-word explanation is an
inference from the failures and the pass, not read from `BpServiceManager::checkService2`.

This establishes: the image's libbinder decoded a returned
Binder object, called it, and serialized an outgoing FD Parcel that the native
endpoint verified. It does not establish Android receiving or reading an FD,
exporting a local Binder object, full lifetimes, death notifications, DMA-BUF,
fences, real servicemanager, HALs, Android boot or graphics.

### Confirmed result

Reported by the user from `work/asahi/android-parcel-probe-edolfdu5`
(read-only check): `exit_status` 0, `android_receipt_verified` True, cleanup
codes 0 and 0, `error` None; the client printed `Result: Parcel( 47504231
00004000)`. Build, device-context, libraries, existing-modules, policy-install,
probe-label, parcel-call, restore-context and policy-remove all exit 0; the
AVC query returned `<no matches>` (exit 1 is grep's no-match). No manual cleanup is needed.

## One command: Android returned object and outgoing FD Parcel

**THIS STEP MODIFIES STORAGE**

Git updates source in `~/Gbosm1`, preserving history and stopping on conflicts.
The runner creates a new `work/asahi/android-parcel-probe-*` directory containing
copied Android runtime files, a native endpoint executable, generated policy,
client output, syscall trace and reports. It reuses the prepared service runtime
and rechecks its ARM64/16 KiB ELF files. The existing extraction limits (64 MiB
per member, 64 libraries; bounded APEX scratch if an extra library is needed)
remain in force. Retain earlier reports and the recovery image.

The launcher creates a sealed 16 KiB memfd after the server fork, reopens it
read-only through its private proc descriptor path, and closes the original
writable descriptor. Only the client child receives the read-only file as
stdin (FD 0), which survives the strace/linker execs. The launcher closes its
copy after the client fork; the server inherits no input descriptor. The image
service CLI opens `/proc/self/fd/0` read-only and serializes that new FD through
Android Parcel. No memfd bind mount or disk input file is needed.

The existing private `/dev` tmpfs still holds the Binder alias and proven
single-file readiness property fixture. Binder/proc mounts, the property file
and anonymous memory file are released on namespace/process exit. Fedora's
host `/dev` is retained. Supplying the client's stdin fixture is an input
prerequisite; it does not demonstrate Android receiving an FD through Binder.

The runner temporarily installs `gbos_binder_probe` and labels only the new
endpoint executable, affecting the active policy/store until cleanup. It uses
the same object-transfer and memfd read/getattr/map rules as the completed native
transfer mode, with the actual observed memfd type. It restores the label and
removes the module afterward, refuses a preexisting module, and keeps SELinux
enforcing. Failed/interrupted cleanup may leave the module/label installed;
reversal instructions remain below. No additional policy permission is added
for this mode beyond the completed native transfer policy.

Run as `gbos_user`; sudo is for the scoped policy/label and audit operations.
This command uses the already installed prerequisites and installs no packages:

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --android-parcel
```

The unmodified image `/system/bin/service` runs through the extracted Android
linker with its local Bionic/system libraries, under client-only strace. It
performs `call gbos.parcel 5658 i32 5658 fd /proc/self/fd/0`. This is a
controlled native endpoint with one private service lookup; it is not Android
servicemanager, service registration or a HAL. The endpoint:

1. Checks the `android.os.IServiceManager` token and the exact `gbos.parcel`
   lookup name. Its checkService2 reply contains a `Service` union, outer and inner present words of 1 and a
   sized `ServiceWithMetadata` with the Binder object, system stability and
   lazy metadata. These are explicit fixtures for the scoped lookup.
2. Returns an interface descriptor on the object. It then verifies that the
   Android client's custom call reaches that object's original pointer/cookie,
   proving the returned object was decoded and used by image libbinder.
3. Checks the interface token, integer challenge and FD object/offset from the
   image's actual Parcel. It verifies the received file's full 16384-byte pattern
   through mmap and pread, size/type, seals, read-only mode and CLOEXEC.
4. Replies with receipt integers `0x47504231` and `16384`. The runner checks the
   Android client's actual printed reply dump for these values, as well as
   native markers, both process exits and cleanup. CLI exit 0 alone is
   insufficient because the service command ignores transact's return value.

Expected evidence: `STUB_READY_PROPERTY=CREATED`, `PARCEL_INPUT=READY`,
`ANDROID_OBJECT_READ=PASS`, `ANDROID_FD_WRITE=PASS`,
`PARCEL_ENDPOINT_REPLY=PASS`, `ANDROID_PARCEL=PASS`,
`android_receipt_verified: true`, execution/summary exit 0, and policy/label
cleanup codes 0. The execution's memfd type must match the generated policy.
The Android output is retained separately in `android-runtime/parcel-client.stdout`
and copied into the report/summary. The full syscall trace remains
`android-runtime/client.trace`; its tail is collected on failure as well.
Unknown transaction codes or layouts stop and retain `PARCEL_TRANSACTION` and
`PARCEL_HEX` rather than guessing additional service-manager behavior.

Wire reference: frameworks/native revision
`4f463a6b1de9198963dc6aff74154a504ba3f8f6`, the IServiceManager/Service/
ServiceWithMetadata AIDLs, Parcel.cpp, service.cpp and Stability.h. The recovery
image is Android 17; its compatibility with this scoped reply layout must be
established by the target run. No image binary is patched or API intercepted.

**Reversal:** confirm both cleanup codes are 0. If interrupted, restore only
the new executable's recorded original context before removing the temporary
module, following the cleanup instructions below. Delete only the new printed
result directory after cleanup to reverse generated files. Git retains source
history. The input descriptor disappears on process exit; the private property
fixture disappears with its namespace.

After it finishes, say **get logs**. I will fetch the report, summary, client
output and trace through the HTTP listing. A pass establishes Android decoding/
calling a returned Binder object and **writing an outgoing FD Parcel**, verified
by the native endpoint. It does not establish Android receiving/reading an FD,
exporting a local Binder object, full lifetimes, death notifications, DMA-BUF,
fences, real servicemanager, HALs, Android boot or graphics.

### First Android Parcel attempt: stopped before client execution

Fetched report, summary, execution stderr and runtime directory listing from
`work/asahi/android-parcel-probe-z0iwsov0` on the Fedora HTTP server.

- Build exit 0 with no diagnostics; Binder protocol 8 and page size 16384.
- Memfd context matched the policy's `user_tmp_t`.
- Execution exit 1: `bind private sealed Parcel input: Invalid argument`.
  No `PARCEL_INPUT=READY`, Android output or client trace was created. The
  attempted anonymous memfd bind mount failed before the Android client fork.
- The old summary then reported a missing `parcel-client.stdout`, obscuring
  the earlier mount error. The runner now records the execution error first
  and collects any available client output/trace during cleanup.
- Executable context restoration and module removal both exited 0. The
  recent AVC query returned `<no matches>`. No manual cleanup is needed.

The corrected launcher supplies the read-only sealed fixture on client stdin
and removes the unsupported input bind mount. The command above pulls this
fix and creates a new result directory. Android object/FD execution and wire
compatibility remain pending; keep this failed run as provenance.

### Second Android Parcel attempt: lookup reply not accepted by the client

Reported by the user from `work/asahi/android-parcel-probe-u9mgoinu` (pasted
report/summary/trace tail; the HTTP listing is not reachable from the cloud
session). Build exit 0; Binder protocol 8, page size 16384; memfd context
`user_tmp_t` matched the policy.

- The stdin input worked: `PARCEL_INPUT=READY` and `STUB_READY_PROPERTY=CREATED`.
  The earlier `Invalid argument` bind-mount failure is resolved.
- The image `service` client ran under the extracted linker, opened the private
  `/dev/binder`, and reached the native endpoint: a `_NTF` interface-descriptor
  request (code 1599098439) and a code 4 request of 100 bytes containing the
  `android.os.IServiceManager` token and `gbos.parcel`. The endpoint accepted
  the layout and submitted its `PARCEL_LOOKUP_REPLY`.
- The client then printed `service: Service gbos.parcel does not exist` and
  exited 10. No further Binder request reached the endpoint, so no FD call was
  made. `ANDROID_OBJECT_READ`, `ANDROID_FD_WRITE`, `PARCEL_ENDPOINT_REPLY` and
  `ANDROID_PARCEL` were not reached; `android_receipt_verified: false`.
- Not established: whether code 4 is the intended checkService2 in this Android 17
  image, or why the client treated the reply as null. The reply layout (status,
  union tag 0, sized ServiceWithMetadata with binder, stability 12, lazy 1) is
  unverified against this image. Observed noise, not yet linked to the failure:
  missing `/linkerconfig/ld.config.txt`, rejected `PR_SET_VMA` names, no logd
  socket, trace_marker EACCES.
- Cleanup: executable context restore and module removal exit 0; recent AVC
  query `<no matches>`. No manual cleanup is needed. Keep this directory as
  provenance.

Repeat run `work/asahi/android-parcel-probe-rxdul6oa` (reported by the user)
reproduced the same result: identical native markers and request hex, client
`Service gbos.parcel does not exist`, exit 10, cleanup codes 0, no AVC matches.
The failure is deterministic, not a one-off.

## Completed native Binder object/FD milestone

User-supplied report and summary:
`/home/gbos_user/Gbosm1/work/asahi/binder-transfer-probe-1iegvhjx`.

| Operation | Result | Scope |
|---|---|---|
| Build | Exit 0, no compiler diagnostics | Native ARM64 probe compiled against the target headers. |
| Execution | Exit 0; Binder protocol 8, pages 16384 | Two native Linux processes used the private Binder device. |
| Received memfd on server | BINDER_FD_SERVER=PASS | Full 16 KiB pattern, mmap/pread, seals, read-only mode and CLOEXEC verified. |
| Returned FD on client | BINDER_FD_CLIENT=PASS | Separately installed descriptor, same device/inode, contents and flags verified. |
| Returned Binder object and call | BINDER_OBJECT_SERVER=PASS and BINDER_OBJECT_CLIENT=PASS | Nonzero translated handle reached the original server object/cookie and returned the verified payload. |
| Completion | BINDER_TRANSFER_SERVER=PASS and BINDER_TRANSFER=PASS; no missing markers | Final acknowledgement and both process exits completed. |
| Memfd type | user_tmp_t in preflight and runtime | Actual runtime type matched the generated scoped policy. |
| Executable label restoration | Exit 0 | Original new-executable context restored. |
| Temporary module removal | Exit 0, last priority removed | Dedicated module removed after execution. |
| Recent AVC query | Exit 1, `<no matches>` | No matching AVCs in the queried window; not a historical audit. |

Overall summary exit_status is 0 and execution stderr is empty. The adjacent
object success lines interleaved in stdout because both processes wrote to the
same report stream; both complete markers are present. This is not a failed or
missing check. No manual policy cleanup or repeat run is needed.

This establishes one native Binder object call and a sealed read-only memfd
round trip under the dedicated SELinux development policy. Android Parcel
object/FD handling, DMA-BUF, fences, death notifications, full object lifetimes,
real servicemanager, HALs and graphics remain untested.

## Completed command: native Binder object and FD transfer

Retained as provenance. The run above passed; do not repeat it for this milestone.

**THIS STEP MODIFIES STORAGE**

Git updates source in `~/Gbosm1`, preserving history and stopping on conflicts.
The runner creates a new `work/asahi/binder-transfer-probe-*` directory with a
native executable, generated policy, report and logs. Existing images and
reports are retained. It creates an anonymous, sealed 16 KiB memory file in RAM;
it does not write a disk image or open a GPU/display device. Namespace/process
exit releases this memory file and the private Binder mount.

As in the completed milestones, sudo temporarily installs the dedicated
`gbos_binder_probe` module and labels only the new executable. This affects the
active policy and policy store until cleanup; interruption may leave that module
or label in place. The runner restores the label and removes the module in its
cleanup path and refuses a preexisting module. SELinux stays enforcing.

For this mode only, the generated policy adds Binder **transfer** from the
dedicated server domain to `unconfined_t`, allowing the returned local object to
become a client handle. It also permits the server to read/getattr/map the actual
client-created memfd type observed before policy installation. Existing inherited
`fd use` and Binder call rules cover the FD and callback. These are type-wide
SELinux rules, not per-file rules; they add no transferred-file write/execute
permission and no DMA-BUF or graphics access. The generated CIL is retained.

Run as `gbos_user`; sudo is used only for the scoped policy/label and audit-log
operations. No package installation is part of this command:

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --object-fd
```

The command uses private user/mount/PID namespaces and a new Binder device.
It runs **two native Linux processes**. This mode does not execute an Android
binary or repeat recovery/runtime extraction. The server registers its context
manager using `BINDER_SET_CONTEXT_MGR_EXT` with explicit FD acceptance. It
performs these checks:

1. The client creates a memfd **after** the server fork, writes a deterministic
   16384-byte pattern, applies write/grow/shrink/seal seals, and sends its FD
   through a Binder transaction. It first reopens the sealed file read-only
   through private `/proc/self/fd` and closes the original writable descriptor;
   a seal alone does not change the FD access mode. The server cannot inherit
   that file.
2. The server verifies the received file size, complete contents via both a
   read-only shared mapping and pread, seals, read-only access mode and
   close-on-exec flag.
3. The server returns that FD together with a local Binder object. The client
   verifies a separately installed FD, matching file device/inode identity and
   contents, and translation of the local object to a nonzero handle.
4. The client retains the strong handle before freeing its reply buffer, calls
   it, and verifies the reply. The server verifies the original object pointer,
   cookie and call payload. Reference acquire/incref notifications are acknowledged.
5. The client releases the handle and sends a final acknowledgement through the
   context manager. Both processes close their resources and exit; the runner
   then restores the executable label and removes the temporary module.

Expected markers: `BINDER_FD_SERVER=PASS`, `BINDER_FD_CLIENT=PASS`,
`BINDER_OBJECT_SERVER=PASS`, `BINDER_OBJECT_CLIENT=PASS`,
`BINDER_TRANSFER_SERVER=PASS` and `BINDER_TRANSFER=PASS`, with execution and
overall exit 0 and both policy/label cleanup codes 0. The runtime memfd type
must match the type used in the generated policy. Unknown layouts, driver
errors, denied operations or timeouts stop with retained reports.

**Reversal:** confirm context restoration and module removal returned 0. If
interrupted, restore only the new executable's recorded original context before
removing `gbos_binder_probe`, using the cleanup instructions retained below.
After cleanup, deleting only the new printed result directory reverses generated
files. Source versions remain recoverable in Git. No partition, boot policy or
existing Binder device is changed by this step.

The report and summary have been received and establish one native Binder
object call and a sealed memfd round trip on the running kernel. Android object/
FD Parcel handling, DMA-BUF allocation/sharing, fences, death notifications,
complete object lifetimes, real servicemanager, HALs and graphics remain later
milestones.

## Completed Android libbinder milestone

Retrieved directly from the target HTTP server:
`/home/gbos_user/Gbosm1/work/asahi/libbinder-probe-xcsf6yfs`.

| Operation | Result | Scope |
|---|---|---|
| Native build and overall execution | Both exit 0; summary exit_status 0 | ARM64 with 16384-byte pages and private Binder protocol 8. |
| Bionic readiness file | Open and 131072-byte MAP_SHARED read mapping succeeded | Image Bionic accepted the private single-property fixture. |
| Android Binder device | `/dev/binder` opened; version, buffer map and BINDER_WRITE_READ succeeded | Unmodified image client used the private device. |
| Metadata request | Code 1599098439 (PING), bytes 0 | Stub handled the client's ping. |
| List request | Code 6, bytes 76, flags 0x10; UTF-16 interface token and priority 15 verified | Android libbinder marshalled the expected listServices request. |
| Android result | `Found 0 services:`; Android exit_group(0) | Client accepted the stub's empty-list reply. |
| Probe success markers | LIBBINDER_STUB_REPLY=PASS and LIBBINDER_LIST=PASS | Client and native server both completed. |
| Executable label restoration and policy removal | Both exit 0; last module priority removed | Temporary policy/label cleanup completed. |
| Recent AVC query | Exit 1, `<no matches>` | No matches in the queried recent window; not a historical audit. |

The readiness wait is resolved for this probe. Missing generated linker
configuration and denied optional trace_marker opens were nonfatal for this
completed exchange. Preserve the reports and current enforcement settings.
This proves Android libbinder IPC against the minimal native empty-list stub.
Full Android init/property service, split property contexts, real servicemanager,
Binder object/FD transfer, HALs, Android boot and graphics remain untested.

## Completed command: libbinder with private stub readiness

Retained as provenance. The run above passed; do not repeat it for this milestone.

**THIS STEP MODIFIES STORAGE**

Git updates source in `~/Gbosm1`, preserving history and stopping on conflicts.
If missing, the command installs Fedora's `strace` package using dnf, affecting
the root filesystem and RPM database. The probe creates a new
`work/asahi/libbinder-probe-*` directory with runtime copies, syscall trace,
report and logs. It reuses the already prepared runtime from the previous
attempt and rechecks copied ELF files; source images and earlier reports remain
retained. Existing per-file/library size limits remain in force.

Inside the existing private tmpfs `/dev`, this revision creates a 128 KiB
read-only binary property area at `/dev/__properties__`. It contains only
`servicemanager.ready=true`, published after the native stub's readiness
handshake. It uses Bionic's supported single-file `ContextsPreSplit` format;
the image's Android property API and client binaries are unchanged. The file
disappears when the private mount namespace exits. It represents readiness of
this minimal stub, not readiness of a full Android service manager or boot.

As before, the runner temporarily installs the dedicated SELinux server module
and labels only the new native stub executable, affecting the policy store and
active policy until cleanup. It restores the original label and removes the
module afterward; manual reversal instructions are retained below. SELinux stays
enforcing and this revision adds no policy permissions.

Run as `gbos_user`; sudo is for the optional strace installation and the
previously scoped policy/label operations:

```bash
cd ~/Gbosm1 && git pull --ff-only && { command -v strace >/dev/null 2>&1 || sudo dnf install -y strace; } && python3 asahi/probe-binder.py --temporary-policy --android-service
```

The corrected runner creates a private **PID namespace** as well as private
user/mount namespaces, with a private proc mount. Exiting the namespace's first
process causes the kernel to terminate its remaining children. Explicit alarm
handlers return 124 (rather than relying on PID 1's default signal behavior).
The outer runner also terminates the isolated process group on every exit path,
including when the launcher exits by itself. It observes the leader's exit
without reaping it before terminating the group, preventing PID-reuse ambiguity.

Before another attempt, the runner inspects earlier failed libbinder reports for
surviving Android service clients. It uses pidfds to terminate only a process
owned by the current user whose executable and full arguments exactly match a
recorded client in that failed report's runtime directory. Unrelated processes
are retained. Prior cleanup identities/results are saved in the new summary.

Only the Android client is traced (`strace -f -tt -T -yy`); the dedicated server
is not traced and gains no ptrace permission. `LD_LIBRARY_PATH` is passed to the
Android tracee with strace's `-E`, so the host tracer uses its native libraries.
The full trace is `android-runtime/client.trace`, and its last 100 lines are
copied into `client-trace-tail.txt` and `report.txt`, including on failure.
The next trace should show Bionic opening/mapping the private property file,
then opening `/dev/binder` and issuing Binder ioctls. A layout incompatibility,
access denial or a further dependency failure still stops with retained logs.
The missing linker configuration warning is not treated as the cause by itself.

The private `/dev/binder` alias, tmpfs /dev overlay and proc mount are visible
only inside the probe's namespaces. No Android system files or device aliases
are installed on Fedora's host `/dev`.

**Reversal:** let normal cleanup finish and confirm both context restoration and
module removal returned 0. If interrupted, restore only the new executable's
recorded original context and remove `gbos_binder_probe` as documented below.
Delete only the new result directory after cleanup to reverse generated files.
If strace was newly installed solely for this probe, removing that package with
`sudo dnf remove strace` reverses its package installation; inspect the removal
transaction before accepting it. Source versions remain recoverable in Git.

The summary, report and full client trace have been retrieved. Their verified
stub reply, Android output, exit status and cleanup results establish the
completed milestone above.

The expected evidence is `STUB_READY_PROPERTY=CREATED`, a verified
`ANDROID_PARCEL` list request, `LIBBINDER_STUB_REPLY=PASS`, Android's
`Found 0 services:`, `LIBBINDER_LIST=PASS`, exit 0 and cleanup return codes 0.
Property-file creation alone is not a pass. Even a pass demonstrates only this
empty-list exchange. Full Android property service, split property contexts,
Binder object/FD transfer, real servicemanager, HALs and graphics remain later
milestones.

Implementation reference: Bionic revision
`731631f300090436d7f5df80d50b6275c8c60a93`, `SystemProperties::InitContexts`,
`prop_area::map_fd_ro`, `prop_trie_node` and `prop_info`. The area has magic
`0x504f5250`, version `0xfc6ed0ab`, a 128-byte header, relative trie offsets,
and a clean value serial. Its owner/group are verified as root inside the user
namespace and its final mode is 0444. The completed target run confirms that
the extracted Android 17 Bionic accepts this fixture for the scoped exchange.

## Retrieved traced libbinder attempt: readiness wait

Results: `/home/gbos_user/Gbosm1/work/asahi/libbinder-probe-yvdq9dbs`.

| Operation | Result | Scope |
|---|---|---|
| Runtime reuse and native build | 14 ARM64/16 KiB ELF files; build exit 0 | Prepared dependencies reused and inspected. |
| Previous failed client cleanup | PID 3536 terminated by pidfd after exact identity checks | Removed the saved failed attempt's surviving Android client. |
| Client execution | Exit 124; launcher/server alarms recorded | No list request or reply established. |
| Client trace | `/dev/__properties__` stat/open returned ENOENT; repeated `Waited for servicemanager.ready...` | Android reached the service-manager readiness gate. No `/dev/binder` open appears in the captured trace. |
| Executable label restoration | Exit 0 | Original new-stub context restored. |
| Temporary policy removal | Exit 0 | Scoped module removed. |

The upstream `BackendUnifiedServiceManager::getBackendUnifiedServiceManager`
waits for `servicemanager.ready=true` before obtaining the Binder context
object. In this isolated probe no Android init/property service supplies that
property. The new fixture supplies the one readiness fact after the stub has
registered its context manager and entered its looper. It does not fabricate
service-list output or a Binder reply; those still have to come from the client
and the verified native transaction path.

## Earlier untraced libbinder attempt: incomplete

Results: `/home/gbos_user/Gbosm1/work/asahi/libbinder-probe-gx0g75qh`.

| Operation | Result | Scope |
|---|---|---|
| Android client/runtime extraction | 14 inspected ARM64/16 KiB ELF files, including libbinder and runtime libdl_android | Candidate dependencies prepared; no missing ELF reported. |
| Native stub build and device context collection | Exit 0; device/root type binderfs_t | Existing Binder setup succeeded. |
| Temporary policy installation and executable labeling | Both exit 0 | Dedicated server policy installed for this attempt. |
| Android client/stub execution | Return -14 (SIGALRM); no `ANDROID_PARCEL` request or success marker logged | Launcher reached its alarm; client completion and parcel exchange not established. |
| Executable context restoration | Exit 0 | New native stub label restored. |
| Temporary policy removal | Exit 0 | Removal log confirms no other gbos_binder_probe priority remained. |
| Recent AVC search | Exit 1, `<no matches>` | No matching AVCs in that queried window. |

The private /dev overlay was established and the Android linker started.
Its missing generated linker-configuration warning was already nonfatal for
Toybox; this output does not identify it as the cause of this wait. The stub
readiness handshake preceded launching the client, but no request was logged.
The subsequent traced run above identified the readiness-property wait.

The earlier runner killed a process group on a Python timeout, but a launcher
that exits from its own alarm can leave descendants running. Policy/label
cleanup succeeded; this report does not prove every descendant exited or every
private mount namespace was released. The current runner corrected this defect;
the traced run above removed the recorded surviving client and retained its
cleanup identity in the summary.

## Previous command: Android libbinder list request

Retained as provenance; the corrected probe has now passed as recorded above.

**THIS STEP MODIFIES STORAGE**

Git updates tracked source in `~/Gbosm1`, preserving history and stopping on
conflicts. The runner creates a new `work/asahi/libbinder-probe-*` directory
containing copied Android runtime files, new library candidates, a native stub,
policy, report and logs. It reuses the completed Bionic runtime, extracts only
needed files from the retained recovery image and validates ARM64/16 KiB ELF
alignment. Existing results and the source image are retained. Copies are
bounded to 64 MiB each and 64 library entries. If an additional Bionic APEX
library is needed, temporary package/payload copies are bounded to 512 MiB each
and removed automatically. Allow several GiB of capacity for the new result.

The previously demonstrated dedicated server policy is installed temporarily;
only the new native stub executable is specially labeled. Fedora's active
policy/store under `/var/lib/selinux` is affected until cleanup. Cleanup restores
the executable label and removes the new module. The runner refuses an existing
`gbos_binder_probe` module. Failed/interrupted cleanup may leave that module or
label in place; reversal instructions remain in the completed policy section
below. Generated files can be reversed by deleting only the new printed result
directory after label/module cleanup. Source history remains recoverable in Git.

Inside a private user/mount namespace, the native launcher mounts binderfs and
a small temporary tmpfs over `/dev`. It exposes only its new Binder device at
that namespace's `/dev/binder`, plus the original null device at `/dev/null`.
The host's `/dev` mount and device nodes are retained. All probe mounts disappear
on namespace exit. This alias lets the image's unmodified libbinder client use
its expected Binder path without installing Android files on the host.

Run as `gbos_user`; sudo is used only for temporary policy/label operations and
reading AVC logs, as in the completed echo milestone:

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy --android-service
```

The Android binary is `/system/bin/service` from the image, invoked through the
copied Android linker with local Bionic/system libraries and only the `list`
argument. The native server runs in `gbos_binder_probe_t`; the client remains in
its existing domain. Enforcement stays enabled. No Android init, full Android
servicemanager, service registration, framework, HAL or graphics service starts.

The stub supports only ping/interface metadata and one empty-list reply. It
verifies the UTF-16 `android.os.IServiceManager` token, transaction code 6 and
priority flags 15 from the pinned AOSP reference. Unknown wire layouts stop with
the received code/size in the report; the Android 17 image is not assumed to
match every Android 16 interface detail. Success requires the Android client to
print `Found 0 services:`, the stub to verify and reply, and both processes to
exit 0 (`LIBBINDER_LIST=PASS`). This establishes only the observed client/stub
parcel request/reply path. It is not a real service directory or evidence that
Android servicemanager, object/FD transfer, VINTF or HALs work.

Native launcher/stub and extraction code are original project source. Protocol
references inspected (AOSP Apache-2.0 sources, tag `android-16.0.0_r1`):

- [IServiceManager.aidl](https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-16.0.0_r1/libs/binder/aidl/android/os/IServiceManager.aidl)
- [service.cpp](https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-16.0.0_r1/cmds/service/service.cpp)

After completion, say **get logs**. Keep the HTTP server available at
`http://192.168.178.47:8000/work/asahi/`; I will locate and retrieve the new
`libbinder-probe-*` report and cleanup status directly.

## Completed Binder echo and cleanup

Retrieved directly from `work/asahi/binder-probe-xwhs2s_3` on the target HTTP server.

| Operation | Result | Scope |
|---|---|---|
| Native build and context discovery | Exit 0; device/root label `binderfs_t` | Native Linux probe with target UAPI headers. |
| Dedicated policy installation and executable labeling | Both exit 0 | Temporary module and newly built executable only. |
| Binder transaction/reply | `BINDER_ECHO=PASS`; exit 0; protocol 8, pages 16384 | Server verified request; client verified reply between two native Linux processes. |
| Executable context restoration | Exit 0 | Original `unconfined_u:object_r:user_home_t:s0` restored. |
| Temporary module removal | Exit 0 | Removal log confirms no `gbos_binder_probe` module at another priority. |
| Recent AVC search | Exit 1, `<no matches>` | No matching AVCs in the queried recent window; not an exhaustive historical audit. |

This establishes native Binder transaction/reply operation under the dedicated
development policy. Android libbinder/servicemanager, Binder object/FD transfer,
HAL execution and graphics integration remain untested. Retain the result
directory; no manual policy cleanup is needed for this completed run.

## Completed command: dedicated-domain Binder echo

The command below is retained as provenance; it does not need to be rerun.

**THIS STEP MODIFIES STORAGE**

Git updates source in `~/Gbosm1`, preserving history and stopping on conflicts.
The runner creates a new `work/asahi/binder-probe-*` directory and a shared
`work/asahi/binder-policy.lock`. It builds a native probe, reads the actual
private binderfs device/root labels and native library/directory/log labels,
and saves the generated policy as `gbos_binder_probe.cil` for review.

Using sudo, it temporarily installs module `gbos_binder_probe` into Fedora's
SELinux policy store under `/var/lib/selinux`, changes only the new executable's
SELinux label, and later restores that label and removes the module. The policy
store and active policy are affected until cleanup completes. A failed/interrupted
cleanup may leave that module installed or the new executable specially labeled.
Existing modules are retained; the runner stops if this module name already
exists. Keep the terminal open until the cleanup result is printed.

Run as `gbos_user` (sudo may request your password for the policy operations):

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-binder.py --temporary-policy
```

Only the echo server explicitly transitions via `runcon` into
`gbos_binder_probe_t`. There is no automatic transition rule, permissive domain,
unconfined attribute, network permission or mount capability in that domain.
Context-manager registration is allowed for that domain itself; Binder calls
are allowed only between it and the observed `unconfined_t` client domain.
The client stays in its existing domain. Binder devices are opened after the
server transition, so their driver mappings belong to the correct address space.

The server's device/library/path rules use actual target **types**, not individual
path isolation: any object bearing one of those types has the same SELinux
permission. Entry into the domain uses the separately labeled probe executable.
Runtime libraries/cache are readable/mappable; inherited report files are writable;
no report file create/open-write rule is added. The generated CIL and module
install logs are retained. This is a focused development policy, not an Android
service policy. No packages are installed and no policy is generated from a
blanket audit2allow invocation.

The binderfs mount remains inside private user/mount namespaces and disappears
when they exit. The server/client run without host root privileges. The runner
checks enforcement before execution; enforcement is never disabled. It records
the echo outcome, cleanup return codes and recent AVCs when `ausearch` is present.
If tools, policy compilation, transition or IPC fail, retain the report instead
of widening policy permissions manually.

**Reversal:** normal cleanup restores the new executable's original context and
removes the temporary module. If cleanup reports failure, use the new result
directory's saved `restore-context` command/log to restore its recorded original
context; then remove only this module:

```bash
sudo semodule -r gbos_binder_probe
```

If interrupted before a restore-context log exists, restore the context recorded
for the executable in `summary.json` (when available), or run `sudo restorecon -v`
on only that result directory's `binder-echo` before module removal. Delete only
the newly printed result directory after restoring its label to reverse report
and binary writes. Source versions remain recoverable through Git. The lock file
may remain and contains no policy; kernel flock state is released at process exit.

Keep the HTTP server available. After the command finishes, say “get logs” and
I will retrieve the latest result directory from
`http://192.168.178.47:8000/work/asahi/`.
`BINDER_ECHO=PASS` plus successful cleanup would establish native Binder
transaction/reply operation only; Android libbinder and HAL integration remain
untested.

## Confirmed SELinux denial

Retrieved `work/asahi/binder-denial-Oo9G7R.log` directly through the target HTTP
listing. The audit event at 19:31:56 on 10 October 2026 reports:

```text
avc: denied { set_context_mgr } comm=binder-echo
scontext=unconfined_u:unconfined_r:unconfined_t:s0-s0:c0.c1023
tcontext=unconfined_u:unconfined_r:unconfined_t:s0-s0:c0.c1023
tclass=binder permissive=0
```

This confirms SELinux denied Binder context-manager registration for this run.
The ordinary user's reported process domain was also `unconfined_t`.
It does not establish whether additional Binder operations would be allowed
once registration is addressed. No policy or enforcement change was made.

## Completed command: Binder denial diagnostics

The diagnostic log has been received; do not rerun this collection now.

**THIS STEP MODIFIES STORAGE**

Git updates tracked source files in `~/Gbosm1`, preserving history and stopping
on conflicts. The command creates only one new `work/asahi/binder-denial-*.log`
on the home filesystem. Existing reports are retained. Possible loss is disk
space consumed by the new log and source versions updated by Git. Delete only
the printed new log to reverse generated writes; source history remains in Git.

Run as `gbos_user`. The `sudo` invocation below only reads existing audit/kernel
logs; it does not rerun the probe with privilege or modify policy.

```bash
cd ~/Gbosm1 && git pull --ff-only && report=$(mktemp work/asahi/binder-denial-XXXXXX.log) && { date -Is; id; id -Z; if command -v ausearch >/dev/null 2>&1; then sudo ausearch -m AVC,USER_AVC -ts today -i; else sudo journalctl -k -b --no-pager; fi; } 2>&1 | tee "$report"; printf 'Diagnostic log: %s\n' "$report"
```

Keep the HTTP server available at `http://192.168.178.47:8000/work/asahi/`
and send the printed log name; the reports can be retrieved directly from its
directory listing. Audit logs may contain unrelated denial events; the relevant
evidence is the Binder context-manager denial and its source/target security
contexts. If no matching denial is present, retain the output: absence of a
record does not establish that SELinux allowed the operation.

Do not disable enforcement, change device permissions, install a broad allow
rule or rerun the Binder probe with sudo from this error alone. Any policy
development should follow the recorded denial and remain narrowly scoped.

## Completed Binder attempt

Retrieved directly from the target HTTP directory listing:
`/home/gbos_user/Gbosm1/work/asahi/binder-probe-tdbr2cym`.

- Native C build: exit 0, no compiler diagnostics.
- Private namespace/binderfs and device creation: succeeded; device major 508,
  minor 4 for this run.
- Binder protocol: 8; runtime page size: 16384; the server's driver buffer mapping
  succeeded before context-manager registration.
- `BINDER_SET_CONTEXT_MGR`: permission denied. Server stopped before readiness;
  probe exit 1. Client transactions and replies were not attempted.
- This is a permission failure at the context-manager boundary, not evidence
  that Binder is absent. The exact enforcement mechanism remains unconfirmed.

## Completed Bionic execution

Retain `/home/gbos_user/Gbosm1/work/asahi/bionic-probe-lf6jtryp`.
Twelve extracted ELF files met ARM64/16 KiB alignment. Android Toybox `uname -a`
ran through the recovery image's linker with the extracted Bionic/system
libraries and returned exit 0 on kernel `7.1.6-400.asahi.fc44.aarch64+16k`.
The missing `/linkerconfig/ld.config.txt` warning was nonfatal for this command.
The local library search path supported this invocation; complete Android
linker namespace configuration has not been established. No host linkerconfig
files should be created from this warning alone. The completed probe does not
need to be rerun.

## Completed image audits

Retain these results; the next command does not repeat the completed scans:

- Recovery: `/home/gbos_user/Gbosm1/work/asahi/recovery-audit-frw8c3gt`;
  15 ELF samples and 81 VINTF XML files, exit 0. Missing candidate properties in
  `system_b` were the only reported unresolved finding.
- Graphics/runtime: `/home/gbos_user/Gbosm1/work/asahi/graphics-audit-mvyr702a`;
  34 ELF samples, exit 0 and zero reported unresolved findings. Runtime linker64,
  libc, libm and libdl are ARM64/16 KiB aligned. These are static results.
- `system_b` contains `preloads`, `product`, `system`, `system_ext` and
  `system-other-odex-marker`. This suggests auxiliary precompiled content;
  its boot/slot role has not been established.
- The graphics init collector omitted `hwc3-drm.rc` because its name did not
  match the filter. Zero unresolved findings is not exhaustive init coverage.
- Qualcomm `libgralloccore` depends on `libion`, `libdmabufheap` and `libvmmem`;
  `libsdmcore` depends on `libsdedrm` and `libsdmdal`. Generic DRM naming does not
  demonstrate compatibility with Apple DRM. The allocator, mapper, composer
  and Adreno Vulkan path need replacement/adaptation for Asahi.

## Evidence already received

User-supplied Fedora output on 10 October 2026 establishes:

| Observation | Result | Scope |
|---|---|---|
| Running kernel | `7.1.6-400.asahi.fc44.aarch64+16k` | Collector output; installed kernel headers are a separate package. |
| Runtime page size | 16384 bytes | Confirmed by collector and GPU probe. |
| Binder IPC / binderfs | Native echo, Android libbinder empty-list exchange, and native object/FD round trip passed with protocol 8 on 16 KiB pages | Dedicated SELinux server domain; verified request/reply, object call and sealed memfd transfer; temporary module removed and executable label restored. Android Parcel object/FD handling and real servicemanager remain untested. |
| DMA-BUF / DMA heaps | Configured `y`; system heap node present | Allocation and sharing remain untested. |
| GPU kernel driver | Asahi module loaded; `renderD128` and `card2` use Asahi | Collector evidence. |
| Internal display | `apple-drm`, connected eDP, 2560×1600 mode | Enumeration only; Android composition remains untested. |
| SELinux | Enforcing (`1`) | Retain enforcement. |
| Namespaces / cgroups / IOMMU | Enabled in config; cgroup2 mounted; apple_dart loaded | Android workload compatibility remains untested. |
| Native probe build | Exit status 0 after prerequisites installed | Earlier missing-compiler failure resolved. |
| Vulkan driver | Honeykrisp, Mesa 26.2.4, Apple M1 (G13G B1) | Missing ICD failure resolved by installing `mesa-vulkan-drivers`; Honeykrisp selected over llvmpipe. |
| GPU submission/readback | `VM_VULKAN readback=PASS`; readback and capabilities exit 0 | Buffer fill, queue submission, fence completion and content verification succeeded. |
| DRM primary node warning | Opening `/dev/dri/card2` returned permission denied | Non-fatal for tested Honeykrisp readback; display use remains untested. Do not change permissions from this warning alone. |
| Recovery raw image | 19,230,966,272 bytes; pinned SHA-256 verified | `artifacts/mica-recovery.raw`; source is Google mica-user build 16471258. |
| Home filesystem | 219 GB available when checked | Recovery download/extraction capacity check; current usage will have increased. |
| GPT and LP metadata | GPT CRC and LP tables SHA-256 passed | `super` is 12 GiB; logical extents enumerated. |
| Userspace filesystems | system_a, system_b, vendor_a, product_a, system_ext_a are EROFS | Filesystem signatures read directly from image. |
| Android properties | A-side system/vendor/product/system_ext report Android 17, SDK 37/37.1, build 16471258 | System/vendor security patch 2026-09-01; vendor board API 202604 and first API level 37. Image facts, not runtime boot evidence. |
| system_b | EROFS with system-other-odex-marker and auxiliary directory roots; no candidate build properties | Suggests precompiled auxiliary content; boot/slot role remains unverified. |
| ELF samples | 15 core/vendor graphics binaries are ARM64 and satisfy 16 KiB PT_LOAD alignment | Static sample only; runtime APEX and complete userspace compatibility remain unverified. |
| Runtime follow-up | 30 vendor dependency ELF files and 4 runtime APEX ELF files meet ARM64/16 KiB alignment; exit 0, zero reported unresolved findings | Static collection only. |
| Bionic execution | Android linker + Toybox uname ran successfully; 12 extracted ELF files meet ARM64/16 KiB alignment | Operational userspace execution for this applet only; missing generated linker configuration was nonfatal. |
| VINTF | 81 XML files collected; vendor target-level 202604 | Enumeration only; full VINTF compatibility and runtime operation untested. |
| Core graphics declarations | Allocator AIDL v2, composer3 AIDL v5, native mapper 5.0/qti | Versions fall within framework matrix 202604 ranges (allocator 1–3, composer3 4–5, mapper 5.0); no full compatibility pass. |
| Vendor graphics dependencies | Adreno Vulkan; QTI allocator/mapper; DRM-named composer requires libsdmcore, libgralloc.qti and QTI snapalloc | Hardware-specific dependency evidence. These paths need replacement/adaptation for Asahi; 16 KiB alignment does not establish driver compatibility. |

Vulkan validation results were saved in
`/home/gbos_user/Gbosm1/work/asahi/results-7EXxM0au`.

The recovery ZIP and raw image were verified against `lib.sh` pins:

- ZIP: `cb68dd6dbd73e568cfeecca0cbd4ceb23338640814dc3ee52aba5507e5a645d7`
- Raw: `b0fd614ffe1a088fa3451a4b81c55a73f83e246286184a46ca306250db9af1f1`

## Graphics: stages 0, 1, 2 and 3a complete; stage 3b is next

Plan: [Asahi graphics stack plan](https://claude.ai/code/artifact/52353ab5-12b6-4f59-91e2-a80f80e1eaf8)
(Honeykrisp built for Android, the image's ANGLE for GLES, a GBM-backed minigbm
allocator/mapper, AOSP drm_hwcomposer on `apple-drm`). This is a plan, not a
result; nothing in it has been built. Independent of the Binder steps above.

### Result: display planes, formats and fences (step 2)

Reported by the user from `drm_info` 2.10.0 run as the ordinary user
(enumerated; no mode set and nothing committed). `drm_info` was installed with
dnf for this step.

- Display node: `/dev/dri/card3`, driver `apple` (Apple display controller)
  1.0.0, platform `apple,display-subsystem`, primary node only. GPU node:
  `/dev/dri/card1`, driver `asahi` (Apple AGX, `apple,agx-t8103`), primary and
  render nodes. `/dev/dri/card2` was permission denied and is unidentified.
  The earlier baseline recorded Asahi on `card2`, so card numbering is not
  stable across boots; scripts must select nodes by driver name, not number.
- Display caps: atomic and universal planes supported, `DRM_CAP_PRIME=3`
  (import and export), `ADDFB2_MODIFIERS=1`, dumb buffers, syncobj and
  timeline syncobj; no async page flip.
- One eDP connector, connected, 286x179 mm, one mode 2560x1600@60. One CRTC
  with `OUT_FENCE_PTR`, `CTM` and `VRR_ENABLED`; gamma size 0.
- Primary plane (zpos 0): `IN_FENCE_FD`; `IN_FORMATS` lists only
  `DRM_FORMAT_MOD_LINEAR`, with XRGB/ARGB/XBGR/ABGR8888, XRGB/ARGB2101010,
  NV12, NV16, NV24, P010 and P210. GNOME currently scans out a linear
  XRGB8888 2560x1600 framebuffer, pitch 10240.
- One overlay plane (zpos 1): `IN_FENCE_FD`, linear only, alpha and YUV
  formats only (no XRGB8888). No cursor plane.

Effect on the plan: the display gate passes. Scanout buffers must be linear,
so the allocator must return linear layouts for composer and scanout usage
while GPU-only buffers may keep Asahi's tiled modifiers. In and out fences exist
for drm_hwcomposer, and a second (overlay) plane is available later. Nothing has
been committed to the display; Stage 5 still needs DRM master, which GNOME
holds.

### Next: Graphics stage 3b, linear buffers without Mesa's GBM

Stage 3a (below) found no Asahi or Apple support in minigbm, but it does have a
plain "dumb buffer" backend selected by DRM driver name. Whether a dumb buffer
from the display driver (`apple`), or a buffer from a dma-heap, can be used by
Honeykrisp is untested. This step allocates 256x256 ARGB8888 linear buffers
those ways, imports each into Honeykrisp as a linear image, and checks both
directions: CPU write then GPU read, and GPU clear then CPU read through an
mmap of the dma-buf. It also prints the node permissions and the two minigbm
source excerpts still needed (read from the stage 3a clone, no network).

**THIS STEP MODIFIES STORAGE**

Creates one new `work/asahi/linear-dmabuf-*` directory with the probe binary
and `report.txt`. Allocates a few 256x256 buffers for the run only (dumb
buffers on the `apple` and `asahi` card nodes, one per readable dma-heap).
No modeset and no DRM master are used, so GNOME is unaffected. Delete the
directory to reverse. It uses the same `vulkan-loader-devel` and
`libdrm-devel` packages as stage 1.

```bash
cd ~/Gbosm1 && git pull --ff-only
bash asahi/run-linear-dmabuf.sh
```

Send the printed `report.txt`. Each case reports its own step (`dumb_create`,
`prime_export`, `vulkan_import`, `cpu_to_gpu`, `gpu_to_cpu`); a refusal such
as a permission error on a node is a result, not something to work around.
The probe compiled here but has not run; Honeykrisp is only on your machine.

### Result: Graphics stage 3a, minigbm source (read, not built)

Reported by the user from `work/asahi/minigbm-src-pinRnTy0`; these are source
facts, nothing was built or run:

- Sources: AOSP `external/minigbm` at `455b745` (2025-03-26) and ChromiumOS
  `minigbm` at `a2d42f0` (2026-10-01). Both carry a BSD `LICENSE`, to be kept
  with anything derived from them. The AOSP copy lacks `dma_heap.c`, which
  upstream has.
- Neither tree mentions `asahi` or `apple` anywhere.
- `drv_get_backend()` picks a backend by exact `drmGetVersion()` name against
  a fixed list (amdgpu, i915, msm, vc4, xe behind `DRV_*` flags, plus virtgpu,
  mediatek, rockchip, nouveau, radeon, vkms, and others). No match makes
  `drv_create()` fail, so an unmodified minigbm would not allocate on this
  machine.
- `dumb_driver.c` defines a trivial backend with one line per driver
  (`INIT_DUMB_DRIVER(name)`); it uses DRM dumb buffers, so it gives linear
  CPU-mappable buffers and no GPU layouts.
- `dri.c` is the other route: it `dlopen`s a Mesa DRI driver and uses
  `__DRI_IMAGE`. Using it for Asahi would need Mesa's gallium `asahi` driver
  built for Android, which stages 2b-2d did not build (they built Honeykrisp
  only). Inferred, not checked: stage 1's GBM allocations on Fedora likely went
  through that gallium driver for the same reason.
- `cros_gralloc_driver.cc:129-130` enumerates `renderD%d` and `card%d` nodes;
  the excerpt is printed in stage 3b because the allocation order matters.
- AOSP builds per-vendor flag sets (intel, meson, msm, arcvm), the AIDL
  allocator `android.hardware.graphics.allocator-service.minigbm`, the
  stable-C mapper `mapper.minigbm`, and `libminigbm_gralloc`: the same
  components the VM port used.

What this leaves open (decided only after stage 3b):

1. Add `asahi` and/or `apple` as dumb-style backends, if stage 3b shows those
   linear buffers work with Honeykrisp. That is a small patch but gives linear
   only; tiled and compressed buffers (which stage 1b showed work through
   GBM) would stay unused.
2. Write a small allocator on Honeykrisp's own Vulkan allocation with
   dma-buf export, which keeps tiled layouts and avoids building gallium.
3. Build gallium `asahi` for Android and use minigbm's DRI path or GBM.

### Result: Graphics stage 1b, Apple tiled and compressed layouts (passed)

Operationally tested, reported by the user from
`work/asahi/gbm-dmabuf-0tvfywxX`: all four cases passed (`linear`,
`driver-layout`, `apple-tiled`, `apple-compressed`). GBM allocated exactly the
requested modifier each time (`0x0c00000000000001` and `0x0c00000000000002`),
Honeykrisp imported it from the dma-buf with that modifier, and both the
Vulkan readback and GBM's CPU map read 65536/65536 pixels `0xff204080`. The
compressed image is 264192 bytes, 2048 more than the others. GBM reported
stride 1024 for every layout, and the probe passed that as the plane pitch.
GBM's default allocation (no modifier requested) is still linear.

So Mesa's GBM on the Asahi render node can allocate linear, tiled and
compressed buffers that round-trip with Honeykrisp in one process. Sharing
between processes, Android and the display remain untested.

### Result: Graphics stage 1, GBM to Vulkan dma-buf round trip (passed)

Operationally tested, reported by the user from
`work/asahi/gbm-dmabuf-QXvMvf3T` (`bash asahi/run-gbm-dmabuf.sh`, the dnf
packages were already present):

- Nodes on this boot: `card1` and `renderD128` are `asahi`, `card3` is
  `apple`, and `card2` could not be opened by the ordinary user (errno 13,
  driver unknown). Mesa also printed `Opening /dev/dri/card2 failed:
  Permission denied`, which did not affect the run.
- GBM backend `drm` on `renderD128`; Vulkan device `Apple M1 (G13G B1)`,
  Honeykrisp Mesa 26.2.4.
- Honeykrisp lists three modifiers for B8G8R8A8, all importable from dma-buf
  with features `0xdd83`: `0x0c00000000000002`, `0x0c00000000000001` and
  linear `0x0`.
- Linear case: GBM allocated modifier `0x0`, stride 1024. The dma-buf import
  into Vulkan, the GPU clear, the Vulkan readback and the GBM CPU map readback
  all passed (65536/65536 pixels `0xff204080`).
- Default-layout case: GBM also chose linear (`0x0`) without being asked, and
  passed identically. Apple's tiled and compressed layouts were therefore not
  exercised (stage 1b).

This supports plan decision 1 (allocate through Mesa's GBM) for linear,
scanout-compatible buffers. It does not test Android, Bionic, scanout or
fences between processes.

### Result: Graphics stage 2d, Android Honeykrisp under Bionic (passed)

Operationally tested, reported by the user from
`work/asahi/honeykrisp-bionic-a9oqkl_m` (command:
`python3 asahi/probe-honeykrisp-bionic.py`):

- The Android probe and every library in the closure are ARM64 with 16 KiB
  load alignment. The closure came from `system_a:/system/lib64` (libhardware,
  libnativewindow, libui, libbinder, libhidlbase, the graphics common,
  allocator and mapper HIDL/AIDL interface libraries, and others), plus
  `libdl_android.so` read from the runtime APEX through its symlink. The
  stage 2c build supplied libdrm 2.4.133.
- Under the image's `linker64` with `page_size=16384`: `dlopen` passed, ICD
  interface version 5 was negotiated, and `vkEnumeratePhysicalDevices` found
  one device, `Apple M1 (G13G B1)`, API 1.4.354, integrated GPU. Device
  creation, a GPU buffer fill, fence wait and readback all passed
  (`readback=PASS words=1024/1024 first=0x47424f53`), exit status 0, giving
  `HONEYKRISP_BIONIC=PASS`.
- The linker warned that `/linkerconfig/ld.config.txt` is missing. This is
  expected outside Android and did not stop the run.

Limits: the probe dlopened the driver directly, so Android's `libvulkan`, HAL
loading, properties, AHardwareBuffer, images, ANGLE and display were not
exercised. The first run stopped earlier on the `libdl_android.so` symlink
(`work/asahi/honeykrisp-bionic-4mpimr0k`); the runner was fixed in 17b14cc.

### Result: Graphics stage 2c Android Honeykrisp build

Reported by the user from `work/asahi/honeykrisp-android-b7O7HstT` (built and
inspected; not loaded or run):

- Fedora's `mesa-26.2.4.tar.xz` sha256 matches the upstream pin in `lib.sh`
  (`bce5f7fb...e9`).
- `android-ndk-r28c-linux.zip` downloaded, sha256
  `dfb20d396df28ca02a8c708314b814a4d961dc9074f9a161932746f815aa552f`
  (not yet compared with Google's published checksum); 4604 sysroot and
  runtime files extracted.
- Fedora clang 22.1.8 with the NDK sysroot and combined resource directory
  configured and built (625 steps, exit 0, no compiler warnings in the
  output). Meson used the stage 2b `mesa_clc`, `vtn_bindgen2` and `asahi_clc`;
  libdrm 2.4.133 was built as a fallback subproject.
- `vulkan.asahi.so`: ELF64 AArch64, 13.6 MB stripped, all four PT_LOAD
  segments 16 KiB aligned. `DT_NEEDED`: libdrm, libhardware, liblog,
  libnativewindow, libsync, libz, libm, libdl, libc. Exports `HMI` (the Android
  HAL module symbol) and `vk_icdGetInstanceProcAddr`; contains
  `VK_ANDROID_native_buffer`, `VK_ANDROID_external_memory_android_hardware_buffer`
  and `VK_ANDROID_external_format_resolve`.

### Result: Graphics stage 2b host compilers

Reported by the user from `work/asahi/mesa-host-to82QUme`: configure and build
exit 0 (612 steps) with gcc 16.2.1, LLVM 22.1.8, LLVMSPIRVLib 22.1.0 and
SPIRV-Tools 2026.1.1. Installed into its `prefix/bin`: `mesa_clc`,
`asahi_clc` (the Asahi precompiled-shader compiler) and `vtn_bindgen2`. A native
`libvulkan_asahi.so` and `asahi_icd.aarch64.json` were also built under
`prefix`; they are not installed or used by the system. Build packages were
installed with dnf (46 new, expat 2.8.3 to 2.8.5).

### Result: Graphics stage 2a toolchain check

Reported by the user from `work/asahi/mesa-toolchain-mWVvIK2W` (nothing
installed or built):

- Running driver `mesa-vulkan-drivers-26.2.4-1.fc44`; kernel
  `7.1.6-400.asahi.fc44.aarch64+16k`. Fedora's matching source RPM
  `mesa-26.2.4-1.fc44.src.rpm` downloaded and unpacked (`mesa-26.2.4.tar.xz`
  plus Rust crates for other drivers).
- Fedora builds mesa with `-Dllvm=enabled -Dshared-llvm=enabled`,
  BuildRequires `pkgconfig(libclc)` and sets `-Dandroid-libbacktrace=disabled`.
- `src/asahi/meson.build` includes `subdir('clc')`, so the Asahi driver needs
  the OpenCL C compiler path. The source has the options `mesa-clc`,
  `install-mesa-clc`, `precomp-compiler`, `install-precomp-compiler`,
  `android-stub`, `android-libbacktrace` and `platform-sdk-version`.
- Installed: gcc 16.2.1, python3-pyyaml, -packaging, -pycparser. Missing but
  available: clang/LLVM 22.1.8 (devel), lld, libclc-devel 22.1.8,
  spirv-llvm-translator-devel 22.1.2, spirv-tools-devel 2026.1, glslang,
  meson 1.11.2, python3-mako.
- No Android NDK on the machine. Google ships NDK host tools only for x86_64
  Linux and macOS, so the cross build will use Fedora's clang with the NDK's
  target sysroot (inference; stage 2c will test it).

### Result: Honeykrisp extensions (step 1)

Reported by the user from the saved `work/asahi/results-7EXxM0au/capabilities.log`
(enumerated, not exercised): Apple M1 (G13G B1), Vulkan API 1.4.354,
Honeykrisp (driver id 26), Mesa 26.2.4. Device extensions include
`VK_EXT_external_memory_dma_buf`, `VK_EXT_image_drm_format_modifier`,
`VK_KHR_external_memory_fd`, `VK_KHR_external_semaphore_fd`,
`VK_KHR_external_fence_fd`, `VK_EXT_queue_family_foreign` and
`VK_KHR_sampler_ycbcr_conversion`. These are the import, modifier and sync
extensions Mesa's Android buffer path builds on, so the stage 0 Vulkan gate
passes. No dma-buf import or export has been run yet.

### Result: image graphics files and properties (step 3)

Reported by the user from `work/asahi/graphics-image-f2q_lo9q` (static; nothing
executed):

- GLES is ANGLE: `ro.hardware.egl=angle`, `ro.gfx.angle.supported=true`, and
  system_a ships `libEGL_angle.so`, `libGLESv1_CM_angle.so` and
  `libGLESv2_angle.so`. ANGLE runs on the Vulkan driver, so the image needs no
  native vendor GLES driver.
- Vulkan is `ro.hardware.vulkan=adreno` with `vendor_a:/lib64/hw/vulkan.adreno.so`;
  HWUI and RenderEngine are set to Vulkan (`ro.hwui.use_vulkan=true`,
  `debug.renderengine.vulkan=true`, Graphite opt-in).
- Allocator: vendor `allocator-V2-ndk` plus QTI allocator libraries. Mapper:
  `vendor_a:/lib64/hw/mapper.qti.so` (stable-C mapper 5.0) and a HIDL 4.0 QTI
  mapper. Vendor also has `libcros_gralloc_bindgen.dylib.so`, a name that
  suggests ChromeOS gralloc (minigbm) bindings; its use is unknown.
- Composer: `drm_hwcomposer_atom_reporter.so` is present, consistent with the
  earlier audit's DRM-named HWC3, and `ro.vendor.hwc.drop_drm_master=1`.
- Display properties assume wide color, HDR, protected content and hole punch;
  these will need lowering for a first `apple-drm` bring-up (inference, not tested).
- Board platform `hamoa`, `ro.opengles.version=196610` (GLES 3.2).

Effect on the plan: the recommended GLES path changes from Mesa's Asahi GLES
driver to the image's own ANGLE on Honeykrisp. The Vulkan driver becomes the
single GPU driver to build for Android; `ro.hardware.vulkan` would select it.

## Later milestones

Use the report to select the next focused work:

1. Extend the completed native object/FD milestone to Android Parcel object/FD
   handling before real Android servicemanager or HAL integration.
2. Select an Asahi allocator/mapper/composer and Android Mesa development
   milestone using matching kernel sources and actual buffer/fence requirements.
   Planned in the graphics stage 0 section above.
3. Review a recoverable standalone boot approach only after userspace/kernel
   integration has been demonstrated.

Partitioning, formatting, flashing, EFI/boot file changes, Apple boot policy
changes and recovery replacement require separate explicit approval stating
affected storage, risks and recovery method. Preserve macOS, Apple ISC,
System Recovery and the working Fedora Asahi boot setup.

Do not run inherited VM `fetch.sh`, `install.sh`, `build-image.sh` or
`update-image.sh` for this audit. `fetch.sh` is a macOS workflow that also
downloads UTM and Cuttlefish artifacts. [asahi/README.md](asahi/README.md) provides
technical background; [UPSTREAM.md](UPSTREAM.md) describes the original VM workflow.
