from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from leaflet.admin import LeafletGeoAdmin

from .models import (
    Tracker,
    TrackerGroup,
    TrackerIdentifier,
    TrackerIdentifierType,
    TrackerMessage,
    default_tracker_visible_fields,
    get_tracker_field_choices,
)


admin.site.site_header = "TTS Beheer"
admin.site.site_title = "TTS Beheerportal"


# --------- FORMULIEREN --------- #

class TrackerGroupAdminForm(forms.ModelForm):
    """
    Formulier voor het beheren van TrackerGroepen in de admin-interface.

    Toont keuzevelden voor zichtbare velden en gekoppelde identifier types.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['visible_fields'] = forms.MultipleChoiceField(
                choices=get_tracker_field_choices(),
                required=False,
                widget=forms.CheckboxSelectMultiple,
                initial=default_tracker_visible_fields()
        )

        self.fields['identifier_types'] = forms.ModelMultipleChoiceField(
                queryset=TrackerIdentifierType.objects.all(),
                required=False,
                widget=forms.CheckboxSelectMultiple
        )

    class Meta:
        model = TrackerGroup
        fields = '__all__'


class TrackerIdentifierTypeAdminForm(forms.ModelForm):
    """
    Formulier voor het beheren van Identifier Types.

    Zorgt ervoor dat gekoppelde groepen vooraf worden geladen.
    """

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.get('initial', {})
        if instance:
            initial['groups'] = instance.groups.all()
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)
        self.fields['groups'] = forms.ModelMultipleChoiceField(
                queryset=TrackerGroup.objects.all(),
                required=False,
                widget=forms.CheckboxSelectMultiple
        )

    class Meta:
        model = TrackerIdentifierType
        fields = '__all__'

    def save(self, commit=True):
        """
        Zorgt ervoor dat de many-to-many relatie correct wordt opgeslagen.
        """
        instance = super().save(commit=False)
        if commit:
            instance.save()
            instance.groups.set(self.cleaned_data['groups'])
            self.save_m2m()
        return instance


class TrackerIdentifierInlineForm(forms.ModelForm):
    """
    Inline formulier voor identifiers binnen een tracker.
    """

    class Meta:
        model = TrackerIdentifier
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['identifier_type'].disabled = True

        for attr in ['can_add_related', 'can_change_related', 'can_view_related', 'can_delete_related']:
            setattr(self.fields['identifier_type'].widget, attr, False)

    def clean(self):
        """
        Valideert dat een combinatie van identifier_type en external_id uniek is.

        Raises:
            ValidationError: Als de combinatie al bestaat in de database.
        """
        cleaned_data = super().clean()
        identifier_type = cleaned_data.get("identifier_type")
        external_id = cleaned_data.get("external_id")

        if identifier_type and external_id:
            identkey = f"{identifier_type.code}_{external_id}".upper()

            qs = TrackerIdentifier.objects.filter(identkey=identkey)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                raise ValidationError({
                        "external_id": f"De combinatie van type '{identifier_type.code}' en ID '{external_id}' bestaat al."
                })

        return cleaned_data


class TrackerIdentifierAdminForm(forms.ModelForm):
    """
    Admin formulier voor TrackerIdentifier.
    """

    class Meta:
        model = TrackerIdentifier
        fields = '__all__'

    def clean(self):
        """
        Voert dezelfde validatie uit als de inline variant.
        """
        cleaned_data = super().clean()
        identifier_type = cleaned_data.get("identifier_type")
        external_id = cleaned_data.get("external_id")

        if identifier_type and external_id:
            identkey = f"{identifier_type.code}_{external_id}".upper()

            qs = TrackerIdentifier.objects.filter(identkey=identkey)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                raise ValidationError({
                        "external_id": f"De combinatie van type '{identifier_type.code}' en ID '{external_id}' bestaat al."
                })

        return cleaned_data


# --------- INLINES --------- #

class TrackerIdentifierInline(admin.TabularInline):
    """
    Inline voor weergave van identifiers binnen een tracker.
    """
    model = TrackerIdentifier
    form = TrackerIdentifierInlineForm
    extra = 1
    readonly_fields = ('identkey', 'linked_groups')

    def linked_groups(self, obj):
        """
        Toont automatisch gekoppelde groepen van een identifier.
        """
        if not obj.pk:
            return "-"
        return ", ".join(g.name for g in obj.identifier_type.groups.all())

    linked_groups.short_description = "Automatisch gekoppelde groepen"


class TrackerInline(admin.TabularInline):
    """
    Inline voor weergave van tracker-groep relaties.
    """
    model = Tracker.groups.through
    extra = 1
    fields = ('tracker', 'link_origin')
    readonly_fields = ('link_origin',)

    def link_origin(self, obj):
        """
        Berekent hoe de link tot stand is gekomen (direct of via identifier).
        """
        tracker = obj.tracker
        group = obj.trackergroup
        type_ids = group.identifier_types.values_list('id', flat=True)
        matching_types = tracker.identifiers.filter(
                identifier_type_id__in=type_ids
        ).values_list('identifier_type__name', flat=True).distinct()
        return "Via Identifier(s): " + ", ".join(matching_types) if matching_types else "Direct"


# --------- ADMIN CONFIG --------- #

@admin.register(Tracker)
class TrackerAdmin(LeafletGeoAdmin):
    """
    Admin configuratie voor het Tracker-model.
    Dynamisch worden kolommen aangemaakt op basis van de aanwezige identifier types.
    """

    search_fields = (
            'screen_name',
            'ais_name',
            'adsb_registration',
            'identifiers__identkey',
    )
    list_filter = ('identifiers__identifier_type', 'groups')
    filter_horizontal = ('groups',)
    inlines = [TrackerIdentifierInline]
    readonly_fields = ('inferred_group_list', 'position_timestamp_display')

    def inferred_group_list(self, obj):
        """
        Toont groepen waaraan deze tracker impliciet is gekoppeld via identifier types.
        """
        groups = TrackerGroup.objects.filter(
                identifier_types__in=obj.identifiers.values_list('identifier_type', flat=True)
        ).distinct()
        return ", ".join(g.name for g in groups)

    inferred_group_list.short_description = "Indirect Linked Groups"

    def position_timestamp_display(self, obj):
        return obj.position_timestamp_display

    position_timestamp_display.short_description = "Position_time"
    position_timestamp_display.admin_order_field = 'position_timestamp'

    def position_age_display_column(self, obj):
        return obj.position_age_display or "-"

    position_age_display_column.short_description = "Positie Leeftijd"

    def meta_age_display_column(self, obj):
        return obj.meta_age_display or "-"

    meta_age_display_column.short_description = "Meta Leeftijd"

    def get_list_display(self, request):
        """
        Bepaalt dynamisch de kolommen in de lijstweergave van de admin voor Trackers.

        Returns:
            list[str]: Een lijst met kolomnamen, inclusief dynamische identifier types.
        """
        columns = ['screen_name', 'icon']
        columns += ['position_age_display_column', 'meta_age_display_column']

        types = TrackerIdentifierType.objects.all()
        for itype in types:
            safe_slug = slugify(itype.name).replace("-", "_")
            column_name = f'identifier_{safe_slug}'

            if not hasattr(self.__class__, column_name):
                def make_func(itype):
                    def col(admin_self, obj):  # ← let op: 2 argumenten
                        identifiers = obj.identifiers.filter(identifier_type=itype)
                        return ", ".join(i.external_id for i in identifiers)

                    col.short_description = itype.name
                    col.admin_order_field = None
                    return col

                setattr(self.__class__, column_name, make_func(itype))

            columns.append(column_name)

        return columns


@admin.register(TrackerIdentifier)
class TrackerIdentifierAdmin(admin.ModelAdmin):
    """
    Admin configuratie voor TrackerIdentifiers.
    """
    form = TrackerIdentifierAdminForm
    list_display = ('tracker', 'identifier_type', 'external_id',)
    search_fields = ('identifier_type__name', 'external_id', 'identkey')
    list_filter = ('identifier_type__name',)
    readonly_fields = ('identkey',)


@admin.register(TrackerGroup)
class TrackerGroupAdmin(LeafletGeoAdmin):
    """
    Admin configuratie voor TrackerGroups.
    """
    list_display = ('smartcode', 'name')
    form = TrackerGroupAdminForm
    search_fields = ('name', 'smartcode')
    inlines = [TrackerInline]


@admin.register(TrackerIdentifierType)
class TrackerIdentifierTypeAdmin(admin.ModelAdmin):
    """
    Admin configuratie voor Identifier Types.
    """
    form = TrackerIdentifierTypeAdminForm
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(TrackerMessage)
class TrackerMessageAdmin(LeafletGeoAdmin):
    """
    Admin configuratie voor TrackerMessages.
    """
    list_display = ('tracker_identifier', 'created_at_display', 'msgtype', 'sha256_key')
    search_fields = (
            'tracker_identifier__external_id',
            'tracker_identifier__tracker__screen_name',
    )
    list_filter = ('tracker_identifier', 'msgtype')
    readonly_fields = ('sha256_key', 'created_at_display')

    def created_at_display(self, obj):
        return obj.created_at_display

    created_at_display.short_description = "Ontvangen om"
    created_at_display.admin_order_field = 'created_at'
