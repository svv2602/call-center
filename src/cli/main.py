"""Call Center AI admin CLI — main entry point.

Usage:
    call-center-admin version
    call-center-admin config check
    call-center-admin config show
"""

from __future__ import annotations

import typer

from src.cli.analytics import calls_app, stats_app
from src.cli.db import db_app
from src.cli.export import export_app
from src.cli.operations import ops_app
from src.cli.prompts import prompts_app
from src.config import Settings, get_settings

app = typer.Typer(
    name="call-center-admin",
    help="Call Center AI administration CLI",
    no_args_is_help=True,
)

config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(stats_app, name="stats")
app.add_typer(calls_app, name="calls")
app.add_typer(prompts_app, name="prompts")
app.add_typer(export_app, name="export")
app.add_typer(ops_app, name="ops")

_VERSION = "0.1.0"

# Pattern for masking secrets: keep first 6 chars, mask the rest
_SECRET_FIELDS = {"api_key", "password", "jwt_secret", "key"}


def _mask_secret(value: str, visible_chars: int = 6) -> str:
    """Mask a secret value, keeping first few characters visible."""
    if len(value) <= visible_chars:
        return "***"
    return value[:visible_chars] + "***"


def _collect_config_display(settings: Settings) -> list[tuple[str, str, str]]:
    """Collect (section, key, display_value) tuples from settings."""
    rows: list[tuple[str, str, str]] = []

    for field_name, field_value in settings:
        if field_name == "prometheus_port":
            rows.append(("root", field_name, str(field_value)))
            continue

        if not hasattr(field_value, "__iter__"):
            continue

        # Sub-settings object
        section = field_name
        for sub_name, sub_value in field_value:
            display = str(sub_value)
            if sub_name in _SECRET_FIELDS and display:
                display = _mask_secret(display)
            rows.append((section, sub_name, display))

    return rows


@app.command()
def version() -> None:
    """Show application version."""
    typer.echo(f"Call Center AI v{_VERSION}")


@config_app.command("check")
def config_check() -> None:
    """Validate configuration and show status of each parameter."""
    settings = get_settings()
    result = settings.validate_required()

    if result.ok:
        typer.echo(typer.style("\u2705 All configuration checks passed", fg=typer.colors.GREEN))
    else:
        for err in result.errors:
            hint = f"  Hint: {err.hint}" if err.hint else ""
            typer.echo(
                typer.style(f"\u274c {err.field}: {err.message}.{hint}", fg=typer.colors.RED)
            )
        typer.echo(f"\n{len(result.errors)} error(s) found.")
        raise typer.Exit(code=1)


@config_app.command("show")
def config_show() -> None:
    """Show current configuration (secrets are masked)."""
    settings = get_settings()
    rows = _collect_config_display(settings)

    current_section = ""
    for section, key, value in rows:
        if section != current_section:
            if current_section:
                typer.echo("")
            typer.echo(typer.style(f"[{section}]", fg=typer.colors.CYAN, bold=True))
            current_section = section
        typer.echo(f"  {key} = {value}")


if __name__ == "__main__":
    app()
