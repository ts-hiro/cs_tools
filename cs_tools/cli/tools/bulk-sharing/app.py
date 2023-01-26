from typing import List
import logging
import socket

import uvicorn
import typer

from cs_tools.cli.dependencies import thoughtspot
from cs_tools.cli.ux import rich_console
from cs_tools.cli.ux import CSToolsArgument as Arg
from cs_tools.cli.ux import CSToolsOption as Opt
from cs_tools.cli.ux import CSToolsApp
from cs_tools.types import ShareModeAccessLevel

from .web_app import _scoped

log = logging.getLogger(__name__)


def _find_my_local_ip() -> str:
    """
    Gets the local ip, or loopback address if not found.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("10.255.255.255", 1))  # does not need to be a valid addr

        try:
            ip = sock.getsockname()[0]
        except IndexError:
            ip = "127.0.0.1"

    return ip


def _get_table_ids(api, *, db: str, schema: str = "falcon_default_schema", table: str = None) -> List[str]:
    """
    Returns a list of table GUIDs.
    """
    r = api._metadata.list(type="LOGICAL_TABLE", subtype=["ONE_TO_ONE_LOGICAL"])
    table_details = r.json()["headers"]

    guids = []

    for details in table_details:
        # Don't allow sharing of System Tables.
        if "databaseStripe" not in details:
            continue

        # specific table
        if db == details["databaseStripe"] and schema == details["schemaStripe"]:
            if table == _get_physical_table(api, table_id=details["id"]):
                guids.append(details["id"])
                break

        # metadata/list returns LOGICA_TABLE name, need another call for physical name
        if table is None:
            if db == details["databaseStripe"]:
                guids.append(details["id"])

    return guids or None


def _get_physical_table(api, *, table_id: str) -> str:
    """
    Returns the physical table name for a given GUID.
    """
    r = api._metadata.detail(guid=table_id, type="LOGICAL_TABLE").json()
    return r["logicalTableContent"]["physicalTableName"]


def _permission_param_to_permission(permission: str) -> ShareModeAccessLevel:
    """ """
    # should be one of these due to parameter checking
    _mapping = {
        "view": ShareModeAccessLevel.read_only,
        "edit": ShareModeAccessLevel.modify,
        "remove": ShareModeAccessLevel.no_access,
    }
    return _mapping[permission]


app = CSToolsApp(
    help="""
    Scalably manage your table- and column-level security right in the browser.

    Setting up Column Level Security (especially on larger tables) can be time-consuming
    when done directly in the ThoughtSpot user interface. The web interface provided by
    this tool will allow you to quickly understand the current security settings for a
    given table across all columns, and as many groups as are in your platform. You may
    then set the appropriate security settings for those group-table combinations.
    """,
)


@app.command(dependencies=[thoughtspot])
def run(ctx: typer.Context, webserver_port: int = Opt(5000, help="port to host the webserver on")):
    """
    Start the built-in webserver which runs the security management interface.
    """
    ts = ctx.obj.thoughtspot
    visit_ip = _find_my_local_ip()

    _scoped["ts"] = ts

    rich_console.print("starting webserver..." f"\nplease visit [green]http://{visit_ip}:5000/[/] in your browser")

    uvicorn.run(
        "cs_tools.cli.tools.security-sharing.web_app:web_app",
        host="0.0.0.0",
        port=webserver_port,
        log_config=None,  # TODO log to file instead of console (less confusing for user)
    )


@app.command(dependencies=[thoughtspot])
def share(
    ctx: typer.Context,
    group: str = Opt(..., help="group to share with"),
    permission: ShareModeAccessLevel = Opt(..., help="permission type to assign"),
    database: str = Opt(..., help="name of database of tables to share"),
    schema: str = Opt("falcon_default_schema", help="name of schema of tables to share"),
    table: str = Opt(None, help="name of the table to share, if not provided then share all tables"),
):
    """
    Share database tables with groups.
    """
    ts = ctx.obj.thoughtspot

    table_ids = _get_table_ids(ts.api, db=database, schema=schema, table=table)

    if not table_ids:
        rich_console.print(f"No tables found for {database}.{schema}{f'.{table}' if table else ''}")
        raise typer.Exit()

    r = ts.api._security.share(
        type="LOGICAL_TABLE",
        id=table_ids,
        permission={ts.group.guid_for(group): _permission_param_to_permission(permission)},
    )

    status = "[green]success[/]" if r.status_code == 204 else "[red]failed[/]"
    rich_console.print(f'Sharing with group "{group}": {status}')

    if r.status_code != 204:
        log.error(r.content)
