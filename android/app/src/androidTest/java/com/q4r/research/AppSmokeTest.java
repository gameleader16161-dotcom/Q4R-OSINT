package com.q4r.research;

import android.app.Instrumentation;
import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.graphics.Bitmap;
import android.webkit.WebView;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.espresso.intent.Intents;
import androidx.core.content.FileProvider;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import static androidx.test.espresso.intent.Intents.intending;
import static androidx.test.espresso.intent.matcher.IntentMatchers.hasAction;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public final class AppSmokeTest {
    private String js(Instrumentation instrument,WebView web,String script) throws Exception {
        CountDownLatch complete=new CountDownLatch(1);AtomicReference<String> value=new AtomicReference<>();
        instrument.runOnMainSync(()->web.evaluateJavascript(script,result->{value.set(result);complete.countDown();}));
        assertTrue("JavaScript callback",complete.await(10,TimeUnit.SECONDS));return value.get();
    }
    private void until(Instrumentation instrument,WebView web,String expression) throws Exception {
        long deadline=System.currentTimeMillis()+30000;
        while(System.currentTimeMillis()<deadline){if("true".equals(js(instrument,web,expression)))return;Thread.sleep(300);}
        fail("Timed out: "+expression+"; body="+js(instrument,web,"document.body.innerText"));
    }
    private void capture(Instrumentation instrument,Activity activity,String name) throws Exception {
        Bitmap screenshot=instrument.getUiAutomation().takeScreenshot();
        File output=new File(activity.getExternalFilesDir(null),name);
        try(FileOutputStream stream=new FileOutputStream(output)){screenshot.compress(Bitmap.CompressFormat.PNG,100,stream);}
    }
    @Test public void localEngineLanguageAndRankedInterface() throws Exception {
        Instrumentation instrument=InstrumentationRegistry.getInstrumentation();
        Intent launch=new Intent(instrument.getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        MainActivity activity=(MainActivity)instrument.startActivitySync(launch);
        Field field=MainActivity.class.getDeclaredField("web");field.setAccessible(true);WebView web=(WebView)field.get(activity);
        until(instrument,web,"document.getElementById('connectionNotice') && document.getElementById('connectionNotice').hidden");
        assertEquals("19",js(instrument,web,"document.querySelectorAll('#sourceChoices input').length"));
        js(instrument,web,"document.getElementById('languageButton').click()");
        assertEquals("\"ltr\"",js(instrument,web,"document.documentElement.dir"));
        js(instrument,web,"document.getElementById('languageButton').click()");
        capture(instrument,activity,"form-smoke.png");
        Intents.init();
        intending(hasAction(Intent.ACTION_OPEN_DOCUMENT)).respondWith(new Instrumentation.ActivityResult(Activity.RESULT_CANCELED,null));
        js(instrument,web,"document.getElementById('choosePhoto').click()");Thread.sleep(600);
        assertEquals("true",js(instrument,web,"document.getElementById('photoPreview').hidden"));
        Intents.release();Intents.init();
        File exports=new File(activity.getFilesDir(),"exports");exports.mkdirs();
        File reference=new File(exports,"reference-fixture.png");Bitmap pixel=Bitmap.createBitmap(4,4,Bitmap.Config.ARGB_8888);pixel.eraseColor(0xff90baa8);
        try(FileOutputStream stream=new FileOutputStream(reference)){pixel.compress(Bitmap.CompressFormat.PNG,100,stream);}
        Uri photo=FileProvider.getUriForFile(activity,activity.getPackageName()+".files",reference);
        intending(hasAction(Intent.ACTION_OPEN_DOCUMENT)).respondWith(new Instrumentation.ActivityResult(Activity.RESULT_OK,new Intent().setData(photo)));
        js(instrument,web,"document.getElementById('choosePhoto').click()");
        until(instrument,web,"document.getElementById('photoPreview').naturalWidth>0");
        js(instrument,web,"document.getElementById('fullName').value='Example Researcher';document.getElementById('username').value='example_demo';document.getElementById('offlineMode').checked=true;document.getElementById('fullName').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('startButton').click()");
        until(instrument,web,"document.getElementById('runStatusText').textContent==='خطة فقط؛ لم تُرسل طلبات بحث'");
        assertEquals("true",js(instrument,web,"!document.getElementById('exportCase').disabled"));
        assertTrue(new File(activity.getFilesDir(),"cases").listFiles().length>0);
        Instrumentation.ActivityMonitor reportMonitor=instrument.addMonitor(ReportActivity.class.getName(),null,false);
        js(instrument,web,"document.getElementById('openReport').click()");
        Activity report=instrument.waitForMonitorWithTimeout(reportMonitor,15000);assertNotNull("Report opens natively",report);
        AtomicReference<WebView> reportWeb=new AtomicReference<>();
        instrument.runOnMainSync(()->reportWeb.set((WebView)((android.view.ViewGroup)report.findViewById(android.R.id.content)).getChildAt(0)));
        until(instrument,reportWeb.get(),"document.querySelector('img') && document.querySelector('img').naturalWidth>0");
        instrument.runOnMainSync(report::finish);
        intending(hasAction(Intent.ACTION_CHOOSER)).respondWith(new Instrumentation.ActivityResult(Activity.RESULT_CANCELED,null));
        js(instrument,web,"document.getElementById('exportCase').click()");
        until(instrument,web,"!document.getElementById('toast').hidden");
        File[] archives=exports.listFiles((folder,name)->name.endsWith(".zip"));assertNotNull(archives);assertTrue(archives.length>0);
        try(java.util.zip.ZipFile archive=new java.util.zip.ZipFile(archives[0])){assertNotNull(archive.getEntry("report.json"));assertNotNull(archive.getEntry("attachments/reference.png"));}
        js(instrument,web,"document.getElementById('casesNav').click()");until(instrument,web,"document.querySelector('.case-row')!==null");
        js(instrument,web,"document.querySelector('.case-row').click()");until(instrument,web,"!document.getElementById('resultsPage').hidden && document.getElementById('casePhoto').naturalWidth>0");
        // Synthetic renderer fixture only; not measured search accuracy.
        js(instrument,web,"renderReport({target:{name:'Example Researcher · DEMO'},findings:[{id:'a',source:'github',title:'Fixture low',score:0,match_percent:0},{id:'b',source:'instagram',title:'Fixture high',score:90,match_percent:100},{id:'c',source:'bluesky',title:'Fixture middle',score:50,match_percent:56}],coverage:[]});renderRunState('complete')");
        assertEquals("\"100%,56%,0%\"",js(instrument,web,"[...document.querySelectorAll('.score-number')].map(x=>x.textContent).join(',')"));
        assertEquals("true",js(instrument,web,"document.documentElement.scrollWidth<=window.innerWidth"));
        capture(instrument,activity,"ui-smoke.png");
        Intents.release();
        instrument.runOnMainSync(activity::finish);
    }
}
