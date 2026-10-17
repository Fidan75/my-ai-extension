import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing from .env")

client = genai.Client(api_key=api_key)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

class SummaryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)

@app.get("/")
def home():
    return {"message": "AI Summarizer backend is running"}

@app.post("/summarize")
def summarize(request: SummaryRequest):
    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=(
                "Summarize this text clearly in its original language:\n\n"
                + request.text
            ),
        )

        if not response.text:
            raise HTTPException(
                status_code=502,
                detail="AI returned an empty response",
            )

        return {"summary": response.text}

    except HTTPException:
        raise
    except Exception as error:
        print("Gemini error:", error)
        raise HTTPException(
            status_code=502,
            detail="AI request failed. Check the server terminal.",
        )