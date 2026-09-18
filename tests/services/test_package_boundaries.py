"""验证 services 已按业务域组织，并且不保留旧平铺入口。"""

import ast
import importlib
from pathlib import Path


SERVICES_ROOT = Path(__file__).parents[2] / "app" / "services"


def test_services_root_contains_only_package_initializer() -> None:
    """根目录只保留包初始化文件，业务代码必须位于业务域子包。"""

    assert [path.name for path in SERVICES_ROOT.glob("*.py")] == ["__init__.py"]


def test_archive_agent_and_identity_service_tests_are_mirrored() -> None:
    """档案助手与身份服务的行为测试应同步位于对应业务域目录。"""
    tests_root = SERVICES_ROOT.parents[1] / "tests" / "services"
    assert (tests_root / "agent" / "test_archive_sessions.py").is_file()
    assert (tests_root / "agent" / "test_archive_execution.py").is_file()
    assert (tests_root / "identity" / "test_authentication.py").is_file()


def test_services_domain_modules_exist() -> None:
    """目标业务域模块应可以由固定路径发现。"""

    expected = {
        "archive/reads.py",
        "archive/documents.py",
        "archive/parser.py",
        "archive/drafts.py",
        "archive/suggestions.py",
        "archive/confirmation.py",
        "archive/cancellation.py",
        "archive/indexing.py",
        "archive/final_chunks.py",
        "archive/deletion.py",
        "archive/catalog.py",
        "archive/audit.py",
        "archive/checklist_links.py",
        "archive/retrieval.py",
        "archive/questions.py",
        "archive/reranker.py",
        "archive/evidence_matching.py",
        "project/management.py",
        "project/checklists.py",
        "agent/archive_sessions.py",
        "agent/archive_execution.py",
        "identity/authentication.py",
        "infrastructure/ai_models.py",
        "infrastructure/files.py",
        "infrastructure/chroma.py",
        "infrastructure/chroma_namespace.py",
    }
    actual = {
        path.relative_to(SERVICES_ROOT).as_posix()
        for path in SERVICES_ROOT.rglob("*.py")
        if path.name != "__init__.py"
    }
    assert actual == expected


def test_services_packages_and_modules_are_importable() -> None:
    """五个业务域包及其全部目标模块都必须可以导入。"""

    packages = {
        "archive",
        "project",
        "agent",
        "identity",
        "infrastructure",
    }
    module_paths = {
        path.relative_to(SERVICES_ROOT).with_suffix("").parts
        for path in SERVICES_ROOT.rglob("*.py")
        if path.name != "__init__.py"
    }
    for package in packages:
        importlib.import_module(f"app.services.{package}")
    for parts in module_paths:
        importlib.import_module("app.services." + ".".join(parts))


def test_repository_sources_do_not_import_old_service_paths() -> None:
    """应用、测试和脚本不能继续引用已经删除的根级 service 模块。"""

    old_module_names = {
        "archive_parser_service",
        "archive_draft_service",
        "archive_suggestion_service",
        "archive_confirmation_service",
        "archive_cancel_confirmation_service",
        "archive_index_service",
        "archive_final_chunk_service",
        "archive_document_delete_service",
        "archive_checklist_service",
        "archive_retrieval_service",
        "archive_question_service",
        "archive_reranker_service",
        "archive_evidence_match_service",
        "archive_catalog_service",
        "project_service",
        "checklist_service",
        "agent_service",
        "auth_service",
        "parser_service",
        "chunk_service",
        "embedding_service",
        "chat_service",
        "retrieval_service",
        "file_service",
        "model_service",
        "chroma_namespace_service",
        "document_service",
        "vector_service",
    }
    source_roots = [
        SERVICES_ROOT.parents[1] / name
        for name in ("app", "tests", "scripts", "pixie_qa")
    ]
    offenders = []
    for root in source_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            # MonkeyPatch 的目标通常位于字符串中，AST 无法识别这类路径。
            if any(f"app.services.{old}" in text for old in old_module_names):
                offenders.append(f"{path}:string")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules = [alias.name for alias in node.names]
                    if any(
                        module == f"app.services.{old}"
                        for module in imported_modules
                        for old in old_module_names
                    ):
                        offenders.append(f"{path}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "app.services" and any(
                        alias.name in old_module_names for alias in node.names
                    ):
                        offenders.append(f"{path}:{node.lineno}")
                    elif any(
                        node.module == f"app.services.{old}"
                        for old in old_module_names
                    ):
                        offenders.append(f"{path}:{node.lineno}")
    assert sorted(set(offenders)) == []


def test_retired_runtime_paths_are_absent() -> None:
    """旧通用 RAG、Chat 和制度 Agent 的运行代码及正向测试必须下线。"""
    root = SERVICES_ROOT.parents[1]
    retired_paths = {
        "app/routers/agent.py",
        "app/routers/chat.py",
        "app/routers/documents.py",
        "app/routers/knowledge_bases.py",
        "app/routers/retrieval.py",
        "app/agents/admin",
        "app/agents/tools/policy_tools.py",
        "app/agents/tools/context.py",
        "app/dependencies/agent.py",
        "app/dependencies/resources.py",
        "app/services/rag",
        "app/services/agent/sessions.py",
        "app/services/agent/execution.py",
        "app/services/agent/messages.py",
        "app/services/agent/audit.py",
        "app/schemas/agent.py",
        "app/schemas/chat.py",
        "app/schemas/retrieval.py",
        "app/models/chat.py",
        "evals/policy_agent",
        "scripts/policy_collection_embedding_rebuild.py",
        "scripts/generate_enterprise_rag_eval_data.py",
        "tests/agents/admin",
        "tests/agents/tools/test_policy_tools.py",
        "tests/evals/policy_agent",
        "tests/scripts/test_policy_collection_embedding_rebuild.py",
        "tests/scripts/test_generate_enterprise_rag_eval_data.py",
        "tests/services/rag",
        "tests/services/agent/test_sessions.py",
        "tests/services/agent/test_execution.py",
        "tests/services/agent/test_messages.py",
        "tests/services/agent/test_audit.py",
        "tests/routers/test_agent.py",
        "tests/routers/test_chat.py",
        "tests/routers/test_documents.py",
        "tests/routers/test_retrieval.py",
    }
    present = []
    for path in retired_paths:
        candidate = root / path
        if candidate.is_file():
            present.append(path)
        elif candidate.is_dir() and any(
            child.is_file()
            and "__pycache__" not in child.parts
            and (
                child.suffix in {".py", ".md"}
                or child.name == "policy-agent-golden.json"
                or child.name.startswith("input-")
            )
            for child in candidate.rglob("*")
        ):
            present.append(path)
    present.sort()
    assert present == []


def test_services_do_not_import_private_symbols_from_other_service_modules() -> None:
    """跨 services 导入只能依赖目标模块的公开接口。"""

    offenders: list[str] = []
    for path in SERVICES_ROOT.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        current_module = ".".join(
            ("app", "services", *path.relative_to(SERVICES_ROOT).with_suffix("").parts)
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("app.services."):
                continue
            if node.module == current_module:
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    offenders.append(f"{path}:{node.lineno}:{node.module}.{alias.name}")
    assert offenders == []


def test_archive_reads_exports_shared_public_helpers() -> None:
    """档案共享读取模块提供跨服务使用的公开函数。"""

    from app.services.archive import reads

    expected = {
        "list_archive_field_values",
        "archive_field_value",
        "build_process_document_read",
        "build_archive_draft_read",
        "list_visibility_blocked_document_ids",
    }
    assert expected <= set(vars(reads))
