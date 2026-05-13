# PROJECT.md — Project Overview

> This is a high-level overview of the project.
> For full technical details, refer to README.md (master source of truth).

---

## What Is This?

A **Flutter mobile app (Android + iOS)** for a client who runs multiple **Zoho Commerce** online stores.
The app is a mobile storefront — users browse products across different organizations, manage carts and wishlists, and place orders.
Backend is **Django**, database is **PostgreSQL**.

**Current Phase:** Production

---

## Core Tech

| Layer | Tech |
|---|---|
| Mobile App | Flutter (Android + iOS) |
| Backend | Django REST Framework |
| Database | PostgreSQL |
| Commerce | Zoho Commerce API |
| Billing | Zoho Books |

---

## The Big Picture — How It Works

1. User signs up / logs in → handled entirely by our Django backend (not Zoho)
2. User sees multiple organization tiles on the home screen
3. User picks an org → browses categories → browses products
4. User adds to cart / wishlist (isolated per org, no data leakage between orgs)
5. User checks out → selects address → selects payment → places order
6. All Zoho API calls go through Django backend — Flutter never calls Zoho directly

---

## Organizations

- Client has multiple Zoho Commerce stores, each = one "Organization" in the app
- Currently 3 live, 2 more coming
- Cart and Wishlist are completely separate per organization

---

## What Is Done vs Not Done

| Feature | Status |
|---|---|
| Authentication (Sign In, Sign Up, Reset Password) | Done |
| Home page with org tiles | Done |
| Category listing per org | Done |
| Product listing + search | Done |
| Product detail page | Done |
| Cart (per org) | Done |
| Wishlist (per org) | Done |
| Checkout - Address selection | Done |
| Checkout - Cash on Delivery | Done |
| Banner images on home page | Not done (dummy data only) |
| Best Deals section | Not done (hardcoded placeholder) |
| Other payment methods | Not done |
| 2 new organizations | Not added yet |

> For detailed breakdown of each feature, refer to README.md

---

## Non-Negotiable Rules (Every Agent Must Know)

1. Flutter never calls Zoho APIs directly — always through Django backend
2. Auth is our own logic — Django + PostgreSQL, nothing to do with Zoho
3. Cart and Wishlist are always scoped to org_id + user_id — never shared across orgs
4. Do not implement other payment methods until explicitly told
5. Banner and Best Deals are not implemented — do not assume or add logic for them
6. Do not break anything currently working — see AGENT_STARTER_PROMPT.md for full rules
