from django.forms import Widget, Select


class AdconStationSelectWidget(Widget):
    template_name = 'adl_adcon_db_plugin/widgets/adcon_station_select_widget.html'
    
    def __init__(self, adcon_stations_url_name, **kwargs):
        self.adcon_stations_url_name = adcon_stations_url_name
        super().__init__(**kwargs)
    
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        
        context.update({
            'adcon_stations_url_name': self.adcon_stations_url_name,
        })
        
        return context
