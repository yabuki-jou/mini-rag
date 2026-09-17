"""提供 FR-042 参考 Trace 使用的虚构且脱敏世界数据。"""

from copy import deepcopy
from datetime import date
from typing import Any

from app.models import ArchiveDocumentType, ProjectStage
from app.services.archive.catalog import AgentCatalogPage


_REFERENCE_DOCUMENT_DATE = date(2026, 4, 18)
_REFERENCE_ORGANIZATION = "North Star Build Lab"
_REFERENCE_CATALOG_ITEM: dict[str, object] = {
    "document_ref": "A1",
    "filename": "PX-BETA-cover.txt",
    "confirmed_at": "2026-04-20T08:30:00+00:00",
    "fields": {
        "TITLE": {
            "value": "PX-BETA 归档责任说明",
            "source": "MANUAL",
            "has_source_evidence": True,
        },
        "DOCUMENT_TYPE": {
            "value": "CONTRACT",
            "source": "MANUAL",
            "has_source_evidence": True,
        },
        "DOCUMENT_DATE": {
            "value": _REFERENCE_DOCUMENT_DATE.isoformat(),
            "source": "MANUAL",
            "has_source_evidence": True,
        },
        "AUTHORING_ORGANIZATION": {
            "value": _REFERENCE_ORGANIZATION,
            "source": "MANUAL",
            "has_source_evidence": True,
        },
        "PROJECT_STAGE": {
            "value": "CONSTRUCTION",
            "source": "MANUAL",
            "has_source_evidence": True,
        },
    },
}
_REFERENCE_EVIDENCE: list[dict[str, object]] = [
    {
        "document_ref": "D1",
        "filename": "PX-BETA-cover.txt",
        "document_title": "PX-BETA 归档责任说明",
        "location_type": "TEXT_LINE_RANGE",
        "location_start": 2,
        "location_end": 2,
        "excerpt": (
            "Fictional project PX-BETA archive responsibility unit: "
            "North Star Build Lab."
        ),
    },
    {
        "document_ref": "D2",
        "filename": "PX-BETA-timeline.txt",
        "document_title": "PX-BETA 归档索引复核计划",
        "location_type": "TEXT_LINE_RANGE",
        "location_start": 8,
        "location_end": 9,
        "excerpt": (
            "Fictional project PX-BETA milestone: archive index review "
            "planned for cycle 3."
        ),
    },
]


def reference_catalog_result(
    *,
    page: int,
    page_size: int,
    document_type: ArchiveDocumentType | None = None,
    project_stage: ProjectStage | None = None,
    document_date_from: date | None = None,
    document_date_to: date | None = None,
    document_date_is_null: bool = False,
    authoring_organization: str | None = None,
    **_scope: Any,
) -> AgentCatalogPage:
    """按生产目录参数筛选一份既有 PX-BETA 虚构档案。"""
    matches = (
        not document_date_is_null
        and document_type in {None, ArchiveDocumentType.CONTRACT}
        and project_stage in {None, ProjectStage.CONSTRUCTION}
        and (document_date_from is None or document_date_from <= _REFERENCE_DOCUMENT_DATE)
        and (document_date_to is None or _REFERENCE_DOCUMENT_DATE <= document_date_to)
        and (
            authoring_organization is None
            or authoring_organization.casefold() in _REFERENCE_ORGANIZATION.casefold()
        )
    )
    items = [deepcopy(_REFERENCE_CATALOG_ITEM)] if matches else []
    start = (page - 1) * page_size
    return AgentCatalogPage(
        page=page,
        page_size=page_size,
        total=len(items),
        items=items[start : start + page_size],
    )


def reference_evidence_result(**_scope: Any) -> list[dict[str, object]]:
    """返回既有 PX-BETA 两条安全候选，不携带持久化标识和分数。"""
    return deepcopy(_REFERENCE_EVIDENCE)


__all__ = ["reference_catalog_result", "reference_evidence_result"]
