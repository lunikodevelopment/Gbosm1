// Minimal host stand-in for the few liblog entry points libbase's logging.cpp calls, so the
// natively built `aidl` links without Android's liblog. Original code (not copied from AOSP);
// it implements the documented android/log.h API and writes to stderr. Build-host tool only.
#include <android/log.h>

#include <stdio.h>
#include <stdlib.h>

#include <string>

namespace {
__android_logger_function g_logger = nullptr;
__android_aborter_function g_aborter = nullptr;
int32_t g_min_priority = ANDROID_LOG_INFO;
std::string g_default_tag;
}  // namespace

extern "C" {

void __android_log_logd_logger(const struct __android_log_message* m) {
    fprintf(stderr, "%s: %s\n", m->tag ? m->tag : g_default_tag.c_str(), m->message ? m->message : "");
}

void __android_log_stderr_logger(const struct __android_log_message* m) {
    __android_log_logd_logger(m);
}

void __android_log_set_logger(__android_logger_function logger) { g_logger = logger; }

void __android_log_write_log_message(struct __android_log_message* m) {
    if (g_logger) g_logger(m); else __android_log_logd_logger(m);
}

void __android_log_set_aborter(__android_aborter_function aborter) { g_aborter = aborter; }

void __android_log_call_aborter(const char* abort_message) {
    if (g_aborter) g_aborter(abort_message);
    else { fprintf(stderr, "abort: %s\n", abort_message ? abort_message : ""); abort(); }
}

int __android_log_is_loggable(int prio, const char*, int) { return prio >= g_min_priority; }

int32_t __android_log_set_minimum_priority(int32_t priority) {
    int32_t old = g_min_priority;
    g_min_priority = priority;
    return old;
}

int32_t __android_log_get_minimum_priority(void) { return g_min_priority; }

void __android_log_set_default_tag(const char* tag) { g_default_tag = tag ? tag : ""; }

}  // extern "C"
