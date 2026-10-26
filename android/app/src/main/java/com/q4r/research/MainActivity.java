package com.q4r.research;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.net.Uri;
import android.os.Bundle;
import android.util.Base64;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.core.content.FileProvider;
import androidx.webkit.WebViewAssetLoader;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private WebView web;
    private final ExecutorService executor = Executors.newFixedThreadPool(2);
    private String pendingPhoto;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true);
        web.setBackgroundColor(Color.rgb(12,20,34));
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setJavaScriptCanOpenWindowsAutomatically(false);
        web.getSettings().setMixedContentMode(android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW);
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(), insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        WebViewAssetLoader assets = new WebViewAssetLoader.Builder().addPathHandler("/assets/", new WebViewAssetLoader.AssetsPathHandler(this)).build();
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) { return assets.shouldInterceptRequest(request.getUrl()); }
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !request.getUrl().toString().startsWith("https://appassets.androidplatform.net/assets/ui/");
            }
        });
        web.addJavascriptInterface(new Host(), "Android");
        NativeLayout.install(this, web);
        web.loadUrl("https://appassets.androidplatform.net/assets/ui/index.html");
    }

    private String engine(String action, JSONObject payload) throws Exception {
        synchronized (Python.class) { if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext())); }
        JSONObject request = new JSONObject().put("action",action).put("payload",payload);
        return Python.getInstance().getModule("q4r.app_service").callAttr("android_request", new File(getFilesDir(),"cases").toString(), request.toString()).toString();
    }

    private void reply(String id, String result) {
        runOnUiThread(() -> { if (!isFinishing() && web != null) web.evaluateJavascript("window.q4rReceive("+JSONObject.quote(id)+","+JSONObject.quote(result)+")",null); });
    }

    private void error(String id, Exception problem) {
        try { reply(id,new JSONObject().put("ok",false).put("error",problem.getMessage()==null?"Request failed":problem.getMessage()).toString()); }
        catch(Exception ignored) { }
    }

    private void success(String id, Object value) throws Exception { reply(id,new JSONObject().put("ok",true).put("data",value==null?JSONObject.NULL:value).toString()); }

    private final class Host {
        @JavascriptInterface public void postMessage(String raw) {
            if (raw == null || raw.length()>10*1024*1024) return;
            try {
                JSONObject request = new JSONObject(raw);
                String id = request.getString("id");
                String action = request.getString("action");
                JSONObject payload = request.optJSONObject("payload");
                if (payload==null) payload=new JSONObject();
                if (action.equals("choose_photo")) {
                    runOnUiThread(() -> { try {
                        if (pendingPhoto!=null) throw new IllegalStateException("An image picker is already open");
                        pendingPhoto=id;
                        Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("image/*").putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"image/png","image/jpeg","image/webp"}).addCategory(Intent.CATEGORY_OPENABLE);
                        startActivityForResult(intent,42);
                    } catch(Exception e) { pendingPhoto=null;error(id,e); } });
                    return;
                }
                final JSONObject data=payload;
                executor.execute(() -> { try {
                    if (action.equals("open_url")) {
                        JSONObject result = new JSONObject(engine("open_url",data));
                        if (!result.getBoolean("ok")) { reply(id,result.toString()); return; }
                        Uri url=Uri.parse(result.getString("data"));
                        runOnUiThread(() -> { try { startActivity(new Intent(Intent.ACTION_VIEW,url));success(id,true); } catch(Exception e){error(id,e);} });
                    } else if (action.equals("open_report")) {
                        String caseId=data.getString("case_id");
                        if (!caseId.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,100}")) throw new IllegalArgumentException("Invalid case identifier");
                        JSONObject result = new JSONObject(engine("report_path",data));
                        if (!result.getBoolean("ok")) { reply(id,result.toString());return; }
                        runOnUiThread(() -> { try { startActivity(new Intent(MainActivity.this,ReportActivity.class).putExtra("case_id",caseId));success(id,true); } catch(Exception e){error(id,e);} });
                    } else if (action.equals("export")) {
                        JSONObject result = new JSONObject(engine("export",data));
                        if (!result.getBoolean("ok")) { reply(id,result.toString());return; }
                        File archive=new File(result.getString("data"));
                        Uri uri=FileProvider.getUriForFile(MainActivity.this,getPackageName()+".files",archive);
                        runOnUiThread(() -> { try {
                            Intent share=new Intent(Intent.ACTION_SEND).setType("application/zip").putExtra(Intent.EXTRA_STREAM,uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                            startActivity(Intent.createChooser(share,"Export Q4R case"));success(id,true);
                        } catch(Exception e){error(id,e);} });
                    } else reply(id,engine(action,data));
                } catch(Exception problem){error(id,problem);} });
            } catch(Exception ignored){ }
        }
    }

    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data) {
        super.onActivityResult(requestCode,resultCode,data);
        if(requestCode!=42 || pendingPhoto==null)return;
        String id=pendingPhoto;pendingPhoto=null;
        if(resultCode!=RESULT_OK || data==null || data.getData()==null){try{success(id,null);}catch(Exception ignored){}return;}
        Uri uri=data.getData();
        executor.execute(() -> { try {
            BitmapFactory.Options bounds=new BitmapFactory.Options();bounds.inJustDecodeBounds=true;
            try(InputStream input=getContentResolver().openInputStream(uri)){BitmapFactory.decodeStream(input,null,bounds);}
            if(bounds.outWidth<1 || bounds.outHeight<1 || (long)bounds.outWidth*bounds.outHeight>45000000L)throw new IllegalArgumentException("Choose an image smaller than 45 megapixels");
            BitmapFactory.Options options=new BitmapFactory.Options();options.inSampleSize=1;
            while(Math.max(bounds.outWidth,bounds.outHeight)/options.inSampleSize>2800)options.inSampleSize*=2;
            Bitmap source;
            try(InputStream input=getContentResolver().openInputStream(uri)){source=BitmapFactory.decodeStream(input,null,options);}
            if(source==null)throw new IllegalArgumentException("This image could not be read");
            Matrix rotation=new Matrix();
            try(InputStream input=getContentResolver().openInputStream(uri)) {
                int orientation=new ExifInterface(input).getAttributeInt(ExifInterface.TAG_ORIENTATION,1);
                if(orientation==6)rotation.postRotate(90);else if(orientation==3)rotation.postRotate(180);else if(orientation==8)rotation.postRotate(270);
            } catch(Exception ignored){}
            Bitmap oriented=Bitmap.createBitmap(source,0,0,source.getWidth(),source.getHeight(),rotation,true);
            double scale=Math.min(1.0,1400.0/Math.max(oriented.getWidth(),oriented.getHeight()));
            Bitmap scaled=Bitmap.createScaledBitmap(oriented,Math.max(1,(int)(oriented.getWidth()*scale)),Math.max(1,(int)(oriented.getHeight()*scale)),true);
            Bitmap clean=Bitmap.createBitmap(scaled.getWidth(),scaled.getHeight(),Bitmap.Config.ARGB_8888);
            Canvas canvas=new Canvas(clean);canvas.drawColor(Color.WHITE);canvas.drawBitmap(scaled,0,0,null);
            ByteArrayOutputStream output=new ByteArrayOutputStream();clean.compress(Bitmap.CompressFormat.PNG,100,output);
            if(output.size()>6*1024*1024)throw new IllegalArgumentException("Image is too large");
            JSONObject value=new JSONObject().put("data_url","data:image/png;base64,"+Base64.encodeToString(output.toByteArray(),Base64.NO_WRAP));
            success(id,value);
            clean.recycle();if(scaled!=oriented)scaled.recycle();if(oriented!=source)oriented.recycle();source.recycle();
        } catch(Exception problem){error(id,problem);} });
    }

    @Override protected void onDestroy(){
        if(isFinishing())executor.execute(() -> { try{engine("cancel",new JSONObject());}catch(Exception ignored){} });
        super.onDestroy();
    }
}
