# Asahi development runbook

**All current and future user-executed steps belong here.** Work in Fedora Asahi
and retain the existing checkout and result directories.

Last updated: 10 October 2026, Europe/Amsterdam.
Current status: Bionic Toybox execution, native Binder echo, Android libbinder's
empty-list request/reply, native Binder object/FD transfer, and the Android
returned-object plus outgoing-FD Parcel probe all passed on Fedora Asahi
ARM64/16 KiB.
The dedicated Binder server domain ran with SELinux enforcing; its temporary
module was removed and the new executable label restored after the latest run.
Qualcomm graphics adaptation remains required.
Next action: rerun the one command under "Run now: real servicemanager first
run (stage 5a)" (the `/usr` bind is fixed), then say **get logs**.
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

## Graphics stage 0: complete (read-only evidence)

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

### Next: Graphics stage 1 (not yet written)

A native Fedora probe that allocates a linear and a tiled buffer with GBM on the
Asahi render node, imports each into Honeykrisp through dma-buf and its
modifier, renders and reads back. It is new code and will be added here once
written. It needs no DRM master and does not touch the display.

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
