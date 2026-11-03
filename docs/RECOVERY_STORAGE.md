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

Startup's deferred-recovery scan uses a newest-first keyset cursor consisting
of the archive's modification time and filename, rather than repeatedly
advancing an offset into a changing directory. Removal of a newer archive by
another instance between page reads therefore cannot skip an older candidate.
An archive created later *above* the cursor is visible on the next startup
or through the interactive inventory. The legacy `StateStore.recovery_candidates()` compatibility iterator also
uses the keyset cursor, preventing a concurrent discard from skipping
complete older archives during a multi-page scan. The interactive
Previous/Next controls remain positional; if concurrent writers change the
directory, their pages may shift, but no archive is automatically deleted as a
consequence.

Inspection offers Restore, explicit Discard and Back. A snapshot from another
workspace explains that its root must be opened to restore it. Unreadable
archives are visible and can be explicitly discarded by the primary instance;
an invalid archive is never silently treated as an empty snapshot. Live
snapshots belonging to another instance remain protected. The current
instance's active write slot is visible but cannot be discarded through the
inventory.

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

Unreferenced content is collected while holding the publication guard. If any
archive cannot be inspected, automatic collection stops conservatively because
that archive might still reference useful content. Discarding the unreadable
archive explicitly makes later collection possible. The inventory also exposes
an explicit **Reclaim unreferenced recovery content** action.

## Ownership and publication

The primary instance holds a kernel lock on persistent `owner.guard` for its
lifetime: `flock` on POSIX and a byte-range lock on Windows. The guard is never
unlinked. Stale metadata inspection and new ownership publication occur under
that same guard, so simultaneous reapers cannot unlink a newly acquired
owner's metadata. Ownership JSON is fully written and flushed before an atomic
no-clobber hard link publishes it. A crash cannot leave a partially written
new ownership record.

Modern ownership records identify this protocol. Acquiring the unheld kernel
guard proves a modern owner has gone even if its recorded PID was reused.
Unversioned legacy records still receive the conservative, non-signalling
process-liveness check. Unknown or inaccessible legacy owners are retained.

Other instances use their own recovery slots and leases. Every manifest/blob
publication, restore read, removal and garbage collection uses a separate
`recovery.guard`, serializing these operations across primary and secondary
instances. This prevents collection from deleting a blob between another
instance's content write and manifest publication. The UI queues these disk
operations with its persistence worker; only completion handlers mutate live
workspace state.
