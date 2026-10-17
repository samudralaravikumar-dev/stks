package com.srk.tradingportal;

import android.annotation.SuppressLint;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

/**
 * Full-screen shell around the Streamlit portal. The analysis engine runs on the server; this app shows it
 * like a native app: remembered portal address, offline screen with Retry, back navigation, external
 * links in the browser, self-update from GitHub Releases (see Updater). Long-press the offline screen's "Change address" to point it somewhere else.
 */
public class MainActivity extends AppCompatActivity {
    private static final String PREFS = "portal";
    private static final String KEY_URL = "url";

    private WebView web;
    private SwipeRefreshLayout swipe;
    private ProgressBar progress;
    private View offline;
    private String portalHost = "";
    private Updater updater;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        swipe = findViewById(R.id.swipe);
        progress = findViewById(R.id.progress);
        offline = findViewById(R.id.offline);
        setupWebView();
        swipe.setOnRefreshListener(() -> web.reload());
        // Pull-to-refresh is off: Streamlit scrolls inside its own container, so web.getScrollY() is always 0 and
        // every downward drag reloaded the page (ending the session). The portal refreshes itself (sidebar Auto).
        swipe.setEnabled(false);
        ((Button) findViewById(R.id.retry)).setOnClickListener(v -> loadPortal());
        ((Button) findViewById(R.id.change)).setOnClickListener(v -> askForUrl(true));
        updater = new Updater(this);
        ((Button) findViewById(R.id.check_update)).setOnClickListener(v -> updater.check(true));
        updater.checkIfDue();

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else if (savedUrl().isEmpty()) {
            askForUrl(false);
        } else {
            loadPortal();
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);          // Streamlit is a JavaScript app
        s.setDomStorageEnabled(true);          // needed for its session handling
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);  // Streamlit Cloud serves the app in an iframe

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                String host = u.getHost() == null ? "" : u.getHost();
                boolean inside = host.equals(portalHost) || host.endsWith(".streamlit.app")
                        || host.endsWith("streamlit.io") || host.endsWith("github.com") || host.endsWith("githubusercontent.com");
                if (inside || !request.isForMainFrame()) {
                    return false;
                }
                startActivity(new Intent(Intent.ACTION_VIEW, u));  // news links etc. open in the browser
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                progress.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                swipe.setRefreshing(false);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showOffline(String.valueOf(error.getDescription()));
                }
            }
        });
    }

    private String savedUrl() {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, "");
    }

    private void loadPortal() {
        String url = savedUrl();
        if (url.isEmpty()) {
            askForUrl(false);
            return;
        }
        portalHost = Uri.parse(url).getHost() == null ? "" : Uri.parse(url).getHost();
        offline.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(url);
    }

    private void showOffline(String why) {
        progress.setVisibility(View.GONE);
        swipe.setRefreshing(false);
        web.setVisibility(View.GONE);
        offline.setVisibility(View.VISIBLE);
        ((TextView) findViewById(R.id.offline_detail)).setText(getString(R.string.offline_detail, why));
    }

    private void askForUrl(boolean cancellable) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://your-app.streamlit.app");
        input.setText(savedUrl());
        input.setSingleLine(true);
        LinearLayout box = new LinearLayout(this);
        box.setPadding(48, 16, 48, 0);
        box.addView(input, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));
        AlertDialog.Builder b = new AlertDialog.Builder(this)
                .setTitle(R.string.ask_title)
                .setMessage(R.string.ask_message)
                .setView(box)
                .setCancelable(cancellable)
                .setPositiveButton(R.string.open, (d, w) -> {
                    String url = input.getText().toString().trim();
                    if (!url.isEmpty() && !url.startsWith("http")) {
                        url = "https://" + url;
                    }
                    if (url.startsWith("http://")) {
                        url = "https://" + url.substring(7);   // portal must be served over HTTPS
                    }
                    SharedPreferences.Editor e = getSharedPreferences(PREFS, MODE_PRIVATE).edit();
                    e.putString(KEY_URL, url).apply();
                    loadPortal();
                });
        if (cancellable) {
            b.setNegativeButton(android.R.string.cancel, null);
        }
        b.show();
    }

    @Override
    public void onBackPressed() {
        if (web.getVisibility() == View.VISIBLE && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        updater.onResume();  // back from the "Install unknown apps" setting: continue the pending update
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();  // persist the sign-in cookie now, not whenever WebView gets to it
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }
}
