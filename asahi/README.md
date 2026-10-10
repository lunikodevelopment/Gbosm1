# Googlebook M1 bring-up: first milestone

Source inspected: `skylartaylor/gbos-vm`, main commit
`3b08ed29a73858644c65f24e06ee2a7cfb7e3774`.
Local development branch: `bringup/asahi-vulkan-probe`.

## What the repository actually contains

This is a macOS image-porting toolkit, not full Android platform source. README
identifies a prebuilt ARM64 Android 17 Dell Googlebook recovery image, build
16471258. `lib.sh` pins that download and Cuttlefish build 16373615. The image
itself is not checked in or downloaded in this workspace; Android version, SDK,
build properties and runtime behavior are therefore upstream reports, not
independently verified image facts.

There are no AndroidProducts.mk, BoardConfig.mk, repo manifest, Asahi device tree,
or kernel sources in this checkout. `build-guest.sh` uses an Android API 35 NDK
toolchain and API 34 minimum for the Java helper. Those settings are component
build targets, not evidence of the OS version.

`build-image.sh` extracts the recovery kernel/ramdisks, grafts Cuttlefish services,
creates a modified EROFS vendor filesystem, and builds a regular-file VM disk.
`run/run_vm.py` boots QEMU virt with HVF, an emulated GIC, PL011 UART, PCI
virtio GPU/block devices, USB input/network/audio, and virtual Bluetooth.

## Compatibility matrix

| Component | Classification | Evidence and required action |
|---|---|---|
| Vulkan buffer-fill/readback logic | Reusable without modification | Existing `guest/guest_vulkan_probe.c` uses ordinary Vulkan commands; Linux wrapper now reuses that path. |
| System/product userspace and apps | Unknown/blocked | Prebuilt recovery image absent; inspect build properties, ELF alignment, dependencies and VINTF before deciding reuse. |
| Vulkan probe entry point | Requires adaptation | Android AHardwareBuffer/EGL tests and virtgpu ioctls are isolated behind `__ANDROID__`. Linux uses native Vulkan. |
| Generic DRM HWC3 composer | Requires adaptation | `image/mica_graphics_port.py` imports a Cuttlefish generic DRM composer; validate Asahi KMS, buffer layouts, fences, formats and Android ABI before deployment. |
| Graphics allocator/mapper, GLES/Vulkan driver selection | Must be replaced or rebuilt for Asahi | Minigbm donor compatibility is unknown; current drivers are VirGL/Venus, with a virtual buffer-sharing patch. Native Asahi/Honeykrisp and compatible Android allocation paths are required. |
| Software KeyMint/Gatekeeper | Requires adaptation | Image overlay uses software services; potentially useful for development but not evidence of hardware-backed security or production compatibility. |
| SELinux and vendor init | Requires adaptation | QEMU GPU sysfs labels, virtual UART and service assumptions do not describe M1. Retain enforcing policy and develop labels from actual target evidence. |
| VM input/clipboard helper | Must be replaced for native input | Java code depends on Android reflection, app_process, uinput and host connection to 10.0.2.2. It is not a native keyboard/trackpad driver. |
| VM audio/Bluetooth/network transport | Must be replaced | Virtual USB/virtconsole and netsimd do not provide M1 hardware integration. |
| Host viewer/render server | Must be replaced | CocoaSpice, Metal, MoltenVK and macOS frameworks implement the VM host side. |
| Kernel/module and boot path | Must be replaced | Current image uses recovery kernel plus donor virtio/DMA-heap modules. Do not load them into Asahi: kernel ABI/configuration must match. |
| Image/partition tooling | Requires adaptation | Fixed Mica offsets, Android logical partitions, EROFS/ext4 and APFS clone commands; none is a valid recipe for writing the M1 internal disk. |

## First proof of concept

Build the existing Vulkan probe as a native Linux executable. It requests Vulkan
1.2 and selects a Honeykrisp integrated GPU by driver ID, refusing software-only
success. Honeykrisp uses Mesa's vendor ID rather than Apple's PCI vendor ID. It submits
a buffer fill, waits up to ten seconds for a fence, invalidates host-visible memory,
and checks all 1024 words. The 4096-byte test buffer is a payload size, not a kernel
page-size assumption. Allocation uses the driver's reported memory requirements.

`--caps` enumerates capabilities only; it does not submit the readback test.
Android builds retain their virtgpu and AHardwareBuffer paths. Linux rejects
`--ahb` and `--ahb-render` rather than implying Android buffer integration works.

This milestone checks native Linux GPU submission using a reused Android-project
component. It does not establish Android boot, Android HAL functionality, display
scanout, Android buffer interoperability, or bare-metal Android completion.

`baseline.py` prints read-only evidence from the running kernel's config, modules,
device tree, DRM sysfs, mounts, SELinux state and Binder/DMA-heap nodes. Missing
information is marked unknown or accompanied by an error; `m` configuration does
not prove a module is loaded. Built-in drivers may not appear in `/proc/modules`.
No mounts, module loading, boot configuration, policy or partition changes occur.
The report contains paths and device details; review it before sharing publicly.

## Run in Fedora Asahi

Transfer the supplied source archive to your Fedora home directory first. No
full AOSP build, recovery-image download, or VM installer is needed.

**THIS STEP MODIFIES STORAGE**

Extracting the archive creates a source directory under your home filesystem.
Building creates/replaces `work/asahi/vulkan-probe` there. These ordinary files
can be deleted to reverse the step; no EFI, boot or Apple partitions are changed.
Use a new directory name to avoid overwriting an existing checkout.

```bash
mkdir ~/gbos-asahi-poc
tar -xzf ~/gbos-asahi-poc.tar.gz -C ~/gbos-asahi-poc
cd ~/gbos-asahi-poc
python3 asahi/baseline.py
bash asahi/build-probe.sh
./work/asahi/vulkan-probe
./work/asahi/vulkan-probe --caps
```

Run as `gbos_user` without sudo. Copy the JSON, compiler output and both probe
outputs back into the conversation. If the build script reports missing
prerequisites, stop and return that output; it does not install packages.
Likely Fedora package names: gcc, pkgconf-pkg-config, vulkan-loader-devel.
Vulkan runtime/driver availability on the Minimal install is unverified.

Acceptance: reported runtime page size 16384; Apple hardware device selected;
successful submit/wait/map/invalidate; `VM_VULKAN readback=PASS`; exit status 0.
An error or exit 20 is a diagnostic result, not milestone success. Do not work
around permissions by running the probe as root or disabling SELinux.

## 16 KiB and next steps

Android supports 16 KiB configurations, but the prebuilt recovery userspace and
all transplanted libraries still need ELF PT_LOAD alignment checks and runtime
validation. Android build guidance requires 16 KiB ELF alignment and removing
compile-time page-size assumptions:
https://source.android.com/docs/core/architecture/16kb-page-size/16kb

The existing image's 4096-byte boot-image header alignment and EROFS block size
are file-format parameters, not proof that all its ELF binaries are incompatible
with a 16 KiB kernel. Kernel modules cannot be reused across the kernel change.

After the Fedora results, identify the exact kernel package/source and Asahi DRM
driver ABI, then audit the existing userspace binaries and allocator/composer
dependencies. Binder availability may require configuration or matching module
work; no operational Binder claim is made from config alone. Android build
configuration and vendor paths should follow those results, not speculative HALs.

Native Asahi Vulkan background:
https://asahilinux.org/2024/06/vk13-on-the-m1-in-1-month/

## Validation in this workspace

The collector has fixture tests for disabled versus unknown configuration,
gzip/fallback selection and DRM driver symlinks:

```bash
python3 -m unittest discover -s tests -p 'test_asahi_*.py' -v
bash -n asahi/build-probe.sh
```

Linux compilation, native GPU execution and the Android build regression check
remain pending: this local session is macOS without a C compiler/Android NDK or
an execution connection to Fedora. No image build or boot attempt has been made.
