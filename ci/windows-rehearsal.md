# Windows CUDA and ROCm rehearsal

The branch-only `windows-gpu-rehearsal.yml` workflow builds one shared Windows
CLI, then the existing CUDA 12, CUDA 13 and ROCm packages on hosted Windows
builders. Two native Linux JY jobs boot disposable Windows guests: one tests both
CUDA products, the other tests ROCm. They use the existing source/archive-bound
Qwen3 smoke, GPU selection/offload and shutdown contract. The Windows launcher
and provisioning source live in public `Mesh-LLM/mesh-llm-runner-images/windows`;
consumer payloads, model input, Windows disks and job credentials stay outside
the public launcher image.

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
  "gpu": "auto",
  "work_root": "/var/tmp/mesh-windows-attempts",
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

Use each lab's existing NFS export mounted at `/mnt/model-store`, with the same
relative cache root `ci/windows`. Keep actual export addresses and host inventory
in operator configuration rather than the public source tree.

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

The public recipe now supplies `host.py` and `install-host.sh`. The installed
root helper accepts `acquire|release --lease ID --gpu BDF --journal PATH` and
`describe --backend cuda|rocm`. Set `gpu` to `auto` to resolve the operator-approved
PCI address from that host's installed configuration.

The helper verifies a connected single-slot scheduler and the current native
reservation or accepted GitHub worker. Before changing a binding it journals
original drivers and persists `jy off`. An independent root systemd timer
reconciles abandoned or expired leases after cancellation and reboot. It stops
only that lease's labeled guests, restores original bindings and display services,
and verifies native GPU discovery before resuming claims. Operator configuration
changes or recovery failures preserve quarantine.

The golden Windows base remains on NFS. Disposable QCOW2 overlays and admitted
payloads use local scratch space; only guest writes occupy the smaller host disk.
The launcher requires 10 GiB free and stops below a 2 GiB reserve. Collect guest
and lifecycle evidence before deleting a successful attempt. Host-specific
configuration, Windows disks, signed drivers and the prepared launcher archive
remain operator-controlled artifacts outside Git.

Installation and offline checks do not establish GPU qualification. Both actual
Windows GPU smokes and cancellation/reset restoration must succeed before the
rehearsal is considered ready.

## Initial host probes

Bounded read-only probes inspected an x86_64 RTX 5080 host and an x86_64 Radeon
8060S host. Both passed KVM API 12 through a root container. The NVIDIA GPU's
IOMMU group contained only GPU/audio functions and the boot display was on a
separate iGPU. The AMD GPU was isolated in its own group. The launcher image
built successfully and created/stopped a paused Q35/KVM guest on both host types.

Both lab NFS caches now contain the same Microsoft Windows 11 26H2 English x64
ISO, 9,047,330,816 bytes, verified against the published digest pinned in the
public launcher's `windows/media.json`. Cache directory read/write/cleanup checks
passed in both labs, as did root-container file creation followed by host cleanup.
The exports remap numeric ownership, so dedicated cache directories must not
rely on sticky-bit ownership checks. Prepared driver staging avoids restoring
archive owners and makes its disposable working tree owner-writable before pruning.

The enrolled account and non-root compiler image users could not open KVM
directly; root inside a container with the explicit KVM device could. No account,
PCI binding, boot option, display service or existing Linux pool was changed.
These probes establish topology, KVM availability and media-cache admission only.
They do not establish GPU detach/reset/restore or Windows inference qualification.
