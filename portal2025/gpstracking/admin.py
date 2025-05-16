from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe
from django.utils.text import slugify
import json

from leaflet.admin import LeafletGeoAdmin

from .models import (
    Tracker,
    TrackerDecoder,
    TrackerGroup,
    TrackerIdentifier,
    TrackerIdentifierType,
    TrackerMessage,
    TrackerStName,
    default_tracker_visible_fields,
    get_tracker_field_choices,
)

admin.site.site_header = "TTS Beheer"
admin.site.site_title = "TTS Beheerportal"


# --------- FORMULIEREN --------- #

class TrackerGroupAdminForm(forms.ModelForm):
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

        for attr in ['can_add_related', 'can_change_related', 'can_view_related', 'can_delete_related']:
            setattr(self.fields['identifier_type'].widget, attr, False)

    def clean(self):
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
    class Meta:
        model = TrackerIdentifier
        fields = '__all__'

    def clean(self):
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
        ).values_list('identifier_type__code', flat=True).distinct()
        return "Via Identifier(s): " + ", ".join(matching_types) if matching_types else "Direct"


# --------- ADMIN CONFIG --------- #

@admin.register(Tracker)
class TrackerAdmin(LeafletGeoAdmin):
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

    position_age_display_column.short_description = "Position_age"

    def meta_age_display_column(self, obj):
        return obj.meta_age_display or "-"

    meta_age_display_column.short_description = "Meta_age"

    def get_list_display(self, request):
        columns = ['screen_name', 'icon']
        columns += ['position_age_display_column', 'meta_age_display_column']
        types = TrackerIdentifierType.objects.all()
        for itype in types:
            safe_slug = slugify(itype.code).replace("-", "_")
            column_name = f'identifier_{safe_slug}'
            if not hasattr(self.__class__, column_name):
                def make_func(itype):
                    def col(admin_self, obj):
                        identifiers = obj.identifiers.filter(identifier_type=itype)
                        return ", ".join(i.external_id for i in identifiers)

                    col.short_description = itype.code
                    col.admin_order_field = None
                    return col

                setattr(self.__class__, column_name, make_func(itype))
            columns.append(column_name)
        return columns


@admin.register(TrackerIdentifier)
class TrackerIdentifierAdmin(admin.ModelAdmin):
    form = TrackerIdentifierAdminForm
    list_display = ('tracker', 'identifier_type', 'external_id',)
    search_fields = ('identifier_type__code', 'external_id', 'identkey')
    list_filter = ('identifier_type__code',)
    readonly_fields = ('identkey',)


@admin.register(TrackerGroup)
class TrackerGroupAdmin(LeafletGeoAdmin):
    list_display = ('smartcode', 'name')
    form = TrackerGroupAdminForm
    search_fields = ('name', 'smartcode')
    inlines = [TrackerInline]


@admin.register(TrackerIdentifierType)
class TrackerIdentifierTypeAdmin(admin.ModelAdmin):
    form = TrackerIdentifierTypeAdminForm
    list_display = ('code', 'description')
    search_fields = ('code',)


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

    created_at_display.short_description = "Received at"
    created_at_display.admin_order_field = 'created_at'


class TrackerDecoderAdminForm(forms.ModelForm):
    class Meta:
        model = TrackerDecoder
        exclude = ['mapping']

    new_key = forms.CharField(required=False, label="Mapping Key")
    new_key_stn = forms.ChoiceField(required=False, label="Standardized name")
    new_key_dbn = forms.ChoiceField(required=False, label="Database field")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tracker_fields = [f.name for f in Tracker._meta.fields]
        stn_choices = [('', '---')] + [(stn.name, stn.name) for stn in TrackerStName.objects.all()]
        dbn_choices = [('', '---')] + [(f, f) for f in tracker_fields]

        self.fields['new_key_stn'].choices = stn_choices
        self.fields['new_key_dbn'].choices = dbn_choices

        current_mapping = self.instance.mapping or {}

        if current_mapping:
            rows = "".join(
                    f"<tr><td><b>{key}</b></td><td>{val.get('DBN')}</td><td>{val.get('STN')}</td></tr>"
                    for key, val in current_mapping.items()
            )
            html_table = f"""
                <table style='border-collapse: collapse;'>
                    <thead>
                        <tr><th style='padding:4px;border:1px solid #ccc;'>Key</th><th style='padding:4px;border:1px solid #ccc;'>DBN</th><th style='padding:4px;border:1px solid #ccc;'>STN</th></tr>
                    </thead>
                    <tbody>{rows}</tbody>
                </table>
            """
            self.fields['mapping_preview'] = forms.CharField(
                    label="Huidige Mapping",
                    required=False,
                    widget=forms.Textarea(attrs={
                            'readonly': 'readonly',
                            'style'   : 'font-family: monospace; border: none; background: transparent;'
                    }),
                    initial=mark_safe(html_table)
            )

        self._existing_keys = []
        for key in current_mapping.keys():
            self._existing_keys.append(key)
            stn_val = current_mapping[key].get('STN', '')
            dbn_val = current_mapping[key].get('DBN', '')
            self.fields[f'{key}_stn'] = forms.ChoiceField(
                    choices=stn_choices, required=False, label=f'{key} STN')
            self.fields[f'{key}_dbn'] = forms.ChoiceField(
                    choices=dbn_choices, required=False, label=f'{key} DBN')
            self.fields[f'{key}_delete'] = forms.BooleanField(
                    required=False, label=f"Verwijder {key}")
            self.fields[f'{key}_stn'].initial = stn_val
            self.fields[f'{key}_dbn'].initial = dbn_val

    def clean(self):
        cleaned_data = super().clean()
        new_mapping = {}

        for key in self._existing_keys:
            if cleaned_data.get(f'{key}_delete'):
                continue
            stn_val = cleaned_data.get(f'{key}_stn') or None
            dbn_val = cleaned_data.get(f'{key}_dbn') or None
            new_mapping[key] = {'STN': stn_val, 'DBN': dbn_val}

        new_key = cleaned_data.get('new_key', '').strip()
        new_stn = cleaned_data.get('new_key_stn') or None
        new_dbn = cleaned_data.get('new_key_dbn') or None

        if new_key:
            new_mapping[new_key] = {'STN': new_stn, 'DBN': new_dbn}

        cleaned_data['mapping'] = new_mapping
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.mapping = self.cleaned_data.get('mapping', {})
        if commit:
            instance.save()
        return instance


@admin.register(TrackerDecoder)
class TrackerDecoderAdmin(admin.ModelAdmin):
    form = TrackerDecoderAdminForm
    readonly_fields = ['display_mapping_table']
    search_fields = (
            'identifier_type__code',
            'msgtype',
    )
    list_filter = ('identifier_type__code',
                   'msgtype',)

    def display_mapping_table(self, obj):
        if not obj or not obj.mapping:
            return "Geen mapping aanwezig."
        rows = "".join(
                f"<tr><td><b>{key}</b></td><td>{val.get('DBN')}</td><td>{val.get('STN')}</td></tr>"
                for key, val in obj.mapping.items()
        )
        return mark_safe(f"""
            <table style='border-collapse: collapse;'>
                <thead>
                    <tr>
                        <th style='padding:4px;border:1px solid #ccc;'>Key</th>
                        <th style='padding:4px;border:1px solid #ccc;'>DBN</th>
                        <th style='padding:4px;border:1px solid #ccc;'>STN</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        """)

    display_mapping_table.short_description = "Huidige Mapping"

@admin.register(TrackerStName)
class TrackerStNameAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)