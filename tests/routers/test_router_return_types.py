"""验证路由函数返回类型与 Service 返回对象及 HTTP 响应模型的边界。"""

import ast
import inspect
import textwrap
from typing import get_type_hints

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.models import Project, User
from app.routers.archive_agent import router as archive_agent_router
from app.routers.auth import router as auth_router
from app.routers.health import router as health_router
from app.routers.projects import router as projects_router
from app.schemas import ProjectRead, UserRead


ALL_ROUTERS: tuple[tuple[str, APIRouter], ...] = (
    ("archive_agent", archive_agent_router),
    ("auth", auth_router),
    ("health", health_router),
    ("projects", projects_router),
)


def _find_route(router: APIRouter, path: str, method: str) -> APIRoute:
    """按公开路径和 HTTP 方法找到待验证的 FastAPI 路由。"""
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"未找到路由：{method} {path}")


@pytest.mark.parametrize(
    ("route", "path", "method", "expected_return", "expected_response_model"),
    [
        (auth_router, "/auth/register", "POST", User, UserRead),
        (projects_router, "/projects", "POST", Project, ProjectRead),
        (projects_router, "/projects/{project_id}", "GET", Project, ProjectRead),
        (projects_router, "/projects/{project_id}", "PATCH", Project, ProjectRead),
    ],
)
def test_router_return_annotation_matches_service_object_but_keeps_http_schema(
    route: APIRouter,
    path: str,
    method: str,
    expected_return: type,
    expected_response_model: type,
) -> None:
    """函数注解描述 Service 对象，response_model 继续描述外部 HTTP 契约。"""
    api_route = _find_route(route, path, method)
    return_annotation = get_type_hints(api_route.endpoint)["return"]

    assert return_annotation is expected_return
    assert api_route.response_model is expected_response_model


def test_all_routers_declare_all_path_parameters_in_endpoint_signature() -> None:
    """全部路由的路径参数必须在端点函数签名中显式声明。"""
    missing: list[str] = []
    routes_with_missing: set[tuple[str, str, frozenset[str]]] = set()

    for router_name, router in ALL_ROUTERS:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            parameter_names = inspect.signature(route.endpoint).parameters
            route_missing = [
                name for name in route.param_convertors if name not in parameter_names
            ]
            if route_missing:
                routes_with_missing.add((router_name, route.path, frozenset(route.methods)))
                missing.extend(
                    f"{router_name} {sorted(route.methods)} {route.path}: {name}"
                    for name in route_missing
                )

    assert not missing, (
        "路径参数未在端点函数签名中声明；"
        f"涉及 {len(routes_with_missing)} 个端点、共 {len(missing)} 个参数："
        + "; ".join(missing)
    )


def test_all_routers_read_path_parameters_in_endpoint_body() -> None:
    """全部路由的路径参数必须在端点函数体中至少读取一次。"""
    missing: list[str] = []
    routes_with_missing: set[tuple[str, str, frozenset[str]]] = set()

    for router_name, router in ALL_ROUTERS:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            source = textwrap.dedent(inspect.getsource(route.endpoint))
            syntax_tree = ast.parse(source)
            endpoint = next(
                node
                for node in syntax_tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            loaded_names = {
                node.id
                for node in ast.walk(endpoint)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            }
            route_missing = [
                name for name in route.param_convertors if name not in loaded_names
            ]
            if route_missing:
                routes_with_missing.add((router_name, route.path, frozenset(route.methods)))
                missing.extend(
                    f"{router_name} {sorted(route.methods)} {route.path}: {name}"
                    for name in route_missing
                )

    assert not missing, (
        "路径参数未在端点函数体中读取；"
        f"涉及 {len(routes_with_missing)} 个端点、共 {len(missing)} 个参数："
        + "; ".join(missing)
    )


def test_project_document_list_uses_internal_status_name_with_public_alias() -> None:
    """项目文档列表用内部参数名区分公开查询参数别名。"""
    route = _find_route(projects_router, "/projects/{project_id}/documents", "GET")
    parameters = inspect.signature(route.endpoint).parameters

    assert "document_status" in parameters
    assert "status" not in parameters
    assert getattr(parameters["document_status"].default, "alias", None) == "status"
