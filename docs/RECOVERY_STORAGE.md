# Recovery storage and inventory

Recovery is private workspace state. It never overwrites a source document;
restoration creates dirty buffers and retains the saved disk fingerprints for
the normal guarded Save operation.

## Format and limits

New snapshots use `mdbm-recovery/3.0`. A JSON manifest records the workspace,
document identities, paths, selections, tile memories, baseline fingerprints,
and immutable content references. UTF-8 text is stored in
`content-<sha256>.bin` files, shared when snapshots contain identical text.
The digest and exact byte length are checked on every read. A fresh write can
repair a corrupt content file when the current buffer provides the matching
original text.
During v3 publication, documents sharing a content digest must declare exactly
the same byte length. Conflicting sizes are rejected before any publication or
partial-discard replacement; this prevents an inconsistent manifest from
replacing a restorable snapshot. Correctly shared content remains deduplicated.

The writer and reader enforce the same limits: at most 32 documents, each
within the configured `application.max_file_mib` UTF-8 byte budget, and a
manifest no larger than 16 MiB. Text no longer expands into ASCII-escaped JSON
or competes for a reader budget sized for only eight documents. Metadata that
exceeds the manifest budget is rejected before publication, retaining the
previous snapshot.

The recovery pool has a hard limit of four complete maximum-size snapshots,
including four 16 MiB manifest allowances. At the default 16 MiB document
limit this is 2,112 MiB. Before writing, MDBM accounts for all manifests and
content files, deduplicates content, and reclaims only unreferenced blobs. If
the new snapshot cannot fit, the old manifest remains intact and the UI
reports how to open the recovery inventory. Saved archives do not expire or
get evicted automatically.

Legacy `mdbm-recovery/1.0` and `/2.0` snapshots remain readable. A streaming
JSON reader processes fields and documents individually, with an encoded
envelope large enough for all 32 documents and JSON escaping. Inventory
inspection skips their text strings instead of allocating every buffer.
Restoration checks each document's decoded UTF-8 size and digest. Subsequent
publication uses v3; there is no destructive bulk migration at startup.
Captured non-text metadata is limited to 16 MiB across the whole envelope,
with at most 64 fields per envelope/document object. Oversized metadata remains
listed as an unreadable archive and is retained until explicitly discarded.

## Inventory and retention

**File → Recovery inventory**, also available through the command palette,
shows 24 snapshots per page. Discovery inspects every matching snapshot name
before ordering by filesystem publication time; it does not truncate the
directory before sorting. Rows show date, document count, content size,
workspace and live/saved state. Enter and clicking a row use the same inspect
command. Previous/next page controls remain reachable through the same command
system.

Inspection offers Restore, explicit Discard and Back. A snapshot from another
workspace explains that its root must be opened to restore it. Unreadable
archives are visible and can be explicitly discarded by the primary instance;
an invalid archive is never silently treated as an empty snapshot. Live
snapshots belonging to another instance remain protected. The current
instance's active write slot is visible but cannot be discarded through the
inventory. An inspected, readable snapshot is also a versioned selection:
when its metadata changes before the guarded discard or partial replacement,
MDBM refuses that stale confirmation and retains the newer archive. This
check occurs while holding `recovery.guard` and before any rewrite or deletion.
The explicit **Discard unreadable snapshot** command binds its confirmation
to the inspected regular file's device/inode, size, and nanosecond modification
and change timestamps. The identity is captured with the metadata read while
holding `recovery.guard` and compared under that same guard immediately before
a confirmed deletion. It refuses a replacement even if the successor is also
unreadable (or has become readable). Missing, inaccessible and non-regular
entries have no valid destructive confirmation; refresh inventory instead.
This bounded identity check protects cooperating MDBM publications and routine
external replacements, but is not a cryptographic digest or a guarantee
against hostile unguarded writers that forge filesystem attributes. Direct
programmatic invalid-archive deletion without an expected identity remains an
explicit unversioned operation and must not be used for delayed UI intents.
Callers intentionally acting on the current snapshot under the guard may still
use that API without a version precondition.

File-specific startup restoration preserves unselected sibling documents.
The original archive is consumed only after the new recovery publication
succeeds. A failed load, validation, capacity check, sibling publication or
current publication retains the original archive. Restore is held while file
saves are pending so an asynchronous save cannot publish under a replaced
document model.

Legacy single-document recovery uses the same path targeting, dirty-conflict
and capacity checks as a multi-document bundle. It cannot replace an unrelated
active buffer. When a complete 32-document recovery set needs the last slot,
MDBM may retire its own untouched automatic Welcome/empty placeholder during
the same validated update. User-created or edited untitled documents retain
their capacity protection.

Recovery loading also constructs and validates document models, line indexes
and content hashes in the disk worker. The completion handler checks the
current workspace, pending saves, dirty conflicts and capacity before applying
those prepared models; it does not repeat full-document indexing or hashing.

Unreferenced **regular** content files are collected while holding the
publication guard. A directory, FIFO, device or symlink that merely has a
`content-<sha256>.bin`-shaped name is not treated as a deletable blob: it is
preserved rather than aborting unrelated snapshot publication. A referenced
non-regular blob remains unreadable and fails closed on restore/publication.
An unreferenced regular blob that cannot be *unlinked* (for example because
Windows still has an open file handle) is left in place and continues to
count toward the storage quota. A deduplicated diagnostic records deferred
cleanup, but publication may still proceed if the charged bytes fit.
In contrast, an orphan that cannot even be *statted* aborts publication
rather than silently undercounting its unknown size. Publication's storage
accounting is strict even if garbage collection was already suspended by an
unreadable manifest: inaccessible blob or archive metadata never disappears
from the quota calculation. An unsafe non-regular archive likewise prevents
publication until inspected or explicitly removed. The interactive inventory
remains best-effort so it can still display other archives and diagnostics.
If any archive cannot be inspected, automatic collection stops conservatively
because that archive might still reference useful content. Discarding the
unreadable archive explicitly makes later collection possible. The inventory
also exposes an explicit **Reclaim unreferenced recovery content** action.

## Ownership and publication

The primary instance holds a kernel lock on persistent `owner.guard` for its
lifetime: `flock` on POSIX and a byte-range lock on Windows. The guard is never
unlinked. Stale metadata inspection and new ownership publication occur under
that same guard, so simultaneous reapers cannot unlink a newly acquired
owner's metadata. Ownership JSON is fully written and flushed before an atomic
no-clobber hard link publishes it. A crash cannot leave a partially written
new ownership record.

On POSIX, both kernel guard files are opened with `O_NONBLOCK` and
`O_NOFOLLOW` before their descriptor types are validated. This avoids
blocking on an externally substituted FIFO/device at `owner.guard` or
`recovery.guard` before the regular-file test runs. This flag does **not**
make requested blocking kernel-lock acquisition nonblocking: lock contention
still follows the caller's explicit blocking policy. Special-file guards
fail closed; existing snapshots are not removed. Windows retains descriptor
validation and its documented path-level reparse-point limitations.

Modern ownership records identify this protocol. Acquiring the unheld kernel
guard proves a modern owner has gone even if its recorded PID was reused.
Unversioned legacy records still receive the conservative, non-signalling
process-liveness check. Unknown or inaccessible legacy owners are retained.

Other instances use their own recovery slots and leases. A *missing*
tokenized lease permits the owner to treat a foreign archive as stale, but an
*existing unreadable or inconsistent* lease is not evidence that the process
exited: the archive remains protected. This includes dangling lease symlinks:
MDBM checks whether the directory entry exists without following its target,
and treats filesystem metadata/permission errors as unknown ownership rather
than proof of absence. The same fail-closed check applies to
explicit removal of a damaged foreign archive. Only a demonstrably dead
valid process PID or an actually removed lease permits that reclamation;
invalid/unknown PIDs are preserved conservatively. A lease's `primary`
flag is not independent proof that its process exited: a live follower whose
lease is incorrectly marked primary remains protected even when `lock.json`
names the actual primary. Stale primary-marked leases with a demonstrably dead
PID remain reclaimable. This conservatively retains some PID-reuse cases rather
than risk overwriting unsaved text.
Every manifest/blob
publication, restore read, removal and garbage collection uses a separate
`recovery.guard`, serializing these operations across primary and secondary
instances. This prevents collection from deleting a blob between another
instance's content write and manifest publication. The UI queues these disk
operations with its persistence worker; only completion handlers mutate live
workspace state.

A confirmed **Restore** selection is versioned as well as an explicit
discard: its disk worker compares the previously inspected manifest metadata
with the current metadata while holding `recovery.guard`, before hydrating
text or updating any open buffer. If another instance has replaced that
archive, restore rejects the stale selection; it neither loads the replacement
nor consumes or rewrites the archive. This applies to v3 and bounded legacy
v1/v2 snapshots. A matching legacy archive may need a second bounded JSON
read for metadata verification. The guard covers the read transaction, not the
subsequent UI scheduling delay; a cooperating writer can still publish a new
revision *after* the verified read. In that case, subsequent guarded archive
consumption rechecks its original version and cannot delete the successor.

A live owner's archive is never replaceable by another instance through the
storage publication API. A secondary instance may update only its own
lease-token recovery slot; any existing unreadable archive is preserved for
explicit inspection rather than silently overwritten. Partial archive
replacement holds the publication guard from ownership verification through
manifest publication and orphan collection. Explicit discard and the inventory
UI's mutability check also honor a live follower lease identified by the
tokenized archive *filename*, independently of the manifest's owner fields.
An otherwise parseable archive with missing or inconsistent owner metadata
cannot be discarded or partially replaced while its original follower is live.
The primary may explicitly discard it only after that follower's lease is
demonstrably released. See
[Recovery transaction continuation](RECOVERY_TRANSACTION_CONTINUATION_2026-11-03.md).

The generic `StateStore.write` entry point also enforces this boundary. Names
matching `recovery.json` or `recovery-<token>.json` are accepted only with
supported recovery envelopes; invalid, non-object and future-version payloads
raise an explicit error without creating or replacing a file. They can never
fall through to ordinary JSON publication and bypass `recovery.guard` or the
live-owner check. Only the kernel-lock owner may publish the shared
`session.json`; secondary instances still read the session and publish their
own recovery slots, but cannot overwrite the primary's session on disk.
`lock.json` and tokenized `lease-*.json` records are private to the store
lifecycle and cannot be written through generic `StateStore.write`, because
forged owner metadata could make a live recovery archive appear reclaimable.
The primary's ordinary session writes remain supported.


Content-addressed `content-<sha256>.bin` files are likewise excluded from generic
JSON publication. These bytes can be created or repaired only while publishing
a validated recovery manifest under `recovery.guard`; an ordinary state write
cannot corrupt shared text referenced by multiple recovery archives.

Recovery manifest, content and ordinary state-JSON reads validate the
opened descriptor as a regular file, rather than trusting a path stat before
opening. This includes `lock.json` during owner startup and tokenized leases
during recovery-slot ownership checks: a present but unreadable special-file
lease must still be treated as possibly live, without indefinitely holding
`recovery.guard`. POSIX opens also use non-blocking and no-follow flags so
FIFOs/devices and replacement symlinks cannot stall or redirect these reads.
Windows retains path-level symlink/junction checks and descriptor validation;
atomic no-follow reparse-point opening is not claimed there. Unsafe material
fails closed for inspection/publication, without silently replacing the
manifest. POSIX regressions cover FIFO manifests and content blobs, while
the ordinary Windows regression and installed-wheel matrix remains required.
Additional POSIX regressions cover FIFO lease records and owner startup; no
claim is made that untrusted external processes cannot mutate the state folder.
