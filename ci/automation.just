# Offline automation and reporting recipes; no native build inputs.

ci-windows-vm-contracts:
    @powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/tests/ci_windows_vm_variants.ps1

# Explicit hardware rehearsal; never a dependency of offline checks.
ci-rocm-preflight:
    @bash scripts/ci-rocm-preflight.sh

inference-contracts:
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_failure.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_logs.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_streaming.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_build.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_deadlines.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_peer.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_socket.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_peer_evidence.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_probe.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/jy_read_recovery.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_markdown.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_nightly.py

inference-publication-test:
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_publication.py

ci-vulkan-contracts:
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/ci_vulkan.py

ci-vulkan-sdk version:
    @"{{ automation_python }}" scripts/ci-vulkan-sdk.py "{{ version }}"

automation-contracts: inference-contracts ci-vulkan-contracts
    @"{{ automation_python }}" scripts/tests/ci_cuda_license.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/ci_windows_cuda.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/ci_windows_vm_profile.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/ci_process_environment.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/automation_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/ci_process_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/ci_smoke_readiness.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/ci_product_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_abi.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_publisher_config.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_diagnostics.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_verification.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_lifecycle.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_rehearsal.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/graph_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/release_contracts.py

[positional-arguments]
inference-render-report format input *options:
    @"{{ automation_python }}" scripts/inference-render-report.py "$@"

ci-automation: automation-contracts inference-publication-test
    @bash scripts/ci-automation.sh

[positional-arguments]
ci-automation-setup directory:
    @bash scripts/ci-automation-setup.sh "$@"

# Explicit model-backed controller rehearsal; excluded from offline defaults.
upgrade-goose-rehearsal:
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/upgrade_goose_rehearsal.py
