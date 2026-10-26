"""Real Chromium UI smoke with the real local service and synthetic source fixtures."""
import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from contextlib import contextmanager

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from q4r.app_service import AppService


@contextmanager
def managed_service(root):
    service=AppService(root)
    try:yield service
    finally:
        service.cancel.set()
        if service.worker:service.worker.join(20)


def run(output, chrome=None):
    from PIL import Image, ImageDraw
    import io
    output.mkdir(parents=True,exist_ok=True)
    pixels=Image.new('RGB',(400,320),'#d6e6e0')
    draw=ImageDraw.Draw(pixels);draw.rectangle((55,55,345,265),fill='#49675f');draw.rectangle((80,80,320,240),outline='#b9dccd',width=3)
    draw.text((135,150),'LOCAL REFERENCE',fill='#edf6f0')
    stream=io.BytesIO();pixels.save(stream,format='PNG')
    photo='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
    with tempfile.TemporaryDirectory(prefix='q4r-ui-') as temp, managed_service(Path(temp)/'cases') as service:
        def host(raw):
            request=json.loads(raw)
            action=request['action']
            if action=='choose_photo':return json.dumps(dict(ok=True,data=dict(data_url=photo)))
            if action in ('open_report',):return json.dumps(dict(ok=True,data=True))
            return service.request(json.dumps(dict(action=action,payload=request.get('payload'))))
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=chrome)
            page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
            page.expose_function('__q4rHost',host)
            page.add_init_script('''window.Android={postMessage:async raw=>{const r=JSON.parse(raw);window.q4rReceive(r.id,await window.__q4rHost(raw));}};''')
            page.goto((Path(__file__).resolve().parents[1]/'q4r/ui/index.html').as_uri())
            page.wait_for_function('()=>document.getElementById("connectionNotice").hidden')
            assert page.locator('#sourceChoices input').count()==19
            page.screenshot(path=str(output/'desktop-form.png'),full_page=True)
            page.locator('#languageButton').click();assert page.locator('html').get_attribute('dir')=='ltr'
            assert page.locator('h1').first.text_content()=='Start a new search'
            page.screenshot(path=str(output/'desktop-form-english.png'),full_page=True)
            page.locator('#languageButton').click();page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.screenshot(path=str(output/'phone-form.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('#fullName').fill('Single');page.locator('#startButton').click()
            assert not page.locator('#error').is_hidden()
            page.locator('#fullName').fill('Example Researcher');page.locator('#username').fill('example_demo')
            page.locator('#email').fill('me@example.org');page.locator('#country').fill('Jordan')
            page.locator('#choosePhoto').click();page.wait_for_function('()=>document.getElementById("photoPreview").naturalWidth>0')
            page.locator('#sourceChoices input').evaluate_all("nodes=>nodes.forEach(n=>n.checked=['github','instagram','bluesky'].includes(n.value))")
            def source(name):
                def collect(case,client,limit):
                    attrs={'username':'example_demo','name':'Example Researcher'} if name!='bluesky' else {'username':'other_fixture','name':'Other fixture'}
                    if name=='github':attrs['email']='me@example.org'
                    eid=case.record(name,'https://example.org/'+name,200,json.dumps({'synthetic_fixture':True,**attrs}).encode(),0)
                    case.add(name,('Example Researcher' if name!='bluesky' else 'Other public profile')+' · DEMO',
                             'https://example.org/'+name+'/demonstration','Synthetic UI demonstration. Not a real profile.',eid,'fixture',attrs,kind='public_profile')
                    return 'ok','Synthetic offline source fixture'
                return collect
            with patch.dict('q4r.cli.PROVIDERS',{name:source(name) for name in ('github','instagram','bluesky')}),patch('q4r.http.Client.fetch',side_effect=AssertionError('Live network used')):
                page.locator('#startButton').click()
                page.wait_for_function('()=>document.querySelectorAll(".finding").length===3 && !document.getElementById("exportCase").disabled',timeout=30000)
                assert page.locator('.score-number').all_text_contents()==['100%','56%','0%']
                assert page.locator('#casePhoto').get_attribute('src').startswith('data:image/png')
                page.screenshot(path=str(output/'desktop-results.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                page.screenshot(path=str(output/'phone-results.png'),full_page=True)
                page.locator('#filterResults').fill('Instagram');assert page.locator('.finding').count()==1
                page.locator('#filterResults').fill('');assert page.locator('.finding').count()==3
                page.locator('#exportCase').click();page.wait_for_function('()=>!document.getElementById("toast").hidden')
                page.locator('#casesNav').click();page.wait_for_selector('.case-row')
                page.locator('.case-row').first.click();page.wait_for_selector('.finding')
                assert page.locator('.score-number').all_text_contents()==['100%','56%','0%']
            # Persisted cancellation must remain cancelled after reopening in either language.
            report_path=next((Path(temp)/'cases').glob('*/report.json'))
            saved=json.loads(report_path.read_text(encoding='utf-8'))
            saved['run']={'outcome':'cancelled','total_sources':19,'processed_sources':1}
            saved['coverage'].append({'source':'interrupted','status':'cancelled','detail':'UI lifecycle regression fixture'})
            report_path.write_text(json.dumps(saved),encoding='utf-8')
            page.locator('#casesNav').click();page.wait_for_selector('.case-row')
            page.locator('.case-row').first.click()
            page.wait_for_function("()=>document.getElementById('runStatusText').textContent.includes('أُوقف') || document.getElementById('runStatusText').textContent.includes('توقف')")
            assert page.locator('#runProgress').evaluate('n=>n.value')<100
            page.screenshot(path=str(output/'cancelled-ar.png'),full_page=True)
            page.locator('#languageButton').click()
            assert 'stopped' in page.locator('#runStatusText').text_content().lower() or 'cancelled' in page.locator('#runStatusText').text_content().lower()
            page.screenshot(path=str(output/'cancelled-en.png'),full_page=True)
            browser.close()
    print('PASS: real Chromium; Arabic/English, photo attachment, ranked results, phone layout, filtering, export and saved-case reopening.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--chrome')
    a=p.parse_args();run(a.output,a.chrome)
