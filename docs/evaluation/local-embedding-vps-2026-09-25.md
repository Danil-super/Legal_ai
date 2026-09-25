# Local embeddings: VPS feasibility probe, 2026-09-25

## Verdict

`intfloat/multilingual-e5-small` INT8 runs on the current VPS CPU. It is suitable
for a bounded pilot, not yet cleared for an always-on production deployment.
A 512 MiB container failed on a 512-token passage (cgroup `oom_kill=1`). A
640 MiB container completed, but peak process RSS reached 624.9 MiB: too close
to its cap to recommend that limit for a service with HTTP/concurrency overhead.

No embedding endpoint, production configuration, corpus status or bot service
was changed. Synthetic embeddings were discarded; none were inserted into PostgreSQL.
The temporary model/dependency installation was removed after measurements;
there is no persistent embedding process on the VPS.

## Baseline

- VPS: 2 vCPU, Intel Xeon Icelake, AVX512/VNNI supported.
- RAM: 1,966 MiB total; 1,057 MiB available before the probe; no swap.
- Disk: 4.2 GiB available before the probe.
- Core and gateway: approximately 106 MiB and 55 MiB at baseline.
- Model, tokenizer and isolated Python dependencies occupied 376 MiB on disk.
- After the probe: 1,049 MiB RAM available; core and gateway remained healthy,
  with unchanged uptimes. This is a health check, not a Telegram latency load test.

## Artifact and method

Official model repository: <https://huggingface.co/intfloat/multilingual-e5-small>.
Revision: `614241f622f53c4eeff9890bdc4f31cfecc418b3`.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `onnx/model_qint8_avx512_vnni.onnx` | 118346824 | `dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88` |
| `onnx/tokenizer.json` | 17082730 | `0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39` |

Runtime: Python 3.13, ONNX Runtime 1.23.2, tokenizers 0.22.2, NumPy 2.5.3.
Dependencies were installed in a disposable directory, not in the application.
Test used the existing Legal Core image, a read-only artifact mount, no network,
no database credentials, non-root UID, no capabilities, one vCPU, one ONNX
intra/inter-op thread and batch size one. CPU memory arena was disabled.
No PyTorch installation, GPU, remote model code or patient data was used.

The model-card pooling recipe was used: attention-mask mean pooling, L2
normalisation, 384 dimensions, `query: ` / `passage: ` prefixes and at most
512 tokens. Long input was deliberately synthetic and repetitive; truncation in
this diagnostic does **not** authorise truncating legal evidence in production.

## Measurements

Each completed case used 20 serial calls after loading and one query warm-up.
Times cover tokenisation, inference, pooling and normalisation, not HTTP, database
retrieval, LLM generation or end-to-end bot latency. RSS is the cumulative process
high-water mark, not container memory and not steady-state resident memory.

| Container cap | Case | Median | Maximum | Peak RSS | Result |
| --- | --- | ---: | ---: | ---: | --- |
| 512 MiB | 11-token query | 5.56 ms | 9.19 ms | 463.2 MiB | completed |
| 512 MiB | 512-token passage | — | — | — | OOM, no completed batch |
| 640 MiB | 11-token query | 3.56 ms | 4.64 ms | 465.8 MiB | completed |
| 640 MiB | 512-token passage | 206.51 ms | 348.99 ms | 624.9 MiB | completed |

Cold loading took 9.01 / 10.12 seconds respectively. These are feasibility samples,
not a proven speedup, capacity limit, p95 SLA or a legal retrieval quality evaluation.
A single synthetic related passage outranked an unrelated one (cosine 0.872 versus
0.768); this is only a sanity check, not a relevance acceptance test.

## Outstanding gates

1. Prefer 4 GiB RAM for a permanent co-located service; the 2 GiB host currently
   has little headroom for a roughly 625 MiB process plus API overhead, deployment
   builds and concurrent bot work. No server resize was performed.
2. A pilot can investigate a 768 MiB cap, single-worker queue and background
   indexing, but this cap and concurrent bot workload have **not** been tested.
3. Implement explicit query/document embedding modes. The current generic
   embedding adapter does not add E5's different prefixes. Do not point it at E5
   unchanged or mix new vectors with another model/revision.
4. Define document chunking with complete source/article provenance; do not
   silently discard legal text beyond the token limit. Test Russian legal queries
   with a lawyer-reviewed relevance set, including exact article/number lookups.
5. Keep lexical fallback, approved-version/date filters and fail-closed evidence
   rules. Production inspection found zero approved fragments and zero vectors;
   indexing cannot substitute for legal-editor review.
6. Pin and security-audit the complete runtime dependency set before shipping.
   This disposable dependency installation is not a production lockfile.

## Related import repair (local only)

The deployed importer rejected safe RTF files without a recognised Garant URL;
its retry identity also included the new receipt timestamp. Local fixes preserve
unknown-source files only as `METADATA_REQUIRED` review materials and retain the
original receipt time on retries. Parsing the supplied package now accepts 58
files (51 legal copies, 7 clinical references; 69,305,904 bytes). This does not mean
they have been imported on the server, promoted to legal versions or approved.

Verification: 959 unit tests passed, 81 integration tests skipped in the ordinary
run; 10 focused tests subsequently passed against a separately created disposable
PostgreSQL database, including retry persistence. Ruff and Legal Core mypy passed.
Compose validation passed with dummy required environment values. Real secrets
were not loaded into local tooling. The disposable database was stopped afterward.
