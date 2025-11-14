from adl.core.models import NetworkConnection, StationLink, DataParameter, Unit
from django.db import models
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, MultiFieldPanel, InlinePanel

from .db import ADCONDBClient
from .validators import validate_start_date
from .widgets import AdconStationSelectWidget, AdconVariableSelectWidget


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
    
    def get_db_connection(self):
        return ADCONDBClient(
            db_host=self.db_host,
            db_port=self.db_port,
            db_user=self.db_user,
            db_password=self.db_password,
            db_name=self.db_name
        )


class ADCONStationLink(StationLink):
    adcon_station_id = models.PositiveIntegerField(verbose_name=_("ADCON Station ID"),
                                                   help_text=_("Select an ADCON Station ID"))
    start_date = models.DateTimeField(blank=True, null=True, validators=[validate_start_date],
                                      verbose_name=_("Start Date"),
                                      help_text=_("Start date for data pulling. Select a past date to include the "
                                                  "historical data. Leave blank for collecting realtime data only"), )
    
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
