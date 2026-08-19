import logging

from adl.core.registries import Plugin

logger = logging.getLogger(__name__)


class ADCONDBPlugin(Plugin):
    type = "adl_adcon_db_plugin"
    label = "ADL ADCON DB Plugin"

    def get_urls(self):
        return []

    def get_station_data(self, station_link, start_date=None, end_date=None):
        network_connection = station_link.network_connection
        network_conn_name = network_connection.name

        logger.info(f"[ADL_ADCON_DB_PLUGIN] Starting data processing for {network_conn_name}.")

        db = network_connection.get_db_connection()

        try:
            station_name = station_link.station.name

            logger.debug(f"[ADL_ADCON_DB_PLUGIN] Getting latest data for {station_name}.")

            station_timezone = station_link.timezone

            station_variable_mappings = station_link.get_variable_mappings()
            station_adcon_parameter_ids = [mapping.adcon_parameter_id for mapping in station_variable_mappings]

            start_timestamp = int(start_date.timestamp())
            end_timestamp = int(end_date.timestamp())

            records, sources_count = db.get_data_for_parameters(
                station_adcon_parameter_ids, start_timestamp, end_timestamp, station_timezone)

            # Duck-typed sources-count handover: core stores this on the run's
            # activity log so "looked, found nothing" (0) stays distinguishable
            # from "never looked" (None). Committed only here, once the fetch
            # has returned — a query that raised leaves the attribute None, and
            # core's evidence rule abstains on NULL rather than blaming the
            # source for a run that never got an answer. The accumulate idiom is
            # used even though this plugin makes one query: there is no
            # straight-assignment variant.
            if getattr(station_link, "adl_sources_count", None) is None:
                station_link.adl_sources_count = 0
            station_link.adl_sources_count += sources_count

            return records

        except Exception as e:
            logger.error(f"[ADL_ADCON_DB_PLUGIN] Error processing data for {network_conn_name}. {e}")
            raise e
        finally:
            db.close()
