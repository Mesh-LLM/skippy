#!/usr/bin/env bash
set -euo pipefail

# The caller supplies only whether the private-key secret exists, never its value.
missing=0
if [[ -z "${SKIPPY_UPGRADE_APP_ID:-}" ]]; then
    echo '::error::Configure the SKIPPY_UPGRADE_APP_ID Actions variable before starting a changed-pin llama upgrade.' >&2
    missing=1
fi
if [[ "${SKIPPY_UPGRADE_APP_KEY_CONFIGURED:-false}" != 'true' ]]; then
    echo '::error::Configure the SKIPPY_UPGRADE_APP_PRIVATE_KEY Actions secret before starting a changed-pin llama upgrade.' >&2
    missing=1
fi
if [[ "$missing" == 1 ]]; then
    echo 'Upgrade publication is not configured; candidate preparation has not started.' >&2
    exit 1
fi
printf '%s\n' 'Upgrade publisher credential settings are present; verifying App access next.'
