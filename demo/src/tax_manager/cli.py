"""Maintenance commands for a FastAPI deployment."""

import click

from tax_manager.app import create_app
from tax_manager.auth import blueprint as auth_blueprint
from tax_manager.web import app_context


def create_cli(app=None):
    @click.group()
    @click.pass_context
    def cli(ctx):
        active_app = app if app is not None else create_app()
        context = app_context(active_app)
        context.__enter__()
        ctx.call_on_close(lambda: context.__exit__(None, None, None))

    cli.add_command(auth_blueprint.cli)
    return cli


main = create_cli()

if __name__ == "__main__":
    main()
