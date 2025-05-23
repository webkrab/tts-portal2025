from utils.logger import get_logger
logger = get_logger(__name__)

from gpstracking.models import TrackerDecoder, TrackerIdentifierType
def get_decoder_mapping(self, identtype, msgtype):
    """Haalt mapping uit cache of DB bij eerste gebruik"""
    key = (identtype, msgtype)

    if key in self.MAPPING_STN:
        return self.MAPPING_STN[key]
    identtypeObj = self.IDENTTYPE.get(code=identtype)
    decoder, _ = TrackerDecoder.objects.get_or_create(
        identifier_type=identtypeObj,
        msgtype=msgtype,
        defaults={"mapping": {}}
    )

    self.MAPPING_STN[key] = decoder.mapping
    return self.MAPPING_STN[key]


def update_mapping_if_missing(self, identtype, msgtype, missing_keys):
    """Voegt ontbrekende keys toe aan mapping en slaat op in DB"""
    key = (identtype, msgtype)
    mapping = self.MAPPING_STN.get(key, {})
    identtypeObj = self.IDENTTYPE.get(code=identtype)

    changed = False
    for m in missing_keys:
        if m not in mapping:
            mapping[m] = None
            changed = True

    if changed:
        self.MAPPING_STN[key] = mapping
        TrackerDecoder.objects.filter(
            identifier_type=identtypeObj,
            msgtype=msgtype
        ).update(mapping=mapping)