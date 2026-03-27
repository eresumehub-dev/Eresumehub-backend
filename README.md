EresumeHub - FastAPI Backend
This project is a Python FastAPI server that provides an "End-to-End" Resume Intelligence Engine (Generation 4).

## Features: Generation 4 Architecture
This system implements a 3-Layer Defense & Analysis Engine:

### Layer 1: Forensic Ingestion (The Gatekeeper)
- **Anti-Cheating:** Automatically detects "White Fonting" (hidden text) and microscopic fonts (<4.5pt).
- **OCR Fallback:** Uses **Gemini 1.5 Flash Vision** to transcribe image-based resumes if text extraction fails (<50 chars).

### Layer 2: Security & Sanitization (The Iron Dome)
- **Input Sanitization:** Strips "Prompt Injection" attacks (e.g., "Ignore previous instructions").
- **DoS Protection:** Hard truncates input at 25,000 characters to prevent Token Bombs.
- **Privacy:** Redacts SSN-like patterns (`000-00-0000`).

### Layer 3: Hybrid Intelligence (The Brain)
- **Resume2Vec Semantic Scoring (60%):** Uses `text-embedding-004` to calculate Cosine Similarity between Resume and Job Description vectors. Matches concepts ("Fiscal") even without exact keywords ("Financial").
- **Qualification Scoring (40%):** Uses LLM to verify hard constraints (Visa, Degree, Years of Exp).

## API documentation
Automatic interactive documentation is available at `/docs`.

### Key Endpoint: `POST /api/v1/ats/analyze`
Analyzes a resume against a specific Job Description.

**Response Structure:**
```json
{
  "success": true,
  "data": {
    "qualification_score": 75,
    "semantic_score": 82.5,  // Resume2Vec Score
    "score_breakdown": { ... },
    "warnings": [
      "SUSPICIOUS_FORMATTING_DETECTED: Potential hidden text found",
      "SCANNED_DOCUMENT: Text extracted via AI OCR"
    ],
    "top_fix": { ... }
  }
}
```

## 1. Setup
### Prerequisites
- Python 3.8+
- An OpenRouter API Key
- A Gemini API Key (for OCR & Embeddings)

### Installation
```bash
# Clone
git clone <your-repo-url>
cd <your-repo-directory>

# Virtual Env
python -m venv venv
.\venv\Scripts\activate  # Windows
source venv/bin/activate # Mac/Linux

# Install
pip install -r requirements.txt
```

### Environment Variables
Create a `.env` file:
```env
OPENROUTER_API_KEY="sk-..."
GEMINI_API_KEY="AIza..."
SUPABASE_URL="https://..."
SUPABASE_SERVICE_KEY="eyJ..."
```

## 2. Running the Server
```bash
uvicorn main:app --reload
```
Server runs at `http://127.0.0.1:8000`.
