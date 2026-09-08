"""Evidence for the existing retrieval evaluator; no production runtime changes."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess

from app.evaluation.retrieval_dataset import load_retrieval_dataset
from app.evaluation.retrieval_models import RetrievalEvaluationReport
from app.evaluation.retrieval_runtime import (
    RETRIEVAL_EVALUATION_AS_OF_DATE,
    retrieval_evaluation_access_context,
)
from app.rag.embeddings import DEFAULT_BGE_MODEL_NAME
from app.rag.reranking import DEFAULT_BGE_RERANKER_MODEL_NAME
from app.security import authorized_chunk_ids

MODEL_IDS = {"embedding": DEFAULT_BGE_MODEL_NAME, "reranker": DEFAULT_BGE_RERANKER_MODEL_NAME}
_REVISION = re.compile(r"^[0-9a-f]{40}$")


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


def json_sha256(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cohorts(root: Path, protocol_path: Path):
    """Freeze input identity before inference; never silently omit invalid queries."""
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol["schema_version"] != "1.0":
        raise ValueError("unsupported evidence protocol")
    cohorts = []
    ids: set[str] = set()
    queries: set[str] = set()
    names: set[str] = set()
    for cohort in protocol["cohorts"]:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", cohort["name"]):
            raise ValueError("unsafe cohort name")
        if cohort["name"] in names:
            raise ValueError("duplicate cohort name")
        names.add(cohort["name"])
        dataset_path = (root / cohort["path"]).resolve()
        if not dataset_path.is_relative_to(root.resolve()):
            raise ValueError("dataset must be inside repository")
        dataset = load_retrieval_dataset(dataset_path)
        normalized = dataset_path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
        if (
            sha256(normalized.encode()).hexdigest() != cohort["sha256_lf"]
            or len(dataset.cases) != cohort["case_count"]
        ):
            raise ValueError(f"dataset drift: {cohort['name']}; version the protocol explicitly")
        for case in dataset.cases:
            if case.case_id in ids or case.query in queries:
                raise ValueError("cohorts must have distinct query text and case IDs")
            ids.add(case.case_id)
            queries.add(case.query)
        cohorts.append((cohort, dataset))
    if not cohorts:
        raise ValueError("at least one cohort is required")
    return protocol, cohorts


def source_manifest(root: Path) -> dict[str, str]:
    """Only code/config inputs, never .env, caches, credentials or git remotes."""
    paths = [root / "pyproject.toml"]
    for directory in ("app", "scripts"):
        paths.extend((root / directory).rglob("*.py"))
    return {path.relative_to(root).as_posix(): file_sha256(path) for path in sorted(paths)}


def git_identity(root: Path) -> dict[str, object]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, encoding="utf-8", stderr=subprocess.DEVNULL
        ).strip()

    try:
        return {
            "head": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "working_tree_dirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"head": None, "branch": None, "working_tree_dirty": None}


def environment_evidence(*, real_models: bool, device: str, threads: int) -> dict:
    versions = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            # First distribution wins, matching normal sys.path import precedence.
            versions.setdefault(name.lower().replace("_", "-"), distribution.version)
    cpu = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if (not cpu or cpu == platform.machine()) and cpuinfo.is_file():
        cpu = next(
            (
                line.split(":", 1)[1].strip()
                for line in cpuinfo.read_text().splitlines()
                if line.startswith("model name")
            ),
            "unknown",
        )
    result = {
        "python": platform.python_version(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu or "unknown",
        "logical_cpus": os.cpu_count(),
        "requested_device": device,
        "packages": dict(sorted(versions.items())),
        "seed": 0,
        "torch_threads": threads if real_models else None,
    }
    if real_models:
        import random
        import numpy as np
        import torch

        random.seed(0)
        np.random.seed(0)
        torch.manual_seed(0)
        torch.set_num_threads(threads)
        torch.use_deterministic_algorithms(True)
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA requested but unavailable")
        result["cuda_version"] = torch.version.cuda
        result["gpu"] = torch.cuda.get_device_name(0) if device == "cuda" else None
    return result


def _model_files(filenames: list[str]) -> list[str]:
    """Select one PyTorch weight format, excluding ONNX/OpenVINO duplicates."""
    roots = [name for name in filenames if "/" not in name]
    weight_suffix = ".safetensors" if any(n.endswith(".safetensors") for n in roots) else ".bin"
    files = sorted(
        name
        for name in filenames
        if ("/" not in name or name.startswith("1_Pooling/"))
        and name.endswith((".json", ".txt", ".model", weight_suffix))
    )
    if "config.json" not in files or not any(n.endswith(weight_suffix) for n in files):
        raise ValueError("model is missing config or PyTorch weights")
    return files


def prepare_model_lock(path: Path) -> dict:
    """Explicit download step; resolve mutable refs once, then record hashes."""
    if path.exists():
        raise FileExistsError("model lock already exists; use a new path for a new experiment")
    from huggingface_hub import HfApi, snapshot_download

    lock = {"schema_version": "1.0", "models": {}}
    for role, repo_id in MODEL_IDS.items():
        info = HfApi().model_info(repo_id, revision="main")
        revision = info.sha
        if not revision or not _REVISION.fullmatch(revision):
            raise ValueError("Hub did not resolve an immutable model revision")
        filenames = _model_files([item.rfilename for item in info.siblings or ()])
        snapshot = Path(snapshot_download(repo_id, revision=revision, allow_patterns=filenames))
        lock["models"][role] = {
            "repo_id": repo_id,
            "revision": revision,
            "files": {name: file_sha256(snapshot / name) for name in filenames},
        }
    write_json(path, lock)
    return lock


def load_locked_models(path: Path) -> tuple[dict, dict[str, str]]:
    """Local cache only; a missing/changed weight cannot become an offline fixture."""
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock["schema_version"] != "1.0" or set(lock["models"]) != set(MODEL_IDS):
        raise ValueError("invalid model lock")
    from huggingface_hub import snapshot_download

    snapshots = {}
    for role, expected_id in MODEL_IDS.items():
        model = lock["models"][role]
        if model["repo_id"] != expected_id or not _REVISION.fullmatch(model["revision"]):
            raise ValueError("model identity must be the declared BGE model at a commit SHA")
        for filename in model["files"]:
            name = PurePosixPath(filename)
            if name.is_absolute() or ".." in name.parts or "\\" in filename:
                raise ValueError("unsafe model file name")
        _model_files(list(model["files"]))
        snapshot = Path(
            snapshot_download(
                model["repo_id"],
                revision=model["revision"],
                allow_patterns=list(model["files"]),
                local_files_only=True,
            )
        )
        for name, expected in model["files"].items():
            if file_sha256(snapshot / name) != expected:
                raise ValueError(f"model file drift: {role}/{name}")
        snapshots[role] = str(snapshot)
    return lock, snapshots


class CandidateTracingRetriever:
    """Observe existing public retrieval calls without replacing scoring/fusion."""

    def __init__(self, retriever, *, candidate_k: int):
        self.retriever = retriever
        self.candidate_k = candidate_k
        self.candidates: dict[str, dict[str, list[str]]] = {}

    def _record(self, query, channel, results):
        self.candidates.setdefault(query, {})[channel] = [r.chunk.chunk_id for r in results]
        return results

    def search(self, query, *, top_k=5):
        results = self.retriever.search(query, top_k=self.candidate_k)
        return self._record(query, "vector", results)[:top_k]

    def search_keywords(self, query, *, top_k=5):
        results = self.retriever.search_keywords(query, top_k=self.candidate_k)
        return self._record(query, "bm25", results)[:top_k]

    def search_hybrid(self, query, *, top_k=5, candidate_k=None):
        if candidate_k != self.candidate_k:
            raise ValueError("candidate window mismatch")
        results = self.retriever.search_hybrid(query, top_k=candidate_k, candidate_k=candidate_k)
        return self._record(query, "hybrid", results)[:top_k]

    def search_reranked(self, query, *, top_k=5, candidate_k=None):
        if candidate_k != self.candidate_k:
            raise ValueError("candidate window mismatch")
        return self.retriever.search_reranked(query, top_k=top_k, candidate_k=candidate_k)


def corpus_evidence(chunks) -> dict:
    context = retrieval_evaluation_access_context()
    allowed = sorted(
        authorized_chunk_ids(
            chunks,
            context,
            as_of_date=RETRIEVAL_EVALUATION_AS_OF_DATE,
        )
    )
    manifest = [
        chunk.model_dump(mode="json", exclude={"source_path", "metadata_source_path"})
        for chunk in sorted(chunks, key=lambda c: c.chunk_id)
    ]
    return {
        "chunk_count": len(chunks),
        "document_count": len({c.document_id for c in chunks}),
        "formats": dict(Counter(c.source_media_type for c in chunks)),
        "access_context": asdict(context),
        "as_of_date": str(RETRIEVAL_EVALUATION_AS_OF_DATE),
        "allowed_chunk_ids": allowed,
        "allowed_chunk_count": len(allowed),
        "full_chunk_manifest_sha256": json_sha256(manifest),
        "chunks": manifest,
    }


def analyze_report(report: RetrievalEvaluationReport, candidates: dict) -> dict:
    """Retain every imperfect case, including errors; improvements are signed."""
    baseline = next(s for s in report.summaries if s.channel.value == "vector")
    comparisons = []
    failures = []
    for summary in report.summaries:
        comparisons.append(
            {
                "channel": summary.channel.value,
                "effective_n": summary.case_count,
                "recall_at_5": summary.recall_at_k[5],
                "mrr_at_5": summary.mrr_at_k,
                "recall_delta_pp_vs_vector": 100
                * (summary.recall_at_k[5] - baseline.recall_at_k[5]),
                "mrr_delta_pp_vs_vector": 100 * (summary.mrr_at_k - baseline.mrr_at_k),
                "error_count": summary.error_count,
            }
        )
    for case in report.case_results:
        pool = candidates.get(case.query, {})
        for result in case.channels:
            missing = sorted(set(case.relevant_chunk_ids).difference(result.retrieved_chunk_ids))
            if missing or result.reciprocal_rank < 1 or result.error:
                failures.append(
                    {
                        "case_id": case.case_id,
                        "query": case.query,
                        "channel": result.channel.value,
                        "missing_at_5": missing,
                        "first_relevant_rank": result.first_relevant_rank,
                        "recall_at_5": result.recall_at_k[5],
                        "error": result.error,
                        "missing_outside_rrf_window": [
                            x for x in missing if x not in pool.get("hybrid", [])
                        ],
                        "candidate_ranks": {
                            channel: {
                                chunk: ids.index(chunk) + 1 if chunk in ids else None
                                for chunk in case.relevant_chunk_ids
                            }
                            for channel, ids in pool.items()
                        },
                    }
                )
    return {"comparisons": comparisons, "failures": failures, "candidates": candidates}


def write_evidence_markdown(path: Path, evidence: dict) -> None:
    lines = [
        "# 检索评测证据",
        "",
        f"模式：`{evidence['mode']}`；状态：`{evidence['status']}`。",
        "开发集与未人工复核探索集分别报告；不代表生产质量，也不代表回答准确率。",
        "offline 的 Vector/Reranker 是词法替身，BM25 是实际算法。禁止把替身差值写成 BGE 提升。",
        "",
        f"代码 HEAD：`{evidence['git']['head']}`；代码内容指纹：`{evidence['source_sha256']}`。",
        "",
        "Recall@5 = Top-5 命中的已标注相关 Chunk 数 / 该查询全部已标注相关 Chunk 数，按查询宏平均。",
        "Grade 1/2/3 均为正相关；MRR@5 为首个正相关项排名倒数的宏平均，未命中记 0。",
        "运行错误保留样本并记 0；无答案/无权限/无标注不适用本正例排名套件，当前各为 0 条。",
        "非法或不可见标签在推理前报错，不静默删除。未标注的返回项只按未命中计算，不等于人工判为无关。",
        "",
        "每路精确检索最多 20 条，RRF 常数 60，去重后最多 20 条参与重排，最终 K=5。",
        "两种融合方案使用相同候选窗口；所有方案使用同一语料、增强检索文本和检索前权限范围。",
        "耗时仅为诊断：含查询向量化（BM25 除外），排除模型加载/建库，固定顺序、单次采样；不用于延迟简历结论。",
    ]
    for cohort in evidence["cohorts"]:
        lines += [
            "",
            f"## {cohort['name']}",
            "",
            cohort["label_status"],
            "",
            "| 方案 | N | Recall@5 | MRR@5 | Recall 差值（百分点） | MRR 差值（百分点） | 错误 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in cohort["analysis"]["comparisons"]:
            lines.append(
                f"| {row['channel']} | {row['effective_n']} | {row['recall_at_5']:.2%} | "
                f"{row['mrr_at_5']:.4f} | {row['recall_delta_pp_vs_vector']:+.2f} | "
                f"{row['mrr_delta_pp_vs_vector']:+.2f} | {row['error_count']} |"
            )
        lines += ["", "全部非满召回、首相关项非第一名或运行错误（未挑选）：", ""]
        for failure in cohort["analysis"]["failures"]:
            missing = ", ".join(failure["missing_at_5"]) or "无"
            lines.append(
                f"- {failure['case_id']} / {failure['channel']}：{failure['query']} "
                f"Recall@5={failure['recall_at_5']:.3f}，首相关排名={failure['first_relevant_rank']}，"
                f"缺失：{missing}；错误：{failure['error']}。"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
