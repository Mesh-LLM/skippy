# Scheduler and KV fixes

The [investigation](SCHEDULER_KV.md) found two scheduling delays and a resident
cache that discarded its working set after one pass of fresh prompts.

- The planned scheduler orders runnable prefills by their last turn. A long
  prompt yields after its chunk, including on the recurrent path with a
  one-sequence prefill limit. Selected native rows still use canonical order.
- After a successful prefill-only batch, the direct scheduler checks whether
  the next batch would again be prefill-only. In that case, ready decodes
  from independent sessions get one turn. Existing mixed batches and
  per-session ordering are preserved. Arrival order resumes after the
  decode turn.
- A successful resident restore earns bounded eviction preference. First-use
  entries are reclaimed first. Reused entries can still be reclaimed for
  capacity and lose preference after two entry-limit turnovers without another
  restore. Entry and byte limits still apply.

## Local checks

The three crates passed focused checking, warning-denying all-target Clippy
and the full offline test command. The final run recorded 1,687 passing
tests and 34 existing ignored tests. All four new regression tests passed.

```sh
just with-lld cargo fmt -p skippy-scheduler -p skippy-cache -p skippy-serving --check
just with-lld cargo check --locked --offline -p skippy-scheduler -p skippy-cache -p skippy-serving
just with-lld cargo clippy --locked --offline -p skippy-scheduler -p skippy-cache -p skippy-serving --all-targets -- -D warnings
just with-lld cargo test --locked --offline -p skippy-scheduler -p skippy-cache -p skippy-serving
just with-lld cargo bench --locked --offline -p skippy-scheduler --features scheduler-lab --bench scheduler_lab
```

The release scheduler lab completed all 13 scenarios in each build. The
simulated makespan, queue and latency percentiles, batch occupancy and
iteration counts matched across builds.

## Local Metal validation

The release builds compare baseline `671c6ebbf7661b236ce44d352024c51741b08f9c`
with the Rust working-tree patch. Both use the same packaged Metal runtime
on the Apple M5 Max and the same model file within each comparison. The
hybrid-dense fixture uses the Qwen3.5-4B GGUF from the investigation. The cache
fixture uses the locally available Qwen3-4B Q4_K_M revision
`22c9fc8a8c7700b76a1789366280a6a5a1ad1120`. The local fixture uses different
synthetic text from the M1 study.

The first uninstrumented timing attempt stopped at the thermal guard. A local
diagnostic replay confirmed scheduler behavior and cache hits with native
thermal pressure levels up to 2 permitted. Another Skippy process interrupted
the second cache-off baseline arm. The complete base/candidate pair and the
second candidate arm retain separate evidence.

- **Cache-off scheduling.** The baseline sent the late short request's first
  token after both long requests. In both candidate arms the short request
  emitted its first token ahead of both long requests. The complete pair
  matched request counts and generated text. A fresh pair on the final patch
  reproduced the ordering and exact output agreement. Interleaving also
  delayed the two long requests in that pair.
- **Cache-on decode service.** In two paired replays of the narrowed policy,
  both baseline traces included consecutive 512-token prefill batches.
  Both candidate traces included decode-only batches between these prefills.
  The longest anchor inter-token gap was 366–372 ms on baseline and
  232–240 ms on the candidate. The wave p95 TTFT was 2,297–2,352 ms on
  baseline and 2,347–2,396 ms on the candidate. These are instrumented local
  observations and show a trade-off worth checking under a controlled load.
  The same anchor's generated text differed in both pairs. All other
  generated text matched. The anchor produced identical text across builds
  when run alone with the resident cache enabled and zero cached tokens.
  The candidate's mixed-load output matched that solo control in both
  pairs. The baseline's mixed-load output differed from the solo control in
  both pairs. This is a single-fixture consistency check. Wider likelihood
  and task-quality validation remain open.
- **Resident cache.** Both arms hit all four recently warmed prefixes on
  immediate reuse. Following the scan of novel prefixes, baseline restored
  zero of four and the candidate restored three of four, totaling 6,141
  restored tokens. Generated text matched in every request in the replay.

The final build candidates use the same unified native KV. This validation
compares the patch with unchanged Skippy to isolate its effect. The
[earlier comparison](SCHEDULER_KV.md) measures vanilla llama.cpp with
its default separate slots and with explicit unified KV. The new evidence
covers local behavior. A fresh controlled vanilla comparison, the native
cache-family matrix and the required Thoughtworks c64/c128/c256 workload
remain outstanding. Their prepared workload manifests were unavailable
locally. This follow-up used the local Mac and consumed zero lab minutes.

The native four-lane recurrent restore test passed with the Qwen3.5-4B
fixture, 32 model layers and full GPU offload. It restored the fourth lane
while retaining the existing lane owners.

## Evidence

- [Full offline test log](../../target/scheduler-fix-validation/test-v2.log)
- [Baseline scheduler lab](../../target/scheduler-fix-validation/baseline-scheduler-lab.tsv)
- [Candidate scheduler lab](../../target/scheduler-fix-validation/candidate-scheduler-lab.tsv)
- [Native recurrent restore test log](../../target/scheduler-fix-validation/native-fourth-lane-v2.log)
- [Cache-on concurrent pairs and native telemetry](../../target/scheduler-fix-validation/replay-cache-v2/validation.json)
- [Solo control, cache-off pair and cache churn](../../target/scheduler-fix-validation/replay-v2-controls/validation.json)
- [Exact Rust patch tested](../../target/scheduler-fix-validation/candidate-v2.patch)
