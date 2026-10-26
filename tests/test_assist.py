import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from q4r import assist
from q4r.app_service import AppService, ranked_report
from q4r.core import Target, build_plan, load_sites, prioritize_plan


class AssistanceTests(unittest.TestCase):
    def config(self, **extra):
        return assist.configure(dict(provider='compatible', endpoint='https://example.org/v1/chat/completions',
                                     model='example-model', key='private-test-key', consent=True, **extra))

    def test_provider_requires_consent_and_secure_noncredential_endpoint(self):
        base=dict(provider='compatible', model='demo', key='test', consent=True)
        for endpoint in ['http://example.org/api', 'https://me:secret@example.org/api', 'https://example.org/api?key=secret']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):assist.configure(dict(base,endpoint=endpoint))
        with self.assertRaises(ValueError):assist.configure(dict(base,endpoint='https://example.org',consent=False))
        self.assertIsNone(assist.configure(None))
        self.assertNotIn('private-test-key', repr(self.config()))

    def test_local_ollama_and_no_remote_plaintext(self):
        self.assertEqual(assist.configure(dict(provider='ollama',endpoint='http://127.0.0.1:11434/api/generate',
                                             model='local-model',consent=True)).provider,'ollama')
        with self.assertRaises(ValueError):assist.configure(dict(provider='ollama',endpoint='https://example.org',model='demo',consent=True))

    def test_query_drafts_preserve_complete_names_and_known_handles(self):
        target=Target(name='Example Full Researcher',username='demo_user')
        with patch('q4r.assist.generate',return_value={'queries':['site:facebook.com "demo_user"','"Example Full Researcher" Jordan']}):
            rows=assist.draft_queries(self.config(),target)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['origin']=='machine_generated_query' for r in rows))
        with patch('q4r.assist.generate',return_value={'queries':['"Full" Jordan']}),self.assertRaises(ValueError):
            assist.draft_queries(self.config(),target)

    def test_invented_citations_are_rejected_and_empty_evidence_never_calls_provider(self):
        rows={'findings':[dict(id='F0001',source='github',title='Fixture',summary='fixture',signals=[])]}
        with patch('q4r.assist.generate',return_value={'observations':[{'text':'invented','evidence_ids':['F9999']}]}),self.assertRaises(ValueError):
            assist.review(self.config(),rows)
        with patch('q4r.assist.generate') as call:
            self.assertEqual(assist.review(self.config(),{'findings':[]})['status'],'skipped');call.assert_not_called()

    def test_http_payload_keeps_key_out_of_body_and_disallows_redirects(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return json.dumps({'choices':[{'message':{'content':'{"queries":[]}'}}]}).encode()
        with patch('q4r.assist.build_opener') as factory:
            factory.return_value.open.return_value=Response()
            self.assertEqual(assist.generate(self.config(),'system',{'public':'fixture'}),{'queries':[]})
            request=factory.return_value.open.call_args.args[0]
            self.assertNotIn(b'private-test-key',request.data)
            self.assertEqual(request.get_header('Authorization'),'Bearer private-test-key')
            self.assertTrue(any(isinstance(h,assist.NoRedirect) for h in factory.call_args.args))

    def test_balanced_budget_retains_unscoped_inputs_and_multiple_platforms(self):
        target=Target(name='Example Full Researcher',username='demo_user',email='me@example.org',country='Jordan',aliases=['second_demo'])
        plan=list(build_plan(target,load_sites()))
        chosen=prioritize_plan(plan)[:12]
        self.assertIn('"demo_user"',[q['query'] for q in chosen])
        self.assertIn('"Example Full Researcher"',[q['query'] for q in chosen])
        self.assertIn('"second_demo"',[q['query'] for q in chosen])
        self.assertGreaterEqual(sum('site:' in q['query'] for q in chosen),5)
        self.assertEqual(len(prioritize_plan(plan)),len({q['query'] for q in plan}))

    def test_health_does_not_claim_no_account_when_index_missing(self):
        report=ranked_report(dict(findings=[],run=dict(mode='search',web_configured=False),
            coverage=[dict(source='brave',status='skipped',detail='set BRAVE_SEARCH_API_KEY')]))
        self.assertEqual(report['search_health']['index_status'],'not_configured')
        self.assertEqual(report['coverage'][0]['retrieved_count'],0)

    def test_real_service_integration_drafts_and_reviews_without_mutating_scores_or_saving_key(self):
        with tempfile.TemporaryDirectory() as folder:
            service=AppService(Path(folder)/'cases')
            def provider(case,client,limit):
                eid=case.record('github','https://example.org/profile',200,b'fixture',0)
                case.add('github','Example Researcher','https://example.org/profile','Synthetic evidence',eid,'demo_user',{'username':'demo_user'})
                return 'ok','Fixture collected'
            def generate(config,system,data):
                if 'identifiers' in data:return {'queries':['site:facebook.com "demo_user"']}
                return {'observations':[{'text':'Fixture has a matching handle; ownership needs review.',
                                          'evidence_ids':[data['evidence'][0]['id']]}],'limitations':['Synthetic fixture']}
            config=dict(username='demo_user',sources=['github'],ai=dict(provider='compatible',endpoint='https://example.org/api',
                       model='demo',key='private-test-key',consent=True))
            with patch.dict('q4r.cli.PROVIDERS',{'github':provider},clear=True),patch('q4r.assist.generate',side_effect=generate):
                case_id=service.start(config)['case_id'];service.worker.join(10)
            self.assertFalse(service.worker.is_alive());self.assertNotEqual(service.state['status'],'error',service.state)
            report=service.load_case(case_id)['report']
            self.assertEqual(report['assistance']['status'],'draft')
            self.assertEqual(report['findings'][0]['score'],25)
            self.assertTrue(any(p.get('input')=='ai_suggested' for p in report['plan']))
            self.assertNotIn('private-test-key',(service.case_path(case_id)/'report.json').read_text(encoding='utf-8'))
            self.assertNotIn('private-test-key',service.state['log'])


if __name__=='__main__':unittest.main()
