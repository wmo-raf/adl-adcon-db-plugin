from django.urls import path
from wagtail import hooks

from .views import (
    adcon_db_station_detail,
    get_adcon_stations_for_connection,
    get_adcon_variables_for_connection
)


@hooks.register('register_admin_urls')
def urlconf_wis2box_adl_adcon_plugin():
    return [
        
        path("adl-db-plugin/adcon-conn-stations/", get_adcon_stations_for_connection,
             name="get_adcon_stations_for_connection"),
        path("adl-db-plugin/adcon-conn-variables/", get_adcon_variables_for_connection,
             name="get_adcon_variables_for_connection"),
        path('adl-db-plugin/station-detail/<int:station_link_id>/', adcon_db_station_detail,
             name='adcon_db_station_detail'),
    ]
