import uuid

from django.contrib.gis.db import models as gis_models
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.text import slugify
import gpstracking.models as gpstrackingModel

host_validator = RegexValidator(
        regex=r'^([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}|(\d{1,3}\.){3}\d{1,3})$',
        message="Moet een geldig IP-adres of domeinnaam zijn."
)


# Create your models here.
class Gateway(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=30, unique=True)
    position = gis_models.PointField(geography=True, blank=True, null=True, srid=4326)
    slug = models.SlugField(max_length=50, unique=True, blank=True)

    datatype = models.CharField(max_length=15, choices=(("traccar", "Tracar"),))
    identifier_prefix = (models.ForeignKey(gpstrackingModel.TrackerIdentifierType, on_delete=models.RESTRICT))


    host = models.CharField(
            max_length=255,
            blank=True,
            null=True,
            validators=[host_validator],
            help_text="Voer een geldig IP-adres of domeinnaam in. (geen http(s)://"
    )
    port = models.IntegerField(blank=True, validators=[MinValueValidator(0), MaxValueValidator(65535)])
    database = models.CharField(max_length=255, blank=True)
    table = models.CharField(max_length=255, blank=True)
    interval = models.IntegerField(blank=True, verbose_name='Refresh interval', help_text="Data refresh interval in seconden")

    auth_key = models.CharField(max_length=255, blank=True)
    auth_user = models.CharField(max_length=255, blank=True)
    auth_pass = models.CharField(max_length=255, blank=True)

    externalurl = models.URLField(max_length=255, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        unique_together = [['host', 'port', 'database', 'table']]
        ordering = ['datatype', 'name']

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.datatype} | {self.name}'
