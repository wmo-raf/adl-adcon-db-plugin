from adl.core.registries import Plugin


class ADCONDBPlugin(Plugin):
    type = "adl_adcon_db_plugin"
    label = "ADL ADCON DB Plugin"
    
    def get_urls(self):
        return []
    
    def get_data(self):
        return []
