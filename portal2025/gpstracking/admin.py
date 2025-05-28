from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.db import models
from django.db.models import Field
from django.contrib.gis.geos import MultiPolygon, Polygon, Point
from leaflet.admin import LeafletGeoAdmin

from .models import (
    Tracker,
    TrackerDecoder,
    TrackerGroup,
    TrackerIdentifier,
    TrackerIdentifierType,
    TrackerMessage,
    TrackerDecoderField,
    default_tracker_visible_fields,
    get_tracker_field_choices,
)

admin.site.site_header = "TTS Beheer"
admin.site.site_title = "TTS Beheerportal"


# --------- CUSTOM WIDGET --------- #

class MappingDropdownWidget(forms.Widget):
    def format_value(self, value):
        import json
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        if not isinstance(value, dict):
            return {}
        return value

    def render(self, name, value, attrs=None, renderer=None):
        value = self.format_value(value)

        choices = list(TrackerDecoderField.objects.values_list('name', flat=True))
        choices_html = lambda selected: ''.join(
                [f'<option value="" {"selected" if selected in ("", None) else ""}>---</option>'] +
                [f'<option value="{c}" {"selected" if c == selected else ""}>{c}</option>' for c in choices]
        )

        html = '<table><tr><th>Sleutel</th><th>Waarde</th></tr>'

        for k, v in sorted(value.items()):
            html += f'''
            <tr>
                <td><input type="text" name="{name}_key" value="{k}" /></td>
                <td><select name="{name}_value">{choices_html(v)}</select></td>
            </tr>
            '''

        # Extra lege rij
        html += f'''
        <tr>
            <td><input type="text" name="{name}_key" /></td>
            <td><select name="{name}_value">{choices_html("")}</select></td>
        </tr>
        </table>
        '''

        return mark_safe(html)

    def value_from_datadict(self, data, files, name):
        keys = data.getlist(f'{name}_key')
        values = data.getlist(f'{name}_value')
        result = {k: (v if v else None) for k, v in zip(keys, values) if k}
        return result or {}


# --------- FORMULIEREN --------- #

from django.contrib.gis.geos import GEOSGeometry


class TrackerGroupAdminForm(forms.ModelForm):
    geojson_upload = forms.FileField(
            label="GeoJSON upload (optioneel)",
            required=False,
            help_text="Upload een GeoJSON-bestand om het gebied automatisch in te stellen."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _, all_fields = get_tracker_field_choices()
        self.fields['visible_fields'] = forms.MultipleChoiceField(
                choices=all_fields,
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

    def clean(self):
        cleaned_data = super().clean()
        geojson_file = cleaned_data.get("geojson_upload")
        if geojson_file:
            try:
                geojson_str = geojson_file.read().decode("utf-8")
                geom = GEOSGeometry(geojson_str, srid=4326)
                cleaned_data["area"] = geom
            except Exception as e:
                raise ValidationError({"geojson_upload": f"Kon GeoJSON niet verwerken: {e}"})
        return cleaned_data


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


class TrackerDecoderAdminForm(forms.ModelForm):
    mapping = forms.JSONField(widget=MappingDropdownWidget(), required=False)

    class Meta:
        model = TrackerDecoder
        fields = '__all__'


class TrackerDecoderFieldAdminForm(forms.ModelForm):
    class Meta:
        model = TrackerDecoderField
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        model_fields, _ = get_tracker_field_choices()
        self.fields['dbfield'] = forms.ChoiceField(
                choices=[('', '---')] + model_fields,
                required=False,
                label=self.fields['dbfield'].label,
                help_text=self.fields['dbfield'].help_text
        )


# --------- INLINES --------- #

class TrackerIdentifierInline(admin.TabularInline):
    model = TrackerIdentifier
    form = TrackerIdentifierInlineForm
    extra = 1
    readonly_fields = ('identkey', 'linked_groups', 'latest_message_timestamp', 'latest_message_age_in_sec')
    fields = ('identifier_type', 'external_id', 'identkey', 'linked_groups', 'latest_message_timestamp', 'latest_message_age_in_sec')

    def linked_groups(self, obj):
        if not obj.pk:
            return "-"
        return ", ".join(g.name for g in obj.identifier_type.groups.all())

    linked_groups.short_description = "Automatisch gekoppelde groepen"

    def latest_message_timestamp(self, obj):
        message = obj.messages.order_by('-message_timestamp').first()
        return message.message_timestamp_display if message else "-"

    latest_message_timestamp.short_description = "Laatst seen"

    def latest_message_age_in_sec(self, obj):
        message = obj.messages.order_by('-message_timestamp').first()
        if not message or not message.age_in_sec:
            return "-"
        total_seconds = message.age_in_sec // 1000
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        if seconds or not parts: parts.append(f"{seconds}s")
        return ' '.join(parts)

    latest_message_age_in_sec.short_description = "Last seen age"


class TrackerInline(admin.TabularInline):
    model = Tracker.groups.through
    extra = 1
    fields = ('tracker', 'link_origin')
    readonly_fields = ('link_origin',)
    verbose_name = "Tracker"
    verbose_name_plural = "Tracker-TrackerGroup Relationships"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.group_instance = obj  # 👈 geef de parent group door
        return formset

    def link_origin(self, obj):
        try:
            if not obj.tracker_id:
                return "Nog niet opgeslagen"
            tracker = Tracker.objects.get(pk=obj.tracker_id)

            # Gebruik group via formset, als obj.group_id niet bestaat
            group = getattr(obj, 'group', None)
            if group is None and hasattr(obj, '_tracker_group_admin_group'):
                group = obj._tracker_group_admin_group
            elif hasattr(self, 'formset') and hasattr(self.formset, 'group_instance'):
                group = self.formset.group_instance

            if group is None:
                return "(groep ontbreekt)"

            type_codes = group.identifier_types.values_list('code', flat=True)
            matching_types = tracker.identifiers.filter(
                    identifier_type__code__in=type_codes
            ).values_list('identifier_type__code', flat=True).distinct()

            codes = list(matching_types)
            return f"Via Identifier(s): {', '.join(codes)}" if codes else "Direct"
        except Exception as e:
            return f"(fout: {e})"

    link_origin.short_description = "Link Origin"


# --------- ADMIN CONFIG --------- #

@admin.register(Tracker)
class TrackerAdmin(LeafletGeoAdmin):
    search_fields = (
            'custom_name',
            'ais_name',
            'adsb_registration',
            'identifiers__identkey',
    )
    list_filter = ('identifiers__identifier_type', 'groups')
    filter_horizontal = ('groups',)
    inlines = [TrackerIdentifierInline]
    readonly_fields = ('inferred_group_list', 'meta_timestamp_display', 'position_timestamp_display')

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

    def meta_timestamp_display(self, obj):
        return obj.meta_timestamp_display

    meta_timestamp_display.short_description = "Meta_time"
    meta_timestamp_display.admin_order_field = 'meta_timestamp'

    def meta_age_display_column(self, obj):
        return obj.meta_age_display or "-"

    meta_age_display_column.short_description = "Meta_age"

    def get_list_display(self, request):
        columns = ['id', 'display_name', 'icon', 'meta_timestamp_display', 'meta_age_display_column', 'position_timestamp_display', 'position_age_display_column']
        types = TrackerIdentifierType.objects.all().order_by("code")
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
    list_display = (
            'identkey',
            'tracker',
            'identifier_type',
            'external_id',
            'latest_message_timestamp',
            'latest_message_age_in_sec',
    )
    search_fields = (
            'identifier_type__code',
            'external_id',
            'identkey',
            'tracker__custom_name',
    )
    list_filter = ('identifier_type__code',)
    readonly_fields = ('identkey',)

    def latest_message_timestamp(self, obj):
        """
        Laat de timestamp van het laatste bericht zien.
        """
        message = obj.messages.order_by('-message_timestamp').first()
        return message.message_timestamp_display if message else "-"

    latest_message_timestamp.short_description = "Last seen"

    def latest_message_age_in_sec(self, obj):
        """
        Laat de leeftijd in ms zien van het laatste bericht.
        """
        message = obj.messages.order_by('-message_timestamp').first()
        if not message or not message.age_in_sec:
            return "-"
        total_seconds = message.age_in_sec // 1000
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        if seconds or not parts: parts.append(f"{seconds}s")
        return ' '.join(parts)

    latest_message_age_in_sec.short_description = "Last seen age"


@admin.register(TrackerGroup)
class TrackerGroupAdmin(LeafletGeoAdmin):
    list_display = ('smartcode', 'name', 'tracker_count')
    form = TrackerGroupAdminForm
    search_fields = ('name', 'smartcode')
    inlines = [TrackerInline]

    fieldsets = (
            (None, {
                    'fields': ('smartcode', 'name')
            }),
            ('GEOGRAPHIC AREA', {
                    'fields'     : ('geojson_upload', 'area'),
                    'description': (
                            "Trackers in deze groep worden alleen weergegeven wanneer ze zich binnen het geselecteerde gebied bevinden.<br>"
                            "Laat dit veld leeg om geen weergavebeperking toe te passen.<br>"
                            "Je kunt een GeoJSON-bestand, geometry-type (multi-)polygoon, uploaden of het gebied handmatig op de kaart intekenen."
                    )
            }),

            ('FIELDS IN DB-VIEW', {
                    'fields': ('visible_fields',),
                    'description': 'Alleen de geselecteerde velden worden meegegeven in de view'
            }),
            ('TRACKER GROUPS', {
                    'fields'     : ('identifier_types',),
                    'description': 'Een tracker met het geselecteerde type wordt automatisch toegevoegd aan deze groep.'
            })
    )

    def tracker_count(self, obj):
        return obj.trackers.count()

    tracker_count.short_description = "Trackers in group"

    def get_formsets_with_inlines(self, request, obj=None):
        for inline in self.get_inline_instances(request, obj):
            if isinstance(inline, TrackerInline):
                formset = inline.get_formset(request, obj)
                # 👇 sla de groep op in elke form-instance (voor link_origin)
                for form in formset.form.base_fields.values():
                    form._tracker_group_admin_group = obj
                inline.formset = formset
                yield formset, inline
            else:
                yield inline.get_formset(request, obj), inline


@admin.register(TrackerIdentifierType)
class TrackerIdentifierTypeAdmin(admin.ModelAdmin):
    form = TrackerIdentifierTypeAdminForm
    list_display = ('code', 'description')
    search_fields = ('code',)


@admin.register(TrackerMessage)
class TrackerMessageAdmin(LeafletGeoAdmin):
    list_display = ('sha256_key', 'tracker_identifier', 'created_at_display', 'msgtype', 'content')
    search_fields = (
            'tracker_identifier__external_id',
            'tracker_identifier__tracker__custom_name',
    )
    list_filter = ('msgtype', 'tracker_identifier__identifier_type__code')
    readonly_fields = ('sha256_key', 'message_timestamp_display')

    def created_at_display(self, obj):
        return obj.message_timestamp_display

    created_at_display.short_description = "Received at"
    created_at_display.admin_order_field = 'message_timestamp'


@admin.register(TrackerDecoder)
class TrackerDecoderAdmin(admin.ModelAdmin):
    form = TrackerDecoderAdminForm
    list_display = ('msgtype', 'identifier_type')
    search_fields = ('identifier_type__code', 'msgtype')
    list_filter = ('identifier_type__code', 'msgtype')


@admin.register(TrackerDecoderField)
class TrackerDecoderFieldsAdmin(admin.ModelAdmin):
    form = TrackerDecoderFieldAdminForm
    list_display = ('name', 'dbfield')
    search_fields = ('name', 'dbfield')
