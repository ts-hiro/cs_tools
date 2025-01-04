from __future__ import annotations

from typing import Any
import collections.abc
import itertools as it
import logging

import click

from cs_tools._compat import StrEnum

# from cs_tools.cli.dependencies.syncer import DSyncer

log = logging.getLogger(__name__)


class MetadataType(click.ParamType):
    def __init__(self, to_system_types: bool = False, include_subtype: bool = False):
        self.to_system_types = to_system_types
        self.include_subtype = include_subtype
        self.enum = StrEnum(
            "MetadataType", ["connection", "table", "view", "sql_view", "worksheet", "liveboard", "answer"]
        )

    def get_metavar(self, _param) -> str:
        return "|".join(self.enum)

    def convert(self, value, param, ctx):
        if value is None:
            return value

        try:
            value = self.enum(value)
        except ValueError:
            self.fail(f"{value!r} is not a valid {self.__class__.__name__}", param, ctx)

        if self.to_system_types:
            metadata_type, subtype = self.convert_system_types(value)

            if self.include_subtype:
                value = (metadata_type, subtype)
            else:
                value = metadata_type

        return value

    def convert_system_types(self, value) -> tuple[str, str]:
        mapping = {
            "connection": ("DATA_SOURCE", None),
            "table": ("LOGICAL_TABLE", "ONE_TO_ONE_LOGICAL"),
            "view": ("LOGICAL_TABLE", "AGGR_WORKSHEET"),
            "sql_view": ("LOGICAL_TABLE", "SQL_VIEW"),
            "worksheet": ("LOGICAL_TABLE", "WORKSHEET"),
            "liveboard": ("PINBOARD_ANSWER_BOOK", None),
            "answer": ("QUESTION_ANSWER_BOOK", None),
        }
        return mapping[value]


class CommaSeparatedValuesType(click.ParamType):
    """
    Convert arguments to a list of strings.
    """

    name = "string"

    def __init__(self, *args_passthru, return_type: Any = str, **kwargs_passthru):
        super().__init__(*args_passthru, **kwargs_passthru)
        self.return_type = return_type

    def convert(self, value, param, ctx):  # noqa: ARG002
        if value is None:
            return None

        if isinstance(value, str):
            values = value.split(",")

        elif isinstance(value, collections.abc.Iterable):
            values = [v.split(",") if isinstance(v, str) else v for v in value]

        return [self.return_type(v) for v in it.chain.from_iterable(values) if v != ""]
