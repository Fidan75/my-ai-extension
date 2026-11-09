
import os
import time
import json
import hmac
import hashlib
import urllib.request
import urllib.error

import jwt
import psycopg

from jwt import PyJWKClient
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from google import genai


# --------------------------------------------------
# ENVIRONMENT CONFIGURATION
# --------------------------------------------------

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY", "").strip()
database_url = os.getenv("DATABASE_URL", "").strip()
supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")

paddle_api_key = (
    os.getenv("PADDLE_API_KEY", "")
    .replace("\n", "")
    .replace("\r", "")
    .strip()
)

paddle_price_id = os.getenv("PADDLE_PRICE_ID", "").strip()

paddle_webhook_secret = (
    os.getenv("PADDLE_WEBHOOK_SECRET", "").strip()
)

gemini_model = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
).strip()


# --------------------------------------------------
# REQUIRED CONFIGURATION CHECKS
# --------------------------------------------------

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not database_url:
    raise RuntimeError("DATABASE_URL is missing")

if not supabase_url:
    raise RuntimeError("SUPABASE_URL is missing")

if not paddle_api_key:
    raise RuntimeError("PADDLE_API_KEY is missing")

if not paddle_price_id:
    raise RuntimeError("PADDLE_PRICE_ID is missing")


# --------------------------------------------------
# INITIALIZE SERVICES
# --------------------------------------------------

client = genai.Client(api_key=api_key)

jwks_client = PyJWKClient(
    f"{supabase_url}/auth/v1/.well-known/jwks.json"
)

security = HTTPBearer()

app = FastAPI()


# --------------------------------------------------
# CORS
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def get_db_connection():
    return psycopg.connect(database_url)


@app.on_event("startup")
def setup_database():

    with get_db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id TEXT NOT NULL,
                    usage_date DATE NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, usage_date)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS paddle_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    received_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    price_id TEXT,
                    current_period_end TIMESTAMPTZ,
                    scheduled_change_action TEXT,
                    scheduled_change_effective_at TIMESTAMPTZ,
                    last_event_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """)

    print("DATABASE: Tables ready")


# --------------------------------------------------
# SUPABASE AUTHENTICATION
# --------------------------------------------------

def verify_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):

    token = credentials.credentials

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        token_header = jwt.get_unverified_header(token)
        algorithm = token_header.get("alg")

        if algorithm not in ["ES256", "RS256"]:
            raise HTTPException(
                status_code=401,
                detail="Unsupported authentication token."
            )

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=f"{supabase_url}/auth/v1",
        )

        user_id = payload.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="Invalid user token."
            )

        return user_id

    except HTTPException:
        raise

    except Exception as error:

        print("AUTH: Token verification failed:", str(error))

        raise HTTPException(
            status_code=401,
            detail="Invalid or expired login token. Please sign in again."
        )


# --------------------------------------------------
# REQUEST MODEL
# --------------------------------------------------

class SummaryRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=20000
    )


# --------------------------------------------------
# SUBSCRIPTION CHECK
# --------------------------------------------------

def is_pro_user(user_id: str) -> bool:

    with get_db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT EXISTS (
                    SELECT 1
                    FROM subscriptions
                    WHERE user_id = %s
                    AND price_id = %s
                    AND status IN ('active', 'trialing')
                    AND (
                        current_period_end IS NULL
                        OR current_period_end > CURRENT_TIMESTAMP
                    )
                )
            """, (user_id, paddle_price_id))

            result = cur.fetchone()

            return bool(result[0])


# --------------------------------------------------
# PADDLE SIGNATURE VERIFICATION
# --------------------------------------------------

def verify_paddle_signature(
    raw_body: bytes,
    signature_header: str
) -> bool:

    if not paddle_webhook_secret:
        print("WEBHOOK: Secret is not configured")
        return False

    if not signature_header:
        print("WEBHOOK: Signature header is missing")
        return False

    parts = {}

    for item in signature_header.split(";"):

        if "=" in item:
            key, value = item.split("=", 1)

            parts.setdefault(
                key.strip(),
                []
            ).append(value.strip())

    timestamps = parts.get("ts", [])
    signatures = parts.get("h1", [])

    if not timestamps or not signatures:
        print("WEBHOOK: Signature header format is invalid")
        return False

    timestamp = timestamps[0]

    try:
        event_timestamp = int(timestamp)

    except ValueError:
        print("WEBHOOK: Invalid signature timestamp")
        return False

    # Reject requests outside the five-minute window
    if abs(time.time() - event_timestamp) > 300:
        print("WEBHOOK: Signature timestamp outside allowed window")
        return False

    signed_payload = (
        timestamp.encode("utf-8")
        + b":"
        + raw_body
    )

    expected_signature = hmac.new(
        paddle_webhook_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256
    ).hexdigest()

    return any(
        hmac.compare_digest(
            expected_signature,
            signature
        )
        for signature in signatures
    )


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.get("/")
def home():

    return {
        "message": "AI Summarizer backend is running"
    }


# --------------------------------------------------
# GET USER USAGE
# --------------------------------------------------

@app.get("/usage")
def get_usage(user_id: str = Depends(verify_user)):

    try:

        pro = is_pro_user(user_id)

        with get_db_connection() as conn:
            with conn.cursor() as cur:

                cur.execute("""
                    SELECT count
                    FROM daily_usage
                    WHERE user_id = %s
                    AND usage_date = CURRENT_DATE
                """, (user_id,))

                result = cur.fetchone()

                return {
                    "used_today": result[0] if result else 0,
                    "daily_limit": None if pro else 10,
                    "plan": "pro" if pro else "free"
                }

    except Exception as error:

        print("USAGE: Check failed:", repr(error))

        raise HTTPException(
            status_code=503,
            detail="Could not retrieve usage."
        )


# --------------------------------------------------
# SUMMARIZE
# --------------------------------------------------

@app.post("/summarize")
def summarize(
    request: SummaryRequest,
    user_id: str = Depends(verify_user)
):

    # Check subscription
    try:

        pro = is_pro_user(user_id)

    except Exception as error:

        print("SUBSCRIPTION: Check failed:", repr(error))

        raise HTTPException(
            status_code=503,
            detail="Could not verify subscription status."
        )

    # Enforce daily limit for free users
    if not pro:

        try:

            with get_db_connection() as conn:
                with conn.cursor() as cur:

                    cur.execute("""
                        INSERT INTO daily_usage (
                            user_id,
                            usage_date,
                            count
                        )
                        VALUES (
                            %s,
                            CURRENT_DATE,
                            1
                        )
                        ON CONFLICT (user_id, usage_date)
                        DO UPDATE SET
                            count = daily_usage.count + 1
                        WHERE daily_usage.count < 10
                        RETURNING count
                    """, (user_id,))

                    result = cur.fetchone()

                    if result is None:

                        raise HTTPException(
                            status_code=429,
                            detail="Free limit reached (10 summaries/day)."
                        )

        except HTTPException:
            raise

        except Exception as error:

            print("USAGE: Database error:", repr(error))

            raise HTTPException(
                status_code=503,
                detail="Usage service temporarily unavailable."
            )

    # Try Gemini models
    models_to_try = list(dict.fromkeys([
        gemini_model,
        "gemini-2.5-flash",
        "gemini-3.5-flash-lite"
    ]))

    last_error = ""

    for model_name in models_to_try:

        try:

            print("GEMINI: Trying model:", model_name)

            response = client.models.generate_content(
                model=model_name,
                contents=(
                    "Summarize this text clearly in its original language:\n\n"
                    + request.text
                )
            )

            if response and response.text:

                return {
                    "summary": response.text
                }

            last_error = "Empty response from " + model_name

        except Exception as error:

            print(
                "GEMINI: Model failed:",
                model_name,
                repr(error)
            )

            last_error = str(error)

            time.sleep(0.5)

    print("GEMINI: All models failed:", last_error)

    raise HTTPException(
        status_code=502,
        detail="The AI service is temporarily unavailable. Please try again later."
    )


# --------------------------------------------------
# PADDLE CHECKOUT
# --------------------------------------------------

@app.post("/create-checkout")
def create_checkout(
    user_id: str = Depends(verify_user)
):

    print("CHECKOUT: Request received")

    payload = {
        "items": [
            {
                "price_id": paddle_price_id,
                "quantity": 1
            }
        ],
        "custom_data": {
            "user_id": user_id
        }
    }

    paddle_request = urllib.request.Request(
        "https://sandbox-api.paddle.com/transactions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {paddle_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            paddle_request,
            timeout=30
        ) as response:

            response_body = response.read().decode("utf-8")
            result = json.loads(response_body)

            print(
                "CHECKOUT: Paddle HTTP status:",
                response.status
            )

            print(
                "CHECKOUT: Paddle request ID:",
                response.headers.get("Paddle-Request-Id")
            )

        transaction = result.get("data", {})

        checkout = transaction.get("checkout") or {}

        checkout_url = checkout.get("url")

        if not checkout_url:

            print("CHECKOUT: Missing checkout URL")

            raise HTTPException(
                status_code=502,
                detail="Paddle did not return a checkout URL."
            )

        print("CHECKOUT: Checkout URL created")

        return {
            "checkout_url": checkout_url
        }

    except urllib.error.HTTPError as error:

        error_body = error.read().decode(
            "utf-8",
            errors="replace"
        )

        print("CHECKOUT: Paddle HTTP error:", error.code)

        print(
            "CHECKOUT: Paddle request ID:",
            error.headers.get("Paddle-Request-Id")
        )

        print("CHECKOUT: Paddle error:", error_body)

        raise HTTPException(
            status_code=502,
            detail="Paddle rejected the checkout request."
        )

    except HTTPException:
        raise

    except Exception as error:

        print("CHECKOUT: Unexpected error:", repr(error))

        raise HTTPException(
            status_code=500,
            detail="Could not create checkout."
        )


# --------------------------------------------------
# PADDLE WEBHOOK
# --------------------------------------------------

@app.post("/paddle-webhook")
async def paddle_webhook(request: Request):

    # IMPORTANT: Read the original body before parsing JSON
    raw_body = await request.body()

    signature = request.headers.get(
        "Paddle-Signature",
        ""
    )

    # Verify Paddle signature
    if not verify_paddle_signature(
        raw_body,
        signature
    ):

        print("WEBHOOK: Invalid signature")

        raise HTTPException(
            status_code=401,
            detail="Invalid Paddle signature."
        )

    # Parse verified event
    try:

        event = json.loads(
            raw_body.decode("utf-8")
        )

    except (json.JSONDecodeError, UnicodeDecodeError):

        raise HTTPException(
            status_code=400,
            detail="Invalid webhook JSON."
        )

    event_id = event.get("event_id")
    event_type = event.get("event_type")
    occurred_at = event.get("occurred_at")

    data = event.get("data") or {}

    if not event_id or not event_type or not occurred_at:

        raise HTTPException(
            status_code=400,
            detail="Missing event information."
        )

    print(
        "WEBHOOK: Received",
        event_type,
        event_id
    )

    subscription_events = {
        "subscription.created",
        "subscription.activated",
        "subscription.updated",
        "subscription.canceled",
        "subscription.past_due",
        "subscription.paused",
        "subscription.resumed",
    }

    try:

        with get_db_connection() as conn:
            with conn.cursor() as cur:

                # Check duplicate event before processing
                cur.execute("""
                    SELECT 1
                    FROM paddle_events
                    WHERE event_id = %s
                """, (event_id,))

                if cur.fetchone():

                    print(
                        "WEBHOOK: Duplicate event ignored",
                        event_id
                    )

                    return {
                        "success": True,
                        "duplicate": True
                    }

                # Process subscription events
                if event_type in subscription_events:

                    subscription_id = data.get("id")

                    if not subscription_id:

                        raise HTTPException(
                            status_code=400,
                            detail="Subscription ID is missing."
                        )

                    custom_data = data.get("custom_data") or {}

                    user_id = custom_data.get("user_id")

                    # Find user from an existing subscription
                    if not user_id:

                        cur.execute("""
                            SELECT user_id
                            FROM subscriptions
                            WHERE subscription_id = %s
                        """, (subscription_id,))

                        existing = cur.fetchone()

                        if existing:
                            user_id = existing[0]

                    if not user_id:

                        print(
                            "WEBHOOK: User ID not found for subscription",
                            subscription_id
                        )

                        raise HTTPException(
                            status_code=400,
                            detail="Subscription user ID is missing."
                        )

                    status = data.get("status")

                    if not status:

                        status_by_event = {
                            "subscription.activated": "active",
                            "subscription.resumed": "active",
                            "subscription.paused": "paused",
                            "subscription.past_due": "past_due",
                            "subscription.canceled": "canceled",
                        }

                        status = status_by_event.get(event_type)

                    if not status:

                        raise HTTPException(
                            status_code=400,
                            detail="Subscription status is missing."
                        )

                    # Get subscription price
                    items = data.get("items") or []

                    price_id = None

                    for item in items:

                        price = item.get("price") or {}

                        if price.get("id") == paddle_price_id:

                            price_id = price.get("id")
                            break

                    billing_period = (
                        data.get("current_billing_period") or {}
                    )

                    current_period_end = billing_period.get("ends_at")

                    scheduled_change = (
                        data.get("scheduled_change") or {}
                    )

                    scheduled_action = scheduled_change.get("action")

                    scheduled_effective_at = (
                        scheduled_change.get("effective_at")
                    )

                    # Save or update subscription
                    cur.execute("""
                        INSERT INTO subscriptions (
                            subscription_id,
                            user_id,
                            status,
                            price_id,
                            current_period_end,
                            scheduled_change_action,
                            scheduled_change_effective_at,
                            last_event_at,
                            updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (subscription_id)
                        DO UPDATE SET
                            user_id = EXCLUDED.user_id,
                            status = EXCLUDED.status,
                            price_id = EXCLUDED.price_id,
                            current_period_end = EXCLUDED.current_period_end,
                            scheduled_change_action =
                                EXCLUDED.scheduled_change_action,
                            scheduled_change_effective_at =
                                EXCLUDED.scheduled_change_effective_at,
                            last_event_at = EXCLUDED.last_event_at,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE subscriptions.last_event_at
                            <= EXCLUDED.last_event_at
                    """, (
                        subscription_id,
                        user_id,
                        status,
                        price_id,
                        current_period_end,
                        scheduled_action,
                        scheduled_effective_at,
                        occurred_at
                    ))

                    print(
                        "WEBHOOK: Subscription processed",
                        subscription_id,
                        status,
                        price_id
                    )

                # Record successfully processed event
                cur.execute("""
                    INSERT INTO paddle_events (
                        event_id,
                        event_type
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                """, (event_id, event_type))

                print(
                    "WEBHOOK: Event recorded",
                    event_type
                )

        return {
            "success": True
        }

    except HTTPException:
        raise

    except Exception as error:

        print(
            "WEBHOOK: Processing failed:",
            repr(error)
        )

        raise HTTPException(
            status_code=500,
            detail="Webhook processing failed."
        )
