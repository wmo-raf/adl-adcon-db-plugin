def get_station_parameters(network_conn, adcon_station_id):
    db = network_conn.get_db_connection()
    
    parameters = db.get_adcon_parameters_for_station(adcon_station_id)
    parameter_options = [{"label": parameter["displayname"], "value": parameter["id"], } for parameter in parameters]
    db.close()
    
    return parameter_options