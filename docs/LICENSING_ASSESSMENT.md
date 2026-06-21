# Licensing Assessment — bentolab

Assessment of IP and licensing considerations for making this repository public.

## Verdict: MEDIUM RISK — publishable after git history cleanup

The Python BLE client is original code. However, git history contains vendor
firmware and BLE captures that were committed then removed from the working tree.

## Legal Basis

Reverse engineering of BLE protocols for interoperability is protected under:

- **US:** DMCA §1201(f) interoperability exception
  — https://www.law.cornell.edu/uscode/text/17/1201
- **EU:** Software Directive 2009/24/EC, Article 6 (decompilation for
  interoperability)
  — https://eur-lex.europa.eu/eli/dir/2009/24/oj

BLE traffic observation from an owned device is analogous to network traffic
analysis — no access controls are circumvented.

## Items to Address Before Public Release

### Mandatory

1. **Rewrite git history** to purge blobs that were removed from working tree
   but remain in pack objects:
   - `firmware/bg-p000-1.zip` — Nordic DFU firmware package (Bento Bioworks copyright)
   - `captures/sessions/*.jsonl` — raw BLE session logs
   - `captures/ble/protocol_transcript.json`
   
   Use `git filter-repo` or BFG Repo-Cleaner:
   — https://github.com/newren/git-filter-repo
   — https://rtyley.github.io/bfg-repo-cleaner/

2. ~~**Add a LICENSE file** — currently missing entirely. Recommended: MIT or~~
   **DONE.** Apache License 2.0 with a `NOTICE` file. See `LICENSE` and `NOTICE`
   at the repo root. Apache was preferred over MIT for the explicit patent
   grant, retaliation clause, change-tracking requirement, and trademark scope
   (Section 6) — all of which matter for a reverse-engineered library that
   names third-party hardware. Original recommendation noted: MIT or
   Apache-2.0 for library code.

### Recommended

3. **Soften RE provenance language** in docs — replace "APK decompilation"
   and "libapp.so string extraction" with "protocol analysis" or "black-box
   observation." The code is clean, but explicit decompilation references
   increase scrutiny.

4. **Add disclaimer to README:**
   > Not affiliated with Bento Bioworks Ltd. Bento Lab is a trademark of
   > Bento Bioworks. Protocol information was determined through
   > interoperability analysis of BLE communication.

## What Is Already Clean

- All Python code is original (not derived from decompiled APK)
- No vendor binaries in current working tree
- No EULA or restrictive terms found (vendor T&C page returned 403)
- Bento Bioworks does not publish a public API or SDK, strengthening the
  interoperability justification

## References

- Bento Bioworks (vendor): https://bento.bio
- Nordic UART Service spec: https://docs.nordicsemi.com/bundle/ncs-latest/page/nrf/libraries/bluetooth_services/services/nus.html
- git-filter-repo: https://github.com/newren/git-filter-repo
