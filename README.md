# AoneGt - Django Auth API Starter

This project gives you the backend APIs for the screens you shared:
- Create Account
- Sign In (email step + password step)
- Forgot Password
- Reset Password with OTP

## Tech Stack
- Python
- Django
- Django REST Framework
- PostgreSQL
- JWT authentication

## Project Structure
```text
AoneGt/
├── aonegt/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── accounts/
│   ├── migrations/
│   ├── admin.py
│   ├── apps.py
│   ├── models.py
│   ├── serializers.py
│   ├── urls.py
│   └── views.py
├── manage.py
├── requirements.txt
└── .env.example
```

## APIs
Base URL: `http://127.0.0.1:8000/api/auth/`

### 1) Create Account
**POST** `register/`

Request:
```json
{
  "first_name": "Amina",
  "last_name": "Ali",
  "email": "amina@example.com",
  "password": "Test@1234"
}
```

Response:
```json
{
  "message": "Account created successfully.",
  "user": {
    "id": 1,
    "first_name": "Amina",
    "last_name": "Ali",
    "email": "amina@example.com",
    "created_at": "2026-03-27T10:00:00Z"
  }
}
```

### 2) Sign In - Step 1 Check Email
**POST** `check-email/`

Request:
```json
{
  "email": "amina@example.com"
}
```

Response:
```json
{
  "email": "amina@example.com",
  "exists": true,
  "message": "Email found. Continue to password screen."
}
```

### 3) Sign In - Step 2 Login
**POST** `login/`

Request:
```json
{
  "email": "amina@example.com",
  "password": "Test@1234"
}
```

Response:
```json
{
  "user": {
    "id": 1,
    "first_name": "Amina",
    "last_name": "Ali",
    "email": "amina@example.com"
  },
  "tokens": {
    "refresh": "<refresh_token>",
    "access": "<access_token>"
  }
}
```

### 4) Forgot Password
**POST** `forgot-password/`

Request:
```json
{
  "email": "amina@example.com"
}
```

Response:
```json
{
  "message": "Password reset OTP sent to email.",
  "email": "amina@example.com"
}
```

### 5) Reset Password
**POST** `reset-password/`

Request:
```json
{
  "email": "amina@example.com",
  "otp": "123456",
  "new_password": "NewPass@123",
  "confirm_password": "NewPass@123"
}
```

### 6) Profile
**GET** `profile/`

Headers:
```text
Authorization: Bearer <access_token>
```

## Setup Steps
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create PostgreSQL database:
```sql
CREATE DATABASE aonegt_db;
```

Run migrations:
```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Mobile App Screen Mapping
### Create Account screen
Use `POST /api/auth/register/`
- firstname -> `first_name`
- lastname -> `last_name`
- email -> `email`
- password -> `password`

### Sign In email screen
Use `POST /api/auth/check-email/`
- email field only

### Sign In password screen
Use `POST /api/auth/login/`
- send email from previous screen
- send password from password screen

### Forgot Password screen
Use `POST /api/auth/forgot-password/`
- send email
- backend generates OTP and sends email

## Notes
- Social login buttons shown in your design are UI only in this starter. Apple/Google/Facebook login needs OAuth setup later.
- By default, forgot password emails go to console because console email backend is safer for local testing.
- Change to SMTP in `.env` for real email sending.
