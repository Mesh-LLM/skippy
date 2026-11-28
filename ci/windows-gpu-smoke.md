# Windows CUDA and ROCm GPU smoke

Full `ci.yml` builds each Windows archive with the existing shared CLI, then
runs two native Linux JY jobs with disposable Windows guests. One job tests CUDA
12 and CUDA 13 in the same guest; the other tests ROCm as soon as its archive
is ready. They require the existing source/archive-bound Qwen3 smoke, matching
GPU selection, positive GPU layer offload, a completion and clean shutdown.
The public launcher and provisioning source live in
`Mesh-LLM/mesh-llm-runner-images/windows`; host profiles, disks, drivers and
job credentials stay in operator configuration.

Nightly, manual full CI, release candidates and llama-upgrade validation call
this same implementation. Ordinary PR and main-push checks use the short profile
and do not schedule Windows jobs. No separate release or upgrade smoke workflow
is added. The temporary `windows-gpu-rehearsal.yml` is removed from the merge tree.

CUDA 13 uses the configured eight-core Depot Windows runner when
`SKIPPY_DEPOT_RUNNERS_ENABLED` is true; other archive producers use
`windows-2022`. The Windows CUDA 12.9.2 archive includes `sm_120`, so the RTX 5080
can exercise both CUDA products. Runtime admission still checks the actual GPU
against compiled architecture metadata.

The full CI gate requires both Windows smoke jobs. Each uploads per-runtime
`<scope>-device-evidence-windows-x86_64-*` artifacts. The manifest requires
inference for the three GPU archives, so release inventory rejects missing,
wrong-source or wrong-archive device receipts. Windows CPU retains archive
verification. Failure diagnostics include guest output and host restoration.

## Required configuration

Use the existing repository-scoped native JY groups:

| Backend | Runner group | Required labels |
| --- | --- | --- |
| CUDA 12 and 13 | `jy-rnr_j74hjtfyzpp6943` | `self-hosted`, `Linux`, `X64`, `windows-vm`, `cuda` |
| ROCm | `jy-rnr_ya8h5cpegg47v3x` | `self-hosted`, `Linux`, `X64`, `windows-vm`, `rocm` |

Restrict both groups to Skippy and
`Mesh-LLM/skippy/.github/workflows/ci.yml@refs/heads/main`. Keep each host
single-slot with an exclusive native reservation. Keep unqualified or failed
hosts disabled. The Linux smoke pools remain separate. Jobs have read-only
contents access and no publication credentials.

Repository variables `SKIPPY_WINDOWS_CUDA_PROFILE` and
`SKIPPY_WINDOWS_ROCM_PROFILE` contain the supplied host paths and signed driver
checksum. No driver is discovered or replaced from an unpinned download:

```json
{
  "base": "/mnt/model-store/ci/windows/bases/base-v1",
  "driver": "/mnt/model-store/ci/windows/media/vendor-windows-driver.exe",
  "driver_sha256": "<trusted SHA-256>",
  "gpu": "auto",
  "work_root": "/var/tmp/mesh-windows-attempts",
  "lifecycle_manager": "/usr/local/bin/mesh-windows-host"
}
```

For APU hosts requiring their own VBIOS and a paired audio UEFI ROM, add
`"firmware": "host"`. Each installed lifecycle manager supplies that machine's
`rom`, `rom_sha256`, `audio_rom` and `audio_rom_sha256` fields through
`describe --backend rocm --firmware`. Admission verifies the files before GPU
acquisition and passes each path/hash as a distinct launcher argument.

The site-local Windows cache root is `/mnt/model-store/ci/windows` in both labs.
Base paths must resolve to NFS; a missing mount must not silently
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

ROCm guests use a multifunction PCIe root port and unload the assigned AMD GPU
before shutdown. Host recovery requires the matching Windows disabled-device
receipt before rebinding an initialized allocation to Linux. Missing release
confirmation retains VFIO quarantine. After a host restart, recovery can resume
only when original driver bindings and native discovery already succeed.
Cancellation requests a cooperative guest exit before stopping the VM. The
guest stops the task process tree and unloads the assigned AMD device. The
independent timer uses the same request when the reservation owner disappears;
failure to obtain the release receipt still retains quarantine.

The golden Windows base remains on NFS. Disposable QCOW2 overlays use local scratch where capacity permits; smaller
hosts keep their writable overlays on NFS. The approved profile selects the path.
The launcher requires 10 GiB free and stops below a 2 GiB reserve. Collect guest
and lifecycle evidence before deleting a successful attempt. Host-specific
configuration, Windows disks, signed drivers and the prepared launcher archive
remain operator-controlled artifacts outside Git.

After retaining the smoke evidence, the workflow removes its copied product,
model and driver inputs from the native runner workspace. Repeated jobs must not
accumulate these large disposable files on hosts with small root filesystems.

Installation and offline checks do not establish GPU qualification. Both actual
Windows GPU smokes and cancellation/reset restoration must succeed before enabling a host for CI.

## Hardware qualification evidence

The Radeon 8060S canary uses BIOS 04.01 and a freshly extracted, checksum-pinned
VBIOS. Actual Windows ROCm inference passed with required GPU offload and a
four-token completion. Three normal Windows-to-Linux cycles and an
initialized-driver owner-death recovery passed on the same host boot. Each
restoration required the matching disabled-device receipt and final task result,
original GPU/audio bindings and native gfx1151 discovery. The earlier failed AMD
host stays quarantined. BIOS and release safeguards changed together; these
results do not establish isolated BIOS causality.

The RTX 5080 passed the normal CUDA 12 smoke with the verified `ff1aac1b`
archive and separately pinned `48930753` readiness controller, including the
completion, clean server shutdown and original NVIDIA/audio restoration.
This comparison records both source identities; it does not replace final-source
qualification. CUDA 13 and the complete final-source matrix remain pending.

Both lab NFS caches contain the checksum-verified Windows 11 26H2 English x64
ISO pinned in the public recipe. The immutable base remains on NFS. Its small
bootstrap is stable; per-run scripts and tools are hash-admitted from the recipe.
Bootstrap changes require preparing a new base.
