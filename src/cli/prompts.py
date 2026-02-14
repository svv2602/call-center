"""CLI commands for prompt version management.

Usage:
    call-center-admin prompts list
    call-center-admin prompts activate <version_id>
    call-center-admin prompts rollback
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import typer
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.agent.prompt_manager import PromptManager
from src.config import get_settings

prompts_app = typer.Typer(help="Prompt version management")


async def _get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database.url)


async def _list_versions() -> list[dict[str, Any]]:
    engine = await _get_engine()
    try:
        manager = PromptManager(engine)
        return await manager.list_versions()
    finally:
        await engine.dispose()


async def _activate_version(version_id: UUID) -> dict[str, Any]:
    engine = await _get_engine()
    try:
        manager = PromptManager(engine)
        return await manager.activate_version(version_id)
    finally:
        await engine.dispose()


async def _rollback_prompt() -> dict[str, Any]:
    """Rollback to the most recent previously active version."""
    engine = await _get_engine()
    try:
        manager = PromptManager(engine)
        versions = await manager.list_versions()

        # Find non-active versions as rollback candidates
        candidates = [v for v in versions if not v.get("is_active")]

        if not candidates:
            msg = "No previous version to rollback to"
            raise ValueError(msg)

        # Pick the most recent non-active version (list is ordered by created_at DESC)
        target = candidates[0]
        return await manager.activate_version(target["id"])
    finally:
        await engine.dispose()


@prompts_app.command("list")
def prompts_list() -> None:
    """List all prompt versions."""
    try:
        versions = asyncio.run(_list_versions())
    except Exception as e:
        typer.echo(typer.style(f"Failed to fetch prompts: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None

    if not versions:
        typer.echo("No prompt versions found.")
        return

    typer.echo(f"{'ID':<38} {'Name':<25} {'Active':<8} {'Created'}")
    typer.echo("-" * 90)
    for v in versions:
        active_mark = "\u2705" if v.get("is_active") else ""
        vid = str(v["id"])
        vname = str(v.get("name", "N/A"))
        typer.echo(f"{vid:<38} {vname:<25} {active_mark:<8} {v.get('created_at', 'N/A')}")
    typer.echo(f"\n{len(versions)} version(s)")


@prompts_app.command("activate")
def prompts_activate(
    version_id: str = typer.Argument(..., help="Prompt version ID (UUID)"),
) -> None:
    """Activate a specific prompt version."""
    try:
        uid = UUID(version_id)
    except ValueError:
        typer.echo(typer.style(f"Invalid UUID: {version_id}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None

    try:
        result = asyncio.run(_activate_version(uid))
        typer.echo(
            typer.style(
                f"\u2705 Activated prompt: {result.get('name', uid)}",
                fg=typer.colors.GREEN,
            )
        )
    except ValueError as e:
        typer.echo(typer.style(str(e), fg=typer.colors.RED))
        raise typer.Exit(code=1) from None
    except Exception as e:
        typer.echo(typer.style(f"Failed to activate prompt: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None


@prompts_app.command("rollback")
def prompts_rollback() -> None:
    """Rollback to the previous prompt version."""
    try:
        result = asyncio.run(_rollback_prompt())
        typer.echo(
            typer.style(
                f"\u2705 Rolled back to: {result.get('name', result.get('id'))}",
                fg=typer.colors.GREEN,
            )
        )
    except ValueError as e:
        typer.echo(typer.style(str(e), fg=typer.colors.RED))
        raise typer.Exit(code=1) from None
    except Exception as e:
        typer.echo(typer.style(f"Rollback failed: {e}", fg=typer.colors.RED))
        raise typer.Exit(code=1) from None
