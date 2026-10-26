package com.q4r.research;

import android.app.Activity;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.io.File;

public final class ReportActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        String id=getIntent().getStringExtra("case_id");
        if(id==null || !id.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,100}")){finish();return;}
        try {
            File root=new File(getFilesDir(),"cases").getCanonicalFile();
            File directory=new File(root,id).getCanonicalFile();
            File report=new File(directory,"report.html");
            if(!directory.getParentFile().equals(root)||!report.isFile()){finish();return;}
            WebView web=new WebView(this);
            web.setBackgroundColor(0xff0c1422);
            web.getSettings().setJavaScriptEnabled(true);
            web.getSettings().setAllowFileAccess(true);
            web.getSettings().setAllowFileAccessFromFileURLs(false);
            web.getSettings().setAllowUniversalAccessFromFileURLs(false);
            web.setWebViewClient(new WebViewClient());
            web.setOnApplyWindowInsetsListener((view,insets)->{view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;});
            setContentView(web);
            // A separate view with no native/Python bridge.
            web.loadUrl(android.net.Uri.fromFile(report).toString());
        } catch(Exception problem){finish();}
    }
}
