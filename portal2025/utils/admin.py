# admin.py

from django.contrib import admin
from .models import City

@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Land', {
            'fields': ('landcode',),
        }),
        ('State', {
            'fields': ('state_code', 'state'),
        }),
        ('Municipality', {
            'fields': (
                'municipality_code',
                'municipality',
                'municipality_1',
                'municipality_abv',
            ),
        }),
        ('WPS', {
            'fields': ('wps_code', 'wps_naam', 'wps_abv'),
        }),
        ('Veiligheidsregio', {
            'fields': (
                'veiligheidsregio_code',
                'veiligheidsregio',
                'veiligheidsregio_num',
                'mk_naam',
                'mk_plaats')
        }),
    )
    list_display = ('shortname', 'wps_naam', 'municipality', 'state', 'landcode')
    search_fields = ('wps_naam', 'wps_abv', 'municipality')
    ordering = ('landcode', 'wps_naam')
