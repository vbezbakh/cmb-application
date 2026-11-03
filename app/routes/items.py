"""A small CRUD surface that stands in for the real mobile-facing API.

It exists to exercise the platform: writes go to the primary, the list endpoint reads from
the replica pool, and fetch-by-id stays on the primary so a client can read its own write
without hitting replication lag.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.routes import DB

router = APIRouter(prefix="/api/v1/items", tags=["items"])


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ItemOut(BaseModel):
    id: int
    name: str
    created_at: datetime


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_item(item: ItemIn, db: DB) -> ItemOut:
    async with db.connection() as conn:
        row = await conn.fetchrow(
            "INSERT INTO items (name) VALUES ($1) RETURNING id, name, created_at", item.name
        )
    return ItemOut(**dict(row))


@router.get("/{item_id}")
async def get_item(item_id: int, db: DB) -> ItemOut:
    async with db.connection() as conn:
        row = await conn.fetchrow("SELECT id, name, created_at FROM items WHERE id = $1", item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="item not found")
    return ItemOut(**dict(row))


@router.get("")
async def list_items(db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[ItemOut]:
    async with db.connection(readonly=True) as conn:
        rows = await conn.fetch(
            "SELECT id, name, created_at FROM items ORDER BY id DESC LIMIT $1", limit
        )
    return [ItemOut(**dict(r)) for r in rows]
