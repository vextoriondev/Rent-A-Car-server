"""Standalone driver agreements (M16b): the operator and a driver agree salary and staff terms outside any vehicle agreement.

Each issued month is a Period owned by the driver agreement (Period.agreement is null) holding one driver_salary obligation, so payments,
advances, absence deductions, fines, statements, driver payroll and bill colours all reuse the existing ledger. Customer, owner, invoices,
expenses and month-end settlement do not exist on these periods."""
from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import Q
from ..i18n import translate
from ..models import Agreement, DriverAgreement, DriverAgreementVersion, Obligation, Organization, Party, Period
from .audit import audit
from .helpers import ZERO, DomainError, bd, journal
from .statements import bulk_ledger_statements

def _first_on_or_after(d:date) -> date:
    return d if d.day==1 else (d.replace(day=1)+timedelta(days=32)).replace(day=1)

def check_driver_overlaps(da):
    """Non-blocking warnings: the same driver on another standalone agreement or on a vehicle agreement in the same dates."""
    end=da.end_date or date.max
    warnings=[]
    for o in DriverAgreement.objects.filter(organization_id=da.organization_id,driver_id=da.driver_id,active=True,start_date__lte=end).exclude(pk=da.pk).filter(Q(end_date__isnull=True)|Q(end_date__gte=da.start_date)):
        warnings.append(translate('warning_driver_also_on_driver_agreement',{'ref':f'DRV-{o.pk}'}))
    for a in Agreement.objects.filter(organization_id=da.organization_id,driver_id=da.driver_id,active=True,start_date__lte=end).filter(Q(end_date__isnull=True)|Q(end_date__gte=da.start_date)):
        warnings.append(translate('warning_driver_also_on_vehicle_agreement',{'ref':f'AGR-{a.pk}'}))
    return warnings

def _validate(da):
    org=da.organization_id
    if da.driver.kind!='driver':raise DomainError('the_party_must_be_a_driver')
    if da.driver.organization_id!=org or (da.vehicle_id and da.vehicle.organization_id!=org) or (da.branch_id and da.branch.organization_id!=org):raise DomainError('driver_vehicle_and_branch_must_belong_to')
    if da.salary is None or da.salary<=0:raise DomainError('the_salary_must_be_above_zero')
    if da.end_date and da.end_date<da.start_date:raise DomainError('end_date_precedes_start_date')

def _version_for(da,month):
    v=da.versions.filter(effective_from__lt=_first_on_or_after(month+timedelta(days=1))).order_by('-effective_from','-number').first()
    if not v:raise DomainError('no_driver_agreement_terms_for_this_month')
    return v

def _sync(da,today=None):
    """Keep salary and terms on the agreement equal to the version effective today."""
    today=today or date.today()
    v=da.versions.filter(effective_from__lte=today).order_by('-effective_from','-number').first() or da.versions.order_by('effective_from').first()
    da.salary=v.salary;da.terms=v.terms;da.save(update_fields=['salary','terms'])

@transaction.atomic
def create_driver_agreement(org,user,da,reason='Initial terms'):
    """Returns (driver_agreement, warnings). Version 1 holds the starting salary and terms."""
    da.organization=org
    if da.salary is not None:da.salary=bd(da.salary) if da.salary>0 else da.salary
    _validate(da)
    da.status='active';da.active=True;da.save()
    DriverAgreementVersion.objects.create(driver_agreement=da,number=1,effective_from=da.start_date,salary=da.salary,terms=da.terms,reason=reason,created_by=user)
    audit(org,user,'driver_agreement.create','driver_agreement',da.pk,after={'driver_id':da.driver_id,'start_date':da.start_date,'end_date':da.end_date,'salary':da.salary,'vehicle_id':da.vehicle_id})
    return da,check_driver_overlaps(da)

@transaction.atomic
def update_driver_agreement(org,user,da_id,changes):
    """End date (or clear it) and terms text. Salary changes go through revise_driver_agreement."""
    da=DriverAgreement.objects.select_for_update(of=('self',)).select_related('driver').get(pk=da_id,organization=org)
    before={'end_date':da.end_date,'terms':da.terms}
    unknown=set(changes)-{'end_date','terms'}
    if unknown:raise DomainError('unknown_fields_sorted', sorted=sorted(unknown))
    if 'end_date' in changes:
        end=changes['end_date']
        if end and end<da.start_date:raise DomainError('the_end_date_cannot_be_before_the')
        da.end_date=end
        da.status='active' if not end or end>=date.today() else 'ended'
    if 'terms' in changes and changes['terms'] is not None:
        da.terms=changes['terms'].strip()
        latest=da.versions.order_by('-number').first();latest.terms=da.terms;latest.save(update_fields=['terms'])  # free text only: wording is kept on the latest version
    da.save()
    audit(org,user,'driver_agreement.update','driver_agreement',da.pk,before=before,after={'end_date':da.end_date,'terms':da.terms})
    return da

@transaction.atomic
def revise_driver_agreement(org,user,da_id,effective_from,changes,reason):
    """A dated salary (or terms) change, applied from the month it starts; refused once a period from then on is issued."""
    if not (reason or '').strip():raise DomainError('a_reason_is_required')
    da=DriverAgreement.objects.select_for_update(of=('self',)).select_related('driver').get(pk=da_id,organization=org)
    start=_first_on_or_after(effective_from)
    if da.end_date and start>da.end_date:raise DomainError('revision_starts_after_the_agreement_ends')
    if Period.objects.filter(driver_agreement=da,month__gte=start).exists():raise DomainError('periods_from_that_date_are_already_issued')
    unknown=set(changes)-{'salary','terms'}
    if unknown:raise DomainError('unknown_terms_sorted', sorted=sorted(unknown))
    previous=da.versions.filter(effective_from__lte=start).order_by('-effective_from','-number').first() or da.versions.order_by('-number').first()
    salary=bd(changes['salary']) if changes.get('salary') is not None else previous.salary
    terms=changes['terms'].strip() if changes.get('terms') is not None else previous.terms
    if da.versions.filter(effective_from=start).exists():raise DomainError('a_version_already_starts_on_that_date')
    number=(da.versions.order_by('-number').values_list('number',flat=True).first() or 0)+1
    v=DriverAgreementVersion.objects.create(driver_agreement=da,number=number,effective_from=start,salary=salary,terms=terms,reason=reason.strip(),created_by=user)
    _sync(da)
    audit(org,user,'driver_agreement.revise','driver_agreement',da.pk,before={'salary':previous.salary,'terms':previous.terms},after={'salary':salary,'terms':terms,'number':number,'effective_from':start},reason=reason)
    return v

@transaction.atomic
def issue_driver_period(org,da_id,month,user=None):
    """Issue one month of salary for a standalone driver agreement. Idempotent per month."""
    da=DriverAgreement.objects.select_for_update(of=('self',)).select_related('driver','vehicle').get(pk=da_id,organization=org,active=True)
    if month.day!=1:raise DomainError('month_must_be_its_first_day')
    if month<da.start_date.replace(day=1) or (da.end_date and month>da.end_date.replace(day=1)):raise DomainError('agreement_is_not_effective_this_month')
    existing=Period.objects.filter(organization=org,driver_agreement=da,month=month).first()
    if existing:return existing
    version=_version_for(da,month)
    period=Period.objects.create(organization=org,driver_agreement=da,month=month)
    ob=Obligation.objects.create(period=period,kind=Obligation.Kind.DRIVER,party=da.driver,amount=version.salary)
    journal(org,'obligation',ob.pk,[('DRIVER_SALARY_EXPENSE',version.salary,ZERO),('DRIVER_PAYABLE',ZERO,version.salary,da.driver)],period=period,event_date=month)
    audit(org,user,'period.issue','period',period.pk,after={'driver_agreement_id':da.pk,'month':month,'version':version.number,'salary':version.salary})
    return period

def driver_balance_split(org,party_id):
    """What the operator owes one driver, split between vehicle agreements and standalone driver agreements, with every period listed."""
    driver=Party.objects.get(pk=party_id,organization=org,kind='driver')
    periods=list(Period.objects.filter(organization=org,obligations__kind='driver_salary',obligations__party=driver).select_related('agreement','driver_agreement').distinct().order_by('month','pk'))
    stmts=bulk_ledger_statements(periods)
    out={'vehicle_agreements':{'due':ZERO,'items':[]},'standalone':{'due':ZERO,'items':[]}}
    for p in periods:
        due=stmts[p.pk]['driver']['due'];standalone=p.driver_agreement_id is not None
        group=out['standalone' if standalone else 'vehicle_agreements']
        group['due']+=due
        group['items'].append({'ref':f'DRV-{p.driver_agreement_id}' if standalone else f'AGR-{p.agreement_id}','period_id':p.pk,'month':p.month.isoformat(),'due':due})
    out['total_due']=out['vehicle_agreements']['due']+out['standalone']['due']
    return out
