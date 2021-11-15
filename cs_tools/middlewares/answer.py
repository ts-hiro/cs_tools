from typing import Any, Dict, List, Union
import logging

from pydantic import validate_arguments

from cs_tools._enums import MetadataCategory
from cs_tools.errors import ContentDoesNotExist


log = logging.getLogger(__name__)


class AnswerMiddleware:
    """
    """
    def __init__(self, ts):
        self.ts = ts

    @validate_arguments
    def all(
        self,
        *,
        tags: Union[str, List[str]] = None,
        category: MetadataCategory = MetadataCategory.all,
        exclude_system_content: bool = True,
        chunksize: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Get all answers in ThoughtSpot.

        Parameters
        ----------
        tags : str, or list of str
          answers which are specifically tagged or stickered

        category : str = 'all'
          one of: 'all', 'yours', or 'favorites'

        exclude_system_content : bool = True
          whether or not to include system-generated answers

        Returns
        -------
        answers : List[Dict[str, Any]]
          all answer headers
        """
        if isinstance(tags, str):
            tags = [tags]

        offset = 0
        answers = []

        while True:
            r = self.ts.api._metadata.list(
                    type='QUESTION_ANSWER_BOOK',
                    category=category,
                    tagname=tags,
                    batchsize=chunksize,
                    offset=offset
                )

            data = r.json()
            to_extend = data['headers']
            offset += len(to_extend)

            if exclude_system_content:
                to_extend = [
                    answer
                    for answer in to_extend
                    if answer['authorName'] not in ('system', 'tsadmin')
                ]

            answers.extend(to_extend)

            if not to_extend and not answers:
                rzn  = f"'{category.value}' category ("
                rzn += 'excluding ' if exclude_system_content else 'including '
                rzn += 'admin-generated answers)'
                rzn += '' if tags is None else ' and tags: ' + ', '.join(tags)
                raise ContentDoesNotExist(type='ANSWER', reason=rzn)

            if data['isLastBatch']:
                break

        return answers
