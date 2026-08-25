"""验证 FR-031 清单项 HTTP 契约的输入边界。"""

import pytest
from pydantic import ValidationError

from app.models import ArchiveDocumentType, ProjectStage
from app.schemas import ChecklistItemCreate, ChecklistItemUpdate


def test_create_schema_normalizes_name_and_requires_project_version() -> None:
    """创建请求必须规范化名称，并要求客户端提交读取到的项目版本。"""
    payload = ChecklistItemCreate(
        name="  施工方案  ",
        document_type=ArchiveDocumentType.CONSTRUCTION,
        is_required=True,
        project_stage=ProjectStage.CONSTRUCTION,
        expected_project_version=1,
    )

    assert payload.name == "施工方案"
    assert payload.expected_project_version == 1

    with pytest.raises(ValidationError, match="expected_project_version"):
        ChecklistItemCreate(
            name="施工方案",
            document_type=ArchiveDocumentType.CONSTRUCTION,
            is_required=True,
            project_stage=ProjectStage.CONSTRUCTION,
        )


def test_create_schema_rejects_blank_name_and_unknown_fields() -> None:
    """客户端不能创建空白清单项，也不能伪造服务端管理字段。"""
    base_payload = {
        "name": "   ",
        "document_type": "CONSTRUCTION",
        "is_required": True,
        "project_stage": "CONSTRUCTION",
        "expected_project_version": 1,
    }
    with pytest.raises(ValidationError, match="清单项名称不能为空"):
        ChecklistItemCreate(**base_payload)

    base_payload["name"] = "施工方案"
    base_payload["project_id"] = "client-must-not-set-this"
    with pytest.raises(ValidationError, match="project_id"):
        ChecklistItemCreate(**base_payload)


def test_update_schema_accepts_false_and_explicit_description_clear() -> None:
    """可选属性可改为 false，说明字段显式传 null 则表示清空。"""
    payload = ChecklistItemUpdate(
        is_required=False,
        description=None,
        expected_version=2,
    )

    assert payload.is_required is False
    assert "is_required" in payload.model_fields_set
    assert "description" in payload.model_fields_set


def test_update_schema_rejects_version_only_request() -> None:
    """仅携带版本号不会形成有效业务修改，必须被请求契约拒绝。"""
    with pytest.raises(ValidationError, match="至少提交一个可修改的清单项字段"):
        ChecklistItemUpdate(expected_version=1)


@pytest.mark.parametrize("field_name", ["name", "document_type", "is_required", "project_stage"])
def test_update_schema_rejects_null_for_persisted_required_fields(field_name: str) -> None:
    """只允许用未提交字段表示不修改，不能以 null 覆盖数据库非空列。"""
    with pytest.raises(ValidationError, match=f"{field_name} 不能为 null"):
        ChecklistItemUpdate(expected_version=1, **{field_name: None})
