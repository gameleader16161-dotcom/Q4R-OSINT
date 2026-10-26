package com.q4r.research;

import android.app.Activity;
import android.webkit.WebView;
import android.widget.FrameLayout;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

final class NativeLayout {
    private NativeLayout() {}
    static void install(Activity activity, WebView web) {
        // WebView does not reliably respect its own padding. Inset its parent.
        FrameLayout root = new FrameLayout(activity);
        root.setBackgroundColor(0xff0c1422);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets keyboard = insets.getInsets(WindowInsetsCompat.Type.ime());
            view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, keyboard.bottom));
            return insets;
        });
        activity.setContentView(root);
        ViewCompat.requestApplyInsets(root);
    }
}
