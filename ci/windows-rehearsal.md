# Windows CUDA and ROCm rehearsal

The branch-only `windows-gpu-rehearsal.yml` workflow builds one shared Windows
CLI, then the existing CUDA 12, CUDA 13 and ROCm packages on hosted Windows
builders. Two native Linux JY jobs boot disposable Windows guests: one tests both
CUDA products, the other tests ROCm. They use the existing source/archive-bound
Qwen3 smoke, GPU selection/offload and shutdown contract. The Windows launcher
and provisioning source live in public `Mesh-LLM/mesh-llm-runner-images/windows`;
private Skippy source, model input, Windows disks and job credentials do not.

This is an unqualified rehearsal. Production CI, release verification and target
validation policies are unchanged. Remove the branch-only workflow before merging
production integration, then disable its historical workflow entry and remove
its JY workflow allowlists, as with the Linux ROCm rehearsal.

## Required configuration

Create separate repository-scoped native JY runner groups for CUDA and ROCm,
restricted to this workflow at `refs/heads/rehearsal/windows-gpu-smoke`.
Use Linux x86_64 hosts with Docker and the labels `windows-vm-rehearsal` plus
the backend. Keep capacity disabled until host lifecycle recovery is installed
and reconciled. Do not change the existing Linux smoke pools or give these jobs
publication credentials.

Set repository variables `SKIPPY_WINDOWS_CUDA_REHEARSAL_GROUP` and
`SKIPPY_WINDOWS_ROCM_REHEARSAL_GROUP` to the actual JY runner groups. Their matching
`SKIPPY_WINDOWS_CUDA_REHEARSAL_PROFILE` and `SKIPPY_WINDOWS_ROCM_REHEARSAL_PROFILE`
variables contain host paths and a supplied driver checksum:

```json
{
  "base": "/mnt/model-store/ci/windows/bases/base-v1",
  "driver": "/mnt/model-store/ci/windows/media/vendor-windows-driver.exe",
  "driver_sha256": "<trusted SHA-256>",
  "gpu": "0000:01:00.0",
  "work_root": "/mnt/model-store/ci/windows/attempts",
  "lifecycle_manager": "/usr/local/libexec/windows-vm-gpu"
}
```

The site-local Windows cache root is `/mnt/model-store/ci/windows` in both labs.
Base and attempt paths must resolve to NFS; a missing mount must not silently
consume the RTX host's small root filesystem. Base preparation needs an admitted
Windows 11 ISO and its trusted checksum, plus at least 64 GiB available on NFS.
The public recipe's README documents preparation and driver provisioning.
Missing configuration fails admission rather than creating successful skips.

## Site-local installation media cache

Use each lab's existing NFS export, with the same relative directory layout:

| Network | Mounted store | Windows cache |
| --- | --- | --- |
| `dan-lab` | `10.1.1.10:/var/nfs/shared/Models` at `/mnt/model-store` | `/mnt/model-store/ci/windows` |
| `james-lab` | `10.2.1.10:/var/nfs/shared/Models` at `/mnt/model-store` | `/mnt/model-store/ci/windows` |

The roots contain `media`, `bases` and `attempts` directories. They keep Windows
media apart from the existing `huggingface` cache. The ISO is admitted under
`media/sha256/<trusted-sha256>/windows11.iso` on both NFS servers. Prepared base
disks are reused read-only; every run gets its own attempt directory.

Microsoft's consumer ISO links expire after 24 hours. Use a fresh browser-generated
link only when populating a lab cache; retain no URL in source, profile variables,
receipts or CI logs. The public recipe's `cache-iso` command uses curl redirects
and resume, verifies the selected ISO checksum and atomically publishes it.
Subsequent preparation/CI reads cached bytes without requiring the link. A copy
transferred between labs must be admitted against the same trusted checksum.

Example, run once in each lab with a private temporary URL file:

```sh
just --justfile windows/justfile cache-iso \
  --cache /mnt/model-store/ci/windows --require-nfs \
  --sha256 "$WINDOWS_ISO_SHA256" --url-file /private/windows-iso-url.txt
```

## Host lifecycle prerequisite

The public launcher consumes VFIO devices and does not unbind host GPUs itself.
The host manager is **not implemented or installed by this rehearsal PR**. It is
an explicit prerequisite that must be proved on both GPU types before activation.
Its adapter accepts `acquire|release --lease ID --gpu BDF --journal PATH`.

Acquisition must verify that the current JY reservation exclusively owns the
host, persist original bindings before changes, refuse active GPU users and
unrelated IOMMU devices, and bind the intended GPU/audio functions to VFIO.
Release must stop any surviving guest, restore every original binding and verify
native GPU discovery. Partial acquisition must be releasable. Cleanup failure
must quarantine the allocation in JY before returning; the agent must reconcile
the crash journal after cancellation, restart or lease expiry before new claims.
A shell trap alone does not establish that guarantee.

Until that manager, Windows media/storage and vendor installers are available,
the workflow is a draft rehearsal and cannot establish Windows qualification.

## Initial host probes

On 2026-11-25, bounded read-only JY jobs inspected one host of each type:

| Host | KVM through root container | GPU IOMMU group | Current native driver | Storage observation |
| --- | --- | --- | --- | --- |
| `lab-a33338`, RTX 5080 | API 12 | Group 12: GPU `0000:01:00.0` and audio only; host boot display is a separate AMD iGPU | `nvidia` | About 27 GiB free on the Linux work filesystem; also an unmounted 734 GiB NTFS partition, left untouched |
| `lab-003827`, Radeon 8060S | API 12 | Group 19: GPU `0000:c2:00.0` alone | `amdgpu` | About 1.7 TiB free on the Linux work filesystem |

Topology probe jobs: `job_7z87yxawqbc2fw4`, `job_f3hy4wzcw8vb09p`.
Successful root-container KVM probes: `job_qhq4havjr9zv775`, `job_t7b7sssr0mceq44`.
The public launcher build and its own KVM probe succeeded in
`job_kf7rdzgjnjkwm7k`, run `run_fwq6d8pxny2crj3`, using public source
`acfa02e50004644e324715c02737f3216d13d83b`.

The enrolled account and non-root compiler image users could not open KVM
directly; root inside a container with the explicit KVM device could. No account,
PCI binding, boot option, display service or existing Linux pool was changed.
These probes establish topology and KVM availability only. No Windows boot,
GPU detach/reset/restore or Windows inference smoke has run.
