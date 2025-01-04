from __future__ import annotations

import datetime as dt
import logging
import pathlib
import platform
import shutil
import sys
import sysconfig
import zipfile

from cs_tools import __version__, updater, utils
from cs_tools.cli import custom_types
from cs_tools.cli.ux import RICH_CONSOLE, AsyncTyper
from cs_tools.settings import _meta_config as meta
from cs_tools.sync import base
from cs_tools.updater._bootstrapper import get_latest_cs_tools_release
from cs_tools.updater._updater import cs_tools_venv
import rich
import typer

_LOG = logging.getLogger(__name__)
app = AsyncTyper(
    name="self",
    help=f"""
    Perform actions on CS Tools.

    {meta.newer_version_string()}
    """,
)


@app.command()
def info(
    directory: custom_types.Directory = typer.Option(None, help="export an image to share with the CS Tools team"),
    anonymous: bool = typer.Option(False, "--anonymous", help="remove personal references from the output"),
):
    """Get information on your install."""
    if platform.system() == "Windows":
        source = f"{pathlib.Path(sys.executable).parent.joinpath('Activate.ps1')}"
    else:
        source = f"source \"{pathlib.Path(sys.executable).parent.joinpath('activate')}\""

    text = (
        f"\n       [b blue]Info snapshot[/] taken on [b green]{dt.datetime.now(tz=dt.timezone.utc).date()}[/]"
        f"\n"
        f"\n           CS Tools: [b yellow]{__version__}[/]"
        f"\n     Python Version: [b yellow]Python {sys.version}[/]"
        f"\n        System Info: [b yellow]{platform.system()}[/] (detail: [b yellow]{platform.platform()}[/])"
        f"\n  Configs Directory: [b yellow]{cs_tools_venv.base_dir}[/]"
        f"\nActivate VirtualEnv: [b yellow]{source}[/]"
        f"\n      Platform Tags: [b yellow]{sysconfig.get_platform()}[/]"
        f"\n"
    )

    if anonymous:
        text = utils.anonymize(text, anonymizer=" [dim]{anonymous}[/] ")

    renderable = rich.panel.Panel.fit(text, padding=(0, 4, 0, 4))
    RICH_CONSOLE.print(rich.align.Align.center(renderable))

    if directory is not None:
        utils.svg_screenshot(
            renderable,
            path=directory / f"cs-tools-info-{dt.datetime.now(tz=dt.timezone.utc):%Y-%m-%d}.svg",
            console=RICH_CONSOLE,
            centered=True,
            width="fit",
            title="cs_tools self info",
        )


@app.command()
def sync():
    """Sync your local environment with the most up-to-date dependencies."""
    # CURRENTLY, THIS ONLY AFFECTS thoughtspot_tml WHICH CAN OFTEN CHANGE BETWEEN CS TOOL RELEASES.
    PACKAGES_TO_SYNC = ("thoughtspot_tml",)

    for package in PACKAGES_TO_SYNC:
        cs_tools_venv.install(package, "--upgrade")


@app.command(name="update")
@app.command(name="upgrade", hidden=True)
def update(
    beta: custom_types.Version = typer.Option(None, "--beta", help="The specific beta version to fetch from Github."),
    offline: custom_types.Directory = typer.Option(None, help="Install cs_tools from a local directory."),
):
    """Upgrade CS Tools."""
    assert isinstance(offline, pathlib.Path), "offline directory must be a pathlib.Path"

    if offline is not None:
        cs_tools_venv.offline_index = offline
        where = offline.as_posix()
    else:
        # FETCH THE VERSION TO INSTALL.
        ref = beta if beta is not None else get_latest_cs_tools_release().get("tag_name", f"v{__version__}")
        where = f"https://github.com/thoughtspot/cs_tools/archive/{ref}.zip"

    cs_tools_venv.install(f"cs_tools[cli] @ {where}", raise_if_stderr=False)


@app.command(name="export", hidden=True)
@app.command(name="download", hidden=True)
def _make_offline_distributable(
    directory: custom_types.Directory = typer.Option(help="Location to export the python distributable to."),
    platform: str = typer.Option(help="A tag describing the target environment architecture, see --help for details."),
    python_version: custom_types.Version = typer.Option(
        metavar="X.Y", help="The major and minor version of the target Python environment, see --help for details"
    ),
    beta: custom_types.Version = typer.Option(
        None, metavar="X.Y.Z", help="The specific beta version to fetch from Github."
    ),
    syncer: str = typer.Option(None, metavar="DIALECT", help="Name of the dialect to fetch dependencies for."),
):
    """
    Create an offline distribution of this CS Tools environment.

    \b
    Q. How can I find my platform?
    >>> [fg-warn]python -c "from pip._vendor.packaging.tags import platform_tags; print(next(iter(platform_tags())))"[/]

    Q. How can I find my python version?
    >>> [fg-warn]python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"[/]
    """
    assert isinstance(directory, pathlib.Path), "directory must be a pathlib.Path"

    # JUST IN CASE SOMEONE FORGETS THE `v` PREFIX :~)
    if beta is not None and "v" not in beta:
        beta = custom_types.Version().convert(f"v{beta}", None, None)

    # ENSURE WE HAVE THE DESIRED SYNCER INSTALLED.
    if syncer is not None:
        syncer_base_dir = utils.get_package_directory("cs_tools") / "sync" / syncer.lower()
        assert syncer_base_dir.exists(), f"Syncer dialect '{syncer}' not found, did you mistype it?"
        syncer_manifest = base.SyncerManifest.model_validate_json(syncer_base_dir.joinpath("MANIFEST.json").read_text())

        for requirement_info in syncer_manifest.requirements:
            cs_tools_venv.install(str(requirement_info.requirement), *requirement_info.pip_args)

    # FETCH THE VERSION TO INSTALL.
    ref = beta if beta is not None else get_latest_cs_tools_release().get("tag_name", f"v{__version__}")
    where = f"https://github.com/thoughtspot/cs_tools/archive/{ref}.zip"

    # ENSURE WE HAVE THE BUILD PACKAGES INSTALLED.
    cs_tools_venv.install(f"cs_tools[offline] @ {where}", raise_if_stderr=False)

    # GENERATE THE DIRECTORY OF DEPENDENCIES.
    cs_tools_venv.make_offline_distribution(output_dir=directory, platform=platform, python_version=python_version)

    # COPY THE BOOTSTRAPPER AND UPDATER SCRIPTS
    shutil.copyfile(updater._updater.__file__, directory / "_updater.py")
    shutil.copyfile(updater._bootstrapper.__file__, directory / "_bootstrapper.py")

    # ZIP IT UP
    zipfile_name = directory / f"cs-tools-{__version__}-{platform}-{python_version}.zip"
    _LOG.info(f"Zipping CS Tools venv > {zipfile_name}")
    utils.make_zip_archive(directory=directory, zipfile_path=zipfile_name, compression=zipfile.ZIP_DEFLATED)

    # DELETE THE EXTRA FILES.
    for path in directory.iterdir():
        if path == zipfile_name:
            continue

        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

        if path.is_file():
            path.unlink(missing_ok=True)

    # PRINT A NOTE ON HOW TO INSTALL.
    RICH_CONSOLE.print(
        f"""
        [fg-warn]INSTALL INSTRUCTIONS[/]
        1. Extract the zip file.
        2. Move into the directory with all the python dependencies.
        3. Run [fg-secondary]python[/] against the _bootstrapper.py file with [fg-secondary]--offline-mode[/] specified

        [fg-warn]cd[/] [fg-secondary]{zipfile_name.stem}[/]
        [fg-warn]python[/] [fg-secondary]_bootstrapper.py --install --offline-mode --no-clean[/]
        """
    )
