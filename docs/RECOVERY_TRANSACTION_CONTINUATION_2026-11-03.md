# Recovery ownership and partial replacement hardening (3 November 2026)

Follow-on to [PR #8](https://github.com/techrote/mdbm/pull/8), whose published source and CI are unchanged.

## Finding

The existing recovery publication guard serialized raw writes and collection,
but `write_recovery` did not validate the live owner of an existing snapshot.
A secondary `StateStore` could therefore overwrite the primary's
`recovery.json` through the storage API. Partial archive discard validated
the owner under `recovery.guard`, then released that guard before publishing
the shortened archive, leaving a cross-process change-of-owner window.

## Change

- Shared validation and encoding in `_prepare_recovery`; no persistent
  mutation occurs until the candidate is valid.
- Under the publication guard, disallow a secondary from writing arbitrary
  slots, prevent overwrites of another *live* owner, and conservatively reject
  a pre-existing unreadable archive instead of destroying recovery evidence.
- Publish partial archive replacements under the same guard used for
  inspection, ownership checks, content publication, quota accounting and
  orphan collection.
- Keep secondary writes to their own tokenized slots, primary preservation,
  content-addressed blobs and previous-snapshot-on-failure behavior.
- Add deterministic coverage for the live-primary overwrite, own-slot
  update, unreadable archive preservation, guard continuity and injected
  manifest-write failure.

## Qualification

Run `python -m pytest -q tests/test_recovery_transaction.py
tests/test_recovery_storage.py tests/test_recovery_inventory.py
tests/test_recovery_background_lifecycle.py tests/test_state_lock_processes.py`
plus full Linux/Windows matrix and installed-wheel smoke for the precise
commit. This stacked PR does **not** alter the qualified PR #8 head and
must not merge before its base and exact-head checks are accepted.

## Generic publication dispatch follow-up

Source review identified a second ownership bypass: previously the generic
`StateStore.write` delegated to `write_recovery` *only* for recognized
recovery schemas; malformed payloads and unknown schemas fell through to
`_write_json`, which uses neither the publication guard nor the live-owner
check. An invalid payload from a secondary could therefore overwrite the
primary's protected archive outright. All recovery filenames now enter the
guarded recovery-validation path or fail closed before any write. Regression
cases cover malformed and unknown schemas, non-object payloads, primary-owned
and another instance's live archive, and ordinary session JSON unaffected.


## Administrative state publication boundary

A further generic-dispatch audit found that the session and ownership records
still lacked equivalent isolation. A secondary could call `StateStore.write`
to replace the primary's `session.json`, and either instance could replace
`lock.json` or a tokenized lease record, falsifying the liveness evidence
used to protect another instance's recovery archive.

The public writer now rejects administrative owner/lease names for every
instance, and permits `session.json` only while holding the primary kernel
owner lock. Store creation/close still manage owner and lease records through
their internal lifecycle; primary session writes and each instance's recovery
publication remain unchanged. Tests cover session preservation, attempts to
forge the primary and follower's owner/lease records, the live-follower
recovery boundary, and repeated legitimate primary session writes.


## Reserved recovery slot before first publication

Review of the guarded writer exposed one more cross-instance gap: owner
validation formerly returned immediately when the target path did not exist.
A primary instance could claim a live secondary's tokenized recovery name
before that secondary had written any snapshot. The secondary's legitimate
first save would then be rejected as an overwrite of a foreign live owner,
leaving unsaved work without its expected recovery slot.

The writer now checks the name's token against the active lease before the
file-existence shortcut, under the same recovery publication guard. A live
follower retains its reserved slot from initialization onward; a missing or
dead lease does not indefinitely block an archive name. Two regressions
exercise the first-snapshot reservation, repeated follower updates and
stale-lease reuse. This closes a cooperative API ownership gap; it does not
claim to defend against malicious direct filesystem mutation by another
process with the same OS permissions.

## Shared recovery-blob dispatch boundary

The same generic writer also accepted `content-<sha256>.bin` paths, because the
internal path validator permits recovery blobs and `StateStore.write` only
special-cased recovery JSON names. An arbitrary JSON write to that blob would
corrupt all manifests sharing it, without the publication guard. Generic blob
writes now fail before any filesystem mutation. Valid content creation and
repair remain private to the guarded recovery publisher. A shared-blob
regression exercises both primary and secondary write attempts and verifies
that both archives remain hydratable.

## Invalid PID versus exited owner: CI qualification correction

The first reservation regression passed, but the stale-lease test used PID
`-1` as an example of a dead owner. That is **not** a valid PID; the existing
non-signalling liveness helper intentionally treats invalid, inaccessible and
otherwise unverifiable owner identities as potentially live. The new
reservation check correctly rejected the write, and the previous exact-head
matrix failed one test on each of Ubuntu 3.11, Ubuntu 3.13 and Windows 3.13.
Because that failure occurred in the regression phase, wheel checks were
skipped on that head ([run 41630020273](https://github.com/techrote/mdbm/actions/runs/41630020273)).

The repaired test creates and reaps a real short-lived subprocess and uses
that positive, native evidence of termination when exercising stale-lease
reuse. A parameterized companion verifies that malformed/unknown PID values
(including `-1`) continue to reserve their slots. Neither production
liveness logic nor ownership-failure behavior was relaxed. Future assertions
must distinguish a demonstrably exited PID from an unknown one. Exact-head
Linux/Windows tests and installed-wheel checks must pass before merging.
