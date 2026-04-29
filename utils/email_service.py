import logging
import httpx
from app_settings import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    async def send_contact_email(name: str, email: str, topic: str, message: str) -> bool:
        """
        Send a contact form submission to the support team.
        Integrates with Resend API (v16.5.2).
        """
        api_key = getattr(Config, "RESEND_API_KEY", None)
        support_email = getattr(Config, "SUPPORT_EMAIL", "support@eresumehub.com")

        subject = f"[Support Request] {topic} from {name}"
        body = f"Name: {name}\nEmail: {email}\nTopic: {topic}\n\nMessage:\n{message}"

        if not api_key:
            logger.warning(f"[EMAIL MOCK] Sending email to {support_email}\nSubject: {subject}\nBody: {body}")
            return True

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": "EresumeHub Support <onboarding@resend.dev>",
                        "to": [support_email],
                        "subject": subject,
                        "text": body,
                        "reply_to": email
                    }
                )
                response.raise_for_status()
                logger.info(f"Email sent successfully via Resend for {email}")
                return True
        except Exception as e:
            logger.error(f"Failed to send email via Resend: {e}")
            return False

email_service = EmailService()
