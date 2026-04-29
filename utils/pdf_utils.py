import logging
import traceback
from weasyprint import HTML, CSS
from typing import Any
import io

logger = logging.getLogger(__name__)

def html_to_pdf(html_content: str) -> bytes:
    """
    Convert HTML string to PDF bytes using WeasyPrint.
    WeasyPrint provides superior kerning and CSS support compared to xhtml2pdf.
    """
    try:
        # WeasyPrint handles UTF-8 by default.
        # We can also pass base_url if we need to resolve relative assets, 
        # but for EresumeHub, we usually use inline styles or absolute URLs.
        
        pdf_bytes = HTML(string=html_content).write_pdf()
        
        return pdf_bytes
    except Exception as e:
        logger.error(f"PDF Generation ERROR (WeasyPrint): {str(e)}")
        logger.error(traceback.format_exc())
        raise RuntimeError("PDF generation failed. Please try again.")
