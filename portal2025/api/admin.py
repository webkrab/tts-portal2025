from django.contrib import admin
from .models import Gateway

from leaflet.admin import LeafletGeoAdmin

@admin.register(Gateway)
class GatewayAdmin(LeafletGeoAdmin):
    """
    Beheerweergave voor Gateway-objecten in de Django admin.
    """

    list_display = (
        'name',
        'datatype',
        'host',
        'port',
        'database',
        'table',
    )

    list_filter = (
        'datatype',
    )

    search_fields = (
        'name',
        'datatype',
        'host',
        'port',
        'database',
        'table',
    )

    ordering = ('name',)

    fieldsets = (
        ('Gateway informatie', {
            'fields': ('uuid', 'name', 'position')
        }),
        ('Data vewerking', {
                'fields': ('datatype', 'identifier_prefix')
        }),
        ('Connectie-instellingen', {
                'fields': ('host', 'port', 'database', 'table', 'interval')
        }),
        ('Authenticatie', {
                'fields' : ('auth_user', 'auth_pass', 'auth_key'),
        }),
        ('Overige', {
            'fields': ('remarks','externalurl'),
        }),
    )

    readonly_fields = ('uuid',)
