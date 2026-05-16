Firebase service account (local only — do not commit JSON keys)

1. Open https://console.firebase.google.com/
2. Select your AoneGt project (same app as the mobile FCM config).
3. Gear icon → Project settings → Service accounts.
4. Click "Generate new private key" → confirm → a .json file downloads.
5. Move/rename that file to:
   firebase-service-account.json
   (this folder: secrets/firebase-service-account.json)

6. In project root .env (already set if you copied from docs):
   GOOGLE_APPLICATION_CREDENTIALS=C:\Users\Administrator\Desktop\AoneGt\secrets\firebase-service-account.json

7. Restart: python manage.py runserver
   The "Firebase credentials not found" message should disappear.

Never commit firebase-service-account.json to git.