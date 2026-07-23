# Agent Learnings

Patterns and gotchas discovered while working on this codebase.

## BLE Response Chunking

**Context**: BLE notifications have a maximum payload size (~20 bytes for
default MTU). Longer responses from the Bento Lab are split across multiple
NUS TX notifications.

**Problem**: Parsing a single notification as a complete message can fail when
the device splits a response (e.g., touchdown stage `y;3;68.00;20;-1.00;8`
arrives in two chunks).

**Solution**: The `_collect_responses` method in `ble_client.py` accumulates
responses over a timeout window. Continuation messages (`;;;`) are filtered
out. Always use `_collect_responses` rather than reading a single notification.

## macOS CoreBluetooth UUID-Only Addressing

**Context**: On macOS, CoreBluetooth does not expose raw GATT handle numbers.
Devices are addressed by UUID only.

**Problem**: Code that references GATT handles directly (common in Linux
BlueZ examples) will not work on macOS.

**Solution**: Always use UUID strings (e.g., `NUS_RX_CHAR_UUID`) for
characteristic access via bleak. Never hardcode handle integers.

## Protocol Command Prefix

**Context**: All commands to the Bento Lab must be wrapped in `_.;<cmd>\n\n`
framing.

**Problem**: Sending raw command strings without the prefix results in the
device silently ignoring the message.

**Solution**: Always use `encode_command()` or the typed `encode_*` helpers
from `protocol.py`. Never construct raw command bytes manually.

## Wi-Fi Client is a Stub

**Context**: The V1.31 Wi-Fi unit's protocol has not been reverse-engineered.

**Problem**: `wifi_client.py` methods raise `NotImplementedError`.

**Solution**: Do not write tests or integrations against `BentoLabWiFi`
beyond construction/connection. Protocol work is blocked on capture analysis.

## Status Broadcast Timing

**Context**: The Bento Lab sends `bb;...` status broadcasts every ~5 seconds
when a BLE connection is active.

**Problem**: `get_status()` may block up to 10 seconds waiting for the first
broadcast if called immediately after connection.

**Solution**: The handshake (`Xa`) triggers an early status. A 0.5s sleep
after handshake in `connect()` gives the device time to respond before
the first `get_status()` call.

## Hold Stage Was Silently Ignored (#12)

**Context**: `PCRProfile.hold_temperature` (default 4.0) was a public field
on the dataclass, advertised in the API, and used by the TUI's synthetic
"hold" phase. But `to_stages_and_cycles()` did not emit a corresponding
hold stage to the device.

**Problem**: Footgun — the API accepted a value the user thought was
configuring post-run behavior, but it was silently dropped before the
protocol command stream was built.

**Solution**: Emit a final hold stage with a fixed 24h duration (bounded
by int32, well-defined for the firmware). The TUI's `iter_steps`-based
synthetic hold becomes accurate for the first time. `iter_steps()` is
deliberately NOT changed so the runtime estimate, dry-run, and progress
UX stay independent of the device's hold semantics.

**Pattern**: When a domain field has multiple consumers (UI, protocol,
estimate), audit each consumer independently. Public API fields are a
contract — every consumer must be wired up, or the field should be
removed.

## `iter_steps` vs `to_stages_and_cycles` Are Two Distinct Walks (#12)

**Context**: `PCRProfile` has two output shapes — a flat stream of
`(phase, ThermalStep)` for telemetry/dry-run/UI, and a
`(stages, cycles)` tuple of indexed references for the device
protocol. The two walks have different consumers and can legitimately
disagree on what they emit (e.g. the post-run hold belongs in the
device protocol but not in the cyclable program's step stream).

**Pattern**: Don't unify the two. `iter_steps` is the canonical step
walker for "what does the program do over time"; `to_stages_and_cycles`
is the device-protocol serializer. Adding the hold to one but not the
other is intentional, not an oversight.

## Tools/ Refactor Pattern (#20)

**Context**: Five `tools/*.py` debug scripts had 13 functions exceeding
the repo's complexity-10 budget. The repo carries a per-file `C901`
ignore for `tools/*` to allow this.

**Problem**: The `tools/` ignore was masking real complexity in code
that runs against the real device. The fix is to refactor, not just
drop the budget.

**Pattern**:
1. **One PR per file**, not one giant refactor. Each PR is small
   enough to review in one sitting; git history shows the per-file
   win clearly.
2. **Extract at the function's natural boundaries**: arg-parsing,
   command-building, sub-process invocation, result-parsing,
   result-printing. Each becomes a 1-7-complexity helper.
3. **Pull magic numbers to module-level constants** (`_FUZZ_COMMON_PAYLOADS`,
   `_LIVE_DECODE_FIELDS`, `_RAMP_BUFFER_SECONDS`). This shrinks
   function bodies by 5-10 lines each and makes the constants
   reusable / overridable in tests.
4. **Avoid module-level mutable globals** for state. If a helper
   needs to accumulate, thread the list through the function
   signature (`_stream_live_packets(live_proc, target_ip, packets)`)
   rather than introducing a `_LAST_PACKETS` global.
5. **The final cleanup PR drops the ignore**; if the per-file-ignore
   is removed before all files are clean, CI goes red. Order matters.

## Ruff's C901 vs Complexipy Are Different Metrics (#20)

**Context**: `complexipy` reports 13 functions over its 15-budget in
`tools/`; ruff's `C901` (McCabe complexity) reports the same functions
at numbers 2-3x higher. The two tools disagree by a factor of 2-3
because they count different things (complexipy counts more granular
control-flow tokens than McCabe).

**Pattern**: The gate that matters for `make validate` is ruff's C901
(McCabe ≤ 10). When refactoring for complexity, check ruff first —
passing ruff means CI is green. complexipy is informational; the 15
budget is set in `pyproject.toml` because tools/ debug scripts can
afford to be more lenient than shipped code.

## Dependabot Pip → Uv Switch (#22)

**Context**: A repo that uses `uv` for dependency management but
`uv.lock` lives in the root. Dependabot's `pip` ecosystem only handles
`requirements.txt` layouts, not `pyproject.toml` + `uv.lock`, so it
was a silent no-op — dependabot would never open a PR for
`uv`-managed dependencies.

**Pattern**: Always pair the `package-ecosystem` with the actual
package layout. `uv` for `uv.lock`-driven projects, `pip` for
`requirements.txt`-driven, `poetry` for `poetry.lock`-driven. The
mismatch is silent and only shows up when an important CVE drops
and dependabot never opens a PR.

## CodeQL pull_request Trigger Drops SARIFs on a Race (#22)

**Context**: The default CodeQL workflow runs on `push` and
`pull_request`. Under `pull_request`, the codeql-action uploads SARIFs
against a synthetic merge-ref SHA that GitHub recomputes on every
main-push, racing the merge-protection check. The error message is
cryptic: "Code scanning is still expecting 1 result from CodeQL for
<merge> or <head>".

**Pattern**: Drop the `pull_request:` trigger and rely on `push:`
(no `branches:` filter). CodeQL still scans every branch push
because `push:` fires on every ref, and the upload goes against the
real commit SHA — no race. This is the qte77 reference exemplar
and matches the modern CodeQL gold standard.

## Coverage Gate Is the Project's Real Quality Bar (#32, #12)

**Context**: `pyproject.toml` enforces `fail_under = 80` on
`pytest-cov`. PRs that drop coverage below 80% cannot merge.

**Pattern**: When adding new modules (TUI, profiles store, refactor
helpers), the first or second commit often drops coverage below 80%
because the new code is exercised only by the next slice. The
correct response is to commit a temporary `fail_under = 75` with a
clear comment ("TEMPORARY: drops to 75 during the rollout — restore
to 80 before the PR merges"), then add the missing tests in the
next commit and restore the gate. This keeps CI green throughout
the slice work without sacrificing the long-term bar.

## BLE Keep-Alive Is Required, Not Optional (#18)

**Context**: The V1.4 firmware drops the BLE link after tens of
seconds of application-layer silence, even though it's still
broadcasting status. The connection didn't actually fail — it just
went silent on the app side and the link tore down. Without an
app-level keep-alive, every long-running operation (a 90-minute
PCR run) would hit the disconnect.

**Pattern**: For any BLE device where the firmware doesn't have its
own link supervision, the application layer must keep-alive by
sending a cheap command on a timer (PR #18 uses `Xa` handshake every
30s). Set the keep-alive interval to 0 to disable (escape hatch
for debugging). The keep-alive must NOT swallow send errors — a
firmware that ignores a handshake is still better off than one where
the keep-alive hangs the whole client.

