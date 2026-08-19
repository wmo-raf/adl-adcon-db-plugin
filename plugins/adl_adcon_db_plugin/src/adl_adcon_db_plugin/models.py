import psycopg2
from adl.core.models import NetworkConnection, StationLink, DataParameter, Unit
from django.db import models
from django.utils.translation import gettext, gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, InlinePanel

from .db import ADCONDBClient, category_for_sqlstate
from .validators import validate_start_date
from .widgets import AdconStationSelectWidget, AdconVariableSelectWidget

# What the diagnostic's on-demand checks pass instead of the ingestion default.
# Core bounds its whole probe — DNS, TCP and the check together — by a 15-second
# wall clock and abandons rather than kills a worker that overruns it, so the
# check has to come back first with a real verdict. Deliberately not a model
# field: an operator who raised it to 300 for a slow partner would silently
# re-break the probe.
SOURCE_CHECK_CONNECT_TIMEOUT_SECONDS = 5


class ADCONDBConnection(NetworkConnection):
    station_link_model_string_label = "adl_adcon_db_plugin.ADCONStationLink"
    db_host = models.CharField(max_length=255, verbose_name=_("Database Host"))
    db_port = models.PositiveIntegerField(verbose_name=_("Database Port"))
    db_name = models.CharField(max_length=255, verbose_name=_("Database Name"))
    db_user = models.CharField(max_length=255, verbose_name=_("Database User"))
    db_password = models.CharField(max_length=255, blank=True, null=True, verbose_name=_("Database Password"))

    only_stations_with_coords = models.BooleanField(default=False,
                                                    verbose_name=_("List Only Stations with Coordinates"))

    panels = NetworkConnection.panels + [
        MultiFieldPanel([
            FieldPanel("db_host"),
            FieldPanel("db_port"),
            FieldPanel("db_name"),
            FieldPanel("db_user"),
            FieldPanel("db_password"),
        ], heading=_("Database Credentials")),
        FieldPanel("only_stations_with_coords"),
    ]

    class Meta:
        verbose_name = _("ADCON Database Connection")
        verbose_name_plural = _("ADCON Database Connections")

    def get_db_connection(self, connect_timeout=None):
        """
        Returns the ADCON database client.

        The default is the ingestion path's behaviour, unchanged — an unbounded
        connect. The diagnostic's on-demand checks pass a bound instead, so they
        come back inside core's probe budget without changing what ingestion
        does.
        """
        return ADCONDBClient(
            db_host=self.db_host,
            db_port=self.db_port,
            db_user=self.db_user,
            db_password=self.db_password,
            db_name=self.db_name,
            connect_timeout=connect_timeout,
        )

    def get_source_endpoint(self):
        """
        The (host, port) core's generic DNS -> TCP probe dials (layer 4 of the
        ingestion diagnostic).

        Both fields are required and configured by the operator, so there is
        nothing to parse and nothing to guess.
        """
        return self.db_host, self.db_port

    def check_source(self):
        """
        Ask whether the source accepts our credentials and answers (layer 5 of
        the ingestion diagnostic). Read-only, on demand only.

        The client connects in its constructor, so construction belongs inside
        the guarded region: for this archetype the connect *is* the credential
        check, since libpq completes the startup handshake, the credential and
        the database selection before returning. One trivial round trip follows
        it, which is what catches a server in recovery or a pooler that accepts
        the startup packet and fails on the first statement.

        Accepted gap: SELECT 1 cannot tell "server healthy" from "server healthy
        but our grants on the data tables are gone". That question belongs to
        the station check, which answers it.
        """
        # Imported lazily: this module does not exist on a core release
        # predating the source-check contracts, where this method is never
        # called and a module-level import would kill the whole plugin.
        from adl.core.source_checks import SourceCheckResult, SourceCheckStatus

        client = None

        try:
            client = self.get_db_connection(
                connect_timeout=SOURCE_CHECK_CONNECT_TIMEOUT_SECONDS)
            client.ping_readonly()
        except psycopg2.OperationalError as e:
            # OperationalError only. Anything else is our bug rather than the
            # source's, and core's container logs strictly more about it than a
            # friendly sentence here would.
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                category=category_for_sqlstate(e.pgcode),
                message=str(e),
            )
        finally:
            if client is not None:
                client.close()

        return SourceCheckResult(
            status=SourceCheckStatus.OK,
            message=gettext("Connected to %(db)s on %(host)s:%(port)s as %(user)s.") % {
                "db": self.db_name,
                "host": self.db_host,
                "port": self.db_port,
                "user": self.db_user,
            },
        )


class ADCONStationLink(StationLink):
    adcon_station_id = models.PositiveIntegerField(verbose_name=_("ADCON Station ID"),
                                                   help_text=_("Select an ADCON Station ID"))
    start_date = models.DateTimeField(
        blank=True,
        null=True,
        validators=[validate_start_date],
        verbose_name=_("Collection Start Date"),
        help_text=_(
            "Collection never starts before this date. On the first run it is "
            "the start of the backfill; afterwards, moving it forward past the "
            "latest saved record skips the gap. Leave empty to start from the "
            "last hour."
        ),
    )

    panels = StationLink.panels + [
        FieldPanel("adcon_station_id", widget=AdconStationSelectWidget("get_adcon_stations_for_connection")),
        FieldPanel("start_date"),
        InlinePanel("variable_mappings", label=_("Station Variable Mapping"), heading=_("Station Variable Mappings")),
    ]

    class Meta:
        verbose_name = _("ADCON Station Link")
        verbose_name_plural = _("ADCON Station Links")

    def __str__(self):
        return f"{self.adcon_station_id} - {self.station} - {self.station.wigos_id}"

    def get_variable_mappings(self):
        """
        Returns the variable mappings for this station link.
        """
        return self.variable_mappings.all()

    def get_first_collection_date(self):
        """
        Returns the first collection date for this station link.
        Returns None if no start date is set.
        """
        return self.start_date

    def check_station_source(self):
        """
        Ask whether this station's ADCON id resolves at the source (layer 5 of
        the ingestion diagnostic, station-scoped).

        Two queries, because they answer different questions. The station table
        is authoritative, so a membership test over it gives positive proof of
        absence and the upstream's own label for free. The per-station
        parameters query is then run **for its error, not its result**: it is
        the only place in the whole diagnostic that can see a missing per-table
        SELECT grant, which is an ordinary fault that otherwise produces a
        permanently silent connection — layer 4 fine, layer 5 fine, and layer 6
        watching no records arrive with nothing to blame.
        """
        from adl.core.source_checks import SourceCheckResult, SourceCheckStatus

        connection = self.network_connection
        client = None

        try:
            client = connection.get_db_connection(
                connect_timeout=SOURCE_CHECK_CONNECT_TIMEOUT_SECONDS)
            stations = client.get_stations()

            station = next(
                (s for s in stations if s.get("id") == self.adcon_station_id), None)

            if station is None:
                # Absent from the authoritative station table is proof, not
                # suspicion: this station link can never ingest anything.
                return SourceCheckResult(
                    status=SourceCheckStatus.FAILED,
                    category="PATH_NOT_FOUND",
                    message=gettext("Station %(station)s was not found in the source's "
                                    "station table.") % {
                        "station": self.adcon_station_id,
                    },
                )

            parameters = client.get_adcon_parameters_for_station(self.adcon_station_id)
        except psycopg2.Error as e:
            # Never convert a failed read into OK — and never into a claim of
            # absence either, which we have no proof of here. Only 42501 carries
            # a category: schema drift is a plugin bug, not an operator's
            # misconfiguration, so 42P01 and the rest decline.
            return SourceCheckResult(
                status=SourceCheckStatus.FAILED,
                category=category_for_sqlstate(e.pgcode),
                message=str(e),
            )
        finally:
            if client is not None:
                client.close()

        # The upstream's own label is what catches a valid-but-wrong id — a real
        # station belonging to a different site — which is the failure that
        # yields plausible wrong data rather than an outage. The parameter count
        # is a byproduct of a query we had to run anyway; zero is still OK,
        # stated plainly, and left for the operator to judge.
        label = station.get("displayname") or ""
        count = len(parameters)

        if label:
            message = gettext('Station %(station)s found upstream as "%(label)s", '
                              'offering %(count)s parameter(s).') % {
                "station": self.adcon_station_id,
                "label": label,
                "count": count,
            }
        else:
            message = gettext("Station %(station)s was found in the source's station "
                              "table, offering %(count)s parameter(s).") % {
                "station": self.adcon_station_id,
                "count": count,
            }

        return SourceCheckResult(status=SourceCheckStatus.OK, message=message)


class ADCONStationVariableMapping(models.Model):
    station_link = ParentalKey(ADCONStationLink, on_delete=models.CASCADE, related_name="variable_mappings")
    adl_parameter = models.ForeignKey(DataParameter, on_delete=models.CASCADE, verbose_name=_("ADL Parameter"))
    adcon_parameter_id = models.PositiveIntegerField(verbose_name=_("ADCON Parameter ID"), unique=True)
    adcon_parameter_unit = models.ForeignKey(Unit, on_delete=models.CASCADE, verbose_name=_("ADCON Parameter Unit"))

    panels = [
        FieldPanel("adl_parameter"),
        FieldPanel("adcon_parameter_id", widget=AdconVariableSelectWidget),
        FieldPanel("adcon_parameter_unit"),
    ]

    def __str__(self):
        return f"{self.station_link.station.name} - {self.adl_parameter} - {self.adcon_parameter_id}"

    @property
    def source_parameter_name(self):
        """
        Returns the shortcode of the TAHMO variable.
        """
        return self.adcon_parameter_id

    @property
    def source_parameter_unit(self):
        """
        Returns the unit of the TAHMO variable.
        """
        return self.adcon_parameter_unit
