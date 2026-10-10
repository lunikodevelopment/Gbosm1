# Asahi development runbook

**All current and future user-executed steps belong here.** Work in Fedora Asahi
and retain the existing checkout and result directories.

Last updated: 10 October 2026, Europe/Amsterdam.
Current status: Honeykrisp GPU readback passed; recovery image verified; Android
17 build properties and four core ARM64 ELF alignment samples inspected.
Next action: run the focused recovery audit once and send its report.

## One command: recovery userspace audit

**THIS STEP MODIFIES STORAGE**

The Git pull updates tracked source files in `~/Gbosm1` on the home filesystem.
It preserves Git history and stops on conflicts; do not discard local changes.
The audit creates a new `work/asahi/recovery-audit-*` directory containing
`report.txt`, `summary.json`, inventories, XML copies, and tool diagnostics.
It retains earlier results and opens the recovery image read-only.
Delete only the newly printed result directory to reverse the generated writes;
source versions remain recoverable through Git. No partition, mount, SELinux,
or boot configuration changes are performed.

Run as `gbos_user`, without sudo:

```bash
cd ~/Gbosm1 && git pull --ff-only && python3 asahi/audit-recovery.py artifacts/mica-recovery.raw
```

The current target already has Python 3 and `erofs-utils 1.9.4`.
The script discovers GPT and Android logical-partition offsets, validates
primary metadata checksums, identifies EROFS, reads build properties, walks
VINTF directories and fragments, inventories APEX packages, and checks selected
core and vendor graphics ELF files for ARM64, 16 KiB PT_LOAD alignment and
declared DT_NEEDED dependencies. It reads files with `dump.erofs`; it does not
execute Android binaries, download images, install packages, or rerun the
completed hardware baseline and Vulkan milestone.

Send `report.txt` and `summary.json` from the printed result directory.
If the command stops, send the error and any report it created.
An audit exit status of 0 means the static scan completed, not that Android is
compatible. Unresolved findings are reported explicitly. APEX payload contents,
dependency resolution, complete ELF coverage, operational VINTF checks and
Android HAL execution remain unverified.

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
| Core ELF samples | init, surfaceflinger, libbinder.so and libgui.so are ARM64; every PT_LOAD has alignment 0x4000 and 16 KiB offset/address congruence | Four-file static sample only; broader library/APEX compatibility remains unverified. |
| VINTF | System, vendor, product and system_ext files/directories found | Declarations enumerated; compatibility and runtime HAL operation untested. |

Vulkan validation results were saved in
`/home/gbos_user/Gbosm1/work/asahi/results-7EXxM0au`.

The recovery ZIP and raw image were verified against `lib.sh` pins:

- ZIP: `cb68dd6dbd73e568cfeecca0cbd4ceb23338640814dc3ee52aba5507e5a645d7`
- Raw: `b0fd614ffe1a088fa3451a4b81c55a73f83e246286184a46ca306250db9af1f1`

## Later milestones

Use the report to select the next focused work:

1. Inspect APEX payloads and resolve graphics allocator/composer/driver
   dependencies and VINTF declarations.
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

