# Asahi development runbook

**All current and future user-executed steps belong here.** Work in Fedora Asahi
and retain the existing checkout and result directories.

Last updated: 10 October 2026, Europe/Amsterdam.
Current status: Honeykrisp GPU readback passed; recovery image verified;
recovery audit completed with 15 ARM64/16 KiB ELF samples and 81 VINTF XML files.
Next action: inspect the Qualcomm graphics dependencies and runtime APEX payloads.

## One command: graphics and runtime follow-up

**THIS STEP MODIFIES STORAGE**

The Git pull updates tracked source files in `~/Gbosm1` on the home filesystem.
It preserves Git history and stops on conflicts; retain local changes.
The script creates a new `work/asahi/graphics-audit-*` report directory and
uses temporary APEX package/payload copies inside it. Temporary copies are
removed automatically. Existing reports and the recovery image are retained.
Possible loss is limited to disk space used by new reports and temporary copies;
Git updates tracked source versions. Delete only the newly printed result
directory to reverse report writes; source versions remain recoverable in Git.

Run as `gbos_user`, without sudo:

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/audit-graphics.py
```

The script selects the latest completed recovery audit, uses its image path,
and revalidates GPT/LP metadata. The current target already has Python 3 and
`dump.erofs` from `erofs-utils 1.9.4`. It inspects selected vendor graphics
library dependencies and init files, inventories APEX packages and `system_b`,
and reads selected runtime/graphics APEX payloads. Runtime samples cover
`linker64`, `libc.so`, `libm.so` and `libdl.so`.

Packages larger than 512 MiB are rejected after copying; declared nested APEX
and payload sizes are checked before extraction, with a 512 MiB limit each.
An accepted package and payload can occupy approximately 1 GiB temporarily.
EROFS payloads use `dump.erofs`; ext4 payloads use read-only `debugfs` when
available and are reported unresolved otherwise. No image download or Android
binary execution is needed.

Send `report.txt` and `summary.json` from the printed result directory.
If the command stops, send the error and any report it created.
Exit 0 means collection completed, not Android compatibility. Candidate library
locations do not establish linker namespace resolution. Selected APEX packages
and their base VINTF XML are inspected; fragment coverage is not exhaustive.
Operational VINTF compatibility, HAL execution and display composition remain
unverified.

The preceding recovery audit is complete; retain its results without rerunning:
`/home/gbos_user/Gbosm1/work/asahi/recovery-audit-frw8c3gt`.
Its only reported unresolved finding was missing candidate build properties in
`system_b`; that partition's purpose remains unidentified.

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
| system_b | EROFS extent present; properties not found at candidate paths | Its purpose and slot relevance remain unidentified. |
| ELF samples | 15 core/vendor graphics binaries are ARM64 and satisfy 16 KiB PT_LOAD alignment | Static sample only; runtime APEX and complete userspace compatibility remain unverified. |
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

1. Use the follow-up report to select an Asahi graphics allocator/composer/driver
   replacement or adaptation and identify remaining runtime dependencies.
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

