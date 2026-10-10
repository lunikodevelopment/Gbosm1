# Googlebook OS on Apple M1

Engineering workspace for native Android on the MacBook Pro 13-inch M1 (2020),
MacBookPro17,1 / j293ap, using Asahi Linux hardware support.

**Current milestone:** run the existing gbos-vm GPU smoke test directly on Fedora
Asahi and collect kernel/interface evidence. Native Android boot and Android
HAL integration are not yet implemented or verified.

Source imported from [skylartaylor/gbos-vm](https://github.com/skylartaylor/gbos-vm)
at commit `3b08ed29a73858644c65f24e06ee2a7cfb7e3774`, with our Asahi additions.
The original README is preserved in [UPSTREAM.md](UPSTREAM.md).
Copyright and license notices remain in [LICENSE](LICENSE) and the source files.
This is a source import, not a GitHub fork retaining upstream history.

## Run after booting Fedora Asahi

Clone directly in Fedora; transferring the earlier archive is no longer necessary.
Run as `gbos_user`.

**THIS STEP MODIFIES STORAGE**

Cloning creates `~/Gbosm1` on your home filesystem. Building creates/replaces
`~/Gbosm1/work/asahi/vulkan-probe`. No system files, partitions or boot settings
are changed. Use a new directory if that checkout already exists.
Deleting the new checkout reverses these ordinary file writes.

Execute these commands one at a time. If any command fails, stop and return its
output. The scripts do not install packages automatically.

```bash
cd ~
git clone https://github.com/lunikodevelopment/Gbosm1.git
cd Gbosm1
python3 asahi/baseline.py
bash asahi/build-probe.sh
./work/asahi/vulkan-probe
echo "readback exit status: $?"
./work/asahi/vulkan-probe --caps
echo "capabilities exit status: $?"
```

Do not run the probe with sudo. Send back the baseline JSON, compiler output,
both probe outputs, and their exit statuses. Review paths/device details in the
baseline before sharing publicly.

Expected result: runtime page size 16384, an integrated GPU using Honeykrisp,
`VM_VULKAN readback=PASS`, and exit status 0. Software Vulkan does not count as
a hardware pass. Missing drivers, development packages or permissions require
investigation from actual output.

The test reuses the upstream buffer-fill/readback logic. It does not establish
Android AHardwareBuffer sharing, display scanout, Binder transactions or Android
boot. Linux rejects the Android-only `--ahb` and `--ahb-render` modes.

## Engineering guide and validation

Read [asahi/README.md](asahi/README.md) for the compatibility matrix, kernel
evidence, 16 KiB constraints and next engineering steps.
The [Android 16 KiB documentation](https://source.android.com/docs/core/architecture/16kb-page-size/16kb)
describes userspace alignment and runtime page-size handling.

- Four Python collector fixture tests passed on macOS.
- Shell syntax and Git whitespace checks passed.
- Native Linux compilation and M1 GPU execution are pending.
- Android build regression validation is pending; no NDK is installed locally.
- No recovery image was downloaded, assembled, installed or booted.

```bash
python3 -m unittest discover -s tests -p 'test_asahi_*.py' -v
bash -n asahi/build-probe.sh
```

## Preserve the working installations

The target is reported as Fedora Asahi Remix 44 with a 16 KiB kernel.
The collector must verify the relevant live configuration.
Use the 16 GB M1 for targeted bring-up; do not assume full AOSP builds fit.
Full platform builds need a suitable separate build host.

Keep macOS, Apple ISC, System Recovery and the working Asahi boot setup intact.
Partitioning, formatting, flashing, boot-policy, EFI and boot-chain changes
require separate explicit approval and are outside this milestone.

The inherited root VM scripts belong to the original macOS VM workflow.
**Do not run `install.sh`, `build-image.sh` or `update-image.sh` for this
Asahi milestone.** Follow the commands above.
