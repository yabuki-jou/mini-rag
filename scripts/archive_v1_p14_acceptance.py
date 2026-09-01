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
) -> tuple[dict[str, list[str]], list[int]]:
    """经 P05/P06/P07/P09 路由准备两项目的真实正式档案。"""
    projects = _as_object_list(document_label.get("projects"), name="projects")
    normal_documents = _as_object_list(
        document_label.get("normal_documents"), name="normal_documents"
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
            expected_fields = document["expected_fields"]
        except (KeyError, TypeError) as exc:
            raise AcceptanceError("P14 文档标注缺少项目、路径或字段。") from exc
        if not isinstance(expected_fields, dict):
            raise AcceptanceError("P14 文档字段标注格式无效。")
        path = EVALUATION_ROOT / relative_path
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
) -> tuple[list[dict[str, object]], list[float]]:
    """在未过滤候选的服务上收集固定问题集响应和请求耗时。"""
    questions = _as_object_list(question_label.get("questions"), name="questions")
    outcomes: list[dict[str, object]] = []
    latencies_ms: list[float] = []
    for question in questions:
        try:
            project_key = str(question["project_id"])
            project_id = project_ids[project_key]
            category = str(question["category"])
            content = str(question["question"])
        except KeyError as exc:
            raise AcceptanceError("P14 问题标注缺少项目、类别或问题。") from exc
        started_at = time.perf_counter()
        response = _require_object(
            api.request(
                "POST",
                f"/projects/{project_id}/archive-retrieval",
                expected_statuses=(200,),
                payload={"query": content, "top_k": 10},
            ),
            step="正式检索",
        )
        latencies_ms.append((time.perf_counter() - started_at) * 1000)
        raw_items = response.get("items")
        if not isinstance(raw_items, list) or not all(
            isinstance(item, dict) for item in raw_items
        ):
            raise AcceptanceError("正式检索响应缺少有效 items。")
        outcome: dict[str, object] = {
            "category": category,
            "project_id": project_key,
            "items": raw_items,
            "allowed_filenames": filenames_by_project[project_key],
        }
        for optional_key in ("expected_evidence", "hidden_evidence_in_other_project"):
            if optional_key in question:
                outcome[optional_key] = question[optional_key]
        outcomes.append(outcome)
    return outcomes, latencies_ms


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


def write_safe_diagnostic(
    path: Path,
    *,
    stage: str,
    error: BaseException,
    retrieval_diagnostics: list[dict[str, object]] | None = None,
    index_context_summary: dict[str, int] | None = None,
) -> None:
    """保存不含请求路径、资源 ID 或正文的最小失败诊断。"""
    diagnostic: dict[str, str] = {
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
    result_file: Path | None = None,
    diagnostic_file: Path | None = None,
) -> dict[str, int | float | bool]:
    """执行完整真实检索链路并返回仅含聚合指标的 P11 验收结果。"""
    question_label = _read_label(QUESTION_LABEL_PATH)
    document_label = _read_label(DOCUMENT_LABEL_PATH)
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
    try:
        user_id, _ = _register_and_login(api, run_tag=run_tag)
        stage = "seed_confirmation"
        filenames_by_project, index_context_counts = _seed_confirmed_documents(
            api,
            document_label=document_label,
            run_tag=run_tag,
            project_ids=project_ids,
            seeded=seeded,
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
        )
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
        if diagnostic_file is not None:
            write_safe_diagnostic(
                path=diagnostic_file,
                stage=stage,
                error=exc,
                retrieval_diagnostics=retrieval_diagnostics,
                index_context_summary=index_context_summary,
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
            # 清理失败意味着虚构验收资料可能残留，必须优先暴露稳定错误，不能被主流程异常掩盖。
            raise AcceptanceError("P14 虚构验收数据清理未全部完成。") from cleanup_error


def main() -> int:
    """运行命令行验收并仅输出无敏感信息的聚合结论。"""
    parser = argparse.ArgumentParser(description="运行 AV1-P14 真实检索验收。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--diagnostic-file", type=Path)
    args = parser.parse_args()
    try:
        result = run_retrieval_calibration(
            base_url=str(args.base_url),
            result_file=args.result_file,
            diagnostic_file=args.diagnostic_file,
        )
    except (AcceptanceError, ValueError, httpx.HTTPError) as exc:
        print(f"P14 retrieval evaluation failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _normalized_text(value: object) -> str:
    """以稳定的空白与大小写规则比较人工标注和检索摘录。"""
    return re.sub(r"\s+", "", str(value)).casefold()


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
    if not isinstance(expected_evidence, dict):
        return False
    if str(item.get("filename")) != _filename_from_relative_path(
        expected_evidence.get("relative_path", "")
    ):
        return False
    expected_items = expected_evidence.get("items")
    if not isinstance(expected_items, list) or not expected_items:
        return False

    for expected in expected_items:
        if not isinstance(expected, dict):
            return False
        try:
            same_location_type = str(item["location_type"]) == str(
                expected["location_type"]
            )
            contains_location = (
                int(item["location_start"])
                <= int(expected["location_start"])
                <= int(expected["location_end"])
                <= int(item["location_end"])
            )
            contains_excerpt = _normalized_text(expected["excerpt"]) in _normalized_text(
                item["excerpt"]
            )
        except (KeyError, TypeError, ValueError):
            return False
        if not (same_location_type and contains_location and contains_excerpt):
            return False
    return True


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
        nearest_candidate_distance = (
            round(min(distance for _, _, distance in dense_ranked_items), 6)
            if dense_ranked_items
            else None
        )
        nearest_incorrect = min(dense_incorrect, key=lambda pair: pair[1], default=None)
        expected_distance = (
            round(expected_dense[1], 6) if expected_dense is not None else None
        )
        nearest_incorrect_distance = (
            round(nearest_incorrect[1], 6) if nearest_incorrect is not None else None
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
            "incorrect_candidate_kind": (
                _diagnostic_candidate_kind(
                    item=nearest_incorrect[0],
                    expected_evidence=expected_evidence,
                    allowed_filenames=allowed_filenames,
                )
                if nearest_incorrect is not None
                else "NONE"
            ),
            "nearest_candidate_distance": nearest_candidate_distance,
        }
        if category == "GROUNDED":
            grounded_case_count += 1
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
            diagnostic.update(
                {
                    "case_id": f"GROUNDED-{grounded_case_count:02d}",
                    "candidate_count": len(items),
                    "expected_in_chroma_top_10": expected_dense is not None,
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
                    "strongest_reranker_incorrect_kind": (
                        _diagnostic_candidate_kind(
                            item=strongest_incorrect[0],
                            expected_evidence=expected_evidence,
                            allowed_filenames=allowed_filenames,
                        )
                        if strongest_incorrect is not None
                        else "NONE"
                    ),
                }
            )
        elif category == "NO_EVIDENCE":
            reranker_ranked_items = _reranker_ranked_items(items)
            strongest_candidate = (
                reranker_ranked_items[0] if reranker_ranked_items else None
            )
            diagnostic.update(
                {
                    "candidate_count": len(items),
                    "strongest_candidate_reranker_score": (
                        round(strongest_candidate[2], 6)
                        if strongest_candidate is not None
                        else None
                    ),
                    "strongest_candidate_dense_distance": (
                        round(_item_distance(strongest_candidate[1]), 6)
                        if strongest_candidate is not None
                        else None
                    ),
                }
            )
        diagnostics.append(diagnostic)
    return diagnostics


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
