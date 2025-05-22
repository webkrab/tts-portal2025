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

class TrackerGroupAdminForm(forms.ModelForm):
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
        try:
            tracker = obj.tracker
            group = obj.group
            type_ids = group.identifier_types.values_list('id', flat=True)
            matching_types = tracker.identifiers.filter(
                identifier_type_id__in=type_ids
            ).values_list('identifier_type__code', flat=True).distinct()

            codes = list(matching_types)
            return f"Via Identifier(s): {', '.join(codes)}" if codes else "Direct"
        except Exception:
            return "Direct"

    link_origin.short_description = "Link Origin"



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
        columns = ['id', 'screen_name', 'icon', 'meta_timestamp', 'meta_age_display_column', 'position_timestamp', 'position_age_display_column']
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
    list_display = ('tracker_identifier', 'created_at_display', 'msgtype', 'sha256_key', 'content')
    search_fields = (
        'tracker_identifier__external_id',
        'tracker_identifier__tracker__screen_name',
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
    list_display = ('identifier_type', 'msgtype')
    search_fields = ('identifier_type__code', 'msgtype')
    list_filter = ('identifier_type__code', 'msgtype')


@admin.register(TrackerDecoderField)
class TrackerDecoderFieldsAdmin(admin.ModelAdmin):
    form = TrackerDecoderFieldAdminForm
    list_display = ('name', 'dbfield')
    search_fields = ('name', 'dbfield')
