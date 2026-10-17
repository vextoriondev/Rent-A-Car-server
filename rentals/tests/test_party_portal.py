"""M10: staff-viewed party profile pages. Each response is projected by the party's kind, never by the viewer's role."""
import csv
import io
import json
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from ..models import AuditEvent, Branch, Contact, DriverAgreement, Membership, Organization, Party
from .. import services
from .test_reports import ReportBase

D=Decimal

class PortalBase(ReportBase):
    def as_role(self,role,branch=None):
        u=get_user_model().objects.create_user(f'p-{role}-{branch.pk if branch else 0}',password='x');Membership.objects.create(organization=self.org,user=u,role=role,branch=branch)
        c=Client();c.force_login(u);return c

    def url(self,party,section='',qs=''):
        return f'{self.base}/parties/{party.pk}/portal{section}{qs}'

    def get(self,party,section='',qs='',client=None):
        r=(client or self.c).get(self.url(party,section,qs));self.assertEqual(r.status_code,200,r.content);return r.json()

    def dump(self,party,*sections):
        """Every section of one portal as one string, to assert what must never appear."""
        out=[self.get(party)]
        for s in sections:out.append(self.get(party,s))
        return json.dumps(out)

    ALL=('/statement','/obligations','/payments','/activity','/documents','/notices')

class PortalContentTests(PortalBase):
    def test_root_for_each_kind_shows_profile_links_and_the_worked_figures(self):
        Contact.objects.create(party=self.customer,name='Billing desk',phone='017',role='billing')
        c=self.get(self.customer)
        self.assertEqual((c['party']['kind'],c['party']['name']),('customer','Meridian Textiles Ltd.'));self.assertEqual(c['party']['contacts'][0]['name'],'Billing desk')
        self.assertEqual((c['summary']['kind'],c['summary']['due'],c['summary']['bill_state']),('customer','30000.00','owed_to_us'))
        self.assertEqual([a['ref'] for a in c['agreements']],[f'AGR-{self.agreement.pk}']);self.assertEqual(c['agreements'][0]['customer_rent'],'100000.00')
        o=self.get(self.owner);self.assertEqual((o['summary']['due'],o['summary']['bill_state']),('12000.00','outstanding'));self.assertEqual(o['vehicles'][0]['registration'],'DHA-19-7724')
        d=self.get(self.driver);self.assertEqual((d['summary']['due'],d['summary']['bill_state']),('5000.00','outstanding'));self.assertEqual(d['agreements'][0]['ref'],f'AGR-{self.agreement.pk}')

    def test_driver_page_never_shows_customer_or_owner_figures(self):
        text=self.dump(self.driver,*self.ALL)
        for secret in('Meridian','Farhana','100000','60000','customer_rent','owner_rent','8000.00'):self.assertNotIn(secret,text,secret)
        self.assertIn('20000.00',text)                                                                  # the driver's own salary is there

    def test_owner_page_never_shows_driver_or_customer_figures(self):
        text=self.dump(self.owner,*self.ALL,'/deductions')
        for secret in('Meridian','Imran','100000','20000.00','driver_salary','customer_rent'):self.assertNotIn(secret,text,secret)
        self.assertIn('60000.00',text)

    def test_customer_page_never_shows_owner_or_driver_figures(self):
        text=self.dump(self.customer,*self.ALL)
        for secret in('Farhana','Imran','60000','20000.00','owner_rent','driver_salary','8000.00'):self.assertNotIn(secret,text,secret)
        self.assertIn('100000.00',text)

    def test_statement_rows_totals_and_range(self):
        s=self.get(self.customer,'/statement','?from=2026-09-01&to=2026-09-30')
        r=s['rows'][0];self.assertEqual((r['month'],r['ref'],r['vehicle'],r['billed'],r['received'],r['due'],r['bill_state']),('2026-09-01',f'AGR-{self.agreement.pk}','DHA-19-7724','100000.00','70000.00','30000.00','owed_to_us'))
        self.assertEqual((s['totals']['billed'],s['totals']['due']),('100000.00','30000.00'))
        self.assertEqual(self.get(self.customer,'/statement','?from=2026-10-01&to=2026-10-31')['rows'],[])
        o=self.get(self.owner,'/statement')['rows'][0];self.assertEqual((o['gross'],o['offsets'],o['net'],o['paid'],o['due']),('60000.00','8000.00','52000.00','40000.00','12000.00'))
        d=self.get(self.driver,'/statement')['rows'][0];self.assertEqual((d['earned'],d['paid'],d['due']),('20000.00','15000.00','5000.00'))
        self.assertEqual(self.c.get(self.url(self.customer,'/statement','?from=2026-10-02&to=2026-10-01')).status_code,422)

    def test_obligations_and_payments(self):
        ob=self.get(self.customer,'/obligations')['items'];self.assertEqual((ob[0]['kind'],ob[0]['amount'],ob[0]['allocated'],ob[0]['open_amount']),('customer_rent','100000.00','70000.00','30000.00'))
        pay=self.get(self.customer,'/payments')['items'];self.assertEqual((pay[0]['kind'],pay[0]['amount'],pay[0]['reference']),('customer_payment','70000.00','R-C'))
        self.assertEqual(pay[0]['allocations'][0]['month'],'2026-09-01')
        self.assertEqual(self.get(self.owner,'/payments')['items'][0]['reference'],'R-O')

    def test_owner_deductions_with_reason_and_proof_and_other_kinds_have_none(self):
        from ..models import Expense
        e=Expense.objects.get(reference='GAR-0182');e.description='Brake assembly repair';e.save(update_fields=['description'])
        self.client_upload(e)
        d=self.get(self.owner,'/deductions')['items'][0]
        self.assertEqual((d['category'],d['amount'],d['reason'],d['state']),('repair','8000.00','Brake assembly repair','posted'));self.assertEqual(d['proof'][0]['name'],'receipt.pdf')
        for p in(self.customer,self.driver):self.assertEqual(self.c.get(self.url(p,'/deductions')).status_code,404)

    def client_upload(self,expense):
        r=self.c.post(f'{self.base}/documents',data={'target_type':'expense','target_id':expense.pk,'file':SimpleUploadedFile('receipt.pdf',b'%PDF-1.4 receipt',content_type='application/pdf')})
        self.assertEqual(r.status_code,200,r.content)

    def test_activity_documents_and_notices(self):
        a=self.get(self.customer,'/activity')['items'];kinds={x['type'] for x in a};self.assertTrue({'payment','month_issued'}<=kinds)
        self.assertTrue(any(x['ref']=='R-C' and x['amount']=='70000.00' for x in a))
        services.post_charge(self.org,self.user,{'period_id':self.period.pk,'side':'customer','kind':'charge','amount':D(500),'description':'late fee'},'act-1')
        self.assertTrue(any(x['type']=='charge' and 'late fee' in x['text'] for x in self.get(self.customer,'/activity')['items']))
        self.assertEqual(self.get(self.customer,'/documents'),[])
        n=self.get(self.customer,'/notices')['items'];self.assertTrue(any(x['event_type']=='rent_issued' for x in n))

    def test_identity_scans_stay_admin_only_on_the_page(self):
        admin=self.as_role('admin')
        r=admin.post(f'{self.base}/documents',data={'target_type':'party','target_id':self.driver.pk,'category':'identity','file':SimpleUploadedFile('nid.pdf',b'%PDF-1.4 id',content_type='application/pdf')});self.assertEqual(r.status_code,200)
        self.assertEqual(len(self.get(self.driver,'/documents',client=admin)),1);self.assertEqual(self.get(self.driver,'/documents'),[])
        self.assertNotIn('nid_number',self.dump(self.driver,*self.ALL))

    def test_a_driver_with_a_standalone_agreement_shows_both_balances(self):
        da,_=services.create_driver_agreement(self.org,self.user,DriverAgreement(organization=self.org,driver=self.driver,start_date=date(2026,9,1),salary=D(12000),terms='Evening duty'))
        services.issue_driver_period(self.org,da.pk,date(2026,9,1),self.user)
        d=self.get(self.driver)
        self.assertEqual(d['driver_agreements'][0]['ref'],f'DRV-{da.pk}');self.assertEqual(d['driver_agreements'][0]['salary'],'12000.00')
        self.assertEqual((d['split']['vehicle_agreements']['due'],d['split']['standalone']['due'],d['summary']['due']),('5000.00','12000.00','17000.00'))
        rows=self.get(self.driver,'/statement')['rows'];self.assertEqual(sorted(r['ref'] for r in rows),sorted([f'AGR-{self.agreement.pk}',f'DRV-{da.pk}']))
        self.assertEqual(self.get(self.customer)['summary']['due'],'30000.00')

class PortalAccessTests(PortalBase):
    def test_roles_and_organizations(self):
        for role in('admin','accountant','operations','viewer'):self.assertEqual(self.as_role(role).get(self.url(self.customer)).status_code,200,role)
        self.assertEqual(self.as_role('mechanic').get(self.url(self.customer)).status_code,403)
        other=Organization.objects.create(name='Other');stranger=Party.objects.create(organization=other,kind='customer',name='Stranger')
        self.assertEqual(self.c.get(f'{self.base}/parties/{stranger.pk}/portal').status_code,404)
        self.assertEqual(self.c.get(f'{self.base}/parties/999999/portal').status_code,404)

    def test_a_branch_limited_member_cannot_open_another_branchs_party(self):
        main=services.default_branch(self.org);sylhet=Branch.objects.create(organization=self.org,name='Sylhet')
        solo=Party.objects.create(organization=self.org,kind='driver',name='Sylhet Driver')
        services.create_driver_agreement(self.org,self.user,DriverAgreement(organization=self.org,driver=solo,start_date=date(2026,9,1),salary=D(9000),branch=sylhet))
        limited=self.as_role('accountant',branch=main)
        self.assertEqual(limited.get(self.url(solo)).status_code,404)
        self.assertEqual(limited.get(self.url(self.customer)).status_code,200)
        self.assertEqual(self.get(solo)['party']['name'],'Sylhet Driver')

    def test_every_view_is_logged(self):
        before=AuditEvent.objects.filter(action='sensitive.read',target_type='party_portal').count()
        self.get(self.customer);self.get(self.customer,'/statement');self.get(self.customer,'/payments')
        rows=AuditEvent.objects.filter(action='sensitive.read',target_type='party_portal',target_id=str(self.customer.pk))
        self.assertEqual(rows.count()-before,3);self.assertEqual({r.after['section'] for r in rows},{'profile','statement','payments'})

class PortalExportTests(PortalBase):
    def export(self,party,qs='',client=None):
        return (client or self.c).get(self.url(party,'/statement/export',qs))

    def test_csv_follows_the_report_rules_and_is_audited(self):
        r=self.export(self.customer,'?from=2026-09-01&to=2026-09-30');self.assertEqual(r.status_code,200)
        text=r.content.decode('utf-8');self.assertTrue(text.startswith('﻿'));self.assertIn('text/csv',r['Content-Type']);self.assertIn('no-store',r['Cache-Control'])
        rows=list(csv.reader(io.StringIO(text.lstrip('﻿'))))
        flat=[c for row in rows for c in row];self.assertIn('Meridian Textiles Ltd.',flat);self.assertIn('2026-09-01',flat);self.assertIn('100000.00',flat)
        header=[row for row in rows if row and row[0]=='Month'][0];self.assertIn('Billed (৳)',header)
        self.assertTrue(AuditEvent.objects.filter(action='party.portal_export',target_id=str(self.customer.pk)).exists())

    def test_export_has_only_the_partys_own_side_and_no_ids(self):
        Party.objects.filter(pk=self.driver.pk).update(nid_number='1987654321098')
        text=self.export(self.driver).content.decode('utf-8')
        for secret in('Meridian','Farhana','100000','60000','1987654321098'):self.assertNotIn(secret,text,secret)
        self.assertIn('20000.00',text)

    def test_formula_cells_are_neutralised_and_roles_are_checked(self):
        Party.objects.filter(pk=self.customer.pk).update(name='=HYPERLINK("x")')
        text=self.export(self.customer).content.decode('utf-8');self.assertNotIn(',"=HYPERLINK',text);self.assertIn("'=HYPERLINK",text)
        self.assertEqual(self.export(self.customer,client=self.as_role('operations')).status_code,403)         # same rule as the other exports
        self.assertEqual(self.export(self.customer,client=self.as_role('viewer')).status_code,200)
        self.assertEqual(self.c.get(self.url(self.customer,'/statement/export','?from=2026-10-02&to=2026-10-01')).status_code,422)
