"""Per-version expense rules. A version's rules are frozen once any period was issued under it; change them by revising the agreement."""
from decimal import Decimal
from django.db import transaction
from ..models import AgreementVersion, ExpenseCategory, ExpenseRule
from .helpers import DomainError, bd
from .expenses import ensure_categories, PARTIES

@transaction.atomic
def set_expense_rule(org,user,version_id,category,*,responsible,first_payer='operator',reimbursable=True,deductible_from='none',approval_threshold=None,proof_required=False,notify_parties=True):
    version=AgreementVersion.objects.select_for_update().select_related('agreement').get(pk=version_id,agreement__organization=org)
    if version.periods.exists():raise DomainError('Periods were already issued under this version; revise the agreement to change rules')
    ensure_categories(org)
    if not ExpenseCategory.objects.filter(organization=org,code=category,active=True).exists():raise DomainError('Unsupported expense category')
    if responsible not in PARTIES:raise DomainError('Unsupported responsible party')
    if first_payer not in ('operator','owner'):raise DomainError('First payer must be operator or owner')
    if deductible_from not in ExpenseRule.Deductible.values:raise DomainError('Unknown deduction target')
    if deductible_from=='owner_payable' and (responsible!='owner' or first_payer!='operator'):raise DomainError('Only operator-paid, owner-responsible costs can be deducted from owner payable')
    rule,_=ExpenseRule.objects.update_or_create(agreement_version=version,category=category,defaults={'responsible':responsible,'first_payer':first_payer,'reimbursable':reimbursable,'deductible_from':deductible_from,'approval_threshold':bd(approval_threshold) if approval_threshold is not None else None,'proof_required':proof_required,'notify_parties':notify_parties})
    return rule

@transaction.atomic
def delete_expense_rule(org,user,version_id,category):
    version=AgreementVersion.objects.select_for_update().get(pk=version_id,agreement__organization=org)
    if version.periods.exists():raise DomainError('Periods were already issued under this version; revise the agreement to change rules')
    ExpenseRule.objects.filter(agreement_version=version,category=category).delete()

def copy_rules(previous,new_version):
    for r in previous.expense_rules.all():
        ExpenseRule.objects.create(agreement_version=new_version,category=r.category,responsible=r.responsible,first_payer=r.first_payer,reimbursable=r.reimbursable,deductible_from=r.deductible_from,approval_threshold=r.approval_threshold,proof_required=r.proof_required,notify_parties=r.notify_parties)
