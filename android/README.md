# SRK Options: Android app

A full-screen WebView shell around the Streamlit portal. All analysis runs on the server.

## Build
GitHub Actions builds the APK on every push that touches `android/`, or on demand from
**Actions → Build Android app → Run workflow**. Download it from the run's **Artifacts** (`SRK-Options-apk-v1.1.N`).
To build locally (JDK 17 and the Android SDK required), run `gradlew assembleRelease` in this folder.

## Signing and updates
Release APKs are signed with one private key, supplied to CI by two repository secrets:
`ANDROID_KEYSTORE_B64` (the base64 of `app/signing.p12`) and `ANDROID_KEYSTORE_PASSWORD`.
Every build has a higher version code (the CI run number), so a new APK installs over the old one.
The key files are git-ignored and must never be committed. Keep a backup of `signing.p12`: if it is lost,
the next APK cannot update the installed app, which then has to be uninstalled first.
Without the secrets the build still succeeds, but it is signed with a temporary debug key.
