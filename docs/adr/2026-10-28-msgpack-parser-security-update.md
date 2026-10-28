# Patch the MessagePack parser dependency

Date: 2026-10-28

## Finding and boundary

The [maintainer advisory GHSA-j586-36cw-2gc2](https://github.com/msgpack/msgpack-python/security/advisories/GHSA-j586-36cw-2gc2)
identifies msgpack 1.2.2 as affected by CVE-2026-114613 and 1.2.3 as patched.
The C extension can retain a decoded map value after rejecting its key under
the default `strict_map_key=True`, through both `unpackb` and `Unpacker`.
The advisory also describes stale references in the streaming parser's stack.

Here the dependency chain is the development tool `pip-audit` →
`CacheControl[filecache]` → `msgpack`. App images install the shared lockfile,
but no service source directly deserializes MessagePack. CacheControl does
deserialize its cache-record envelope and treats invalid map keys as a cache
miss. This establishes a dependency/parser exposure; it does not establish an
unauthenticated application endpoint exploit. CacheControl 0.14.4 allows
`msgpack>=0.5.2,<2.0.0`, including the patched release.

## Decision

Recompile the existing lockfile with pip-tools, selecting only
`--upgrade-package=msgpack==1.2.3` and retaining hash enforcement. Preserve all
other package versions and artifact hashes. Verify the msgpack hashes against
the [official PyPI release metadata](https://pypi.org/pypi/msgpack/1.2.3/json).
Keep the compiled backend and its strict-map-key behavior; do not disable
validation or select the slower fallback to hide the defect.

No application API, model, provider, risk policy, access control, Hermes image
pin or parser configuration changes with this dependency update.

## Validation

The focused regression constructs tiny decoded values through `object_hook`
and retains only weak references. Integer and null map keys must still raise
`ValueError`, while constructed values must be released after parser cleanup.
This deterministically exposes the 1.2.2 leak in both APIs without large
allocations, RSS thresholds or timing assertions. The Python fallback may
reject the key before constructing a value and satisfies the same invariant.

Compatibility checks cover a CacheControl serializer binary-body roundtrip,
status and headers, and malformed cache-envelope rejection. Release verification
also requires a hash-enforced isolated lock install, compiled-backend smoke,
`pip check`, focused tests and `pip-audit --strict -r requirements.lock`.
These focused checks supplement the full project's release gates; the streaming
stale-stack defect is covered by the upstream patch and is not separately
reproduced by the weak-reference regression.
