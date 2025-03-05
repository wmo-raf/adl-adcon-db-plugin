from datetime import datetime

import psycopg2


class ADCONDBClient:
    def __init__(self, db_host, db_port, db_name, db_user, db_password):
        self.connection = psycopg2.connect(
            host=db_host,
            port=db_port,
            password=db_password,
            dbname=db_name,
            user=db_user,
        )
    
    def close(self):
        if self.connection:
            self.connection.close()
    
    def get_stations(self, only_stations_with_coords=False):
        sql = "SELECT id, displayname,latitude,longitude,timezoneid FROM node_60 WHERE dtype ='DeviceNode'"
        
        if only_stations_with_coords:
            sql += " AND latitude IS NOT NULL AND longitude IS NOT NULL"
        
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            stations = cursor.fetchall()
        
        stations = [dict(zip([column.name for column in cursor.description], station)) for station in stations]
        
        return stations
    
    def get_adcon_parameters_for_station(self, adcon_station_id):
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT DISTINCT id, displayname,subclass
                    FROM node_60  WHERE dtype ='AnalogTagNode' and parent_id = %s""", (adcon_station_id,)
            )
            
            parameters = cursor.fetchall()
        
        parameters = [dict(zip([column.name for column in cursor.description], parameter)) for parameter in parameters]
        
        return parameters
    
    def get_data_for_parameters(self, parameter_ids, start_date, end_date, station_timezone):
        
        # tag_id is the ADCON parameter id
        # status=0 means the data is valid
        
        if not parameter_ids:
            raise ValueError("No parameter ids provided")
        
        tag_ids_placeholders = ', '.join(['%s'] * len(parameter_ids))
        query = f"""
            SELECT tag_id, enddate, startdate, measuringvalue
            FROM historiandata
            WHERE tag_id IN ({tag_ids_placeholders})
            AND startdate >= %s
            AND enddate <= %s
            AND status = 0
        """
        
        parameters = parameter_ids + [start_date, end_date]
        
        with self.connection.cursor() as conn_cursor:
            conn_cursor.execute(query, parameters)
            
            data = conn_cursor.fetchall()
            
            # organize the data by dates
            parameter_data_by_date = {}
            
            for data_point in data:
                data_point = dict(zip([column.name for column in conn_cursor.description], data_point))
                
                end_date = datetime.fromtimestamp(data_point['enddate'], tz=station_timezone)
                start_date = datetime.fromtimestamp(data_point['startdate'], tz=station_timezone)
                tag_id = data_point['tag_id']
                
                time_diff = (end_date - start_date).total_seconds() / 60
                
                # 10 and 15 minutes interval,
                # take obs with greater than 3 minutes sampling and less than 20
                if 3 <= time_diff < 20:
                    data_point["enddate"] = end_date
                    data_point["startdate"] = start_date
                    
                    if not parameter_data_by_date.get(end_date):
                        parameter_data_by_date[end_date] = {
                            "TIMESTAMP": end_date
                        }
                    
                    parameter_data_by_date[end_date][tag_id] = data_point["measuringvalue"]
        
        return list(parameter_data_by_date.values())
