from django.contrib import admin
from django import forms
from django.forms import Select
from leaflet.admin import LeafletGeoAdmin
from django.utils.text import slugify

from .models import *

# Helemaal onderaan je admin.py
admin.site.site_header = "TTS Beheer"
admin.site.site_title = "TTS Beheerportal"


# --------- FORMULIEREN --------- #

class TrackerGroupAdminForm(forms.ModelForm):
    visible_fields = forms.MultipleChoiceField(
            choices=get_tracker_field_choices(),
            required=False,
            widget=forms.CheckboxSelectMultiple
    )
    identifier_types = forms.ModelMultipleChoiceField(
            queryset=TrackerIdentifierType.objects.all(),
            required=False,
            widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = TrackerGroup
        fields = '__all__'


class TrackerIdentifierTypeAdminForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
            queryset=TrackerGroup.objects.all(),
            required=False,
            widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = TrackerIdentifierType
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.get('initial', {})
        if instance:
            initial['groups'] = instance.groups.all()
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            instance.groups.set(self.cleaned_data['groups'])
            self.save_m2m()
        return instance


class TrackerIdentifierInlineForm(forms.ModelForm):
    class Meta:
        model = TrackerIdentifier
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields['identifier_type'].disabled = True

        self.fields['identifier_type'].widget.can_add_related = False
        self.fields['identifier_type'].widget.can_change_related = False
        self.fields['identifier_type'].widget.can_view_related = False
        self.fields['identifier_type'].widget.can_delete_related = False


# --------- INLINES --------- #

class TrackerIdentifierInline(admin.TabularInline):
    model = TrackerIdentifier
    form = TrackerIdentifierInlineForm
    extra = 1
    readonly_fields = ('identkey', 'linked_groups')

    def linked_groups(self, obj):
        if not obj.pk:
            return "-"
        return ", ".join(g.name for g in obj.identifier_type.groups.all())

    linked_groups.short_description = "Automatisch gekoppelde groepen"


class TrackerInline(admin.TabularInline):
    model = Tracker.groups.through
    extra = 1
    fields = ('tracker', 'link_origin')
    readonly_fields = ('link_origin',)

    def link_origin(self, obj):
        tracker = obj.tracker
        group = obj.trackergroup
        type_ids = group.identifier_types.values_list('id', flat=True)

        matching_types = tracker.identifiers.filter(
                identifier_type_id__in=type_ids
        ).values_list('identifier_type__name', flat=True).distinct()

        if matching_types:
            return "Via Identifier(s):" + ", ".join(matching_types)

        return "Direct"


# --------- DYNAMISCHE IDENTIFIER-KOLOMMEN --------- #

identifier_column_names = []
types = TrackerIdentifierType.objects.all()


def make_identifier_column(itype):
    def col_func(self, obj):
        identifiers = obj.identifiers.filter(identifier_type=itype)
        return ", ".join(i.external_id for i in identifiers)

    col_func.short_description = itype.name
    col_func.__name__ = f'identifier_{slugify(itype.name)}'
    return col_func


for identifier_type in types:
    func = make_identifier_column(identifier_type)
    setattr(admin.ModelAdmin, func.__name__, func)
    identifier_column_names.append(func.__name__)


# --------- ADMIN REGISTRATIE --------- #

@admin.register(Tracker)
class TrackerAdmin(LeafletGeoAdmin):
    list_display = ['screen_name', 'icon'] + identifier_column_names
    search_fields = (
        'screen_name',
        'ais_name',
        'adsb_registration',
        'identifiers__external_id',
        'identifiers__identifier_type',
    )
    list_filter = ('identifiers__identifier_type', 'groups')
    filter_horizontal = ('groups',)  # ✅ HIER opnieuw toegevoegd
    inlines = [TrackerIdentifierInline]
    readonly_fields = ('inferred_group_list', 'position_timestamp_display')

    def inferred_group_list(self, obj):
        groups = TrackerGroup.objects.filter(
            identifier_types__in=obj.identifiers.values_list('identifier_type', flat=True)
        ).distinct()
        return ", ".join(g.name for g in groups)

    inferred_group_list.short_description = "Indirect Linked Groups"

    def position_timestamp_display(self, obj):
        return obj.position_timestamp_display

    position_timestamp_display.short_description = "Position_time"
    position_timestamp_display.admin_order_field = 'position_timestamp'




@admin.register(TrackerIdentifier)
class TrackerIdentifierAdmin(admin.ModelAdmin):
    list_display = ( 'tracker', 'identifier_type__name', 'external_id',)
    search_fields = ('identifier_type__name', 'external_id', 'identkey')
    list_filter = ('identifier_type__name',)
    readonly_fields = ('identkey',)


@admin.register(TrackerGroup)
class TrackerGroupAdmin(LeafletGeoAdmin):
    form = TrackerGroupAdminForm
    search_fields = ('name',)
    inlines = [TrackerInline]


@admin.register(TrackerIdentifierType)
class TrackerIdentifierTypeAdmin(admin.ModelAdmin):
    form = TrackerIdentifierTypeAdminForm
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(TrackerMessage)
class TrackerMessageAdmin(LeafletGeoAdmin):
    list_display = ('tracker_identifier', 'created_at_display', 'msgtype', 'sha256_key')
    search_fields = (
        'tracker_identifier__external_id',
        'tracker_identifier__tracker__screen_name',
    )
    list_filter = ('tracker_identifier', 'msgtype')
    readonly_fields = ('sha256_key', 'created_at_display')

    def created_at_display(self, obj):
        return obj.created_at_display

    created_at_display.short_description = "Ontvangen"
    created_at_display.admin_order_field = 'created_at'
