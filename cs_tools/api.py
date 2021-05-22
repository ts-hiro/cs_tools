import logging.config
import logging

import httpx

from cs_tools.models.ts_dataservice import TSDataService
from cs_tools.models.dependency import _Dependency
from cs_tools.models.periscope import _Periscope
from cs_tools.models.metadata import Metadata, _Metadata
from cs_tools.models.security import _Security
from cs_tools.models.auth import Session
from cs_tools.models.user import User
from cs_tools.schema.user import User as UserSchema


log = logging.getLogger(__name__)


class ThoughtSpot:
    """
    """
    def __init__(self, ts_config):
        self.config = ts_config
        self._setup_logging()

        # set up our session
        self.http = httpx.Client(timeout=10.0, verify=not ts_config.thoughtspot.disable_ssl)
        self.http.headers.update({'X-Requested-By': 'ThoughtSpot'})

        # set in __enter__()
        self.logged_in_user = None
        self.thoughtspot_version = None

        # add remote TQL & tsload services
        self.ts_dataservice = TSDataService(self)

        # add public API endpoints
        self.auth = Session(self)
        self.metadata = Metadata(self)
        self.user = User(self)

        # add private API endpoints
        self._dependency = _Dependency(self)
        self._metadata = _Metadata(self)
        self._periscope = _Periscope(self)
        self._security = _Security(self)

    def _setup_logging(self):
        logging.getLogger('urllib3').setLevel(logging.ERROR)

        logging.basicConfig(
            format='[%(levelname)s - %(asctime)s] '
                   '[%(name)s - %(module)s.%(funcName)s %(lineno)d] '
                   '%(message)s',
            level='INFO'
        )

        # try:
        #     logging.config.dictConfig(**self.config.logging.dict())
        #     log.info(f'set up provided logger at level {self.config.logging}')
        # except (ValueError, AttributeError):
        #     logging.basicConfig(
        #         format='[%(levelname)s - %(asctime)s] '
        #                '[%(name)s - %(module)s.%(funcName)s %(lineno)d] '
        #                '%(message)s',
        #         level=getattr(logging, self.config.logging.level)
        #     )

        #     level = logging.getLevelName(logging.getLogger('root').getEffectiveLevel())
        # log.info(f'set up the default logger at level {level}')

    @property
    def host(self):
        """
        URL of ThoughtSpot.
        """
        return self.config.thoughtspot.host

    def __enter__(self):
        try:
            r = self.auth.login()
        except httpx.ConnectError as e:
            if 'CERTIFICATE_VERIFY_FAILED' in str(e):
                log.error('SSL verify failed, did you mean to use flag --disable_ssl?')
                raise SystemExit(1)

        rj = r.json()

        self.logged_in_user = UserSchema(
            guid=rj['userGUID'], name=rj['userName'], display_name=rj['userDisplayName'],
            email=rj['userEmail'], privileges=rj['privileges']
        )

        self.thoughtspot_version = rj['releaseVersion']
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.auth.logout()
