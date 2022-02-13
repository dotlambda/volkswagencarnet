import logging.config
import os
import unittest
from io import StringIO
from sys import argv
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import pytest
import pytest_asyncio
from aiohttp import CookieJar, ClientSession

import volkswagencarnet.vw_connection
from .fixtures import resource_path
from volkswagencarnet.vw_connection import Connection
from volkswagencarnet.vw_vehicle import Vehicle


@pytest.fixture
def connection(session):
    """Real connection for integration tests"""
    return Connection(
        session=session,
        username='',
        password='',
        country='DE',
        interval=999,
        fulldebug=True
    )


@pytest_asyncio.fixture
async def session():
    """Client session that can be used in tests"""
    jar = CookieJar()
    jar.load(os.path.join(resource_path, 'dummy_cookies.pickle'))
    sess = ClientSession(
        headers={'Connection': 'keep-alive'},
        cookie_jar=jar
    )
    yield sess
    await sess.close()


def test_clear_cookies(connection):
    assert len(connection._session._cookie_jar._cookies) > 0
    connection._clear_cookies()
    assert len(connection._session._cookie_jar._cookies) == 0


class CmdLineTest(IsolatedAsyncioTestCase, unittest.TestCase):

    class FailingLoginConnection:
        def __init__(self, sess, **kwargs):
            self._session = sess

        async def doLogin(self):
            return False

    class TwoVehiclesConnection:
        def __init__(self, sess, **kwargs):
            self._session = sess

        async def doLogin(self):
            return True

        async def update(self):
            return True

        @property
        def vehicles(self):
            vehicle1 = Vehicle(None, 'vin1')
            vehicle2 = Vehicle(None, 'vin2')
            return [vehicle1, vehicle2]

    @pytest.mark.asyncio
    @patch.object(volkswagencarnet.vw_connection.logging, 'basicConfig')
    @patch('volkswagencarnet.vw_connection.Connection', spec_set=Connection, new=FailingLoginConnection)
    async def test_main_argv(self, logger_config):
        # Assert default logger level is ERROR
        await volkswagencarnet.vw_connection.main()
        logger_config.assert_called_with(level=logging.ERROR)

        # -v should be INFO
        argv.append('-v')
        await volkswagencarnet.vw_connection.main()
        logger_config.assert_called_with(level=logging.INFO)
        argv.remove('-v')

        # -vv should be DEBUG
        argv.append('-vv')
        await volkswagencarnet.vw_connection.main()
        logger_config.assert_called_with(level=logging.DEBUG)

    @pytest.mark.asyncio
    @patch('sys.stdout', new_callable = StringIO)
    @patch('volkswagencarnet.vw_connection.Connection', spec_set=Connection, new=FailingLoginConnection)
    async def test_main_output_failed(self, stdout: StringIO):
        await volkswagencarnet.vw_connection.main()
        assert stdout.getvalue() == ""

    @pytest.mark.asyncio
    @patch('sys.stdout', new_callable = StringIO)
    @patch('volkswagencarnet.vw_connection.Connection', spec_set=Connection, new=TwoVehiclesConnection)
    async def test_main_output_two_vehicles(self, stdout: StringIO):
        await volkswagencarnet.vw_connection.main()
        assert stdout.getvalue() == """Vehicle id: vin1
Supported sensors:
 - Force data refresh (domain:switch) - Off
 - Request results (domain:sensor) - Unknown
 - Requests remaining (domain:sensor) - -1
 - Request in progress (domain:binary_sensor) - Off
Vehicle id: vin2
Supported sensors:
 - Force data refresh (domain:switch) - Off
 - Request results (domain:sensor) - Unknown
 - Requests remaining (domain:sensor) - -1
 - Request in progress (domain:binary_sensor) - Off
"""
