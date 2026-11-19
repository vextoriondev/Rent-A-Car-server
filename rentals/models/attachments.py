"""Centralized file attachments (photos, scans, PDFs, Word files) for every module.

The bytes live in object storage (S3 compatible, or a mounted volume in development); this row is the source of truth for
what the file is, who owns it and which business record it belongs to. Every attachment points at exactly ONE record through a real
foreign key, so deleting the record deletes its attachment rows (no orphans) and the database refuses a row with no owner.
"""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from .organizations import Organization
from .parties import Party, Vehicle
from .finance import TasinAgreement
from .tasin import MaintenanceRecord, AccountTransaction

OWNER_FIELDS = ('party', 'vehicle', 'agreement', 'maintenance', 'transaction')


class Attachment(models.Model):
    class Kind(models.TextChoices):
        PROFILE = 'profile', 'Profile / main picture'
        PHOTO = 'photo', 'Photo'
        DOCUMENT = 'document', 'Document'

    class Status(models.TextChoices):
        UPLOADING = 'uploading', 'Uploading'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='attachments')
    # exactly one of these is set (CheckConstraint below); owner/driver/client files hang on the Party row
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')
    agreement = models.ForeignKey(TasinAgreement, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')
    maintenance = models.ForeignKey(MaintenanceRecord, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')
    transaction = models.ForeignKey(AccountTransaction, null=True, blank=True, on_delete=models.CASCADE, related_name='attachments')

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.DOCUMENT)
    category = models.CharField(max_length=40, blank=True, default='')
    description = models.CharField(max_length=500, blank=True, default='')

    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=80)                      # server generated, never the client's name
    mime_type = models.CharField(max_length=100)                        # sniffed from the content, not the client's claim
    extension = models.CharField(max_length=10)
    storage_provider = models.CharField(max_length=20, default='filesystem')
    storage_key = models.CharField(max_length=255, blank=True, default='')   # the "main" object (optimized image, or the document itself)
    original_key = models.CharField(max_length=255, blank=True, default='')  # untouched scan kept for document images
    variants = models.JSONField(default=dict, blank=True)                    # {"thumb": {"key","size","width","height"}, "medium": ..., "large": ...}
    size_original = models.PositiveBigIntegerField(default=0)
    size_stored = models.PositiveBigIntegerField(default=0)                  # main object as stored (optimized size for photos)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64)

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PROCESSING)
    error = models.CharField(max_length=255, blank=True, default='')
    upload_key = models.CharField(max_length=80, blank=True, default='')     # client retry key: a repeated upload returns the same row

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'kind']),
            models.Index(fields=['party', 'kind']),
            models.Index(fields=['vehicle', 'kind']),
            models.Index(fields=['agreement']),
            models.Index(fields=['maintenance']),
            models.Index(fields=['transaction']),
            models.Index(fields=['sha256']),
        ]
        constraints = [
            models.CheckConstraint(name='attachment_exactly_one_owner', condition=(
                Q(party__isnull=False, vehicle__isnull=True, agreement__isnull=True, maintenance__isnull=True, transaction__isnull=True)
                | Q(party__isnull=True, vehicle__isnull=False, agreement__isnull=True, maintenance__isnull=True, transaction__isnull=True)
                | Q(party__isnull=True, vehicle__isnull=True, agreement__isnull=False, maintenance__isnull=True, transaction__isnull=True)
                | Q(party__isnull=True, vehicle__isnull=True, agreement__isnull=True, maintenance__isnull=False, transaction__isnull=True)
                | Q(party__isnull=True, vehicle__isnull=True, agreement__isnull=True, maintenance__isnull=True, transaction__isnull=False))),
            # one profile picture per person / per vehicle, enforced by the database
            models.UniqueConstraint(fields=['party'], condition=Q(kind='profile'), name='uniq_profile_per_party'),
            models.UniqueConstraint(fields=['vehicle'], condition=Q(kind='profile'), name='uniq_profile_per_vehicle'),
            models.UniqueConstraint(fields=['organization', 'upload_key'], condition=~Q(upload_key=''), name='uniq_attachment_upload_key'),
        ]

    @property
    def owner_ref(self):
        for f in OWNER_FIELDS:
            if getattr(self, f + '_id'):
                return f, getattr(self, f + '_id')
        return None, None

    def all_keys(self):
        keys = [self.storage_key, self.original_key] + [v.get('key', '') for v in (self.variants or {}).values()]
        return [k for k in dict.fromkeys(keys) if k]
