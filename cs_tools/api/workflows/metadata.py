from __future__ import annotations

from collections.abc import Coroutine, Iterable
from typing import cast
import asyncio
import datetime as dt
import itertools as it
import logging
import pathlib

import awesomeversion
import httpx

from cs_tools import types, utils
from cs_tools.api.client import RESTAPIClient
from cs_tools.api.workflows.utils import paginator

log = logging.getLogger(__name__)


async def fetch_all(
    object_types: Iterable[types.APIObjectType], *, http: RESTAPIClient, record_size: int = 5_000, **search_options
) -> list[types.APIResult]:
    """Wraps metadata/search fetching all objects of the given type and exhausts the pagination."""
    results: list[types.APIResult] = []
    tasks: list[asyncio.Task] = []

    async with utils.BoundedTaskGroup(max_concurrent=len(list(object_types))) as g:
        for object_type in object_types:
            search_options["guid"] = ""
            search_options["metadata"] = [{"type": object_type}]
            coro = paginator(http.metadata_search, record_size=record_size, **search_options)
            task = g.create_task(coro, name=object_type)
            tasks.append(task)

    for task in tasks:
        try:
            d = task.result()

        except httpx.HTTPError as e:
            log.error(f"Could not fetch all objects for '{task.get_name()}' object type, see logs for details..")
            log.debug(f"Full error: {e}", exc_info=True)
            continue

        results.extend(d)

    return results


async def fetch(
    typed_guids: dict[types.APIObjectType, Iterable[types.GUID]],
    *,
    http: RESTAPIClient,
    record_size: int = 5_000,
    **search_options,
) -> list[types.APIResult]:
    """Wraps metadata/search fetching specific objects and exhausts the pagination."""
    CONCURRENCY_MAGIC_NUMBER = 15  # Why? In case **search_options contains

    results: list[types.APIResult] = []
    tasks: list[asyncio.Task] = []

    async with utils.BoundedTaskGroup(max_concurrent=CONCURRENCY_MAGIC_NUMBER) as g:
        for metadata_type, guids in typed_guids.items():
            for guid in guids:
                # A SINGLE OBJECT
                if isinstance(guid, str):
                    search_options["metadata"] = [{"type": metadata_type, "identifier": guid}]

                # AN ARRAY OF OBJECTS
                if isinstance(guid, list):
                    search_options["metadata"] = [{"type": metadata_type, "identifier": _} for _ in guid]

                coro = http.metadata_search(guid="", record_size=record_size, **search_options)
                task = g.create_task(coro, name=guid)
                tasks.append(task)

    for task in tasks:
        try:
            r = task.result()
            r.raise_for_status()
            d = r.json()

        except httpx.HTTPError as e:
            log.error(f"Could not fetch the object for guid={task.get_name()}, see logs for details..")
            log.debug(f"Full error: {e}", exc_info=True)
            continue

        results.extend(d)

    return results


async def permissions(
    typed_guids: dict[types.APIObjectType, Iterable[types.GUID]],
    *,
    compat_ts_version: awesomeversion.AwesomeVersion,
    record_size: int = -1,
    http: RESTAPIClient,
    **permission_options,
) -> list[types.APIResult]:
    """Wraps security/metadata/fetch-permissions fetching specific objects and exhausts the pagination."""
    CONCURRENCY_MAGIC_NUMBER = 5  # Why? Fetching permissions could potentially be very expensive for the server.
    FIFTEEN_MINUTES = 60 * 15

    results: list[types.APIResult] = []
    tasks: list[asyncio.Task] = []

    async with utils.BoundedTaskGroup(max_concurrent=CONCURRENCY_MAGIC_NUMBER) as g:
        for metadata_type, guids in typed_guids.items():
            for guid in guids:
                # DEV NOTE: @boonhapus, 2024/11/25
                # 10.3.0 IS WHEN WE RELEASED .permission_type={DEFINED|EFFECTIVE} FOR THE
                # ENDPOINT security/metadata/fetch-permissions , PRIOR TO THIS, THE DEFAULT
                # WAS TO FETCH EFFECTIVE PERMISSIONS.
                #
                # ONCE 10.3.0.SW IS N-2, WE CAN SWITCH FROM typed_guids -> guids .
                #
                if compat_ts_version < "10.3.0":
                    # A SINGLE OBJECT
                    if isinstance(guid, str):
                        permission_options["id"] = [guid]

                    # AN ARRAY OF OBJECTS
                    if isinstance(guid, list):
                        permission_options["id"] = guid

                    c = http.v1_security_metadata_permissions(guid="", api_object_type=metadata_type, **permission_options)
                else:
                    permission_options["timeout"] = FIFTEEN_MINUTES

                    # A SINGLE OBJECT
                    if isinstance(guid, str):
                        permission_options["metadata"] = [{"type": metadata_type, "identifier": guid}]

                    # AN ARRAY OF OBJECTS
                    if isinstance(guid, list):
                        permission_options["metadata"] = [{"type": metadata_type, "identifier": _} for _ in guid]

                    c = http.security_metadata_permissions(guid="", record_size=record_size, **permission_options)

                t = g.create_task(c, name=guid)
                tasks.append(t)

    for task in tasks:
        try:
            r = task.result()
            r.raise_for_status()
            d = r.json()

        except httpx.HTTPError as e:
            log.error(f"Could not fetch the permissions for guid={task.get_name()}, see logs for details..")
            log.debug(f"Full error: {e}", exc_info=True)
            continue

        results.append(d)

    return results


async def dependents(guid: types.GUID, *, http: RESTAPIClient) -> list[types.APIResult]:
    """Fetch all dependents of a given object, regardless of its type."""
    r = await http.metadata_search(
        guid=guid, include_details=True, include_dependent_objects=True, dependent_objects_record_size=-1
    )

    r.raise_for_status()
    _: types.APIResult = next(iter(r.json()), {})

    # DEV NOTE: @boonhapus, 2024/11/30
    # metadata/search?include_dependent_objects=True DOESN'T WORK FOR CONNECTIONS.
    if _["metadata_type"] == "CONNECTION":
        track_top_level_info = True
        coros: list[Coroutine] = []

        for logical_table in _["metadata_detail"]["logicalTableList"]:
            guid = logical_table["header"]["id"]
            c = http.metadata_search(guid=guid, include_dependent_objects=True, dependent_objects_record_size=-1)
            coros.append(c)

        _ = await asyncio.gather(*coros)  # type: ignore[assignment]
        d = cast(Iterable[types.APIResult], it.chain.from_iterable(r.json() for r in _))  # type: ignore[attr-defined]
    else:
        track_top_level_info = False
        d = cast(Iterable[types.APIResult], [_])

    all_dependents: list[types.APIResult] = []
    seen: set[types.GUID] = set()

    for metadata_object in d:
        if track_top_level_info and metadata_object["metadata_id"] not in seen:
            all_dependents.append(
                {
                    "guid": metadata_object["metadata_id"],
                    "name": metadata_object["metadata_name"],
                    "type": types.lookup_api_type(metadata_object["metadata_type"]),
                    "author_guid": metadata_object["metadata_header"]["author"],
                    "author_name": metadata_object["metadata_header"]["authorName"],
                    "tags": metadata_object["metadata_header"]["tags"],
                    "last_modified": dt.datetime.fromtimestamp(
                        metadata_object["metadata_header"]["modified"] / 1000, tz=dt.timezone.utc
                    ),
                }
            )

            seen.add(metadata_object["metadata_id"])

        for dependent_info in metadata_object["dependent_objects"].values():
            for dependent_type, dependents in dependent_info.items():
                for dependent in dependents:
                    if dependent["id"] in seen:
                        continue

                    all_dependents.append(
                        {
                            "guid": dependent["id"],
                            "name": dependent["name"],
                            "type": types.lookup_api_type(dependent_type),
                            "author_guid": dependent["author"],
                            "author_name": dependent.get("authorName", "UNKNOWN"),
                            "tags": dependent["tags"],
                            "last_modified": dt.datetime.fromtimestamp(
                                dependent["modified"] / 1000, tz=dt.timezone.utc
                            ),
                        }
                    )

                    seen.add(dependent["id"])

    return all_dependents


async def tml_export(
    guid: types.GUID,
    *,
    directory: pathlib.Path | None = None,
    http: RESTAPIClient,
    **tml_export_options,
) -> types.APIResult:
    """Export a metadata object, optionally to a directory."""
    try:
        r = await http.metadata_tml_export(guid=guid, **tml_export_options)
        r.raise_for_status()

        d = next(iter(r.json()))

        if d["info"]["status"]["status_code"] == "ERROR":
            raise ValueError(d["info"])

    except (httpx.HTTPStatusError, StopIteration):
        log.error(f"Unable to export {guid}, see log for details..")
        log.debug(r.text)
        return {"edoc": None, "info": {"id": guid}, "httpx_response": r.text}

    except ValueError as e:
        log.error(f"Unable to export {guid}, see log for details..")
        log.debug(e.args[0])
        return {"edoc": None, "info": e.args[0]}

    if directory is not None:
        i = d["info"]
        directory.joinpath(i["type"]).mkdir(parents=True, exist_ok=True)
        directory.joinpath(f"{i['type']}/{i['id']}.{i['type']}.tml").write_text(d["edoc"], encoding="utf-8")

    return d
