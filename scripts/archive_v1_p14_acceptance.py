"""运行 AV1-P14 固定问题集的真实检索验收。"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.services.archive.evidence_matching import (
    item_contains_expected_evidence as _shared_item_contains_expected_evidence,
)

if __package__:
    from scripts.p14_d4_threshold_feasibility import (
        build_safe_snapshot,
        write_safe_snapshot,
    )
else:
    from p14_d4_threshold_feasibility import build_safe_snapshot, write_safe_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = PROJECT_ROOT / "tests" / "pytest_docs"
QUESTION_LABEL_PATH = EVALUATION_ROOT / "labels" / "question-ground-truth.json"
DOCUMENT_LABEL_PATH = EVALUATION_ROOT / "labels" / "document-ground-truth.json"
ARCHIVE_FIELD_NAMES = (
    "TITLE",
    "DOCUMENT_TYPE",
    "DOCUMENT_DATE",
    "AUTHORING_ORGANIZATION",
    "VERSION_NUMBER",
    "PROJECT_STAGE",
    "KEYWORDS",
)
C4A_CANDIDATE_POOL_SIZE = 30
C4A_TOP20_NO_EVIDENCE_BASELINE = (
    {
        "strongest_candidate_reranker_score": 0.973935,
        "strongest_candidate_dense_distance": 0.287029,
    },
    {
        "strongest_candidate_reranker_score": 0.707142,
        "strongest_candidate_dense_distance": 0.311391,
    },
)


class AcceptanceError(RuntimeError):
    """表示真实验收接口未满足预期，但不携带响应正文。"""

    def __init__(
        self,
        message: str,
        *,
        safe_code: str = "ACCEPTANCE_ERROR",
        http_status: int | None = None,
        api_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.safe_code = safe_code
        self.http_status = http_status
        self.api_code = api_code


class P14Api:
    """只返回必要 JSON 的本地 FastAPI 验收客户端。"""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(180.0))
        self.headers: dict[str, str] = {}
        self.safe_code = "HTTP_REQUEST"

    def close(self) -> None:
        """关闭 HTTP 连接池。"""
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: Iterable[int],
        payload: dict[str, object] | None = None,
        files: dict[str, tuple[str, Any, str]] | None = None,
        authenticated: bool = True,
    ) -> dict[str, Any] | None:
        """调用一个接口并将非预期状态转换为不含正文的安全错误。"""
        headers = self.headers if authenticated else {}
        response = self._client.request(
            method,
            path,
            headers=headers,
            json=payload if files is None else None,
            files=files,
        )
        if response.status_code not in set(expected_statuses):
            api_code: str | None = None
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            if isinstance(error_body, dict):
                candidate = error_body.get("code")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Z_]{1,80}", candidate):
                    api_code = candidate
            raise AcceptanceError(
                f"{method} {path} 返回 HTTP {response.status_code}，不符合验收预期。",
                safe_code=self.safe_code,
                http_status=response.status_code,
                api_code=api_code,
            )
        if response.status_code == 204:
            return None
        try:
            body = response.json()
        except ValueError as exc:
            raise AcceptanceError(f"{method} {path} 未返回有效 JSON。") from exc
        if not isinstance(body, dict):
            raise AcceptanceError(f"{method} {path} 返回了非对象 JSON。")
        return body


def _read_label(path: Path) -> dict[str, Any]:
    """读取本地虚构 Ground Truth，并验证其基本结构。"""
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError("P14 虚构评测标注不可读取。") from exc
    if not isinstance(content, dict):
        raise AcceptanceError("P14 虚构评测标注格式无效。")
    return content


def _as_object_list(value: object, *, name: str) -> list[dict[str, Any]]:
    """将 JSON 数组安全转换为对象列表。"""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise AcceptanceError(f"P14 标注缺少有效 {name}。")
    return value


def _content_type(path: Path) -> str:
    """为四种已冻结的虚构资料格式提供稳定上传 MIME 类型。"""
    return {
        ".pdf": "application/pdf",
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "application/octet-stream")


def _require_object(value: object, *, step: str) -> dict[str, Any]:
    """验证一个 API JSON 对象，避免后续把无效响应当成成功。"""
    if not isinstance(value, dict):
        raise AcceptanceError(f"{step} 未返回对象响应。")
    return value


def _document_id_and_version(payload: dict[str, Any], *, step: str) -> tuple[str, int]:
    """读取文档 ID 和乐观锁版本，但不把它们输出到验收报告。"""
    try:
        document_id = str(payload["id"])
        version = int(payload["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError(f"{step} 缺少文档 ID 或版本。") from exc
    return document_id, version


def _draft_document_version(payload: dict[str, Any], *, step: str) -> int:
    """从草稿响应读取已递增的文档版本。"""
    try:
        return int(payload["document"]["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError(f"{step} 缺少草稿文档版本。") from exc


def parse_registered_user_id(value: object) -> UUID:
    """将注册响应的用户标识转换为 PostgreSQL 可安全绑定的 UUID。"""
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AcceptanceError("账号注册未返回有效用户标识。") from exc


def is_confirmed_document_response(payload: dict[str, Any]) -> bool:
    """按公开 ProcessDocumentRead 契约确认文档已进入正式状态。"""
    return payload.get("status") == "CONFIRMED" and payload.get("confirmed_at") is not None


def _register_and_login(api: P14Api, *, run_tag: str) -> tuple[UUID, str]:
    """注册唯一虚构账号并只在内存中保留 Bearer Token。"""
    username = f"p14be-{run_tag}"
    password = "P14-fixture-only-password"
    registered = _require_object(
        api.request(
            "POST",
            "/auth/register",
            expected_statuses=(201,),
            payload={
                "username": username,
                "name": "P14 虚构验收账号",
                "password": password,
            },
            authenticated=False,
        ),
        step="账号注册",
    )
    try:
        user_id = parse_registered_user_id(registered["id"])
    except KeyError as exc:
        raise AcceptanceError("账号注册未返回用户标识。") from exc
    token_pair = _require_object(
        api.request(
            "POST",
            "/auth/login",
            expected_statuses=(200,),
            payload={"username": username, "password": password},
            authenticated=False,
        ),
        step="账号登录",
    )
    try:
        access_token = str(token_pair["access_token"])
    except KeyError as exc:
        raise AcceptanceError("账号登录未返回 Access Token。") from exc
    api.headers = {"Authorization": f"Bearer {access_token}"}
    return user_id, username


def _seed_confirmed_documents(
    api: P14Api,
    *,
    document_label: dict[str, Any],
    run_tag: str,
    project_ids: dict[str, str],
    seeded: list[tuple[str, str]],
    evaluation_root: Path | None = None,
) -> tuple[dict[str, list[str]], list[int]]:
    """经 P05/P06/P07/P09 路由准备两项目的真实正式档案。"""
    root = evaluation_root or EVALUATION_ROOT
    projects = _as_object_list(document_label.get("projects"), name="projects")
    normal_documents = _as_object_list(
        document_label.get("normal_documents", document_label.get("documents")),
        name="normal_documents",
    )
    for project in projects:
        try:
            project_key = str(project["id"])
        except KeyError as exc:
            raise AcceptanceError("P14 项目标注缺少 id。") from exc
        api.safe_code = "SEED_PROJECT_CREATE"
        created = _require_object(
            api.request(
                "POST",
                "/projects",
                expected_statuses=(201,),
                payload={
                    "name": f"p14-{project_key}-{run_tag}",
                    "description": "P14 虚构检索验收数据",
                    "use_demo_checklist": True,
                },
            ),
            step="项目创建",
        )
        try:
            project_ids[project_key] = str(created["id"])
        except KeyError as exc:
            raise AcceptanceError("项目创建未返回标识。") from exc

    filenames_by_project: dict[str, list[str]] = {key: [] for key in project_ids}
    index_context_counts: list[int] = []
    for document in normal_documents:
        try:
            project_key = str(document["project_id"])
            project_id = project_ids[project_key]
            relative_path = str(document["relative_path"])
            expected_fields = document.get("expected_fields", {})
        except (KeyError, TypeError) as exc:
            raise AcceptanceError("P14 文档标注缺少项目、路径或字段。") from exc
        if not isinstance(expected_fields, dict):
            raise AcceptanceError("P14 文档字段标注格式无效。")
        path = root / relative_path
        if not path.is_file():
            raise AcceptanceError("P14 虚构资料文件缺失。")

        with path.open("rb") as source:
            api.safe_code = "SEED_UPLOAD"
            uploaded = _require_object(
                api.request(
                    "POST",
                    f"/projects/{project_id}/documents",
                    expected_statuses=(201,),
                    files={"file": (path.name, source, _content_type(path))},
                ),
                step="文档上传",
            )
        document_id, _ = _document_id_and_version(uploaded, step="文档上传")
        # 上传已持久化到 PostgreSQL/文件系统；后续任一步骤失败时也必须进入精确清理范围。
        seeded.append((project_id, document_id))
        api.safe_code = "SEED_PARSE"
        parsed = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/documents/{document_id}/parse",
                expected_statuses=(200,),
            ),
            step="文档解析",
        )
        _, parsed_version = _document_id_and_version(parsed, step="文档解析")
        api.safe_code = "SEED_MANUAL_DRAFT"
        drafted = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/documents/{document_id}/manual-draft",
                expected_statuses=(200,),
            ),
            step="人工草稿创建",
        )
        version = _draft_document_version(drafted, step="人工草稿创建")
        if version < parsed_version:
            raise AcceptanceError("人工草稿版本不应早于解析版本。")
        for field_name in ARCHIVE_FIELD_NAMES:
            field_spec = expected_fields.get(field_name)
            if field_spec is not None and not isinstance(field_spec, dict):
                raise AcceptanceError("P14 字段标注格式无效。")
            api.safe_code = "SEED_FIELD_UPDATE"
            updated = _require_object(
                api.request(
                    "PUT",
                    f"/projects/{project_id}/documents/{document_id}/fields/{field_name}",
                    expected_statuses=(200,),
                    payload=build_manual_field_payload(
                        str(field_name), field_spec, expected_version=version
                    ),
                ),
                step="人工字段确认",
            )
            version = _draft_document_version(updated, step="人工字段确认")
        api.safe_code = "SEED_CONFIRM"
        confirmed = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/documents/{document_id}/confirm",
                expected_statuses=(200,),
                payload={"expected_version": version},
            ),
            step="人工确认并索引",
        )
        if not is_confirmed_document_response(confirmed):
            raise AcceptanceError("确认响应未表明文档进入 CONFIRMED 状态。")
        context_count = confirmed.get("index_context_chunk_count")
        if isinstance(context_count, bool) or not isinstance(context_count, int) or context_count < 0:
            raise AcceptanceError("确认响应缺少安全的索引上下文覆盖计数。")
        index_context_counts.append(context_count)
        filenames_by_project[project_key].append(path.name)
    return filenames_by_project, index_context_counts


def _collect_retrieval_outcomes(
    api: P14Api,
    *,
    question_label: dict[str, Any],
    project_ids: dict[str, str],
    filenames_by_project: dict[str, list[str]],
    top_k: int = 10,
    include_ground_truth_in_diagnostic: bool = True,
) -> tuple[list[dict[str, object]], list[float]]:
    """在未过滤候选的服务上收集固定问题集响应和请求耗时。

    `include_ground_truth_in_diagnostic` 仅为历史阈值诊断保留；D5 捕获必须关闭，
    这样 Ground Truth 只会进入离线数据集元数据，不会进入生产请求。
    """
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("检索候选数量必须是正整数。")
    questions = _as_object_list(question_label.get("questions"), name="questions")
    outcomes: list[dict[str, object]] = []
    latencies_ms: list[float] = []
    for question in questions:
        try:
            case_id = question["id"]
            project_key = str(question["project_id"])
            project_id = project_ids[project_key]
            category = str(question["category"])
            content = str(question["question"])
        except KeyError as exc:
            raise AcceptanceError("P14 问题标注缺少标识、项目、类别或问题。") from exc
        if not isinstance(case_id, str) or not case_id.strip():
            raise AcceptanceError("P14 问题标注包含无效问题标识。")
        started_at = time.perf_counter()
        response = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/archive-retrieval",
                expected_statuses=(200,),
                payload={"query": content, "top_k": top_k},
            ),
            step="正式检索",
        )
        retrieval_latency_ms = (time.perf_counter() - started_at) * 1000
        latencies_ms.append(retrieval_latency_ms)
        raw_items = response.get("items")
        if not isinstance(raw_items, list) or not all(
            isinstance(item, dict) for item in raw_items
        ):
            raise AcceptanceError("正式检索响应缺少有效 items。")
        requested_top_k = response.get("requested_top_k", top_k)
        returned_count = response.get("returned_count", len(raw_items))
        if (
            isinstance(requested_top_k, bool)
            or not isinstance(requested_top_k, int)
            or requested_top_k != top_k
            or isinstance(returned_count, bool)
            or not isinstance(returned_count, int)
            or returned_count != len(raw_items)
        ):
            raise AcceptanceError("正式检索响应的候选计数不一致。")
        diagnostic_payload: dict[str, object] = {"query": content}
        if (
            include_ground_truth_in_diagnostic
            and category == "GROUNDED"
            and "expected_evidence" in question
        ):
            diagnostic_payload["expected_evidence"] = question["expected_evidence"]
        diagnostic_response = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/archive-retrieval-diagnostic",
                expected_statuses=(200,),
                payload=diagnostic_payload,
            ),
            step="Top-30 双排序诊断",
        )
        diagnostic_candidates = _read_safe_internal_diagnostic(diagnostic_response)
        outcome: dict[str, object] = {
            "case_id": case_id,
            "category": category,
            "project_id": project_key,
            "items": raw_items,
            "allowed_filenames": filenames_by_project[project_key],
            "internal_candidates": diagnostic_candidates["candidates"],
            "diagnostic_candidate_count": diagnostic_candidates["candidate_count"],
            "diagnostic_chroma_candidate_count": diagnostic_candidates[
                "chroma_candidate_count"
            ],
            "reranker_query_mode": diagnostic_candidates["reranker_query_mode"],
            "retrieval_requested_top_k": requested_top_k,
            "retrieval_returned_count": returned_count,
            "retrieval_latency_ms": retrieval_latency_ms,
        }
        for optional_key in ("expected_evidence", "hidden_evidence_in_other_project"):
            if optional_key in question:
                outcome[optional_key] = question[optional_key]
        outcomes.append(outcome)
    return outcomes, latencies_ms


def _read_safe_internal_diagnostic(payload: dict[str, Any]) -> dict[str, object]:
    """校验诊断接口的数值投影，拒绝任何非脱敏或结构不完整响应。"""
    candidate_count = payload.get("candidate_count")
    chroma_candidate_count = payload.get("chroma_candidate_count")
    reranker_query_mode = payload.get("reranker_query_mode", "c4_a")
    candidates = payload.get("candidates")
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count < 0
        or isinstance(chroma_candidate_count, bool)
        or not isinstance(chroma_candidate_count, int)
        or chroma_candidate_count < 0
        or reranker_query_mode not in {"c4_a", "c4_b"}
        or not isinstance(candidates, list)
    ):
        raise AcceptanceError("Top-30 双排序诊断响应结构无效。")
    safe_candidates: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise AcceptanceError("Top-30 双排序诊断候选结构无效。")
        dense_rank = candidate.get("dense_rank")
        dense_distance = candidate.get("dense_distance")
        reranker_rank = candidate.get("reranker_rank")
        reranker_score = candidate.get("reranker_score")
        matches = candidate.get("matches_expected_evidence")
        candidate_kind = candidate.get("candidate_kind")
        if (
            isinstance(dense_rank, bool)
            or not isinstance(dense_rank, int)
            or dense_rank < 1
            or isinstance(dense_distance, bool)
            or not isinstance(dense_distance, (int, float))
            or not math.isfinite(float(dense_distance))
            or isinstance(reranker_rank, bool)
            or not isinstance(reranker_rank, int)
            or reranker_rank < 1
            or isinstance(reranker_score, bool)
            or not isinstance(reranker_score, (int, float))
            or not math.isfinite(float(reranker_score))
            or not isinstance(matches, bool)
            or (
                candidate_kind is not None
                and candidate_kind
                not in {"SAME_DOCUMENT", "OTHER_DOCUMENT", "UNKNOWN"}
            )
        ):
            raise AcceptanceError("Top-30 双排序诊断数值无效。")
        safe_candidate: dict[str, object] = {
            "dense_rank": dense_rank,
            "dense_distance": float(dense_distance),
            "reranker_rank": reranker_rank,
            "reranker_score": float(reranker_score),
            "matches_expected_evidence": matches,
            "candidate_kind": candidate_kind,
        }
        d4_keys = {
            "candidate_key",
            "public_coverage_match",
            "isolation_violation",
        }
        present_d4_keys = d4_keys.intersection(candidate)
        if present_d4_keys and present_d4_keys != d4_keys:
            raise AcceptanceError("Top-30 双排序诊断 D4 字段不完整。")
        if present_d4_keys:
            candidate_key = candidate["candidate_key"]
            public_coverage_match = candidate["public_coverage_match"]
            isolation_violation = candidate["isolation_violation"]
            if (
                not isinstance(candidate_key, str)
                or re.fullmatch(r"[0-9a-f]{64}", candidate_key) is None
                or not isinstance(public_coverage_match, bool)
                or not isinstance(isolation_violation, bool)
                or candidate_kind not in {
                    "SAME_DOCUMENT",
                    "OTHER_DOCUMENT",
                    "UNKNOWN",
                }
            ):
                raise AcceptanceError("Top-30 双排序诊断 D4 字段无效。")
            safe_candidate.update(
                {
                    "candidate_key": candidate_key,
                    "public_coverage_match": public_coverage_match,
                    "isolation_violation": isolation_violation,
                }
            )
        safe_candidates.append(safe_candidate)
    if len(safe_candidates) != candidate_count:
        raise AcceptanceError("Top-30 双排序诊断候选数量不一致。")
    return {
        "chroma_candidate_count": chroma_candidate_count,
        "candidate_count": candidate_count,
        "reranker_query_mode": reranker_query_mode,
        "candidates": safe_candidates,
    }


def _p95_ms(samples: list[float]) -> float:
    """采用 nearest-rank 计算固定样本的 P95，仅记录而不作为通过门槛。"""
    if not samples:
        raise AcceptanceError("没有可用于计算 P95 的检索耗时。")
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * 0.95) - 1e-12)))
    return ordered[index]


def write_aggregate_result(path: Path, result: dict[str, int | float | bool]) -> None:
    """将不含资源标识和原文的验收聚合指标写入调用方指定的临时文件。"""
    if any(not isinstance(value, (int, float, bool)) for value in result.values()):
        raise ValueError("验收结果文件只能包含数值或布尔聚合指标。")
    path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_d5_capture_dataset(
    question_label: dict[str, Any],
    outcomes: list[dict[str, object]],
) -> dict[str, object]:
    """把真实 Top-5 候选和离线 Ground Truth 组合为 Pixie 捕获数据集。"""
    questions = _as_object_list(question_label.get("questions"), name="questions")
    if len(questions) != 12 or len(outcomes) != 12:
        raise AcceptanceError("D5 捕获必须恰好包含 12 道固定问题。")
    questions_by_id: dict[str, dict[str, Any]] = {}
    for question in questions:
        case_id = question.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in questions_by_id:
            raise AcceptanceError("D5 问题标注包含重复或无效问题标识。")
        questions_by_id[case_id] = question
    entries: list[dict[str, object]] = []
    seen_case_ids: set[str] = set()
    for outcome in outcomes:
        case_id = outcome.get("case_id")
        if not isinstance(case_id, str) or case_id in seen_case_ids or case_id not in questions_by_id:
            raise AcceptanceError("D5 检索结果与固定问题标注无法一一对应。")
        seen_case_ids.add(case_id)
        question = questions_by_id[case_id]
        items = outcome.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise AcceptanceError("D5 捕获缺少有效正式候选。")
        candidate_count = outcome.get("diagnostic_candidate_count")
        chroma_candidate_count = outcome.get("diagnostic_chroma_candidate_count")
        retrieval_latency_ms = outcome.get("retrieval_latency_ms")
        if (
            isinstance(candidate_count, bool)
            or not isinstance(candidate_count, int)
            or candidate_count < 0
            or isinstance(chroma_candidate_count, bool)
            or not isinstance(chroma_candidate_count, int)
            or chroma_candidate_count < 0
            or isinstance(retrieval_latency_ms, bool)
            or not isinstance(retrieval_latency_ms, (int, float))
            or not math.isfinite(float(retrieval_latency_ms))
            or retrieval_latency_ms < 0
        ):
            raise AcceptanceError("D5 捕获缺少安全的候选池计数或检索耗时。")
        category = str(outcome.get("category", question.get("category", "")))
        expected_evidence = question.get("expected_evidence")
        # D5 的覆盖指标必须基于正式公开 Top-5 返回项计算，不能读取诊断或把 Ground Truth 注入请求。
        public_coverage_match = (
            category == "GROUNDED"
            and any(
                item_contains_expected_evidence(item, expected_evidence)
                for item in items
            )
        )
        entries.append(
            {
                "description": "D5 固定题集真实检索捕获",
                "input_data": {"question": question["question"]},
                "eval_input": [
                    {
                        "name": "archive_question_retrieval",
                        "value": {
                            "items": items,
                            "requested_top_k": outcome.get("retrieval_requested_top_k", 5),
                            "returned_count": outcome.get(
                                "retrieval_returned_count", len(items)
                            ),
                        },
                    }
                ],
                "eval_metadata": {
                    "case_id": case_id,
                    "case_kind": category,
                    "category": category,
                    "expected_answer_status": (
                        "ANSWERED" if category == "GROUNDED" else "REFUSED_NO_EVIDENCE"
                    ),
                    "direct_evidence_required": category == "GROUNDED",
                    "public_coverage_match": public_coverage_match,
                    # 这些字段是离线评测元数据，绝不进入生产检索请求。
                    "expected_answer": question.get("expected_answer"),
                    "expected_evidence": expected_evidence,
                    "hidden_evidence_in_other_project": question.get(
                        "hidden_evidence_in_other_project"
                    ),
                    "candidate_pool_expected_count": C4A_CANDIDATE_POOL_SIZE,
                    "candidate_count": candidate_count,
                    "chroma_candidate_count": chroma_candidate_count,
                    "candidate_pool_complete": (
                        candidate_count == C4A_CANDIDATE_POOL_SIZE
                        and chroma_candidate_count == C4A_CANDIDATE_POOL_SIZE
                    ),
                    "retrieval_latency_ms": round(float(retrieval_latency_ms), 2),
                },
            }
        )
    if seen_case_ids != set(questions_by_id):
        raise AcceptanceError("D5 检索结果缺少固定问题。")
    return {
        "name": "archive-question-d5-captured",
        "runnable": "pixie_qa/archive_v1_p14/run_app.py:ArchiveQuestionRunnable",
        "evaluators": [
            "pixie_qa/archive_v1_p14/evaluators.py:archive_answer_contract",
            "pixie_qa/archive_v1_p14/evaluators.py:archive_evidence_faithfulness",
            "pixie_qa/archive_v1_p14/evaluators.py:archive_refusal_quality",
            "pixie_qa/archive_v1_p14/evaluators.py:archive_v1_p02_quality_gate",
        ],
        "entries": entries,
    }


def write_d5_capture_dataset(path: Path, dataset: dict[str, object]) -> None:
    """原子写入仅供 Pixie 使用的 D5 捕获数据集。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(dataset, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def build_d6b_capture_dataset(
    question_label: dict[str, Any],
    outcomes: list[dict[str, object]],
) -> dict[str, object]:
    """把真实 Top-8 候选和离线标注组合为明确的 D6-B 数据集。"""
    for outcome in outcomes:
        if outcome.get("retrieval_requested_top_k") != 8:
            raise AcceptanceError("D6-B 捕获结果的问答候选数量必须是 Top-8。")
    dataset = build_d5_capture_dataset(question_label, outcomes)
    dataset["name"] = "archive-question-d6b-top8-captured"
    dataset["description"] = "D6-B Top-8 固定题集真实检索捕获"
    for entry in dataset["entries"]:
        if isinstance(entry, dict):
            entry["description"] = "D6-B Top-8 固定题集真实检索捕获"
    return dataset


def build_enterprise_capture_dataset(
    question_label: dict[str, Any],
    outcomes: list[dict[str, object]],
    *,
    top_k: int = 8,
) -> dict[str, object]:
    """将企业规模真实 Top-K 候选组合为动态问题数量的 Pixie 捕获数据集。"""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("企业捕获 Top-K 必须是正整数。")
    questions = _as_object_list(question_label.get("questions"), name="questions")
    if not questions or len(outcomes) != len(questions):
        raise AcceptanceError("企业捕获问题和检索结果数量必须一致且非空。")
    questions_by_id: dict[str, dict[str, Any]] = {}
    for question in questions:
        case_id = question.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in questions_by_id:
            raise AcceptanceError("企业捕获问题标注包含重复或无效问题标识。")
        questions_by_id[case_id] = question

    entries: list[dict[str, object]] = []
    seen_case_ids: set[str] = set()
    for outcome in outcomes:
        case_id = outcome.get("case_id")
        if (
            not isinstance(case_id, str)
            or case_id in seen_case_ids
            or case_id not in questions_by_id
        ):
            raise AcceptanceError("企业捕获检索结果与问题标注无法一一对应。")
        seen_case_ids.add(case_id)
        items = outcome.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise AcceptanceError("企业捕获缺少有效正式候选。")
        requested_top_k = outcome.get("retrieval_requested_top_k", top_k)
        returned_count = outcome.get("retrieval_returned_count", len(items))
        if (
            isinstance(requested_top_k, bool)
            or not isinstance(requested_top_k, int)
            or requested_top_k != top_k
            or isinstance(returned_count, bool)
            or not isinstance(returned_count, int)
            or returned_count != len(items)
        ):
            raise AcceptanceError("企业捕获响应的 Top-K 或候选计数不一致。")
        question = questions_by_id[case_id]
        category = str(outcome.get("category", question.get("category", "")))
        expected_evidence = question.get("expected_evidence")
        expected_answer_fragments = question.get("expected_answer_fragments")
        candidate_count = outcome.get("diagnostic_candidate_count")
        chroma_candidate_count = outcome.get("diagnostic_chroma_candidate_count")
        retrieval_latency_ms = outcome.get("retrieval_latency_ms", 0.0)
        if (
            isinstance(candidate_count, bool)
            or not isinstance(candidate_count, int)
            or candidate_count < 0
            or isinstance(chroma_candidate_count, bool)
            or not isinstance(chroma_candidate_count, int)
            or chroma_candidate_count < 0
            or isinstance(retrieval_latency_ms, bool)
            or not isinstance(retrieval_latency_ms, (int, float))
            or not math.isfinite(float(retrieval_latency_ms))
            or retrieval_latency_ms < 0
        ):
            raise AcceptanceError("企业捕获缺少安全的候选池计数或检索耗时。")
        public_coverage_match = category == "GROUNDED" and any(
            item_contains_expected_evidence(item, expected_evidence)
            for item in items
        )
        entries.append(
            {
                "description": "企业规模 RAG 真实检索捕获",
                "input_data": {"question": question["question"]},
                "eval_input": [
                    {
                        "name": "archive_question_retrieval",
                        "value": {
                            "items": items,
                            "requested_top_k": requested_top_k,
                            "returned_count": returned_count,
                        },
                    }
                ],
                "eval_metadata": {
                    "case_id": case_id,
                    "case_kind": category,
                    "category": category,
                    "expected_answer_status": (
                        "ANSWERED" if category == "GROUNDED" else "REFUSED_NO_EVIDENCE"
                    ),
                    "direct_evidence_required": category == "GROUNDED",
                    "public_coverage_match": public_coverage_match,
                    # Ground Truth 仅用于离线评测，不能进入 input_data 或生产请求。
                    "expected_answer": question.get("expected_answer"),
                    **(
                        {"expected_answer_fragments": expected_answer_fragments}
                        if expected_answer_fragments is not None
                        else {}
                    ),
                    "expected_evidence": expected_evidence,
                    "hidden_evidence_in_other_project": question.get(
                        "hidden_evidence_in_other_project"
                    ),
                    "candidate_pool_expected_count": C4A_CANDIDATE_POOL_SIZE,
                    "candidate_count": candidate_count,
                    "chroma_candidate_count": chroma_candidate_count,
                    "candidate_pool_complete": (
                        candidate_count == C4A_CANDIDATE_POOL_SIZE
                        and chroma_candidate_count == C4A_CANDIDATE_POOL_SIZE
                    ),
                    "retrieval_latency_ms": round(
                        float(retrieval_latency_ms), 2
                    ),
                },
            }
        )
    if seen_case_ids != set(questions_by_id):
        raise AcceptanceError("企业捕获检索结果缺少问题。")
    return {
        "name": "archive-question-enterprise-captured",
        "description": "企业规模 RAG 真实 Top-K 检索捕获",
        "runnable": "pixie_qa/archive_v1_p14/run_app.py:ArchiveQuestionRunnable",
        "evaluators": [
            "pixie_qa/archive_v1_p14/evaluators.py:archive_answer_contract",
            "pixie_qa/archive_v1_p14/evaluators.py:archive_evidence_faithfulness",
            "pixie_qa/archive_v1_p14/evaluators.py:archive_refusal_quality",
            "pixie_qa/archive_v1_p14/evaluators.py:archive_v1_p02_quality_gate",
        ],
        "entries": entries,
    }


def write_safe_diagnostic(
    path: Path,
    *,
    stage: str,
    error: BaseException,
    retrieval_diagnostics: list[dict[str, object]] | None = None,
    index_context_summary: dict[str, int] | None = None,
    latency_p95_ms: float | None = None,
) -> None:
    """保存不含请求路径、资源 ID 或正文的最小失败诊断。"""
    diagnostic: dict[str, object] = {
        "outcome": "failed",
        "stage": stage,
        "error_type": type(error).__name__,
    }
    if stage == "threshold_calibration" and isinstance(error, ValueError):
        # 此处的 ValueError 仅由固定题集评分器构造，内容只有三类聚合计数。
        diagnostic["threshold_aggregate"] = str(error)
    if isinstance(error, AcceptanceError):
        diagnostic["safe_code"] = error.safe_code
        if error.http_status is not None:
            diagnostic["http_status"] = error.http_status
        if error.api_code is not None:
            diagnostic["api_code"] = error.api_code
    if retrieval_diagnostics is not None:
        # 逐题数据已经过安全投影；保留它才能将阈值失败归因到候选排序，
        # 而不是只得到不可行动的聚合计数。
        diagnostic["retrieval_diagnostics"] = retrieval_diagnostics
    if index_context_summary is not None:
        diagnostic["index_context_summary"] = index_context_summary
    if latency_p95_ms is not None:
        if (
            isinstance(latency_p95_ms, bool)
            or not isinstance(latency_p95_ms, (int, float))
            or not math.isfinite(float(latency_p95_ms))
            or latency_p95_ms < 0
        ):
            raise ValueError("P14 P95 延迟必须是非负有限数值。")
        diagnostic["latency_p95_ms"] = round(float(latency_p95_ms), 2)
    path.write_text(
        json.dumps(diagnostic, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify_and_purge_principal(*, user_id: UUID) -> None:
    """按唯一虚构用户精确清理认证和内部知识库，并核对零残留。"""
    from sqlalchemy import text

    from app.db import engine

    with engine.begin() as connection:
        # 项目和文档由业务 API 删除；此处清理 API 未提供入口的会话、认证主体和内部 KB。
        connection.execute(
            text("DELETE FROM auth_sessions WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        connection.execute(
            text("DELETE FROM knowledge_bases WHERE owner_id = :user_id"),
            {"user_id": user_id},
        )
        connection.execute(
            text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_id}
        )
        remaining = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM users WHERE id = :user_id) + "
                "(SELECT count(*) FROM auth_sessions WHERE user_id = :user_id) + "
                "(SELECT count(*) FROM knowledge_bases WHERE owner_id = :user_id)"
            ),
            {"user_id": user_id},
        ).scalar_one()
    if int(remaining) != 0:
        raise AcceptanceError("P14 虚构认证与知识库清理后仍有残留。")


def _cleanup_seeded_scope(
    api: P14Api,
    *,
    seeded: list[tuple[str, str]],
    project_ids: dict[str, str],
    user_id: UUID,
) -> None:
    """经物理删除服务清理文档，再移除空项目和临时账号。"""
    failures: list[str] = []
    for project_id, document_id in reversed(seeded):
        try:
            api.request(
                "DELETE",
                f"/projects/{project_id}/documents/{document_id}",
                expected_statuses=(204,),
            )
        except AcceptanceError:
            failures.append("document")
    for project_id in project_ids.values():
        try:
            api.request("DELETE", f"/projects/{project_id}", expected_statuses=(204,))
        except AcceptanceError:
            failures.append("project")
    if api.headers:
        try:
            api.request("POST", "/auth/logout", expected_statuses=(204,))
        except AcceptanceError:
            failures.append("logout")
    try:
        _verify_and_purge_principal(user_id=user_id)
    except Exception:
        failures.append("principal")
    if failures:
        raise AcceptanceError("P14 虚构验收数据清理未全部完成。")


def run_retrieval_calibration(
    *,
    base_url: str,
    evaluation_root: Path | None = None,
    result_file: Path | None = None,
    diagnostic_file: Path | None = None,
    snapshot_file: Path | None = None,
    d5_dataset_file: Path | None = None,
    d6b_dataset_file: Path | None = None,
    enterprise_dataset_file: Path | None = None,
    enterprise_top_k: int = 8,
    calibrate_threshold: bool | None = None,
    phase: str | None = None,
) -> dict[str, int | float | bool]:
    """执行完整真实检索链路，并按阶段返回安全聚合指标。"""
    selected_phase = phase or (
        "threshold-calibration" if calibrate_threshold is not False else "c4-a"
    )
    if selected_phase not in {
        "c4-a",
        "c4-b",
        "d4-a-snapshot",
        "threshold-calibration",
        "d5-capture",
        "d6b-capture",
        "enterprise-capture",
    }:
        raise ValueError(
            "P14 阶段必须是 c4-a、c4-b、d4-a-snapshot、threshold-calibration、d5-capture、d6b-capture 或 enterprise-capture。"
        )
    if calibrate_threshold is not None and (
        selected_phase == "threshold-calibration"
    ) != calibrate_threshold:
        raise ValueError("P14 阶段与阈值标定开关不一致。")
    if (
        isinstance(enterprise_top_k, bool)
        or not isinstance(enterprise_top_k, int)
        or enterprise_top_k <= 0
    ):
        raise ValueError("企业捕获 Top-K 必须是正整数。")
    if selected_phase == "d4-a-snapshot" and snapshot_file is None:
        raise ValueError("D4-A 快照文件不能为空。")
    if selected_phase != "d4-a-snapshot" and snapshot_file is not None:
        raise ValueError("快照文件仅允许 D4-A 阶段使用。")
    if selected_phase == "d5-capture" and d5_dataset_file is None:
        raise ValueError("D5 捕获数据集文件不能为空。")
    if selected_phase != "d5-capture" and d5_dataset_file is not None:
        raise ValueError("D5 捕获数据集文件仅允许 d5-capture 阶段使用。")
    if selected_phase == "d6b-capture" and d6b_dataset_file is None:
        raise ValueError("D6-B 捕获数据集文件不能为空。")
    if selected_phase != "d6b-capture" and d6b_dataset_file is not None:
        raise ValueError("D6-B 捕获数据集文件仅允许 d6b-capture 阶段使用。")
    if selected_phase == "enterprise-capture" and enterprise_dataset_file is None:
        raise ValueError("企业捕获数据集文件不能为空。")
    if selected_phase != "enterprise-capture" and enterprise_dataset_file is not None:
        raise ValueError("企业捕获数据集文件仅允许 enterprise-capture 阶段使用。")
    selected_evaluation_root = Path(evaluation_root or EVALUATION_ROOT)
    labels_root = selected_evaluation_root / "labels"
    question_label_path = labels_root / "question-ground-truth.json"
    document_label_path = labels_root / "document-ground-truth.json"
    materials_root = selected_evaluation_root
    if not question_label_path.is_file() and not document_label_path.is_file():
        # 允许调用方把 labels 目录本身作为评测根目录传入，便于临时隔离测试。
        question_label_path = selected_evaluation_root / "question-ground-truth.json"
        document_label_path = selected_evaluation_root / "document-ground-truth.json"
        materials_root = selected_evaluation_root.parent
    protected_paths = {
        question_label_path.resolve(),
        document_label_path.resolve(),
    }
    protected_paths.update(
        path.resolve()
        for path in (result_file, diagnostic_file)
        if path is not None
    )
    output_paths = [
        path.resolve()
        for path in (
            result_file,
            diagnostic_file,
            snapshot_file,
            d5_dataset_file,
            d6b_dataset_file,
            enterprise_dataset_file,
        )
        if path is not None
    ]
    if len(output_paths) != len(set(output_paths)):
        raise ValueError("P14 输出文件不得互相覆盖。")
    if snapshot_file is not None and snapshot_file.resolve() in protected_paths:
        raise ValueError("D4-A 快照文件不得覆盖输入或其他输出文件。")
    if d5_dataset_file is not None:
        if d5_dataset_file.resolve() in protected_paths:
            raise ValueError("D5 捕获数据集文件不得覆盖输入或其他输出文件。")
        try:
            # 在任何外部工作前移除旧捕获，防止失败后误读上一轮结果。
            d5_dataset_file.unlink(missing_ok=True)
        except OSError as exc:
            raise AcceptanceError("D5 旧捕获数据集无法安全清理。") from exc
    if d6b_dataset_file is not None:
        if d6b_dataset_file.resolve() in protected_paths:
            raise ValueError("D6-B 捕获数据集文件不得覆盖输入或其他输出文件。")
        try:
            # 在任何外部工作前移除旧捕获，防止失败后误读上一轮结果。
            d6b_dataset_file.unlink(missing_ok=True)
        except OSError as exc:
            raise AcceptanceError("D6-B 旧捕获数据集无法安全清理。") from exc
    if enterprise_dataset_file is not None:
        if enterprise_dataset_file.resolve() in protected_paths:
            raise ValueError("企业捕获数据集文件不得覆盖输入或其他输出文件。")
        try:
            enterprise_dataset_file.unlink(missing_ok=True)
        except OSError as exc:
            raise AcceptanceError("企业旧捕获数据集无法安全清理。") from exc
    if snapshot_file is not None:
        try:
            # 在任何外部工作前移除旧快照，防止失败后误读上一轮结果。
            snapshot_file.unlink(missing_ok=True)
        except OSError as exc:
            raise AcceptanceError("D4-A 旧快照无法安全清理。") from exc
    question_label = _read_label(question_label_path)
    document_label = _read_label(document_label_path)
    if question_label.get("dataset_id") != document_label.get("dataset_id"):
        raise AcceptanceError("P14 问题与文档标注不属于同一评测集。")

    run_tag = uuid4().hex[:16]
    api = P14Api(base_url)
    user_id: UUID | None = None
    project_ids: dict[str, str] = {}
    seeded: list[tuple[str, str]] = []
    primary_error: BaseException | None = None
    retrieval_diagnostics: list[dict[str, object]] | None = None
    index_context_summary: dict[str, int] | None = None
    stage = "registration"
    latencies_ms: list[float] = []
    try:
        user_id, _ = _register_and_login(api, run_tag=run_tag)
        stage = "seed_confirmation"
        filenames_by_project, index_context_counts = _seed_confirmed_documents(
            api,
            document_label=document_label,
            run_tag=run_tag,
            project_ids=project_ids,
            seeded=seeded,
            evaluation_root=materials_root,
        )
        index_context_summary = {
            "document_count": len(index_context_counts),
            "contextual_chunk_count": sum(index_context_counts),
            "zero_context_document_count": sum(count == 0 for count in index_context_counts),
        }
        stage = "retrieval"
        outcomes, latencies_ms = _collect_retrieval_outcomes(
            api,
            question_label=question_label,
            project_ids=project_ids,
            filenames_by_project=filenames_by_project,
            top_k=(
                5
                if selected_phase == "d5-capture"
                else 8
                if selected_phase == "d6b-capture"
                else enterprise_top_k
                if selected_phase == "enterprise-capture"
                else 10
            ),
            include_ground_truth_in_diagnostic=selected_phase
            not in {"d5-capture", "d6b-capture", "enterprise-capture"},
        )
        if selected_phase in {"d5-capture", "d6b-capture"}:
            is_d6b = selected_phase == "d6b-capture"
            stage = "d6b_capture" if is_d6b else "d5_capture"
            dataset = (
                build_d6b_capture_dataset(question_label, outcomes)
                if is_d6b
                else build_d5_capture_dataset(question_label, outcomes)
            )
            dataset_file = d6b_dataset_file if is_d6b else d5_dataset_file
            write_d5_capture_dataset(dataset_file, dataset)
            category_counts = {
                category: sum(outcome.get("category") == category for outcome in outcomes)
                for category in ("GROUNDED", "NO_EVIDENCE", "ISOLATION")
            }
            complete_count = sum(
                outcome.get("diagnostic_candidate_count") == C4A_CANDIDATE_POOL_SIZE
                and outcome.get("diagnostic_chroma_candidate_count")
                == C4A_CANDIDATE_POOL_SIZE
                for outcome in outcomes
            )
            public_coverage_grounded_count = sum(
                outcome.get("category") == "GROUNDED"
                and any(
                    item_contains_expected_evidence(
                        item, outcome.get("expected_evidence")
                    )
                    for item in outcome.get("items", [])
                    if isinstance(item, dict)
                )
                for outcome in outcomes
            )
            result = {
                "candidate_pool_expected_count": C4A_CANDIDATE_POOL_SIZE,
                "candidate_pool_complete_question_count": complete_count,
                "candidate_pool_incomplete_question_count": len(outcomes) - complete_count,
                "grounded_question_count": category_counts["GROUNDED"],
                "public_coverage_grounded_count": public_coverage_grounded_count,
                "no_evidence_question_count": category_counts["NO_EVIDENCE"],
                "isolation_question_count": category_counts["ISOLATION"],
                "latency_p95_ms": round(_p95_ms(latencies_ms), 2),
                "question_count": len(outcomes),
                ("d6b_dataset_written" if is_d6b else "d5_dataset_written"): True,
                **index_context_summary,
            }
            if result_file is not None:
                write_aggregate_result(result_file, result)
            return result
        if selected_phase == "enterprise-capture":
            stage = "enterprise_capture"
            dataset = build_enterprise_capture_dataset(
                question_label, outcomes, top_k=enterprise_top_k
            )
            write_d5_capture_dataset(enterprise_dataset_file, dataset)
            category_counts = {
                category: sum(outcome.get("category") == category for outcome in outcomes)
                for category in ("GROUNDED", "NO_EVIDENCE", "ISOLATION")
            }
            complete_count = sum(
                outcome.get("diagnostic_candidate_count") == C4A_CANDIDATE_POOL_SIZE
                and outcome.get("diagnostic_chroma_candidate_count")
                == C4A_CANDIDATE_POOL_SIZE
                for outcome in outcomes
            )
            public_coverage_grounded_count = sum(
                outcome.get("category") == "GROUNDED"
                and any(
                    item_contains_expected_evidence(
                        item, outcome.get("expected_evidence")
                    )
                    for item in outcome.get("items", [])
                    if isinstance(item, dict)
                )
                for outcome in outcomes
            )
            result = {
                "candidate_pool_expected_count": C4A_CANDIDATE_POOL_SIZE,
                "candidate_pool_complete_question_count": complete_count,
                "candidate_pool_incomplete_question_count": len(outcomes) - complete_count,
                "grounded_question_count": category_counts["GROUNDED"],
                "public_coverage_grounded_count": public_coverage_grounded_count,
                "no_evidence_question_count": category_counts["NO_EVIDENCE"],
                "isolation_question_count": category_counts["ISOLATION"],
                "latency_p95_ms": round(_p95_ms(latencies_ms), 2),
                "question_count": len(outcomes),
                "enterprise_top_k": enterprise_top_k,
                "enterprise_dataset_written": True,
                **index_context_summary,
            }
            if result_file is not None:
                write_aggregate_result(result_file, result)
            return result
        if selected_phase == "d4-a-snapshot":
            stage = "d4_a_snapshot"
            snapshot = build_safe_snapshot(outcomes)
            # 快照先经过严格白名单投影与校验，再原子替换目标文件。
            write_safe_snapshot(snapshot_file, snapshot)
            category_counts = {
                category: sum(outcome.get("category") == category for outcome in outcomes)
                for category in ("GROUNDED", "NO_EVIDENCE", "ISOLATION")
            }
            result: dict[str, int | float | bool] = {
                "candidate_pool_expected_count": C4A_CANDIDATE_POOL_SIZE,
                "candidate_pool_complete_question_count": len(outcomes),
                "candidate_pool_incomplete_question_count": 0,
                "grounded_question_count": category_counts["GROUNDED"],
                "no_evidence_question_count": category_counts["NO_EVIDENCE"],
                "isolation_question_count": category_counts["ISOLATION"],
                "latency_p95_ms": round(_p95_ms(latencies_ms), 2),
                "question_count": len(outcomes),
                "snapshot_written": True,
                **index_context_summary,
            }
            if result_file is not None:
                write_aggregate_result(result_file, result)
            return result
        if selected_phase in {"c4-a", "c4-b"}:
            stage = (
                "c4_a_candidate_pool"
                if selected_phase == "c4-a"
                else "c4_b_query_expression"
            )
            retrieval_diagnostics = build_c4a_candidate_pool_diagnostics(outcomes)
            result = build_c4a_aggregate_result(
                outcomes,
                retrieval_diagnostics,
                latency_p95_ms=_p95_ms(latencies_ms),
                index_context_summary=index_context_summary,
            )
            if result_file is not None:
                # 先保存无敏感聚合指标，再进入可能较慢的跨存储清理。
                write_aggregate_result(result_file, result)
            if diagnostic_file is not None:
                write_c4a_diagnostic(
                    diagnostic_file,
                    retrieval_diagnostics=retrieval_diagnostics,
                    index_context_summary=index_context_summary,
                    latency_p95_ms=_p95_ms(latencies_ms),
                    stage=stage,
                    reranker_query_mode=_uniform_query_mode(outcomes),
                )
            return result
        retrieval_diagnostics = build_safe_retrieval_diagnostics(outcomes)
        stage = "threshold_calibration"
        threshold, score = choose_reranker_score_threshold(outcomes)
        result: dict[str, int | float | bool] = {
            **score,
            "reranker_score_threshold": round(threshold, 6),
            "latency_p95_ms": round(_p95_ms(latencies_ms), 2),
            "question_count": len(outcomes),
            **index_context_summary,
        }
        if result_file is not None:
            # 先保存无敏感聚合指标，再进入可能较慢的跨存储清理，避免外部运行时限丢失证据。
            write_aggregate_result(result_file, result)
        return result
    except BaseException as exc:
        primary_error = exc
        if d5_dataset_file is not None:
            try:
                d5_dataset_file.unlink(missing_ok=True)
            except OSError:
                pass
        if d6b_dataset_file is not None:
            try:
                d6b_dataset_file.unlink(missing_ok=True)
            except OSError:
                pass
        if enterprise_dataset_file is not None:
            try:
                enterprise_dataset_file.unlink(missing_ok=True)
            except OSError:
                pass
        if diagnostic_file is not None:
            write_safe_diagnostic(
                path=diagnostic_file,
                stage=stage,
                error=exc,
                retrieval_diagnostics=retrieval_diagnostics,
                index_context_summary=index_context_summary,
                latency_p95_ms=_p95_ms(latencies_ms) if latencies_ms else None,
            )
        raise
    finally:
        cleanup_error: BaseException | None = None
        if user_id is not None:
            try:
                _cleanup_seeded_scope(
                    api,
                    seeded=seeded,
                    project_ids=project_ids,
                    user_id=user_id,
                )
            except BaseException as exc:
                cleanup_error = exc
        api.close()
        if cleanup_error is not None:
            if d5_dataset_file is not None:
                try:
                    d5_dataset_file.unlink(missing_ok=True)
                except OSError:
                    pass
            if d6b_dataset_file is not None:
                try:
                    d6b_dataset_file.unlink(missing_ok=True)
                except OSError:
                    pass
            if enterprise_dataset_file is not None:
                try:
                    enterprise_dataset_file.unlink(missing_ok=True)
                except OSError:
                    pass
            # 清理失败意味着虚构验收资料可能残留，必须优先暴露稳定错误，不能被主流程异常掩盖。
            raise AcceptanceError("P14 虚构验收数据清理未全部完成。") from cleanup_error


def main() -> int:
    """运行命令行验收并仅输出无敏感信息的聚合结论。"""
    parser = argparse.ArgumentParser(description="运行 AV1-P14 真实检索验收。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--evaluation-root", type=Path)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--diagnostic-file", type=Path)
    parser.add_argument("--snapshot-file", type=Path)
    parser.add_argument("--d5-dataset-file", type=Path)
    parser.add_argument("--d6b-dataset-file", type=Path)
    parser.add_argument("--enterprise-dataset-file", type=Path)
    parser.add_argument("--enterprise-top-k", type=int, default=8)
    parser.add_argument(
        "--phase",
        choices=(
            "c4-a",
            "c4-b",
            "d4-a-snapshot",
            "threshold-calibration",
            "d5-capture",
            "d6b-capture",
            "enterprise-capture",
        ),
        default="c4-a",
        help="默认只执行 C4-A；其他阶段必须显式选择。",
    )
    args = parser.parse_args()
    try:
        result = run_retrieval_calibration(
            base_url=str(args.base_url),
            evaluation_root=args.evaluation_root,
            result_file=args.result_file,
            diagnostic_file=args.diagnostic_file,
            snapshot_file=args.snapshot_file,
            d5_dataset_file=args.d5_dataset_file,
            d6b_dataset_file=args.d6b_dataset_file,
            enterprise_dataset_file=args.enterprise_dataset_file,
            enterprise_top_k=args.enterprise_top_k,
            calibrate_threshold=args.phase == "threshold-calibration",
            phase=args.phase,
        )
    except (AcceptanceError, ValueError, httpx.HTTPError) as exc:
        print(f"P14 retrieval evaluation failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _item_distance(item: dict[str, object]) -> float:
    """将接口暴露的相关性分数还原为 Chroma cosine distance。"""
    try:
        distance = 1.0 - float(item["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("检索响应缺少有效 score。") from exc
    if not 0.0 <= distance <= 2.0:
        raise ValueError("检索响应 score 无法转换为有效 cosine distance。")
    return distance


def _filename_from_relative_path(value: object) -> str:
    """将 Ground Truth 中的相对路径规范为 API 返回的文件名。"""
    return Path(str(value)).name


def build_manual_field_payload(
    field_name: str, field_spec: dict[str, object] | None, *, expected_version: int
) -> dict[str, object]:
    """将人工 Ground Truth 转换为 P07 字段确认 API 的安全请求体。"""
    if expected_version < 1:
        raise ValueError("人工字段确认必须携带有效的文档版本。")
    if field_spec is None:
        return {
            "review_status": "EMPTY_ACCEPTED",
            "source": "MANUAL",
            "no_source_evidence": True,
            "evidences": [],
            "expected_version": expected_version,
        }
    if "value" not in field_spec:
        raise ValueError("人工字段标注缺少 value。")
    value = field_spec["value"]
    payload: dict[str, object] = {
        "review_status": "VALUE_CONFIRMED",
        "source": "MANUAL",
        "evidences": field_spec.get("evidence", []),
        "expected_version": expected_version,
    }
    if field_name == "DOCUMENT_DATE":
        payload["date_value"] = value
    elif field_name == "KEYWORDS":
        payload["json_value"] = value
    else:
        payload["text_value"] = value
    return payload


def item_contains_expected_evidence(
    item: dict[str, object], expected_evidence: object
) -> bool:
    """判断一个检索项是否包含人工标注的文件、定位和原文摘录。"""
    return _shared_item_contains_expected_evidence(item, expected_evidence)


def _diagnostic_candidate_kind(
    *,
    item: dict[str, object],
    expected_evidence: object,
    allowed_filenames: set[str],
) -> str:
    """将错误候选归为范围问题或可解释的检索问题，不输出其来源。"""
    filename = str(item.get("filename", ""))
    if filename not in allowed_filenames:
        return "OUT_OF_SCOPE"
    if isinstance(expected_evidence, dict) and filename == _filename_from_relative_path(
        expected_evidence.get("relative_path", "")
    ):
        return "SAME_DOCUMENT"
    return "OTHER_DOCUMENT"


def _dense_ranked_items(
    items: list[dict[str, object]],
) -> list[tuple[int, dict[str, object], float]]:
    """按兼容 dense score 还原 Chroma 候选顺序，保持阶段 A 的字段语义。"""
    ordered = sorted(
        items,
        key=lambda item: (
            _item_distance(item),
            str(item.get("chunk_id", "")),
        ),
    )
    return [
        (index, item, _item_distance(item))
        for index, item in enumerate(ordered, start=1)
    ]


def _reranker_ranked_items(
    items: list[dict[str, object]],
) -> list[tuple[int, dict[str, object], float]]:
    """读取服务已按 Reranker 排序的最终顺序，不重排也不改变检索结果。"""
    return [
        (index, item, _item_reranker_score(item))
        for index, item in enumerate(items, start=1)
    ]


def _internal_diagnostic_candidates(
    outcome: dict[str, object],
) -> list[dict[str, object]] | None:
    """读取服务端候选池的安全数值投影；旧结果没有该字段时返回空值。"""
    raw_candidates = outcome.get("internal_candidates")
    if raw_candidates is None:
        return None
    if not isinstance(raw_candidates, list):
        raise ValueError("问题结果缺少有效 internal_candidates 列表。")
    required = (
        "dense_rank",
        "dense_distance",
        "reranker_rank",
        "reranker_score",
        "matches_expected_evidence",
    )
    for candidate in raw_candidates:
        if not isinstance(candidate, dict) or any(key not in candidate for key in required):
            raise ValueError("问题结果包含不完整的候选诊断。")
        if (
            isinstance(candidate["dense_rank"], bool)
            or not isinstance(candidate["dense_rank"], int)
            or candidate["dense_rank"] < 1
            or isinstance(candidate["dense_distance"], bool)
            or not isinstance(candidate["dense_distance"], (int, float))
            or not math.isfinite(float(candidate["dense_distance"]))
            or isinstance(candidate["reranker_rank"], bool)
            or not isinstance(candidate["reranker_rank"], int)
            or candidate["reranker_rank"] < 1
            or isinstance(candidate["reranker_score"], bool)
            or not isinstance(candidate["reranker_score"], (int, float))
            or not math.isfinite(float(candidate["reranker_score"]))
            or not isinstance(candidate["matches_expected_evidence"], bool)
        ):
            raise ValueError("问题结果包含无效的候选诊断数值。")
    return raw_candidates


def build_safe_retrieval_diagnostics(
    outcomes: list[dict[str, object]],
) -> list[dict[str, object]]:
    """从固定集原始候选构造不含资源标识和正文的逐题诊断。"""
    diagnostics: list[dict[str, object]] = []
    grounded_case_count = 0
    for outcome in outcomes:
        category = outcome.get("category")
        if category not in {"GROUNDED", "NO_EVIDENCE", "ISOLATION"}:
            raise ValueError("固定问题集包含未知类别。")
        raw_items = outcome.get("items")
        if not isinstance(raw_items, list) or not all(
            isinstance(item, dict) for item in raw_items
        ):
            raise ValueError("问题结果缺少有效 items 列表。")
        allowed = outcome.get("allowed_filenames")
        if not isinstance(allowed, list) or not all(
            isinstance(filename, str) for filename in allowed
        ):
            raise ValueError("问题结果缺少允许的文件名范围。")
        allowed_filenames = set(allowed)
        expected_evidence = outcome.get("expected_evidence")
        if category == "GROUNDED" and not isinstance(expected_evidence, dict):
            raise ValueError("有据问题缺少标准证据。")

        items = [item for item in raw_items if isinstance(item, dict)]
        internal_candidates = _internal_diagnostic_candidates(outcome)
        dense_ranked_items: list[tuple[int, dict[str, object], float]] = []
        if internal_candidates is None:
            dense_ranked_items = _dense_ranked_items(items)
            expected_dense = next(
                (
                    (index, distance)
                    for index, item, distance in dense_ranked_items
                    if item_contains_expected_evidence(item, expected_evidence)
                ),
                None,
            )
            dense_incorrect = [
                (item, distance)
                for _, item, distance in dense_ranked_items
                if not item_contains_expected_evidence(item, expected_evidence)
            ]
            diagnostic_candidate_count = len(items)
            diagnostic_chroma_candidate_count: int | None = None
        else:
            dense_ranked_internal = sorted(
                internal_candidates,
                key=lambda candidate: int(candidate["dense_rank"]),
            )
            expected_internal = next(
                (
                    candidate
                    for candidate in dense_ranked_internal
                    if candidate["matches_expected_evidence"] is True
                ),
                None,
            )
            expected_dense = (
                (
                    int(expected_internal["dense_rank"]),
                    float(expected_internal["dense_distance"]),
                )
                if expected_internal is not None
                else None
            )
            dense_incorrect = [
                (None, float(candidate["dense_distance"]))
                for candidate in dense_ranked_internal
                if candidate["matches_expected_evidence"] is False
            ]
            diagnostic_candidate_count = int(
                outcome.get("diagnostic_candidate_count", len(internal_candidates))
            )
            diagnostic_chroma_candidate_count = outcome.get(
                "diagnostic_chroma_candidate_count"
            )
            if not isinstance(diagnostic_chroma_candidate_count, int):
                diagnostic_chroma_candidate_count = None
        nearest_candidate_distance = (
            round(
                min(
                    float(candidate["dense_distance"])
                    for candidate in internal_candidates
                ),
                6,
            )
            if internal_candidates
            else (
                round(min(distance for _, _, distance in dense_ranked_items), 6)
                if dense_ranked_items
                else None
            )
        )
        nearest_incorrect = min(dense_incorrect, key=lambda pair: pair[1], default=None)
        expected_distance = (
            round(expected_dense[1], 6) if expected_dense is not None else None
        )
        nearest_incorrect_distance = (
            round(nearest_incorrect[1], 6) if nearest_incorrect is not None else None
        )
        if internal_candidates is None:
            incorrect_candidate_kind = (
                _diagnostic_candidate_kind(
                    item=nearest_incorrect[0],
                    expected_evidence=expected_evidence,
                    allowed_filenames=allowed_filenames,
                )
                if nearest_incorrect is not None
                else "NONE"
            )
        else:
            nearest_internal_incorrect = min(
                (
                    candidate
                    for candidate in internal_candidates
                    if candidate["matches_expected_evidence"] is False
                ),
                key=lambda candidate: float(candidate["dense_distance"]),
                default=None,
            )
            incorrect_candidate_kind = (
                str(nearest_internal_incorrect.get("candidate_kind"))
                if nearest_internal_incorrect is not None
                and nearest_internal_incorrect.get("candidate_kind") is not None
                else ("UNKNOWN" if nearest_internal_incorrect is not None else "NONE")
            )
        diagnostic: dict[str, object] = {
            "category": category,
            "expected_candidate_rank": (
                expected_dense[0] if expected_dense is not None else None
            ),
            "expected_distance": expected_distance,
            "nearest_incorrect_distance": nearest_incorrect_distance,
            "distance_gap": (
                round(nearest_incorrect[1] - expected_dense[1], 6)
                if nearest_incorrect is not None and expected_dense is not None
                else None
            ),
            "incorrect_candidate_kind": incorrect_candidate_kind,
            "nearest_candidate_distance": nearest_candidate_distance,
        }
        if diagnostic_chroma_candidate_count is not None:
            diagnostic["chroma_candidate_count"] = diagnostic_chroma_candidate_count
        if category == "GROUNDED":
            grounded_case_count += 1
            if internal_candidates is None:
                reranker_ranked_items = _reranker_ranked_items(items)
                expected_reranker = next(
                    (
                        (index, score)
                        for index, item, score in reranker_ranked_items
                        if item_contains_expected_evidence(item, expected_evidence)
                    ),
                    None,
                )
                strongest_incorrect = next(
                    (
                        (item, score)
                        for _, item, score in reranker_ranked_items
                        if not item_contains_expected_evidence(item, expected_evidence)
                    ),
                    None,
                )
                strongest_incorrect_kind = (
                    _diagnostic_candidate_kind(
                        item=strongest_incorrect[0],
                        expected_evidence=expected_evidence,
                        allowed_filenames=allowed_filenames,
                    )
                    if strongest_incorrect is not None
                    else "NONE"
                )
            else:
                reranker_ranked_internal = sorted(
                    internal_candidates,
                    key=lambda candidate: int(candidate["reranker_rank"]),
                )
                expected_internal_reranker = next(
                    (
                        candidate
                        for candidate in reranker_ranked_internal
                        if candidate["matches_expected_evidence"] is True
                    ),
                    None,
                )
                strongest_internal_incorrect = next(
                    (
                        candidate
                        for candidate in reranker_ranked_internal
                        if candidate["matches_expected_evidence"] is False
                    ),
                    None,
                )
                expected_reranker = (
                    (
                        int(expected_internal_reranker["reranker_rank"]),
                        float(expected_internal_reranker["reranker_score"]),
                    )
                    if expected_internal_reranker is not None
                    else None
                )
                strongest_incorrect = (
                    (None, float(strongest_internal_incorrect["reranker_score"]))
                    if strongest_internal_incorrect is not None
                    else None
                )
                strongest_incorrect_kind = (
                    str(strongest_internal_incorrect.get("candidate_kind"))
                    if strongest_internal_incorrect is not None
                    and strongest_internal_incorrect.get("candidate_kind") is not None
                    else "UNKNOWN"
                )
            diagnostic.update(
                {
                    "case_id": f"GROUNDED-{grounded_case_count:02d}",
                    "candidate_count": diagnostic_candidate_count,
                    "expected_in_chroma_top_10": (
                        expected_dense is not None and expected_dense[0] <= 10
                    ),
                    "expected_reranker_rank": (
                        expected_reranker[0] if expected_reranker is not None else None
                    ),
                    "expected_reranker_score": (
                        round(expected_reranker[1], 6)
                        if expected_reranker is not None
                        else None
                    ),
                    "strongest_incorrect_reranker_score": (
                        round(strongest_incorrect[1], 6)
                        if strongest_incorrect is not None
                        else None
                    ),
                    "reranker_score_gap": (
                        round(expected_reranker[1] - strongest_incorrect[1], 6)
                        if expected_reranker is not None and strongest_incorrect is not None
                        else None
                    ),
                    "strongest_reranker_incorrect_kind": strongest_incorrect_kind,
                }
            )
        elif category == "NO_EVIDENCE":
            if internal_candidates is None:
                reranker_ranked_items = _reranker_ranked_items(items)
                strongest_candidate = (
                    reranker_ranked_items[0] if reranker_ranked_items else None
                )
                strongest_reranker_score = (
                    round(strongest_candidate[2], 6)
                    if strongest_candidate is not None
                    else None
                )
                strongest_dense_distance = (
                    round(_item_distance(strongest_candidate[1]), 6)
                    if strongest_candidate is not None
                    else None
                )
            else:
                strongest_internal = min(
                    internal_candidates,
                    key=lambda candidate: int(candidate["reranker_rank"]),
                    default=None,
                )
                strongest_reranker_score = (
                    round(float(strongest_internal["reranker_score"]), 6)
                    if strongest_internal is not None
                    else None
                )
                strongest_dense_distance = (
                    round(float(strongest_internal["dense_distance"]), 6)
                    if strongest_internal is not None
                    else None
                )
            diagnostic.update(
                {
                    "candidate_count": diagnostic_candidate_count,
                    "strongest_candidate_reranker_score": strongest_reranker_score,
                    "strongest_candidate_dense_distance": strongest_dense_distance,
                }
            )
        diagnostics.append(diagnostic)
    return diagnostics


def build_c4a_candidate_pool_diagnostics(
    outcomes: list[dict[str, object]],
) -> list[dict[str, object]]:
    """为 C4-A 增加 Top-30 完整性和 Top-20 无据基线对照。"""
    diagnostics = build_safe_retrieval_diagnostics(outcomes)
    no_evidence_index = 0
    for outcome, diagnostic in zip(outcomes, diagnostics, strict=True):
        # 隔离题不需要标准证据字段，但 C4-A 仍需展示安全的候选池数量。
        if "candidate_count" not in diagnostic:
            candidate_count = outcome.get("diagnostic_candidate_count")
            chroma_candidate_count = outcome.get("diagnostic_chroma_candidate_count")
            if isinstance(candidate_count, int) and not isinstance(candidate_count, bool):
                diagnostic["candidate_count"] = candidate_count
            if isinstance(chroma_candidate_count, int) and not isinstance(
                chroma_candidate_count, bool
            ):
                diagnostic["chroma_candidate_count"] = chroma_candidate_count
        candidate_count = diagnostic.get("candidate_count")
        chroma_candidate_count = diagnostic.get("chroma_candidate_count")
        candidate_pool_complete = (
            candidate_count == C4A_CANDIDATE_POOL_SIZE
            and chroma_candidate_count == C4A_CANDIDATE_POOL_SIZE
        )
        diagnostic["candidate_pool_expected_count"] = C4A_CANDIDATE_POOL_SIZE
        diagnostic["candidate_pool_complete"] = candidate_pool_complete
        if diagnostic.get("category") == "GROUNDED":
            expected_evidence = outcome.get("expected_evidence")
            public_result_contains_expected_evidence = any(
                item_contains_expected_evidence(item, expected_evidence)
                for item in outcome.get("items", [])
                if isinstance(item, dict)
            )
            diagnostic["standard_evidence_in_complete_top_30"] = bool(
                candidate_pool_complete
                and diagnostic.get("expected_candidate_rank") is not None
            )
            diagnostic["public_result_contains_expected_evidence"] = (
                public_result_contains_expected_evidence
            )
            diagnostic["matching_semantics_disagree"] = bool(
                candidate_pool_complete
                and public_result_contains_expected_evidence
                and diagnostic["standard_evidence_in_complete_top_30"] is False
            )
        elif diagnostic.get("category") == "NO_EVIDENCE":
            if no_evidence_index >= len(C4A_TOP20_NO_EVIDENCE_BASELINE):
                raise ValueError("固定集无据问题数量超过 Top-20 对照基线。")
            baseline = C4A_TOP20_NO_EVIDENCE_BASELINE[no_evidence_index]
            diagnostic["case_id"] = f"NO_EVIDENCE-{no_evidence_index + 1:02d}"
            diagnostic.update(
                {
                    "top20_baseline_strongest_candidate_reranker_score": baseline[
                        "strongest_candidate_reranker_score"
                    ],
                    "top20_baseline_strongest_candidate_dense_distance": baseline[
                        "strongest_candidate_dense_distance"
                    ],
                    "reranker_score_delta_vs_top20": (
                        round(
                            float(diagnostic["strongest_candidate_reranker_score"])
                            - float(baseline["strongest_candidate_reranker_score"]),
                            6,
                        )
                        if diagnostic["strongest_candidate_reranker_score"] is not None
                        else None
                    ),
                    "dense_distance_delta_vs_top20": (
                        round(
                            float(diagnostic["strongest_candidate_dense_distance"])
                            - float(baseline["strongest_candidate_dense_distance"]),
                            6,
                        )
                        if diagnostic["strongest_candidate_dense_distance"] is not None
                        else None
                    ),
                }
            )
            no_evidence_index += 1
    return diagnostics


def build_c4a_aggregate_result(
    outcomes: list[dict[str, object]],
    diagnostics: list[dict[str, object]],
    *,
    latency_p95_ms: float,
    index_context_summary: dict[str, int],
) -> dict[str, int | float | bool]:
    """构造只观测候选池的 C4-A 聚合结果，不生成重排阈值。"""
    scores = [
        _item_reranker_score(item)
        for outcome in outcomes
        for item in outcome.get("items", [])
        if isinstance(item, dict)
    ]
    if not scores:
        raise ValueError("固定问题集没有可用于 C4-A 聚合的重排分数。")
    unfiltered_score = score_reranker_outcomes(outcomes, threshold=min(scores))
    grounded_diagnostics = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.get("category") == "GROUNDED"
    ]
    grounded_in_pool = sum(
        diagnostic.get("standard_evidence_in_complete_top_30") is True
        for diagnostic in grounded_diagnostics
    )
    grounded_public_coverage = sum(
        diagnostic.get("public_result_contains_expected_evidence") is True
        for diagnostic in grounded_diagnostics
    )
    matching_semantics_disagreement = sum(
        diagnostic.get("matching_semantics_disagree") is True
        for diagnostic in grounded_diagnostics
    )
    complete_pool_count = sum(
        diagnostic.get("candidate_pool_complete") is True
        for diagnostic in diagnostics
    )
    result: dict[str, int | float | bool] = {
        "candidate_pool_expected_count": C4A_CANDIDATE_POOL_SIZE,
        "candidate_pool_complete_question_count": complete_pool_count,
        "candidate_pool_incomplete_question_count": len(diagnostics) - complete_pool_count,
        "grounded_candidate_pool_hits": grounded_in_pool,
        "grounded_candidate_pool_total": len(grounded_diagnostics),
        "grounded_public_coverage_hits": grounded_public_coverage,
        "grounded_matching_semantics_disagreement_count": matching_semantics_disagreement,
        "grounded_returned_count": int(unfiltered_score["grounded_passed"]),
        "no_evidence_rejected_count": int(unfiltered_score["no_evidence_passed"]),
        "isolation_passed_count": int(unfiltered_score["isolation_passed"]),
        "quality_gate_at_current_unfiltered_results": bool(unfiltered_score["passed"]),
        "latency_p95_ms": round(float(latency_p95_ms), 2),
        "question_count": len(outcomes),
        **index_context_summary,
    }
    return result


def write_c4a_diagnostic(
    path: Path,
    *,
    retrieval_diagnostics: list[dict[str, object]],
    index_context_summary: dict[str, int],
    latency_p95_ms: float,
    stage: str = "c4_a_candidate_pool",
    reranker_query_mode: str | None = None,
) -> None:
    """保存 C4-A/C4-B 观测，内容仅包含安全聚合与数值投影。"""
    if stage not in {"c4_a_candidate_pool", "c4_b_query_expression"}:
        raise ValueError("C4 观测阶段标识无效。")
    diagnostic: dict[str, object] = {
        "outcome": "observed",
        "stage": stage,
        "candidate_pool_expected_count": C4A_CANDIDATE_POOL_SIZE,
        "retrieval_diagnostics": retrieval_diagnostics,
        "index_context_summary": index_context_summary,
        "latency_p95_ms": round(float(latency_p95_ms), 2),
    }
    if reranker_query_mode is not None:
        if reranker_query_mode not in {"c4_a", "c4_b"}:
            raise ValueError("Reranker 查询模式标识无效。")
        diagnostic["reranker_query_mode"] = reranker_query_mode
    path.write_text(
        json.dumps(diagnostic, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _uniform_query_mode(outcomes: list[dict[str, object]]) -> str | None:
    """读取固定集诊断返回的统一 Reranker 查询模式。"""
    modes = {
        outcome.get("reranker_query_mode")
        for outcome in outcomes
        if outcome.get("reranker_query_mode") is not None
    }
    if len(modes) == 1:
        mode = next(iter(modes))
        if isinstance(mode, str):
            return mode
    return None


def _filtered_items(outcome: dict[str, object], threshold: float) -> list[dict[str, object]]:
    """按候选最大 distance 复现服务端阈值过滤，不改变原始响应。"""
    raw_items = outcome.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("问题结果缺少 items 列表。")
    items = [item for item in raw_items if isinstance(item, dict)]
    if len(items) != len(raw_items):
        raise ValueError("问题结果包含无效检索项。")
    return [item for item in items if _item_distance(item) <= threshold]


def score_retrieval_outcomes(
    outcomes: list[dict[str, object]], *, threshold: float
) -> dict[str, int | bool]:
    """按照需求 12.3 对固定问题集进行纯检索层评分。"""
    if threshold < 0:
        raise ValueError("distance threshold 不能小于 0。")

    grounded_total = grounded_passed = 0
    no_evidence_total = no_evidence_passed = 0
    isolation_total = isolation_passed = 0
    for outcome in outcomes:
        category = outcome.get("category")
        items = _filtered_items(outcome, threshold)
        if category == "GROUNDED":
            grounded_total += 1
            expected = outcome.get("expected_evidence")
            if any(item_contains_expected_evidence(item, expected) for item in items):
                grounded_passed += 1
        elif category == "NO_EVIDENCE":
            no_evidence_total += 1
            if not items:
                no_evidence_passed += 1
        elif category == "ISOLATION":
            isolation_total += 1
            hidden = outcome.get("hidden_evidence_in_other_project")
            hidden_filename = (
                _filename_from_relative_path(hidden.get("relative_path", ""))
                if isinstance(hidden, dict)
                else ""
            )
            allowed_filenames = outcome.get("allowed_filenames")
            allowed = (
                {str(filename) for filename in allowed_filenames}
                if isinstance(allowed_filenames, list)
                else None
            )
            filenames = {str(item.get("filename")) for item in items}
            hides_other_project = hidden_filename not in filenames
            stays_in_current_project = allowed is None or filenames <= allowed
            if hides_other_project and stays_in_current_project:
                isolation_passed += 1
        else:
            raise ValueError("固定问题集包含未知类别。")

    passed = (
        grounded_total == 8
        and grounded_passed >= 7
        and no_evidence_total == 2
        and no_evidence_passed == 2
        and isolation_total == 2
        and isolation_passed == 2
    )
    return {
        "grounded_total": grounded_total,
        "grounded_passed": grounded_passed,
        "no_evidence_total": no_evidence_total,
        "no_evidence_passed": no_evidence_passed,
        "isolation_total": isolation_total,
        "isolation_passed": isolation_passed,
        "passed": passed,
    }


def score_at_no_evidence_ceiling(
    outcomes: list[dict[str, object]],
) -> dict[str, int | bool]:
    """计算过滤所有无依据候选时可达到的最大有据召回。"""
    no_evidence_distances = [
        _item_distance(item)
        for outcome in outcomes
        if outcome.get("category") == "NO_EVIDENCE"
        for item in outcome.get("items", [])
        if isinstance(item, dict)
    ]
    if not no_evidence_distances:
        raise ValueError("无依据问题没有可用于阈值诊断的候选。")
    # 服务端保留 distance <= threshold，因此取最小无依据候选距离的前一可表示浮点数。
    threshold = math.nextafter(min(no_evidence_distances), 0.0)
    return score_retrieval_outcomes(outcomes, threshold=threshold)


def choose_distance_threshold(
    outcomes: list[dict[str, object]],
) -> tuple[float, dict[str, int | bool]]:
    """选择满足所有门槛且尽可能宽松的固定 cosine distance 阈值。"""
    distances = sorted(
        {
            _item_distance(item)
            for outcome in outcomes
            for item in outcome.get("items", [])
            if isinstance(item, dict)
        }
    )
    if not distances:
        raise ValueError("固定问题集没有可用于标定的检索候选。")

    candidates = [0.0, *distances]
    passing: list[tuple[float, dict[str, int | bool]]] = []
    for candidate in candidates:
        score = score_retrieval_outcomes(outcomes, threshold=candidate)
        if score["passed"]:
            passing.append((candidate, score))
    if not passing:
        raw_score = score_retrieval_outcomes(outcomes, threshold=max(distances))
        no_evidence_ceiling_score = score_at_no_evidence_ceiling(outcomes)
        raise ValueError(
            "不存在同时满足固定问题集门槛的 distance threshold："
            f"grounded={raw_score['grounded_passed']}/{raw_score['grounded_total']}，"
            f"no_evidence={raw_score['no_evidence_passed']}/{raw_score['no_evidence_total']}，"
            f"isolation={raw_score['isolation_passed']}/{raw_score['isolation_total']}；"
            "拒绝全部无依据候选时："
            f"grounded={no_evidence_ceiling_score['grounded_passed']}/"
            f"{no_evidence_ceiling_score['grounded_total']}，"
            f"no_evidence={no_evidence_ceiling_score['no_evidence_passed']}/"
            f"{no_evidence_ceiling_score['no_evidence_total']}，"
            f"isolation={no_evidence_ceiling_score['isolation_passed']}/"
            f"{no_evidence_ceiling_score['isolation_total']}。"
        )

    # 同等通过时选择最大阈值，最大化保留已验证相关证据的余量。
    return max(passing, key=lambda value: value[0])


def _item_reranker_score(item: dict[str, object]) -> float:
    """读取正式检索响应中用于阶段 C 标定的最终重排分数。"""
    value = item.get("reranker_score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("正式检索响应缺少有效 reranker_score。")
    score = float(value)
    if not math.isfinite(score):
        raise ValueError("正式检索响应的 reranker_score 必须是有限数。")
    return score


def _filtered_reranked_items(
    outcome: dict[str, object], threshold: float
) -> list[dict[str, object]]:
    """按最低重排分数复现阶段 C 的最终候选过滤。"""
    raw_items = outcome.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("问题结果缺少 items 列表。")
    items = [item for item in raw_items if isinstance(item, dict)]
    if len(items) != len(raw_items):
        raise ValueError("问题结果包含无效检索项。")
    return [item for item in items if _item_reranker_score(item) >= threshold]


def score_reranker_outcomes(
    outcomes: list[dict[str, object]], *, threshold: float
) -> dict[str, int | bool]:
    """按照阶段 C 的独立重排分数门槛对固定问题集评分。"""
    if not math.isfinite(threshold):
        raise ValueError("reranker score threshold 必须是有限数。")

    grounded_total = grounded_passed = 0
    no_evidence_total = no_evidence_passed = 0
    isolation_total = isolation_passed = 0
    for outcome in outcomes:
        category = outcome.get("category")
        items = _filtered_reranked_items(outcome, threshold)
        if category == "GROUNDED":
            grounded_total += 1
            expected = outcome.get("expected_evidence")
            if any(item_contains_expected_evidence(item, expected) for item in items):
                grounded_passed += 1
        elif category == "NO_EVIDENCE":
            no_evidence_total += 1
            if not items:
                no_evidence_passed += 1
        elif category == "ISOLATION":
            isolation_total += 1
            hidden = outcome.get("hidden_evidence_in_other_project")
            hidden_filename = (
                _filename_from_relative_path(hidden.get("relative_path", ""))
                if isinstance(hidden, dict)
                else ""
            )
            allowed_filenames = outcome.get("allowed_filenames")
            allowed = (
                {str(filename) for filename in allowed_filenames}
                if isinstance(allowed_filenames, list)
                else None
            )
            filenames = {str(item.get("filename")) for item in items}
            hides_other_project = hidden_filename not in filenames
            stays_in_current_project = allowed is None or filenames <= allowed
            if hides_other_project and stays_in_current_project:
                isolation_passed += 1
        else:
            raise ValueError("固定问题集包含未知类别。")

    passed = (
        grounded_total == 8
        and grounded_passed >= 7
        and no_evidence_total == 2
        and no_evidence_passed == 2
        and isolation_total == 2
        and isolation_passed == 2
    )
    return {
        "grounded_total": grounded_total,
        "grounded_passed": grounded_passed,
        "no_evidence_total": no_evidence_total,
        "no_evidence_passed": no_evidence_passed,
        "isolation_total": isolation_total,
        "isolation_passed": isolation_passed,
        "passed": passed,
    }


def choose_reranker_score_threshold(
    outcomes: list[dict[str, object]],
) -> tuple[float, dict[str, int | bool]]:
    """选择满足固定门槛且尽可能宽松的最低重排分数。"""
    scores = sorted(
        {
            _item_reranker_score(item)
            for outcome in outcomes
            for item in outcome.get("items", [])
            if isinstance(item, dict)
        }
    )
    if not scores:
        raise ValueError("固定问题集没有可用于标定的重排分数。")

    passing: list[tuple[float, dict[str, int | bool]]] = []
    for candidate in scores:
        score = score_reranker_outcomes(outcomes, threshold=candidate)
        if score["passed"]:
            passing.append((candidate, score))
    if not passing:
        raw_score = score_reranker_outcomes(outcomes, threshold=min(scores))
        no_evidence_scores = [
            _item_reranker_score(item)
            for outcome in outcomes
            if outcome.get("category") == "NO_EVIDENCE"
            for item in outcome.get("items", [])
            if isinstance(item, dict)
        ]
        if not no_evidence_scores:
            raise ValueError("无依据问题没有可用于阈值诊断的重排分数。")
        ceiling_score = score_reranker_outcomes(
            outcomes,
            threshold=math.nextafter(max(no_evidence_scores), math.inf),
        )
        raise ValueError(
            "不存在同时满足固定问题集门槛的 reranker score threshold："
            f"grounded={raw_score['grounded_passed']}/{raw_score['grounded_total']}，"
            f"no_evidence={raw_score['no_evidence_passed']}/{raw_score['no_evidence_total']}，"
            f"isolation={raw_score['isolation_passed']}/{raw_score['isolation_total']}；"
            "拒绝全部无依据候选时："
            f"grounded={ceiling_score['grounded_passed']}/"
            f"{ceiling_score['grounded_total']}，"
            f"no_evidence={ceiling_score['no_evidence_passed']}/"
            f"{ceiling_score['no_evidence_total']}，"
            f"isolation={ceiling_score['isolation_passed']}/"
            f"{ceiling_score['isolation_total']}。"
        )

    # 分数越高，候选越严格；同样通过时选择最小阈值以最大化保留已验证证据。
    return min(passing, key=lambda value: value[0])


if __name__ == "__main__":
    raise SystemExit(main())
