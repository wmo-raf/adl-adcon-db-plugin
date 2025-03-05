from adl.core.utils import get_object_or_none
from django.core.paginator import Paginator, InvalidPage
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _, gettext
from wagtail.admin import messages
from wagtail.admin.ui.tables import ButtonsColumnMixin, Column, Table
from wagtail.admin.widgets import HeaderButton, ListingButton
from wagtail_modeladmin.helpers import AdminURLHelper, PermissionHelper

from .forms import VariableMappingForm
from .models import (
    ADCONDBConnection,
    ADCONStationLink,
    ADCONStationVariableMapping
)


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


def adcon_station_variable_mapping_list(request, station_link_id):
    template_name = "adl_adcon_db_plugin/station_variable_mapping_list.html"
    queryset = ADCONStationVariableMapping.objects.filter(station_link_id=station_link_id)
    
    station_link_admin_helper = AdminURLHelper(ADCONStationLink)
    station_link_index_url = station_link_admin_helper.get_action_url("index")
    
    breadcrumbs_items = [
        {"url": station_link_index_url, "label": ADCONStationLink._meta.verbose_name_plural},
        {"url": "", "label": _("Station Variable Mapping")},
    ]
    
    # Get search parameters from the query string.
    try:
        page_num = int(request.GET.get("p", 0))
    except ValueError:
        page_num = 0
    
    user = request.user
    all_count = queryset.count()
    result_count = all_count
    paginator = Paginator(queryset, 20)
    
    try:
        page_obj = paginator.page(page_num + 1)
    except InvalidPage:
        page_obj = paginator.page(1)
    
    permission_helper = PermissionHelper(ADCONStationVariableMapping)
    
    buttons = [
        HeaderButton(
            label=_('Add Station Variable Mapping'),
            url=reverse("adcon_station_variable_mapping_create", args=[station_link_id]),
            icon_name="plus",
        ),
    ]
    
    class ColumnWithButtons(ButtonsColumnMixin, Column):
        cell_template_name = "wagtailadmin/tables/title_cell.html"
        
        def get_buttons(self, instance, parent_context):
            delete_url = reverse("adcon_station_variable_mapping_delete", args=[instance.id])
            return [
                ListingButton(
                    _("Delete"),
                    url=delete_url,
                    icon_name="bin",
                    priority=20,
                    classname="serious",
                ),
            ]
    
    columns = [
        ColumnWithButtons("station_link", label=_("Title")),
        Column("adl_parameter", label=_("ADL Variable")),
        Column("adcon_parameter_id", label=_("ADCON Parameter ID")),
    ]
    
    context = {
        "breadcrumbs_items": breadcrumbs_items,
        "all_count": all_count,
        "result_count": result_count,
        "paginator": paginator,
        "page_obj": page_obj,
        "object_list": page_obj.object_list,
        "user_can_create": permission_helper.user_can_create(user),
        "header_buttons": buttons,
        "table": Table(columns, page_obj.object_list),
    }
    
    return render(request, template_name, context)


def adcon_station_variable_mapping_create(request, station_link_id):
    template_name = "adl_adcon_db_plugin/station_variable_mapping_create.html"
    
    station_link_admin_helper = AdminURLHelper(ADCONStationLink)
    station_link_index_url = station_link_admin_helper.get_action_url("index")
    station_link = ADCONStationLink.objects.get(pk=station_link_id)
    
    breadcrumbs_items = [
        {
            
            "url": station_link_index_url,
            "label": ADCONStationLink._meta.verbose_name_plural},
        {
            "url": reverse("adcon_station_variable_mapping_list", args=[station_link_id]),
            "label": _("Station Parameter Mapping")},
        {
            "url": "",
            "label": _("Create Station Parameter Mapping")},
    ]
    
    context = {
        "breadcrumbs_items": breadcrumbs_items,
        "header_icon": "snippet",
        "page_subtitle": _("Create Station Parameter Mapping"),
        "submit_button_label": _("Create"),
        "action_url": reverse("adcon_station_variable_mapping_create", args=[station_link_id]),
    }
    
    if request.method == "POST":
        form = VariableMappingForm(request.POST, initial={"station_link": station_link.id})
        
        if form.is_valid():
            adcon_parameter_id = form.cleaned_data["adcon_parameter_id"]
            station_parameter_mapping_data = {
                "station_link": station_link,
                "adl_parameter": form.cleaned_data["adl_parameter"],
                "adcon_parameter_id": adcon_parameter_id,
                "adcon_parameter_unit": form.cleaned_data["adcon_parameter_unit"],
            }
            
            try:
                ADCONStationVariableMapping.objects.create(**station_parameter_mapping_data)
                messages.success(request, _("Station Parameter Mapping created successfully."))
                return redirect(reverse("adcon_station_variable_mapping_list", args=[station_link_id]))
            except Exception as e:
                form.add_error(None, str(e))
                context["form"] = form
                return render(request, template_name, context)
        else:
            context["form"] = form
            return render(request, template_name, context)
    else:
        form = VariableMappingForm(initial={"station_link": station_link.id})
        context["form"] = form
    
    return render(request, template_name, context)


def adcon_station_variable_mapping_delete(request, station_variable_mapping_id):
    station_variable_mapping = get_object_or_404(ADCONStationVariableMapping, pk=station_variable_mapping_id)
    
    if request.method == "POST":
        station_variable_mapping.delete()
        messages.success(request, _("Station Variable Mapping deleted successfully."))
        return redirect(reverse("adcon_station_variable_mapping_list", args=[station_variable_mapping.station_link_id]))
    
    context = {
        "page_title": gettext("Delete %(obj)s") % {"obj": station_variable_mapping},
        "header_icon": "snippet",
        "is_protected": False,
        "view": {
            "confirmation_message": gettext("Are you sure you want to delete this %(model_name)s?") % {
                "model_name": station_variable_mapping._meta.verbose_name
            },
        },
    }
    
    return render(request, "wagtailadmin/generic/confirm_delete.html", context)
