import io
import logging
import traceback
from xhtml2pdf import pisa
from typing import Any

logger = logging.getLogger(__name__)

def html_to_pdf(html_content: str) -> bytes:
    """
    Convert HTML string to PDF bytes using xhtml2pdf.
    Includes character sanitization for PDF rendering.
    """
    def clean_text(t):
        if not isinstance(t, str): return t
        # Standard replacements for common xhtml2pdf-breaking characters
        replacements = {
            '\u2010': '-', '\u2011': '-', '\u2012': '-', '\u2013': '-', '\u2014': '--',
            '\u2015': '--', '\u2017': '_', '\u2018': "'", '\u2019': "'", '\u201a': "'",
            '\u201c': '"', '\u201d': '"', '\u201e': '"', '\u2022': '*', '\u2026': '...',
            '\u00a0': ' ', '\xad': '-'
        }
        for char, rep in replacements.items():
            t = t.replace(char, rep)
        # Remove invisible control characters that break xhtml2pdf kerning
        t = "".join(char for char in t if ord(char) >= 32 or char in "\n\r\t")
        return " ".join(t.split()) # Normalize whitespace gaps

    # Apply cleaning to the raw HTML content
    html_content = clean_text(html_content)

    pdf_buffer = io.BytesIO()
    try:
        pisa_status = pisa.CreatePDF(
            io.BytesIO(html_content.encode('utf-8')),
            dest=pdf_buffer,
            encoding='utf-8'
        )
        if pisa_status.err:
            raise Exception(f"Failed to generate PDF: {pisa_status.err}")
    except Exception as e:
        logger.error(f"PDF Generation ERROR: {str(e)}")
        raise e
        
    return pdf_buffer.getvalue()
