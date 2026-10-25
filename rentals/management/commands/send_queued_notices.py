from django.core.management.base import BaseCommand
from ...services import notify

class Command(BaseCommand):
    help='Send notices that are still queued or stuck sending (run from cron or a scheduler). Failed notices are retried by hand from the app.'
    def add_arguments(self,parser):parser.add_argument('--older-than',type=int,default=300,help='seconds a notice must have waited')
    def handle(self,*args,**o):self.stdout.write(f'{notify.sweep(o["older_than"])} notice(s) processed.')
