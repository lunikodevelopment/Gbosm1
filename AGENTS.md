# Working instructions

- The user works on the target in Fedora Asahi and reads instructions from GitHub.
- Keep all user-executed current and future development steps in root
  `NEXT_STEPS.md`. Update it whenever a result changes the next action; link to
  it from chat and other docs instead of maintaining competing command guides.
- The user has requested publishing these step updates to
  `lunikodevelopment/Gbosm1`. Publish authorized source/runbook changes and
  verify them, preserving unrelated remote work. Do not claim publication if
  a write failed. Do not change repository settings.
- Retain completed observations and distinguish reported, configured,
  enumerated and operationally tested facts. Do not require repeated basic
  hardware checks or exhaustive testing.
- Future steps depend on evidence. Do not invent implementations, successful
  builds, hardware support or a speculative flash/boot recipe.
- Before storage-modifying steps, state `THIS STEP MODIFIES STORAGE` and give
  scope, affected filesystem/partition, possible loss and reversal method.
  Ordinary development writes are authorized with scope explained.
- Partitioning, formatting, flashing, boot-chain and boot-policy changes require
  separate explicit approval. Preserve macOS, Apple ISC, System Recovery and
  the working Fedora boot setup. Do not overwrite EFI/boot partitions.
- Use the M1 for targeted development, not an assumed full native AOSP build.
- Preserve upstream copyright/license notices and document source provenance.
