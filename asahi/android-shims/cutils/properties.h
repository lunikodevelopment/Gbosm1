/* Original minimal stand-in for libcutils' <cutils/properties.h> built on bionic's
 * __system_property_get. Without a running property service every lookup returns the default. */
#ifndef GBOS_SHIM_CUTILS_PROPERTIES_H
#define GBOS_SHIM_CUTILS_PROPERTIES_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/system_properties.h>

#define PROPERTY_KEY_MAX PROP_NAME_MAX
#define PROPERTY_VALUE_MAX PROP_VALUE_MAX

static inline int property_get(const char *key, char *value, const char *default_value) {
    int len;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    len = __system_property_get(key, value);
#pragma clang diagnostic pop
    if (len > 0) return len;
    if (!default_value) { value[0] = '\0'; return 0; }
    len = (int)strlen(default_value);
    if (len >= PROPERTY_VALUE_MAX) len = PROPERTY_VALUE_MAX - 1;
    memcpy(value, default_value, (size_t)len);
    value[len] = '\0';
    return len;
}

static inline int32_t property_get_int32(const char *key, int32_t default_value) {
    char buf[PROPERTY_VALUE_MAX];
    return property_get(key, buf, NULL) > 0 ? (int32_t)strtol(buf, NULL, 0) : default_value;
}
#endif
