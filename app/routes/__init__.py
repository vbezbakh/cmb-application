from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings
from app.db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


DB = Annotated[Database, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
