from fastapi import FastAPI
from policy_analysis.main import create_app


def test_create_app_returns_fastapi() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "政策分析系统"
