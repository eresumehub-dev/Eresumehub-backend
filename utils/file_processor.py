import io
import os
from fastapi import UploadFile, HTTPException
import fitz  # PyMuPDF
import docx2txt
import re

class FileProcessor:
    @staticmethod
    def validate_file(file: UploadFile, allowed_extensions=None) -> str:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No filename provided")
        
        ext = os.path.splitext(file.filename)[1].lower()
        
        # Default to document types if not specified, but allow overriding
        if allowed_extensions is None:
             # Extended list to include images so validate_file doesn't block photos
             allowed_extensions = ['.pdf', '.docx', '.txt', '.jpg', '.jpeg', '.png', '.webp']
             
        if ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")
            
        return file.filename

    @staticmethod
    async def parse_pdf(file: UploadFile, ai_service=None) -> dict:
        try:
            # Read file content
            content = await file.read() 
            
            # Reset cursor for safety if read() didn't work as expected or if repeated reads needed
            # But await file.read() usually works fine for UploadFile.
            
            doc = fitz.open(stream=content, filetype="pdf")
            text = ""
            has_images = False
            page_count = len(doc)
            warnings = []
            suspicious_count = 0
            
            for page in doc:
                # Use "dict" to get detailed formatting
                blocks = page.get_text("dict")["blocks"]
                blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
                
                for b in blocks:
                    if b["type"] == 0: # Check text blocks
                        for line in b["lines"]:
                            for span in line["spans"]:
                                span_text = span["text"]
                                text += span_text + " "
                                
                                # --- ANTI-CHEATING DETECTION ---
                                # 1. Microscopic Text
                                if span["size"] < 4.5: # Threshold for readable text
                                    suspicious_count += 1
                                    
                                # 2. White Text (White Fonting)
                                # Color is usually int (RGB) or tuple. PyMuPDF returns sRGB int.
                                # Check if color is close to white (FFFFFF = 16777215)
                                if span["color"] > 16770000: # Very close to white
                                    suspicious_count += 1
                                    
                    elif b["type"] == 1: # Image block
                         has_images = True
                         
                text += "\n"
                        
            # Flag suspicious formatting
            if suspicious_count > 5:
                warnings.append("SUSPICIOUS_FORMATTING_DETECTED: Potential hidden text or white fonting used.")

            # --- OCR FALLBACK (SCANNED DOCUMENT) ---
            # If text is empty or extremely short, but images exist
            if len(text.strip()) < 50 and page_count > 0:
                if ai_service:
                    try:
                        # Render first page to image
                        pix = doc[0].get_pixmap()
                        img_bytes = pix.tobytes("png")
                        
                        ocr_text = await ai_service.extract_text_from_image(img_bytes, "image/png")
                        if ocr_text:
                            text = ocr_text
                            warnings.append("SCANNED_DOCUMENT: Text extracted via AI OCR.")
                    except Exception as ocr_err:
                        warnings.append(f"OCR Failed: {str(ocr_err)}")
                else:
                    warnings.append("SCANNED_DOCUMENT: OCR unavailable (Service not linked).")

            # --- SECURITY SANITIZATION ---
            text = FileProcessor._sanitize_text(text)

            return {
                "text": text,
                "metadata": {
                    "page_count": page_count,
                    "has_images": has_images,
                    "file_type": "pdf"
                },
                "warnings": warnings 
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {str(e)}")

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """
        Sanitize input text to prevent Prompt Injection and DoS attacks.
        1. Strip known jailbreak phrases.
        2. Hard delete SSN-like patterns.
        3. Truncate to 25,000 chars.
        """
        if not text:
            return ""
            
        # 1. Strip Jailbreak Phrases (Case Insensitive)
        jailbreak_patterns = [
            r"ignore previous instructions",
            r"system prompt",
            r"you are a helpful assistant",
            r"override score",
            r"forget all prior",
            r"ignore all rules"
        ]
        
        for pattern in jailbreak_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
            
        # 2. Redact PII (SSN-like patterns: 000-00-0000)
        # Using a conservative regex to avoid false positives on phone numbers, 
        # but standard SSN is 3-2-4 digits.
        text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED-SSN]", text)
        
        # 3. Hard Truncation (DoS Protection)
        MAX_CHARS = 25000
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "\n[TRUNCATED_FOR_SECURITY]"
            
        return text

    @staticmethod
    async def parse_docx(file: UploadFile) -> dict:
        try:
            # Read file content
            content = await file.read()
            
            # docx2txt doesn't support stream directly easily, but we can verify
            # actually docx2txt.process accepts a filename or file-like object
            text = docx2txt.process(io.BytesIO(content))
            
            # --- SECURITY SANITIZATION ---
            text = FileProcessor._sanitize_text(text)
            
            return {
                "text": text,
                "metadata": {
                    # Estimate page count: ~3000 chars per page is a reasonable heuristic for structured resumes
                    "page_count": max(1, len(text) // 3000 + (1 if len(text) % 3000 > 500 else 0)),
                    "has_images": False, # Basic extraction doesn't detect images easily
                    "file_type": "docx"
                }
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse DOCX: {str(e)}")
