from django.apps import AppConfig


class GpstrackingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gpstracking'

    def ready(self):
        import gpstracking.signals