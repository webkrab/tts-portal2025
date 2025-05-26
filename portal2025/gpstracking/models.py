
import time
import uuid
from datetime import datetime, timedelta, timezone

from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import UniqueConstraint
from django.utils.functional import lazy
from django.utils.translation import gettext_lazy as _


def get_alarm_choises():
    ALARM_CHOICES = [
            ('general', 'Alarm'),
            ('sos', 'SOS'),
            ('vibration', 'Trilling'),
            ('movement', 'Beweging'),
            ('lowspeed', 'Lage snelheid'),
            ('overspeed', 'Snelheidsoverschreiding'),
            ('fallDown', 'Gevallen'),
            ('lowPower', 'Lage stroom'),
            ('lowBattery', 'Laag accuniveau'),
            ('fault', 'Fout'),
            ('powerOff', 'Uitgeschakeld'),
            ('powerOn', 'Ingeschakeld'),
            ('door', 'Deur'),
            ('lock', 'Afgesloten'),
            ('unlock', 'Geopend'),
            ('geofence', 'Geofence'),
            ('geofenceEnter', 'Geofence binnengegaan'),
            ('geofenceExit', 'Geofence verlaten'),
            ('gpsAntennaCut', 'GPS antenne verbroken'),
            ('accident', 'Ongeluk'),
            ('tow', 'Gesleept'),
            ('idle', 'Rust'),
            ('highRpm', 'Hoge RPM'),
            ('hardAcceleration', 'Harde versnelling'),
            ('hardBraking', 'Harde remming'),
            ('hardCornering', 'Scherpe bocht'),
            ('laneChange', 'Rijbaanwisseling'),
            ('fatigueDriving', 'Vermoeid rijden'),
            ('powerCut', 'Stroomonderbreking'),
            ('powerRestored', 'Stroom hersteld'),
            ('jamming', 'Signaal verstoring'),
            ('temperature', 'Temperatuur'),
            ('parking', 'Parkeren'),
            ('bonnet', 'Motorkap'),
            ('footBrake', 'Voetrem'),
            ('fuelLeak', 'Brandstoflek'),
            ('tampering', 'Knoeien'),
            ('removing', 'Verwijderd'),
    ]


def default_tracker_area():
    """
    Geeft een standaardgebied (grofweg Nederland) terug als MultiPolygon.

    Returns:
        MultiPolygon: Standaard geografisch gebied.
    """
    min_lat = 50.475
    max_lat = 53.825
    min_lon = 2.850
    max_lon = 7.550

    polygon = Polygon((
            (min_lon, min_lat),
            (max_lon, min_lat),
            (max_lon, max_lat),
            (min_lon, max_lat),
            (min_lon, min_lat),
    ))
    return MultiPolygon(polygon, srid=4326)


def get_tracker_field_choices():
    """
    Geeft een tuple van:
    - model_fields: Alleen concrete velden uit het Tracker-model
    - all_fields: model_fields + extra virtuele velden

    Returns:
        Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]
    """
    extra_fields = ["age_in_sec", "age_human", "ais_dimensions", "display_name"]
    model_fields = [
            (field.name, field.name)
            for field in Tracker._meta.get_fields()
            if isinstance(field, models.Field) and field.concrete and not field.auto_created
    ]
    all_fields = model_fields + [(field, field) for field in extra_fields]
    return model_fields, all_fields


def default_tracker_visible_fields():
    """
    Standaard zichtbare velden voor een tracker.

    Returns:
        List[str]: Lijst met veldnamen.
    """
    return [
            "id", "custom_name", "icon",
            "altitude", "speed", "heading",
            "position_timestamp", "position",
            "age_in_sec", "age_human"
    ]


class TrackerIdentifierType(models.Model):
    """
    Type identificatie (bijv. MMSI, ICAO) dat gekoppeld kan worden aan een tracker.
    """
    code = models.CharField(
            max_length=10,
            primary_key=True,
            validators=[
                    RegexValidator(
                            r'^[A-Z0-9_]+$',
                            'Alleen hoofdletters (A-Z), cijfers (0-9) en underscores (_) zijn toegestaan.'
                    )
            ]
    )
    description = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f"{self.code} | {self.description}"


class TrackerGroup(models.Model):
    """
    Groepering van trackers, eventueel met afgebakend gebied en zichtbare velden.
    """
    smartcode = models.CharField(
            max_length=25,
            db_index=True,
            unique=True,
            help_text="Suffix voor DB-view, na opslaan is deze code niet wijzigbaar. Viewnaam:  v_tracker_group_[smartcode]",
            validators=[
                    RegexValidator(
                            r'^[a-z0-9_]+$',
                            'Alleen kleine letters (a-z), cijfers (0-9) en underscores (_) zijn toegestaan.'
                    )]
    )
    name = models.CharField(max_length=255, unique=True)
    area = gis_models.MultiPolygonField(
            help_text="Laat leeg indien wereldwijde dekking gewenst is.",
            geography=True,
            blank=True,
            null=True,
            srid=4326,
            default=default_tracker_area
    )
    visible_fields = models.JSONField(default=default_tracker_visible_fields, blank=True)

    identifier_types = models.ManyToManyField(
            TrackerIdentifierType,
            blank=True,
            related_name='groups'
    )

    class Meta:
        ordering = ['smartcode']

    def clean(self):
        """
        Voorkomt dat de smartcode achteraf gewijzigd wordt.
        """
        if self.pk:
            old = TrackerGroup.objects.filter(pk=self.pk).first()
            if old and self.smartcode != old.smartcode:
                raise ValidationError({'smartcode': _(f'Smartcode "{old.smartcode}" mag niet worden aangepast na creatie.')})

    def __str__(self):
        return f'{self.smartcode} | {self.name}'


class Tracker(models.Model):
    """
    Een volgobject (tracker) met optionele AIS/ADSB eigenschappen en geografische positie.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    custom_name = models.CharField(max_length=255, blank=True, null=True, db_index=True, )
    icon = models.CharField(max_length=255, blank=True, null=True)
    alarm_type = models.CharField(max_length=32,choices=get_alarm_choises(), verbose_name='Alarmtype')

    ais_type = models.CharField(max_length=255, blank=True, null=True)
    ais_name = models.CharField(max_length=255, blank=True, null=True)
    ais_callsign = models.CharField(max_length=255, blank=True, null=True)
    ais_length = models.DecimalField(
            max_digits=5, decimal_places=2,
            validators=[MinValueValidator(0), MaxValueValidator(500)],
            default=0, blank=True, null=True
    )
    ais_width = models.DecimalField(
            max_digits=5, decimal_places=2,
            validators=[MinValueValidator(0), MaxValueValidator(500)],
            default=0, blank=True, null=True
    )

    adsb_type = models.CharField(max_length=255, blank=True, null=True)
    adsb_registration = models.CharField(max_length=255, blank=True, null=True)
    adsb_callsign = models.CharField(max_length=255, blank=True, null=True)
    meta_timestamp = models.BigIntegerField(blank=True, null=True,db_index=True,  help_text="UNIX tijd in ms")

    altitude = models.FloatField(blank=True, null=True)
    speed = models.FloatField(blank=True, null=True)
    course = models.FloatField(blank=True, null=True)
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    position_timestamp = models.BigIntegerField(blank=True, null=True, db_index=True, help_text="UNIX tijd in ms")

    groups = models.ManyToManyField(TrackerGroup, related_name='trackers', blank=True)

    class Meta:
        ordering = ['custom_name', 'id']

    @property
    def position_timestamp_display(self):
        """
        Geeft de timestamp weer in leesbaar formaat (UTC).
        """
        if self.position_timestamp:
            dt = datetime.fromtimestamp(self.position_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def position_age_in_sec(self):
        """
        Leeftijdberekening in milliseconden sinds laatste positie.
        """
        if self.position_timestamp:
            return int(time.time() * 1000) - self.position_timestamp
        return None

    @property
    def position_age_display(self):
        """
        Levert de leeftijd in een leesbaar formaat (zoals '2m 30s').
        """
        return self._format_age_display(self.position_age_in_sec)

    @property
    def meta_timestamp_display(self):
        """
        Geeft de meta timestamp weer in leesbaar formaat (UTC).
        """
        if self.meta_timestamp:
            dt = datetime.fromtimestamp(self.meta_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def meta_age_in_sec(self):
        """
        Leeftijdberekening in milliseconden sinds meta_timestamp.
        """
        if self.meta_timestamp:
            return int(time.time() * 1000) - self.meta_timestamp
        return None

    @property
    def meta_age_display(self):
        """
        Levert de leeftijd van de meta_timestamp in een leesbaar formaat.
        """
        return self._format_age_display(self.meta_age_in_sec)

    def _format_age_display(self, age_ms):
        """
        Interne helper om leeftijd weer te geven als '2m 30s'.
        """
        if not age_ms:
            return "-"
        total_seconds = age_ms // 1000
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        if seconds or not parts: parts.append(f"{seconds}s")
        return ' '.join(parts)

    def display_name(self):
        """
        Geeft een leesbare naam voor deze tracker:
        - custom_name indien beschikbaar
        - anders: gekoppelde identifiers
        - anders: UUID
        """
        if self.custom_name:
            return self.custom_name

        identifiers = self.identifiers.all()
        if identifiers.exists():
            return ' | '.join(f"{ident.identkey}" for ident in identifiers)
        return str(self.id)

    def __str__(self):
        return self.display_name()


class TrackerIdentifier(models.Model):
    """
    Identificatie die een externe ID koppelt aan een specifieke tracker.
    """
    external_id = models.CharField(max_length=255,db_index=True)
    identifier_type = models.ForeignKey(TrackerIdentifierType, on_delete=models.PROTECT, related_name='tracker_identifiers')
    tracker = models.ForeignKey(Tracker, on_delete=models.CASCADE, related_name='identifiers')
    identkey = models.CharField(max_length=255, unique=True, editable=False, db_index=True)

    class Meta:
        constraints = [
                UniqueConstraint(fields=['external_id', 'identifier_type'], name='unique_external_id_per_type'),

        ]

        ordering = ['identkey']

    def save(self, *args, **kwargs):
        """
        Zet de external_id om naar hoofdletters, stelt de identkey in,
        en koppelt automatisch relevante groepen aan de tracker.
        """
        self.external_id = self.external_id.upper()
        self.identkey = f"{self.identifier_type.code}_{self.external_id}".upper()
        super().save(*args, **kwargs)

        groups_to_add = self.identifier_type.groups.exclude(
                id__in=self.tracker.groups.values_list('id', flat=True)
        )
        self.tracker.groups.add(*groups_to_add)

    def __str__(self):
        return f"{self.identifier_type.code}: {self.external_id} | {self.tracker.custom_name}"


class TrackerMessage(models.Model):
    """
    Bericht gekoppeld aan een TrackerIdentifier, bevat JSON-inhoud en optioneel een positie.
    """
    tracker_identifier = models.ForeignKey(TrackerIdentifier, on_delete=models.CASCADE, related_name='messages')
    msgtype = models.CharField(max_length=30, default=None)
    sha256_key = models.CharField(max_length=64, primary_key=True)
    content = models.JSONField()
    dbcall = models.JSONField(blank=True, null=True)
    raw = models.JSONField(blank=True, null=True)
    message_timestamp = models.BigIntegerField(db_index=True, help_text="UNIX tijd in milliseconden (UTC)")
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    position_timestamp = models.BigIntegerField(db_index=True, blank=True, null=True, help_text="UNIX tijd in ms")

    class Meta:
        ordering = ['-message_timestamp']

    @property
    def message_timestamp_display(self):
        """
        Geeft de timestamp weer in leesbaar formaat (UTC).
        """
        if self.message_timestamp:
            dt = datetime.fromtimestamp(self.message_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def age_in_sec(self):
        """
        Leeftijdberekening in milliseconden sinds dit bericht.
        """
        if self.message_timestamp:
            return int(time.time() * 1000) - self.message_timestamp
        return None

    @property
    def age_display(self):
        """
        Geeft de leeftijd van het bericht terug als leesbare string.
        """
        age_ms = self.age_in_sec
        if not age_ms:
            return "-"
        total_seconds = age_ms // 1000
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days: parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        if minutes: parts.append(f"{minutes}m")
        if seconds or not parts: parts.append(f"{seconds}s")
        return ' '.join(parts)

    def save(self, *args, **kwargs):
        """
        Genereert automatisch een hash van de content indien niet aanwezig.
        """
        if not self.sha256_key and self.content:
            import hashlib, json
            base_str = json.dumps(self.content, sort_keys=True)
            self.sha256_key = hashlib.sha256(base_str.encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tracker_identifier} msg {self.msgtype} at {self.message_timestamp_display}"


class TrackerDecoder(models.Model):
    identifier_type = models.ForeignKey(TrackerIdentifierType, on_delete=models.PROTECT, related_name='decoder_identifiertypes')
    msgtype = models.CharField(max_length=30, default=None)
    mapping = models.JSONField(default=dict, blank=True)
    ttl = models.IntegerField(default=90, help_text="Leeftijd in dagen van bericht met dit decodertype, voordat deze wordt verwijderd")

    class Meta:
        ordering = ['identifier_type__code', 'msgtype']

    def __str__(self):
        return f"{self.identifier_type.code} - {self.msgtype}"


class TrackerDecoderField(models.Model):
    name = models.CharField(primary_key=True, max_length=30, default=None, unique=True, validators=[
            RegexValidator(
                    r'^[a-z0-9_]+$',
                    'Alleen kleine letters (a-z), cijfers (0-9) en underscores (_) zijn toegestaan.'
            )])
    dbfield = models.CharField(
            max_length=100,
            blank=True,
            help_text="Kies een veld van het Tracker-model welke overeenkomt met dit decoder veld,<br>laat dit veld leeg om alleen in <i>Tracker messages</i> op te slaan."
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        if self.dbfield:
            return f"{self.name}| {self.dbfield}"
        else:
            return f"{self.name}| No DB field"