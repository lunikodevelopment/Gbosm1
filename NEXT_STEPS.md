# Asahi development runbook

**All current and future user-executed steps belong here.** Work in Fedora Asahi
and retain the existing checkout and result directories.

Last updated: 10 October 2026, Europe/Amsterdam.
Current status: Bionic Toybox execution and native private Binder echo both passed
on Fedora Asahi ARM64/16 KiB. The dedicated Binder server domain ran with SELinux
enforcing; its temporary module was removed and the new executable label restored.
Qualcomm graphics adaptation remains required.
Next action: run the libbinder probe with its private readiness property fixture.
The retrieved trace identifies the wait for `servicemanager.ready` before Binder
traffic. The fixture is implemented; target execution of this revision remains
pending. Descendant cleanup and temporary policy/label cleanup succeeded in the
latest traced attempt.

## One command: libbinder with private stub readiness

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

After completion, say **get logs**. I will retrieve the summary, report and
client trace from the HTTP directory listing. A diagnostic run may still stop;
passing libbinder IPC will be claimed only when the verified stub reply, Android
client output, exit status and cleanup results establish it.

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
namespace and its final mode is 0444. Compatibility with the extracted Android
17 Bionic is established only by the next target run.

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

Retained as provenance. Use the corrected tracing command above for the next run.

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
| Binder IPC / binderfs | Native private Binder echo passed with protocol 8 on 16 KiB pages | Dedicated SELinux server domain; request/reply verified, temporary module removed and executable label restored. Android libbinder remains untested. |
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

## Later milestones

Use the report to select the next focused work:

1. Run the probe with the private readiness fixture, retrieve its client trace
   and cleanup results, and establish the Android list request/reply milestone.
2. Select an Asahi allocator/mapper/composer and Android Mesa development
   milestone using matching kernel sources and actual buffer/fence requirements.
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
