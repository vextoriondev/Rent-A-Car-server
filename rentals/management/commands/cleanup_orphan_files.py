"""Find (and optionally delete) storage objects that no Attachment row refers to, e.g. after a crash between the storage write and the
database commit. Safe by default: it only lists. Objects younger than --older-than-hours are never touched (an upload may be in flight).

    python manage.py cleanup_orphan_files                    # report
    python manage.py cleanup_orphan_files --delete           # remove orphans older than 24 h
Also reports Attachment rows whose objects are missing from storage.
"""
import time
from django.core.management.base import BaseCommand
from rentals.models import Attachment
from rentals.services.files.storage import get_storage


class Command(BaseCommand):
    help = 'List or delete storage objects without an Attachment row, and rows whose object is missing.'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', help='actually delete the orphans (default: only report)')
        parser.add_argument('--older-than-hours', type=float, default=24.0)

    def handle(self, *args, **opts):
        st = get_storage()
        known = set()
        missing = []
        for a in Attachment.objects.all().iterator():
            keys = a.all_keys()
            known.update(keys)
            for k in keys:
                if not st.exists(k):
                    missing.append((str(a.id), k))
        cutoff = time.time() - opts['older_than_hours'] * 3600
        orphans, young = [], 0
        for key in st.iter_keys():
            if key in known:
                continue
            m = st.mtime(key)
            if m is not None and m > cutoff:
                young += 1
                continue
            orphans.append(key)
        self.stdout.write(f'{len(known)} referenced objects, {len(orphans)} orphaned (older than {opts["older_than_hours"]} h), {young} too recent to judge, {len(missing)} missing from storage')
        for k in orphans:
            if opts['delete']:
                st.delete(k)
                self.stdout.write(f'deleted {k}')
            else:
                self.stdout.write(f'orphan {k}')
        for att_id, k in missing:
            self.stdout.write(self.style.WARNING(f'attachment {att_id}: object {k} is missing from storage'))
