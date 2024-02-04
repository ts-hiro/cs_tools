from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging
import pathlib

import click
import sqlmodel

from cs_tools.cli.dependencies.base import Dependency
from cs_tools.const import PACKAGE_DIR
from cs_tools.sync import base

log = logging.getLogger(__name__)


@dataclass
class DSyncer(Dependency):
    protocol: str
    definition_fp: pathlib.Path = None
    definition_kw: dict[str, Any] = None
    models: list[sqlmodel.SQLModel] = None

    @property
    def metadata(self) -> sqlmodel.MetaData:
        """"""
        return self._syncer.metadata

    def __enter__(self):
        log.debug(f"Registering syncer: {self.protocol.lower()}")

        if self.protocol == "custom":
            _, _, syncer_pathlike = self.protocol.rpartition("@")
            syncer_dir = pathlib.Path(syncer_pathlike)
        else:
            syncer_dir = PACKAGE_DIR / "sync" / self.protocol

        manifest_path = syncer_dir / "MANIFEST.json"
        manifest = base.SyncerManifest.parse_file(manifest_path)

        SyncerClass = manifest.import_syncer_class(fp=syncer_dir / "syncer.py")

        ctx = click.get_current_context()

        if self.definition_fp:
            conf = self._read_config_from_definition(ctx.obj.thoughtspot, self.protocol, self.definition_fp)
        else:
            conf = {"configuration": self.definition_kw}

        if issubclass(SyncerClass, base.DatabaseSyncer) and self.models is not None:
            conf["configuration"]["models"] = self.models

        log.debug(f"Initializing syncer: {SyncerClass}")
        self._syncer = SyncerClass(**conf["configuration"])

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if isinstance(self._syncer, base.DatabaseSyncer):
            if exc_type is not None:
                self._syncer.session.rollback()
            else:
                self._syncer.session.commit()
                self._syncer.session.close()

        return

    #
    # MAKE THE DEPENDENCY BEHAVE LIKE A SYNCER
    #

    def __getattr__(self, member_name: str) -> Any:
        # proxy calls to the underlying syncer first
        try:
            member = getattr(self._syncer, member_name)
        except AttributeError:
            try:
                member = self.__dict__[member_name]
            except KeyError:
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{member_name}'") from None

        return member

    def __repr__(self) -> str:
        # make the dependency look like the underlying Syncer
        return self._syncer.__repr__()
