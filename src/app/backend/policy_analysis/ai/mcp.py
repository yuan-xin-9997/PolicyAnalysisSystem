"""Official SDK transport; tools call the same authenticated ASGI API as other clients."""

import inspect
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, create_model
from starlette.responses import JSONResponse

from policy_analysis.ai.middleware import mcp_token
from policy_analysis.ai.service import resource_url
from policy_analysis.policies.schemas import PolicyQuery
from policy_analysis.sources.schemas import CollectionRuleCreate, CollectionRuleUpdate

Positive = Annotated[int, Field(ge=1)]


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchPolicies(PolicyQuery):
    # MCP JSON parameters represent datetime values with ISO strings.
    model_config = ConfigDict(extra="forbid", strict=False)


class Page(Empty):
    page: int = Field(default=1, ge=1, le=1000000, description="页码，从 1 开始")
    page_size: int = Field(default=50, ge=1, le=100, description="每页条数，最多 100")


class Write(Empty):
    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="操作唯一标识；重试保持相同值，不同操作使用新值",
    )
    confirm: Literal[True] = Field(description="调用方已确认执行本次写操作；不得自动猜测确认")


def install_mcp(app):
    server = FastMCP(
        "PolicyAnalysisSystem",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
    )
    # No browser-origin requests are permitted; the existing private listener remains unchanged.
    specs = [
        ("search_policies", "GET", "/api/v1/policies", SearchPolicies, "分页检索政策标题、正文及元数据"),
        (
            "get_policy",
            "GET",
            "/api/v1/policies/{policy_id}",
            create_model("PolicyId", __base__=Empty, policy_id=(Positive, ...)),
            "读取政策元数据及纯文本正文",
        ),
        ("get_policy_filters", "GET", "/api/v1/policies/filters", Empty, "查询可用政策筛选项"),
        ("list_sources", "GET", "/api/v1/sources", Empty, "查询来源目录"),
        ("list_categories", "GET", "/api/v1/policy-categories", Empty, "查询政策类别目录"),
        ("list_collection_rules", "GET", "/api/v1/collection-rules", Empty, "查询采集规则"),
        (
            "create_collection_rule",
            "POST",
            "/api/v1/collection-rules",
            create_model("CreateRule", __base__=Write, payload=(CollectionRuleCreate, ...)),
            "管理员创建采集规则；启用定时会导致后台访问外部来源，必须确认",
        ),
        (
            "update_collection_rule",
            "PATCH",
            "/api/v1/collection-rules/{rule_id}",
            create_model(
                "UpdateRule", __base__=Write, rule_id=(Positive, ...), payload=(CollectionRuleUpdate, ...)
            ),
            "管理员修改采集规则，必须确认",
        ),
        (
            "create_collection_task",
            "POST",
            "/api/v1/tasks",
            create_model("Collect", __base__=Write, rule_id=(Positive, ...)),
            "管理员创建异步采集任务；返回任务 ID，随后轮询状态，会访问外部来源",
        ),
        ("list_collection_tasks", "GET", "/api/v1/tasks", Page, "分页查询采集任务"),
        ("list_analysis_tasks", "GET", "/api/v1/analysis/tasks", Page, "分页查询分析任务"),
        (
            "create_analysis_task",
            "POST",
            "/api/v1/analysis/tasks",
            create_model(
                "Analyze", __base__=Write, policy_ids=(list[Positive], Field(min_length=1, max_length=100))
            ),
            "创建异步词频分析任务，返回任务 ID；随后轮询状态及读取词频结果",
        ),
        (
            "create_comparison_task",
            "POST",
            "/api/v1/analysis/comparison-tasks",
            create_model(
                "Compare", __base__=Write, policy_ids=(list[Positive], Field(min_length=2, max_length=100))
            ),
            "创建异步政策比对任务，至少两篇不同政策；完成后读取比对报告",
        ),
    ]
    task_id = create_model("TaskId", __base__=Empty, task_id=(Positive, ...))
    task_page = create_model("TaskPage", __base__=Page, task_id=(Positive, ...))
    top = create_model("TopWords", __base__=task_id, top=(int, Field(default=50, ge=1, le=500)))
    specs += [
        ("get_collection_task", "GET", "/api/v1/tasks/{task_id}", task_id, "查询采集进度、计数和失败原因"),
        ("list_collection_items", "GET", "/api/v1/tasks/{task_id}/items", task_page, "分页查询采集结果"),
        ("list_collection_logs", "GET", "/api/v1/tasks/{task_id}/logs", task_page, "分页查询采集日志"),
        ("get_analysis_task", "GET", "/api/v1/analysis/tasks/{task_id}", task_id, "查询分析状态和失败原因"),
        ("list_analysis_logs", "GET", "/api/v1/analysis/tasks/{task_id}/logs", task_page, "分页查询分析日志"),
        (
            "get_analysis_words",
            "GET",
            "/api/v1/analysis/tasks/{task_id}/words",
            create_model("Words", __base__=top, sort_by=(Literal["frequency", "tfidf"], "frequency")),
            "查询已完成任务的词频及 TF-IDF 结果",
        ),
        (
            "get_analysis_relations",
            "GET",
            "/api/v1/analysis/tasks/{task_id}/relations",
            top,
            "查询关键词共现关系",
        ),
        (
            "get_comparison_report",
            "GET",
            "/api/v1/analysis/tasks/{task_id}/comparison-report",
            task_id,
            "读取已完成的政策比对报告",
        ),
    ]
    for name, method, path, model, description in specs:
        fn = make_tool(app, method, path, model)
        server.add_tool(
            fn,
            name=name,
            description=description + "。权限与同名 API 一致；错误作为工具错误返回。",
            annotations=ToolAnnotations(
                readOnlyHint=method == "GET",
                destructiveHint="rule" in name and method != "GET",
                idempotentHint=True,
                openWorldHint=name
                in {"create_collection_task", "create_collection_rule", "update_collection_rule"},
            ),
        )
    app.state.mcp_server = server
    app.mount("/mcp", StatelessTransport(server, app))


class StatelessTransport:
    """Each stateless request owns and closes its SDK transport task group."""

    def __init__(self, server, owner):
        self.server = server
        self.owner = owner

    async def __call__(self, scope, receive, send):
        if scope["path"].removeprefix(scope.get("root_path", "")) != "/":
            return await JSONResponse(
                {"error": {"code": "NOT_FOUND", "message": "MCP endpoint not found", "details": {}}},
                status_code=404,
            )(scope, receive, send)
        manager = StreamableHTTPSessionManager(
            self.server._mcp_server,
            stateless=True,
            json_response=True,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[urlsplit(resource_url(self.owner)).netloc],
                allowed_origins=[],
            ),
            max_request_body_size=self.owner.state.settings.ai.max_body_bytes,
        )
        async with manager.run():
            await manager.handle_request(scope, receive, send)


def make_tool(app, method, template, model):
    async def invoke(arguments):
        data = arguments.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        path = template
        for key in ("policy_id", "rule_id", "task_id"):
            if "{" + key + "}" in path:
                path = path.replace("{" + key + "}", str(data.pop(key)))
        headers = {"Authorization": "Bearer " + mcp_token.get()}
        if method != "GET":
            headers["Idempotency-Key"] = data.pop("idempotency_key")
            data.pop("confirm")
            headers["X-Confirm-Operation"] = "true"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://internal"
        ) as client:
            response = await client.request(
                method,
                path,
                headers=headers,
                params=data if method == "GET" else None,
                json=data.get("payload", data) if method != "GET" else None,
            )
        if response.is_error:
            from mcp.server.fastmcp.exceptions import ToolError

            raise ToolError(response.text)
        return response.json()

    invoke.__annotations__ = {"arguments": model, "return": dict}
    invoke.__signature__ = inspect.Signature(
        [
            inspect.Parameter("arguments", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=model),
        ],
        return_annotation=dict,
    )
    return invoke
