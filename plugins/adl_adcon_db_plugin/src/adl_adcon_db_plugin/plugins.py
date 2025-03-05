import logging
from datetime import timedelta

from adl.core.models import ObservationRecord
from adl.core.registries import Plugin
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)


class ADCONDBPlugin(Plugin):
    type = "adl_adcon_db_plugin"
    label = "ADL ADCON DB Plugin"
    
    db = None
    
    def get_urls(self):
        return []
    
    def get_data(self):
        network_conn_name = self.network_connection.name
        
        logger.info(f"[ADL_ADCON_DB_PLUGIN] Starting data processing for {network_conn_name}.")
        
        station_links = self.network_connection.station_links.all()
        
        logger.debug(f"[ADL_ADCON_DB_PLUGIN] Found {len(station_links)} station links for {network_conn_name}.")
        
        self.db = self.network_connection.get_db_connection()
        
        stations_records_count = {}
        
        try:
            for station_link in station_links:
                station_name = station_link.station.name
                
                if not station_link.enabled:
                    logger.warning(f"[ADL_ADCON_DB_PLUGIN] Station link {station_name} is disabled.")
                    continue
                
                logger.debug(f"[ADL_ADCON_DB_PLUGIN] Processing data for {station_name}.")
                
                station_link_records_count = self.process_station_link(station_link)
                
                stations_records_count[station_link.station.id] = station_link_records_count
        except Exception as e:
            logger.error(f"[ADL_ADCON_DB_PLUGIN] Error processing data for {network_conn_name}. {e}")
        finally:
            self.db.close()
        
        return stations_records_count
    
    def process_station_link(self, station_link):
        station_name = station_link.station.name
        
        logger.debug(f"[ADL_ADCON_DB_PLUGIN] Getting latest data for {station_name}.")
        
        station_timezone = station_link.timezone
        
        station_variable_mappings = station_link.variable_mappings.all()
        station_adcon_parameter_ids = [mapping.adcon_parameter_id for mapping in station_variable_mappings]
        
        # get the end date(current time now) in the station timezone
        end_date = dj_timezone.localtime(timezone=station_timezone)
        # set the end date to the start of the next hour
        end_date = end_date.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        
        if station_link.start_date:
            start_date = dj_timezone.localtime(station_link.start_date, station_timezone)
        else:
            # set to end_date of the previous hour
            start_date = end_date - timedelta(hours=1)
        
        start_timestamp = int(start_date.timestamp())
        end_timestamp = int(end_date.timestamp())
        
        records = self.db.get_data_for_parameters(station_adcon_parameter_ids, start_timestamp, end_timestamp,
                                                  station_timezone)
        
        observation_records = []
        
        for record in records:
            timestamp = record.get("TIMESTAMP")
            
            if not timestamp:
                logger.debug(f"[ADL_ADCON_DB_PLUGIN] No timestamp found in record {record}")
                return
            
            for variable_mapping in station_link.variable_mappings.all():
                adl_parameter = variable_mapping.adl_parameter
                adcon_parameter_id = variable_mapping.adcon_parameter_id
                adcon_parameter_unit = variable_mapping.adcon_parameter_unit
                
                value = record.get(adcon_parameter_id)
                
                if value is None:
                    logger.debug(f"[ADL_PULSOWEB_PLUGIN] No data record found for parameter {adl_parameter.name}")
                    continue
                
                if adl_parameter.unit != adcon_parameter_unit:
                    value = adl_parameter.convert_value_from_units(value, adcon_parameter_unit)
                
                record_data = {
                    "station": station_link.station,
                    "parameter": adl_parameter,
                    "time": timestamp,
                    "value": value,
                    "connection": station_link.network_connection,
                }
                
                param_obs_record = ObservationRecord(**record_data)
                observation_records.append(param_obs_record)
        
        records_count = len(observation_records)
        
        if observation_records:
            logger.debug(f"[ADL_ADCON_DB_PLUGIN] Saving {records_count} records for {station_name}.")
            ObservationRecord.objects.bulk_create(
                observation_records,
                update_conflicts=True,
                update_fields=["value"],
                unique_fields=["station", "parameter", "time", "connection"]
            )
        
        return records_count
