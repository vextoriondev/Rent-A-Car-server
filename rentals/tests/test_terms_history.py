from datetime import date
from decimal import Decimal
from django.test import Client
from ..models import Agreement, Party, Period, Vehicle, VehicleOwnership
from .. import services
from .base import RentalsTestCase

class TermsHistoryTests(RentalsTestCase):
    def test_issue_period_records_version_and_baseline_exists(self):
        self.assertEqual(self.period.agreement_version.number,1)
        self.assertEqual(self.agreement.driver_assignments.count(),1);self.assertEqual(self.car.ownerships.count(),1)

    def test_revision_applies_from_next_period_and_history_is_unchanged(self):
        before=services.statement(self.period)
        v=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'customer_rent':'120000','owner_rent':'70000'},'Annual review')
        self.assertEqual((v.number,v.status),(2,'active'))
        self.assertEqual(services.statement(Period.objects.get(pk=self.period.pk)),before)          # September untouched
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        self.assertEqual(october.agreement_version_id,v.pk)
        self.assertEqual({o.kind:o.amount for o in october.obligations.all()},{'customer_rent':Decimal('120000.00'),'owner_rent':Decimal('70000.00'),'driver_salary':Decimal('20000.00')})
        self.assertEqual(self.agreement.versions.get(number=1).status,'superseded')

    def test_mid_month_effective_date_is_kept_and_the_month_is_split(self):   # M12: no longer rounded up to the 1st
        v=services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,15),{'driver_salary':'25000'},'Raise')
        self.assertEqual(v.effective_from,date(2026,10,15))
        self.assertEqual(services.issue_period(self.org,self.agreement.pk,date(2026,10,1)).obligations.get(kind='driver_salary').amount,Decimal('22741.94'))
        self.assertEqual(services.issue_period(self.org,self.agreement.pk,date(2026,11,1)).obligations.get(kind='driver_salary').amount,Decimal('25000.00'))

    def test_retroactive_revision_into_issued_or_closed_period_is_rejected(self):
        with self.assertRaises(services.DomainError):services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,9,1),{'owner_rent':'1'},'x')
        services.close_period(self.org,self.user,self.period.pk)
        with self.assertRaises(services.DomainError):services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,8,1),{'owner_rent':'1'},'x')
        with self.assertRaises(services.DomainError):services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'owner_rent':'1'},'  ')

    def test_expense_rules_come_from_period_version_not_current_terms(self):
        services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'repair_responsible':'operator','owner_offset_allowed':False},'New split')
        data={'period_id':self.period.pk,'category':'repair','amount':Decimal('1000'),'paid_by':'operator','responsible':'owner','offset_owner':True,'reference':'R1','event_date':date(2026,9,5)}
        e=services.record_expense(self.org,self.user,data,'k1')              # September still has owner-responsible repairs and offset allowed
        self.assertEqual(e.rule_snapshot['agreement_version'],1)
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        with self.assertRaises(services.DomainError):services.record_expense(self.org,self.user,{**data,'period_id':october.pk,'reference':'R2','event_date':date(2026,10,5)},'k2')

    def test_approval_threshold_is_per_version(self):
        services.revise_agreement(self.org,self.user,self.agreement.pk,date(2026,10,1),{'approval_threshold':'500'},'Tighter control')
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        base={'category':'fuel','paid_by':'operator','responsible':'owner','offset_owner':False}
        self.assertEqual(services.record_expense(self.org,self.user,{**base,'period_id':october.pk,'amount':Decimal('600'),'reference':'F1'},'a').state,'pending')
        self.assertEqual(services.record_expense(self.org,self.user,{**base,'period_id':self.period.pk,'amount':Decimal('600'),'reference':'F2'},'b').state,'posted')

    def test_ownership_transfer_mid_history(self):
        new_owner=Party.objects.create(organization=self.org,kind='owner',name='New Owner')
        services.transfer_vehicle_ownership(self.org,self.user,self.car.pk,new_owner.pk,date(2026,10,1))
        self.assertEqual(self.period.obligations.get(kind='owner_rent').party,self.owner)           # September stays with the old owner
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        self.assertEqual(october.obligations.get(kind='owner_rent').party,new_owner)
        self.assertEqual(services.owner_on(self.car,date(2026,9,30)),self.owner)
        old=self.car.ownerships.get(owner=self.owner);self.assertEqual(old.effective_to,date(2026,9,30))
        with self.assertRaises(services.DomainError):services.transfer_vehicle_ownership(self.org,self.user,self.car.pk,self.owner.pk,date(2026,9,1))

    def test_driver_reassignment_with_salary_override(self):
        d2=Party.objects.create(organization=self.org,kind='driver',name='New Driver')
        a,warnings=services.assign_driver(self.org,self.user,self.agreement.pk,d2.pk,date(2026,10,1),Decimal('18000'))
        self.assertEqual(warnings,[])
        october=services.issue_period(self.org,self.agreement.pk,date(2026,10,1))
        ob=october.obligations.get(kind='driver_salary');self.assertEqual((ob.party,ob.amount),(d2,Decimal('18000.00')))
        self.assertEqual(self.period.obligations.get(kind='driver_salary').party,self.driver)
        self.agreement.refresh_from_db()  # legacy column mirrors what is effective today, not a future assignment
        with self.assertRaises(services.DomainError):services.assign_driver(self.org,self.user,self.agreement.pk,d2.pk,date(2026,9,1))

    def test_draft_then_activate(self):
        car2=Vehicle.objects.create(organization=self.org,registration='D-2',model_name='Y',owner=self.owner)
        a=Agreement(organization=self.org,vehicle=car2,customer=self.customer,driver=self.driver,start_date=date(2026,1,1),customer_rent=Decimal('9'),owner_rent=Decimal('5'),driver_salary=Decimal('2'))
        a,_=services.create_agreement(self.org,self.user,a,draft=True)
        self.assertEqual((a.status,a.active,a.versions.get().status),('draft',False,'draft'))
        with self.assertRaises(Agreement.DoesNotExist):services.issue_period(self.org,a.pk,date(2026,9,1))
        a,_=services.activate_agreement(self.org,self.user,a.pk)
        self.assertEqual((a.status,a.active,a.versions.get().status),('active',True,'active'))
        self.assertEqual(services.issue_period(self.org,a.pk,date(2026,9,1)).agreement_version.number,1)

    def test_other_organization_agreement_cannot_be_revised(self):
        from ..models import Organization
        other=Organization.objects.create(name='Other')
        with self.assertRaises(Agreement.DoesNotExist):services.revise_agreement(other,self.user,self.agreement.pk,date(2026,10,1),{'owner_rent':'1'},'x')

    def test_api_detail_revise_and_actor_redaction(self):
        c=Client();c.force_login(self.user);base=f'/api/v1/orgs/{self.org.pk}'
        r=c.post(f'{base}/agreements/{self.agreement.pk}/revise',{'effective_from':'2026-10-01','reason':'API','changes':{'owner_rent':'65000'}},content_type='application/json')
        self.assertEqual(r.status_code,200,r.content);self.assertEqual(r.json()['owner_rent'],'65000.00')
        d=c.get(f'{base}/agreements/{self.agreement.pk}').json()
        self.assertEqual([v['number'] for v in d['versions']],[1,2]);self.assertEqual(len(d['driver_assignments']),1);self.assertEqual(len(d['ownerships']),1)
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user('drv',password='x');services.grant_party_access(self.org,self.driver.pk,'drv')
        dc=Client();dc.force_login(get_user_model().objects.get(username='drv'))
        dd=dc.get(f'{base}/agreements/{self.agreement.pk}').json()
        self.assertNotIn('versions',dd);self.assertNotIn('owner_rent',dd);self.assertNotIn('customer_rent',dd)
        self.assertEqual(dc.post(f'{base}/agreements/{self.agreement.pk}/revise',{'effective_from':'2026-11-01','reason':'x','changes':{}},content_type='application/json').status_code,403)

    def test_worked_example_invariants_unchanged(self):
        self.pay('customer_payment','70000','C1');self.pay('owner_payout','40000','O1');self.pay('driver_payout','15000','D1')
        s=services.statement(self.period)
        self.assertEqual((s['customer']['due'],s['owner']['due'],s['driver']['due']),(Decimal('30000'),Decimal('20000'),Decimal('5000')))
