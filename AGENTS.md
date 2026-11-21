# Skippy contributor instructions

This private repository owns Skippy. Rust packages live under `crates/`;
the separate Rust facade workspace lives under `skippy-api-rs/`, and the C++
facade lives under `skippy_api/`.

Use Just for all builds. Prefer shell for CI orchestration and simple checks;
use Python for structured manifest parsing, artifact verification, and
qualification. Keep each command in a focused script under `scripts/`.
Honor supplied native source and dependency inputs without downloading replacements.

Run Cargo commands serially in each checkout. For changed Rust crates run
formatting, focused check, warning-denying all-target Clippy, and full tests
including integration and documentation tests. Never claim a build/test passed
until it exits successfully. Never silence new warnings without a documented
reason. Do not introduce generic utility modules or functions above configured
Clippy limits. Extract separable responsibilities from oversized files.

Keep `llama_cpp/upstream.txt` and the ordered core, model-support, and generated
patch queues authoritative. ABI declarations, Rust bindings/version constants,
generated documentation, and recipe-bound certification must remain consistent.
Model-specific behavior belongs in model builders, not family-name branches in
shared stage machinery. Keep stage parity, state handoff, and graph contracts.

Read `.agents/skills/strong-types/SKILL.md` before giving a value in `crates/`
a type in place of a sentinel or a bare count. `skippy_api/AGENTS.md` and
`skippy_api/TYPE-RULES.md` hold the same rules for the C++ facade and the bridge.

Read `.agents/skills/manage-ci/SKILL.md` before CI work. PR and main use the same
CI implementation; tagged releases reuse it. Builds have no publication secrets.
Trusted same-repository PRs may use standard native JY jobs on persistent
hardware, with fresh source-pinned workspaces and no publication credentials.
The standard JY job account is accepted; no separate account migration is required.
Fork and other untrusted PRs require isolated
ephemeral execution. See the CI skill for admission and cleanup requirements.

Default tests are offline and use isolated temporary state. Model and hardware
qualification is explicit and cannot pass through missing-fixture/device skips.
Keep all third-party licenses and preserve wire/hash compatibility deliberately.
Never commit credentials. The extraction plan and progress ledger are under
`docs/design/`.
