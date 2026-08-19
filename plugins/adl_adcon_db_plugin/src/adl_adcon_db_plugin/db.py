from contextlib import contextmanager
from datetime import datetime

import psycopg2

# The ingestion diagnostic's failure categories, keyed by the SQLSTATE the
# server sent. The strings are written out rather than imported from core:
# importing core's vocabulary would break this plugin at import time on an older
# core, and core drops any value it does not recognise anyway. The table is this
# repo's own on purpose — `28000` and `3D000` are standard SQLSTATE but `28P01`
# is Postgres-only, so nothing here transfers to another driver unexamined.
#
# `42501` can only ever arrive from a statement, and the three above only from a
# connection attempt, so no surface sees the whole table. Nothing here stamps
# UNKNOWN: declining leaves core's read-time classification free to do better
# later, and a stamp does not.
SQLSTATE_CATEGORIES = {
    "28P01": "AUTH_FAILED",       # invalid_password
    "28000": "AUTH_FAILED",       # invalid_authorization_specification
    "3D000": "PATH_NOT_FOUND",    # the named database does not exist
    "42501": "PERMISSION_DENIED",  # insufficient_privilege on a table
}


def category_for_sqlstate(pgcode):
    """The diagnostic failure category for a SQLSTATE, or None where it carries
    no honest one.

    `pgcode is None` means the failure was client-side — libpq never received an
    ErrorResponse — which is DNS, refused or connect timeout. Core's own steps
    already named those at layer 4, so declining here is refusing to re-report a
    layer-4 fault at layer 5, not a gap.

    `53300 too_many_connections` and `57P03 cannot_connect_now` stay declined
    too, even though they are unambiguous: no category fits, and the raw message
    is already legible.
    """
    return SQLSTATE_CATEGORIES.get(pgcode)


@contextmanager
def _stamping():
    """Tag an OperationalError raised inside with the source's own verdict.

    The exception is stamped in place rather than wrapped, so the original type
    still matches core's own exception table and the traceback survives. A code
    from the server is proof the server answered, which is what makes every
    category derived from one layer 5.

    OperationalError only: everything else propagates untouched. A TypeError
    from a malformed field or an InterfaceError from misusing a closed
    connection is our bug, not the source's, and core's container names the type
    and logs the full traceback.
    """
    try:
        yield
    except psycopg2.OperationalError as e:
        category = category_for_sqlstate(e.pgcode)
        if category:
            e.adl_category = category
            e.adl_layer = 5
        raise


class ADCONDBClient:
    def __init__(self, db_host, db_port, db_name, db_user, db_password, connect_timeout=None):
        # connect_timeout defaults to None, which is today's unbounded ingestion
        # connect. The diagnostic's on-demand checks pass a bound; bounding
        # ingestion would change runtime behaviour across deployments for
        # reasons that have nothing to do with the diagnostic.
        options = {}
        if connect_timeout is not None:
            options["connect_timeout"] = connect_timeout

        with _stamping():
            self.connection = psycopg2.connect(
                host=db_host,
                port=db_port,
                password=db_password,
                dbname=db_name,
                user=db_user,
                **options,
            )

    def close(self):
        if self.connection:
            self.connection.close()

    def ping_readonly(self):
        """One trivial round trip on a server-enforced read-only session.

        Connecting alone would nearly do — libpq completes the startup
        handshake, credential and database selection inside the connect — but a
        round trip also catches a server in recovery, a connection limit hit at
        first statement, and a pooler that accepts the startup packet and fails
        on the first query.
        """
        with _stamping():
            self.connection.set_session(readonly=True)

            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1")

    def get_stations(self, only_stations_with_coords=False):
        sql = "SELECT id, displayname,latitude,longitude,timezoneid FROM node_60 WHERE dtype ='DeviceNode'"

        if only_stations_with_coords:
            sql += " AND latitude IS NOT NULL AND longitude IS NOT NULL"

        with _stamping(), self.connection.cursor() as cursor:
            cursor.execute(sql)
            stations = cursor.fetchall()

        stations = [dict(zip([column.name for column in cursor.description], station)) for station in stations]

        return stations

    def get_adcon_parameters_for_station(self, adcon_station_id):
        with _stamping(), self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT DISTINCT id, displayname, subclass
                   FROM node_60
                   WHERE dtype = 'AnalogTagNode'
                     and parent_id = %s""", (adcon_station_id,)
            )

            parameters = cursor.fetchall()

        parameters = [dict(zip([column.name for column in cursor.description], parameter)) for parameter in parameters]

        return parameters

    def get_data_for_parameters(self, parameter_ids, start_date, end_date, station_timezone):
        """Fetch the window's rows, returning ``(records, sources_count)``.

        The count is of the rows the query returned, taken once the fetch is
        materialised and before the reshaping below — which drops rows outside
        the sampling interval and collapses the rest by timestamp, so a count
        taken after it would let our own handling read as the source having
        offered nothing. The WHERE clause carries the window, so the result is
        the count. It leaves the client by return value because the station link
        it is reported on belongs to the plugin, not here.
        """

        # tag_id is the ADCON parameter id
        # status=0 means the data is valid

        if not parameter_ids:
            raise ValueError("No parameter ids provided")

        tag_ids_placeholders = ', '.join(['%s'] * len(parameter_ids))
        query = f"""
            SELECT tag_id, enddate, startdate, measuringvalue
            FROM historiandata
            WHERE tag_id IN ({tag_ids_placeholders})
            AND startdate >= %s
            AND enddate <= %s
            AND status = 0
        """

        parameters = parameter_ids + [start_date, end_date]

        with _stamping(), self.connection.cursor() as conn_cursor:
            conn_cursor.execute(query, parameters)

            data = conn_cursor.fetchall()

            # organize the data by dates
            parameter_data_by_date = {}

            for data_point in data:
                data_point = dict(zip([column.name for column in conn_cursor.description], data_point))

                end_date = datetime.fromtimestamp(data_point['enddate'], tz=station_timezone)
                start_date = datetime.fromtimestamp(data_point['startdate'], tz=station_timezone)
                tag_id = data_point['tag_id']

                time_diff = (end_date - start_date).total_seconds() / 60

                # 10 and 15 minutes interval,
                # take obs with greater than 3 minutes sampling and less than 20
                if 3 <= time_diff < 20:
                    data_point["enddate"] = end_date
                    data_point["startdate"] = start_date

                    if not parameter_data_by_date.get(end_date):
                        parameter_data_by_date[end_date] = {
                            "observation_time": end_date
                        }

                    parameter_data_by_date[end_date][tag_id] = data_point["measuringvalue"]

        return list(parameter_data_by_date.values()), len(data)
