from django.apps import AppConfig

from adl.core.registries import plugin_registry


class ADCONDBConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "adl_adcon_db_plugin"
    
    def ready(self):
        from .plugins import ADCONDBPlugin
        
        plugin_registry.register(ADCONDBPlugin())
