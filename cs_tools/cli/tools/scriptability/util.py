"""Contains useful classes and methods for scriptability."""
from __future__ import annotations

from dataclasses import dataclass
from typing import NewType
import pathlib

from thoughtspot_tml.utils import disambiguate as _disambiguate
from thoughtspot_tml.utils import EnvironmentGUIDMapper as Mapper
from thoughtspot_tml.types import TMLObject
from thoughtspot_tml import Connection

from cs_tools.types import GUID

EnvName = NewType("EnvName", str)


class GUIDMapping:
    """
    Wrapper for guid mapping to make it easier to use.

    Attributes
    ----------
    from_env : str
      the source environment

    to_env : str
      the target environment

    filepath : pathlib.Path
      path to the mapping object

    remap_object_guid : bool, default = True
      whether or not to remap the top-level tml.guid
    """

    def __init__(self, from_env: EnvName, to_env: EnvName, path: pathlib.Path, remap_object_guid: bool = True):
        self.from_env: str = from_env
        self.to_env: str = to_env
        self.path: pathlib.Path = path
        self.remap_object_guid = remap_object_guid
        self.guid_mapper = Mapper.read(path, str.lower) if path.exists() else Mapper(str.lower)

    def get_mapped_guid(self, from_guid: GUID) -> GUID:
        """
        Get the mapped guid.
        """
        # { DEV: guid1, PROD: guid2, ... }
        all_envts_from_guid = self.guid_mapper.get(from_guid, default={})
        return all_envts_from_guid.get(self.to_env, from_guid)

    def set_mapped_guid(self, from_guid: GUID, to_guid: GUID) -> None:
        """
        Sets the guid mapping from the old to the new.

        You have to set both to make sure both are in the file.
        """
        self.guid_mapper[from_guid] = (self.from_env, from_guid)
        self.guid_mapper[from_guid] = (self.to_env, to_guid)

    def disambiguate(self, tml: TMLObject, delete_unmapped_guids: bool = False) -> None:
        """
        Replaces source GUIDs with target.
        """
        # self.guid_mapper.generate_map(DEV, PROD) # =>  {envt_A_guid1: envt_B_guid2 , .... }
        mapper = self.guid_mapper.generate_mapping(self.from_env, self.to_env)

        _disambiguate(
            tml=tml,
            guid_mapping=mapper,
            remap_object_guid=self.remap_object_guid,
            delete_unmapped_guids=delete_unmapped_guids,
        )

    def save(self) -> None:
        """
        Saves the GUID mappings.
        """
        self.guid_mapper.save(path=self.path, info={"generated-by": "cs_tools/scriptability"})


@dataclass
class TMLFile:
    """
    Combines file information with TML.
    """

    filepath: pathlib.Path
    tml: TMLObject

    @property
    def is_connection(self) -> bool:
        return isinstance(self.tml, Connection)


def strip_blanks(inp: List[str]) -> List[str]:
    """Strips blank out of a list."""
    return [e for e in inp if e]
