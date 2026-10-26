import base64
import binascii
from contextlib import redirect_stdout
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
import zlib

from q4r.app_service import AppService, ranked_report
from q4r.cli import verify
from q4r.photos import clean_reference_png, decode_photo, SIGNATURE


def chunk(kind,data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',binascii.crc32(kind+data)&0xffffffff)


def photo(extra=b''):
    return SIGNATURE+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+extra+chunk(b'IDAT',zlib.compress(b'\0'+b'\x80\x90\xa0'*2+b'\0'+b'\x80\x90\xa0'*2))+chunk(b'IEND',b'')


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.service=AppService(self.root/'cases')

    def test_photo_removes_all_ancillary_metadata_and_keeps_pixels(self):
        raw=photo(chunk(b'tEXt',b'GPS\0private-location')+chunk(b'eXIf',b'private-metadata'))
        cleaned,width,height=clean_reference_png(raw)
        self.assertEqual((width,height),(2,2))
        self.assertNotIn(b'GPS',cleaned);self.assertNotIn(b'private',cleaned)
        self.assertEqual(cleaned,photo())

    def test_photo_validation_rejects_corrupt_non_png_or_decompression_mismatch(self):
        for raw in (b'JPEG',photo()[:-1],photo()+b'junk',photo().replace(b'IHDR',b'PHDR'),
                    SIGNATURE+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'x'*5000))+chunk(b'IEND',b'')):
            with self.subTest(size=len(raw)),self.assertRaises(ValueError):clean_reference_png(raw)
        with self.assertRaises(ValueError):decode_photo('data:image/jpeg;base64,aaaa')

    def test_ranked_results_always_descend_and_percentages_are_not_probabilities(self):
        report=ranked_report({'target':{},'findings':[{'score':0},{'score':25},{'score':90},{'score':50},{'score':float('nan')},{'score':'bad'}]})
        self.assertEqual([r['score'] for r in report['findings']],[90,50,25,0,0,0])
        self.assertEqual([r['match_percent'] for r in report['findings'][:3]],[100,56,28])
        self.assertIn('not probability',report['score_interpretation'])

    def test_offline_photo_case_saves_report_hashes_and_reopens_without_requests(self):
        config=dict(name='Example Researcher',username='example',email='me@example.org',offline=True,sources=['github','instagram'],
                    photo='data:image/png;base64,'+base64.b64encode(photo(chunk(b'tEXt',b'GPS\0do-not-copy'))).decode())
        with patch('q4r.http.Client.fetch',side_effect=AssertionError('Network used')):
            case_id=self.service.start(config)['case_id'];self.service.worker.join(10)
        self.assertFalse(self.service.worker.is_alive())
        self.assertEqual(self.service.state['status'],'planned')
        data=self.service.load_case(case_id)
        self.assertEqual(data['report']['requests'],[])
        self.assertTrue(data['photo'].startswith('data:image/png;base64,'))
        directory=self.root/'cases'/case_id
        manifest=json.loads((directory/'manifest.json').read_text())
        self.assertEqual(manifest['attachments/reference.png'],hashlib.sha256(photo()).hexdigest())
        self.assertIn('Reference attachment',(directory/'report.html').read_text())
        with redirect_stdout(io.StringIO()):self.assertEqual(verify(directory),0)
        self.assertEqual(self.service.list_cases()[0]['id'],case_id)

    def test_empty_sources_or_country_only_do_not_start_a_search(self):
        for config in (dict(username='demo',sources=[]),dict(country='Jordan'),dict(username='demo',sources=['not-a-provider'])):
            with self.subTest(config=config),self.assertRaises(ValueError):self.service.start(config)
        self.assertEqual(list((self.root/'cases').iterdir()),[])

    def test_android_disables_playwright_but_supports_the_same_offline_engine(self):
        android=AppService(self.root/'android','android')
        self.assertFalse(android.handle('bootstrap')['browser_available'])
        with self.assertRaises(ValueError):android.start(dict(username='demo',browser=True))
        android.start(dict(username='demo',offline=True,sources=['instagram']))
        android.worker.join(10)
        self.assertEqual(android.state['status'],'planned')

    def test_failed_or_unknown_bridge_request_returns_a_structured_error(self):
        self.assertFalse(json.loads(self.service.request('{broken'))['ok'])
        self.assertFalse(json.loads(self.service.request('{"action":"unknown"}'))['ok'])
        for value in ('../outside','..','a/b','C:/outside'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.service.case_path(value)
        with self.assertRaises(ValueError):self.service.handle('open_url',{'url':'file:///private.txt'})

    def test_cancellation_preserves_a_partial_report_and_rejects_parallel_search(self):
        entered=threading.Event()
        def provider(case,client,limit):
            entered.set();client.cancelled.wait(3)
            return 'skipped','User stopped this fixture'
        with patch.dict('q4r.cli.PROVIDERS',{'github':provider},clear=True):
            case_id=self.service.start(dict(username='demo',sources=['github']))['case_id']
            self.assertTrue(entered.wait(3))
            with self.assertRaises(ValueError):self.service.start(dict(username='other'))
            self.service.handle('cancel');self.service.worker.join(10)
        self.assertEqual(self.service.state['status'],'cancelled')
        data=self.service.load_case(case_id)['report']
        self.assertTrue(any(r['status']=='interrupted' for r in data['coverage']))

    def test_session_api_key_is_restored_and_never_saved_in_a_report(self):
        def provider(case,client,limit):
            import os
            self.assertEqual(os.environ.get('BRAVE_SEARCH_API_KEY'),'session-secret-fixture')
            return 'ok','fixture complete'
        with patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':'original-fixture'}),patch.dict('q4r.cli.PROVIDERS',{'github':provider},clear=True):
            case_id=self.service.start(dict(username='demo',sources=['github'],brave_key='session-secret-fixture'))['case_id']
            self.service.worker.join(10)
            import os
            self.assertEqual(os.environ['BRAVE_SEARCH_API_KEY'],'original-fixture')
        report=(self.root/'cases'/case_id/'report.json').read_text()
        self.assertNotIn('session-secret-fixture',report)
        self.assertNotIn('session-secret-fixture',self.service.state['log'])

    def test_case_export_includes_reports_but_excludes_unrelated_files(self):
        case_id=self.service.start(dict(username='demo',offline=True,sources=['github']))['case_id']
        self.service.worker.join(10)
        directory=self.service.case_path(case_id)
        (directory/'.env').write_text('not-case-evidence')
        export=self.service.export_case(case_id)
        with zipfile.ZipFile(export) as archive:
            self.assertIn('report.json',archive.namelist())
            self.assertNotIn('.env',archive.namelist())

    def test_saved_cancelled_case_restores_actual_status_and_attempted_coverage(self):
        data=ranked_report(dict(target={},findings=[],run=dict(outcome='cancelled',total_sources=19),
                                coverage=[dict(source='github',status='ok'),dict(source='run',status='interrupted')],
                                requests=[dict(source='github')]))
        self.assertEqual(data['run_summary'],dict(status='cancelled',completed=1,total=19))
        legacy=ranked_report(dict(target={},findings=[],coverage=[dict(source='run',status='interrupted')]))
        self.assertEqual(legacy['run_summary']['status'],'cancelled')
        self.assertIsNone(legacy['run_summary']['total'])

    def test_plan_only_and_partial_saved_cases_are_not_labeled_successful_searches(self):
        planned=ranked_report(dict(target={},findings=[],run=dict(outcome='planned',total_sources=19),requests=[],coverage=[]))
        self.assertEqual(planned['run_summary'],dict(status='planned',completed=0,total=19))
        partial=ranked_report(dict(target={},findings=[],run=dict(outcome='complete',total_sources=2),coverage=[dict(source='github',status='ok'),dict(source='reddit',status='blocked')]))
        self.assertEqual(partial['run_summary']['status'],'partial')


if __name__=='__main__':unittest.main()
