from django import forms
from wagtail.admin.forms import WagtailAdminModelForm

from .models import ADCONStationVariableMapping, ADCONStationLink


class VariableMappingForm(WagtailAdminModelForm):
    adcon_parameter_id = forms.ChoiceField(choices=[])
    station_link = forms.ModelChoiceField(queryset=ADCONStationLink.objects.all(), widget=forms.HiddenInput())
    
    class Meta:
        model = ADCONStationVariableMapping
        fields = ["adl_parameter", "adcon_parameter_id", "adcon_parameter_unit"]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        initial = kwargs.get("initial", {})
        station_link_id = initial.get("station_link")
        
        station_link = ADCONStationLink.objects.get(id=station_link_id)
        
        existing_parameters = ADCONStationVariableMapping.objects.filter(station_link=station_link)
        existing_adcon_parameter_ids = [parameter.adcon_parameter_id for parameter in existing_parameters]
        
        network_conn = station_link.network_connection
        
        db = network_conn.get_db_connection()
        
        parameters = db.get_adcon_parameters_for_station(station_link.adcon_station_id)
        
        # Filter out parameters that are already mapped
        parameters = [parameter for parameter in parameters if parameter["id"] not in existing_adcon_parameter_ids]
        
        existing_parameters_ids = [parameter.adcon_parameter_id for parameter in existing_parameters]
        
        # Filter out parameters that are already mapped
        self.fields["adl_parameter"].queryset = self.fields["adl_parameter"].queryset.exclude(
            id__in=existing_parameters_ids)
        
        adcon_parameter_id_choices = [("", "---------")] + [(parameter["id"], parameter["displayname"]) for parameter in
                                                            parameters]
        
        self.fields["adcon_parameter_id"].choices = adcon_parameter_id_choices
