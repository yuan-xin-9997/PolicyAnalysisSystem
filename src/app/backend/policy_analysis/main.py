from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI(title="政策分析系统", version="0.1.0")


app = create_app()
