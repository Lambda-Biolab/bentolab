# Scripting the `bentolab` CLI

This document is the **stable contract** for invoking `bentolab` from another
process. If you wrap it in a shell script, a systemd unit, or a downstream
service (e.g. an LLM tool adapter), you can rely on the exit codes, JSON
shapes, and error-stream conventions described here.

The contract is exercised by `tests/test_cli_commands.py` and
`tests/test_cli.py`. Any change to the shapes or codes listed here is a
breaking change and must be reflected in those tests.

## Output modes

Every subcommand supports two output modes:

| Mode | How to select | Goes to | Purpose |
|---|---|---|---|
| **Human** | default (no flag) | stdout (via `rich`) | Pretty, colorized, single-screen summary for terminal users |
| **JSON** | `--json` | stdout (raw JSON) | Machine-readable. One JSON document per line for streaming commands, one for snapshot commands. |

Rules:

- `--json` **never** writes to stderr on the success path.
- All errors (validation, device, transport) go to **stderr** in both modes —
  so a JSON consumer can split `1>payload.json` / `2>errors.log` and always
  parse stdout.
- `--json` output is **newline-delimited** (`\n` terminated) so streaming
  consumers can use line-buffered reads. Even snapshot commands end with a
  newline for symmetry.

## Exit codes

| Code | Meaning | Examples |
|---|---|---|
| `0` | Success | Command ran, output (if any) written to stdout |
| `1` | Generic / environment error | Required optional dep not installed (e.g. TUI extras) |
| `2` | User or validation error | Profile not found, malformed YAML, empty `--device` address, EDITOR missing, run-id not found |
| `3` | Device or transport error | BLE scan/connect failed, command NAK'd by device, timeout |
| `4` | Aborted (by operator) | Reserved for `run` when explicitly stopped via `bentolab stop` |

> **Note on issue #31**: the original issue proposed `1`=device / `2`=validation
> / `3`=no-device. The code uses the more granular scheme above so the
> `aborted` and `generic` cases are distinguishable from the device and
> validation cases. If you are writing a consumer, treat any non-zero exit
> as a failure unless you have a specific reason to branch on the code.

## Per-command contract

### `bentolab scan [--timeout SECONDS] [--json] [--no-remember]`

Discovers nearby Bento Lab BLE devices.

- **Exit 0** — scan completed. `--json` returns `[{address, name, rssi}, ...]`
  (possibly empty array).
- **Exit 3** — BLE stack failure (CoreBluetooth down, permission denied,
  adapter busy).
- An empty result list is **not** an error — exit 0, JSON output `[]`, human
  output prints `No Bento Lab devices found.`

### `bentolab status [--device ADDR] [--json]`

Connects, fetches one status broadcast, disconnects.

- **Exit 0** — `{"address", "running", "block_temperature", "lid_temperature"}`
- **Exit 3** — connect failed, no device remembered, or device NAK'd status
  request.

### `bentolab run <name> [--device ADDR] [--lid °C] [--no-tail] [--json]`

Uploads the named profile and starts the run. With `--no-tail`, starts and
exits immediately. Without it, tails the run until it terminates, emitting
one JSON document per progress event on stdout.

- **Exit 0** — run started (with `--no-tail`) or run completed (without it).
- **Exit 2** — profile not found.
- **Exit 3** — BLE connect or start-run command failed.
- **JSON event shape** (streaming):
  ```json
  {"running": true, "progress": 42, "block": 72.5, "lid": 109.8, "elapsed": 1830}
  ```

### `bentolab monitor [--device ADDR] [--duration SECONDS] [--poll-interval SECONDS] [--json]`

Subscribes to status broadcasts and polls run status periodically until
Ctrl-C or `--duration` elapses.

- **Exit 0** — clean shutdown (Ctrl-C or duration reached).
- **Exit 3** — BLE connect failed.
- **JSON event shapes** (streaming, two `kind`s):
  ```json
  {"kind": "status", "running": true, "block": 72.5, "lid": 109.8}
  {"kind": "run", "running": true, "progress": 42}
  ```

### `bentolab stop [--device ADDR]`

Sends the stop command and exits.

- **Exit 0** — stop command acknowledged.
- **Exit 3** — BLE connect or stop command failed.

### `bentolab profile list [--json]`

Lists profile names in the local store.

- **Exit 0** — always (empty list is not an error).
- **JSON**: `["name1", "name2", ...]`

### `bentolab profile show <name> [--json]`

Prints a single profile.

- **Exit 0** — printed.
- **Exit 2** — profile not found.
- **JSON**: the full `PCRProfile.to_dict()` payload (name, stages, cycles,
  lid_temperature, hold_temperature, etc.).

### `bentolab profile new|edit|delete|import <name>`

Mutate the local store. All return **exit 0** on success and **exit 2** for
any user error (already exists, file not found, YAML parse error, EDITOR
missing). These commands do **not** support `--json`.

### `bentolab logs list [--json]`

Lists run-log filenames in chronological order.

- **Exit 0** — always.
- **JSON**: `["20260724_001530_abc123.jsonl", ...]`

### `bentolab logs show <run-id> [--json]`

Streams one run-log to stdout.

- **Exit 0** — log printed.
- **Exit 2** — run-id not found.
- **JSON mode** is a **raw NDJSON pass-through** of the on-disk file — every
  line is one event (`run_config`, `connected`, `run_started`, `run_progress`,
  `run_finished`). Consumers that need a structured summary of a run should
  use the HTTP API's `GET /runs/{id}/results` instead.

## Environment variables

The CLI respects the same XDG-style overrides as the Python library:

- `BENTOLAB_DATA_DIR` — root for run logs (default: `~/.local/share/bentolab`)
- `BENTOLAB_CONFIG_DIR` — root for devices.json (default: `~/.config/bentolab`)

These are useful in tests and in containerized deployments.

## Examples

### Scan and pick the first device, fail loudly if none found

```sh
set -e
ADDR=$(bentolab scan --json | python -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["address"] if d else "")')
if [ -z "$ADDR" ]; then
  echo "no device" >&2
  exit 1
fi
bentolab status --device "$ADDR" --json
```

### Start a run, then poll once per minute until it terminates

```sh
set -e
bentolab run myprofile --device "$ADDR" --json --no-tail
RUN_ID=$(bentolab logs list --json | python -c 'import json,sys; print(json.load(sys.stdin)[-1].rsplit(".",1)[0])')
while true; do
  STATE=$(bentolab status --device "$ADDR" --json)
  echo "$STATE"
  case "$STATE" in
    *'"running": 0'*) break ;;
  esac
  sleep 60
done
```

### Catch a specific exit code

```sh
bentolab run ghost
case $? in
  2) echo "profile not found — check the name" >&2 ;;
  3) echo "device problem — check BLE" >&2 ;;
  *) echo "unexpected" >&2 ;;
esac
```

## Stability

The exit codes, JSON output shapes, and error-stream conventions in this
document are part of `bentolab`'s public contract. Any change must be
announced in `CHANGELOG.md`, paired with a migration note, and reflected in
`tests/test_cli_commands.py` / `tests/test_cli.py` so consumers can detect
the breaking change at CI time.
