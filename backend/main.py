import os
import time
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing from environment variables")

client = genai.Client(api_key=api_key)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

USAGE_TRACKER = {}
PAID_USERS = set()


class SummaryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    user_id: str = "anonymous"


@app.get("/")
def home():
    return {"message": "AI Summarizer backend is running"}


@app.post("/summarize")
def summarize(request: SummaryRequest):
    user_id = request.user_id

    # Лимит для бесплатной версии (10 запросов в день)
    if user_id not in PAID_USERS:
        current_usage = USAGE_TRACKER.get(user_id, 0)
        if current_usage >= 10:
            raise HTTPException(
                status_code=429,
                detail="Free limit reached (10 summaries/day). Upgrade to Pro for unlimited access!",
            )
        USAGE_TRACKER[user_id] = current_usage + 1

    # Только актуальные и поддерживаемые модели Gemini 2.x
    models_to_try = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]

    last_error = ""

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=(
                    "Summarize this text clearly in its original language:\n\n"
                    + request.text
                ),
            )

            if response and response.text:
                return {"summary": response.text}

        except Exception as error:
            print(f"Error with model {model_name}:", error)
            last_error = str(error)
            time.sleep(0.5)

    raise HTTPException(
        status_code=502,
        detail=f"Gemini request failed: {last_error}",
    )