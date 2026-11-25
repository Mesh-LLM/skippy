#pragma once

#include <cstddef>

#if defined(TIERBENCH_HIP)
#if defined(__HIPCC__)
#include <hip/hip_runtime.h>
#else
#include <hip/hip_runtime_api.h>
#endif
#define GPU(name) hip##name
using gpu_device_prop = hipDeviceProp_t;
using gpu_device_attr = hipDeviceAttribute_t;
inline constexpr auto gpu_attr_integrated = hipDeviceAttributeIntegrated;
inline constexpr auto gpu_attr_sm_count = hipDeviceAttributeMultiprocessorCount;
inline constexpr auto gpu_attr_shared_optin = hipDeviceAttributeSharedMemPerBlockOptin;
inline constexpr const char* gpu_api_name = "hip";
inline hipError_t gpu_malloc_pinned(void** p, std::size_t n) { return hipHostMalloc(p, n, hipHostMallocDefault); }
inline hipError_t gpu_malloc_mapped(void** p, std::size_t n) { return hipHostMalloc(p, n, hipHostMallocMapped); }
inline hipError_t gpu_free_host(void* p) { return hipHostFree(p); }
#else
#if defined(__CUDACC__)
#include <cuda_runtime.h>
#else
#include <cuda_runtime_api.h>
#endif
#define GPU(name) cuda##name
using gpu_device_prop = cudaDeviceProp;
using gpu_device_attr = cudaDeviceAttr;
inline constexpr auto gpu_attr_integrated = cudaDevAttrIntegrated;
inline constexpr auto gpu_attr_sm_count = cudaDevAttrMultiProcessorCount;
inline constexpr auto gpu_attr_shared_optin = cudaDevAttrMaxSharedMemoryPerBlockOptin;
inline constexpr const char* gpu_api_name = "cuda";
inline cudaError_t gpu_malloc_pinned(void** p, std::size_t n) { return cudaMallocHost(p, n); }
inline cudaError_t gpu_malloc_mapped(void** p, std::size_t n) { return cudaHostAlloc(p, n, cudaHostAllocMapped); }
inline cudaError_t gpu_free_host(void* p) { return cudaFreeHost(p); }
#endif

extern "C" {
int tierbench_gpu_pull(const void* source, void* target, std::size_t bytes, void* stream);
int tierbench_gpu_global_read(const void* data, std::size_t bytes, unsigned passes, unsigned blocks, void* sink,
                              void* stream);
int tierbench_gpu_global_chase(const unsigned* next, unsigned stride, unsigned long long steps, void* sink,
                               void* stream);
int tierbench_gpu_shared_prepare(unsigned bytes);
int tierbench_gpu_shared_read(unsigned bytes, unsigned passes, unsigned blocks, void* sink, void* stream);
int tierbench_gpu_shared_chase(const unsigned* order, unsigned bytes, unsigned long long steps, void* sink,
                               void* stream);
}
