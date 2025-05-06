from django.contrib import admin
from django import forms
from leaflet.admin import LeafletGeoAdmin
from .models import (
    Tracker,
    TrackerIdentifier,
    TrackerGroup,
    TrackerIdentifierType,
    Message,
    get_tracker_field_choices
)

# === Dynamische veldkeuze ===
class TrackerGroupAdminForm(forms.ModelForm):
    visible_fields = forms.MultipleChoiceField(
        choices=get_tracker_field_choices(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Zichtbare Tracker-velden"
    )

    class Meta:
        model = TrackerGroup
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.get('initial', {})
        if instance and instance.visible_fields:
            initial['visible_fields'] = instance.visible_fields
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)

    def clean_visible_fields(self):
        return self.cleaned_data['visible_fields']

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.visible_fields = self.cleaned_data['visible_fields']
        if commit:
            instance.save()
        return instance

# === JS met meerdere lagen toevoegen ===
class LeafletWithLayersMixin:
    class Media:
        js = ('js/leaflet_config.js',)

# === TrackerIdentifier inline ===
class TrackerIdentifierInline(admin.TabularInline):
    model = TrackerIdentifier
    extra = 1
    fields = ('external_id', 'identifier_type', 'identkey')
    readonly_fields = ('identkey',)

# === Tracker Admin ===
@admin.register(Tracker)
class TrackerAdmin(LeafletWithLayersMixin, LeafletGeoAdmin):
    list_display = ('screen_name', 'icon', 'id', 'latitude', 'longitude', 'altitude')
    search_fields = ('screen_name', 'ais_name', 'adsb_name')
    list_filter = ('groups',)
    inlines = [TrackerIdentifierInline]

# === TrackerIdentifier Admin ===
@admin.register(TrackerIdentifier)
class TrackerIdentifierAdmin(admin.ModelAdmin):
    list_display = ('identifier_type', 'external_id', 'identkey', 'tracker')
    search_fields = ('identifier_type__name', 'external_id', 'identkey')
    readonly_fields = ('identkey',)

# === TrackerGroup Admin ===
@admin.register(TrackerGroup)
class TrackerGroupAdmin(LeafletWithLayersMixin, LeafletGeoAdmin):
    form = TrackerGroupAdminForm
    search_fields = ('name',)

# === TrackerIdentifierType Admin ===
@admin.register(TrackerIdentifierType)
class TrackerIdentifierTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'is_active')
    search_fields = ('name',)
    list_filter = ('is_active',)

# === Message Admin ===
@admin.register(Message)
class MessageAdmin(LeafletWithLayersMixin, LeafletGeoAdmin):
    list_display = ('tracker_identifier', 'created_at', 'sha256_key')
    search_fields = (
        'tracker_identifier__external_id',
        'tracker_identifier__identifier_type__name',
    )
    list_filter = ('created_at',)
    readonly_fields = ('sha256_key',)
