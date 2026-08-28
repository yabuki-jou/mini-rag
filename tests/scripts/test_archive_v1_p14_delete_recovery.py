"""验证 P14 真实跨存储删除恢复验收脚本的纯判定规则。"""

from scripts.archive_v1_p14_delete_recovery import (
    file_cleanup_verified,
    is_conservative_incomplete_delete_response,
    recovery_passed,
)


def test_incomplete_delete_requires_only_the_public_stable_error_code() -> None:
    """故障服务只能以公开的删除未完成错误作为恢复起点。"""
    assert is_conservative_incomplete_delete_response(
        {"error": {"code": "DOCUMENT_DELETE_INCOMPLETE"}}
    )
    assert not is_conservative_incomplete_delete_response(
        {"error": {"code": "VECTOR_UNAVAILABLE"}}
    )
    assert not is_conservative_incomplete_delete_response({"code": "DOCUMENT_DELETE_INCOMPLETE"})


def test_recovery_requires_every_persistent_layer_check() -> None:
    """P13 不能仅凭重试返回 204 就声称跨存储恢复成功。"""
    checks = {
        "failure_reported": True,
        "visibility_blocked": True,
        "file_preserved_before_retry": True,
        "recovery_delete_succeeded": True,
        "postgres_zero": True,
        "chroma_zero": True,
        "file_zero": True,
        "audit_retained": True,
        "cleanup_completed": True,
    }

    assert recovery_passed(checks)
    assert not recovery_passed({**checks, "chroma_zero": False})
    assert not recovery_passed({key: value for key, value in checks.items() if key != "audit_retained"})


def test_file_cleanup_requires_both_record_and_original_file_to_be_absent(tmp_path) -> None:
    """物理删除验收必须直接验证文件层，而不是以数据库删除代替。"""
    stored_file = tmp_path / "fixture.txt"
    stored_file.write_text("fixture", encoding="utf-8")

    assert not file_cleanup_verified(
        stored_path=stored_file,
        document_record_missing=True,
    )
    stored_file.unlink()
    assert file_cleanup_verified(
        stored_path=stored_file,
        document_record_missing=True,
    )
    assert not file_cleanup_verified(
        stored_path=stored_file,
        document_record_missing=False,
    )
