"""M17: every sentence a person reads comes from the message catalog, in the user's language, and a language switch never changes a figure."""
import ast
import glob
import json
import re
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from django.test import Client, SimpleTestCase
from .base import RentalsTestCase
from .. import i18n, services
from ..models import UserPreference

ROOT=Path(__file__).resolve().parents[1]
BANNED=['ledger','journal','idempotency','accrual','receivable','payable','django','postgres','payload','snapshot','obligation','operator','vehicle','void','posted']   # words the glossary retires from user text

def catalog(lang):
    path=ROOT/'locale'/f'{lang}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

class CatalogTests(RentalsTestCase):
    def test_every_code_used_in_the_source_has_english_text(self):
        en=catalog('en');used=set()
        for path in glob.glob(str(ROOT/'services'/'*.py'))+glob.glob(str(ROOT/'api'/'*.py'))+[str(ROOT/'i18n.py')]:
            tree=ast.parse(Path(path).read_text(encoding='utf-8'))
            for n in ast.walk(tree):
                if isinstance(n,ast.Call):
                    name=n.func.id if isinstance(n.func,ast.Name) else n.func.attr if isinstance(n.func,ast.Attribute) else None
                    if name in('DomainError','ApiError','translate'):
                        args=[a for a in n.args if isinstance(a,ast.Constant) and isinstance(a.value,str)]
                        if name=='ApiError':args=args[:1]
                        if args and re.fullmatch(r'[a-z0-9_]+',args[0].value):used.add(args[0].value)
        missing=sorted(c for c in used if c not in en and not any(c==p for p in('col_','kind_','notice_detail_','report_')))
        self.assertEqual(missing,[])

    def test_text_is_plain_no_retired_words(self):
        bad=[(k,v) for k,v in catalog('en').items() if any(re.search(r'\b'+w,v.lower()) for w in BANNED)]
        self.assertEqual(bad,[])

    def test_bangla_never_invents_or_drops_a_value(self):
        en,bn=catalog('en'),catalog('bn')
        self.assertEqual(sorted(set(bn)-set(en)),[])                       # no Bangla text for a code that does not exist
        for code,text in bn.items():
            self.assertEqual(sorted(set(re.findall(r'\{(\w+)\}',text))),sorted(set(re.findall(r'\{(\w+)\}',en[code]))),code)

class LanguageTests(RentalsTestCase):
    def setUp(self):
        super().setUp()
        self.c=Client();self.c.force_login(self.user)
        self.base=f'/api/v1/orgs/{self.org.pk}'

    def test_an_error_answers_with_text_code_and_values(self):
        r=self.c.post(f'{self.base}/payments',{'kind':'customer_payment','amount':'999999','reference':'X','method':'bank','event_date':'2026-09-20','allocations':[{'obligation_id':self.ob['customer_rent'].pk,'amount':'999999'}]},content_type='application/json',HTTP_IDEMPOTENCY_KEY='m17-1')
        self.assertEqual(r.status_code,422);body=r.json()
        self.assertEqual(body['code'],'payment_exceeds_an_open_obligation');self.assertIn('detail',body);self.assertIsInstance(body['params'],dict)

    def test_accept_language_picks_the_language_and_english_is_the_fallback(self):
        i18n.catalog('bn')['payment_exceeds_an_open_obligation']='বাংলা পরীক্ষা'
        try:
            body={'kind':'customer_payment','amount':'999999','reference':'X','method':'bank','event_date':'2026-09-20','allocations':[{'obligation_id':self.ob['customer_rent'].pk,'amount':'999999'}]}
            bn=self.c.post(f'{self.base}/payments',body,content_type='application/json',HTTP_IDEMPOTENCY_KEY='m17-2',HTTP_ACCEPT_LANGUAGE='bn-BD,bn;q=0.9').json()
            self.assertEqual(bn['detail'],'বাংলা পরীক্ষা');self.assertEqual(bn['code'],'payment_exceeds_an_open_obligation')
            en=self.c.post(f'{self.base}/payments',body,content_type='application/json',HTTP_IDEMPOTENCY_KEY='m17-3',HTTP_ACCEPT_LANGUAGE='en').json()
            self.assertEqual(en['detail'],catalog('en')['payment_exceeds_an_open_obligation'])
            self.assertEqual(i18n.translate('a_date_is_required',lang='xx'),catalog('en')['a_date_is_required'])   # unknown language -> English
        finally:
            i18n.reload_catalogs()

    def test_a_missing_bangla_sentence_falls_back_to_english(self):
        i18n.catalog('bn').pop('a_date_is_required',None)
        self.assertEqual(i18n.translate('a_date_is_required',lang='bn'),catalog('en')['a_date_is_required'])
        i18n.reload_catalogs()

    def test_the_saved_choice_is_used_when_the_app_sends_no_header(self):
        r=self.c.patch(f'{self.base[:-len(f"/orgs/{self.org.pk}")]}/me/preferences',{'language':'bn'},content_type='application/json');self.assertEqual(r.status_code,200)
        self.assertEqual(UserPreference.objects.get(user=self.user).language,'bn')
        self.assertEqual(self.c.get('/api/v1/me').json()['language'],'bn')
        i18n.catalog('bn')['period_not_found']='পাওয়া যায়নি'
        try:
            self.assertEqual(self.c.get(f'{self.base}/periods/999999/ledger').json()['detail'],'পাওয়া যায়নি')
            self.assertEqual(self.c.get(f'{self.base}/periods/999999/ledger',HTTP_ACCEPT_LANGUAGE='en').json()['detail'],catalog('en')['period_not_found'])   # the header wins
        finally:
            i18n.reload_catalogs()

    def test_switching_language_changes_words_never_figures(self):
        en=self.c.get(f'{self.base}/reports/customer-balances?month=2026-09-01',HTTP_ACCEPT_LANGUAGE='en').json()
        i18n.catalog('bn').update({'col_billed':'বিল','report_customer_balances':'গ্রাহকের বাকি'})
        try:
            bn=self.c.get(f'{self.base}/reports/customer-balances?month=2026-09-01',HTTP_ACCEPT_LANGUAGE='bn').json()
        finally:
            i18n.reload_catalogs()
        self.assertNotEqual(en['title'],bn['title']);self.assertEqual(bn['title'],'গ্রাহকের বাকি')
        self.assertEqual((en['rows'],en['totals'],en['column_ids']),(bn['rows'],bn['totals'],bn['column_ids']))

    def test_party_language_defaults_to_english(self):
        self.assertEqual(self.owner.language,'en')


class CsvPreambleTests(SimpleTestCase):
    def test_filter_names_follow_the_language_and_the_stamp_is_plain_digits(self):
        from ..services.reports import csv_stamp, filter_label
        self.assertEqual((filter_label('from'),filter_label('owner_id')),('From','Owner'))
        tokens=i18n.set_language('bn')
        try:self.assertEqual((filter_label('from'),filter_label('owner_id')),('শুরুর তারিখ','মালিক'))
        finally:i18n.reset_language(tokens)
        self.assertEqual(filter_label('unknown_filter'),'unknown_filter')
        self.assertRegex(csv_stamp(),r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
