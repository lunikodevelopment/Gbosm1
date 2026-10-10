/* Original minimal stand-in for libcutils' <cutils/log.h> built on the NDK's <android/log.h>. */
#ifndef GBOS_SHIM_CUTILS_LOG_H
#define GBOS_SHIM_CUTILS_LOG_H
#include <android/log.h>
#include <stdlib.h>

#define ALOGV(...) ((void)0)
#define ALOGD(...) ((void)__android_log_print(ANDROID_LOG_DEBUG, "minigbm", __VA_ARGS__))
#define ALOGI(...) ((void)__android_log_print(ANDROID_LOG_INFO, "minigbm", __VA_ARGS__))
#define ALOGW(...) ((void)__android_log_print(ANDROID_LOG_WARN, "minigbm", __VA_ARGS__))
#define ALOGE(...) ((void)__android_log_print(ANDROID_LOG_ERROR, "minigbm", __VA_ARGS__))
#define ALOG_ASSERT(cond, ...) do { if (!(cond)) { \
    __android_log_print(ANDROID_LOG_FATAL, "minigbm", "assertion failed: " #cond); abort(); } } while (0)
#define LOG_ALWAYS_FATAL_IF(cond, ...) do { if (cond) { \
    __android_log_print(ANDROID_LOG_FATAL, "minigbm", "fatal: " #cond); abort(); } } while (0)
#define LOG_ALWAYS_FATAL(...) do { __android_log_print(ANDROID_LOG_FATAL, "minigbm", __VA_ARGS__); abort(); } while (0)
#endif
