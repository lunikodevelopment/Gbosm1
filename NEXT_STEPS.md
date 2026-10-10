# Asahi development runbook

**All current and future user-executed steps belong here.** Work in Fedora Asahi
and retain the existing checkout and result directories.

Last updated: 10 October 2026, Europe/Amsterdam.
Current status: native Honeykrisp readback passed; recovery and graphics audits
completed. All 34 follow-up ELF samples, including Android linker64 and Bionic,
meet ARM64/16 KiB load alignment. Qualcomm graphics adaptation remains required.
Next action: execute only the extracted Android Toybox `uname` applet through
the image's Android linker on the existing Fedora kernel.

## One command: minimal Bionic execution milestone

**THIS STEP MODIFIES STORAGE**

The Git pull updates tracked source files in `~/Gbosm1` on the home filesystem,
preserving history and stopping on conflicts. Retain local changes.
The probe creates a new `work/asahi/bionic-probe-*` directory containing
extracted runtime files, library candidates and logs. Temporary runtime APEX
copies are removed automatically. Existing reports and the source image are
retained. Possible loss is disk space consumed by these new files; tracked
source versions are updated by Git. Delete only the newly printed result
directory to reverse probe writes; source versions remain recoverable in Git.

Run as `gbos_user`, without sudo:

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/probe-bionic.py
```

The script uses the latest completed graphics audit and revalidates GPT/LP
metadata. It copies the runtime linker, Bionic libc/libm/libdl and Toybox from
the existing image. It inspects their ARM64/16 KiB alignment, follows DT_NEEDED
into candidate `/system/lib64` libraries and stops on missing or malformed
candidates. `ld-android.so` is supplied by the Android linker itself.
No host glibc libraries are substituted. Copies are bounded to 512 MiB for the
runtime APEX and its payload, and 64 MiB per binary/library, with at most 64
library entries. Allow several GiB of temporary/report capacity.

After extraction, it invokes the copied Android linker explicitly with a local
library search path and **only `toybox uname -a`**, as your ordinary user.
The program reports the Fedora host kernel. No Android init, services, HALs,
mounts, chroot or system installation are needed. Execution has a 20-second
limit. The Fedora SELinux policy remains enforced; if execution is denied,
retain the diagnostic rather than changing permissions or enforcement.

Send `report.txt` and `summary.json` from the printed directory. If it stops,
send the error and any report it created. `BIONIC_UNAME=PASS` and exit 0 establish
only this small Android userspace execution milestone. They do not establish
Android boot, complete linker namespace compatibility, Binder transactions,
HAL operation or graphics integration. This probe has not yet run on the target.

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
| Binder IPC / binderfs | Both configured `y`; binder filesystem registered | No Binder nodes reported; transactions remain untested. |
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
| Runtime follow-up | 30 vendor dependency ELF files and 4 runtime APEX ELF files meet ARM64/16 KiB alignment; exit 0, zero reported unresolved findings | Static collection only; Android binary execution is the next milestone. |
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

1. Establish minimal Bionic execution using the command above, then select an
   Asahi allocator/mapper/composer and Android Mesa development milestone.
2. Validate Android kernel interfaces and choose a native vendor/HAL milestone
   using matching Asahi kernel sources and driver ABI.
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

