from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter_ns

from app.evaluation.pgvector_ann_experiments import (
    PgvectorAnnExperimentRunner,
    PreparedHnswTarget,
)
from app.evaluation.pgvector_ann_models import HnswConfiguration
from app.evaluation.pgvector_ann_reporting import write_pgvector_ann_report
from app.evaluation.retrieval_dataset import RetrievalDatasetError, load_retrieval_dataset
from app.evaluation.retrieval_models import (
    RetrievalEvaluationMode,
    RetrievalEvaluationThresholds,
)
from app.evaluation.retrieval_runtime import (
    RETRIEVAL_EVALUATION_AS_OF_DATE,
    RetrievalJudgmentError,
    corpus_sha256,
    retrieval_evaluation_access_context,
    validate_retrieval_judgments,
)
from app.portfolio.runtime import DeterministicLexicalEmbeddingProvider
from app.rag.embeddings import BGEEmbeddingProvider, DEFAULT_BGE_MODEL_NAME
from app.rag.pgvector_hnsw_experiment import PgVectorHnswExperimentIndex
from app.rag.pgvector_index import PgVectorIndex
from app.rag.policy_chunker import chunk_policy_directory
from app.rag.policy_retriever import PolicyRetriever
from app.security import authorized_chunk_ids

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATASET = _PROJECT_ROOT / "tests" / "evaluation" / "retrieval_test_cases.jsonl"
_DEFAULT_POLICY_DIRECTORY = _PROJECT_ROOT / "data" / "policies"
_DEFAULT_OUTPUT_DIRECTORY = _PROJECT_ROOT / "artifacts" / "evaluation"
_DEFAULT_DSN = "postgresql://policy_agent:local-development-only@127.0.0.1:5432/policy_agent"
_DEFAULT_CONFIGS = ("8:32:20", "16:64:40", "16:64:80")
_DEFAULT_CONFIGURATION = "16:64:40"


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_hnsw_configuration(value: str) -> HnswConfiguration:
    try:
        parts = tuple(int(item) for item in value.split(":"))
    except ValueError as exc:
        raise ValueError("HNSW config must use m:ef_construction:ef_search") from exc
    if len(parts) != 3:
        raise ValueError("HNSW config must use m:ef_construction:ef_search")
    return HnswConfiguration(m=parts[0], ef_construction=parts[1], ef_search=parts[2])


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare exact pgvector search with authorization-isolated HNSW indexes."
    )
    parser.add_argument(
        "--mode",
        choices=[item.value for item in RetrievalEvaluationMode],
        default=RetrievalEvaluationMode.OFFLINE.value,
    )
    parser.add_argument("--hnsw-config", nargs="+", default=list(_DEFAULT_CONFIGS))
    parser.add_argument("--default-config", default=_DEFAULT_CONFIGURATION)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repetitions", type=_positive_integer, default=3)
    parser.add_argument("--minimum-ann-recall-at-5", type=float, default=0.95)
    parser.add_argument("--minimum-recall-at-5", type=float, default=0.80)
    parser.add_argument("--minimum-mrr-at-5", type=float, default=0.80)
    parser.add_argument("--minimum-ndcg-at-5", type=float, default=0.80)
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--policy-dir", type=Path, default=_DEFAULT_POLICY_DIRECTORY)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--dsn", default=os.getenv("RAG_PGVECTOR_DSN", _DEFAULT_DSN))
    parser.add_argument(
        "--collection",
        default="enterprise-policy-ann-experiment",
        help="Dedicated experiment collection; do not point this at production data.",
    )
    parser.add_argument("--embedding-model", default=DEFAULT_BGE_MODEL_NAME)
    parser.add_argument("--device", default=None)
    parser.add_argument("--embedding-batch-size", type=_positive_integer, default=32)
    parser.add_argument("--connect-timeout-seconds", type=float, default=5.0)
    return parser.parse_args(argv)


def _resolve_configurations(
    values: list[str],
    *,
    default_value: str,
) -> tuple[tuple[HnswConfiguration, ...], HnswConfiguration]:
    configurations = tuple(parse_hnsw_configuration(value) for value in values)
    identities = tuple(item.identity for item in configurations)
    if len(set(identities)) != len(identities):
        raise ValueError("HNSW configurations must be unique")
    default = parse_hnsw_configuration(default_value)
    if default.identity not in identities:
        raise ValueError("default configuration must be included in --hnsw-config")
    return configurations, default


def _experiment_collection(base_name: str, mode: RetrievalEvaluationMode, dimension: int) -> str:
    normalized = base_name.strip()
    if not normalized:
        raise ValueError("collection must not be blank")
    return f"{normalized}-{mode.value}-{dimension}d"


def _run(args: argparse.Namespace) -> int:
    configurations, default = _resolve_configurations(
        args.hnsw_config,
        default_value=args.default_config,
    )
    if args.warmups < 0:
        raise ValueError("warmups must not be negative")
    mode = RetrievalEvaluationMode(args.mode)
    dataset = load_retrieval_dataset(args.dataset)
    chunks = tuple(chunk_policy_directory(args.policy_dir))
    access_context = retrieval_evaluation_access_context()
    validate_retrieval_judgments(
        dataset.cases,
        chunks,
        access_context=access_context,
        as_of_date=RETRIEVAL_EVALUATION_AS_OF_DATE,
    )
    allowed_ids = authorized_chunk_ids(
        chunks,
        access_context,
        as_of_date=RETRIEVAL_EVALUATION_AS_OF_DATE,
    )
    authorized_chunks = tuple(chunk for chunk in chunks if chunk.chunk_id in allowed_ids)

    if mode is RetrievalEvaluationMode.BGE:
        embedding_provider = BGEEmbeddingProvider(
            model_name=args.embedding_model,
            device=args.device,
            batch_size=args.embedding_batch_size,
        )
        embedding_identity = args.embedding_model
        external_model_calls = True
    else:
        embedding_provider = DeterministicLexicalEmbeddingProvider()
        embedding_identity = "deterministic_hashed_lexical_v1"
        external_model_calls = False

    source_collection = _experiment_collection(args.collection, mode, embedding_provider.dimension)

    exact_index = PgVectorIndex.from_dsn(
        args.dsn,
        dimension=embedding_provider.dimension,
        collection_name=source_collection,
        connect_timeout_seconds=args.connect_timeout_seconds,
    )
    hnsw_indexes: list[PgVectorHnswExperimentIndex] = []
    try:
        exact_index.initialize_schema()
        exact_target = PolicyRetriever(
            embedding_provider=embedding_provider,
            chunks=chunks,
            vector_index=exact_index,
        ).restrict(access_context, as_of_date=RETRIEVAL_EVALUATION_AS_OF_DATE)

        prepared_targets: list[PreparedHnswTarget] = []
        for configuration in configurations:
            index = PgVectorHnswExperimentIndex.from_dsn(
                args.dsn,
                dimension=embedding_provider.dimension,
                source_collection=source_collection,
                connect_timeout_seconds=args.connect_timeout_seconds,
            )
            hnsw_indexes.append(index)
            started = perf_counter_ns()
            index.prepare(
                authorized_record_ids=allowed_ids,
                m=configuration.m,
                ef_construction=configuration.ef_construction,
                ef_search=configuration.ef_search,
            )
            build_ms = (perf_counter_ns() - started) / 1_000_000
            target = PolicyRetriever(
                embedding_provider=embedding_provider,
                chunks=authorized_chunks,
                vector_index=index,
                index_vectors=False,
            ).restrict(access_context, as_of_date=RETRIEVAL_EVALUATION_AS_OF_DATE)
            prepared_targets.append(PreparedHnswTarget(configuration, target, build_ms))

        report = PgvectorAnnExperimentRunner(
            exact_target=exact_target,
            hnsw_targets=prepared_targets,
            default_configuration=default,
            evaluation_mode=mode,
            embedding_provider=embedding_identity,
            source_collection=source_collection,
            requested_device=args.device,
            embedding_batch_size=args.embedding_batch_size,
            external_model_calls=external_model_calls,
            dataset_sha256=dataset.sha256,
            corpus_sha256=corpus_sha256(chunks),
            warmup_iterations=args.warmups,
            measured_repetitions=args.repetitions,
            minimum_ann_recall_at_5=args.minimum_ann_recall_at_5,
            thresholds=RetrievalEvaluationThresholds(
                minimum_recall=args.minimum_recall_at_5,
                minimum_mrr=args.minimum_mrr_at_5,
                minimum_ndcg=args.minimum_ndcg_at_5,
            ),
        ).run(dataset.cases)
        paths = write_pgvector_ann_report(report, args.output_dir)
        print(
            json.dumps(
                {
                    "quality_gate_passed": report.quality_gate_passed,
                    "evaluation_mode": report.evaluation_mode.value,
                    "source_collection": report.source_collection,
                    "security_boundary": report.security_boundary,
                    "default_configuration": report.default_configuration.identity,
                    "pareto_configurations": report.pareto_configurations,
                    "points": [
                        {
                            "backend": point.backend,
                            "configuration": (
                                point.configuration.identity if point.configuration else "exact"
                            ),
                            "ann_recall_at_5": point.ann_recall_at_5,
                            "judged_recall_at_5": point.judged_recall_at_5,
                            "mrr_at_5": point.mrr_at_5,
                            "ndcg_at_5": point.ndcg_at_5,
                            "p95_ms": round(point.p95_ms, 3),
                            "index_build_ms": point.index_build_ms,
                            "error_count": point.error_count,
                            "errors": point.errors,
                            "meets_quality_gate": point.meets_quality_gate,
                        }
                        for point in report.points
                    ],
                    "json_report": str(paths.json_path.resolve()),
                    "markdown_report": str(paths.markdown_path.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if report.quality_gate_passed else 1
    finally:
        for index in reversed(hnsw_indexes):
            index.close()
        exact_index.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return _run(args)
    except (RetrievalDatasetError, RetrievalJudgmentError, ValueError) as exc:
        print(f"pgvector HNSW 实验参数或数据无效：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - stable CLI failure boundary
        print(f"pgvector HNSW 实验失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
