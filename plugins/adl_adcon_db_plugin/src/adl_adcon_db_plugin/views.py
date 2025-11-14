from adl.core.utils import get_object_or_none
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from .models import (
    ADCONDBConnection,
    ADCONStationLink
)
from .utils import get_station_parameters


def get_adcon_stations_for_connection(request):
    network_connection_id = request.GET.get('connection_id')
    
    if not network_connection_id:
        response = {
            "error": _("Network connection ID is required.")
        }
        return JsonResponse(response, status=400)
    
    network_conn = get_object_or_none(ADCONDBConnection, pk=network_connection_id)
    
    if not network_conn:
        response = {
            "error": _("The selected connection is not an ADCON Database Connection")
        }
        
        return JsonResponse(response, status=400)
    
    db = network_conn.get_db_connection()
    stations = db.get_stations(only_stations_with_coords=network_conn.only_stations_with_coords)
    
    db.close()
    
    return JsonResponse(stations, safe=False)


def adcon_db_station_detail(request, station_link_id):
    station_link = ADCONStationLink.objects.get(pk=station_link_id)
    device_node_id = station_link.adcon_station_id
    
    network_conn = station_link.network_connection
    
    db = network_conn.get_db_connection()
    
    station_parameters = db.get_adcon_parameters_for_station(device_node_id)
    
    context = {
        "station_link": station_link,
        "station_parameters": station_parameters,
    }
    
    return render(request, "adl_adcon_db_plugin/adcon_db_station_detail.html", context)


def get_adcon_variables_for_connection(request):
    network_connection_id = request.GET.get('connection_id')
    
    if not network_connection_id:
        response = {
            "error": _("Network connection ID is required.")
        }
        return JsonResponse(response, status=400)
    
    network_conn = get_object_or_none(ADCONDBConnection, pk=network_connection_id)
    if not network_conn:
        response = {
            "error": _("The selected connection is not an ADCON Database Connection")
        }
        
        return JsonResponse(response, status=400)
    
    station_id = request.GET.get('station_id')
    if not station_id:
        response = {
            "error": _("Station ID is required.")
        }
        return JsonResponse(response, status=400)
    
    variables = get_station_parameters(network_conn, station_id) or []
    
    return JsonResponse(variables, safe=False)
