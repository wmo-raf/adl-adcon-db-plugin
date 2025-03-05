from django.urls import path
from wagtail import hooks

from .views import (
    adcon_db_station_detail,
    get_adcon_stations_for_connection,
    adcon_station_variable_mapping_list,
    adcon_station_variable_mapping_create,
    adcon_station_variable_mapping_delete
)


@hooks.register('register_admin_urls')
def urlconf_wis2box_adl_adcon_plugin():
    return [
        
        path("adl-db-plugin/adcon-conn-stations/", get_adcon_stations_for_connection,
             name="get_adcon_stations_for_connection"),
        path('adl-db-plugin/station-detail/<int:station_link_id>/', adcon_db_station_detail,
             name='adcon_db_station_detail'),
        path('adl-db-plugin/station-variable-mapping/<int:station_link_id>/',
             adcon_station_variable_mapping_list, name='adcon_station_variable_mapping_list'),
        path('adl-db-plugin/station-variable-mapping/<int:station_link_id>/create/',
             adcon_station_variable_mapping_create,
             name='adcon_station_variable_mapping_create'),
        path('adl-db-plugiin/station-variable-mapping/delete/<int:station_variable_mapping_id>/',
             adcon_station_variable_mapping_delete, name='adcon_station_variable_mapping_delete'),
    ]
