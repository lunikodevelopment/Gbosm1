# Asahi development runbook

**All current and future user-executed steps belong here.** Work in Fedora Asahi
and retain the existing checkout and result directories.

Last updated: 10 October 2026, Europe/Amsterdam.
Current status: Bionic Toybox execution passed. The private Binder probe built,
mounted binderfs, allocated a device and reported protocol 8 on 16 KiB pages,
but stopped at context-manager registration with `Permission denied`.
No Binder payload exchange has passed.
Next action: run the revised Binder probe with a temporary, dedicated SELinux
server domain. The module is removed after the attempt and enforcement remains
active. Target execution of this revision is pending; additional denials are
possible and will be retained for review.

## One command: Binder echo with a dedicated server domain

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
| Binder IPC / binderfs | Both configured `y`; private binderfs device created, protocol 8 and buffer mapping succeeded | Context-manager registration denied; transactions remain untested. |
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

1. Run the dedicated-domain Binder probe and review IPC and cleanup results
   before Android libbinder integration.
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
