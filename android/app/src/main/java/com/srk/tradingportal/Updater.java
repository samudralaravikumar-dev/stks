package com.srk.tradingportal;

import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Self-update from the repo's GitHub Releases. CI publishes every build signed with the app's key as the latest
 * release, with version.json next to the APK. The app compares versionCode, downloads the APK and hands it to the
 * system installer. Android still asks the user to confirm (and, once, to allow "Install unknown apps"), and it only
 * accepts an APK signed with the same key as the installed app, so a tampered file cannot replace it.
 */
final class Updater {
    private static final String BASE = "https://github.com/samudralaravikumar-dev/stks/releases/latest/download/";
    private static final String PREFS = "portal";
    private static final String KEY_LAST = "update_checked";
    private static final long EVERY_MS = 6 * 3600 * 1000L;

    private final AppCompatActivity act;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private File pending;  // downloaded APK waiting for the "Install unknown apps" permission

    Updater(AppCompatActivity act) {
        this.act = act;
    }

    /** Silent background check on launch, at most every 6 hours. */
    void checkIfDue() {
        SharedPreferences p = act.getSharedPreferences(PREFS, AppCompatActivity.MODE_PRIVATE);
        long now = System.currentTimeMillis();
        if (now - p.getLong(KEY_LAST, 0) < EVERY_MS) {
            return;
        }
        p.edit().putLong(KEY_LAST, now).apply();
        check(false);
    }

    /** manual=true reports "up to date" and errors; the automatic check stays quiet unless there is an update. */
    void check(boolean manual) {
        io.execute(() -> {
            try {
                JSONObject j = new JSONObject(new String(fetch(BASE + "version.json"), "UTF-8"));
                long latest = j.getLong("versionCode");
                String name = j.optString("versionName", String.valueOf(latest));
                String apk = j.optString("apk", BASE + "SRK-Options.apk");
                if (latest > currentCode()) {
                    main.post(() -> offer(name, apk));
                } else if (manual) {
                    toast("You have the latest version (" + currentName() + ")");
                }
            } catch (Exception e) {
                if (manual) {
                    toast("Update check failed: " + e.getMessage());
                }
            }
        });
    }

    /** Call from the activity's onResume: finishes an install that was waiting for the permission. */
    void onResume() {
        if (pending != null && canInstall()) {
            File f = pending;
            pending = null;
            install(f);
        }
    }

    private void offer(String name, String apkUrl) {
        if (act.isFinishing()) {
            return;
        }
        new AlertDialog.Builder(act)
                .setTitle("Update available")
                .setMessage("SRK Options " + name + " is ready (you have " + currentName() + ").\n\n"
                        + "Download and install it now? Your sign-in and portal address are kept.")
                .setPositiveButton("Update now", (d, w) -> download(apkUrl))
                .setNegativeButton("Later", null)
                .show();
    }

    private void download(String apkUrl) {
        ProgressBar bar = new ProgressBar(act, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        bar.setIndeterminate(true);
        TextView label = new TextView(act);
        label.setText("Downloading…");
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(48, 24, 48, 8);
        box.addView(label);
        box.addView(bar);
        AlertDialog dlg = new AlertDialog.Builder(act).setTitle("Updating SRK Options").setView(box)
                .setCancelable(false).show();
        io.execute(() -> {
            File dir = new File(act.getCacheDir(), "updates");
            File out = new File(dir, "SRK-Options.apk");
            try {
                if (!dir.isDirectory() && !dir.mkdirs()) {
                    throw new IOException("cannot create " + dir);
                }
                HttpURLConnection c = open(apkUrl);
                long total = c.getContentLengthLong();
                try (InputStream in = c.getInputStream(); OutputStream os = new FileOutputStream(out)) {
                    byte[] buf = new byte[64 * 1024];
                    long done = 0;
                    int n;
                    int lastPct = -1;
                    while ((n = in.read(buf)) > 0) {
                        os.write(buf, 0, n);
                        done += n;
                        if (total > 0) {
                            int pct = (int) (done * 100 / total);
                            if (pct != lastPct) {
                                lastPct = pct;
                                main.post(() -> {
                                    bar.setIndeterminate(false);
                                    bar.setProgress(pct);
                                    label.setText("Downloading… " + pct + "%");
                                });
                            }
                        }
                    }
                } finally {
                    c.disconnect();
                }
                if (out.length() < 100_000) {
                    throw new IOException("download incomplete");
                }
                main.post(() -> {
                    dlg.dismiss();
                    install(out);
                });
            } catch (Exception e) {
                main.post(() -> {
                    dlg.dismiss();
                    toast("Update download failed: " + e.getMessage());
                });
            }
        });
    }

    private void install(File apk) {
        if (!canInstall()) {
            pending = apk;
            new AlertDialog.Builder(act)
                    .setTitle("Allow updates")
                    .setMessage("Android needs your permission once to let SRK Options install its own updates. "
                            + "Turn on \"Allow from this source\", then come back.")
                    .setPositiveButton("Open settings", (d, w) -> act.startActivity(new Intent(
                            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                            Uri.parse("package:" + act.getPackageName()))))
                    .setNegativeButton("Later", (d, w) -> pending = null)
                    .show();
            return;
        }
        Uri uri = FileProvider.getUriForFile(act, act.getPackageName() + ".updates", apk);
        Intent i = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        act.startActivity(i);
    }

    private boolean canInstall() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.O || act.getPackageManager().canRequestPackageInstalls();
    }

    private long currentCode() throws PackageManager.NameNotFoundException {
        PackageInfo pi = act.getPackageManager().getPackageInfo(act.getPackageName(), 0);
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.P ? pi.getLongVersionCode() : pi.versionCode;
    }

    private String currentName() {
        try {
            return act.getPackageManager().getPackageInfo(act.getPackageName(), 0).versionName;
        } catch (PackageManager.NameNotFoundException e) {
            return "?";
        }
    }

    private static HttpURLConnection open(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(30000);
        c.setInstanceFollowRedirects(true);  // releases/latest/download/... redirects (https -> https)
        c.setRequestProperty("User-Agent", "SRK-Options-Android");
        int code = c.getResponseCode();
        if (code != HttpURLConnection.HTTP_OK) {
            c.disconnect();
            throw new IOException("HTTP " + code);
        }
        return c;
    }

    private static byte[] fetch(String url) throws IOException {
        HttpURLConnection c = open(url);
        try (InputStream in = c.getInputStream(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
            }
            return out.toByteArray();
        } finally {
            c.disconnect();
        }
    }

    private void toast(String msg) {
        main.post(() -> Toast.makeText(act, msg, Toast.LENGTH_LONG).show());
    }
}
