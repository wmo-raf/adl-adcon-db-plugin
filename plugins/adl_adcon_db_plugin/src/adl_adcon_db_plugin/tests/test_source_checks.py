"""
Tests for the ingestion-diagnostic contracts: ``get_source_endpoint()``,
``check_source()``, ``check_station_source()``, the ``adl_sources_count``
duck-typed handover and the SQLSTATE stamping in ``db.py``. See the
"Ingestion Diagnostic Contracts" page in the ADL developer guide.

All tests run without touching a database — neither ADL's nor ADCON's: model
instances are built unsaved and ``psycopg2.connect`` is stubbed, so the seam
under test is exactly the contract core consumes. That is what
``SimpleTestCase`` buys here — Django still calls ``setup_databases()``
whatever the class, so the suite is run on this plugin's own compose stack
with ``make test`` from the repo root.
"""

import ast
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import psycopg2
from adl.core.source_checks import SourceCheckResult, SourceCheckStatus
from django.test import SimpleTestCase

from adl_adcon_db_plugin import db as db_module
from adl_adcon_db_plugin.db import ADCONDBClient, category_for_sqlstate
from adl_adcon_db_plugin.models import ADCONDBConnection, ADCONStationLink
from adl_adcon_db_plugin.plugins import ADCONDBPlugin

DB_HOST = "adcon.example.org"
DB_PORT = 5432


class _PgCode:
    """psycopg2's ``pgcode`` is read-only on the real exceptions, so the test
    doubles below re-declare it as a property they can fill in."""

    def __init__(self, message="boom", pgcode=None):
        super().__init__(message)
        self._pgcode = pgcode

    @property
    def pgcode(self):
        return self._pgcode


class FakeOperationalError(_PgCode, psycopg2.OperationalError):
    """What a connect or a server-side session fault raises."""


class FakeProgrammingError(_PgCode, psycopg2.ProgrammingError):
    """What a missing grant or a missing table raises — not an
    OperationalError, which is why the stamping helper leaves it alone."""


class Column:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, rows=(), columns=(), error=None):
        self.rows = list(rows)
        self.description = [Column(name) for name in columns]
        self.error = error
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if self.error is not None:
            raise self.error

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursors=None):
        # One cursor per call, in order: the station check makes two queries.
        self.cursors = list(cursors or [FakeCursor()])
        self.handed_out = []
        self.readonly = None
        self.closed = False

    def cursor(self):
        cursor = self.cursors[len(self.handed_out)] if len(self.handed_out) < len(
            self.cursors) else self.cursors[-1]
        self.handed_out.append(cursor)
        return cursor

    def set_session(self, readonly=False):
        self.readonly = readonly

    def close(self):
        self.closed = True


def stub_connect(connection=None, error=None):
    """Patch psycopg2.connect where db.py looks it up, capturing its kwargs."""
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        return connection or FakeConnection()

    patcher = mock.patch.object(db_module.psycopg2, "connect", side_effect=connect)
    return patcher, calls


def make_connection(**kwargs):
    kwargs.setdefault("db_host", DB_HOST)
    kwargs.setdefault("db_port", DB_PORT)
    kwargs.setdefault("db_name", "adcon")
    kwargs.setdefault("db_user", "adl")
    kwargs.setdefault("db_password", "secret")
    return ADCONDBConnection(**kwargs)


def make_station_link(connection=None, **kwargs):
    kwargs.setdefault("adcon_station_id", 42)
    link = ADCONStationLink(**kwargs)
    link.network_connection = connection or make_connection()
    return link


class FakeDBClient:
    """A stubbed ADCON client that answers the calls a check makes."""

    STATION_COLUMNS = ("id", "displayname", "latitude", "longitude", "timezoneid")

    def __init__(self, stations=None, parameters=None, ping_error=None,
                 stations_error=None, parameters_error=None, data=None):
        self.stations = stations if stations is not None else []
        self.parameters = parameters if parameters is not None else []
        self.ping_error = ping_error
        self.stations_error = stations_error
        self.parameters_error = parameters_error
        self.data = data
        self.closed = False
        self.readonly = False

    def ping_readonly(self):
        if self.ping_error is not None:
            raise self.ping_error
        self.readonly = True

    def get_stations(self, only_stations_with_coords=False):
        if self.stations_error is not None:
            raise self.stations_error
        return self.stations

    def get_adcon_parameters_for_station(self, adcon_station_id):
        if self.parameters_error is not None:
            raise self.parameters_error
        return self.parameters

    def get_data_for_parameters(self, parameter_ids, start_date, end_date, tz):
        if self.data is None:
            raise FakeOperationalError("no data", pgcode=None)
        return self.data

    def close(self):
        self.closed = True


def station_row(station_id=42, label="Wad Medani"):
    return {"id": station_id, "displayname": label, "latitude": 14.4,
            "longitude": 33.5, "timezoneid": "Africa/Khartoum"}


def stub_db_client(client):
    """Patch the client factory, capturing the arguments the check passed."""
    calls = []

    def factory(self, **kwargs):
        calls.append(kwargs)
        return client

    patcher = mock.patch.object(ADCONDBConnection, "get_db_connection", autospec=True,
                                side_effect=factory)
    return patcher, calls


class GetSourceEndpointTests(SimpleTestCase):

    def test_returns_the_configured_host_and_port(self):
        self.assertEqual(make_connection().get_source_endpoint(), (DB_HOST, DB_PORT))


class GetDbConnectionTests(SimpleTestCase):
    """The factory's default is the ingestion path's behaviour, unchanged;
    only the on-demand checks ask for a bound."""

    def test_ingestion_connects_unbounded(self):
        patcher, calls = stub_connect()
        with patcher:
            make_connection().get_db_connection()
        self.assertNotIn("connect_timeout", calls[0])

    def test_the_checks_can_bound_the_connect(self):
        patcher, calls = stub_connect()
        with patcher:
            make_connection().get_db_connection(connect_timeout=5)
        self.assertEqual(calls[0]["connect_timeout"], 5)


class CheckSourceTests(SimpleTestCase):

    def run_check(self, client, connection=None):
        connection = connection or make_connection()
        patcher, calls = stub_db_client(client)
        with patcher:
            result = connection.check_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result, calls

    def test_a_completed_round_trip_is_ok(self):
        client = FakeDBClient()
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIsNone(result.category)
        self.assertIn(DB_HOST, result.message)
        self.assertIn("adcon", result.message)
        self.assertTrue(client.readonly)

    def test_bounds_the_connect(self):
        _result, calls = self.run_check(FakeDBClient())
        self.assertEqual(calls, [{"connect_timeout": 5}])

    def test_classifies_from_the_sqlstate_the_server_sent(self):
        for pgcode, category in (("28P01", "AUTH_FAILED"), ("28000", "AUTH_FAILED"),
                                 ("3D000", "PATH_NOT_FOUND")):
            with self.subTest(pgcode=pgcode):
                error = FakeOperationalError("refused", pgcode=pgcode)
                result, _calls = self.run_check(FakeDBClient(ping_error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertEqual(result.category, category)

    def test_declines_a_sqlstate_no_category_fits(self):
        # 53300 too_many_connections and 57P03 cannot_connect_now are
        # unambiguous but have no category; the raw message is already legible,
        # and UNKNOWN would claim a classification we did not make.
        for pgcode in ("53300", "57P03", "42P01"):
            with self.subTest(pgcode=pgcode):
                error = FakeOperationalError("busy", pgcode=pgcode)
                result, _calls = self.run_check(FakeDBClient(ping_error=error))
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertIsNone(result.category)

    def test_a_client_side_failure_declines(self):
        # pgcode is None means libpq never received an ErrorResponse: DNS,
        # refused or connect timeout, all of which core already named at layer
        # 4. Re-reporting one here would have the diagnostic contradict itself.
        error = FakeOperationalError("could not translate host name", pgcode=None)
        result, _calls = self.run_check(FakeDBClient(ping_error=error))
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertIsNone(result.category)

    def test_the_connection_is_closed_on_both_paths(self):
        for client in (FakeDBClient(),
                       FakeDBClient(ping_error=FakeOperationalError(pgcode="28P01"))):
            with self.subTest(client=client):
                self.run_check(client)
                self.assertTrue(client.closed)

    def test_survives_the_core_normaliser(self):
        from adl.core.source_checks import normalise_source_check_result
        result, _calls = self.run_check(FakeDBClient())
        self.assertEqual(normalise_source_check_result(result).status, SourceCheckStatus.OK)

    def test_core_detects_the_override(self):
        from adl.core.source_checks import connection_implements_check_source
        self.assertTrue(connection_implements_check_source(make_connection()))


class CheckStationSourceTests(SimpleTestCase):

    def run_check(self, client, link=None):
        link = link or make_station_link()
        patcher, calls = stub_db_client(client)
        with patcher:
            result = link.check_station_source()
        self.assertIsInstance(result, SourceCheckResult)
        self.assertIn(result.status, SourceCheckStatus.ALL)
        return result, calls

    def test_a_present_id_is_ok_with_the_upstream_label_and_count(self):
        client = FakeDBClient(stations=[station_row()], parameters=[{"id": 1}, {"id": 2}])
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("42", result.message)
        self.assertIn("Wad Medani", result.message)
        self.assertIn("2", result.message)

    def test_zero_parameters_is_still_ok_with_the_zero_stated(self):
        client = FakeDBClient(stations=[station_row()], parameters=[])
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("0", result.message)

    def test_a_present_id_without_a_label_still_reads_cleanly(self):
        client = FakeDBClient(stations=[{"id": 42, "displayname": None}])
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.OK)
        self.assertIn("42", result.message)

    def test_an_absent_id_is_proven_not_found(self):
        # The station table is authoritative, so absence from it is proof.
        client = FakeDBClient(stations=[station_row(station_id=7)])
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PATH_NOT_FOUND")
        self.assertIn("42", result.message)

    def test_a_missing_grant_on_the_parameters_table_is_permission_denied(self):
        # The only place in the whole diagnostic that can see this: layer 4 is
        # fine, check_source() is fine, and records silently never arrive.
        error = FakeProgrammingError("permission denied for table node_60", pgcode="42501")
        client = FakeDBClient(stations=[station_row()], parameters_error=error)
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertEqual(result.category, "PERMISSION_DENIED")

    def test_schema_drift_declines_rather_than_blaming_the_operator(self):
        error = FakeProgrammingError("relation does not exist", pgcode="42P01")
        client = FakeDBClient(stations=[station_row()], parameters_error=error)
        result, _calls = self.run_check(client)
        self.assertEqual(result.status, SourceCheckStatus.FAILED)
        self.assertIsNone(result.category)

    def test_bounds_the_connect(self):
        client = FakeDBClient(stations=[station_row()])
        _result, calls = self.run_check(client)
        self.assertEqual(calls, [{"connect_timeout": 5}])

    def test_a_failed_read_is_never_converted_into_ok_or_absence(self):
        for error in (FakeOperationalError("refused", pgcode=None),
                      FakeOperationalError("auth", pgcode="28P01"),
                      FakeProgrammingError("denied", pgcode="42501")):
            with self.subTest(pgcode=error.pgcode):
                client = FakeDBClient(stations_error=error)
                result, _calls = self.run_check(client)
                self.assertEqual(result.status, SourceCheckStatus.FAILED)
                self.assertNotEqual(result.category, "PATH_NOT_FOUND")

    def test_the_connection_is_closed_on_every_path(self):
        for client in (FakeDBClient(stations=[station_row()]),
                       FakeDBClient(stations=[]),
                       FakeDBClient(stations_error=FakeOperationalError(pgcode=None))):
            with self.subTest(client=client):
                self.run_check(client)
                self.assertTrue(client.closed)

    def test_core_detects_the_override(self):
        from adl.core.source_checks import station_link_implements_check_station_source
        self.assertTrue(station_link_implements_check_station_source(make_station_link()))


class SourcesCountTests(SimpleTestCase):
    """The count is committed only from something the source told us, and only
    once it has told us."""

    START = datetime(2026, 8, 1, tzinfo=timezone.utc)
    END = datetime(2026, 8, 2, tzinfo=timezone.utc)

    def collect(self, link, client):
        patcher, _calls = stub_db_client(client)
        # `timezone` is a read-only property on core's StationLink and `station`
        # is a foreign key an unsaved instance cannot resolve, so both are
        # patched rather than assigned. Neither is what these tests are about.
        station = SimpleNamespace(name="Wad Medani")
        with patcher, \
                mock.patch.object(ADCONStationLink, "timezone", timezone.utc), \
                mock.patch.object(ADCONStationLink, "station", station):
            return ADCONDBPlugin().get_station_data(link, self.START, self.END)

    def link_with_mappings(self, parameter_ids=(1,)):
        link = make_station_link()
        link.get_variable_mappings = lambda: [
            mock.Mock(adcon_parameter_id=pid) for pid in parameter_ids]
        return link

    def test_counts_the_rows_the_query_returned(self):
        link = self.link_with_mappings()
        records = self.collect(link, FakeDBClient(data=([{"a": 1}, {"a": 2}], 5)))
        self.assertEqual(link.adl_sources_count, 5)
        self.assertEqual(len(records), 2)

    def test_an_empty_answer_is_zero_not_silence(self):
        link = self.link_with_mappings()
        self.collect(link, FakeDBClient(data=([], 0)))
        self.assertEqual(link.adl_sources_count, 0)

    def test_a_failed_query_makes_no_claim_at_all(self):
        # None, never 0: a run that never got an answer must not accuse the
        # source of having offered nothing.
        link = self.link_with_mappings()
        link.adl_sources_count = None
        with self.assertRaises(psycopg2.OperationalError):
            self.collect(link, FakeDBClient(data=None))
        self.assertIsNone(link.adl_sources_count)

    def test_the_count_is_taken_before_the_reshaping(self):
        # Three rows returned, one of which our own sampling-interval filter
        # drops and two of which collapse onto one timestamp. The source offered
        # three; anything less would read as a partly-empty source.
        columns = ("tag_id", "enddate", "startdate", "measuringvalue")
        base = 1756684800  # 2025-09-01T00:00:00Z
        rows = [
            (1, base + 600, base, 21.5),        # 10 minutes: kept
            (2, base + 600, base, 55.0),        # same timestamp: collapses
            (3, base + 7200, base, 9.9),        # 120 minutes: dropped
        ]
        cursor = FakeCursor(rows=rows, columns=columns)
        patcher, _calls = stub_connect(FakeConnection([cursor]))
        with patcher:
            client = ADCONDBClient(DB_HOST, DB_PORT, "adcon", "adl", "secret")
            records, count = client.get_data_for_parameters(
                [1, 2, 3], base, base + 86400, timezone.utc)
        self.assertEqual(count, 3)
        self.assertEqual(len(records), 1)


class StampingTests(SimpleTestCase):
    """A failed ingestion run carries the server's own verdict into the
    activity log, stamped in place so core's type table still applies."""

    def connect(self, error):
        patcher, _calls = stub_connect(error=error)
        with patcher:
            ADCONDBClient(DB_HOST, DB_PORT, "adcon", "adl", "secret")

    def query(self, error):
        cursor = FakeCursor(error=error)
        patcher, _calls = stub_connect(FakeConnection([cursor]))
        with patcher:
            client = ADCONDBClient(DB_HOST, DB_PORT, "adcon", "adl", "secret")
            client.get_stations()

    def test_stamps_a_failed_connect_at_layer_5(self):
        for pgcode, category in (("28P01", "AUTH_FAILED"), ("28000", "AUTH_FAILED"),
                                 ("3D000", "PATH_NOT_FOUND")):
            with self.subTest(pgcode=pgcode):
                with self.assertRaises(psycopg2.OperationalError) as caught:
                    self.connect(FakeOperationalError("nope", pgcode=pgcode))
                self.assertEqual(caught.exception.adl_category, category)
                self.assertEqual(caught.exception.adl_layer, 5)

    def test_stamps_a_failed_query_at_layer_5(self):
        with self.assertRaises(psycopg2.OperationalError) as caught:
            self.query(FakeOperationalError("nope", pgcode="28000"))
        self.assertEqual(caught.exception.adl_category, "AUTH_FAILED")
        self.assertEqual(caught.exception.adl_layer, 5)

    def test_leaves_a_client_side_failure_unstamped(self):
        with self.assertRaises(psycopg2.OperationalError) as caught:
            self.connect(FakeOperationalError("could not translate host", pgcode=None))
        self.assertFalse(hasattr(caught.exception, "adl_category"))

    def test_leaves_a_sqlstate_with_no_category_unstamped(self):
        # Declining keeps core's read-time tier free to classify the row later;
        # a stamp — UNKNOWN above all — would block it permanently.
        with self.assertRaises(psycopg2.OperationalError) as caught:
            self.connect(FakeOperationalError("too many clients", pgcode="53300"))
        self.assertFalse(hasattr(caught.exception, "adl_category"))

    def test_a_statement_error_propagates_untouched(self):
        # Not an OperationalError, so the helper leaves it alone: a missing
        # grant is answered by the station check, and a missing table is our
        # bug, which core's container logs in full.
        with self.assertRaises(psycopg2.ProgrammingError) as caught:
            self.query(FakeProgrammingError("permission denied", pgcode="42501"))
        self.assertFalse(hasattr(caught.exception, "adl_category"))

    def test_core_reads_the_stamp(self):
        from adl.core.classification import classify_failure
        with self.assertRaises(psycopg2.OperationalError) as caught:
            self.connect(FakeOperationalError("nope", pgcode="28P01"))
        self.assertEqual(classify_failure(caught.exception), ("AUTH_FAILED", 5))

    def test_the_table_declines_what_it_cannot_name(self):
        self.assertIsNone(category_for_sqlstate(None))
        self.assertIsNone(category_for_sqlstate("42P01"))
        self.assertEqual(category_for_sqlstate("42501"), "PERMISSION_DENIED")


class OlderCoreImportSafetyTests(SimpleTestCase):
    """The plugin must import cleanly on a core release that predates the
    source-check contracts, so nothing may import ``adl.core.source_checks``
    at module level.

    The contracts import it lazily instead, inside the method that needs it.
    Never wrap that import in ``try/except ImportError``: on an older core the
    method is never called, so the handler is unreachable, and it would turn a
    genuine import failure into a silent "this plugin does not support the
    check".
    """

    # Every module this plugin ships. Extend it as the plugin grows more.
    MODULES = ["models.py", "plugins.py", "db.py", "apps.py", "views.py",
               "widgets.py", "utils.py", "validators.py", "wagtail_hooks.py"]

    DENIED = "adl.core.source_checks"

    def test_no_module_level_import_of_source_checks(self):
        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in self.MODULES:
            path = os.path.join(package_dir, name)
            if not os.path.exists(path):
                continue  # a module this plugin does not (yet) ship
            with open(path) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node.col_offset != 0:
                    continue  # indented imports are lazy, inside a function
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                self.assertNotIn(
                    self.DENIED, [module] + names,
                    f"{name} imports {self.DENIED} at module level")
