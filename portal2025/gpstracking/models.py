import uuid
from datetime import datetime, timezone, timedelta
import time

from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import Polygon, MultiPolygon
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.db import models

def default_tracker_area():
    min_lat = 50.475
    max_lat = 53.825
    min_lon = 2.850
    max_lon = 7.550

    polygon = Polygon((
        (min_lon, min_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
        (min_lon, max_lat),
        (min_lon, min_lat),  # sluit de polygon
    ))
    return MultiPolygon(polygon, srid=4326)

def get_tracker_field_choices():
    extra_fields = ["age_in_sec", "age_human", "ais_dimensions"]
    model_fields = [
        (field.name, field.name)
        for field in Tracker._meta.get_fields()
        if isinstance(field, models.Field) and field.concrete and not field.auto_created
    ]
    return model_fields + [(field, field) for field in extra_fields]

def default_tracker_visible_fields():
    return [
        "id", "screen_name", "icon",
        "altitude", "speed", "heading",
        "position_timestamp", "position",
        "age_in_sec", "age_human"
    ]

class TrackerIdentifierType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.name}"


class TrackerGroup(models.Model):
    smartcode = models.CharField(
        max_length=10,
        unique=True,
        validators=[RegexValidator(r'^[a-z0-9]+$', 'Alleen kleine letters (a-z) en cijfers (0-9) zijn toegestaan.')],
    )
    name = models.CharField(max_length=255, unique=True)
    area = gis_models.MultiPolygonField(
            help_text="Laat leeg indien wereldwijde dekking gewenst is.",
            geography=True,
            blank=True,
            null=True,
            srid=4326,
            default=default_tracker_area,
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
        if self.pk:
            old = TrackerGroup.objects.filter(pk=self.pk).first()
            if old and self.smartcode != old.smartcode:
                raise ValidationError({'smartcode': _(f'Smartcode "{old.smartcode}" mag niet worden aangepast na creatie.')})


    def __str__(self):
        return f'{self.smartcode} | {self.name}'


class Tracker(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    screen_name = models.CharField(max_length=255)
    icon = models.CharField(max_length=255)

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

    altitude = models.FloatField(default=0, blank=True, null=True)
    speed = models.FloatField(blank=True, null=True)
    heading = models.FloatField(blank=True, null=True)
    position_timestamp = models.BigIntegerField(blank=True, null=True, help_text="UNIX tijd in ms")
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)

    groups = models.ManyToManyField(TrackerGroup, related_name='trackers', blank=True)

    @property
    def position_timestamp_display(self):
        if self.position_timestamp:
            dt = datetime.fromtimestamp(self.position_timestamp / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def age_in_sec(self):
        if self.position_timestamp:
            return int(time.time() * 1000) - self.position_timestamp
        return None

    @property
    def age_display(self):
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

    def __str__(self):
        return self.screen_name


class TrackerIdentifier(models.Model):
    external_id = models.CharField(max_length=255)
    identifier_type = models.ForeignKey(TrackerIdentifierType, on_delete=models.PROTECT, related_name='tracker_identifiers')
    tracker = models.ForeignKey(Tracker, on_delete=models.CASCADE, related_name='identifiers')
    identkey = models.CharField(max_length=255, unique=True, editable=False)

    class Meta:
        unique_together = ('external_id', 'identifier_type')

    def save(self, *args, **kwargs):
        self.identkey = f"{self.identifier_type.name}_{self.external_id}".upper()
        super().save(*args, **kwargs)

        groups_to_add = self.identifier_type.groups.exclude(
            id__in=self.tracker.groups.values_list('id', flat=True)
        )
        self.tracker.groups.add(*groups_to_add)

    def __str__(self):
        return f"{self.identifier_type.name}: {self.external_id} | {self.tracker.screen_name}"


class TrackerMessage(models.Model):
    tracker_identifier = models.ForeignKey(TrackerIdentifier, on_delete=models.CASCADE, related_name='messages')
    msgtype = models.CharField(max_length=10, default=None)
    content = models.JSONField()
    created_at = models.BigIntegerField(help_text="UNIX tijd in milliseconden (UTC)")
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    sha256_key = models.CharField(max_length=64, blank=True, null=True, unique=True)

    @property
    def created_at_display(self):
        if self.created_at:
            dt = datetime.fromtimestamp(self.created_at / 1000, tz=timezone.utc)
            return dt.isoformat(sep=' ', timespec='seconds')
        return "-"

    @property
    def age_in_sec(self):
        if self.position_timestamp:
            return int(time.time() * 1000) - self.position_timestamp
        return None

    @property
    def age_display(self):
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
        if not self.sha256_key and self.content:
            import hashlib, json
            base_str = json.dumps(self.content, sort_keys=True)
            self.sha256_key = hashlib.sha256(base_str.encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Message for {self.tracker_identifier} at {self.created_at}"
