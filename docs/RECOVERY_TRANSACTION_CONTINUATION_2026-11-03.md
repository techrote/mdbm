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
