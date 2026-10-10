# Asahi development runbook

**All current and future user-executed steps belong here.** Read this file in
Fedora; you do not need to boot macOS to get the instructions. Future commands
will be added here and published as actual results determine the next milestone.

Last updated: 10 October 2026, Europe/Amsterdam.
Current blocker: the Fedora Minimal installation has no C compiler.

## 1. Get the latest instructions in Fedora

**THIS STEP MODIFIES STORAGE**

The pull updates tracked files in `~/Gbosm1` on the home filesystem. It retains
Git history and stops if local changes conflict; do not discard local changes.
Previous source versions remain recoverable through Git. No partition or boot
settings are changed.

```bash
cd ~/Gbosm1
git pull --ff-only
less NEXT_STEPS.md
```

Press `q` to leave `less`. Keep this checkout in Fedora for subsequent work.
If pull fails, stop and report the error; do not reset or reclone over your work.

## 2. Install the missing build prerequisites

**THIS STEP MODIFIES STORAGE**

DNF installs GCC, pkg-config, Vulkan development files and required dependencies
into Fedora's system filesystem, and updates its package database/cache. It may
update dependency packages; review its transaction summary before accepting.
Personal files are not targeted. This is not a partitioning or boot-chain step.
Newly installed packages can be removed through DNF after checking dependencies;
retain the transaction record if any package versions were updated.

```bash
sudo dnf install gcc pkgconf-pkg-config vulkan-loader-devel
```

If installation fails, stop and send that output. No full AOSP build or large
recovery download is required. Vulkan runtime/driver availability is still
unverified; do not install or replace graphics packages speculatively.

Package references: [gcc](https://packages.fedoraproject.org/pkgs/gcc/gcc/),
[pkg-config](https://packages.fedoraproject.org/pkgs/pkgconf/pkgconf-pkg-config/),
[Vulkan development files](https://packages.fedoraproject.org/pkgs/vulkan-loader/vulkan-loader-devel/).

## 3. Build and validate with one command

**THIS STEP MODIFIES STORAGE**

Validation creates/replaces `work/asahi/vulkan-probe` and creates a new result
directory under `work/asahi` within the checkout. No source files are removed;
existing logs are retained. Deleting the generated binary/result directory
reverses these file writes. Run as `gbos_user`, without sudo.

```bash
cd ~/Gbosm1
bash asahi/validate.sh
```

This builds the native probe, runs the GPU readback, then enumerates capabilities.
It stops at the first failure, prints the output, and identifies the saved logs.
It does not rerun the completed baseline, download images, install packages,
load modules, mount Binder, or change boot settings.

Acceptance: page size 16384, Honeykrisp integrated GPU selected,
`VM_VULKAN readback=PASS`, and exit status 0. `--caps` alone is not a readback
test. CPU/software Vulkan does not count as success.

Send the displayed build/readback/capabilities output and exit statuses from
your other computer. The same output is saved in the printed result directory.
If validation fails, send the first failed stage's output. Do not continue with
missing executables, run the probe as root, or disable SELinux.

## Evidence already received

User-supplied Fedora output on 10 October 2026 establishes:

| Observation | Result | Scope |
|---|---|---|
| Running kernel | `7.1.6-400.asahi.fc44.aarch64+16k` | From collector output. |
| Runtime page size | 16384 bytes | Confirmed; no need to repeat baseline now. |
| Binder IPC / binderfs | Both configured `y`; binder filesystem registered | No Binder nodes reported; transactions remain untested. |
| DMA-BUF / DMA heaps | Configured `y`; system heap node present | Allocation and sharing remain untested. |
| GPU | Asahi module loaded; `renderD128` and `card2` use Asahi | Vulkan userspace and command submission remain untested. |
| Internal display | `apple-drm`, connected eDP, 2560×1600 mode | Enumeration only; Android composition remains untested. |
| SELinux | Enforcing (`1`) | Retain enforcement. |
| Namespaces / cgroups / IOMMU | Enabled in config; cgroup2 mounted; apple_dart loaded | Android workload compatibility remains untested. |
| Build | Stopped: `Missing C compiler` | No probe binary created; subsequent exit 127 was expected. |

## Later milestones — not executable instructions yet

After the GPU result, this runbook will gain the next concrete commands:

1. Resolve compilation, driver or permission failures from actual output.
2. Inspect available recovery userspace/artifacts for Android build properties,
   ELF 16 KiB alignment, dependencies and VINTF compatibility.
3. Validate Android kernel interfaces and select a focused native vendor/HAL
   milestone using the matching Asahi kernel sources and driver ABI.
4. Design and review a recoverable standalone boot approach only after the
   userspace/kernel integration is demonstrated.

There is no authorization here to partition, format, flash, modify EFI/boot
files, change Apple boot policy or replace recovery. Those steps require separate
explicit approval with the affected storage, risks and recovery method stated.
Keep macOS, Apple ISC, System Recovery and working Fedora Asahi recoverable.

Do not run the inherited VM `install.sh`, `build-image.sh` or `update-image.sh`
for this milestone. [asahi/README.md](asahi/README.md) is technical background;
[UPSTREAM.md](UPSTREAM.md) describes the original macOS VM workflow.
