#ifndef TIERBENCH_H
#define TIERBENCH_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TIERBENCH_ABI_VERSION 2u

typedef enum tierbench_tier {
    TIERBENCH_HOST = 1,
    TIERBENCH_GPU = 2,
    TIERBENCH_STORAGE = 3,
} tierbench_tier;

typedef enum tierbench_unit {
    TIERBENCH_BYTES = 1,
    TIERBENCH_TOKENS = 2,
} tierbench_unit;

typedef struct tierbench_env {
    const char* scratch_dir; /* on the storage under test; a plugin removes what it creates there */
    uint32_t gpu_index;
    uint64_t page_size;
} tierbench_env;

typedef struct tierbench_error {
    uint8_t unsupported; /* setup only: set when this size cannot run here, so the runner records it but does not fail */
    char message[256];
} tierbench_error;

typedef struct tierbench_measure {
    uint64_t bytes_per_iteration; /* 0 when a case measures latency, not bandwidth */
    uint64_t ops_per_iteration;   /* arithmetic operations, e.g. 2*M*N*K for a matrix multiply; 0 if none */
    uint64_t device_ns;
    uint8_t has_device_ns;
} tierbench_measure;

typedef struct tierbench_case {
    const char* name;
    const char* mechanism;
    tierbench_tier from;
    tierbench_tier to;
    tierbench_unit unit; /* what `size` means in setup, min_size and max_size */
    uint64_t min_size;
    uint64_t max_size;
    /* returns state the plugin owns until teardown; NULL with error set on failure */
    void* (*setup)(uint64_t size, const tierbench_env* env, tierbench_error* error);
    /* returns 0 once all iterations have completed, including any device work */
    int (*run)(void* state, uint64_t iterations, tierbench_measure* out, tierbench_error* error);
    void (*teardown)(void* state);
} tierbench_case;

typedef struct tierbench_plugin {
    uint32_t abi_version;
    const char* name;
    /* JSON object text, owned by the plugin until it is unloaded */
    const char* (*facts)(const tierbench_env* env);
    uint32_t case_count;
    const tierbench_case* cases;
} tierbench_plugin;

const tierbench_plugin* tierbench_plugin_v1(void);

#ifdef __cplusplus
}
#endif

#endif
