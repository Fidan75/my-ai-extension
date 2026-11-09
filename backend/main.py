
import os
import time
import json
import urllib.request
import urllib.error

import jwt
import psycopg

from jwt import PyJWKClient
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from google import genai


# Load environment variables
load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
database_url = os.getenv("DATABASE_URL")
supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")

paddle_api_key = os.getenv("PADDLE_API_KEY")
paddle_price_id = os.getenv("PADDLE_PRICE_ID")


# Check required configuration
if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing")

if not database_url:
    raise RuntimeError("DATABASE_URL is missing")

if not supabase_url:
    raise RuntimeError("SUPABASE_URL is missing")

if not paddle_api_key or not paddle_price_id:
    raise RuntimeError("Paddle configuration is missing")


# Initialize services
client = genai.Client(api_key=api_key)

jwks_client = PyJWKClient(
    f"{supabase_url}/auth/v1/.well-known/jwks.json"
)

security = HTTPBearer()

app = FastAPI()


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Database connection
def get_db_connection():
    return psycopg.connect(database_url)


# Verify Supabase user
def verify_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
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
        print("Token verification failed:", str(error))

        raise HTTPException(
            status_code=401,
            detail="Invalid or expired login token. Please sign in again."
        )


# Create database table
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


# Request model
class SummaryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


# Home endpoint
@app.get("/")
def home():
    return {
        "message": "AI Summarizer backend is running"
    }


# Get daily usage
@app.get("/usage")
def get_usage(user_id: str = Depends(verify_user)):
    try:
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
                    "daily_limit": 10
                }

    except Exception as error:
        print("Usage check failed:", str(error))

        raise HTTPException(
            status_code=503,
            detail="Could not retrieve usage."
        )


# Summarize endpoint
@app.post("/summarize")
def summarize(
    request: SummaryRequest,
    user_id: str = Depends(verify_user)
):
    # Check daily limit
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO daily_usage (user_id, usage_date, count)
                    VALUES (%s, CURRENT_DATE, 1)
                    ON CONFLICT (user_id, usage_date)
                    DO UPDATE SET count = daily_usage.count + 1
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
        print("Database usage check failed:", str(error))

        raise HTTPException(
            status_code=503,
            detail="Usage service temporarily unavailable. Please try again later."
        )

    # Try Gemini models
    models_to_try = [
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]

    last_error = ""

    for model_name in models_to_try:
        try:
            print(f"Trying Gemini model: {model_name}")

            response = client.models.generate_content(
                model=model_name,
                contents=(
                    "Summarize this text clearly in its original language:\n\n"
                    + request.text
                ),
            )

            if response and response.text:
                return {
                    "summary": response.text
                }

            last_error = f"{model_name} returned an empty response."

        except Exception as error:
            print(f"Error with model {model_name}: {error}")
            last_error = f"{model_name}: {error}"
            time.sleep(0.5)

    print("All Gemini models failed:", last_error)

    raise HTTPException(
        status_code=502,
        detail="The AI service is temporarily unavailable. Please try again later."
    )


# Paddle checkout endpoint
@app.post("/create-checkout")
def create_checkout(user_id: str = Depends(verify_user)):

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

            print("CHECKOUT: Paddle HTTP status:", response.status)

            request_id = response.headers.get("Paddle-Request-Id")
            print("CHECKOUT: Paddle request ID:", request_id)

        transaction = result.get("data", {})
        checkout = transaction.get("checkout") or {}
        checkout_url = checkout.get("url")

        if not checkout_url:
            print("CHECKOUT: Paddle did not return a checkout URL")
            print("CHECKOUT: Paddle response:", result)

            raise HTTPException(
                status_code=502,
                detail="Paddle did not return a checkout URL."
            )

        print("CHECKOUT: Checkout URL created successfully")

        return {
            "checkout_url": checkout_url
        }

    except urllib.error.HTTPError as error:

        error_body = error.read().decode(
            "utf-8",
            errors="replace"
        )

        print("CHECKOUT: Paddle HTTP status:", error.code)
        print(
            "CHECKOUT: Paddle request ID:",
            error.headers.get("Paddle-Request-Id")
        )
        print("CHECKOUT: Paddle error response:", error_body)

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
