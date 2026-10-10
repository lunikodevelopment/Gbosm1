# Android header shims for the stage 3e minigbm build

minigbm's Android code paths include `cutils/log.h` and `cutils/properties.h`, which ship with
Android's libcutils sources and are not in the NDK sysroot. These two files are small original
replacements written for this project (not copied from AOSP) that implement only what minigbm
needs on top of NDK APIs: `__android_log_print` and `__system_property_get`. They are used only
by `asahi/run-minigbm-bionic.sh`.
