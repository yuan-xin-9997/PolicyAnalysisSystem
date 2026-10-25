from fastapi.openapi.utils import get_openapi

from policy_analysis.ai.service import required_scope


def install_openapi(app):
    def schema():
        if app.openapi_schema:
            return app.openapi_schema
        result = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
            description="政策检索、采集及分析 API。浏览器保留 session + CSRF；自动化使用独立 Bearer 凭据。",
        )
        schemes = result.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["AutomationBearer"] = {
            "type": "http",
            "scheme": "bearer",
            "description": "通过 /api/v1/credentials 创建；仅返回一次明文。",
        }
        schemes["BrowserSession"] = {"type": "apiKey", "in": "cookie", "name": "session"}
        error = {
            "type": "object",
            "required": ["error"],
            "properties": {
                "error": {
                    "type": "object",
                    "required": ["code", "message", "details"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                        "request_id": {"type": "string"},
                    },
                }
            },
        }
        for path, item in result["paths"].items():
            for method, operation in item.items():
                if method not in {"get", "post", "patch", "delete", "put", "head"}:
                    continue
                scope = required_scope(path, method.upper())
                if scope:
                    operation["security"] = [{"AutomationBearer": []}, {"BrowserSession": []}]
                    operation["x-required-scope"] = scope
                    if method not in {"get", "head"}:
                        parameters = operation.setdefault("parameters", [])
                        parameters.append(
                            {
                                "name": "Idempotency-Key",
                                "in": "header",
                                "required": False,
                                "description": "Bearer 写操作必填；重试使用相同值。浏览器兼容原调用方式。",
                                "schema": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 128,
                                    "pattern": "^[A-Za-z0-9._:-]+$",
                                },
                            }
                        )
                        if scope == "rules:write":
                            parameters.append(
                                {
                                    "name": "X-Confirm-Operation",
                                    "in": "header",
                                    "required": False,
                                    "description": "Bearer 规则写操作必须确认，值为 true。",
                                    "schema": {"type": "string", "enum": ["true"]},
                                }
                            )
                elif path.startswith("/api/") and path != "/api/v1/auth/login":
                    operation["security"] = [{"BrowserSession": []}]
                if path.startswith("/api/"):
                    for status, description in {
                        "401": "未认证、凭据错误、撤销或过期",
                        "403": "权限不足或 CSRF 无效",
                        "409": "幂等冲突或处理中",
                        "422": "参数校验失败",
                        "429": "速率限制，见 Retry-After",
                        "503": "执行器或数据库不可用",
                    }.items():
                        operation["responses"][status] = {
                            "description": description,
                            "content": {
                                "application/json": {
                                    "schema": error,
                                    "example": {
                                        "error": {
                                            "code": "VALIDATION_ERROR"
                                            if status == "422"
                                            else "PERMISSION_DENIED",
                                            "message": description,
                                            "details": {},
                                        }
                                    },
                                },
                            },
                        }
        for path in ("/api/v1/analysis/tasks", "/api/v1/analysis/comparison-tasks"):
            operation = result["paths"][path]["post"]
            operation["requestBody"]["content"]["application/json"]["example"] = {"policy_ids": [1, 2]}
            operation["responses"]["200"]["content"]["application/json"]["example"] = {
                "task_id": 42,
                "status": "pending",
            }
        app.openapi_schema = result
        return result

    app.openapi = schema
