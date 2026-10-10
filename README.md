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

## Current and future steps

Use **[NEXT_STEPS.md](NEXT_STEPS.md)** as the single runbook in Fedora.
It contains the missing-package installation, one-command validation, saved-log
instructions, completed baseline evidence and the next milestones.
Future actionable steps will be published there as results arrive.

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


## Preserve the working installations

The received Fedora baseline confirms a 16 KiB runtime page size and the
Asahi GPU driver. GPU command execution and Android integration remain pending.
Use the 16 GB M1 for targeted bring-up; do not assume full AOSP builds fit.
Full platform builds need a suitable separate build host.

Keep macOS, Apple ISC, System Recovery and the working Asahi boot setup intact.
Partitioning, formatting, flashing, boot-policy, EFI and boot-chain changes
require separate explicit approval and are outside this milestone.

The inherited root VM scripts belong to the original macOS VM workflow.
**Do not run `install.sh`, `build-image.sh` or `update-image.sh` for this
Asahi milestone.** Follow the central runbook.
